import sys
from pathlib import Path
import pytest
from dgm import startup_admin as startup


@pytest.mark.parametrize('args,frozen,wanted',[
    (['gui'],False,True),(['--home','D:/gui','doctor'],False,False),
    (['--home','D:/App With Spaces','--debug','gui','--tray'],False,True),
    (['--help'],False,False),(['schedule','agent'],True,False),([],True,True),([],False,False),
    (['--home','D:/preview','fast','gui'],False,True),(['fast','history'],False,False)])
def test_gui_detection_does_not_elevate_headless_or_confuse_home(args,frozen,wanted):
    assert startup.gui_requested(args,frozen=frozen)==wanted


def test_source_gui_elevates_before_any_data_access(isolated,monkeypatch):
    monkeypatch.setattr(startup.ctypes.windll.shell32,'IsUserAnAdmin',lambda:False)
    monkeypatch.setattr(sys,'frozen',False,raising=False)
    calls=[];monkeypatch.setattr(startup,'shell_launch',lambda *a:calls.append(a))
    root=isolated/'App With Spaces';monkeypatch.setenv('DGM_HOME',str(root))
    assert startup.ensure_gui_admin(['gui','--tray'])
    exe,args,cwd=calls[0]
    assert exe==Path(sys.prefix)/'Scripts/pythonw.exe' and cwd==root
    assert args==['-m','dgm','--home',str(root),'gui','--tray']
    assert not root.exists()


def test_admin_session_never_prompts_again(monkeypatch):
    monkeypatch.setattr(startup.ctypes.windll.shell32,'IsUserAnAdmin',lambda:True)
    monkeypatch.setattr(startup,'shell_launch',lambda *a:pytest.fail('Already elevated'))
    assert not startup.ensure_gui_admin(['gui'])


def test_frozen_cli_gui_uses_windowed_admin_exe(isolated,monkeypatch):
    monkeypatch.setattr(startup.ctypes.windll.shell32,'IsUserAnAdmin',lambda:False)
    monkeypatch.setattr(sys,'frozen',True,raising=False)
    monkeypatch.setattr(sys,'executable',str(isolated/'App/dgm.exe'))
    calls=[];monkeypatch.setattr(startup,'shell_launch',lambda *a:calls.append(a))
    assert startup.ensure_gui_admin(['--home',str(isolated/'Data'),'gui'])
    assert calls[0][0]==isolated/'App/DiskGrowthMonitor.exe'


def test_cancel_uac_does_not_open_data_or_retry(isolated,monkeypatch):
    monkeypatch.setattr(startup.ctypes.windll.shell32,'IsUserAnAdmin',lambda:False)
    monkeypatch.setattr(sys,'argv',['dgm','gui'])
    calls=[]
    def deny(*a):calls.append(1);raise startup.StartupCancelled('cancel')
    monkeypatch.setattr(startup,'shell_launch',deny)
    with pytest.raises(SystemExit) as exc:startup.preflight()
    assert exc.value.code==130 and calls==[1]


def test_gui_build_requires_admin_but_console_does_not():
    spec=(Path(__file__).resolve().parents[1]/'packaging/DiskGrowthMonitor.spec').read_text()
    gui=next(line for line in spec.splitlines() if line.startswith('gui=EXE'))
    cli=next(line for line in spec.splitlines() if line.startswith('cli=EXE'))
    assert 'uac_admin=True' in gui and 'uac_admin=True' not in cli


def test_preview_stays_on_source_when_uac_drops_environment(monkeypatch):
    from dgm import runtime
    monkeypatch.delenv('DGM_SOURCE',raising=False)
    monkeypatch.setattr(sys,'frozen',False,raising=False)
    monkeypatch.setattr(sys,'argv',['dgm','--home','D:/preview','fast','gui'])
    monkeypatch.setattr(runtime,'managed_executable',lambda:pytest.fail('Preview must not forward to old binary'))
    assert runtime.forward_managed() is None
