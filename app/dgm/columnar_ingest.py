"""One-time ingestion into immutable Parquet; captured file paths are never reopened."""
from __future__ import annotations

import csv
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import threading
import time
from zoneinfo import ZoneInfo

from .columnar_store import (Repository, MemorySession, CapturedPaths, SCHEMA,
                             quote, read_json, safe_path, utc_now)
from .platform_ops import ProcessCancelled

FIELDS = ('path', 'parent', 'kind', 'logical', 'allocated', 'created', 'modified',
          'identity', 'attrs', 'status', 'note')
RAW_SCHEMA = ','.join(f'{name} VARCHAR' for name in FIELDS)
RAW_STRUCT = 'STRUCT(' + RAW_SCHEMA + ')[]'
PATH_KEY_VERSION = 2
OVERLAY_FIELDS = ('kind', 'logical', 'allocated', 'created', 'modified', 'identity', 'attrs', 'status')


def insert_records(db, table, structure, batch, columns='r.*'):
    # Bind one JSON scalar, not nested Python objects. This avoids DuckDB's
    # Python object/replacement-scan import path and bounds conversion overhead.
    db.execute(f'INSERT INTO {table} SELECT {columns} FROM unnest(from_json(?,?)) t(r)',
               [json.dumps(batch, ensure_ascii=False, separators=(',', ':')), json.dumps(structure)])


def _check(stop):
    if stop is not None and stop.is_set():
        raise ProcessCancelled('Đã dừng nhập; nguồn và lịch sử cũ giữ nguyên')


def verify_jsonl(path, *, stop=None, expected=None, columns=FIELDS):
    """Verify framing/digest without decoding a million metadata rows in Python."""
    digest = hashlib.sha256()
    records = 0
    with gzip.open(path, 'rb') as stream:
        header = json.loads(stream.readline(4 * 1024 * 1024 + 1))
        if header.get('format') != 'dgm-metadata-jsonl-1' or header.get('columns') != list(columns):
            raise ValueError('Log JSONL sai format/cột; không tự đoán schema')
        for line in stream:
            if records % 10000 == 0:
                _check(stop)
            if len(line) > 4 * 1024 * 1024:
                raise ValueError('Dòng metadata dài bất thường')
            if line.startswith(b'{"end":'):
                footer = json.loads(line)['end']
                if footer != {'sha256': digest.hexdigest(), 'records': records}:
                    raise ValueError('Checksum/số dòng JSONL không khớp')
                if stream.read(1):
                    raise ValueError('Log có dữ liệu sau footer')
                if expected and any(footer[key] != expected[key] for key in ('sha256', 'records')):
                    raise ValueError('Log không khớp catalog nguồn')
                return header, footer
            if not (line.startswith(b'[') or line.startswith(b'{"error":')):
                raise ValueError('Log có bản ghi không nhận diện được')
            digest.update(line)
            records += 1
    raise ValueError('Log chưa hoàn tất; thiếu footer')


def metadata_for(roots, *, engine='wiztree', profile='preview', scope=None, **extra):
    from . import __version__
    return {'started': utc_now(), 'profile': profile, 'engine': engine,
            'app_version': __version__, 'columnar_version': 1, 'path_key_version':PATH_KEY_VERSION,
            'scope': scope or {'roots': list(roots), 'excludes': [], 'strict_browsers': False,
                               'implicit_excludes': [], 'volumes': []},
            'coverage': {'source': 'export', 'unknown_roots': list(roots), 'error_regions': []},
            **extra}


def _json_arrays(db, path, *, table='raw', fields=FIELDS):
    selectors = '[' + ','.join(quote(f'$[{index}]') for index in range(len(fields))) + ']'
    columns = ','.join(f'a[{index + 1}] AS {name}' for index, name in enumerate(fields))
    db.execute(f'''CREATE TEMP TABLE {table} AS WITH array_source AS (
        SELECT json_extract_string(json,{selectors}) a
        FROM read_json_objects(?,format='newline_delimited')
        WHERE json_type(json)='ARRAY') SELECT {columns} FROM array_source''', [str(path)])


def _date_expression(column, mode):
    if mode == 'epoch':
        return f"TRY_CAST(NULLIF({column},'') AS DOUBLE)"
    parsed = f'TRY_CAST({_date_text(column)} AS TIMESTAMP)'
    return f"CASE WHEN year({parsed})<=1900 THEN NULL ELSE epoch({parsed}) END"


