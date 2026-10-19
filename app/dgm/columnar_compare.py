"""Path-based full joins in private RAM, with paged views and explicit coverage."""
from __future__ import annotations

import base64
from datetime import datetime
import fnmatch
import hashlib
import json
import threading
import time
import uuid

from .columnar_store import Repository, MemorySession, CapturedPaths, quote
from .columnar_ingest import rollup_values, insert_records, PATH_KEY_VERSION

REASONS = {'scope': 'Khác phạm vi/loại trừ', 'browser': 'Không quét Chrome/Edge',
           'coverage': 'Vùng chưa xác minh/quyền đọc', 'legacy': 'Thiếu bằng chứng độ bao phủ',
           'metadata': 'Metadata thiếu/thay đổi', 'collision': 'Khóa đường dẫn không duy nhất',
           'volume': 'Khác danh tính ổ'}
SORTS = {'path': 'r.path_key', 'kind': 'r.kind', 'old': 'r.before', 'new': 'r.after',
         'delta': 'r.delta', 'abs_delta': 'abs(r.delta)', 'allocated_old': 'r.allocated_before',
         'allocated_new': 'r.allocated_after', 'allocated_delta': 'r.allocated_delta',
         'change': 'r.change', 'presence': 'r.presence'}
SORTS.update(logical_old='r.logical_before', logical_new='r.logical_after', logical_delta='r.logical_delta')
SORTS.update(created='CASE WHEN r.new_row IS NOT NULL THEN r.new_created ELSE r.old_created END',
             modified='CASE WHEN r.new_row IS NOT NULL THEN r.new_modified ELSE r.old_modified END')


def _inside(path, root):
    prefix = root if root.endswith('|') else root.rstrip('\\/') + ('/' if '/' in root and '\\' not in root else '\\')
    return path == root or path.startswith(prefix)


class CapturedScope:
    def __init__(self, scan):
        self.scan = scan
        self.keys = CapturedPaths(scan)
        self.scope = scan.get('scope') or {}
        self.roots = [self.keys.key(path) for path in self.scope.get('roots', [])]
        self.excludes = list(self.scope.get('excludes') or []) + list(self.scope.get('implicit_excludes') or [])
        self.coverage = scan.get('coverage') or {}
        self.unknown = [self.keys.key(path) for path in self.coverage.get('unknown_roots', [])]
        self.errors = [self.keys.key(row['path'] if isinstance(row, dict) else row) for row in self.coverage.get('error_regions', [])]
        self.errors += [self.keys.key(row['path']) for row in scan.get('errors', [])]

    def issue(self, path, origin):
        from .config import browser_path
        key = self.keys.key(path)
        origin_key = origin.keys.key(path)
        volume = key.split('|', 1)[0]
        other_volume = origin_key.split('|', 1)[0]
        if not volume.startswith(('mount:', 'unknown-')) and not other_volume.startswith(('mount:', 'unknown-')) and volume != other_volume:
            return 'volume'
        if self.scope.get('strict_browsers') and browser_path(path):
            return 'browser'
        if not any(_inside(key, root) for root in self.roots):
            return 'scope'
        value = CapturedPaths.canonical(path).replace('\\', '/').lower().rstrip('/')
        for pattern in self.excludes:
            normalized = CapturedPaths.canonical(pattern).replace('\\', '/').lower().rstrip('/') if not any(c in str(pattern) for c in '*?[') else str(pattern).replace('\\', '/').lower().rstrip('/')
            if value == normalized or value.startswith(normalized + '/') or fnmatch.fnmatchcase(value, normalized):
                return 'scope'
        if any(_inside(key, root) for root in self.errors + self.unknown):
            return 'coverage'
        if self.coverage.get('source') == 'legacy' or 'source' not in self.coverage:
            return 'legacy'
        if self.scope.get('implicit_excludes') is None:
            return 'legacy'
        return ''

    def sql_issue(self, alias):
        """Evaluate only captured coverage, in native SQL (no per-row callbacks)."""
        def inside(key):
            prefix = key if key.endswith('|') else key.rstrip('\\/') + ('/' if self.scope['roots'][0].startswith('/') else '\\')
            return f'({alias}.path_key={quote(key)} OR starts_with({alias}.path_key,{quote(prefix)}))'
        tests = []
        known_volumes = {key.split('|',1)[0] for key in self.roots if not key.startswith(('mount:', 'unknown-'))}
        if known_volumes:
            supported = ' OR '.join(f'starts_with({alias}.path_key,{quote(v+"|")})' for v in known_volumes)
            tests.append((f'NOT ({supported}) AND NOT starts_with({alias}.path_key,\'mount:\')', 'volume'))
        if self.scope.get('strict_browsers'):
            pattern = r'(^|[/\\])(chrome|chrome beta|chrome dev|chrome sxs|edge|edge beta|edge dev|edge sxs|edgecore|edgeupdate|edgewebview|googleupdater)([/\\]|$)|microsoft-edge-webview|microsoft-edge_'
            tests.append((f'regexp_matches(lower({alias}.path),{quote(pattern)})', 'browser'))
        tests.append(('NOT (' + (' OR '.join(inside(key) for key in self.roots) or 'false') + ')', 'scope'))
        for pattern in self.excludes:
            key = self.keys.key(pattern)
            if any(c in str(pattern) for c in '*?['):
                namespace, tail = key.split('|', 1)
                namespace = namespace.replace('[', '[[]').replace('*', '[*]').replace('?', '[?]')
                condition = f'{alias}.path_key GLOB {quote(namespace + "|" + tail)}'
            else:
                condition = inside(key)
            tests.append((condition, 'scope'))
        if self.errors or self.unknown:
            tests.append((' OR '.join(inside(key) for key in self.errors + self.unknown), 'coverage'))
        fallback = 'legacy' if self.coverage.get('source', 'legacy') == 'legacy' or self.scope.get('implicit_excludes') is None else ''
        return 'CASE ' + ' '.join(f'WHEN ({condition}) THEN {quote(reason)}' for condition, reason in tests) + f' ELSE {quote(fallback)} END'


