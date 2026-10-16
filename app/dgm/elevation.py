"""One-shot UAC workers. Fixed actions, hashed tickets, nonce-scoped IPC.

The unelevated caller never kills an elevated process. The elevated worker
owns its cancellable tools and observes its own cancel file/parent lifetime.
"""
from __future__ import annotations
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
import uuid
import psutil
from .config import home, load, Policy
from .platform_ops import is_admin, ProcessCancelled


class ElevationCancelled(ProcessCancelled):
    pass


def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def atomic(path,value):
    temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    fd=os.open(temporary,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
    try:
        with os.fdopen(fd,'wb') as file:file.write(canonical(value))
        temporary.replace(path)
    finally:temporary.unlink(missing_ok=True)


class DirectoryPins:
    """Deny directory rename/reparse substitution while privileged IPC runs."""
    def __init__(self,folder):self.folder=folder;self.handles=[];self.identity=None
    def __enter__(self):
        try:
            for path in (home(),home()/'data',home()/'data/uac',self.folder):
                if path.resolve()!=path.absolute():raise ValueError('IPC không cho phép junction/symlink')
                if os.name=='nt':
                    import win32file,win32con,ntsecuritycon
                    handle=win32file.CreateFile(str(path),win32con.GENERIC_READ,
                        win32con.FILE_SHARE_READ|win32con.FILE_SHARE_WRITE,None,win32con.OPEN_EXISTING,
                        win32file.FILE_FLAG_BACKUP_SEMANTICS|win32file.FILE_FLAG_OPEN_REPARSE_POINT,None)
                    self.handles.append(handle);info=win32file.GetFileInformationByHandle(handle)
                    if info[0]&0x400:raise ValueError('IPC không cho phép reparse point')
                    self.identity=[info[4],info[8],info[9]]
            return self
        except BaseException:self.close();raise
    def close(self):
        for handle in reversed(self.handles):handle.Close()
        self.handles=[]
    def __exit__(self,*args):self.close()


def alive(owner):
    try:return abs(psutil.Process(owner['pid']).create_time()-owner['created'])<.01
    except psutil.AccessDenied:return True  # Do not steal a live elevated lock.
    except (psutil.NoSuchProcess,KeyError,ValueError,TypeError):return False


def owner():
    return {'pid':os.getpid(),'created':psutil.Process().create_time()}


def interactive():
    if os.name!='nt':return False
    import ctypes
    session=ctypes.c_ulong()
    return bool(ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(),ctypes.byref(session)) and session.value)


def private_directory(path):
    path.mkdir()
    if os.name=='nt':
        import win32api,win32con,win32security,ntsecuritycon
        token=win32security.OpenProcessToken(win32api.GetCurrentProcess(),win32con.TOKEN_QUERY)
        try:sid=win32security.GetTokenInformation(token,win32security.TokenUser)[0]
        finally:token.Close()
        acl=win32security.ACL()
        for account in (sid,win32security.CreateWellKnownSid(win32security.WinBuiltinAdministratorsSid),win32security.CreateWellKnownSid(win32security.WinLocalSystemSid)):
            acl.AddAccessAllowedAceEx(win32security.ACL_REVISION,win32con.OBJECT_INHERIT_ACE|win32con.CONTAINER_INHERIT_ACE,ntsecuritycon.FILE_ALL_ACCESS,account)
        sd=win32security.SECURITY_DESCRIPTOR();sd.SetSecurityDescriptorDacl(1,acl,0)
        win32security.SetFileSecurity(str(path),win32security.DACL_SECURITY_INFORMATION|win32security.PROTECTED_DACL_SECURITY_INFORMATION,sd)


def directory(ticket):
    if not re.fullmatch('[0-9a-f]{32}',ticket):raise ValueError('Mã tác vụ Admin không hợp lệ')
    base=home()/'data/uac';base.mkdir(exist_ok=True)
    target=base/ticket
    if target.resolve()!=target.absolute() or base.resolve()!=base.absolute():
        raise ValueError('Thư mục tác vụ Admin không được là liên kết/junction')
    return target