def _date_text(column):
    pattern=quote(r'^(\d{4})[ /.-](\d{2})[ /.-](\d{2})([ T])')
    replacement=quote(r'\1-\2-\3\4')
    return f"regexp_replace(NULLIF(trim({column}),''),{pattern},{replacement})"


def _scope_filter(metadata, paths):
    scope = metadata['scope']
    roots = [paths.key(root) for root in scope['roots']]
    def inside(key):
        prefix = key if key.endswith('|') else key.rstrip('\\/') + ('/' if paths.anchor(scope['roots'][0]) == '/' else '\\')
        return f'(captured_key={quote(key)} OR starts_with(captured_key,{quote(prefix)}))'
    result = '(' + ' OR '.join(inside(key) for key in roots) + ')'
    for pattern in list(scope.get('excludes') or []) + list(scope.get('implicit_excludes') or []):
        try:
            key = paths.key(pattern)
        except ValueError:
            key = str(pattern).replace('/', '\\').lower()
        if any(char in str(pattern) for char in '*?['):
            if '|' in key:
                namespace, tail = key.split('|', 1)
                namespace = namespace.replace('[', '[[]').replace('*', '[*]').replace('?', '[?]')
                key = namespace + '|' + tail
            result += f' AND NOT (captured_key GLOB {quote(key)})'
        else:
            result += ' AND NOT ' + inside(key)
    if scope.get('strict_browsers'):
        browser = r'(^|[/\\])(chrome|chrome beta|chrome dev|chrome sxs|edge|edge beta|edge dev|edge sxs|edgecore|edgeupdate|edgewebview|googleupdater)([/\\]|$)|microsoft-edge-webview|microsoft-edge_'
        result += f' AND NOT regexp_matches(lower(path),{quote(browser)})'
    return result


def _native_path_sql(paths):
    """Fast path for normalized drive-letter exports, fallback only for odd paths."""
    slash = quote('\\')
    repeated = quote('\\\\')
    regular = quote(r'^[A-Za-z]:\\')
    dots = quote(r'(^|\\)\.\.?($|\\)')
    clean = f"replace(path,'/',{slash})"
    # Extended drive-letter names are lexical aliases, not a reason to stat.
    extended = quote('\\\\?\\')
    trailing=quote(r'[ .](\\|$)')
    clean = f"CASE WHEN starts_with({clean},{extended}) AND substr({clean},6,1)=':' AND NOT regexp_matches(substr({clean},5),{trailing}) THEN substr({clean},5) ELSE {clean} END"
    trim_edges=quote(r'[ .]+(\\|$)')
    backref=quote(r'\1')
    clean=f"CASE WHEN regexp_matches({clean},{dots}) OR starts_with({clean},{extended}) THEN {clean} ELSE regexp_replace({clean},{trim_edges},{backref},'g') END"
    namespace = "'mount:'||lower(substr(clean,1,2))"
    for anchor, volume in paths.volume_map.items():
        if '|' in str(volume):
            raise ValueError('Danh tính volume không hợp lệ')
        namespace = f'CASE WHEN lower(substr(clean,1,2))={quote(anchor)} THEN {quote(str(volume).lower())} ELSE {namespace} END'
    relative = 'lower(rel_path)'
    for root in paths.sensitive:
        prefix = root[2:].lstrip('\\')
        condition = f'(lower(clean)={quote(root.lower())} OR starts_with(lower(clean),{quote(root.lower().rstrip(chr(92)) + chr(92))}))'
        relative = f'CASE WHEN {condition} THEN {quote(prefix.lower())}||substr(rel_path,{len(prefix)+1}) ELSE {relative} END'
    native = f"regexp_matches(clean,{regular}) AND NOT contains(rel_path,{repeated}) AND NOT regexp_matches(rel_path,{dots}) AND NOT contains(rel_path,'|')"
    last = quote(r'\\[^\\]+$')
    parent = f"CASE WHEN contains(fast_relative,{slash}) THEN regexp_replace(fast_relative,{last},'') ELSE '' END"
    parent_display = f"CASE WHEN NOT contains(rel_path,{slash}) THEN substr(clean,1,2)||{slash} ELSE regexp_replace(rtrim(clean,{slash}),{last},'') END"
    cte = f'''cleaned AS (SELECT *,{clean} clean FROM raw),
        components AS (SELECT *,rtrim(ltrim(substr(clean,3),{slash}),{slash}) AS rel_path FROM cleaned),
        fast_paths AS (SELECT *,{namespace} namespace,{relative} fast_relative,({native}) is_native FROM components),
        keyed AS (SELECT *,CASE WHEN is_native THEN namespace||'|'||fast_relative ELSE capture_key(path) END captured_key,
            CASE WHEN is_native THEN CASE WHEN fast_relative='' THEN NULL ELSE namespace||'|'||({parent}) END ELSE capture_parent(path) END captured_parent,
            CASE WHEN is_native THEN {parent_display} ELSE capture_parent_display(path) END captured_parent_display FROM fast_paths)'''
    return cte


