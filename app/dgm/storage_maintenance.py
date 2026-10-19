"""Preview-first maintenance, strictly limited to this app's own disposable data."""
from __future__ import annotations
import json
import logging
import os
from pathlib import Path
import re
import shutil
import sqlite3
import uuid
from contextlib import contextmanager,ExitStack
from .config import home,load

BACKUP_NAMES={'history.sqlite','comparisons.sqlite','normalization.sqlite','config.json','ui-settings.json'}
SESSION=re.compile(r'session-[0-9a-f]{32}')
DOWNLOAD=re.compile(r'\d+\.\d+\.\d+-[0-9a-f]{12}')


@contextmanager
def maintenance_lock():
    from .elevation import alive,owner
    path=safe(home()/'data/storage-maintenance.lock');token=uuid.uuid4().hex
    if path.exists():
        previous=json.loads(path.read_text(encoding='utf-8'))
        if alive(previous.get('owner',{})):raise RuntimeError('Đang bảo trì dung lượng/cập nhật; chờ hoàn tất')
        path.unlink()
    with path.open('x',encoding='utf-8') as stream:json.dump({'owner':owner(),'token':token},stream)
    try:yield
    finally:
        try:
            if json.loads(path.read_text(encoding='utf-8')).get('token')==token:path.unlink()
        except FileNotFoundError:pass


@contextmanager
def pinned_parent(path):
    """Deny Windows ancestor rename/junction replacement during privileged unlink."""
    safe(path)
    path.relative_to(home()/'data/updates')
    with ExitStack() as stack:
        if os.name=='nt':
            import win32file,win32con
            folders=[home(),*reversed([p for p in path.parents if p!=home() and p.is_relative_to(home())])]
            for folder in folders:
                handle=win32file.CreateFile(str(folder),win32con.GENERIC_READ,
                    win32con.FILE_SHARE_READ|win32con.FILE_SHARE_WRITE,None,win32con.OPEN_EXISTING,
                    win32file.FILE_FLAG_BACKUP_SEMANTICS|win32file.FILE_FLAG_OPEN_REPARSE_POINT,None)
                stack.callback(handle.Close)
                if win32file.GetFileInformationByHandle(handle)[0]&0x400:raise ValueError('Reparse point thay đổi khi dọn')
        yield


def safe(path):
    path=Path(path).absolute()
    if path.resolve()!=path:raise ValueError('Không dọn qua junction/symlink: '+str(path))
    # resolve() also detects junction ancestors, but keep an explicit attribute
    # check for Windows reparse types that cannot be resolved normally.
    if path.exists() and getattr(path.lstat(),'st_file_attributes',0)&0x400:
        raise ValueError('Không dọn reparse point: '+str(path))
    return path


def files_in(path):
    if not path.exists():return
    stack=[safe(path)]
    while stack:
        folder=stack.pop()
        with os.scandir(folder) as entries:
            for entry in entries:
                item=safe(Path(entry.path))
                if entry.is_dir(follow_symlinks=False):stack.append(item)
                elif entry.is_file(follow_symlinks=False):yield item


def _session(path):
    safe(path)
    data=json.loads(path.read_text(encoding='utf-8-sig'))
    if (data.get('id')!=path.parent.name or Path(data.get('root','')).absolute()!=home() or
            not SESSION.fullmatch(path.parent.name)):raise ValueError('Session khác bản app')
    return data


