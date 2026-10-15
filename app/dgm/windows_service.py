"""Optional WinSW wrapper; installation is explicit, never during bootstrap."""
from pathlib import Path
from xml.etree import ElementTree as ET
import hashlib
import json
import urllib.request
from .config import ROOT, home
from .platform_ops import is_admin, run

SERVICE_ID = "DiskGrowthMonitor"
VERSION = "v2.12.0"


def status():
    import os
    if os.name != "nt":
        return {"installed": False, "running": False}
    import pywintypes, win32serviceutil, win32service
    try:
        state = win32serviceutil.QueryServiceStatus(SERVICE_ID)[1]
        manager = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_CONNECT)
        try:
            service = win32service.OpenService(manager, SERVICE_ID, win32service.SERVICE_QUERY_CONFIG)
            try:
                configuration=win32service.QueryServiceConfig(service)
                startup_type = configuration[1]
            finally:
                win32service.CloseServiceHandle(service)
        finally:
            win32service.CloseServiceHandle(manager)
        return {"installed": True, "running": state == win32service.SERVICE_RUNNING, "state": state,
                'binary_path':configuration[3],
                "enabled": startup_type != win32service.SERVICE_DISABLED, "startup_type": startup_type}
    except pywintypes.error as exc:
        if exc.winerror == 1060:
            return {"installed": False, "running": False}
        raise RuntimeError(f"Không đọc được trạng thái service: {exc}") from exc


def service_path():
    return home() / ".venv/tools/service/DiskGrowthMonitorService.exe"


def prepare():
    ROOT = home()
    exe = service_path()
    exe.parent.mkdir(parents=True, exist_ok=True)
    if not exe.exists():
        url = f"https://github.com/winsw/winsw/releases/download/{VERSION}/WinSW-x64.exe"
        urllib.request.urlretrieve(url, exe)
        (exe.parent / "source.json").write_text(json.dumps({"source": url, "version": VERSION, "sha256": hashlib.sha256(exe.read_bytes()).hexdigest()}), encoding="utf-8")
        urllib.request.urlretrieve(f"https://raw.githubusercontent.com/winsw/winsw/{VERSION}/LICENSE.txt", exe.parent / "LICENSE.txt")
    root = ET.Element("service")
    from .runtime import background_command
    import subprocess
    executable,arguments=background_command('schedule','agent')
    for name, value in {"id": SERVICE_ID, "name": "Disk Growth Monitor", "description": "Local scans and cleanup proposals; never unattended confirmation",
                        "executable": str(executable),
                        "arguments": subprocess.list2cmdline(arguments), "workingdirectory": str(ROOT),
                        "startmode": "Automatic", "stoptimeout": "30 sec"}.items():
        ET.SubElement(root, name).text = value
    ET.SubElement(root, "logpath").text = str(home() / "logs")
    log = ET.SubElement(root, "log", {"mode": "roll-by-size"})
    ET.SubElement(log, "sizeThreshold").text = "5120"
    ET.SubElement(log, "keepFiles").text = "3"
    ET.SubElement(root, "env", {"name": "DGM_HOME", "value": str(ROOT)})
    # Use the interactive user's profile paths even though the wrapper is a service.
    import os
    for name in ("USERPROFILE", "LOCALAPPDATA", "APPDATA"):
        if os.environ.get(name):
            ET.SubElement(root, "env", {"name": name, "value": os.environ[name]})
    for name in ("TEMP", "TMP", "SQLITE_TMPDIR"):
        ET.SubElement(root, "env", {"name": name, "value": str(home() / "data/tmp")})
    ET.ElementTree(root).write(exe.with_suffix(".xml"), encoding="utf-8", xml_declaration=True)
    return exe


def control(action):
    if action not in ("install", "uninstall", "start", "stop", "status", "enable", "disable"):
        raise ValueError("Service action không hợp lệ")
    if action == "status":
        return status()
    if action != "status" and not is_admin():
        raise PermissionError("Quản lý Windows Service cần Administrator")
    if action in ("install", "start", "enable"):
        from .schedule import task_status
        if task_status().get("installed"):
            raise RuntimeError("Đã đăng ký Task Scheduler; gỡ task trước khi chuyển sang Windows Service.")
    if action in ("stop", "disable"):
        (home() / "data/agent.stop").touch()
    exe = prepare() if action == "install" else service_path()
    if not exe.exists():
        return {"installed": False, "note": "Chưa tải/đăng ký service"}
    if action in ("enable", "disable"):
        import win32service
        manager = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_CONNECT)
        try:
            service = win32service.OpenService(manager, SERVICE_ID, win32service.SERVICE_CHANGE_CONFIG)
            try:
                win32service.ChangeServiceConfig(service, win32service.SERVICE_NO_CHANGE,
                    win32service.SERVICE_AUTO_START if action == "enable" else win32service.SERVICE_DISABLED,
                    win32service.SERVICE_NO_CHANGE, None, None, 0, None, None, None, None)
            finally:
                win32service.CloseServiceHandle(service)
        finally:
            win32service.CloseServiceHandle(manager)
        running = status()["running"]
        if (action == "enable" and running) or (action == "disable" and not running):
            return status()
    command = {"enable": "start", "disable": "stop"}.get(action, action)
    result = run([exe, command], timeout=120)
    if result.returncode:
        raise RuntimeError(result.stdout + result.stderr)
    return {"exit": result.returncode, "stdout": result.stdout, "stderr": result.stderr, **status()}