def _normalize(session, metadata, *, normalize=True, date_mode='epoch', timezone_name='UTC', include=None, stop=None):
    db = session.db
    paths = CapturedPaths(metadata)
    session.progress(35, 'Kiểm tra byte và timestamp nguồn trước khi chuẩn hóa', stage='validate')
    db.create_function('capture_key', paths.key, ['VARCHAR'], 'VARCHAR')
    db.create_function('capture_parent', paths.parent, ['VARCHAR'], 'VARCHAR', null_handling='special')
    db.create_function('capture_parent_display', paths.display_parent, ['VARCHAR'], 'VARCHAR')
    # Validate before casting: DuckDB otherwise rounds fractional VARCHAR bytes.
    invalid = db.execute('''SELECT COUNT(*) FROM raw WHERE path IS NULL OR path='' OR kind NOT IN ('file','folder','link')
        OR (logical IS NOT NULL AND logical<>'' AND (NOT regexp_full_match(logical,'[0-9]+(\\.0+)?') OR TRY_CAST(logical AS BIGINT) IS NULL))
        OR (allocated IS NOT NULL AND allocated<>'' AND (NOT regexp_full_match(allocated,'[0-9]+(\\.0+)?') OR TRY_CAST(allocated AS BIGINT) IS NULL))
        OR (attrs IS NOT NULL AND attrs<>'' AND (NOT regexp_full_match(attrs,'[0-9]+(\\.0+)?') OR TRY_CAST(attrs AS BIGINT) IS NULL))''').fetchone()[0]
    if invalid:
        raise ValueError(f'{invalid:,} bản ghi sai path/kind/byte/attributes; chưa publish dữ liệu')
    if date_mode != 'epoch':
        for column in ('created', 'modified'):
            bad = db.execute(f"SELECT COUNT(*) FROM raw WHERE NULLIF(trim({column}),'') IS NOT NULL AND TRY_CAST({_date_text(column)} AS TIMESTAMP) IS NULL").fetchone()[0]
            if bad:
                raise ValueError(f'{bad:,} ngày {column} không đúng định dạng WizTree')
    else:
        for column in ('created', 'modified'):
            if db.execute(f"SELECT COUNT(*) FROM raw WHERE NULLIF({column},'') IS NOT NULL AND (TRY_CAST({column} AS DOUBLE) IS NULL OR NOT isfinite(TRY_CAST({column} AS DOUBLE)))").fetchone()[0]:
                raise ValueError('Timestamp epoch không hợp lệ')
    created = _date_expression('created', date_mode)
    modified = _date_expression('modified', date_mode)
    if date_mode != 'epoch' and timezone_name != 'UTC':
        zone = ZoneInfo(timezone_name)
        def local_date(value):
            if not value:
                return None
            from .scanner import normalize_export_date
            date = datetime.fromisoformat(normalize_export_date(value))
            if date.year <= 1900:
                return None
            if date.tzinfo is not None:
                return date.timestamp()
            first = date.replace(tzinfo=zone, fold=0)
            second = date.replace(tzinfo=zone, fold=1)
            if first.utcoffset() != second.utcoffset() or first.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) != date:
                raise ValueError('Ngày local mơ hồ do DST; xuất lại CSV theo UTC')
            return first.timestamp()
        db.create_function('capture_date', local_date, ['VARCHAR'], 'DOUBLE', null_handling='special')
        created, modified = 'capture_date(created)', 'capture_date(modified)'
    if normalize:
        from .config import home
        implicit = list(metadata['scope'].get('implicit_excludes') or [])
        if str(home()) not in implicit:
            implicit.append(str(home()))
        metadata['scope']['implicit_excludes'] = implicit
    condition = 'WHERE ' + _scope_filter(metadata, paths) if normalize else ''
    link = "(kind='link' OR (COALESCE(TRY_CAST(attrs AS BIGINT),0)&1024)<>0)" if normalize else "kind='link'"
    parent = "COALESCE(NULLIF(parent,''),captured_parent_display)" if normalize else 'parent'
    identity = "COALESCE(identity,'')"
    if normalize and metadata.get('identity_kind') == 'mft':
        identity = "CASE WHEN coalesce(identity,'')='' THEN '' ELSE 'wiz:'||split_part(captured_key,'|',1)||':'||identity END"
    session.progress(45, 'Lập khóa đường dẫn native trong bộ nhớ; không đọc file trên ổ', stage='normalize')
    db.execute(f'''CREATE TEMP TABLE staged AS WITH {_native_path_sql(paths)}
        SELECT row_number() OVER ()-1 AS row_id,
        path,{parent} parent,captured_key path_key,captured_parent parent_key,
        CASE WHEN {link} THEN 'link' ELSE kind END kind,
        CASE WHEN {link} AND {str(normalize).lower()} THEN 0 ELSE TRY_CAST(NULLIF(logical,'') AS BIGINT) END logical,
        CASE WHEN {link} AND {str(normalize).lower()} THEN 0 ELSE TRY_CAST(NULLIF(allocated,'') AS BIGINT) END allocated,
        {created} created,{modified} modified,{identity} AS "identity",
        TRY_CAST(NULLIF(attrs,'') AS BIGINT) attrs,COALESCE(NULLIF(status,''),'ok') status,COALESCE(note,'') note
        FROM keyed {condition}''')
    db.execute('DROP TABLE raw')
    logical = db.execute("SELECT COALESCE(SUM(logical),0),COUNT(*) FILTER(WHERE logical IS NULL) FROM staged WHERE kind='file'").fetchone()
    metadata['ingestion_check'] = {'logical_before_aggregation': logical[0], 'unknown_logical_files': logical[1],
                                 'source': 'captured-records; no live filesystem enrichment'}
    _check(stop)
    if normalize:
        session.progress(55, 'Đã nhập snapshot; khử trùng hardlink bằng metadata đã chụp', stage='hardlinks')
        db.execute('''CREATE TEMP TABLE links AS SELECT split_part(path_key,'|',1) volume,identity,
            FIRST(row_id ORDER BY path_key,row_id) anchor_row,MAX(allocated) allocation,COUNT(*) paths,
            MIN(logical) min_logical,MAX(logical) max_logical
            FROM staged WHERE kind='file' AND identity<>'' GROUP BY 1,2 HAVING COUNT(*)>1''')
        db.execute('''UPDATE staged AS s SET allocated=CASE WHEN l.min_logical<>l.max_logical THEN NULL
            WHEN s.row_id=l.anchor_row THEN l.allocation ELSE 0 END,
            status=CASE WHEN l.min_logical<>l.max_logical THEN 'changed-during-scan' ELSE s.status END
            FROM links l WHERE s.kind='file' AND split_part(s.path_key,'|',1)=l.volume AND s.identity=l.identity''')
        # Ambiguous duplicate paths are retained, never collapsed into one row.
        metadata['hardlinks'] = dict(zip(('extra_paths', 'groups'), db.execute('SELECT COALESCE(SUM(paths-1),0),COUNT(*) FROM links').fetchone()))
        db.execute('DROP TABLE links')
        session.progress(65, 'Đang cộng file về thư mục một lượt; không cộng trùng cha/con', stage='folders')
        _rollup(session, stop)
    collisions = db.execute('SELECT COUNT(*) FROM (SELECT path_key FROM staged GROUP BY path_key HAVING COUNT(*)>1)').fetchone()[0]
    metadata['path_key_collisions'] = collisions
    if collisions:
        metadata.setdefault('warnings', []).append(f'{collisions:,} khóa đường dẫn không duy nhất; không tự ghép các hàng này')
    return paths


