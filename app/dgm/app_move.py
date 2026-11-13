"""Verified folder moves, using a durable journal and a retained source backup."""
from __future__ import annotations

from contextlib import contextmanager, ExitStack
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import uuid

from .app_move_paths import (check_stop, exists, has_streams, identify, inside, local_volume,
                             no_link, path_of, protected, tree)
from .columnar_store import atomic_json, read_json, utc_now
from .config import browser_path, home


RESERVE = 256 * 1024**2
ID = re.compile(r'[0-9a-f]{32}')


def _notify(notify, message):
    if notify:
        notify('info', {'message':message})


def _entry(source, parent, ident):
    return {'source':str(source), 'target':str(parent/source.name),
            'backup':str(source.with_name(f'.dgm-old-{ident}-{source.name}'))}


def _check_paths(entry, *, new=False):
    source = path_of(entry['source'])
    target = path_of(entry['target'])
    backup = path_of(entry['backup'])
    if protected(source) or protected(target) or protected(backup):
        raise ValueError('Thư mục thuộc vùng bảo vệ hoặc dùng chung.')
    if source.parent != backup.parent or not backup.name.startswith('.dgm-old-'):
        raise ValueError('Vị trí bản cũ không hợp lệ.')
    if local_volume(source.parent) == local_volume(target.parent):
        raise ValueError('Nguồn và đích phải ở hai phân vùng khác nhau.')
    no_link(source.parent)
    no_link(target.parent)
    if new:
        no_link(source)
        if not source.is_dir():
            raise ValueError('Nguồn phải là thư mục: ' + str(source))
        if exists(target) or exists(backup):
            raise ValueError('Đích hoặc bản cũ đã tồn tại; không ghi đè: ' + str(target))
    return source,target,backup


@contextmanager
def _pin_folders(*folders):
    """Keep existing ancestor directories from being swapped under an elevated operation."""
    if os.name != 'nt':
        yield
        return
    import win32con
    import win32file
    paths = dict.fromkeys(p for folder in folders for p in (*reversed(folder.parents),folder) if p.parent != p)
    with ExitStack() as stack:
        for path in paths:
            handle = win32file.CreateFile(str(path),0x80,  # FILE_READ_ATTRIBUTES
                win32con.FILE_SHARE_READ|win32con.FILE_SHARE_WRITE,None,win32con.OPEN_EXISTING,
                win32con.FILE_FLAG_BACKUP_SEMANTICS|0x00200000,None)
            stack.callback(handle.Close)
            if win32file.GetFileInformationByHandle(handle)[0] & 0x400:
                raise ValueError('Thư mục đã đổi thành liên kết: ' + str(path))
        yield


def _open_file(path, *, delete=False):
    """Read the named file without following a reparse point or allowing writes."""
    if os.name != 'nt':
        fd = os.open(path, os.O_RDONLY | getattr(os,'O_NOFOLLOW',0))
        return os.fdopen(fd,'rb')
    import msvcrt
    import win32con
    import win32file
    access = win32con.GENERIC_READ | (win32con.DELETE if delete else 0)
    handle = win32file.CreateFile(str(path),access,win32con.FILE_SHARE_READ,
        None,win32con.OPEN_EXISTING,0x00200000,None)
    try:
        if win32file.GetFileInformationByHandle(handle)[0] & (0x400|0x1000|0x4000):
            raise ValueError('File đã đổi thành liên kết hoặc dữ liệu offline/mã hóa: ' + str(path))
        fd = msvcrt.open_osfhandle(handle.Detach(),os.O_RDONLY | os.O_BINARY)
        return os.fdopen(fd,'rb')
    finally:
        handle.Close()


def _hash_stream(source,stop,target=None):
    digest = hashlib.sha256()
    while chunk := source.read(4*1024**2):
        check_stop(stop)
        digest.update(chunk)
        if target is not None:
            target.write(chunk)
    if target is not None:
        target.flush()
        os.fsync(target.fileno())
    return digest.hexdigest()


