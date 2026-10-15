"""Read-only ownership checks for safe upgrade/uninstall of one installation."""
import shlex
from .config import home,normalize


def owns_background(kind,state):
    # Unknown state is not proof of safety. Do not silently remove a worker.
    if state.get('installed') is False:return False
    if state.get('installed') is not True:return True
    root=home()
    if kind=='task':
        if not state.get('actions'):return True
        executables={normalize(root/name) for name in ('DiskGrowthMonitor.exe','dgm.exe','.venv/Scripts/pythonw.exe','.venv/Scripts/python.exe')}
        for action in state['actions']:
            if normalize(action.get('path','').strip('"')) in executables:return True
            if action.get('working_directory') and normalize(action['working_directory'])==normalize(root):return True
        return False
    if not state.get('binary_path'):return True
    try:binary=shlex.split(state['binary_path'],posix=False)[0].strip('"')
    except (ValueError,IndexError):return True
    return normalize(binary)==normalize(root/'.venv/tools/service/DiskGrowthMonitorService.exe')
