"""Shared background controls; UAC only on an explicit interactive mutation."""
from .platform_ops import is_admin


def status():
    from .schedule import task_status
    from .windows_service import status as service_status
    from .scan_control import current
    result={"scan":current()}
    for name,function in (("task",task_status),("service",service_status)):
        try:result[name]=function()
        except Exception as exc:result[name]={"installed":None,"error":str(exc)}
    from .installation import owns_background, verified_background_owner
    from datetime import datetime, timezone
    result['checked_at'] = datetime.now(timezone.utc).isoformat()
    for kind in ('task','service'):
        item = result[kind]
        item['owned'] = verified_background_owner(kind,item)
        item['owner_status'] = 'current' if item['owned'] else 'unknown' if owns_background(kind,item) else 'other'
    try:
        from .schedule import listing
        from .config import load
        rows=listing() if load()['schedules'] else []
        enabled=[row for row in rows if row.get('enabled',True)]
        dates=[row['next_run'] for row in enabled if row.get('next_run')]
        result['schedules']={'enabled_count':len(enabled),'next_run':min(dates) if dates else None}
    except Exception as exc:result['schedules']={'error':str(exc)}
    return result


def open_manager(kind):
    """Only open Windows management UI; never install/start a worker."""
    import subprocess
    from .platform_ops import system_tool
    if kind not in ('task','service'): raise ValueError('Bộ chạy nền không hợp lệ')
    target = system_tool('taskschd.msc' if kind == 'task' else 'services.msc')
    subprocess.Popen([str(system_tool('mmc.exe')),str(target)], creationflags=0x08000000)
    return {'opened':str(target)}


def control(kind,action,*,allow_uac=True,notify=None,stop=None):
    valid={"task":("install","enable","disable","remove","status"),"service":("install","enable","disable","remove","uninstall","start","stop","status")}
    if kind not in valid or action not in valid[kind]:raise ValueError("Thao tác chạy nền không hợp lệ")
    if action != 'status':
        from .installation import verified_background_owner
        state = status()[kind]
        if state.get('installed') is None or state.get('error'): raise RuntimeError('Không xác minh được bộ chạy nền; chưa thay đổi gì.')
        if state.get('installed') and not verified_background_owner(kind,state): raise RuntimeError('Không xác minh được bộ chạy nền thuộc bản app này; hãy mở đúng bản để quản lý.')
    if action!="status" and not is_admin():
        if not allow_uac:raise PermissionError("Thao tác chạy nền cần quyền Administrator.")
        return elevated(kind,action,notify=notify,stop=stop) if notify or stop else elevated(kind,action)
    if kind=="task":
        from .schedule import install_task,remove_task,task_status,enable_task
        functions={"install":install_task,"remove":remove_task,"status":task_status,"enable":lambda:enable_task(True),"disable":lambda:enable_task(False)}
        return functions[action]()
    from .windows_service import control as service_control,status as service_status
    action="uninstall" if action=="remove" else action
    if action=="uninstall" and service_status().get("running"):service_control("disable")
    return service_control(action)


def elevated(kind,action,notify=None,stop=None):
    from .elevation import request
    return request("background-control",{"kind":kind,"action":action},notify=notify,stop=stop)