def rollup_values(folder_keys, direct, *, stop=None, width=None):
    """Linear nearest-ancestor rollup of nullable vectors, no recursive SQL join."""
    from .columnar_store import parent_key
    folders = set(folder_keys)
    width = width or len(next(iter(direct.values()), [0, 0]))
    totals = {key: [0] * width for key in folders}
    cache = {}
    def nearest(key):
        trail = []
        while key and key not in folders:
            if key in cache:
                key = cache[key]; break
            trail.append(key); key = parent_key(key)
        for item in trail:
            cache[item] = key
        return key
    def add(destination, value):
        for index, amount in enumerate(value):
            destination[index] = None if destination[index] is None or amount is None else destination[index] + amount
    for key, values in direct.items():
        found = nearest(key)
        if found is not None:
            add(totals[found], values)
    for index, key in enumerate(sorted(folders, key=len, reverse=True)):
        if index % 2000 == 0:
            _check(stop)
        parent = nearest(parent_key(key))
        if parent is not None:
            add(totals[parent], totals[key])
    return totals


def _rollup(session, stop):
    db = session.db
    direct = {key: [logical, allocated] for key, logical, allocated in db.execute('''SELECT parent_key,
        CASE WHEN COUNT(*) FILTER(WHERE logical IS NULL)>0 THEN NULL ELSE SUM(logical) END,
        CASE WHEN COUNT(*) FILTER(WHERE allocated IS NULL)>0 THEN NULL ELSE SUM(allocated) END
        FROM staged WHERE kind='file' GROUP BY parent_key''').fetchall()}
    folders = [row[0] for row in db.execute("SELECT path_key FROM staged WHERE kind='folder' GROUP BY path_key HAVING COUNT(*)=1").fetchall()]
    totals = rollup_values(folders, direct, stop=stop)
    db.execute('CREATE TEMP TABLE folder_totals(path_key VARCHAR,logical BIGINT,allocated BIGINT)')
    rows = []
    for key, values in totals.items():
        rows.append({'path_key': key, 'logical': values[0], 'allocated': values[1]})
        if len(rows) >= 10000:
            insert_records(db, 'folder_totals', 'STRUCT(path_key VARCHAR,logical BIGINT,allocated BIGINT)[]', rows)
            rows.clear(); _check(stop)
    if rows:
        insert_records(db, 'folder_totals', 'STRUCT(path_key VARCHAR,logical BIGINT,allocated BIGINT)[]', rows)
    db.execute("UPDATE staged s SET logical=f.logical,allocated=f.allocated FROM folder_totals f WHERE s.kind='folder' AND s.path_key=f.path_key")
    db.execute('DROP TABLE folder_totals')