def _digest(path, stop, copy_to=None):
    with _pin_folders(path.parent), _open_file(path) as source:
        if os.fstat(source.fileno()).st_nlink>1:
            raise ValueError('File đã đổi thành hardlink: ' + str(path))
        if copy_to is None:
            return _hash_stream(source,stop)
        with copy_to.open('xb') as target:
            return _hash_stream(source,stop,target)


def _snapshot(root, *, stop=None, copy_to=None, progress=None):
    """Hash the entire tree (including empty directories), in deterministic order."""
    if copy_to is not None:
        copy_to.mkdir()
    digest = hashlib.sha256()
    files = size = 0
    folders = []
    for path, info, directory in tree(root,stop=stop):
        if has_streams(path):
            raise ValueError('Có luồng NTFS phụ (ADS); bản đầu không chuyển để tránh mất dữ liệu: ' + str(path))
        relative = path.relative_to(root)
        target = copy_to / relative if copy_to is not None else None
        if directory:
            if target is not None:
                if relative.parts:
                    with _pin_folders(target.parent):
                        target.mkdir()
                folders.append((path,target))
            row = ('d',relative.as_posix())
        else:
            if target is not None:
                with _pin_folders(target.parent):
                    value = _digest(path,stop,target)
                    _copy_metadata(path,target)
            else:
                value = _digest(path,stop)
            row = ('f',relative.as_posix(),info.st_size,value)
            files += 1
            size += info.st_size
            if progress:
                progress(info.st_size)
        digest.update(json.dumps(row,ensure_ascii=True,separators=(',',':')).encode()+b'\n')
    for source,target in reversed(folders):
        check_stop(stop)
        with _pin_folders(source,target):
            _copy_metadata(source,target)
    return {'sha256':digest.hexdigest(),'files':files,'bytes':size}


def _copy_metadata(source,target):
    shutil.copystat(source,target,follow_symlinks=False)
    if os.name=='nt':
        import win32security
        mask = win32security.DACL_SECURITY_INFORMATION
        acl = win32security.GetFileSecurity(str(source),mask)
        # Apply the effective old DACL, not broader access inherited on F:.
        win32security.SetNamedSecurityInfo(str(target),win32security.SE_FILE_OBJECT,
            mask | win32security.PROTECTED_DACL_SECURITY_INFORMATION,None,None,
            acl.GetSecurityDescriptorDacl(),None)


def _app_closed(app, entries):
    import psutil
    names = {Path(app['exe']).name.casefold()} if app else set()
    roots = [Path(entry[key]) for entry in entries for key in ('source','target')]
    if app and app.get('exe'):
        roots.append(Path(app['exe']).parent)
    for proc in psutil.process_iter(['name']):
        if proc.pid==os.getpid():
            continue
        name = (proc.info.get('name') or '').casefold()
        try:
            exe = proc.exe()
            if exe and any(inside(Path(exe),root) for root in roots):
                raise RuntimeError('Hãy đóng ứng dụng và tiến trình phụ trước: ' + (name or exe))
        except (psutil.NoSuchProcess,psutil.ZombieProcess):
            pass
        except psutil.AccessDenied:
            if name in names:
                raise RuntimeError('Chưa xác minh tiến trình đã đóng: ' + name)


def _portable_policy(source, app, portable):
    if app and inside(Path(app['exe']), source):
        if not portable or app['kind']!='unknown' or source!=Path(app['exe']).parent:
            raise ValueError('Không chuyển thư mục chứa bộ cài. Portable phải xác nhận riêng và chọn đúng thư mục EXE.')
        if any(source.glob('unins*.exe')) or any(source.glob('Uninstall*.exe')):
            raise ValueError('Phát hiện trình gỡ cài đặt; không coi là portable.')
        return 'portable'
    if portable:
        raise ValueError('Xác nhận portable chỉ áp dụng khi chọn đúng thư mục chứa EXE.')
    if any(source.glob('unins*.exe')) or any(source.glob('Uninstall*.exe')):
        raise ValueError('Phát hiện trình gỡ cài đặt; hãy chọn thư mục dữ liệu riêng.')
    # Manual input is for data, not for moving an unclassified application binary.
    if not app and any(source.glob('*.exe')):
        raise ValueError('Thư mục có EXE; hãy chọn EXE trước để phân loại.')
    return 'data'


