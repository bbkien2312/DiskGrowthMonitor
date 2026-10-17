"""Verified streaming SQLite -> compressed metadata text conversion."""
from __future__ import annotations
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import uuid
from .config import home,load,save
from . import history_logs as logs
from .history_index import TABLES,rows,write_json,mutex
from .storage_maintenance import safe,maintenance_lock
from .platform_ops import ProcessCancelled


def discard_staging(folder):
    if not folder.exists():return
    from .storage_maintenance import files_in
    if folder.parent!=home()/'data' or not folder.name.startswith('history-stage-'):raise ValueError('Sai thư mục chuyển đổi tạm')
    try:files=list(files_in(folder))
    except (OSError,ValueError):return
    if any(not (p.name.endswith('.jsonl.gz') or p.name in ('catalog.json','migration.json')) for p in files):return
    for path in files:path.unlink()
    for name in ('scans','normalization'):
        path=folder/name
        if path.exists():path.rmdir()
    folder.rmdir()


def file_hash(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def preview():
    source=home()/'data/history.sqlite'
    if not source.exists():return {'needed':False,'legacy_bytes':0,'scans':0}
    db=sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)
    try:count=db.execute('SELECT COUNT(*) FROM scans').fetchone()[0]
    finally:db.close()
    names=('history.sqlite','comparisons.sqlite','normalization.sqlite')
    return {'needed':not (home()/'data/history/catalog.json').exists(),'scans':count,
        'legacy_bytes':sum(p.stat().st_size for p in (home()/'data'/n for n in names) if p.exists()),
        'note':'Chỉ chuyển metadata, không đọc nội dung file đã quét. Xác minh checksum/số bản ghi trước khi bỏ SQLite cũ.'}


