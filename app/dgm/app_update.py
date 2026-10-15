"""Explicit application updates. No pip, git, system Python or unattended install.

The detached PowerShell coordinator survives replacement of the frozen runtime.
Registered runners are disabled/stopped, NOT removed; their exact state is restored.
"""
from __future__ import annotations
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import uuid
from contextlib import closing
import psutil
from . import __version__
from .config import home, inside
from .elevation import atomic, alive, owner
from .platform_ops import ProcessCancelled

REPO = 'bbkien2312/DiskGrowthMonitor'
API = f'https://api.github.com/repos/{REPO}/releases/latest'
MAX_INSTALLER = 512 * 1024 * 1024


def semver(value):
    match = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)', value or '')
    if not match: raise ValueError('Chỉ hỗ trợ phiên bản ổn định dạng X.Y.Z')
    return tuple(map(int, match.groups()))


def asset_url(url, tag, name):
    parsed = urllib.parse.urlsplit(url)
    expected = f'/{REPO}/releases/download/{tag}/{name}'
    if parsed.scheme != 'https' or parsed.netloc != 'github.com' or parsed.path != expected or parsed.query or parsed.fragment:
        raise ValueError('Asset không thuộc Release chính thức của app')
    return url


def open_asset(url):
    request = urllib.request.Request(url, headers={'User-Agent':'DiskGrowthMonitor/'+__version__})
    response = urllib.request.urlopen(request, timeout=20)
    final = urllib.parse.urlsplit(response.geturl())
    if final.scheme != 'https' or final.netloc not in ('github.com','release-assets.githubusercontent.com','objects.githubusercontent.com'):
        response.close(); raise ValueError('Địa chỉ tải chuyển hướng không hợp lệ')
    return response


def parse_release(raw):
    if raw.get('draft') or raw.get('prerelease'): raise ValueError('Không cài bản nháp/bản thử nghiệm')
    tag = raw['tag_name']; version = '.'.join(map(str,semver(tag)))
    if raw.get('html_url') != f'https://github.com/{REPO}/releases/tag/{tag}': raise ValueError('Release không thuộc repository chính thức')
    assets = [a for a in raw.get('assets',[]) if a.get('name') == 'installer.exe']
    if len(assets) != 1: raise ValueError('Release chưa có đúng một installer.exe')
    asset = assets[0]; size = asset.get('size',0)
    if not isinstance(size,int) or not 0 < size <= MAX_INSTALLER: raise ValueError('Dung lượng installer không hợp lệ')
    url = asset_url(asset['browser_download_url'],tag,'installer.exe')
    digest = asset.get('digest') or ''
    if digest and not re.fullmatch(r'sha256:[0-9a-fA-F]{64}',digest): raise ValueError('Digest Release không hợp lệ')
    return {'version':version,'tag':tag,'url':url,'size':size,'sha256':digest[7:].lower() if digest else '',
            'release_url':raw['html_url'],'notes':str(raw.get('body') or '')[:50000],
            'published_at':raw.get('published_at'),'current':__version__,
            'available':semver(version)>semver(__version__),'signature':'SHA-256 là kiểm tra toàn vẹn, không phải chữ ký nhà phát hành.'}


def check():
    from .updates import fetch
    raw = fetch(API); result = parse_release(raw)
    if not result['sha256']:
        matches = [a for a in raw.get('assets',[]) if a.get('name') == 'SHA256SUMS.txt']
        if len(matches) != 1: raise ValueError('Release thiếu SHA-256; không cho cài')
        url = asset_url(matches[0]['browser_download_url'],result['tag'],'SHA256SUMS.txt')
        with open_asset(url) as response: content = response.read(65537)
        if len(content)>65536: raise ValueError('Checksum quá lớn')
        hashes = re.findall(r'^([a-fA-F0-9]{64})\s+\*?installer\.exe\s*$',content.decode('utf-8-sig'),re.M)
        if len(hashes)!=1: raise ValueError('Checksum installer thiếu/không rõ ràng')
        result['sha256']=hashes[0].lower()
    result['mode'] = installation_mode()
    return result