def plan(folders, destination, *, selection=None, portable=False, stop=None):
    """Read-only preview. Each selected folder gets its own destination child."""
    parent = path_of(destination)
    if protected(parent,parent=True):
        raise ValueError('Không dùng vùng hệ thống, vùng trình duyệt hoặc thư mục Disk Growth Monitor làm đích.')
    no_link(parent)
    if not parent.is_dir():
        raise ValueError('Chọn phân vùng hoặc thư mục đích có sẵn.')
    app = identify(selection) if selection else None
    if app:
        app.pop('args',None)
    ident = uuid.uuid4().hex
    sources = list(dict.fromkeys(path_of(p) for p in folders))
    if not 1 <= len(sources) <= 16:
        raise ValueError('Chọn 1–16 thư mục riêng để chuyển.')
    if any(inside(a,b) for a in sources for b in sources if a!=b):
        raise ValueError('Chọn thư mục cha hoặc thư mục con, không chọn cả hai.')
    entries = []
    for source in sources:
        entry = _entry(source,parent,ident)
        _check_paths(entry,new=True)
        role = _portable_policy(source,app,portable and app is not None and inside(Path(app['exe']),source))
        if any(entry['target'].casefold()==other['target'].casefold() for other in entries):
            raise ValueError('Hai thư mục trùng tên ở đích; tách thành hai lượt.')
        data = {'bytes':0,'files':0}
        for path, info, is_dir in tree(source,stop=stop):
            if has_streams(path):
                raise ValueError('Có luồng NTFS phụ (ADS); chọn thư mục khác để không làm mất dữ liệu: ' + str(path))
            if not is_dir:
                if role=='data' and path.suffix.casefold() in ('.exe','.msi','.sys'):
                    raise ValueError('Thư mục chứa chương trình/cài đặt; hãy chọn thư mục dữ liệu hẹp hơn: ' + str(path))
                data['files'] += 1
                data['bytes'] += info.st_size
        entries.append(dict(entry,role=role,**data))
    if portable and not any(entry['role']=='portable' for entry in entries):
        raise ValueError('Chưa chọn đúng thư mục portable.')
    size = sum(entry['bytes'] for entry in entries)
    free = shutil.disk_usage(parent).free
    if free < size+RESERVE:
        raise ValueError('Ổ đích không đủ chỗ sao chép và 256 MB dự phòng.')
    return dict(id=ident,app=app,entries=entries,bytes=size,required=size+RESERVE,free=free)


def _journal_dir(*, create=True):
    path = home()/'data/app-moves'
    no_link(path,missing=True)
    if create:
        path.mkdir(exist_ok=True)
    return path


def _journal(ident):
    if not isinstance(ident,str) or not ID.fullmatch(ident):
        raise ValueError('Mã lượt chuyển không hợp lệ.')
    return _journal_dir()/(ident+'.json')


@contextmanager
def _lock():
    from .storage_maintenance import maintenance_lock
    with maintenance_lock():
        yield


def _save(record):
    atomic_json(_journal(record['id']),record)