def summarize(db, table='staged'):
    fields = ('rows', 'files', 'folders', 'links', 'logical_known', 'allocated_known', 'logical_unknown_files', 'allocated_unknown_files')
    values = db.execute(f'''SELECT COUNT(*),COUNT(*) FILTER(WHERE kind='file'),COUNT(*) FILTER(WHERE kind='folder'),
        COUNT(*) FILTER(WHERE kind='link'),COALESCE(SUM(logical) FILTER(WHERE kind='file'),0),
        COALESCE(SUM(allocated) FILTER(WHERE kind='file'),0),COUNT(*) FILTER(WHERE kind='file' AND logical IS NULL),
        COUNT(*) FILTER(WHERE kind='file' AND allocated IS NULL) FROM {table}''').fetchone()
    result = dict(zip(fields, values))
    result['logical'] = None if result['logical_unknown_files'] else result['logical_known']
    result['allocated'] = None if result['allocated_unknown_files'] else result['allocated_known']
    result['row_digest'] = db.execute(f"SELECT bit_xor(hash({','.join(name for name, _ in SCHEMA)})) FROM {table}").fetchone()[0]
    return result


def _publish(session, metadata, *, stop=None, started=None):
    _check(stop)
    session.progress(80, 'Ghi một snapshot Parquet nén; không dựng database lịch sử', stage='write')
    destination = session.work / 'ready.parquet'
    before = summarize(session.db)
    if metadata['ingestion_check']['logical_before_aggregation'] != before['logical_known']:
        raise ValueError('Tổng byte file đã thay đổi khi tổng hợp; không publish')
    metadata['ingestion_check']['logical_matches'] = True
    session.repo.check_space()
    session.db.execute(f"COPY staged TO {quote(destination)} (FORMAT PARQUET, COMPRESSION ZSTD, COMPRESSION_LEVEL 3, ROW_GROUP_SIZE 65536)")
    _check(stop)
    session.progress(90, 'Đọc lại số dòng, byte, null và digest trước khi publish', stage='verify')
    session.db.execute(f'CREATE TEMP VIEW verified AS SELECT * FROM read_parquet({quote(destination)})')
    after = summarize(session.db, 'verified')
    if before != after:
        raise ValueError('Snapshot ghi/đọc lại không khớp; nguồn được giữ nguyên')
    metadata['finished'] = utc_now()
    metadata['seconds'] = time.perf_counter() - started if started is not None else None
    metadata['status'] = 'complete' if not metadata.get('coverage', {}).get('unknown_roots') and not metadata.get('errors') else 'partial'
    _check(stop)
    result = session.repo.publish(destination, metadata, after)
    session.progress(100, f"Đã lưu mốc #{result['id']}: {after['files']:,} file • {after['folders']:,} thư mục", stage='done')
    return result


