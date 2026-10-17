import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid
import pytest
from typer.testing import CliRunner
from dgm import elevation as uac
from dgm.config import home,load,save


class FixtureProcess:
    def __init__(self,process):self.process=process
    def poll(self):return self.process.poll()
    def close(self):
        if self.process.poll() is not None:self.process.wait(timeout=2)


@pytest.fixture
def transport(isolated,monkeypatch):
    cfg=load();cfg['debug']=True;save(cfg)
    from dgm.logging_utils import setup
    setup(True)
    monkeypatch.setattr(uac,'interactive',lambda:True)
    scenario=['events'];calls=[];processes=[]
    def launch(ticket,checksum):
        calls.append((ticket,checksum))
        process=subprocess.Popen([sys.executable,'-I',str(Path(__file__).with_name('uac_fixture_worker.py')),
             '--home',str(home()),'--ticket',ticket,'--digest',checksum,'--scenario',scenario[0]],creationflags=0x08000000 if os.name=='nt' else 0)
        processes.append(process);return FixtureProcess(process)
    monkeypatch.setattr(uac,'launch',launch)
    yield scenario,calls,processes
    for process in processes:
        if process.poll() is None:process.wait(timeout=12)


def test_transport_progress_logs_audit_and_cleanup(transport):
    scenario,calls,processes=transport;events=[]
    before=(home()/'data/config.json').read_bytes()
    result=uac.request('system-check',{'tool':'winsxs'},notify=lambda e,d:events.append((e,d)))
    assert result['fixture'] and len(calls)==1
    assert any(e=='progress' and d['count']==20 for e,d in events)
    assert [d['phase'] for e,d in events if e=='uac-state']==['prompt','running','done']
    text=(home()/'logs/app.log').read_text(encoding='utf-8')
    assert '[Admin] fixture INFO from worker' in text
    assert '[Admin] fixture DEBUG from worker' not in text
    assert '[Admin] fixture DEBUG from worker' in (home()/'logs/debug.log').read_text(encoding='utf-8')
    audit=(home()/'logs/audit.jsonl').read_text(encoding='utf-8');assert 'fixture-worker' in audit
    assert not list((home()/'data/uac').iterdir())
    assert (home()/'data/config.json').read_bytes()==before


def test_transport_real_native_scan_unsaved_profile(transport,isolated):
    scenario,calls,_=transport;scenario[0]='native'
    root=isolated/'fixture';root.mkdir();(root/'allowed').write_bytes(b'abc')
    chrome=root/'Chrome';chrome.mkdir();(chrome/'secret').write_bytes(b'not scanned')
    p={'roots':[str(root)],'engine':'native','strict_browsers':True,'threshold_mb':3,'excludes':[],'mft':False}
    before=(home()/'data/config.json').read_bytes()
    result=uac.request('scan',{'name':'unsaved-demo','profile':p})
    assert result['status']=='complete' and result['files']==1 and result['logical']==3
    from dgm.store import Store
    s=Store();row=s.scan(result['scan_id']);s.close()
    assert json.loads(row['config'])==p and row['profile']=='unsaved-demo'
    assert (home()/'data/config.json').read_bytes()==before


def test_remote_cancel_does_not_kill_worker_then_restart(transport):
    scenario,calls,_=transport;scenario[0]='wait';stop=threading.Event()
    def notify(e,d):
        if e=='info' and d['message']=='fixture waiting':stop.set()
    result=uac.request('scan',{'name':'demo','profile':{'roots':[str(home().parent)],'engine':'native'}},stop,notify)
    assert result['status']=='cancelled'
    scenario[0]='events';assert uac.request('system-check',{'tool':'drivers'})['fixture']
    assert len(calls)==2


def test_user_declines_no_worker_no_scan(transport,monkeypatch):
    import dgm.scanner as scanner
    calls=[]
    def no(*args):calls.append(args);raise uac.ElevationCancelled('Đã hủy cấp quyền Admin; tác vụ chưa chạy.')
    monkeypatch.setattr(uac,'launch',no);monkeypatch.setattr(scanner,'is_admin',lambda:False)
    result=scanner.scan('demo',{'roots':[str(home().parent)],'engine':'wiztree','mft':True,'strict_browsers':False})
    assert result['before_start'] and result['status']=='cancelled' and result['scan_id'] is None
    assert len(calls)==1 and not list((home()/'data/uac').iterdir())
    assert not (home()/'data/worker.lock').exists()