def install_separately(release, installer, destination):
    validate_release(release)
    installer=Path(installer).absolute();destination=Path(destination).absolute()
    if destination.resolve()!=destination or inside(destination,home()) or not destination.is_dir() or any(destination.iterdir()):
        raise ValueError('Chọn thư mục trống ngoài source, không qua junction/symlink')
    if not inside(installer,home()/'data/updates') or installer.resolve()!=installer or installer.stat().st_size!=release['size'] or file_digest(installer)!=release['sha256']:
        raise ValueError('Bộ cài chưa qua kiểm tra SHA-256/dung lượng')
    argv=[str(installer),'/DIR='+str(destination)]
    logging.getLogger('dgm').debug('APP-UPDATE separate installer argv=%s',argv)
    subprocess.Popen(argv)
    return {'launched':True,'destination':str(destination),'source_unchanged':True}


def installation_mode():
    try: marker=json.loads((home()/'installed-app.json').read_text(encoding='utf-8-sig'))
    except (OSError,ValueError): return 'source'
    return 'installed' if getattr(sys,'frozen',False) and marker.get('app')=='DiskGrowthMonitor' else 'source'


def validate_release(release):
    semver(release['version']); asset_url(release['url'],release['tag'],'installer.exe')
    if semver(release['version']) != semver(release['tag']): raise ValueError('Tag/phiên bản không khớp')
    if not re.fullmatch('[0-9a-f]{64}',release.get('sha256','')): raise ValueError('Thiếu SHA-256 hợp lệ')
    if not isinstance(release['size'],int) or not 0 < release['size'] <= MAX_INSTALLER: raise ValueError('Dung lượng không hợp lệ')
    if semver(release['version']) <= semver(__version__): raise ValueError('Không có bản mới hơn; không hạ phiên bản')


def file_digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def download(release, stop=None, notify=None):
    validate_release(release)
    folder=home()/'data/updates'/f"{release['version']}-{release['sha256'][:12]}"; folder.mkdir(parents=True,exist_ok=True)
    if folder.resolve()!=folder.absolute(): raise ValueError('Không tải qua junction/symlink')
    target=folder/'installer.exe'
    if target.is_symlink(): raise ValueError('Đích tải là symlink')
    if target.exists() and target.stat().st_size==release['size'] and file_digest(target)==release['sha256']: return str(target)
    if shutil.disk_usage(folder).free < release['size']*2: raise RuntimeError('Không đủ dung lượng tải cập nhật')
    partial=folder/(uuid.uuid4().hex+'.part'); total=0; digest=hashlib.sha256(); last=0
    try:
        with open_asset(release['url']) as response, partial.open('xb') as stream:
            while True:
                if stop is not None and stop.is_set(): raise ProcessCancelled('Đã hủy tải cập nhật')
                chunk=response.read(128*1024)
                if not chunk: break
                total+=len(chunk)
                if total>release['size']: raise ValueError('File tải lớn hơn metadata Release')
                digest.update(chunk); stream.write(chunk)
                if notify and (time.monotonic()-last>.2 or total==release['size']):
                    notify('info',{'message':f'Tải cập nhật: {total:,}/{release["size"]:,} byte ({total*100//release["size"]}%)'}); last=time.monotonic()
        if total!=release['size'] or digest.hexdigest()!=release['sha256']: raise ValueError('SHA-256/dung lượng không khớp; không cho cài')
        partial.replace(target); atomic(folder/'release.json',release)
        return str(target)
    finally: partial.unlink(missing_ok=True)


def lease_path(): return home()/'data/update.lock'


def active_update():
    try: lease=json.loads(lease_path().read_text(encoding='utf-8'))
    except FileNotFoundError: return False
    except (OSError,ValueError): raise RuntimeError('Khóa cập nhật không đọc được; kiểm tra Nhật ký trước khi quét')
    return alive(lease['owner'])


def check_scan_allowed():
    if active_update(): raise RuntimeError('Đang bàn giao/cài cập nhật; chưa được quét. Chờ cập nhật kết thúc.')


def load_session(path):
    path=Path(path).absolute(); base=home()/'data/updates'
    if path.name!='session.json' or not re.fullmatch('session-[0-9a-f]{32}',path.parent.name) or path.parent.parent!=base:
        raise ValueError('Session cập nhật không thuộc app này')
    if path.resolve()!=path: raise ValueError('Session không cho phép junction/symlink')
    session=json.loads(path.read_text(encoding='utf-8'))
    if Path(session['root'])!=home() or session['id']!=path.parent.name: raise ValueError('Session khác bản cài')
    return path,session


