"""GUI startup elevation BEFORE configuration/log/data writes.

The GUI EXE also has a requireAdministrator manifest. This bootstrap covers the
source launcher and `dgm.exe gui`, while headless CLI/background calls retain
their explicit per-operation elevation rules.
"""
from __future__ import annotations
import ctypes
import os
from pathlib import Path
import subprocess
import sys


class StartupCancelled(Exception):pass


def gui_requested(arguments,*,frozen=False):
    args=iter(arguments)
    for arg in args:
        if arg=='--home':next(args,None);continue
        if arg.startswith('--home=') or arg=='--debug':continue
        if arg.startswith('-'):return False
        if arg == 'fast':
            return next(args, None) == 'gui'
        return arg=='gui'
    return frozen


def shell_launch(executable,arguments,directory):
    import pythoncom
    from win32com.shell import shell,shellcon
    pythoncom.CoInitialize()
    try:
        try:
            shell.ShellExecuteEx(fMask=getattr(shellcon,'SEE_MASK_NOASYNC',0x100),lpVerb='runas',
                lpFile=str(executable),lpParameters=subprocess.list2cmdline(arguments),lpDirectory=str(directory),nShow=1)
        except Exception as exc:
            if getattr(exc,'winerror',None)==1223:raise StartupCancelled('Đã hủy mở app vì chưa cấp quyền Admin.') from exc
            raise
    finally:pythoncom.CoUninitialize()


def ensure_gui_admin(arguments=None):
    args=list(sys.argv[1:] if arguments is None else arguments)
    frozen=bool(getattr(sys,'frozen',False))
    if os.name!='nt' or not gui_requested(args,frozen=frozen):return False
    if ctypes.windll.shell32.IsUserAnAdmin():return False
    # No home()/config imports here: creating data/tmp in Program Files before
    # UAC was the startup failure this bootstrap is intended to prevent.
    root=Path(os.environ.get('DGM_HOME',Path(__file__).resolve().parents[2])).absolute()
    for index,arg in enumerate(args):
        if arg=='--home' and index+1<len(args):root=Path(args[index+1]).absolute();break
        if arg.startswith('--home='):root=Path(arg.split('=',1)[1]).absolute();break
    if not any(a=='--home' or a.startswith('--home=') for a in args):args=['--home',str(root),*args]
    if not any(a=='gui' for a in args):args.append('gui')
    if frozen:
        executable=Path(sys.executable).with_name('DiskGrowthMonitor.exe')
    else:
        executable=Path(sys.prefix)/'Scripts/pythonw.exe'
        args=['-m','dgm',*args]
    shell_launch(executable,args,root)
    return True


def preflight():
    try:
        if ensure_gui_admin():raise SystemExit(0)
    except StartupCancelled as exc:
        if sys.stderr:print(str(exc),file=sys.stderr)
        raise SystemExit(130) from exc
    except Exception as exc:
        if os.name=='nt':ctypes.windll.user32.MessageBoxW(None,str(exc),'Không mở được Disk Growth Monitor',0x10)
        raise SystemExit(1) from exc
