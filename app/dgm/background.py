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
    return result


def control(kind,action,*,allow_uac=True,notify=None,stop=None):
    valid={"task":("install","enable","disable","remove","status"),"service":("install","enable","disable","remove","uninstall","start","stop","status")}
    if kind not in valid or action not in valid[kind]:raise ValueError("Thao tác chạy nền không hợp lệ")
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
