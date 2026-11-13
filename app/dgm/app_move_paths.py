"""Bounded Windows app discovery; never launch an executable or shortcut."""
from __future__ import annotations

import os
from pathlib import Path
import re
import stat

from .config import browser_path, home
from .platform_ops import ProcessCancelled

MAX_ENTRIES = 150_000
REPARSE = 0x400


def check_stop(stop):
    if stop is not None and stop.is_set():
        raise ProcessCancelled('Đã dừng chuyển; dữ liệu nguồn được giữ.')


def path_of(value):
    raw = os.fspath(value)
    path = Path(raw)
    if not path.is_absolute() or '..' in path.parts or (os.name == 'nt' and not re.fullmatch(r'[A-Za-z]:', path.drive)):
        raise ValueError('Cần đường dẫn tuyệt đối trên ổ cục bộ: ' + raw)
    if os.name == 'nt' and any(p.endswith(('.', ' ')) or ':' in p or Path(p).is_reserved() for p in path.parts[1:]):
        raise ValueError('Tên Windows không hợp lệ hoặc có luồng dữ liệu phụ: ' + raw)
    return path


def has_streams(path):
    if os.name != 'nt':
        return False
    import pywintypes
    import win32file
    try:
        return any(name != '::$DATA' for _,name in win32file.FindStreams(str(path)))
    except pywintypes.error as exc:
        if exc.winerror==38:  # no streams on this object
            return False
        raise


def no_link(path, *, missing=False):
    """Use lstat, including on dangling junctions; check parents first."""
    for component in (*reversed(path.parents), path):
        try:
            info = component.lstat()
        except FileNotFoundError:
            if missing:
                return
            raise
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & REPARSE:
            raise ValueError('Không đi theo junction/symlink/reparse: ' + str(component))


def exists(path):
    try:
        path.lstat()
        return True
    except FileNotFoundError:
        return False


def inside(path, base):
    return path == base or base in path.parents


def win_dirs():
    user = Path.home()
    system = Path(os.environ.get('SystemRoot', r'C:\Windows'))
    common = Path(os.environ.get('ProgramData', str(system.parent / 'ProgramData')))
    roaming = Path(os.environ.get('APPDATA', str(user / 'AppData/Roaming')))
    local = Path(os.environ.get('LOCALAPPDATA', str(user / 'AppData/Local')))
    return system, common, roaming, local


def protected(path, *, parent=False, allow_installed=False):
    system, common, roaming, local = win_dirs()
    user = Path.home()
    parts = {p.casefold() for p in path.parts}
    if browser_path(path) or parts & {'windowsapps', 'wpsystem', 'system volume information', '$recycle.bin',
                                    '.git', '.ssh', 'windows', 'winsxs', 'driverstore', 'recovery'}:
        return True
    installed = [local / 'Programs'] + [Path(v) for key in ('ProgramFiles','ProgramFiles(x86)','ProgramW6432')
                                      if (v := os.environ.get(key))]
    from .runtime import binary_root
    blocks = [system, home(), binary_root(), local / 'Packages', roaming / 'Microsoft', local / 'Microsoft',
              common / 'Microsoft', user / '.codex']
    if not allow_installed:
        blocks += installed
    if any(inside(path, base) or (not parent and inside(base, path)) for base in blocks):
        return True
    broad = [user, user / 'AppData', common, roaming, local, system.parent / 'Users',
             user / 'Downloads', user / 'Documents', user / 'Desktop', local / 'Temp',
             roaming / 'Microsoft/Windows/Start Menu']
    return not parent and (path.parent == path or any(inside(base, path) for base in broad))


def local_volume(path):
    if os.name == 'nt':
        import win32api
        import win32file
        if win32file.GetDriveType(path.anchor) != 3:
            raise ValueError('Chỉ hỗ trợ phân vùng cố định cục bộ: ' + path.anchor)
        info = win32api.GetVolumeInformation(path.anchor)
        if info[-1].upper() != 'NTFS':
            raise ValueError('Bản đầu chỉ chuyển giữa các phân vùng NTFS.')
        return str(info[1])
    return str(path.stat().st_dev)


