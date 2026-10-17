"""Read-only ownership checks for safe upgrade/uninstall of one installation."""
import shlex
from .config import home,normalize


def owned_executables():
    from .runtime import binary_root
    return {normalize(root/name) for root in (home(),binary_root(),home()/'.dgm-app')
            for name in ('DiskGrowthMonitor.exe','dgm.exe','.venv/Scripts/pythonw.exe','.venv/Scripts/python.exe')}


def owns_background(kind,state):
    # Unknown state is not proof of safety. Do not silently remove a worker.
    if state.get('installed') is False:return False
    if state.get('installed') is not True:return True
    root=home()
    if kind=='task':
        if not state.get('actions'):return True
        executables=owned_executables()
        for action in state['actions']:
            if normalize(action.get('path','').strip('"')) in executables:return True
            if action.get('working_directory') and normalize(action['working_directory'])==normalize(root):return True
        return False
    if not state.get('binary_path'):return True
    try:binary=shlex.split(state['binary_path'],posix=False)[0].strip('"')
    except (ValueError,IndexError):return True
    return normalize(binary)==normalize(root/'.venv/tools/service/DiskGrowthMonitorService.exe')


def verified_background_owner(kind,state):
    """Positive proof for mutations. Conservative uninstall ownership is NOT proof."""
    if state.get('installed') is not True or state.get('error'):return False
    root=home()
    if kind=='task':
        actions=state.get('actions') or []
        executables=owned_executables()
        return bool(actions) and all(normalize(a.get('path','').strip('"')) in executables for a in actions)
    if kind!='service' or not state.get('binary_path'):return False
    try:binary=shlex.split(state['binary_path'],posix=False)[0].strip('"')
    except (ValueError,IndexError):return False
    return normalize(binary)==normalize(root/'.venv/tools/service/DiskGrowthMonitorService.exe')


def upgrade_safety():
    """Unlike uninstall, upgrade may retain a verified disabled, idle runner."""
    import os, psutil
    from .background import status
    state=status(); reasons=[]; active=[]
    if state['scan'].get('running'): reasons.append('Có lượt quét đang chạy')
    for kind in ('task','service'):
        item=state[kind]
        if item.get('error') or item.get('installed') is None: reasons.append(kind+': chưa rõ trạng thái'); continue
        if item.get('installed') and owns_background(kind,item):
            if not verified_background_owner(kind,item):reasons.append(kind+': chưa xác minh được chủ sở hữu')
            if item.get('enabled') is not False or item.get('running') is not False: reasons.append(kind+': cần tắt và dừng trước')
            if kind=='service' and item.get('state') != 1: reasons.append('Service chưa ở trạng thái STOPPED')
    targets={value for value in owned_executables() if value.endswith(('/diskgrowthmonitor.exe','/dgm.exe'))}
    for process in psutil.process_iter(['pid','name']):
        if process.pid==os.getpid() or (process.info['name'] or '').casefold() not in ('diskgrowthmonitor.exe','dgm.exe'):continue
        try:
            if normalize(process.exe()) in targets: active.append(process.pid)
        except psutil.NoSuchProcess: pass
        except psutil.AccessDenied: reasons.append('Không xác minh được tiến trình app')
    return {'safe_to_upgrade':not reasons and not active,'reasons':reasons,'running_app_pids':active,'background':state}