def save_session(path,session,**changes):
    session.update(changes); atomic(path,session)


def release_lease(session):
    try:
        if json.loads(lease_path().read_text())['session']==session['id']: lease_path().unlink()
    except FileNotFoundError: pass


def prepare_install(release, installer):
    """Build a handoff ticket only. Source runs never overwrite their checkout."""
    if installation_mode()!='installed': raise RuntimeError('Bản source/.venv: chỉ tải bộ cài và cài vào thư mục khác; không ghi đè source.')
    validate_release(release)
    installer=Path(installer).absolute()
    if not inside(installer,home()/'data/updates') or installer.resolve()!=installer: raise ValueError('Installer phải ở cache cập nhật riêng của app')
    if installer.stat().st_size!=release['size'] or file_digest(installer)!=release['sha256']: raise ValueError('Installer không qua kiểm tra SHA-256')
    from .background import status
    from .installation import owns_background,verified_background_owner
    state=status()
    if state['scan'].get('running'): raise RuntimeError('Đang quét: chờ kết thúc hoặc tự bấm Dừng trước khi cập nhật')
    if any(s.get('installed') is None or s.get('error') for s in (state['task'],state['service'])): raise RuntimeError('Không xác minh được Task/Service; chưa cập nhật')
    for kind in ('task','service'):
        if state[kind].get('installed') and owns_background(kind,state[kind]) and not verified_background_owner(kind,state[kind]):
            raise RuntimeError('Chưa xác minh được chủ sở hữu '+kind+'; chưa cập nhật')
    owned={kind:item for kind in ('task','service') if verified_background_owner(kind,item:=state[kind])}
    if owned.get('task',{}).get('running'): raise RuntimeError('Task đang chạy: chờ hoàn tất trước khi cập nhật')
    history=home()/'data/history.sqlite'
    required=(history.stat().st_size if history.exists() else 0)+release['size']*5
    if shutil.disk_usage(home()).free<required: raise RuntimeError(f'Cần ít nhất {required:,} byte trống cho sao lưu/cài cập nhật')
    if active_update(): raise RuntimeError('Một lượt cập nhật đang hoạt động')
    pending = pending_recovery()
    if pending: raise RuntimeError('Cần khôi phục bộ chạy nền của lượt cập nhật trước: '+str(pending[0]['session']))
    lease_path().unlink(missing_ok=True)  # Only our known stale lease, never a user-selected path.
    ident='session-'+uuid.uuid4().hex; folder=home()/'data/updates'/ident; folder.mkdir(parents=True)
    marker=json.loads((home()/'installed-app.json').read_text(encoding='utf-8-sig'))
    session={'id':ident,'root':str(home()),'release':release,'installer':str(installer),'parent':owner(),
             'original':owned,'portable':bool(marker.get('portable',False)),'phase':'waiting','changed':[],'restore_pending':False}
    path=folder/'session.json'; atomic(path,session)
    fd=os.open(lease_path(),os.O_WRONLY|os.O_CREAT|os.O_EXCL)
    with os.fdopen(fd,'w',encoding='utf-8') as stream: json.dump({'owner':owner(),'session':ident},stream)
    helper=folder/'update-helper.ps1'
    try: shutil.copyfile(Path(__file__).with_name('update_helper.ps1'),helper)
    except BaseException:release_lease(session);raise
    return {'session':str(path),'helper':str(helper),'admin_required':bool(owned),'backup_bytes_required':required}


def launch(handoff):
    """The OS prompts for UAC only when this installation owns a runner."""
    from .platform_ops import system_tool
    from win32com.shell import shell, shellcon
    import win32process
    path,session=load_session(handoff['session'])
    arguments=['-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',handoff['helper'],'-Session',str(path)]
    try:
        logging.getLogger('dgm').debug('APP-UPDATE coordinator session=%s admin=%s',path,handoff['admin_required'])
        info=shell.ShellExecuteEx(fMask=shellcon.SEE_MASK_NOCLOSEPROCESS,lpVerb='runas' if handoff['admin_required'] else 'open',
            lpFile=str(system_tool('WindowsPowerShell/v1.0/powershell.exe')),lpParameters=subprocess.list2cmdline(arguments),nShow=0)
        handle=info['hProcess']
        try: process=psutil.Process(win32process.GetProcessId(handle)); identity={'pid':process.pid,'created':process.create_time()}
        finally: handle.Close()
        atomic(lease_path(),{'owner':identity,'session':session['id']})
    except BaseException as exc:
        release_lease(session); save_session(path,session,phase='cancelled')
        if getattr(exc,'winerror',None)==1223:
            from .elevation import ElevationCancelled
            raise ElevationCancelled('Bạn đã hủy UAC; app hiện tại vẫn hoạt động.') from exc
        raise
    return {'launched':True,'session':str(path)}


