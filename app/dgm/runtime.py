"""Command construction shared by source and frozen background workers."""
import subprocess
import sys
import json
import os
from pathlib import Path
from .config import home


def binary_root():
    return Path(sys.executable).absolute().parent if getattr(sys,'frozen',False) else home()


def managed_executable():
    root=home();target=root/'.dgm-app'
    if target.resolve()!=target.absolute():return None
    try:
        marker=json.loads((target/'installed-app.json').read_text(encoding='utf-8-sig'))
        link=json.loads((target/'data-home.json').read_text(encoding='utf-8-sig'))
        if marker.get('app')!='DiskGrowthMonitor' or Path(link['home']).resolve()!=root.resolve():return None
    except (OSError,ValueError,KeyError,TypeError):return None
    exe=target/'dgm.exe'
    return exe if exe.is_file() and exe.resolve()==exe.absolute() else None


def forward_managed():
    if getattr(sys,'frozen',False) or os.environ.get('DGM_SOURCE')=='1' or any(arg.startswith('_app-update-') for arg in sys.argv):return
    # An editable source checkout is authoritative, including after UAC strips
    # environment variables. Never send its new main commands to old binaries.
    if home()==Path(__file__).resolve().parents[2] and (home()/'pyproject.toml').is_file() and (home()/'app/dgm/production_core.py').is_file():return
    # UAC can rebuild the environment. The explicit preview command must stay
    # on source even if DGM_SOURCE/DGM_HOME were not inherited by pythonw.
    arguments=iter(sys.argv[1:])
    for argument in arguments:
        if argument=='--home':next(arguments,None);continue
        if argument.startswith('--home=') or argument=='--debug':continue
        if argument=='fast':return
        break
    exe=managed_executable()
    if exe:
        if 'gui' in sys.argv[1:]:exe=exe.with_name('DiskGrowthMonitor.exe')
        flags=getattr(subprocess,'CREATE_NO_WINDOW',0) if Path(sys.executable).name.casefold()=='pythonw.exe' else 0
        raise SystemExit(subprocess.call([str(exe),'--home',str(home()),*sys.argv[1:]],creationflags=flags))


def background_command(*arguments):
    root=home()
    if getattr(sys,'frozen',False):
        return binary_root()/'DiskGrowthMonitor.exe',['--home',str(root),*arguments]
    return root/'.venv/Scripts/pythonw.exe',['-m','dgm','--home',str(root),*arguments]


def command_line(executable,arguments):
    return subprocess.list2cmdline([str(executable),*arguments])
