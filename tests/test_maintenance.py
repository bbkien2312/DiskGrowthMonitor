"""Safe-process/fixture tests only: never run Windows maintenance or real cleanup."""
import subprocess
import sys
import threading
from types import SimpleNamespace
import pytest
from dgm import cleanup,diagnostics
from dgm.platform_ops import run,ProcessCancelled


def test_maintenance_timeout_waits_for_normal_completion(isolated,caplog):
    result=run([sys.executable,'-c',"import time;time.sleep(.25);print('completed')"],timeout=.03,maintenance=True)
    assert result.returncode==0 and 'completed' in result.stdout and result.interrupted
    assert 'not terminating' in caplog.text


def test_maintenance_stop_waits_but_ordinary_stop_still_cancels(isolated):
    stop=threading.Event();timer=threading.Timer(.15,stop.set);timer.start()
    try:
        result=run([sys.executable,'-c',"import time;time.sleep(.4);print('safe completion')"],stop=stop,maintenance=True)
    finally:timer.cancel();timer.join()
    assert result.returncode==0 and result.interrupted and 'safe completion' in result.stdout
    with pytest.raises(ProcessCancelled):run([sys.executable,'-c',"raise Exception('must not start')"],stop=stop)


def test_maintenance_keyboard_interrupt_does_not_kill(isolated,monkeypatch):
    original=subprocess.Popen.communicate;calls=[]
    def interrupted(process,*args,**kwargs):
        if not calls:calls.append(True);raise KeyboardInterrupt()
        return original(process,*args,**kwargs)
    monkeypatch.setattr(subprocess.Popen,'communicate',interrupted)
    result=run([sys.executable,'-c',"print('completed')"],maintenance=True)
    assert result.returncode==0 and result.interrupted and 'completed' in result.stdout


def test_cleanup_stops_after_current_tool_without_touching_next(isolated,monkeypatch):
    root=isolated/'only-fixture';root.mkdir();stop=threading.Event();calls=[]
    monkeypatch.setattr(cleanup,'rules',lambda:{
        'test-a':{'roots':[str(root/'a')],'days':0,'risk':'fixture','tool':'dism'},
        'test-b':{'roots':[str(root/'b')],'days':0,'risk':'fixture','tool':'dism'}})
    monkeypatch.setattr(cleanup,'is_admin',lambda:True)
    def tool(entry,stop=None):
        calls.append(entry['rule']);stop.set();return {'exit':0,'cancel_requested':True}
    monkeypatch.setattr(cleanup,'tool_apply',tool)
    plan=cleanup.plan(['test-a','test-b'])
    result=cleanup.apply(plan['id'],plan['confirmation'],stop=stop)
    assert calls==['test-a'] and result['cancelled'] and result['not_processed']==1
    assert cleanup.get_plan(plan['id'])['status']=='cancelled'
    with pytest.raises(ValueError,match='đã dùng'):cleanup.apply(plan['id'],plan['confirmation'])


def test_cancelled_preview_creates_no_plan(isolated,monkeypatch):
    from dgm.elevation import ElevationCancelled
    from dgm.store import Store
    stop=threading.Event();stop.set()
    with pytest.raises(ElevationCancelled):cleanup.plan(['user-temp'],stop=stop)
    store=Store()
    try:assert store.db.execute('SELECT count(*) FROM plans').fetchone()[0]==0
    finally:store.close()


def test_only_dism_system_analysis_uses_maintenance_wait(isolated,monkeypatch):
    calls=[];stop=threading.Event()
    monkeypatch.setattr(diagnostics,'is_admin',lambda:True)
    monkeypatch.setattr(diagnostics,'run',lambda argv,**kw:calls.append(kw) or SimpleNamespace(returncode=0,stdout='',stderr='',interrupted=True))
    result=diagnostics.system_check('winsxs',stop=stop)
    diagnostics.system_check('drivers',stop=stop)
    assert result['cancel_requested'] and calls[0]['maintenance'] and not calls[1]['maintenance']
    assert all(item['stop'] is stop for item in calls)