def update_plan(*, keep=None):
    """Only known files of successfully completed sessions can become candidates."""
    from .app_update import active_update
    keep=int(load().get('update_backup_keep',1)) if keep is None else int(keep)
    if not 0<=keep<=20:raise ValueError('Số backup cần giữ phải từ 0 đến 20')
    base=safe(home()/'data/updates');sessions=[];protected=[]
    if active_update():raise RuntimeError('Đang cập nhật; chưa dọn backup/installer')
    for path in sorted(base.glob('session-*/session.json')):
        try:
            data=_session(path)
            if data.get('phase')!='complete' or data.get('restore_pending') is not False or data.get('errors'):
                protected.append(str(path.parent));continue
            backup=path.parent/'backup';items=list(files_in(backup))
            if any(p.parent!=backup or p.name not in BACKUP_NAMES for p in items):
                protected.append(str(backup));continue
            sessions.append((path,data,items))
        except (OSError,ValueError,TypeError):protected.append(str(path.parent))
    sessions.sort(key=lambda item:item[0].stat().st_mtime_ns,reverse=True)
    # A previous cleanup may leave a successful session with no backup. It must
    # not consume the keep slot and evict the only remaining recoverable backup.
    retained=set([p.parent for p,d,items in sessions if items][:keep])
    candidates={}
    for path,data,items in sessions:
        if path.parent not in retained:
            for item in items:candidates[item]='backup cập nhật cũ'
        installer=Path(data.get('installer','')).absolute()
        if installer.parent.parent==base and DOWNLOAD.fullmatch(installer.parent.name) and installer.name=='installer.exe':
            try:
                safe(installer)
                if installer.is_file():candidates[installer]='installer đã cập nhật xong'
            except (OSError,ValueError):protected.append(str(installer))
    # Never remove an installer referenced by a failed, waiting, recovery or
    # unrecognized session, even when a completed session shares that download.
    for path in base.glob('session-*/session.json'):
        try:
            data=_session(path)
            if data.get('phase')!='complete' or data.get('restore_pending') is not False or data.get('errors'):
                candidates.pop(Path(data.get('installer','')).absolute(),None)
        except (OSError,ValueError,TypeError):
            candidates={p:r for p,r in candidates.items() if p.name!='installer.exe'}
    rows=[]
    for path,reason in candidates.items():
        stat=safe(path).stat()
        rows.append({'path':str(path),'bytes':stat.st_size,'modified_ns':stat.st_mtime_ns,'reason':reason})
    return {'keep_backups':keep,'files':rows,'reclaimable_bytes':sum(r['bytes'] for r in rows),
        'retained_backups':[str(p/'backup') for p in sorted(retained)],'protected':protected,
        'history_preserved':True}


def cleanup_updates(*, keep=None, preview=None):
    with maintenance_lock():return _cleanup_updates(keep=keep,preview=preview)


def _cleanup_updates(*, keep=None, preview=None):
    """Revalidate immediately before each unlink; retain journals and failed backups."""
    from .app_update import active_update
    current=update_plan(keep=keep)
    approved={r['path']:(r['bytes'],r['modified_ns']) for r in (preview or current)['files']}
    removed=[];errors=[];parents=set()
    for row in current['files']:
        if approved.get(row['path'])!=(row['bytes'],row['modified_ns']):continue
        if active_update():raise RuntimeError('Cập nhật vừa bắt đầu; dừng dọn cache')
        path=safe(Path(row['path']))
        # Recheck session state/retention too, not only file metadata.
        valid={r['path']:(r['bytes'],r['modified_ns']) for r in update_plan(keep=keep)['files']}
        if valid.get(str(path))!=approved[str(path)]:continue
        try:
            with pinned_parent(path):
                stat=path.stat()
                if (stat.st_size,stat.st_mtime_ns)!=approved[str(path)]:continue
                path.unlink();removed.append(row);parents.add(path.parent)
        except OSError as exc:errors.append({'path':str(path),'error':str(exc)})
    for path in parents:
        if path.name=='backup':
            try:path.rmdir()
            except OSError:pass
    result={'removed_bytes':sum(r['bytes'] for r in removed),'removed_files':len(removed),'errors':errors,'history_preserved':True}
    logging.getLogger('dgm').info('STORAGE cleanup updates bytes=%s files=%s errors=%s',result['removed_bytes'],len(removed),len(errors))
    return result