class PromptLock:
    def __init__(self,token):self.token=token;self.path=home()/'data/uac/prompt.lock'
    def __enter__(self):
        try:fd=os.open(self.path,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
        except FileExistsError:
            try:
                current=json.loads(self.path.read_text(encoding='utf-8'))
                if alive(current.get('parent',{})) or alive(current.get('worker',{})):
                    raise RuntimeError('Đang có tác vụ Admin/chờ UAC. Chờ tác vụ đó hoàn tất trước khi chạy tiếp.')
            except (ValueError,FileNotFoundError):pass
            self.path.unlink(missing_ok=True);fd=os.open(self.path,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
        with os.fdopen(fd,'wb') as file:file.write(canonical({'token':self.token,'parent':owner()}))
        return self
    def __exit__(self,*args):
        try:
            current=json.loads(self.path.read_text(encoding='utf-8'))
            if current.get('token')==self.token:
                if alive(current.get('worker',{})):atomic(self.path,{'token':self.token,'worker':current['worker']})
                else:self.path.unlink(missing_ok=True)
        except (OSError,ValueError):pass


def validate(action,payload):
    if not isinstance(payload,dict):raise ValueError('Yêu cầu Admin phải là object')
    keys={
        'admin-probe':set(),'scan':{'name','profile'},'system-check':{'tool'},
        'cleanup-plan':{'rules','update_done','days'},
        'cleanup-apply':{'plan_id','confirmation','plan_digest'},
        'background-control':{'kind','action'},
    }
    if action not in keys or set(payload)!=keys[action]:raise ValueError('Thao tác/tham số Admin không được phép')
    if action=='scan':
        p=payload['profile']
        if not isinstance(payload['name'],str) or not payload['name'] or not isinstance(p,dict) or not p.get('roots'):raise ValueError('Cấu hình quét không hợp lệ')
        if p.get('engine','native') not in ('native','wiztree','windirstat','gdu'):raise ValueError('Engine không hợp lệ')
        if p.get('strict_browsers',False) and p.get('engine','native')!='native':raise ValueError('Bảo vệ Chrome/Edge yêu cầu native; không tự đổi phạm vi')
        Policy(p)
    elif action=='system-check':
        if payload['tool'] not in ('winsxs','drivers','shadowstorage'):raise ValueError('Tool hệ thống không hợp lệ')
    elif action=='cleanup-plan':
        from .cleanup import rules
        if not isinstance(payload['rules'],list) or not payload['rules'] or any(x not in rules() for x in payload['rules']):raise ValueError('Nhóm dọn không hợp lệ')
        if type(payload['update_done']) is not bool or (payload['days'] is not None and (type(payload['days']) is not int or payload['days']<0)):raise ValueError('Tham số preview không hợp lệ')
    elif action=='cleanup-apply':
        if not re.fullmatch('[0-9a-f]{12}',str(payload['plan_id'])) or payload['confirmation']!='XOA '+payload['plan_id'] or not re.fullmatch('[0-9a-f]{64}',str(payload['plan_digest'])):raise ValueError('Thiếu xác nhận/dấu vân tay đề xuất dọn')
    elif action=='background-control':
        allowed={'task':('install','enable','disable','remove'),'service':('install','start','stop','enable','disable','remove','uninstall')}
        if payload['kind'] not in allowed or payload['action'] not in allowed[payload['kind']]:raise ValueError('Thao tác chạy nền không hợp lệ')


class WindowsProcess:
    def __init__(self,handle):self.handle=handle
    @property
    def owner(self):
        import ctypes
        pid=ctypes.windll.kernel32.GetProcessId(ctypes.c_void_p(int(self.handle)))
        value={'pid':pid,'created':0}
        try:value['created']=psutil.Process(pid).create_time()
        except psutil.Error:pass
        return value
    def poll(self):
        import win32event,win32process
        return win32process.GetExitCodeProcess(self.handle) if win32event.WaitForSingleObject(self.handle,0)==win32event.WAIT_OBJECT_0 else None
    def close(self):self.handle.Close()


def launch(ticket,checksum):
    import pythoncom
    from win32com.shell import shell,shellcon
    pythoncom.CoInitialize()
    try:
        if getattr(sys,'frozen',False):
            executable=Path(sys.executable);args=['--home',str(home()),'_elevated-worker','--ticket',ticket,'--digest',checksum]
        else:
            executable=Path(sys.prefix)/'Scripts/pythonw.exe'
            args=['-I','-m','dgm.elevation','--home',str(home()),'--ticket',ticket,'--digest',checksum]
        if not executable.is_file():raise RuntimeError('Không tìm thấy Python riêng của app; chạy install-local.cmd trước.')
        try:
            value=shell.ShellExecuteEx(fMask=shellcon.SEE_MASK_NOCLOSEPROCESS|getattr(shellcon,'SEE_MASK_NOASYNC',0x00000100),lpVerb='runas',lpFile=str(executable),lpParameters=subprocess.list2cmdline(args),lpDirectory=str(home()),nShow=0)
        except Exception as exc:
            if getattr(exc,'winerror',None)==1223:raise ElevationCancelled('Đã hủy cấp quyền Admin; tác vụ chưa chạy.') from exc
            raise
        if not value.get('hProcess'):raise RuntimeError('Windows chưa trả handle tác vụ Admin; không tự chạy lại.')
        return WindowsProcess(value['hProcess'])
    finally:pythoncom.CoUninitialize()


def _cancel(folder,token):
    atomic(folder/'cancel.json',{'token':token})


def _clean(folder):
    # Only files created by this IPC protocol, never a recursive delete.
    try:
        with DirectoryPins(folder):
            for name in ('request.json','events.jsonl','result.json','cancel.json','claimed','state.json'):
                try:(folder/name).unlink(missing_ok=True)
                except OSError:pass
    except (OSError,ValueError):return
    try:folder.rmdir()
    except OSError:pass


def request(action,payload,stop=None,notify=None):
    stop=stop or threading.Event();notify=notify or (lambda *a:None)
    payload=json.loads(canonical(payload))
    if action=='scan' and isinstance(payload,dict) and isinstance(payload.get('profile'),dict):
        payload['profile']['roots']=[str(Path(r).absolute()) for r in payload['profile'].get('roots',[])]
    validate(action,payload)
    if stop.is_set():raise ElevationCancelled('Đã hủy tác vụ trước khi xin quyền Admin.')
    if not interactive():raise PermissionError('Phiên chạy nền không mở UAC. Cài/bật Task hoặc Service bằng quyền Admin trước.')
    token=uuid.uuid4().hex;folder=directory(token)
    with PromptLock(token):
        private_directory(folder)
        pins=DirectoryPins(folder).__enter__()
        try:
            cfg=load()
            task={'version':1,'token':token,'action':action,'payload':payload,'parent':owner(),'created':time.time(),
                  'home':str(home()),'directory_id':pins.identity,'debug':bool(cfg['debug']),'engines':cfg['engines'],
                  'user_environment':{k:os.environ[k] for k in ('USERPROFILE','LOCALAPPDATA','APPDATA') if os.environ.get(k)}}
            checksum=digest(task);atomic(folder/'request.json',task)
            from .logging_utils import audit
            audit('uac-request',ticket=token,operation=action)
            notify('info',{'message':'Đang chờ bạn cấp quyền tại UAC của Windows…'})
            notify('uac-state',{'phase':'prompt','operation':action})
        except BaseException:
            pins.close();_clean(folder);raise
        process=None;completed=False;offset=0;pending=b'';cancel_sent=False;interrupted=False
        def events():
            nonlocal offset,pending
            if not (folder/'events.jsonl').exists():return
            with (folder/'events.jsonl').open('rb') as file:
                file.seek(offset);pending+=file.read(1_000_000);offset=file.tell()
            while b'\n' in pending:
                line,pending=pending.split(b'\n',1);event=json.loads(line)
                if event.get('token')!=token:continue
                kind,data=event['kind'],event['data']
                if kind=='log':logging.getLogger('dgm').log(data['level'],'[Admin] %s',data['message'])
                elif kind=='audit':audit(data['action'],**data['fields'])
                else:notify(kind,data)
        try:
            process=launch(token,checksum)
            notify('uac-state',{'phase':'running','operation':action})
            notify('info',{'message':'Đã cấp quyền; đang khởi động tác vụ Admin…'})
            while True:
                try:
                    events()
                    if stop.is_set() and not cancel_sent:
                        _cancel(folder,token);cancel_sent=True
                        notify('info',{'message':'Đã gửi yêu cầu dừng. Đang chờ tác vụ Admin kết thúc an toàn…'})
                    code=process.poll()
                    if code is not None:break
                    time.sleep(.1)
                except KeyboardInterrupt:
                    stop.set();interrupted=True
            events()
            result_path=folder/'result.json'
            if not result_path.exists():raise RuntimeError(f'Tác vụ Admin thoát mã {code} nhưng không có kết quả. Kiểm tra Nhật ký; không tự chạy lại lệnh.')
            result=json.loads(result_path.read_text(encoding='utf-8'))
            if result.get('token')!=token or result.get('digest')!=checksum:raise RuntimeError('Kết quả Admin không khớp tác vụ.')
            completed=True
            audit('uac-finished',ticket=token,operation=action,ok=result['ok'])
            if not result['ok']:
                if result.get('cancelled'):raise ElevationCancelled(result['error'])
                raise RuntimeError(result['error'])
            if interrupted and action not in ('scan','cleanup-apply','system-check'):raise ElevationCancelled('Tác vụ Admin đã kết thúc; kiểm tra kết quả/nhật ký trước khi chạy lại.')
            return result['value']
        except ElevationCancelled:
            audit('uac-cancelled',ticket=token,operation=action);raise
        finally:
            notify('uac-state',{'phase':'done','operation':action})
            pins.close()
            if process:
                if not completed:
                    try:_cancel(folder,token)
                    except OSError:pass
                code=process.poll();worker_owner=getattr(process,'owner',{}) if code is None else {};process.close()
                if code is not None:
                    try:atomic(home()/'data/uac/prompt.lock',{'token':token,'parent':owner()})
                    except OSError:pass
                    _clean(folder)
                else:
                    try:
                        record=json.loads((home()/'data/uac/prompt.lock').read_text(encoding='utf-8'))
                        worker=record.get('worker') or worker_owner
                        atomic(home()/'data/uac/prompt.lock',{'token':token,'worker':worker})
                    except OSError:pass
            else:_clean(folder)


class Events:
    def __init__(self,folder,token):
        self.folder=folder;self.token=token;self.lock=threading.Lock();self.bytes=0
        self.file=os.fdopen(os.open(folder/'events.jsonl',os.O_CREAT|os.O_EXCL|os.O_WRONLY),'wb')
    def close(self):self.file.close()
    def __call__(self,kind,data):
        if kind=='log':data={**data,'message':str(data['message'])[:128_000]}
        raw=canonical({'token':self.token,'kind':kind,'data':data})+b'\n'
        with self.lock:
            if self.bytes>8_000_000 and kind in ('log','progress'):return
            self.file.write(raw);self.file.flush()
            self.bytes+=len(raw)


def dispatch(action,payload,stop,notify):
    validate(action,payload)
    if stop.is_set():raise ElevationCancelled('Đã hủy trước khi bắt đầu tác vụ Admin.')
    if action=='admin-probe':
        logging.getLogger('dgm').info('Admin probe complete; no scan, maintenance or cleanup commands executed')
        return {'admin':is_admin(),'pid':os.getpid(),'home':str(home()),'executable':sys.executable,
                'note':'Chỉ kiểm tra quyền tác vụ. Không quét ổ, không chạy DISM, không xóa; GUI vẫn giữ quyền ban đầu.'}
    if action=='scan':
        from .scanner import scan
        return scan(payload['name'],payload['profile'],stop,notify,allow_uac=False)
    if action=='system-check':
        from .diagnostics import system_check
        return system_check(payload['tool'],allow_uac=False,stop=stop)
    if action=='cleanup-plan':
        from .cleanup import plan
        return plan(payload['rules'],payload['update_done'],payload['days'],allow_uac=False,stop=stop)
    if action=='cleanup-apply':
        from .cleanup import apply
        notify('info',{'message':'Đang xử lý các mục đã duyệt bằng Admin. Bảo trì DISM sẽ được chờ kết thúc, không cưỡng bức dừng.'})
        return apply(payload['plan_id'],payload['confirmation'],allow_uac=False,expected_digest=payload['plan_digest'],stop=stop)
    from .background import control
    return control(payload['kind'],payload['action'],allow_uac=False,stop=stop)


def probe(notify=None,stop=None):
    if is_admin():return dispatch('admin-probe',{},threading.Event(),notify or (lambda *a:None))
    return request('admin-probe',{},notify=notify,stop=stop)


def worker_main(ticket,checksum):
    if not re.fullmatch('[0-9a-f]{64}',checksum):raise ValueError('Dấu vân tay yêu cầu không hợp lệ')
    folder=directory(ticket)
    with DirectoryPins(folder) as pins:return _worker_pinned(folder,ticket,checksum,pins.identity)


def _worker_pinned(folder,ticket,checksum,identity):
    raw=(folder/'request.json').read_bytes()
    if len(raw)>4_000_000 or hashlib.sha256(raw).hexdigest()!=checksum:raise ValueError('Yêu cầu Admin đã bị thay đổi; không chạy.')
    task=json.loads(raw)
    if task.get('directory_id')!=identity:raise ValueError('Thư mục yêu cầu Admin đã bị thay thế; không chạy.')
    if task.get('version')!=1 or task.get('token')!=ticket or Path(task['home']).resolve()!=home().resolve():raise ValueError('Yêu cầu không khớp thư mục app')
    if not is_admin():raise PermissionError('Worker chưa có quyền Administrator; không tự hỏi UAC lặp lại.')
    notify=Events(folder,ticket);done=threading.Event();stop=threading.Event()
    result={'token':ticket,'digest':checksum,'ok':False}
    from . import logging_utils
    bridge_token=None;config_token=None;debug_token=None
    try:
        if time.time()-task['created']>900 or not alive(task['parent']):raise ElevationCancelled('Yêu cầu đã hết hạn hoặc cửa sổ gửi đã đóng; tác vụ chưa chạy.')
        fd=os.open(folder/'claimed',os.O_CREAT|os.O_EXCL|os.O_WRONLY);os.close(fd)
        os.environ.update({k:v for k,v in task['user_environment'].items() if k in ('USERPROFILE','LOCALAPPDATA','APPDATA')})
        for key in ('TEMP','TMP','SQLITE_TMPDIR'):os.environ[key]=str(home()/'data/tmp')
        os.environ['PYTHONNOUSERSITE']='1'
        from . import config
        config_token=config.ENGINE_OVERRIDE.set(task['engines'])
        debug_token=config.DEBUG_OVERRIDE.set(task['debug'])
        bridge_token=logging_utils.BRIDGE.set(notify);logging_utils.setup(task['debug'])
        atomic(folder/'state.json',{'token':ticket,'worker':owner()})
        lock=home()/'data/uac/prompt.lock'
        try:
            record=json.loads(lock.read_text(encoding='utf-8'))
            if record.get('token')==ticket:atomic(lock,{**record,'worker':owner()})
        except FileNotFoundError:pass
        def monitor():
            while not done.wait(.1):
                try:
                    cancelled=json.loads((folder/'cancel.json').read_text(encoding='utf-8')).get('token')==ticket
                except (OSError,ValueError):cancelled=False
                if cancelled or not alive(task['parent']):stop.set()
        thread=threading.Thread(target=monitor,daemon=True);thread.start()
        if (folder/'cancel.json').exists() or not alive(task['parent']):stop.set()
        notify('admin-ready',{'message':'Tác vụ Admin đã sẵn sàng','pid':os.getpid(),'operation':task['action']})
        result['value']=dispatch(task['action'],task['payload'],stop,notify);result['ok']=True
    except BaseException as exc:
        result['cancelled']=isinstance(exc,(ProcessCancelled,KeyboardInterrupt))
        result['error']=str(exc) or 'Đã hủy tác vụ Admin.'
        if not result['cancelled']:logging.getLogger('dgm').exception('Admin worker failed')
    finally:
        done.set()
        if 'thread' in locals():thread.join(timeout=1)
        atomic(folder/'result.json',result)
        notify.close()
        if bridge_token is not None:logging_utils.BRIDGE.reset(bridge_token)
        if config_token is not None:config.ENGINE_OVERRIDE.reset(config_token)
        if debug_token is not None:config.DEBUG_OVERRIDE.reset(debug_token)
    return 0 if result['ok'] else 130 if result.get('cancelled') else 1


def main():
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--home',required=True);parser.add_argument('--ticket',required=True);parser.add_argument('--digest',required=True)
    args=parser.parse_args()
    from . import config
    config.ROOT=Path(args.home).resolve()
    raise SystemExit(worker_main(args.ticket,args.digest))


if __name__=='__main__':main()