def stage(path):
    path,session=load_session(path)
    from .background import status, control
    from .installation import verified_background_owner
    state=status()
    if state['scan'].get('running'): raise RuntimeError('Một lượt quét đã bắt đầu; chưa cài và không cưỡng bức dừng')
    try:
        for kind in session['original']:
            item=state[kind]
            if not verified_background_owner(kind,item): raise RuntimeError('Bộ chạy nền đã thay đổi; không tác động')
            original=session['original'][kind]
            fields=('enabled','running') if kind=='task' else ('enabled','running','startup_type')
            if any(item.get(field)!=original.get(field) for field in fields):raise RuntimeError('Trạng thái bộ chạy nền vừa thay đổi; hãy kiểm tra lại trước khi cập nhật')
            session['changed'].append(kind); save_session(path,session,restore_pending=True)
            control(kind,'disable',allow_uac=False)
        state=status()
        if state['scan'].get('running') or any(state[k].get('running') is not False or state[k].get('enabled') is not False or
                (k=='service' and state[k].get('state')!=1) for k in session['original']):
            raise RuntimeError('Bộ chạy nền chưa dừng/tắt hoàn toàn; chưa cài')
        backup=path.parent/'backup'; backup.mkdir(exist_ok=True)
        if backup.resolve()!=backup.absolute():raise ValueError('Không sao lưu qua junction/symlink')
        for name in ('config.json','ui-settings.json'):
            source=home()/'data'/name
            if source.resolve()!=source.absolute() or (backup/name).is_symlink():raise ValueError('File cấu hình/backup không được là symlink')
            if source.exists(): shutil.copy2(source,backup/name)
        history=home()/'data/history.sqlite'
        if history.resolve()!=history.absolute() or (backup/'history.sqlite').is_symlink():raise ValueError('Database/backup không được là symlink')
        if history.exists():
            with closing(sqlite3.connect(history.as_uri()+'?mode=ro',uri=True)) as source, closing(sqlite3.connect(backup/'history.sqlite')) as target:
                source.backup(target);target.commit()
        installer=Path(session['installer'])
        if installer.stat().st_size!=session['release']['size'] or file_digest(installer)!=session['release']['sha256']: raise ValueError('Installer đã thay đổi trước khi cài')
        save_session(path,session,phase='prepared')
        return {'prepared':True,'backup':str(backup)}
    except BaseException as exc:
        save_session(path,session,phase='failed',error=str(exc)); raise


def finish(path, success=False):
    path,session=load_session(path)
    from .background import status
    from .installation import verified_background_owner
    from .schedule import enable_task
    from .windows_service import restore_state
    errors=[];restore_errors=[]
    if success and semver(__version__)!=semver(session['release']['version']):
        success=False; errors.append('Phiên bản sau cài không khớp Release')
    for kind in session.get('changed',[]):
        try:
            current=status()[kind]
            if not verified_background_owner(kind,current): raise RuntimeError('Bộ chạy nền khác/chưa rõ; không tự khôi phục')
            original=session['original'][kind]
            if kind=='task': enable_task(bool(original['enabled']))
            else: restore_state(original)
        except Exception as exc: restore_errors.append(kind+': '+str(exc))
    errors.extend(restore_errors)
    save_session(path,session,phase='complete' if success and not errors else 'failed',restore_pending=bool(restore_errors),errors=errors)
    release_lease(session)
    if errors: raise RuntimeError('Cần khôi phục thủ công; xem '+str(path)+'\n'+'\n'.join(errors))
    return {'updated':success,'restored':True,'session':str(path),'backup':str(path.parent/'backup')}


def pending_recovery():
    result=[]
    for path in (home()/'data/updates').glob('session-*/session.json'):
        try:
            _,session=load_session(path)
            if session.get('restore_pending'):result.append({'session':str(path),'phase':session.get('phase'),'errors':session.get('errors',[])})
        except (OSError,ValueError,KeyError):continue
    return result
