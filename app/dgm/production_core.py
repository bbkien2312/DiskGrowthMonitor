"""Production entry points for the tested Parquet/RAM core.

The preview store can be adopted in place: no duplicate snapshots, no ID reset,
and no automatic deletion of legacy history. Non-scan feature metadata keeps its
existing compatibility store; file records never go through its SQLite index.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path
import threading
import time

from .columnar_store import Repository, read_json, safe_path, atomic_json, utc_now
from .columnar_jobs import Jobs
from .columnar_compare import Comparison


def enabled():
    from .config import load
    return load().get('scan_backend', 'columnar') != 'legacy'


def repository():
    from .config import home, load
    root = home()
    target = root / 'data/columnar'
    # The D: preview was explicitly approved. Reuse its immutable files rather
    # than copying 100+ MB or changing existing scan identifiers.
    approved = root / 'data/parquet-preview/data/columnar-preview'
    if not (target / 'catalog.json').exists() and (approved / 'catalog.json').exists():
        target = approved
    cfg=load()
    limits={'reserve_mb':cfg.get('storage_reserve_mb',1024),'source_mb':cfg.get('resume_limit_mb',512),
            **(cfg.get('columnar_limits') or {})}
    repo = Repository(target, limits=limits)
    repo.production = True
    return repo


def previous(ident, repo=None):
    repo = repo or repository()
    current = repo.get(ident)
    rows = [row for row in repo.list() if row['id'] != int(ident)
            and row['profile'] == current['profile'] and row.get('status') in ('complete', 'partial')
            and (row['started'], row['id']) < (current['started'], current['id'])]
    return max(rows, key=lambda row: (row['started'], row['id']))['id'] if rows else None


def history(profile=None):
    from .columnar_ui import scan_label
    return [{**row, 'label': scan_label(row)} for row in repository().list()
            if profile is None or row['profile'] == profile]


def scan(name, profile, stop=None, notify=None, *, allow_uac=True):
    from .config import load, normalize_profile
    from .platform_ops import is_admin, ProcessCancelled
    profile = normalize_profile(profile, load().get('threshold_mb',10))
    if profile.get('engine', 'wiztree') == 'wiztree' and profile.get('mft', True) and not is_admin():
        if not allow_uac:
            raise PermissionError('MFT chạy lịch cần quyền Admin; không mở UAC trong worker nền')
        from .elevation import request
        return request('columnar_scan', {'name': name, 'profile': profile}, stop=stop, notify=notify)
    jobs = Jobs(repository())
    started=time.perf_counter()
    job = jobs.create('scan', {'name': name, 'profile': dict(profile), 'engines': load()['engines']})
    try:
        row = jobs.execute(job['id'], stop=stop, notify=notify)[0]
    except ProcessCancelled:
        return {'status': 'cancelled', 'job_id': job['id'], 'scan_id': None, 'backend': 'parquet'}
    return {**row['summary'], 'scan_id': row['id'], 'status': row['status'], 'job_id': job['id'],
            'seconds': time.perf_counter()-started, 'backend': 'parquet',
            'timing':jobs.get(job['id']).get('timing'),
            'completed_steps': ['Thu thập metadata', 'Chuẩn hóa một lần', 'Lưu snapshot Parquet']}


def query(newer, older=None, *, baseline=False, stop=None, notify=None):
    repo = repository()
    old = None if baseline else older if older is not None else previous(newer, repo)
    result = Comparison(newer, old, repository=repo, stop=stop, notify=notify)
    try:remember_comparison(repo,result)
    except BaseException:result.close();raise
    return result


def remember_comparison(repo, query):
    summary=query.summary()
    # Summaries only. Never persist a materialized join or per-file diff cache.
    record={'newer':query.new['id'],'older':query.old['id'] if query.old else None,
        'created':utc_now(),'rows':summary['rows'],'counts':summary['counts'],
        'common_old':summary['common_old'],'common_new':summary['common_new'],
        'private_old':summary['private_old'],'private_new':summary['private_new'],
        'seconds':summary['seconds'],'backend':'parquet','overview':summary}
    with repo.locked():
        path=repo.root/'comparisons.json'
        rows=read_json(path,[])
        existing=next((r for r in rows if (r['older'],r['newer'])==(record['older'],record['newer'])),None)
        record['id']=existing.get('id',1) if existing else max((r.get('id',0) for r in rows),default=0)+1
        rows=[r for r in rows if (r['older'],r['newer'])!=(record['older'],record['newer'])]
        atomic_json(path,([record]+rows)[:500])


def comparison_history():
    repo=repository();ids={row['id'] for row in repo.list()}
    return [row for row in read_json(repo.root/'comparisons.json',[])
            if row['newer'] in ids and (row['older'] is None or row['older'] in ids)]


def delete_comparison(ident):
    repo=repository()
    with repo.locked():
        path=repo.root/'comparisons.json';rows=read_json(path,[])
        if not any(r.get('id')==ident for r in rows):raise ValueError('Không có tóm tắt so sánh này')
        atomic_json(path,[r for r in rows if r.get('id')!=ident])
    return {'deleted_comparison':ident,'snapshots_preserved':True}


def page(query, *, offset=0, cursor=None, **options):
    from .comparison_policy import choose_measure
    requested = options.get('measure', 'auto')
    policy = choose_measure(query.new, query.old, requested)
    options['measure'] = policy['measurement']
    if offset < 0 or (offset and cursor):
        raise ValueError('Offset không âm; không dùng đồng thời offset và cursor')
    if offset:
        normalized = dict(sort='abs_delta', direction='desc', root=None, recursive=True,
            section='all', changes='all', kind='all', minimum=0, search='', presence='all',
            folders_only=False, root_keys=None, measure='allocated')
        normalized.update({key: value for key, value in options.items() if key != 'limit'})
        cursor = base64.urlsafe_b64encode(json.dumps({'offset': offset,
            'signature': query._fingerprint(normalized)}).encode()).decode()
    return dict(query.page(cursor=cursor, **options), **policy)


def legacy_plan(source_home=None, repo=None):
    """Catalog only: no decompression, snapshot index, or checksum on startup."""
    from .config import home
    source_home = Path(source_home or home()).absolute()
    catalog_path = safe_path(source_home / 'data/history/catalog.json')
    catalog = read_json(catalog_path, {})
    rows = [row for row in catalog.get('tables', {}).get('scans', [])
            if row.get('status') in ('complete', 'partial')]
    repo = repo or repository()
    copied = set()
    for row in repo.list():
        source = row.get('legacy_source')
        if row.get('source_id') is not None and (source == str(source_home) or
            (source is None and repo.root == source_home / 'data/parquet-preview/data/columnar-preview')):
            copied.add(row['source_id'])
    pending = [row for row in rows if row['id'] not in copied]
    return {'source_home': str(source_home), 'pending_ids': [row['id'] for row in pending],
            'already_copied': sorted(copied), 'available': len(rows), 'sources_preserved': True,
            'sqlite_present': (source_home / 'data/history.sqlite').exists()}


def migrate_legacy(ids=None, *, source_home=None, stop=None, notify=None):
    jobs = Jobs(repository())
    plan = legacy_plan(source_home, jobs.repo)
    identifiers = plan['pending_ids'] if ids is None else sorted(set(ids))
    if not identifiers:
        return []
    job = jobs.create('legacy', {'source_home': plan['source_home'], 'ids': identifiers})
    return jobs.execute(job['id'], stop=stop, notify=notify)


def export(ident, destination):
    from .columnar_store import MemorySession, quote
    destination = Path(destination).absolute()
    repo = repository()
    if destination == repo.root or destination.is_relative_to(repo.root):
        raise ValueError('Không xuất đè vào kho lịch sử')
    if destination.exists():
        raise ValueError('File đích đã tồn tại; chọn tên khác, không tự ghi đè')
    with MemorySession(repo) as session:
        row = repo.get(ident)
        session.pin([row])
        source = repo.verify(row)
        session.db.execute(f'COPY (SELECT * FROM read_parquet({quote(source)})) TO {quote(destination)} (HEADER, DELIMITER ",")')
    return {'scan_id': ident, 'destination': str(destination), 'backend': 'parquet'}