def test_cancel_before_uac_does_not_launch(transport):
    scenario,calls,_=transport;stop=threading.Event();stop.set()
    with pytest.raises(uac.ElevationCancelled):uac.request('system-check',{'tool':'winsxs'},stop)
    assert not calls


def test_headless_never_opens_uac(transport,monkeypatch):
    scenario,calls,_=transport;monkeypatch.setattr(uac,'interactive',lambda:False)
    with pytest.raises(PermissionError,match='Phiên chạy nền'):uac.request('system-check',{'tool':'winsxs'})
    assert not calls


def test_invalid_actions_never_launch(transport):
    scenario,calls,_=transport
    for action,payload in [('shell',{'command':'bad'}),('system-check',{'tool':'powershell'}),('system-check',{'tool':'winsxs','argv':['bad']}),('background-control',{'kind':'service','action':'arbitrary'})]:
        with pytest.raises(ValueError):uac.request(action,payload)
    assert not calls


def test_worker_error_and_crash_no_automatic_retry(transport):
    scenario,calls,_=transport;scenario[0]='error'
    with pytest.raises(RuntimeError,match='fixture error'):uac.request('system-check',{'tool':'winsxs'})
    assert len(calls)==1
    scenario[0]='crash'
    with pytest.raises(RuntimeError,match='không có kết quả'):uac.request('system-check',{'tool':'drivers'})
    assert len(calls)==2


def test_ticket_tamper_and_replay_rejected(transport,monkeypatch):
    scenario,calls,_=transport;original=uac.launch
    def tamper(ticket,checksum):
        path=uac.directory(ticket)/'request.json';value=json.loads(path.read_bytes());value['payload']['tool']='drivers';uac.atomic(path,value)
        return original(ticket,checksum)
    monkeypatch.setattr(uac,'launch',tamper)
    with pytest.raises(RuntimeError,match='không có kết quả'):uac.request('system-check',{'tool':'winsxs'})
    assert len(calls)==1


def test_prompt_lock_blocks_second_request(transport):
    scenario,calls,_=transport;token=uuid.uuid4().hex;uac.directory(token)
    with uac.PromptLock(token):
        with pytest.raises(RuntimeError,match='Đang có tác vụ'):uac.request('system-check',{'tool':'drivers'})
    assert not calls


def test_cleanup_confirmation_and_hash_checked_before_any_delete(isolated,monkeypatch):
    from dgm import cleanup
    from dgm.store import Store
    import time
    root=isolated/'fixture';root.mkdir();file=root/'cache';file.write_bytes(b'keep')
    monkeypatch.setattr(cleanup,'rules',lambda:{'windows-temp':{'roots':[str(root)],'days':7}})
    monkeypatch.setattr(cleanup,'is_admin',lambda:True)
    plan_id='a'*12;p={'id':plan_id,'expires':time.time()+100,'confirmation':'XOA '+plan_id,'entries':[], 'update_done':False}
    s=Store();s.db.execute('INSERT INTO plans VALUES(?,?,?,?,?)',(plan_id,'now','pending',json.dumps(p),''));s.db.commit();s.close()
    with pytest.raises(ValueError,match='Cần xác nhận'):cleanup.apply(plan_id,'yes')
    with pytest.raises(ValueError,match='đã thay đổi'):cleanup.apply(plan_id,p['confirmation'],expected_digest='f'*64)
    assert file.read_bytes()==b'keep'


