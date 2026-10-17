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
    from .runtime import binary_root
    try: marker=json.loads((binary_root()/'installed-app.json').read_text(encoding='utf-8-sig'))
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
    progress_id=uuid.uuid4().hex
    if notify:notify('task-progress',{'task':'update-download','id':progress_id,'percent':0,'stage':'Chuẩn bị tải bộ cài','completed':[]})
    folder=home()/'data/updates'/f"{release['version']}-{release['sha256'][:12]}"; folder.mkdir(parents=True,exist_ok=True)
    if folder.resolve()!=folder.absolute(): raise ValueError('Không tải qua junction/symlink')
    target=folder/'installer.exe'
    if target.is_symlink(): raise ValueError('Đích tải là symlink')
    if target.exists() and target.stat().st_size==release['size'] and file_digest(target)==release['sha256']:
        if notify:notify('task-progress',{'task':'update-download','id':progress_id,'percent':100,'stage':'Sẵn sàng cài cập nhật','completed':['Đã đọc bản tải sẵn','Đã kiểm tra kích thước và SHA-256']})
        return str(target)
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
                    notify('task-progress',{'task':'update-download','id':progress_id,'percent':min(90,total*90//release['size']),'stage':'Đang tải bộ cài','detail':f'{total:,}/{release["size"]:,} byte','completed':[]})
        if total!=release['size'] or digest.hexdigest()!=release['sha256']: raise ValueError('SHA-256/dung lượng không khớp; không cho cài')
        partial.replace(target); atomic(folder/'release.json',release)
        if notify:notify('task-progress',{'task':'update-download','id':progress_id,'percent':100,'stage':'Sẵn sàng cài cập nhật','completed':['Đã tải bộ cài','Đã kiểm tra kích thước và SHA-256']})
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
    from .runtime import binary_root
    target=Path(session.get('install_root',session['root'])).absolute()
    expected=home()/'.dgm-app' if session.get('source_conversion') else binary_root()
    if target!=expected or target.resolve()!=target:raise ValueError('Đích cập nhật khác bản app này')
    return path,session


def save_session(path,session,**changes):
    session.update(changes); atomic(path,session)


def release_lease(session):
    try:
        if json.loads(lease_path().read_text())['session']==session['id']: lease_path().unlink()
    except FileNotFoundError: pass


def prepare_install(release, installer, *, reopen=False):
    from .storage_maintenance import maintenance_lock
    with maintenance_lock():return _prepare_install(release,installer,reopen=reopen)


def _prepare_install(release, installer, *, reopen=False):
    """Update the current installation while retaining its existing data home."""
    from .runtime import binary_root,managed_executable
    source_conversion=installation_mode()!='installed'
    target=home()/'.dgm-app' if source_conversion else binary_root()
    if target.resolve()!=target.absolute():raise ValueError('Không cập nhật qua junction/symlink')
    link=target/'data-home.json'
    if link.resolve()!=link.absolute():raise ValueError('Liên kết data-home không được là symlink')
    if source_conversion and target.exists() and any(target.iterdir()) and not managed_executable():
        raise ValueError('Thư mục runtime .dgm-app có dữ liệu không nhận diện được; chưa ghi đè')
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
    from .history_backend import log_mode
    databases=[] if log_mode() else [home()/'data'/name for name in ('history.sqlite','comparisons.sqlite','normalization.sqlite')]
    required=sum(p.stat().st_size+sum(q.stat().st_size for q in (p.with_name(p.name+'-wal'),) if q.exists()) for p in databases if p.exists())+release['size']*8
    from .storage_safety import require_space
    space=require_space(required,operation='sao lưu/cài cập nhật và chỗ trống sau cài')
    required=space['required_bytes']
    if active_update(): raise RuntimeError('Một lượt cập nhật đang hoạt động')
    pending = pending_recovery()
    if pending: raise RuntimeError('Cần khôi phục bộ chạy nền của lượt cập nhật trước: '+str(pending[0]['session']))
    lease_path().unlink(missing_ok=True)  # Only our known stale lease, never a user-selected path.
    ident='session-'+uuid.uuid4().hex; folder=home()/'data/updates'/ident; folder.mkdir(parents=True)
    marker={} if source_conversion else json.loads((target/'installed-app.json').read_text(encoding='utf-8-sig'))
    bootstrap_exe=(Path(sys.executable).with_name('python.exe') if not getattr(sys,'frozen',False) else binary_root()/'dgm.exe')
    session={'id':ident,'root':str(home()),'release':release,'installer':str(installer),'parent':owner(),
             'install_root':str(target),'source_conversion':source_conversion,
             'bootstrap':{'executable':str(bootstrap_exe),'arguments':[] if getattr(sys,'frozen',False) else ['-m','dgm']},
             'original':owned,'portable':True if source_conversion else bool(marker.get('portable',False)),
             'phase':'waiting','changed':[],'restore_pending':False,'reopen':bool(reopen)}
    if reopen:
        if getattr(sys,'frozen',False):
            session['reopen_command']=[str(binary_root()/'DiskGrowthMonitor.exe'),'--home',str(home()),'gui']
        else:
            session['reopen_command']=[str(Path(sys.executable).with_name('pythonw.exe')),'-m','dgm','--home',str(home()),'gui']
    path=folder/'session.json'; atomic(path,session)
    fd=os.open(lease_path(),os.O_WRONLY|os.O_CREAT|os.O_EXCL)
    with os.fdopen(fd,'w',encoding='utf-8') as stream: json.dump({'owner':owner(),'session':ident},stream)
    helper=folder/'update-helper.ps1'
    try:
        shutil.copyfile(Path(__file__).with_name('update_helper.ps1'),helper)
        if reopen:shutil.copyfile(Path(__file__).with_name('update_watch.ps1'),folder/'update-watch.ps1')
    except BaseException:release_lease(session);raise
    return {'session':str(path),'helper':str(helper),'admin_required':True,'backup_bytes_required':required,
            'destination':str(target),'data_home':str(home()),'mode':'source-managed' if source_conversion else 'in-place'}


def launch(handoff):
    """One UAC prompt for the installer; restart observer keeps the original token."""
    from .platform_ops import system_tool
    from win32com.shell import shell, shellcon
    import win32process
    path,session=load_session(handoff['session'])
    arguments=['-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',handoff['helper'],'-Session',str(path)]
    try:
        if session.get('reopen'):
            # A PowerShell observer does not lock Python/Qt files being replaced.
            # Start BEFORE runas: an alternate admin account must not own the GUI.
            subprocess.Popen([str(system_tool('WindowsPowerShell/v1.0/powershell.exe')),
                '-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(path.parent/'update-watch.ps1'),'-Session',str(path)],
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),cwd=str(home()))
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


def start_update(stop=None, notify=None, *, reopen=False):
    """CLI and GUI share one check/download/verify/handoff transaction."""
    stop=stop or __import__('threading').Event();notify=notify or (lambda *a:None)
    ident=uuid.uuid4().hex
    def progress(percent,stage,completed=None):
        notify('task-progress',{'task':'update','id':ident,'percent':percent,'stage':stage,'completed':completed or []})
    progress(0,'Kiểm tra bản mới chính thức')
    release=check()
    notify('update-release',release)
    if stop.is_set():raise ProcessCancelled('Đã hủy cập nhật; app hiện tại chưa thay đổi')
    if not release['available']:
        progress(100,'Đang dùng bản mới nhất',['Đã kiểm tra Release; không cài hoặc hạ phiên bản'])
        return {'launched':False,'release':release}
    progress(10,'Đang tải và kiểm tra bộ cài',['Đã kiểm tra Release'])
    def downloading(event,data):
        if event=='task-progress':progress(10+int(data.get('percent',0)*.65),data.get('stage','Đang tải bộ cài'),data.get('completed'))
        else:notify(event,data)
    installer=download(release,stop,downloading)
    if stop.is_set():raise ProcessCancelled('Đã hủy cập nhật trước khi cài; app hiện tại chưa thay đổi')
    progress(80,'Kiểm tra vị trí cài và lịch sử cần giữ',['Đã tải và xác minh SHA-256'])
    ticket=prepare_install(release,installer,reopen=reopen)
    if stop.is_set():
        path,session=load_session(ticket['session']);save_session(path,session,phase='cancelled');release_lease(session)
        raise ProcessCancelled('Đã hủy cập nhật trước UAC; app hiện tại chưa thay đổi')
    progress(90,'Chờ xác nhận UAC; hủy sẽ giữ app hiện tại',['Đã xác minh bộ cài và đích cập nhật'])
    notify('update-handoff',{'message':'Không hủy khi bộ cài đã bắt đầu; đang chờ UAC'})
    result=launch(ticket)
    progress(95,'Đã bàn giao; chưa kết luận cài thành công',['Đã xác nhận quyền và bàn giao bộ cài'])
    return {**result,'release':release}


def stage(path):
    path,session=load_session(path)
    from .background import status, control
    from .installation import verified_background_owner
    state=status()
    if state['scan'].get('running'): raise RuntimeError('Một lượt quét đã bắt đầu; chưa cài và không cưỡng bức dừng')
    from .storage_safety import require_space
    from .history_backend import log_mode
    backup_names=() if log_mode() else ('history.sqlite','comparisons.sqlite','normalization.sqlite')
    required=sum(p.stat().st_size for p in (home()/'data'/n for n in backup_names) if p.exists())+session['release']['size']*8
    require_space(required,operation='sao lưu/cài cập nhật')
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
        for name in backup_names:
            history=home()/'data'/name
            if history.resolve()!=history.absolute() or (backup/name).is_symlink():raise ValueError('Database/backup không được là symlink')
            if history.exists():
                with closing(sqlite3.connect(history.as_uri()+'?mode=ro',uri=True)) as source, closing(sqlite3.connect(backup/name)) as target:
                    source.backup(target,pages=2048,progress=lambda *args:require_space(operation='sao lưu cập nhật'));target.commit()
        preserved={}
        for name,tables in (('history.sqlite',('scans','members','benchmarks')),('comparisons.sqlite',('pairs',)),('normalization.sqlite',('datasets','overrides'))):
            database=backup/name
            if not database.exists():continue
            with closing(sqlite3.connect(database)) as db:
                preserved[name]={table:db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                    for table in tables if db.execute('SELECT 1 FROM sqlite_master WHERE name=?',(table,)).fetchone()}
        session['preserved_counts']=preserved
        from .history_backend import log_mode
        if log_mode():
            folder=home()/'data/history'
            session['preserved_logs']={str(p.relative_to(folder)):file_digest(p) for p in folder.rglob('*') if p.is_file()}
        session['config_sha256']=file_digest(backup/'config.json') if (backup/'config.json').exists() else None
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
    if success:
        try:
            for relative,digest in session.get('preserved_logs',{}).items():
                log_path=home()/'data/history'/relative
                if log_path.resolve()!=log_path.absolute() or not inside(log_path,home()/'data/history') or file_digest(log_path)!=digest:
                    raise RuntimeError('Log lịch sử thay đổi/mất sau cập nhật: '+relative)
            for name,tables in session.get('preserved_counts',{}).items():
                database=home()/'data'/name
                with closing(sqlite3.connect(database.as_uri()+'?mode=ro',uri=True)) as db:
                    for table,count in tables.items():
                        if db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]<count:
                            raise RuntimeError('Thiếu bản ghi lịch sử sau cập nhật: '+table)
            digest=session.get('config_sha256')
            if digest and file_digest(home()/'data/config.json')!=digest:
                raise RuntimeError('Cấu hình đã thay đổi ngoài dự kiến; có bản sao lưu trong session')
        except Exception as exc:success=False;errors.append(str(exc))
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
    if success:
        # Post-verification only: cleanup failure must never turn an installed,
        # verified version into a failed update or discard recovery evidence.
        try:
            from .storage_maintenance import cleanup_updates
            cleanup=cleanup_updates()
            save_session(path,session,cleanup=cleanup)
        except Exception as exc:logging.getLogger('dgm').warning('STORAGE post-update cleanup deferred: %s',exc)
    return {'updated':success,'restored':True,'session':str(path),'backup':str(path.parent/'backup')}


def pending_recovery():
    result=[]
    for path in (home()/'data/updates').glob('session-*/session.json'):
        try:
            _,session=load_session(path)
            if session.get('restore_pending'):result.append({'session':str(path),'phase':session.get('phase'),'errors':session.get('errors',[])})
        except (OSError,ValueError,KeyError):continue
    return result