def import_entries(entries, metadata, *, repository=None, normalize=True, stop=None, notify=None):
    repository = repository or Repository()
    started = time.perf_counter()
    with MemorySession(repository, notify=notify) as session, session.operation(stop):
        session.progress(5, 'Thu thập metadata vào bộ nhớ; không tạo SQLite', stage='collect')
        session.db.execute('CREATE TEMP TABLE raw(' + RAW_SCHEMA + ')')
        batch = []
        count = 0
        for entry in entries:
            _check(stop)
            batch.append({key: None if entry.get(key) is None else str(entry[key]) for key in FIELDS})
            count += 1
            if len(batch) >= 5000:
                insert_records(session.db, 'raw', RAW_STRUCT, batch, ','.join('r.' + name for name in FIELDS))
                batch.clear()
                session.notify('info', {'message': f'Đã nhận {count:,} file/thư mục; không ghi hash/revisions từng mục'})
        if batch:
            insert_records(session.db, 'raw', RAW_STRUCT, batch, ','.join('r.' + name for name in FIELDS))
        session.progress(40, 'Chuẩn hóa khóa đường dẫn một lần', stage='normalize')
        _normalize(session, metadata, normalize=normalize, stop=stop)
        return _publish(session, metadata, stop=stop, started=started)


def csv_header(path):
    aliases = {'createddate': 'created', 'mftrecno': 'mft record number',
               'mftrecordnumber': 'mft record number', 'lastaccessdate': 'accessed', 'modifieddate': 'modified'}
    with Path(path).open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.reader(stream)
        for _ in range(40):
            start_line = reader.line_num
            row = next(reader, None)
            if row is None:
                break
            if row and row[0].strip().casefold() == 'file name':
                names = [aliases.get(name.strip().casefold(), name.strip().casefold()) for name in row]
                if len(names) != len(set(names)):
                    raise ValueError('CSV có tên cột trùng sau chuẩn hóa')
                if not {'file name', 'size', 'allocated', 'attributes'} <= set(names):
                    raise ValueError('CSV WizTree thiếu File Name/Size/Allocated/Attributes')
                return start_line, dict(zip(names, row))
    raise ValueError('Không tìm thấy header CSV WizTree')


