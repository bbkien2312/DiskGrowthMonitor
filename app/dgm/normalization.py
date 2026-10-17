"""Versioned, reversible history overlays. Never rewrite a captured snapshot.

Only changed fields are stored; source records stay in history.sqlite. The work
table is SQLite-backed so millions of files don't become Python dictionaries.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path

from .config import home, comparison_path_key
from .platform_ops import ProcessCancelled
from .store import now

VERSION = 2
FIELDS = ('kind', 'logical', 'allocated', 'created', 'modified', 'identity', 'attrs', 'status')
STATES = {'verified': 'Đã kiểm chứng phép tính', 'limited': 'Có giới hạn dữ liệu',
          'needs-source': 'Thiếu nguồn để phục hồi chính xác', 'original': 'Đang dùng bản gốc'}
_lock = threading.RLock()


def fingerprint(scan):
    values = [scan[k] for k in ('id', 'started', 'finished', 'status', 'config', 'summary')]
    return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode()).hexdigest()


def effective_rows(rows, history_path=None):
    """Cheap summary lookup, including from a readonly Store. No auto migration."""
    from .history_backend import database_path,log_mode
    path = (Path(history_path).parent / 'normalization.sqlite') if history_path else database_path('normalization.sqlite')
    if log_mode() and not path.exists():
        instance=Normalizations();instance.close()
    if not path.exists(): return rows
    db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=30)
    try:
        # Another startup worker can have opened the database file but not
        # yet created its schema. No published overlay exists at that point.
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='datasets'").fetchone():return rows
        active = {r[0]: r for r in db.execute('SELECT scan_id,fingerprint,report,id FROM datasets WHERE active=1 AND version=?', (VERSION,))}
        for row in rows:
            item = active.get(row['id'])
            if item and item[1] == fingerprint(row):
                report = json.loads(item[2]); original = row['summary']
                totals = json.loads(original or '{}')
                totals.update(report['after'])
                totals['normalization'] = {'version': VERSION, 'dataset_id': item[3], 'state': report['state'],
                                           'warnings': report['warnings']}
                row['raw_summary'] = original
                row['summary'] = json.dumps(totals, ensure_ascii=False)
        return rows
    finally: db.close()


class Normalizations:
    def __init__(self, stop=None, notify=None):
        from .storage_safety import SpaceConnection
        from .history_backend import database_path,log_mode,manager
        self.stop = stop or threading.Event(); self.notify = notify or (lambda *a: None)
        self.path = database_path('normalization.sqlite')
        self.db = sqlite3.connect(self.path, timeout=30,uri=True,factory=SpaceConnection)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA cache_size=-16384')
        self.db.create_function('dgm_path_key', 1, comparison_path_key, deterministic=True)
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS datasets(id INTEGER PRIMARY KEY,scan_id INTEGER,version INTEGER,
          fingerprint TEXT,created TEXT,active INTEGER DEFAULT 0,report TEXT);
        CREATE UNIQUE INDEX IF NOT EXISTS one_active ON datasets(scan_id) WHERE active=1;
        CREATE INDEX IF NOT EXISTS dataset_scan ON datasets(scan_id,version,fingerprint);
        CREATE TABLE IF NOT EXISTS preferences(scan_id INTEGER PRIMARY KEY,use_original INTEGER);
        CREATE TABLE IF NOT EXISTS overrides(dataset_id INTEGER,path_id INTEGER,kind TEXT,logical INTEGER,
          allocated INTEGER,created REAL,modified REAL,identity TEXT,attrs INTEGER,status TEXT,
          PRIMARY KEY(dataset_id,path_id)) WITHOUT ROWID;
        ''')
        self.db.execute('ATTACH DATABASE ? AS source', (database_path('history.sqlite').as_uri() + '?mode=ro',))
        if log_mode():manager().bind_normalization(self.db)
        self.last_pulse = time.monotonic()
        self.db.set_progress_handler(self._progress, 4000)

    def original_enabled(self,scan_id):
        return bool(self.db.execute('SELECT 1 FROM preferences WHERE scan_id=? AND use_original=1',(scan_id,)).fetchone())

    def _progress(self):
        if self.stop.is_set(): return 1
        if self.db.space_guard and self.db.space_guard.progress():return 1
        if time.monotonic() - self.last_pulse > 2:
            self.notify('info', {'message': 'Đang kiểm chứng metadata lịch sử; không quét lại file trên ổ'})
            self.last_pulse = time.monotonic()
        return 0

    def close(self):
        self.db.set_progress_handler(None, 0); self.db.close()

    def current(self, scan_id):
        scan = self.db.execute('SELECT * FROM source.scans WHERE id=?', (scan_id,)).fetchone()
        if scan is None: raise ValueError('Không tìm thấy lượt quét')
        result=self.db.execute('SELECT * FROM datasets WHERE scan_id=? AND active=1 AND version=? AND fingerprint=?',
                               (scan_id, VERSION, fingerprint(scan))).fetchone()
        from .history_backend import log_mode,manager
        if result and log_mode():manager().load_normalization(self.db,result['id'])
        return result

    def inspect(self, scan_id, *, apply=False, force=False):
        from .history_backend import snapshots
        with snapshots([scan_id],stop=self.stop,notify=self.notify):return self._inspect_loaded(scan_id,apply=apply,force=force)

    def _inspect_loaded(self,scan_id,*,apply=False,force=False):
        while not _lock.acquire(timeout=.05):
            if self.stop.is_set(): raise ProcessCancelled()
        self.db.space_guard=None
        try: return self._inspect(scan_id, apply=apply, force=force)
        except BaseException as exc:
            self.db.set_progress_handler(None, 0); self.db.rollback()
            if self.db.space_guard and self.db.space_guard.error:raise self.db.space_guard.error from exc
            if self.stop.is_set(): raise ProcessCancelled('Đã dừng chuẩn hóa; bản gốc và bản đang dùng được giữ nguyên') from exc
            raise
        finally:
            self.db.set_progress_handler(self._progress, 4000); _lock.release()

    def _inspect(self, scan_id, *, apply, force):
        from .progress import TaskProgress
        if self.stop.is_set(): raise ProcessCancelled()
        scan = self.db.execute('SELECT * FROM source.scans WHERE id=?', (scan_id,)).fetchone()
        if scan is None: raise ValueError('Không tìm thấy lượt quét')
        if scan['status'] not in ('complete', 'partial'):
            raise ValueError('Chỉ chuẩn hóa lượt đã nhập xong; lượt lỗi/gián đoạn cần kiểm tra phục hồi trước.')
        current = self.current(scan_id)
        if current and not force: return json.loads(current['report'])
        task = TaskProgress('normalization', self.notify, [('read','Đọc bản gốc',15),
            ('files','Chuẩn hóa file và hardlink',50), ('folders','Tính lại thư mục',85), ('save','Đối soát và lưu lớp chuẩn hóa',100)])
        task.stage('read'); started = time.perf_counter()
        original = json.loads(scan['summary'] or '{}'); warnings = []
        from .storage_metrics import begin as begin_storage,finish as finish_storage
        storage_meter=begin_storage('normalization')
        if not force and original.get('canonical_normalization_version') in (1,VERSION) and original.get('import_complete'):
            # New scans already used the exact normalization rules on ingestion.
            # Register provenance, don't walk the same million rows a second time.
            totals={k:original.get(k,0) for k in ('logical','allocated','files','folders','links')}
            for key in ('identity_unique_logical_estimate','identity_unknown_files'):
                if key in original:totals[key]=original[key]
            fields=('allocated_known','allocated_unknown_files','logical_unknown_files')
            if all(k in original for k in fields):totals.update({k:original[k] for k in fields})
            else:
                totals.update(dict(self.db.execute('''SELECT COALESCE(SUM(r.allocated),0) allocated_known,
                  COUNT(CASE WHEN r.allocated IS NULL THEN 1 END) allocated_unknown_files,
                  COUNT(CASE WHEN r.logical IS NULL THEN 1 END) logical_unknown_files
                  FROM source.members m JOIN source.revisions r ON r.id=m.revision_id
                  WHERE m.scan_id=? AND r.kind='file' ''',(scan_id,)).fetchone()))
            status_upgrades=self.db.execute("SELECT COUNT(*) FROM source.members m JOIN source.revisions r ON r.id=m.revision_id WHERE m.scan_id=? AND r.kind='file' AND r.allocated IS NULL AND r.status='ok'",(scan_id,)).fetchone()[0] if original.get('canonical_normalization_version',0)<2 else 0
            warnings=list(original.get('data_warnings',[]))
            if original.get('coverage_unverified') or original.get('errors'):
                warnings.append('Phép tính đã thực hiện khi nhập; độ bao phủ vẫn theo giới hạn của lượt quét.')
            if totals['allocated'] is None:warnings.append('Có file chưa biết dung lượng cấp phát; không coi là 0.')
            report={'scan_id':scan_id,'version':VERSION,'fingerprint':fingerprint(scan),'before':totals,'after':totals,
                'stored_summary':original,'changed_rows':status_upgrades,'metadata_status_upgrade':bool(status_upgrades),'warnings':warnings,'state':'limited' if warnings else 'verified',
                'applied':bool(apply),'source_preserved':True,'seconds':round(time.perf_counter()-started,3),
                'verified_on_ingestion':True,'estimated_overlay_bytes':status_upgrades*160}
            if apply:self._publish(scan_id,report,storage_meter,copy_overrides=False)
            task.finish('Đã đọc chuẩn hóa được thực hiện lúc quét; không tính lại file/thư mục')
            return report
        from .storage_safety import SpaceGuard
        self.db.space_guard=SpaceGuard((original.get('files',0)+original.get('folders',0))*900,
            operation='chuẩn hóa lịch sử và bảng tạm')
        for table in ('work', 'owners', 'direct'):
            self.db.execute(f'DROP TABLE IF EXISTS temp.{table}')
        columns={r[1] for r in self.db.execute('PRAGMA source.table_info(paths)')}
        keys='p.path_key,p.parent_key' if 'path_key' in columns else 'dgm_path_key(p.path) AS path_key,dgm_path_key(p.parent) AS parent_key'
        self.db.execute(f'''CREATE TEMP TABLE work AS SELECT m.path_id,p.path,p.parent,{keys},
          r.kind,r.logical,r.allocated,r.created,r.modified,r.identity,r.attrs,r.status
          FROM source.members m JOIN source.paths p ON p.id=m.path_id
          JOIN source.revisions r ON r.id=m.revision_id WHERE m.scan_id=?''', (scan_id,))
        self.db.execute('CREATE UNIQUE INDEX temp.work_id ON work(path_id)')
        before = self._totals()
        task.complete(f"Đã đọc {before['files']:,} file / {before['folders']:,} thư mục từ lịch sử")
        task.stage('files')
        invalid = self.db.execute('SELECT COUNT(*) FROM work WHERE logical<0 OR allocated<0 OR kind NOT IN (\'file\',\'folder\',\'link\')').fetchone()[0]
        if invalid: raise ValueError(f'{invalid} bản ghi có kích thước/loại không hợp lệ; không tự sửa thành 0.')
        self.db.execute("UPDATE work SET created=NULL WHERE created<=0")
        self.db.execute("UPDATE work SET modified=NULL WHERE modified<=0")
        self.db.execute("UPDATE work SET status='metadata-partial' WHERE kind='file' AND allocated IS NULL AND status='ok'")
        self.db.execute("UPDATE work SET kind='link',logical=0,allocated=0 WHERE (COALESCE(attrs,0)&1024)<>0 OR kind='link'")
        self.db.execute('CREATE INDEX temp.work_identity ON work(identity,kind,path_key,path_id)')
        self.db.execute('''CREATE TEMP TABLE owners AS SELECT identity,MIN(path_id) AS owner,
          MAX(allocated) AS allocated,COUNT(*) AS paths,SUM(logical)-MAX(logical) AS repeated_logical,
          SUM(allocated IS NULL) AS unknown,MAX(logical) AS logical,MAX(COALESCE(attrs,0)&2560) AS special
          FROM work WHERE kind='file' AND identity<>'' GROUP BY identity''')
        self.db.execute('CREATE UNIQUE INDEX temp.owners_identity ON owners(identity)')
        # Stable lexical owner, with path_id as a tie breaker for ambiguous case.
        self.db.execute('''UPDATE owners SET owner=(SELECT path_id FROM work w WHERE w.identity=owners.identity
          AND w.kind='file' ORDER BY w.path_key,w.path_id LIMIT 1)''')
        hardlinks = dict(self.db.execute('''SELECT COALESCE(SUM(paths-1),0) AS extra_paths,
          COALESCE(SUM(repeated_logical),0) AS repeated_logical FROM owners WHERE paths>1''').fetchone())
        damaged = 0
        if original.get('contract', {}).get('version', 0) < 3:
            damaged = self.db.execute('SELECT COUNT(*) FROM owners WHERE paths>1 AND logical>0 AND allocated=0 AND special=0').fetchone()[0]
            self.db.execute('UPDATE owners SET allocated=NULL WHERE paths>1 AND logical>0 AND allocated=0 AND special=0')
        # One missing hardlink allocation makes the physical total uncertain even
        # if another path happens to contain an allocation value.
        self.db.execute('UPDATE owners SET allocated=NULL WHERE unknown>0')
        self.db.execute('''UPDATE work AS w SET allocated=CASE WHEN w.path_id=o.owner THEN o.allocated ELSE 0 END
          FROM owners o WHERE w.identity=o.identity AND w.kind='file' ''')
        if damaged: warnings.append(f'{damaged:,} nhóm hardlink bản cũ có thể đã mất số cấp phát; cần CSV gốc, không dùng số 0 để kết luận.')
        duplicate_keys = self.db.execute('SELECT COUNT(*) FROM (SELECT path_key FROM work GROUP BY path_key HAVING COUNT(*)>1)').fetchone()[0]
        if duplicate_keys: warnings.append(f'{duplicate_keys:,} khóa đường dẫn không duy nhất; giữ riêng và không kết luận thay đổi chắc chắn.')
        task.complete(f"Đã đối soát {hardlinks['extra_paths']:,} đường dẫn hardlink bổ sung")
        task.stage('folders')
        self.db.execute('''CREATE TEMP TABLE direct AS SELECT parent_key,
          CASE WHEN SUM(logical IS NULL)>0 THEN NULL ELSE SUM(logical) END AS logical,
          CASE WHEN SUM(allocated IS NULL)>0 THEN NULL ELSE SUM(allocated) END AS allocated
          FROM work WHERE kind='file' GROUP BY parent_key''')
        direct = {r['parent_key']: [r['logical'], r['allocated']] for r in self.db.execute('SELECT * FROM direct')}
        folders = self.db.execute("SELECT path_id,path_key,parent_key FROM work WHERE kind='folder' ORDER BY LENGTH(path) DESC,path_id").fetchall()
        from .folder_totals import rollup
        totals,orphans=rollup(folders,direct,self.stop)
        if orphans: warnings.append(f'{orphans:,} nhánh thiếu thư mục cha duy nhất; cộng về tổ tiên đã lưu gần nhất, không suy đoán thư mục bị thiếu.')
        self.db.executemany('UPDATE work SET logical=?,allocated=? WHERE path_id=?',
                            ((v[0],v[1],pid) for pid,v in totals.items()))
        after = self._totals()
        after['identity_unique_logical_estimate']=None if after['logical'] is None else after['logical']-hardlinks['repeated_logical']
        unknown = self.db.execute("SELECT COUNT(*) FROM work WHERE kind='file' AND (allocated IS NULL OR logical IS NULL)").fetchone()[0]
        if unknown: warnings.append(f'{unknown:,} file thiếu dung lượng đã biết; tổng liên quan hiển thị chưa xác định, không coi là 0.')
        if original.get('coverage_unverified') or scan['status']=='partial': warnings.append('Kiểm chứng phép tính không xác minh lại độ bao phủ của lần quét cũ.')
        if not original.get('effective_scope'): warnings.append('Lượt cũ chưa ghi đường dẫn tự loại trừ của app; không suy đoán phạm vi đó từ cấu hình hiện tại.')
        raw = original.get('root_overview', {})
        reconciliation = []
        for root, values in raw.items():
            key = comparison_path_key(root)
            matches = [r for r in folders if r['path_key']==key]
            value = totals[matches[0]['path_id']] if len(matches)==1 else [None,None]
            reconciliation.append({'root':root, 'source_logical':values.get('logical'), 'source_allocated':values.get('allocated'),
                'normalized_logical':value[0], 'normalized_allocated':value[1],
                'logical_difference':None if value[0] is None or values.get('logical') is None else value[0]-values['logical'],
                'note':'Tổng export có thể gồm vùng app loại trừ; chỉ các lượt có nhật ký nhập gốc mới phân rã chính xác được.'})
        if any(r['logical_difference'] not in (None,0) for r in reconciliation) and not original.get('ingestion_audit'):
            warnings.append('Tổng export gốc khác tổng file đã lưu; thiếu nhật ký nhập gốc để giải thích đầy đủ chênh lệch lịch sử.')
        for volume in original.get('volumes', {}).values():
            if after['allocated'] is not None and len(original.get('volumes', {}))==1 and after['allocated']>volume['total']:
                warnings.append('Tổng cấp phát vượt sức chứa ổ: cần kiểm tra nguồn, danh tính file và phạm vi.')
        task.complete(f"Đã tính lại {after['folders']:,} thư mục; không cộng trùng cha/con")
        task.stage('save')
        changed = self.db.execute('''SELECT COUNT(*) FROM work w JOIN source.members m ON m.scan_id=? AND m.path_id=w.path_id
          JOIN source.revisions r ON r.id=m.revision_id WHERE '''+' OR '.join(f'w.{f} IS NOT r.{f}' for f in FIELDS), (scan_id,)).fetchone()[0]
        report = {'scan_id':scan_id, 'version':VERSION, 'fingerprint':fingerprint(scan), 'before':before, 'stored_summary':original,
            'after':after, 'hardlinks':hardlinks, 'damaged_groups':damaged, 'duplicate_path_keys':duplicate_keys,
            'changed_rows':changed, 'reconciliation':reconciliation, 'warnings':warnings,
            'estimated_overlay_bytes':changed*160,
            'state':'needs-source' if damaged else 'limited' if warnings else 'verified', 'applied':bool(apply),
            'seconds':round(time.perf_counter()-started,3), 'source_preserved':True}
        # Preview stays in TEMP only. Applying publishes a complete overlay in a
        # single transaction; interruption cannot leave half-normalized history.
        self.db.commit()
        if apply:self._publish(scan_id,report,storage_meter)
        task.finish(f"{'Đã lưu lớp chuẩn hóa' if apply else 'Đã kiểm tra, chưa thay đổi lớp đang dùng'} • {changed:,} mục khác bản gốc")
        return report

    def _publish(self,scan_id,report,storage_meter,*,copy_overrides=True):
        from .storage_metrics import finish as finish_storage
        self.db.execute('BEGIN IMMEDIATE')
        self.db.execute('UPDATE datasets SET active=0 WHERE scan_id=?', (scan_id,))
        self.db.execute('DELETE FROM preferences WHERE scan_id=?',(scan_id,))
        ident = self.db.execute('INSERT INTO datasets(scan_id,version,fingerprint,created,active,report) VALUES(?,?,?,?,1,?)',
            (scan_id,VERSION,report['fingerprint'],now(),json.dumps(report,ensure_ascii=False))).lastrowid
        if report.get('metadata_status_upgrade'):
            self.db.execute('''INSERT INTO overrides SELECT ?,m.path_id,r.kind,r.logical,r.allocated,r.created,r.modified,r.identity,r.attrs,'metadata-partial'
              FROM source.members m JOIN source.revisions r ON r.id=m.revision_id
              WHERE m.scan_id=? AND r.kind='file' AND r.allocated IS NULL AND r.status='ok' ''',(ident,scan_id))
        if copy_overrides:self.db.execute(f'''INSERT INTO overrides SELECT ?,w.path_id,{','.join('w.'+f for f in FIELDS)}
          FROM work w JOIN source.members m ON m.scan_id=? AND m.path_id=w.path_id
          JOIN source.revisions r ON r.id=m.revision_id WHERE '''+' OR '.join(f'w.{f} IS NOT r.{f}' for f in FIELDS), (ident,scan_id))
        self.db.commit()
        # Publishing is complete. Footprint/retention bookkeeping must not turn
        # an already committed success into a misleading cancellation.
        self.db.set_progress_handler(None,0)
        report['storage']=finish_storage(storage_meter)
        self.db.execute('UPDATE datasets SET report=? WHERE id=?',(json.dumps(report,ensure_ascii=False),ident));self.db.commit()
        stale=[r[0] for r in self.db.execute('SELECT id FROM datasets WHERE scan_id=? ORDER BY id DESC LIMIT -1 OFFSET 2',(scan_id,))]
        for old in stale:
            self.db.execute('DELETE FROM overrides WHERE dataset_id=?',(old,));self.db.execute('DELETE FROM datasets WHERE id=?',(old,))
        self.db.commit()
        from .history_backend import log_mode,manager
        if log_mode():manager().publish_normalization(self.db,ident)

    def _totals(self):
        return dict(self.db.execute('''SELECT CASE WHEN SUM(kind='file' AND logical IS NULL)>0 THEN NULL
          ELSE COALESCE(SUM(CASE WHEN kind='file' THEN logical ELSE 0 END),0) END AS logical,
          CASE WHEN SUM(kind='file' AND allocated IS NULL)>0 THEN NULL
          ELSE COALESCE(SUM(CASE WHEN kind='file' THEN allocated ELSE 0 END),0) END AS allocated,
          COALESCE(SUM(kind='file'),0) AS files,COALESCE(SUM(kind='folder'),0) AS folders,COALESCE(SUM(kind='link'),0) AS links,
          COALESCE(SUM(CASE WHEN kind='file' THEN allocated ELSE 0 END),0) AS allocated_known,
          COALESCE(SUM(kind='file' AND allocated IS NULL),0) AS allocated_unknown_files,
          COALESCE(SUM(kind='file' AND logical IS NULL),0) AS logical_unknown_files,
          COALESCE(SUM(kind='file' AND identity=''),0) AS identity_unknown_files FROM work''').fetchone())

    def restore(self, scan_ids):
        with _lock:
            try:
                self.db.execute('BEGIN IMMEDIATE')
                self.db.executemany('UPDATE datasets SET active=0 WHERE scan_id=?', ((int(i),) for i in scan_ids))
                self.db.executemany('INSERT OR REPLACE INTO preferences VALUES(?,1)',((int(i),) for i in scan_ids));self.db.commit()
            except BaseException:self.db.rollback();raise
        return {'restored_original':list(scan_ids)}

    def delete_for_scans(self, scan_ids):
        with _lock:
            for ident in scan_ids:
                self.db.execute('DELETE FROM overrides WHERE dataset_id IN (SELECT id FROM datasets WHERE scan_id=?)',(ident,))
                self.db.execute('DELETE FROM datasets WHERE scan_id=?',(ident,))
                self.db.execute('DELETE FROM preferences WHERE scan_id=?',(ident,))
            self.db.commit()