def test_cli_uac_no_exit_130_and_never_retry(isolated,monkeypatch):
    from dgm.cli import app
    import dgm.diagnostics as diagnostics
    calls=[];monkeypatch.setattr(diagnostics,'is_admin',lambda:False)
    def no(*args,**kwargs):calls.append(args);raise uac.ElevationCancelled('Đã hủy cấp quyền; tác vụ chưa chạy.')
    monkeypatch.setattr(uac,'request',no)
    result=CliRunner().invoke(app,['system-check','winsxs'])
    assert result.exit_code==130 and 'Đã hủy cấp quyền' in result.output and len(calls)==1


def test_scheduled_mft_refuses_uac_instead_of_prompt(isolated,monkeypatch):
    import dgm.scanner as scanner
    monkeypatch.setattr(scanner,'is_admin',lambda:False)
    monkeypatch.setattr(uac,'request',lambda *a,**k:pytest.fail('No UAC from schedule'))
    with pytest.raises(PermissionError,match='chạy nền'):
        scanner.scan('demo',{'roots':[str(isolated)],'engine':'wiztree','mft':True,'strict_browsers':False},allow_uac=False)


def test_native_shell_launch_uses_runas_private_runtime_and_no_console(isolated,monkeypatch):
    from win32com.shell import shell
    calls=[]
    class Handle:
        def Close(self):pass
    monkeypatch.setattr(shell,'ShellExecuteEx',lambda **kw:calls.append(kw) or {'hProcess':Handle()})
    process=uac.launch('a'*32,'b'*64);process.close()
    args=calls[0];assert args['lpVerb']=='runas' and args['nShow']==0
    assert args['fMask']&0x100 and args['fMask']&0x40
    assert args['lpFile']==str(Path(sys.prefix)/'Scripts/pythonw.exe')
    assert '-I -m dgm.elevation' in args['lpParameters'] and '--ticket' in args['lpParameters']


def test_shell_uac_decline_1223_maps_to_cancellation(isolated,monkeypatch):
    from win32com.shell import shell
    import pywintypes
    def no(**kwargs):raise pywintypes.error(1223,'ShellExecuteEx','The operation was canceled by the user.')
    monkeypatch.setattr(shell,'ShellExecuteEx',no)
    with pytest.raises(uac.ElevationCancelled,match='chưa chạy'):uac.launch('a'*32,'b'*64)


def test_ipc_atomic_write_does_not_follow_predictable_temp_hardlink(isolated):
    target=isolated/'keep.txt';target.write_text('keep',encoding='utf-8')
    path=isolated/'result.json';os.link(target,path.with_suffix('.json.tmp'))
    uac.atomic(path,{'ok':True})
    assert target.read_text(encoding='utf-8')=='keep' and json.loads(path.read_bytes())=={'ok':True}


def test_ipc_events_refuses_preexisting_hardlink(isolated):
    target=isolated/'keep.txt';target.write_text('keep',encoding='utf-8')
    os.link(target,isolated/'events.jsonl')
    with pytest.raises(FileExistsError):uac.Events(isolated,'a'*32)
    assert target.read_text(encoding='utf-8')=='keep'


def test_ipc_pins_prevent_directory_swap(isolated):
    token=uuid.uuid4().hex;folder=uac.directory(token);uac.private_directory(folder)
    with uac.DirectoryPins(folder) as pins:
        assert len(pins.identity)==3
        with pytest.raises(PermissionError):folder.rename(folder.with_name('replacement'))


def test_worker_replay_never_executes_again(transport,monkeypatch):
    scenario,calls,processes=transport;original=uac.launch;folder_seen=[]
    def launch_twice(ticket,checksum):
        process=original(ticket,checksum)
        until=time.monotonic()+5
        while process.poll() is None and time.monotonic()<until:time.sleep(.01)
        assert process.poll()==0
        folder=uac.directory(ticket);folder_seen.append(folder)
        second=original(ticket,checksum)
        until=time.monotonic()+5
        while second.poll() is None and time.monotonic()<until:time.sleep(.01)
        assert second.poll()!=0;second.close()
        text=(folder/'events.jsonl').read_text(encoding='utf-8')
        assert text.count('fixture-worker')==1
        return process
    monkeypatch.setattr(uac,'launch',launch_twice)
    assert uac.request('system-check',{'tool':'winsxs'})['fixture']
    assert not folder_seen[0].exists()