def _metadata_issue(alias):
    return f"CASE WHEN {alias}.row_id IS NOT NULL AND ({alias}.logical IS NULL OR {alias}.status IN ('changed-during-scan','unavailable-after-export','metadata-invalid')) THEN 'metadata' ELSE '' END"


class Comparison:
    def __init__(self, newer, older=None, *, repository=None, stop=None, notify=None):
        self.repo = repository or Repository()
        self.new = self.repo.get(newer)
        self.old = self.repo.get(older) if older is not None else None
        if any(scan.get('path_key_version',1)!=PATH_KEY_VERSION for scan in [self.new]+([self.old] if self.old else [])):
            raise ValueError('Mốc Parquet bản thử dùng khóa đường dẫn cũ; nhập lại từ nguồn JSONL/CSV để chuẩn hóa một lần, không ghép lẫn schema khóa')
        self.token = uuid.uuid4().hex
        self._summary = None
        self.scope_new = CapturedScope(self.new)
        self.scope_old = CapturedScope(self.old) if self.old else None
        self.volume_assumptions = []
        # Old logs did not capture volume IDs. Allow a *documented* same-mount
        # alias for these only; two known, different volumes must never merge.
        self.aliases = {'old': {}, 'new': {}}
        if self.old:
            for side, scope, other in (('old', self.scope_old, self.scope_new), ('new', self.scope_new, self.scope_old)):
                for root in scope.scope['roots']:
                    anchor = scope.keys.anchor(root)
                    if anchor not in scope.keys.volume_map and anchor in other.keys.volume_map:
                        target = str(other.keys.volume_map[anchor]).lower()
                        self.aliases[side]['mount:' + anchor] = target
                        scope.keys.volume_map[anchor] = target
                        self.volume_assumptions.append(f'{side}: {anchor} thiếu ID ổ; ghép theo mount đã lưu, chưa xác minh cùng ổ vật lý')
                scope.roots = [scope.keys.key(p) for p in scope.scope['roots']]
                scope.unknown = [scope.keys.key(p) for p in scope.coverage.get('unknown_roots', [])]
                scope.errors = [scope.keys.key(r['path'] if isinstance(r, dict) else r) for r in scope.coverage.get('error_regions', [])]
                scope.errors += [scope.keys.key(r['path']) for r in scope.scan.get('errors', [])]
        from .operation_progress import Timeline
        self.timeline=Timeline('comparison',notify)
        self.session = MemorySession(self.repo, notify=self.timeline)
        try:
            if self.old and datetime.fromisoformat(self.old['started']) >= datetime.fromisoformat(self.new['started']):
                raise ValueError('Mốc cũ phải trước mốc mới; đổi vị trí hai mốc')
            with self.session.operation(stop):
                self._prepare(stop)
        except BaseException as exc:
            from .platform_ops import ProcessCancelled
            self.timeline.finish('cancelled' if isinstance(exc,ProcessCancelled) else 'failed')
            self.session.close(); raise

    def _prepare(self, stop):
        session = self.session
        db = session.db
        started = time.perf_counter()
        session.progress(5, 'Kiểm checksum hai snapshot Parquet đã chọn', stage='checksum')
        scans = [self.new] + ([self.old] if self.old else [])
        files = session.pin(scans)
        for name, scan, path in [('new_source', self.new, files[0]),
                                 ('old_source', self.old, files[1] if self.old else files[0])]:
            count = "COUNT(*) OVER (PARTITION BY path_key)" if scan and scan.get('path_key_collisions') else '1'
            empty = ' WHERE false' if scan is None else ''
            aliases = self.aliases['new' if name == 'new_source' else 'old']
            def mapped(column):
                expression = column
                for source, target in aliases.items():
                    expression = f'CASE WHEN starts_with({column},{quote(source+"|")}) THEN {quote(target+"|")}||substr({column},{len(source)+2}) ELSE {expression} END'
                return expression
            projection = f'* REPLACE({mapped("path_key")} AS path_key,{mapped("parent_key")} AS parent_key)' if aliases else '*'
            db.execute(f'CREATE TEMP VIEW {name} AS SELECT *,{count} AS key_count FROM (SELECT {projection} FROM read_parquet({quote(path)}){empty})')
        old_missing = self.scope_old.sql_issue('n') if self.old else "''"
        new_missing = self.scope_new.sql_issue('o') if self.old else "''"
        cross = bool(self.old and self.old['engine'] != self.new['engine'])
        modified = 'floor(old_modified)<>floor(new_modified)' if cross else 'old_modified<>new_modified'
        created = 'floor(old_created)<>floor(new_created)' if cross else 'old_created<>new_created'
        baseline = 'false' if self.old else 'true'
        session.progress(25, 'Ghép toàn bộ đường dẫn trong RAM, tách Chung/Riêng', stage='join')
        db.execute(f'''CREATE TEMP TABLE comparison AS WITH ambiguous AS (
            SELECT path_key FROM old_source WHERE key_count>1
            UNION SELECT path_key FROM new_source WHERE key_count>1),
        joined AS (
            SELECT coalesce(n.path_key,o.path_key) path_key,o.row_id old_row,n.row_id new_row,
                o.kind old_kind,n.kind new_kind,coalesce(n.kind,o.kind) kind,
                coalesce(o.kind='folder',false) OR coalesce(n.kind='folder',false) is_folder,
                o.logical AS "before",n.logical AS "after",o.allocated allocated_before,n.allocated allocated_after,
                o.created old_created,n.created new_created,o.modified old_modified,n.modified new_modified,
                o.identity old_identity,n.identity new_identity,o.attrs old_attrs,n.attrs new_attrs,
                CASE WHEN a.path_key IS NOT NULL THEN 'collision' WHEN o.row_id IS NULL THEN {old_missing} ELSE {_metadata_issue('o')} END old_issue,
                CASE WHEN a.path_key IS NOT NULL THEN 'collision' WHEN n.row_id IS NULL THEN {new_missing} ELSE {_metadata_issue('n')} END new_issue,
                CASE WHEN o.row_id IS NULL THEN 'new' WHEN n.row_id IS NULL THEN 'old' ELSE 'both' END presence
            FROM old_source o FULL OUTER JOIN new_source n
                ON (CASE WHEN o.key_count=1 THEN o.path_key END)=(CASE WHEN n.key_count=1 THEN n.path_key END)
            LEFT JOIN ambiguous a ON a.path_key=coalesce(n.path_key,o.path_key)),
        classified AS (SELECT *,NOT {baseline} AND old_issue='' AND new_issue='' AS comparable,
            concat_ws('|',NULLIF(old_issue,''),NULLIF(new_issue,'')) reason FROM joined)
        SELECT row_number() OVER () AS item_id,path_key,old_row,new_row,old_kind,new_kind,kind,is_folder,
            "before","after",allocated_before,allocated_after,old_issue,new_issue,reason,presence,comparable,
            old_created,new_created,old_modified,new_modified,
            CASE WHEN comparable THEN coalesce("after",0)-coalesce("before",0) END delta,
            CASE WHEN comparable AND (old_row IS NULL OR allocated_before IS NOT NULL) AND (new_row IS NULL OR allocated_after IS NOT NULL)
                THEN coalesce(allocated_after,0)-coalesce(allocated_before,0) END allocated_delta,
            CASE WHEN {baseline} THEN 'first_scan'
                 WHEN reason<>'' THEN CASE WHEN reason IN ('scope','browser') THEN 'not_scanned' ELSE 'unknown' END
                 WHEN old_row IS NULL THEN 'new' WHEN new_row IS NULL THEN 'missing'
                 WHEN old_kind<>new_kind THEN 'replaced'
                 WHEN old_identity<>'' AND new_identity<>'' AND starts_with(old_identity,'wiz:')=starts_with(new_identity,'wiz:') AND old_identity<>new_identity THEN 'replaced'
                 WHEN "after">"before" THEN 'grown' WHEN "after"<"before" THEN 'shrunk'
                 WHEN (old_allocated_check(allocated_before,allocated_after)) OR ({modified}) OR ({created})
                      OR (old_attrs IS NOT NULL AND new_attrs IS NOT NULL AND old_attrs<>new_attrs) THEN 'modified'
                 ELSE 'same' END change
        FROM classified'''.replace('old_allocated_check(allocated_before,allocated_after)',
                                   'allocated_before IS NOT NULL AND allocated_after IS NOT NULL AND allocated_before<>allocated_after'))
        session.progress(65, 'Tổng hợp chênh lệch file về cây thư mục một lượt', stage='folders')
        self._folder_parts(stop)
        folder_delta = 'NULL' if self.old is None else 'f.logical_delta'
        folder_disk = 'NULL' if self.old is None else 'f.disk_delta'
        db.execute(f'''CREATE TEMP VIEW result AS SELECT c.* EXCLUDE(delta,allocated_delta,change),
            CASE WHEN c.is_folder AND f.path_key IS NOT NULL AND c.reason IN ('','metadata') THEN {folder_delta} ELSE c.delta END delta,
            CASE WHEN c.is_folder AND f.path_key IS NOT NULL AND c.reason IN ('','metadata') THEN {folder_disk} ELSE c.allocated_delta END allocated_delta,
            CASE WHEN c.is_folder AND f.path_key IS NOT NULL AND c.reason IN ('','metadata') AND c.change NOT IN ('replaced','first_scan') THEN
                CASE WHEN f.logical_delta IS NULL THEN 'unknown' WHEN f.unknown_files>0 THEN 'partial'
                     WHEN f.logical_delta>0 THEN 'grown' WHEN f.logical_delta<0 THEN 'shrunk'
                     WHEN c.change='modified' THEN 'modified' ELSE 'same' END ELSE c.change END change,
            coalesce(f.unknown_files,0) unknown_files,coalesce(f.unknown_disk_files,0) unknown_disk_files,
            f.common_before,f.common_after,f.private_before,f.private_after
            FROM comparison c LEFT JOIN folder_parts f ON (CASE WHEN c.is_folder THEN c.path_key END)=f.path_key''')
        # Keep source bytes untouched; the selected view, filters and ORDER BY
        # must all use the same dimension before LIMIT is applied.
        db.execute('''CREATE TEMP VIEW logical_result AS SELECT r.*,
            r.before logical_before,r.after logical_after,r.delta logical_delta,r.change logical_change FROM result r''')
        db.execute('''CREATE TEMP VIEW allocated_result AS SELECT r.* EXCLUDE(before,after,delta,change),
            r.before logical_before,r.after logical_after,r.delta logical_delta,r.change logical_change,
            r.allocated_before AS "before",r.allocated_after AS "after",r.allocated_delta AS delta,
            CASE WHEN r.change IN ('first_scan','not_scanned','unknown','replaced','new','missing') THEN r.change
                 WHEN r.is_folder AND r.unknown_disk_files>0 THEN 'partial'
                 WHEN r.allocated_delta IS NULL THEN 'unknown'
                 WHEN r.allocated_delta>0 THEN 'grown' WHEN r.allocated_delta<0 THEN 'shrunk'
                 WHEN r.change IN ('modified','grown','shrunk','partial') THEN 'modified' ELSE 'same' END change
            FROM result r''')
        session.progress(90, 'Đếm trạng thái và kiểm tra Chung + Riêng = toàn bộ', stage='summary')
        self._summary = self._overview()
        self._summary['seconds'] = time.perf_counter() - started
        session.progress(100, f"Đã so sánh {self._summary['rows']:,} đường dẫn; sẵn sàng tải chi tiết", stage='done')
        self._summary['timing']=self.timeline.report()

    def _folder_parts(self, stop):
        db = self.session.db
        db.execute('''CREATE TEMP TABLE folder_parts(path_key VARCHAR,common_before BIGINT,common_after BIGINT,
            private_before BIGINT,private_after BIGINT,logical_delta BIGINT,disk_delta BIGINT,
            unknown_files BIGINT,unknown_disk_files BIGINT)''')
        folders = [r[0] for r in db.execute('SELECT path_key FROM comparison WHERE is_folder GROUP BY path_key HAVING COUNT(*)=1').fetchall()]
        self.session.progress(66, f'Đã lấy {len(folders):,} khóa thư mục; cộng file theo cha', stage='folders')
        if not folders:
            self.measured = {'common': [0, 0, 0, 0], 'private': [0, 0, 0, 0]}
        else:
            self.measured = {'common': [0, 0, 0, 0], 'private': [0, 0, 0, 0]}
        direct = {}
        for side, sign in (('old', -1), ('new', 1)):
            other = 'new' if side == 'old' else 'old'
            marker = 'true' if side == 'old' else "coalesce(c.old_kind,'')<>'file'"
            allocated_known = "c.comparable AND (coalesce(c.old_kind,'')<>'file' OR c.allocated_before IS NOT NULL) AND (coalesce(c.new_kind,'')<>'file' OR c.allocated_after IS NOT NULL)"
            rows = db.execute(f'''SELECT CASE WHEN c.is_folder THEN c.path_key ELSE s.parent_key END parent,
                c.presence='both' shared,
                CASE WHEN COUNT(*) FILTER(WHERE s.logical IS NULL)>0 THEN NULL ELSE SUM(s.logical) END size,
                coalesce(SUM(CASE WHEN c.comparable THEN {sign}*s.logical ELSE 0 END),0) verified,
                coalesce(SUM(CASE WHEN {allocated_known} THEN {sign}*s.allocated ELSE 0 END),0) disk,
                COUNT(*) FILTER(WHERE NOT c.comparable AND {marker}) AS "unknown",
                COUNT(*) FILTER(WHERE NOT ({allocated_known}) AND {marker}) AS unknown_disk,
                COUNT(*) FILTER(WHERE c.comparable) known_files,COUNT(*) FILTER(WHERE {allocated_known}) known_disk
                FROM {side}_source s JOIN comparison c ON c.{side}_row=s.row_id
                WHERE s.kind='file' GROUP BY 1,2''').fetchall()
            self.session.progress(68 if side == 'old' else 71, f'Đã nhóm file mốc {side}; xử lý {len(rows):,} nhóm cha', stage='folders')
            for parent, shared, size, verified, disk, unknown, unknown_disk, known_files, known_disk in rows:
                vector = direct.setdefault(parent, [0] * 11)
                index = (0 if shared else 2) + (1 if side == 'new' else 0)
                vector[index] = None if vector[index] is None or size is None else vector[index] + size
                for position, value in enumerate((verified, disk, unknown, unknown_disk, known_files, known_disk, 1), 4):
                    vector[position] += value
                measured = self.measured['common' if shared else 'private']
                for index, value in enumerate((verified, disk, unknown, unknown_disk)):
                    measured[index] += value
        if not folders:
            return
        totals = rollup_values(folders, direct, stop=stop, width=11)
        self.session.progress(75, 'Đã tổng hợp cây; tạo bảng chỉ số thư mục gọn trong RAM', stage='folders')
        structure = 'STRUCT(path_key VARCHAR,common_before BIGINT,common_after BIGINT,private_before BIGINT,private_after BIGINT,logical_delta BIGINT,disk_delta BIGINT,unknown_files BIGINT,unknown_disk_files BIGINT)[]'
        batch = []
        for key, values in totals.items():
            common_before, common_after, private_before, private_after, delta, disk, unknown, unknown_disk, known_files, known_disk, _ = values
            batch.append(dict(zip(('path_key', 'common_before', 'common_after', 'private_before', 'private_after',
                                   'logical_delta', 'disk_delta', 'unknown_files', 'unknown_disk_files'),
                                  (key, common_before, common_after, private_before, private_after,
                                   None if unknown and not known_files else delta,
                                   None if unknown_disk and not known_disk else disk, unknown, unknown_disk))))
            if len(batch) >= 10000:
                insert_records(db, 'folder_parts', structure, batch)
                batch.clear()
        if batch:
            insert_records(db, 'folder_parts', structure, batch)

    def _overview(self):
        db = self.session.db
        result = {'older': self.old, 'newer': self.new, 'rows': db.execute('SELECT COUNT(*) FROM result').fetchone()[0],
                  'partition_note': 'Chung: có hai mốc. Riêng: chỉ Cũ/Mới; không quét khác với đã xóa. Tổng chỉ cộng file, không cộng thêm folder.',
                  'persistent_database_bytes': 0, 'volume_assumptions': self.volume_assumptions,
                  'default_measure': 'allocated',
                  'counts': self.session.rows('SELECT kind,change,presence,COUNT(*) count FROM allocated_result GROUP BY 1,2,3'),
                  'counts_logical': self.session.rows('SELECT kind,change,presence,COUNT(*) count FROM logical_result GROUP BY 1,2,3')}
        for group, clause in (('common', "presence='both'"), ('private', "presence<>'both'"), ('all', 'true')):
            result[group + '_rows'] = db.execute('SELECT COUNT(*) FROM result WHERE ' + clause).fetchone()[0]
        for side, scan in (('old', self.old), ('new', self.new)):
            totals = {}
            for row in self.session.rows(f'''SELECT CASE WHEN c.presence='both' THEN 'common' ELSE 'private' END section,
                COUNT(*) files,coalesce(SUM(s.logical),0) logical_known,coalesce(SUM(s.allocated),0) allocated_known,
                COUNT(*) FILTER(WHERE s.logical IS NULL) logical_unknown_files,
                COUNT(*) FILTER(WHERE s.allocated IS NULL) allocated_unknown_files
                FROM {side}_source s JOIN comparison c ON c.{side}_row=s.row_id WHERE s.kind='file' GROUP BY 1'''):
                row['logical'] = None if row['logical_unknown_files'] else row['logical_known']
                row['allocated'] = None if row['allocated_unknown_files'] else row['allocated_known']
                totals[row.pop('section')] = row
            for group in ('common', 'private'):
                result[group + '_' + side] = totals.get(group, {'files': 0, 'logical': 0, 'allocated': 0,
                    'logical_known': 0, 'allocated_known': 0, 'logical_unknown_files': 0, 'allocated_unknown_files': 0}) if scan else None
            if scan:
                result['full_' + side] = scan['summary']
                for measure in ('logical_known', 'allocated_known', 'files'):
                    if sum(result[group + '_' + side][measure] for group in ('common', 'private')) != scan['summary'][measure]:
                        raise ValueError('Chung + Riêng không khớp snapshot; không hiển thị tổng sai')
                from .disk_measurements import reconcile
                allocations = self.session.rows(f'''SELECT split_part(path_key,'|',1) volume,
                    coalesce(SUM(allocated),0) allocated_known,
                    COUNT(*) FILTER(WHERE allocated IS NULL) allocated_unknown_files
                    FROM {side}_source WHERE kind='file' GROUP BY 1''')
                result['disks_' + side] = reconcile(scan, allocations)
        for group in ('common', 'private'):
            logical, disk, unknown, unknown_disk = self.measured[group]
            result['verified_' + group + '_delta'] = logical if self.old else None
            result['verified_' + group + '_disk_delta'] = disk if self.old else None
            result['unknown_' + group + '_files'] = unknown
            result['unknown_' + group + '_disk_files'] = unknown_disk
        result['partition_valid'] = result['common_rows'] + result['private_rows'] == result['rows']
        return result

    def summary(self):
        return self._summary

    def _fingerprint(self, options):
        return hashlib.sha256(json.dumps([self.token, options], sort_keys=True).encode()).hexdigest()

    def page(self, *, limit=20, cursor=None, sort='abs_delta', direction='desc', root=None,
             recursive=True, section='all', changes='all', kind='all', minimum=0,
             search='', presence='all', folders_only=False, root_keys=None, measure='allocated', reason=None, stop=None):
        if measure not in ('allocated', 'logical'):
            raise ValueError('Chỉ số phải là allocated hoặc logical')
        if not 1 <= limit <= 1000 or sort not in SORTS or direction not in ('asc', 'desc'):
            raise ValueError('Trang từ 1–1000 và cột/hướng sắp xếp phải hợp lệ')
        if section not in ('all', 'common', 'private') or presence not in ('all', 'old', 'new', 'both') or kind not in ('all', 'file', 'folder', 'link'):
            raise ValueError('Bộ lọc không hợp lệ')
        if minimum < 0:
            raise ValueError('Ngưỡng size không âm')
        options = dict(sort=sort, direction=direction, root=root, recursive=recursive,
                       section=section, changes=changes, kind=kind, minimum=minimum,
                       search=search, presence=presence, folders_only=folders_only, root_keys=root_keys, measure=measure)
        if reason is not None:
            if reason not in REASONS:raise ValueError('Lý do không hợp lệ')
            options['reason']=reason
        relation = measure + '_result'
        signature = self._fingerprint(options)
        offset = 0
        if cursor:
            try:
                value = json.loads(base64.urlsafe_b64decode(cursor))
                offset = value['offset']
                if value['signature'] != signature or not isinstance(offset, int) or offset < 0:
                    raise ValueError()
            except Exception as exc:
                raise ValueError('Con trỏ không thuộc cặp/bộ lọc hiện tại; tải lại từ đầu') from exc
        clauses, parameters = [], []
        if reason is not None:
            clauses.append("contains('|'||coalesce(r.reason,'')||'|',?)");parameters.append('|'+reason+'|')
        if root_keys is not None:
            if not root_keys or len(root_keys)>1000:
                raise ValueError('Danh sách gốc cây thư mục không hợp lệ')
            clauses.append('r.path_key IN (' + ','.join('?' for _ in root_keys) + ')'); parameters.extend(root_keys)
        if section != 'all':
            clauses.append("r.presence='both'" if section == 'common' else "r.presence<>'both'")
        if presence != 'all':
            clauses.append('r.presence=?'); parameters.append(presence)
        if kind != 'all':
            clauses.append('r.kind=?'); parameters.append(kind)
        if folders_only:
            clauses.append('r.is_folder')
        if changes != 'all':
            selected = [word.strip() for word in changes.split(',') if word.strip()]
            if not selected:
                raise ValueError('Bộ lọc trạng thái trống')
            clauses.append('r.change IN (' + ','.join('?' for _ in selected) + ')'); parameters.extend(selected)
        if minimum:
            clauses.append('greatest(coalesce(r.before,0),coalesce(r.after,0))>=?'); parameters.append(minimum)
        if search:
            try:
                term = self.scope_new.keys.key(search)
            except ValueError:
                term = search.replace('/', '\\')
            clauses.append('contains(lower(r.path_key),lower(?))'); parameters.append(term)
        if root:
            key = root if '|' in root else self.scope_new.keys.key(root)
            separator = '/' if key.startswith('mount:/|') else '\\'
            prefix = key if key.endswith('|') else key.rstrip('\\/') + separator
            if recursive:
                clauses.append('starts_with(r.path_key,?) AND r.path_key<>?'); parameters.extend((prefix, key))
            else:
                clauses.append('starts_with(r.path_key,?) AND NOT contains(substr(r.path_key,?),?) AND r.path_key<>?')
                parameters.extend((prefix, len(prefix) + 1, separator, key))
        where = ' AND '.join(clauses) or 'true'
        with self.session.operation(stop):
            total = self.session.db.execute(f'SELECT COUNT(*) FROM {relation} r WHERE ' + where, parameters).fetchone()[0]
            order = f"{SORTS[sort]} {direction} NULLS LAST,r.path_key ASC,r.item_id ASC"
            selected = f'SELECT r.* FROM {relation} r WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?'
            rows = self.session.rows(f'''WITH chosen AS ({selected})
                SELECT c.*,coalesce(n.path,o.path) path,
                    coalesce(n.parent,o.parent) parent,n.created new_created,o.created old_created,
                    n.modified new_modified,o.modified old_modified
                FROM chosen c LEFT JOIN new_source n ON n.row_id=c.new_row
                LEFT JOIN old_source o ON o.row_id=c.old_row ORDER BY
                {order.replace('r.', 'c.')}''', [*parameters, limit, offset])
            for row in rows:
                tags = set((row.get('reason') or '').split('|'))
                row['reason_text'] = ' • '.join(text for tag, text in REASONS.items() if tag in tags)
                row['baseline'] = self.old is None
                row['source_new'] = self.new['id']
                row['source_old'] = self.old['id'] if self.old else None
                row['measurement'] = measure
                row['partial_delta'] = bool(row['is_folder'] and row['unknown_disk_files' if measure == 'allocated' else 'unknown_files'])
                if measure == 'allocated':
                    notes = [label+' thiếu cấp phát' for side,label in (('old','Cũ'),('new','Mới'))
                             if row[side+'_row'] is not None and row['allocated_before' if side=='old' else 'allocated_after'] is None]
                    if row['partial_delta']:
                        notes.append('Chênh lệch chỉ gồm phần xác minh được; không khẳng định toàn thư mục tăng/giảm từng ấy')
                    row['reason_text'] = ' • '.join([row['reason_text'], *notes]).strip(' •')
            next_cursor = None
            if offset + len(rows) < total:
                next_cursor = base64.urlsafe_b64encode(json.dumps({'offset': offset + len(rows), 'signature': signature}).encode()).decode()
            return {'rows': rows, 'cursor': next_cursor, 'total': total, 'loaded': offset + len(rows),
                    'sort_scope': root or 'Toàn bộ snapshot', 'summary': self._summary, 'measurement': measure}

    def tree(self, root=None, *, limit=100, cursor=None, measure='allocated', stop=None):
        if root is not None:
            return self.page(root=root, recursive=False, folders_only=True, sort='new',
                             limit=limit, cursor=cursor, measure=measure, stop=stop)
        # A tree root is a captured scope root, not an extra copy of every folder.
        keys = {self.scope_new.keys.key(path) for path in self.new['scope']['roots']}
        if self.old:
            keys.update(self.scope_old.keys.key(path) for path in self.old['scope']['roots'])
        result = self.page(folders_only=True, sort='path', direction='asc', limit=1000, root_keys=sorted(keys), measure=measure, stop=stop)
        result['cursor'] = None
        result['total'] = len(result['rows'])
        return result

    def details(self, item_id, *, stop=None):
        with self.session.operation(stop):
            found = self.session.rows('SELECT old_row,new_row FROM comparison WHERE item_id=?', [int(item_id)])
            if not found:
                raise ValueError('Dòng không còn thuộc phiên so sánh')
            result = {}
            for side in ('old', 'new'):
                row_id = found[0][side + '_row']
                result[side] = self.session.rows(f'SELECT * EXCLUDE(key_count) FROM {side}_source WHERE row_id=?', [row_id])[0] if row_id is not None else None
            return result

    def close(self):
        self.session.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