def tree(path, *, stop=None, maximum=MAX_ENTRIES):
    """Metadata only, no profile traversal through nested reparse points."""
    path = path_of(path)
    no_link(path)
    stack = [path]
    count = 0
    while stack:
        check_stop(stop)
        item = stack.pop()
        if browser_path(item):
            raise ValueError('Phạm vi có dữ liệu Chrome/Edge; không đọc: ' + str(path))
        if item.name.casefold() in ('.git','.ssh','windowsapps','system volume information','$recycle.bin'):
            raise ValueError('Phạm vi chứa thư mục được bảo vệ; chọn thư mục riêng hẹp hơn.')
        info = item.lstat()
        is_dir = stat.S_ISDIR(info.st_mode)
        if (getattr(info,'st_file_attributes',0) & (REPARSE | 0x1000 | 0x4000)
                or stat.S_ISLNK(info.st_mode)
                or not (is_dir or stat.S_ISREG(info.st_mode))):
            raise ValueError('Có liên kết, dữ liệu offline/mã hóa hoặc loại file không hỗ trợ: ' + str(item))
        if not is_dir and info.st_nlink > 1:
            raise ValueError('Có file hardlink; chưa hỗ trợ chuyển nguyên trạng: ' + str(item))
        count += 1
        if count > maximum:
            raise ValueError('Phạm vi vượt giới hạn bản đầu; chọn thư mục con hẹp hơn.')
        yield item, info, is_dir
        if is_dir:
            with os.scandir(item) as children:
                paths = []
                for entry in children:
                    if browser_path(entry.path):
                        raise ValueError('Phạm vi có dữ liệu Chrome/Edge; dừng trước khi đọc.')
                    paths.append(Path(entry.path))
                    if count + len(stack) + len(paths) > maximum:
                        raise ValueError('Phạm vi vượt giới hạn bản đầu; chọn thư mục con hẹp hơn.')
            stack.extend(sorted(paths, reverse=True))