def migrate(*,remove_old=False,stop=None,notify=None):
    from .scanner import ScanLock
    stop=stop or threading.Event();notify=notify or (lambda *args:None)
    from .progress import TaskProgress
    task=TaskProgress('history-migrate',notify,[('prepare','Kiểm tra nguồn SQLite',5),('logs','Ghi và kiểm chứng log các mốc',80),
        ('normal','Giữ lớp chuẩn hóa và tóm tắt',92),('activate','Kích hoạt lịch sử JSONL',96),('retire','Thu hồi dữ liệu cũ đã duyệt',100)])
    task.stage('prepare')
    root=home();data=root/'data';destination=safe(data/'history')
    if destination.exists():raise ValueError('Đã có thư mục lịch sử log; không ghi đè hoặc chuyển lại')
    source=safe(data/'history.sqlite')
    if not source.exists():raise ValueError('Không có lịch sử SQLite cũ để chuyển')
    with maintenance_lock(),ScanLock(stop),mutex(root),ExitStack() as stack:
        ident=uuid.uuid4().hex;staging=safe(data/('history-stage-'+ident));staging.mkdir()
        stack.callback(discard_staging,staging)
        connections={};versions={};originals={}
        for name in ('history.sqlite','comparisons.sqlite','normalization.sqlite'):
            path=safe(data/name)
            if not path.exists():continue
            wal=path.with_name(name+'-wal')
            if wal.exists() and wal.stat().st_size:
                checkpoint=sqlite3.connect(path,timeout=2)
                try:
                    if checkpoint.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0]:
                        raise RuntimeError('SQLite cũ đang được sử dụng; đóng các tác vụ khác trước khi chuyển')
                finally:checkpoint.close()
            notify('info',{'message':'Đang kiểm tra dữ liệu nguồn '+name})
            originals[name]={'bytes':path.stat().st_size,'sha256':file_hash(path)}
            db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=5);db.row_factory=sqlite3.Row
            stack.callback(db.close);connections[name]=db
            versions[name]=db.execute('PRAGMA data_version').fetchone()[0];db.execute('BEGIN')
        original=connections['history.sqlite']
        catalog={'schema':1,'tables':{table:rows(original,table) for table in TABLES},'logs':{}}
        scans=catalog['tables']['scans'];numbers={r['scan_id']:r['ordinal'] for r in catalog['tables']['scan_numbers']}
        table='members' if original.execute("SELECT 1 FROM sqlite_master WHERE name='members' AND type='table'").fetchone() else 'entries'
        total=original.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]+original.execute('SELECT COUNT(*) FROM errors').fetchone()[0]
        completed=0;task.complete('Đã kiểm tra nguồn và bắt đầu ảnh đọc nhất quán');task.stage('logs')
        def forward(event,data):
            if event=='metadata-progress':task.advance(completed+data['records'],total,'Đang ghi metadata, sau đó đọc lại checksum')
            else:notify(event,data)
        counters={}
        for row in sorted(scans,key=lambda r:(r.get('started') or '',r['id'])):
            if stop.is_set():raise ProcessCancelled('Đã dừng chuyển đổi; SQLite gốc chưa bị xóa')
            sid=row['id'];profile=row['profile'];counters[profile]=counters.get(profile,0)+1
            ordinal=numbers.get(sid,counters[profile])
            numbers[sid]=ordinal
            fields=','.join('e.'+f for f in logs.FIELDS)
            count=original.execute('SELECT COUNT(*) FROM entries WHERE scan_id=?',(sid,)).fetchone()[0]
            errors=original.execute('SELECT COUNT(*) FROM errors WHERE scan_id=?',(sid,)).fetchone()[0]
            def records():
                for record in original.execute(f'SELECT {fields} FROM entries e WHERE scan_id=?',(sid,)):yield list(record)
                for record in original.execute('SELECT path,message FROM errors WHERE scan_id=?',(sid,)):yield {'error':list(record)}
            notify('info',{'message':f'Chuyển mốc #{sid}: {count:,} bản ghi vào JSONL nén; không quét lại ổ'})
            result=logs.write(staging/'scans'/f'scan-{sid:08d}.jsonl.gz',{'scan':row,'ordinal':ordinal,'columns':logs.FIELDS},records(),stop=stop,notify=forward)
            if result['records']!=count+errors:raise ValueError('Số bản ghi chuyển không khớp nguồn')
            catalog['logs'][str(sid)]=result
            completed+=result['records'];task.advance(completed,total,f'Đã ghi/đọc lại mốc #{sid}: {result["records"]:,} bản ghi')
        task.complete(f'Đã xác minh {len(scans)} mốc, {completed:,} bản ghi');task.stage('normal')
        if not catalog['tables']['scan_numbers']:
            catalog['tables']['scan_numbers']=[{'scan_id':r['id'],'profile':r['profile'],'ordinal':numbers[r['id']]} for r in scans]
        if not catalog['tables']['scan_id_counter']:catalog['tables']['scan_id_counter']=[{'id':1,'next_id':max([r['id'] for r in scans],default=0)+1}]
        if not catalog['tables']['sequence_counters']:
            catalog['tables']['sequence_counters']=[{'profile':p,'next_number':n+1} for p,n in counters.items()]
        if 'comparisons.sqlite' in connections:
            catalog['comparisons']=rows(connections['comparisons.sqlite'],'pairs')
        if 'normalization.sqlite' in connections:
            db=connections['normalization.sqlite'];from .normalization import FIELDS
            catalog['normalization']={'datasets':rows(db,'datasets'),'preferences':rows(db,'preferences')}
            db.execute('ATTACH DATABASE ? AS original',(source.as_uri()+'?mode=ro',))
            for dataset in catalog['normalization']['datasets']:
                did=dataset['id'];columns=','.join('o.'+f for f in FIELDS)
                records=(list(r) for r in db.execute(f'SELECT p.path,{columns} FROM overrides o JOIN original.paths p ON p.id=o.path_id WHERE dataset_id=?',(did,)))
                logs.write(staging/'normalization'/f'dataset-{did:08d}.jsonl.gz',{'dataset_id':did,'columns':['path',*FIELDS]},records,stop=stop,notify=notify)
        # Any legacy writer during export invalidates retirement. Even though
        # SQLite gave a consistent snapshot, a newer commit must not be lost.
        for name,db in connections.items():
            db.commit()
            if db.execute('PRAGMA data_version').fetchone()[0]!=versions[name]:
                raise RuntimeError('Lịch sử vừa thay đổi bởi tác vụ khác; chưa kích hoạt log hoặc xóa SQLite')
        task.complete('Đã giữ số thứ tự mốc, chuẩn hóa và tóm tắt so sánh/benchmark');task.stage('activate')
        write_json(staging/'catalog.json',catalog)
        manifest={'schema':1,'verified':True,'source':originals,'scans':len(scans),
            'records':sum(r['records'] for r in catalog['logs'].values()),'contains_file_contents':False}
        write_json(staging/'migration.json',manifest)
        staging.rename(destination)
        cfg=load();cfg.update(history_format='jsonl',source_archive_limit_mb=0);save(cfg)
        task.complete('Đã kích hoạt log metadata nén; không chứa nội dung file')
    result={'migrated':True,'verified':True,'scans':len(scans),'records':manifest['records'],
        'log_bytes':sum(p.stat().st_size for p in destination.rglob('*') if p.is_file()),'legacy_removed_bytes':0}
    if remove_old:
        task.stage('retire')
        try:
            result['legacy_cleanup']=retire_legacy();result['legacy_removed_bytes']=result['legacy_cleanup']['removed_bytes']
            if not result['legacy_cleanup'].get('pending'):
                from .storage_maintenance import cleanup_updates
                from .source_archive import clear
                result['completed_backup_cleanup']=cleanup_updates(keep=0)
                result['source_csv_cleanup']=clear()
        except (OSError,RuntimeError,ValueError) as exc:result['legacy_cleanup']={'removed_bytes':0,'pending':True,'error':str(exc)}
    task.finish(f'Đã chuyển {len(scans)} mốc sang JSONL; '+('nguồn cũ còn giữ, xem chi tiết' if result.get('legacy_cleanup',{}).get('pending') else 'checksum/số bản ghi khớp, lịch sử sẵn sàng'))
    return result