def _junction(source,target):
    # pywin32 has no directory-junction helper. Use native PowerShell cmdlet,
    # binding paths via the child environment instead of assembling code.
    import subprocess
    from .platform_ops import system_tool
    env = dict(os.environ,DGM_MOVE_LINK=str(source),DGM_MOVE_TARGET=str(target))
    result = subprocess.run([system_tool(r'WindowsPowerShell\v1.0\powershell.exe'),'-NoProfile','-NonInteractive',
        '-Command',"$ErrorActionPreference='Stop'; New-Item -ItemType Junction -Path $env:DGM_MOVE_LINK -Target $env:DGM_MOVE_TARGET | Out-Null"],
        env=env,capture_output=True,timeout=20,creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode or not _points_to(source,target):
        raise RuntimeError('Không tạo được junction Windows; giữ dữ liệu bản gốc.')


def _points_to(source,target):
    try:
        return source.is_junction() and source.resolve(strict=True)==target.resolve(strict=True)
    except (OSError,RuntimeError):
        return False


def _check_preview(preview,stop):
    app = preview.get('app')
    fresh = plan([entry['source'] for entry in preview['entries']],Path(preview['entries'][0]['target']).parent,
                 selection=app['selection'] if app else None,
                 portable=any(entry['role']=='portable' for entry in preview['entries']),stop=stop)
    for old,new in zip(preview['entries'],fresh['entries']):
        if any(old[k]!=new[k] for k in ('source','target','role','bytes','files')):
            raise ValueError('Nguồn đã thay đổi sau khi xem trước; lập kế hoạch lại.')
    if (app or {}).get('exe')!=(fresh['app'] or {}).get('exe') or (app or {}).get('kind')!=(fresh['app'] or {}).get('kind'):
        raise ValueError('Shortcut đã đổi đích; lập kế hoạch lại.')
    if not ID.fullmatch(preview['id']):
        raise ValueError('Mã lượt chuyển không hợp lệ.')
    fresh['id']=preview['id']
    for entry in fresh['entries']:
        entry.update(_entry(Path(entry['source']),Path(entry['target']).parent,fresh['id']))
    return fresh


def execute(preview, *, stop=None, notify=None):
    """Copy everything first; then switch names without touching Windows registrations."""
    with _lock():
        current = _check_preview(preview,stop)
        _app_closed(current['app'],current['entries'])
        record = dict(current,state='copying',created=utc_now())
        if exists(_journal(record['id'])):
            raise ValueError('Kế hoạch đã được dùng. Xem nhật ký hoặc lập kế hoạch mới.')
        _save(record)
        try:
            for entry in record['entries']:
                source,target,_ = _check_paths(entry,new=True)
                _notify(notify,'Sao chép và xác minh: ' + str(source))
                done = 0
                report_at = 0
                def progress(size):
                    nonlocal done,report_at
                    done += size
                    if done>=report_at:
                        report_at=done+64*1024**2
                        _notify(notify,f'Đã sao chép {done/1024**2:,.0f}/{entry["bytes"]/1024**2:,.0f} MB · {source.name}')
                with _pin_folders(source,target.parent):
                    entry['proof'] = _snapshot(source,stop=stop,copy_to=target,progress=progress)
                    if entry['proof']['bytes']!=entry['bytes'] or entry['proof']['files']!=entry['files']:
                        raise ValueError('Nguồn đổi kích thước trong lúc chuyển.')
                    if _snapshot(target,stop=stop)!=entry['proof']:
                        raise RuntimeError('Bản sao không khớp SHA-256.')
                _save(record)
            record['state']='switching';_save(record)
            for entry in record['entries']:
                check_stop(stop)
                source,target,backup = _check_paths(entry)
                _app_closed(record['app'],record['entries'])
                with _pin_folders(source.parent,target):
                    if _snapshot(source,stop=stop)!=entry['proof'] or exists(backup):
                        raise ValueError('Nguồn thay đổi hoặc vị trí bản cũ đã tồn tại.')
                    check_stop(stop)
                    _app_closed(record['app'],record['entries'])
                    source.rename(backup)
                    try:
                        _junction(source,target)
                        if not _points_to(source,target):
                            raise RuntimeError('Junction không trỏ đúng thư mục đích.')
                    except BaseException:
                        if _points_to(source,target):
                            source.rmdir()
                        if not exists(source):
                            backup.rename(source)
                        raise
                    entry['switched']=True
                    _save(record)
            record['state']='complete';_save(record)
        except BaseException as exc:
            # During a multi-folder switch, restore the already switched roots
            # when the exact junction and backup still exist. Keep both trees.
            for entry in reversed(record['entries']):
                if not entry.get('switched'):
                    continue
                try:
                    source,target,backup = _check_paths(entry)
                    with _pin_folders(source.parent,target):
                        no_link(backup)
                        _app_closed(record['app'],record['entries'])
                        if _points_to(source,target):
                            source.rmdir()
                            backup.rename(source)
                            entry['switched']=False
                except (OSError,ValueError,RuntimeError):
                    pass
            record['state']='interrupted'
            record['error']=str(exc) or type(exc).__name__
            _save(record)
            raise
        return record


def history():
    records = []
    for path in _journal_dir(create=False).glob('*.json'):
        if not ID.fullmatch(path.stem):
            continue
        try:
            record = read_json(path)
            if (isinstance(record,dict) and record.get('id')==path.stem
                    and isinstance(record.get('state'),str) and isinstance(record.get('entries'),list)
                    and record['entries'] and all(isinstance(row,dict) and all(
                        isinstance(row.get(k),str) for k in ('source','target','backup')) for row in record['entries'])):
                records.append(record)
        except (OSError,ValueError,AttributeError):
            continue
    return sorted(records,key=lambda r:r.get('created',''),reverse=True)


def _load(ident):
    record = read_json(_journal(ident))
    if not isinstance(record,dict) or record.get('id')!=ident:
        raise ValueError('Không tìm thấy nhật ký chuyển.')
    return record


def _checked_record(ident):
    record = _load(ident)
    for entry in record['entries']:
        source,target,backup = _check_paths(entry)
        if any(entry.get(key)!=value for key,value in _entry(source,target.parent,ident).items()):
            raise ValueError('Nhật ký chứa đường dẫn không hợp lệ.')
    return record


def undo(ident, *, stop=None, notify=None):
    """Restore the retained source. Never overwrite updates at the destination."""
    with _lock():
        record = _checked_record(ident)
        if record['state'] not in ('complete','interrupted','switching','undoing'):
            raise ValueError('Không còn bản nguồn để hoàn tác.')
        _app_closed(record['app'],record['entries'])
        ready = []
        for entry in record['entries']:
            source,target,backup = _check_paths(entry)
            if not exists(backup):
                if exists(source) and not source.is_junction() and not entry.get('switched'):
                    continue
                raise ValueError('Không thấy đúng bản cũ; không hoàn tác tự động.')
            if exists(source) and not _points_to(source,target):
                raise ValueError('Đường dẫn cũ đã đổi hoặc không còn là junction của lượt này.')
            no_link(backup);no_link(target)
            _notify(notify,'Kiểm tra thay đổi trước khi hoàn tác: ' + str(target))
            if _snapshot(target,stop=stop)!=entry.get('proof') or _snapshot(backup,stop=stop)!=entry.get('proof'):
                raise ValueError('Dữ liệu đã thay đổi sau khi chuyển. Giữ cả hai bản; không ghi đè.')
            ready.append((entry,source,target,backup))
        record['state']='undoing';_save(record)
        for entry,source,target,backup in reversed(ready):
            _app_closed(record['app'],record['entries'])
            with _pin_folders(source.parent,target):
                if _points_to(source,target):
                    source.rmdir()  # Windows removes the junction itself, never its target.
                if exists(source):
                    raise ValueError('Đường dẫn gốc đã được tạo lại; giữ bản cũ.')
                backup.rename(source)
            entry['switched']=False;_save(record)
        record['state']='undone';_save(record)
        return record


def retire(ident, *, stop=None, notify=None, permanent=False):
    """Retire only the exact old tree. Permanent cleanup is a separate approval."""
    with _lock():
        record = _checked_record(ident)
        if record['state'] not in ('complete','retiring'):
            raise ValueError('Chỉ dọn bản cũ sau khi chuyển thành công.')
        _app_closed(record['app'],record['entries'])
        ready = []
        for entry in record['entries']:
            source,target,backup = _check_paths(entry)
            if not _points_to(source,target):
                raise ValueError('Đường dẫn app không còn trỏ về đích đã kiểm tra.')
            if (entry.get('retired') or entry.get('delete_started')) and not exists(backup):
                entry['retired']=True
                continue
            no_link(backup);no_link(target)
            _notify(notify,'Đối chiếu bản cũ: ' + str(backup))
            if entry.get('delete_started'):
                if not permanent:
                    raise ValueError('Lượt dọn vĩnh viễn chưa xong; cần dùng lại đúng lựa chọn.')
                _verify_remaining(backup,target,stop)
            else:
                if _snapshot(backup,stop=stop)!=entry['proof']:
                    raise ValueError('Bản cũ đã thay đổi; chưa dọn.')
                _verify_remaining(backup,target,stop)
            ready.append((entry,source,target,backup))
        record['state']='retiring';_save(record)
        from send2trash import send2trash
        for entry,source,target,backup in ready:
            check_stop(stop)
            _app_closed(record['app'],record['entries'])
            with _pin_folders(backup.parent,target):
                no_link(backup)
                if not _points_to(source,target):
                    raise ValueError('Junction đã thay đổi; không dọn.')
                if permanent:
                    entry['delete_started']=True;_save(record)
                    _delete_verified(backup,target,stop)
                else:
                    send2trash(str(backup))
            if exists(backup):
                raise RuntimeError('Bản cũ chưa được dọn.')
            entry['retired']=True;_save(record)
        record['state']='retired';_save(record)
        return record


def _verify_remaining(backup,target,stop):
    for path, info, directory in tree(backup,stop=stop):
        other = target/path.relative_to(backup)
        no_link(other)
        if has_streams(path):
            raise ValueError('Bản cũ có thêm luồng dữ liệu NTFS; giữ nguyên.')
        if directory:
            if not other.is_dir():
                raise ValueError('Thiếu thư mục ở đích; giữ bản cũ.')
        elif not other.is_file() or other.stat().st_size!=info.st_size or _digest(path,stop)!=_digest(other,stop):
            raise ValueError('Có file thiếu hoặc đã đổi ở đích: ' + str(path.relative_to(backup)) + '. Giữ bản cũ.')


def _delete_verified(backup,target,stop):
    # Do not recursively delete an unchecked path. Check every source and its
    # current equivalent again at the point of unlink, never touch the target.
    entries = list(tree(backup,stop=stop))
    for path,info,directory in reversed(entries):
        check_stop(stop)
        no_link(path)
        if has_streams(path):
            raise ValueError('Bản cũ có thêm luồng dữ liệu NTFS; giữ nguyên.')
        if directory:
            path.rmdir()
        else:
            other = target/path.relative_to(backup)
            with _pin_folders(path.parent,other.parent):
                no_link(other)
                if os.name=='nt':
                    _unlink_matching_handle(path,other,info,stop)
                elif path.stat().st_ino==info.st_ino and other.is_file() and _digest(path,stop)==_digest(other,stop):
                    path.unlink()
                else:
                    raise ValueError('File đã thay đổi trước khi dọn; giữ phần còn lại.')


def _unlink_matching_handle(path,other,expected,stop):
    """Bind verification and deletion to the same Windows file handle."""
    import ctypes
    from ctypes import wintypes
    import msvcrt
    with _open_file(path,delete=True) as old, _open_file(other) as current:
        info = os.fstat(old.fileno())
        if (info.st_ino!=expected.st_ino or info.st_nlink!=1
                or info.st_size!=os.fstat(current.fileno()).st_size
                or _hash_stream(old,stop)!=_hash_stream(current,stop)):
            raise ValueError('File đã thay đổi trước khi dọn; giữ phần còn lại.')
        check_stop(stop)
        flag = ctypes.c_ubyte(1)  # FILE_DISPOSITION_INFO.DeleteFile
        operation = ctypes.WinDLL('kernel32',use_last_error=True).SetFileInformationByHandle
        operation.argtypes = (wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD)
        operation.restype = wintypes.BOOL
        if not operation(msvcrt.get_osfhandle(old.fileno()),4,ctypes.byref(flag),ctypes.sizeof(flag)):
            raise ctypes.WinError(ctypes.get_last_error())