def _shortcut(path):
    import json
    import subprocess
    from .platform_ops import system_tool
    # Isolate Windows Shell's COM parser. Never call Save, Resolve or execute
    # the shortcut or its arguments. Pass the path as data, not script text.
    result = subprocess.run([system_tool(r'WindowsPowerShell\v1.0\powershell.exe'),
        '-NoProfile','-NonInteractive','-Command',
        "$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); "
        "$s=New-Object -ComObject WScript.Shell; $l=$s.CreateShortcut($env:DGM_MOVE_SHORTCUT); "
        "@{target=$l.TargetPath; args=$l.Arguments}|ConvertTo-Json -Compress"],
        env=dict(os.environ,DGM_MOVE_SHORTCUT=str(path)),capture_output=True,timeout=12,
        creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise ValueError('Không đọc được shortcut; chọn EXE trực tiếp.')
    link = json.loads(result.stdout.decode('utf-8-sig'))
    return os.path.expandvars(link['target'] or ''),link['args'] or ''


def _installed(exe):
    if os.name != 'nt':
        return []
    import winreg
    matches = []
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
            try:
                root = winreg.OpenKey(hive, r'Software\Microsoft\Windows\CurrentVersion\Uninstall', 0, winreg.KEY_READ | view)
            except FileNotFoundError:
                continue
            with root:
                for index in range(winreg.QueryInfoKey(root)[0]):
                    try:
                        with winreg.OpenKey(root, winreg.EnumKey(root,index)) as entry:
                            def value(name):
                                try:
                                    v, _ = winreg.QueryValueEx(entry,name)
                                    return os.path.expandvars(v).strip() if isinstance(v,str) else ''
                                except FileNotFoundError:
                                    return ''
                            location = value('InstallLocation').strip('"')
                            icon = re.sub(r',-?\d+$','',value('DisplayIcon')).strip('"')
                            name = value('DisplayName')
                    except (FileNotFoundError,PermissionError):
                        continue
                    if name and ((location and Path(location).parent != Path(location) and inside(exe,Path(location)))
                                 or (icon and Path(icon) == exe)):
                        item = {'name':name,'location':location,'evidence':'Đăng ký gỡ cài đặt Windows'}
                        if item not in matches:
                            matches.append(item)
    return matches


def _version_names(exe):
    import win32api
    import pywintypes
    try:
        langs = win32api.GetFileVersionInfo(str(exe),r'\VarFileInfo\Translation')
        result = []
        for lang, page in langs[:2]:
            try:
                name = win32api.GetFileVersionInfo(str(exe),fr'\StringFileInfo\{lang:04x}{page:04x}\ProductName')
                if isinstance(name,str) and name not in result:
                    result.append(name)
            except (OSError,pywintypes.error):
                pass
        return result
    except (OSError,pywintypes.error):
        return []


def identify(selection):
    selected = path_of(selection)
    if browser_path(selected) or selected.stem.casefold() in ('chrome','msedge','msedgewebview2','edge'):
        raise ValueError('Không đọc cấu hình hay thư mục Chrome/Edge.')
    local_volume(selected)
    no_link(selected)
    if not selected.is_file() or selected.suffix.lower() not in ('.exe','.lnk'):
        raise ValueError('Chọn file .exe hoặc shortcut .lnk đang tồn tại.')
    raw, args = _shortcut(selected) if selected.suffix.lower()=='.lnk' else (str(selected),'')
    if not raw or not Path(raw).is_absolute():
        raise ValueError('Shortcut không trỏ trực tiếp tới EXE cục bộ. Dùng đường dẫn thủ công cho dữ liệu.')
    exe = path_of(raw)
    if browser_path(exe) or exe.stem.casefold() in ('chrome','msedge','msedgewebview2','edge'):
        raise ValueError('Không kiểm tra Chrome/Edge.')
    if exe.suffix.lower()!='.exe':
        raise ValueError('Shortcut không trỏ tới EXE. Không chạy launcher để dò.')
    local_volume(exe)
    if exe.stem.casefold() in ('cmd','powershell','pwsh','explorer','rundll32','wscript','cscript','msiexec','python','pythonw'):
        raise ValueError('Shortcut trỏ tới chương trình trung gian; hãy chọn EXE thật hoặc thêm thư mục thủ công.')
    store = any(p.casefold()=='windowsapps' for p in exe.parts)
    no_link(exe)
    if not exe.is_file():
        raise ValueError('Không tìm thấy EXE đích.')
    installed = [] if store else _installed(exe)
    system, _, _, _ = win_dirs()
    packaged_location = inside(exe,system) or protected(exe,allow_installed=False) or any(
        p.casefold() in ('program files','program files (x86)','program files (arm)') for p in exe.parts)
    kind = 'store' if store else 'installed' if installed or packaged_location else 'unknown'
    products = _version_names(exe) if os.name=='nt' and not store else []
    names = list(dict.fromkeys([*(x['name'] for x in installed), *products, exe.stem]))
    return {'selection':str(selected),'exe':str(exe),'kind':kind,'names':names,
            'registered':installed,'has_shortcut_arguments':bool(args),'args':args}


def measure(path, stop=None):
    count = size = 0
    for _, info, directory in tree(path,stop=stop):
        if not directory:
            count += 1
            size += info.st_size
    return {'bytes':size,'files':count}


def manual(path, *, stop=None):
    path = path_of(path)
    if protected(path):
        raise ValueError('Chọn một thư mục dữ liệu riêng, không phải vùng hệ thống/dùng chung.')
    local_volume(path)
    no_link(path)
    if protected(path) or not path.is_dir():
        raise ValueError('Chọn một thư mục dữ liệu riêng, không phải vùng hệ thống/dùng chung.')
    return {'path':str(path),'role':'data','evidence':'Người dùng chỉ định','confidence':'manual', **measure(path,stop)}


def discover(selection, *, stop=None):
    app = identify(selection)
    exe = Path(app['exe'])
    _, common, roaming, local = win_dirs()
    rows = {}
    def add(path, role, evidence, confidence):
        check_stop(stop)
        if path in rows or browser_path(path):
            return
        if role=='data' and (protected(path) or inside(exe,path)):
            return
        try:
            no_link(path)
            if not path.is_dir():
                return
        except FileNotFoundError:
            return
        except (ValueError,PermissionError) as exc:
            rows[path]={'path':str(path),'role':'blocked','evidence':str(exc),'bytes':None,'confidence':confidence}
            return
        row = {'path':str(path),'role':role,'evidence':evidence,'confidence':confidence,'bytes':None}
        if role != 'blocked':
            try:
                row.update(measure(path,stop))
            except (ValueError,OSError) as exc:
                row.update(role='blocked',evidence=evidence+' · '+str(exc))
        rows[path] = row

    if exe.stem.casefold()=='windowtranslator':
        add(common/'StudioFreesia/WindowTranslator','data','Vị trí model của WindowTranslator','known')
    if exe.stem.casefold().replace(' ','').replace('-','')=='hcut':
        add(roaming/'H cut','data','H cut dùng thư mục người dùng này','known')
    args=app.pop('args')
    match=re.search(r'(?:^|\s)--user-data-dir(?:=|\s+)(?:"([^"]+)"|([^\s"]+))',args,re.I)
    if match:
        try:
            hinted=path_of(os.path.expandvars(match.group(1) or match.group(2)))
        except ValueError:
            pass
        else:
            add(hinted,'data','Tham số --user-data-dir của shortcut','shortcut')
    for name in app['names'][:10]:
        if not re.fullmatch(r'[\w .+-]{2,80}',name) or name.endswith(('.', ' ')):
            continue
        for base in (roaming,local,common):
            add(base/name,'data','Trùng tên ứng dụng; cần xác nhận','name')
    # Installed/store paths are reported, never traversed by this feature.
    if app['kind']!='unknown':
        root = next((Path(r['location']) for r in app['registered'] if r['location'] and inside(exe,Path(r['location']))),exe.parent)
        rows.setdefault(root,{'path':str(root),'role':'blocked','confidence':'known','bytes':None,
                              'evidence':'Microsoft Store: chuyển qua Windows nếu hỗ trợ' if app['kind']=='store'
                              else 'Bản cài Windows: dùng chức năng chuyển/cài lại của hãng'})
    elif not protected(exe.parent):
        add(exe.parent,'portable','Chứa EXE; chỉ chuyển khi xác nhận là portable','manual')
    return {'app':app,'rows':list(rows.values())}