def retire_legacy():
    """Retire exact, unchanged originals only after revalidating every new log."""
    from .scanner import ScanLock
    root=home();folder=safe(root/'data/history')
    with maintenance_lock(),ScanLock(),mutex(root):
        catalog=json.loads((folder/'catalog.json').read_text(encoding='utf-8'))
        manifest=json.loads((folder/'migration.json').read_text(encoding='utf-8'))
        if manifest.get('verified') is not True:raise ValueError('Chưa có biên bản chuyển đổi đã xác minh')
        for row in catalog['tables']['scans']:
            verified=logs.verify(folder/'scans'/f"scan-{row['id']:08d}.jsonl.gz")
            expected=catalog['logs'][str(row['id'])]
            if verified['sha256']!=expected['sha256'] or verified['records']!=expected['records']:
                raise ValueError('Log không khớp biên bản; SQLite gốc được giữ')
        for dataset in catalog.get('normalization',{}).get('datasets',[]):
            logs.verify(folder/'normalization'/f"dataset-{dataset['id']:08d}.jsonl.gz")
        targets=[]
        for name,proof in manifest['source'].items():
            if name not in ('history.sqlite','comparisons.sqlite','normalization.sqlite'):raise ValueError('Tên file nguồn không hợp lệ')
            path=safe(root/'data'/name)
            if not path.exists():continue
            wal=path.with_name(name+'-wal')
            if wal.exists() and wal.stat().st_size:raise RuntimeError('SQLite cũ còn WAL; đóng bản cũ trước khi thu hồi')
            if path.stat().st_size!=proof['bytes'] or file_hash(path)!=proof['sha256']:
                raise RuntimeError('SQLite cũ đã thay đổi; không xóa tự động')
            targets.append(path)
        removed=0;retained=[]
        for path in targets:
            size=path.stat().st_size
            try:path.unlink();removed+=size
            except PermissionError:retained.append(str(path));continue
            for suffix in ('-wal','-shm'):
                sidecar=safe(path.with_name(path.name+suffix))
                try:sidecar.unlink(missing_ok=True)
                except PermissionError:pass
        return {'removed_bytes':removed,'logs_preserved':True,'pending':bool(retained),'retained':retained}