def import_wiztree(path, metadata, *, timezone_name=None, repository=None, stop=None, notify=None):
    if timezone_name is None:
        raise ValueError('Cần chỉ rõ múi giờ CSV (UTC nếu xuất bằng /exportUTCTime=1)')
    ZoneInfo(timezone_name)
    repository = repository or Repository()
    inputs = [safe_path(value) for value in path] if isinstance(path, (list, tuple)) else [safe_path(path)]
    states = [(value, value.stat(), csv_header(value)) for value in inputs]
    total_bytes = sum(state.st_size for _, state, _ in states)
    if total_bytes > repository.limits['source_mb'] * 1024 * 1024:
        raise ValueError('CSV vượt giới hạn nguồn tạm; tăng ngân sách rõ ràng trước khi nhập')
    started = time.perf_counter()
    with MemorySession(repository, notify=notify) as session, session.operation(stop):
        session.progress(10, 'Đọc CSV WizTree bằng engine native; không mở lại file đã quét', stage='read_source')
        metadata['identity_kind'] = 'mft' if metadata.get('mft') is True else 'unverified-walk'
        session.db.execute('CREATE TEMP TABLE raw(' + RAW_SCHEMA + ')')
        metadata['overview'] = []
        for source, _, (skip, names) in states:
            def col(name, default='NULL'):
                return '"' + names[name].replace('"', '""') + '"' if name in names else default
            filename = col('file name')
            session.db.execute('CREATE TEMP TABLE exported AS SELECT * FROM read_csv(?,header=true,skip=?,all_varchar=true,null_padding=true,strict_mode=true)', [str(source), skip])
            session.db.execute(f'''INSERT INTO raw SELECT {filename} path,NULL::VARCHAR parent,
                CASE WHEN ends_with({filename},'\\') OR ends_with({filename},'/') THEN 'folder' ELSE 'file' END kind,
                {col('size')} logical,{col('allocated')} allocated,{col('created')} created,{col('modified')} modified,
                {col('mft record number')} AS "identity",{col('attributes')} attrs,
                'ok'::VARCHAR status,'WizTree snapshot; không đọc lại filesystem'::VARCHAR note FROM exported''')
            # WizTree uses a leading zero to mark a hardlink reference. Without
            # a captured ID it cannot be assigned physical bytes safely.
            session.db.execute("""UPDATE raw SET
                note=note||'; cấp phát nguồn='||coalesce(allocated,'?')||'; thiếu ID, tổng Đĩa chưa xác minh',
                allocated=NULL,status='metadata-partial' WHERE kind='file' AND coalesce(identity,'')=''""")
            if metadata['identity_kind'] != 'mft':
                session.db.execute("""UPDATE raw SET identity='',
                    note=note||'; số cấp phát nguồn='||coalesce(allocated,'?')||'; ID walk không phải MFT, tổng Đĩa chưa xác minh',
                    allocated=CASE WHEN kind='file' THEN NULL ELSE allocated END,
                    status=CASE WHEN kind='file' THEN 'metadata-partial' ELSE status END""")
            capacities = ','.join(f'TRY_CAST({col(name)} AS BIGINT)' for name in ('drivecapacity', 'freespace', 'usedspace'))
            roots = [CapturedPaths.canonical(root).replace('\\', '/').lower().rstrip('/') for root in metadata['scope']['roots']]
            root_rows = session.db.execute(f'''SELECT {filename},TRY_CAST({col('size')} AS BIGINT),TRY_CAST({col('allocated')} AS BIGINT),{capacities}
                FROM exported WHERE rtrim(replace(lower({filename}),'\\','/'),'/') IN (SELECT unnest(?))''', [roots]).fetchall()
            metadata['overview'].extend(dict(zip(('root', 'logical', 'allocated', 'capacity', 'free', 'used'), row)) for row in root_rows)
            session.db.execute('DROP TABLE exported')
        if metadata['identity_kind'] != 'mft':
            metadata.setdefault('warnings',[]).append('CSV không xác nhận MFT admin: cột MFT có thể là số thứ tự giả. Không dùng để khử hardlink; cấp phát file để chưa rõ, giữ số nguồn ở note.')
        session.progress(35, 'Đã đọc CSV; lọc phạm vi và chuẩn hóa một lần', stage='validate')
        raw = session.db.execute("SELECT COUNT(*) FILTER(WHERE kind='file'),COALESCE(SUM(TRY_CAST(logical AS BIGINT)) FILTER(WHERE kind='file'),0) FROM raw").fetchone()
        metadata['source_audit'] = {'files': raw[0], 'logical_files': raw[1], 'source_bytes': total_bytes, 'native_enrichment': False, 'timezone': timezone_name}
        _normalize(session, metadata, date_mode='wiztree', timezone_name=timezone_name, stop=stop)
        for source, before, _ in states:
            current = source.stat()
            if (current.st_size, current.st_mtime_ns, current.st_ino) != (before.st_size, before.st_mtime_ns, before.st_ino):
                raise ValueError('CSV thay đổi trong khi nhập; không publish')
        return _publish(session, metadata, stop=stop, started=started)