def storage_report():
    root=home();groups={};warnings=[];databases={}
    for folder in (root/'data',root/'logs'):
        try:
            for path in files_in(folder):
                rel=path.relative_to(root);parts=rel.parts
                if parts[0]=='logs':group='Log'
                elif path.suffix=='.parquet':group='Snapshot Parquet nén'
                elif 'columnar' in parts or 'columnar-preview' in parts:
                    group='Phiên so sánh RAM / tạm Parquet' if 'work' in parts else 'Journal / export Parquet' if 'jobs' in parts else 'Catalog / tóm tắt Parquet'
                elif parts[1]=='history':group='Lịch sử JSONL nén / metadata'
                elif parts[1]=='cleanup':group='Metadata đề xuất dọn nén'
                elif parts[1]=='tasks':group='Điểm tiếp tục / tệp trung gian tác vụ'
                elif parts[1]=='tmp' and len(parts)>2 and parts[2]=='cleanup-index':group='Chỉ mục đề xuất dọn tạm'
                elif parts[1]=='tmp' and len(parts)>2 and parts[2]=='history-index':group='Chỉ mục SQLite tạm'
                elif len(parts)>2 and parts[1]=='updates':
                    group='Backup cập nhật' if 'backup' in parts[2:] else 'Installer tải sẵn' if path.name=='installer.exe' else 'Nhật ký cập nhật'
                elif parts[1].startswith('history.sqlite'):
                    from .history_backend import log_mode
                    group='SQLite cũ chưa thu hồi' if log_mode() else 'Lịch sử quét'
                elif parts[1].startswith('comparisons.sqlite'):group='Cache so sánh'
                elif parts[1].startswith('normalization.sqlite'):group='Chuẩn hóa'
                elif parts[1]=='tmp':group='File tạm'
                elif parts[1]=='exports':group='Nguồn CSV / xuất dữ liệu'
                else:group='Cấu hình / dữ liệu khác'
                groups[group]=groups.get(group,0)+path.stat().st_size
        except (OSError,ValueError) as exc:warnings.append(str(exc))
    for name in ('history.sqlite','comparisons.sqlite','normalization.sqlite'):
        from .history_backend import database_path
        path=database_path(name,create=False)
        if not path.exists():continue
        try:
            db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=1)
            try:
                stats={key:db.execute('PRAGMA '+key).fetchone()[0] for key in ('page_size','page_count','freelist_count','auto_vacuum')}
                stats['reusable_bytes']=stats['page_size']*stats['freelist_count'];databases[name]=stats
            finally:db.close()
        except sqlite3.Error as exc:warnings.append(name+': '+str(exc))
    try:plan=update_plan()
    except (OSError,ValueError,RuntimeError) as exc:plan=None;warnings.append(str(exc))
    return {'home':str(root),'free_bytes':shutil.disk_usage(root).free,'total_data_log_bytes':sum(groups.values()),
        'groups':groups,'databases':databases,'update_cleanup':plan,'warnings':warnings,
        'reserve_mb':load().get('storage_reserve_mb',1024),
        'note':'Chỉ đo dữ liệu/log riêng của app, không phải dung lượng các file đã quét. Không tự xóa lịch sử.'}


def compact_comparison_cache(stop=None):
    with maintenance_lock():return _compact_comparison_cache(stop)


def _compact_comparison_cache(stop=None):
    """Explicit full compaction, with ample scratch space; preserve all summaries."""
    from .app_update import active_update
    from .scanner import ScanLock
    from .comparison import _locked
    from .storage_safety import SpaceGuard,SpaceConnection
    import threading
    stop=stop or threading.Event()
    if active_update():raise RuntimeError('Đang cập nhật; không thu gọn cache')
    from .history_backend import database_path
    path=safe(database_path('comparisons.sqlite',create=False))
    if not path.exists():return {'before_bytes':0,'after_bytes':0,'reclaimed_bytes':0}
    before=path.stat().st_size
    guard=SpaceGuard(before*2,operation='thu gọn cache SQLite')
    with ScanLock(stop),_locked(stop):
        db=sqlite3.connect(path,timeout=5,factory=SpaceConnection);db.space_guard=guard
        try:
            db.set_progress_handler(lambda:int(stop.is_set()) or guard.progress(),4000)
            db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchall()
            db.execute('PRAGMA auto_vacuum=INCREMENTAL')
            db.execute('VACUUM main')
            db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchall()
        finally:db.close()
    after=path.stat().st_size
    return {'before_bytes':before,'after_bytes':after,'reclaimed_bytes':max(0,before-after),'history_preserved':True}