def import_legacy(source_home, identifiers, *, repository=None, stop=None, notify=None):
    """Copy selected logs only. Never retire originals or instantiate legacy indexes."""
    repository = repository or Repository()
    source = safe_path(Path(source_home) / 'data/history')
    catalog = read_json(source / 'catalog.json')
    if not catalog or catalog.get('schema') != 1:
        raise ValueError('Nguồn chưa là lịch sử JSONL có catalog schema 1')
    wanted = {int(value) for value in identifiers}
    scans = [row for row in catalog.get('tables', {}).get('scans', []) if row['id'] in wanted]
    if len(scans) != len(wanted) or not wanted:
        raise ValueError('Chọn đúng ID các mốc cần sao chép; không tự nhập toàn bộ')
    results = []
    for scan in sorted(scans, key=lambda row: (row.get('started') or '', row['id'])):
        _check(stop)
        if scan.get('status') not in ('complete', 'partial'):
            raise ValueError('Chỉ sao chép mốc đã hoàn tất/có giới hạn')
        started = time.perf_counter()
        path = safe_path(source / 'scans' / f"scan-{scan['id']:08d}.jsonl.gz", beneath=source)
        stat = path.stat()
        proof = catalog.get('logs', {}).get(str(scan['id']))
        if not proof:
            raise ValueError('Mốc không có biên bản checksum trong catalog')
        if notify:
            notify('info', {'message': f"Kiểm checksum và sao chép mốc cũ #{scan['id']}; không xóa nguồn"})
        header, verified = verify_jsonl(path, stop=stop, expected=proof)
        cfg = json.loads(scan['config'])
        old_summary = json.loads(scan.get('summary') or '{}')
        scope = old_summary.get('effective_scope') or {'roots': cfg['roots'], 'excludes': cfg.get('excludes', []),
            'strict_browsers': cfg.get('strict_browsers', False), 'implicit_excludes': None, 'volumes': None}
        metadata = metadata_for(cfg['roots'], engine=scan['engine'], profile=scan['profile'], scope=scope,
            legacy_source=str(Path(source_home).absolute()),
            started=scan['started'], source_id=scan['id'], legacy_summary=old_summary,
            warnings=list(old_summary.get('data_warnings') or []),
            mft=old_summary.get('scan_method', {}).get('mft', cfg.get('mft')),
            coverage=old_summary.get('coverage') or {'unknown_roots': cfg['roots'], 'source': 'legacy'},
            source_provenance=old_summary.get('provenance'))
        normalization = catalog.get('normalization') or {}
        use_original = any(row.get('scan_id') == scan['id'] and row.get('use_original') for row in normalization.get('preferences', []))
        values = [scan[key] for key in ('id', 'started', 'finished', 'status', 'config', 'summary')]
        fingerprint = hashlib.sha256(json.dumps(values, ensure_ascii=False).encode()).hexdigest()
        overlay = next((row for row in normalization.get('datasets', []) if row.get('scan_id') == scan['id'] and row.get('active') and row.get('fingerprint') == fingerprint), None) if not use_original else None
        overlay_proof = None
        overlay_path = None
        if overlay:
            if overlay.get('version') != 2:
                raise ValueError('Lớp chuẩn hóa cũ chưa hỗ trợ; không bỏ qua âm thầm')
            overlay_path = safe_path(source / 'normalization' / f"dataset-{overlay['id']:08d}.jsonl.gz", beneath=source)
            _, overlay_proof = verify_jsonl(overlay_path, stop=stop, columns=('path', *OVERLAY_FIELDS))
            metadata['legacy_normalization'] = json.loads(overlay['report'])
        metadata['source_fingerprint'] = hashlib.sha256(json.dumps([str(source), scan['id'], verified, overlay_proof,PATH_KEY_VERSION], sort_keys=True).encode()).hexdigest()
        existing = next((row for row in repository.list() if row.get('source_fingerprint') == metadata['source_fingerprint']), None)
        if existing:
            repository.verify(existing)
            results.append({**existing, 'already_imported': True}); continue
        with MemorySession(repository, notify=notify) as session, session.operation(stop):
            session.progress(20, f"Đọc mốc #{scan['id']} bằng JSON reader native; không dựng SQLite", stage='read_source')
            _json_arrays(session.db, path)
            if overlay_path:
                _json_arrays(session.db, overlay_path, table='overlay', fields=('path', *OVERLAY_FIELDS))
                if session.db.execute('SELECT COUNT(*) FROM (SELECT path FROM overlay GROUP BY path HAVING COUNT(*)>1)').fetchone()[0]:
                    raise ValueError('Lớp chuẩn hóa có đường dẫn trùng; không tự áp dụng')
                session.db.execute('UPDATE raw AS r SET ' + ','.join(f'{key}=o.{key}' for key in OVERLAY_FIELDS) + ' FROM overlay o WHERE r.path=o.path')
            # Preserve the eleven captured fields. Do not "fix" old numbers by
            # silently running a new hardlink/folder normalization during copy.
            _normalize(session, metadata, normalize=False, stop=stop)
            errors = session.db.execute("SELECT json_extract_string(json,'$.error[0]'),json_extract_string(json,'$.error[1]') FROM read_json_objects(?,format='newline_delimited') WHERE json_type(json)='OBJECT' AND json_exists(json,'$.error') LIMIT 1001", [str(path)]).fetchall()
            metadata['errors'] = [{'path': row[0], 'message': row[1]} for row in errors[:1000]]
            if len(errors) > 1000:
                metadata['warnings'].append('Có hơn 1000 lỗi nguồn; giữ cảnh báo bao phủ toàn phạm vi')
                metadata['coverage']['unknown_roots'] = cfg['roots']
            current = path.stat()
            if (stat.st_size, stat.st_mtime_ns, stat.st_ino) != (current.st_size, current.st_mtime_ns, current.st_ino):
                raise ValueError('Log nguồn thay đổi; chưa publish')
            results.append(_publish(session, metadata, stop=stop, started=started))
    return results
