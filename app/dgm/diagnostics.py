from __future__ import annotations
import importlib.metadata
import json
import os
import shutil
import sys
from pathlib import Path
from . import __version__
from .config import home, load
from .platform_ops import is_admin, run, system_tool


def doctor():
    from .history_backend import log_mode,database_path
    ROOT = home()
    cfg = load()
    packages = {}
    for name in ("PySide6-Essentials", "typer", "psutil", "Send2Trash", "APScheduler", "pywin32"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = "missing"
    engines = {}
    for name, path in cfg["engines"].items():
        exists = bool(path) and Path(path).is_file()
        version = ""
        if exists and os.name == "nt":
            try:
                import win32api
                v = win32api.GetFileVersionInfo(path, "\\")
                ms, ls = v["FileVersionMS"], v["FileVersionLS"]
                version = f"{ms >> 16}.{ms & 65535}.{ls >> 16}.{ls & 65535}"
            except Exception:
                pass
        engines[name] = {"path": path, "exists": exists, "version": version, "supported_scan": True}
    disks = {}
    import psutil
    for p in psutil.disk_partitions():
        try:
            d = shutil.disk_usage(p.mountpoint)
            disks[p.mountpoint] = {"free": d.free, "total": d.total, "filesystem": p.fstype}
        except OSError:
            pass
    return {"app_version": __version__, "home": str(ROOT), "python": sys.version, "executable": sys.executable,
            "base_prefix": sys.base_prefix, "venv_prefix": sys.prefix, "isolated": bool(getattr(sys,'frozen',False)) or sys.prefix != sys.base_prefix,
            "admin": is_admin(), "frozen":bool(getattr(sys,'frozen',False)), "packages": packages, "engines": engines, "disks": disks,
            "database_bytes": database_path('history.sqlite',create=False).stat().st_size if database_path('history.sqlite',create=False).exists() else 0,
            'history_format':'jsonl.gz' if log_mode() else 'legacy-sqlite'}


def system_check(tool, *, allow_uac=True, notify=None,stop=None):
    args = {"winsxs": ["Dism.exe", "/Online", "/Cleanup-Image", "/AnalyzeComponentStore"],
            "drivers": ["pnputil.exe", "/enum-drivers"], "shadowstorage": ["vssadmin.exe", "list", "shadowstorage"]}
    if tool not in args:
        raise ValueError("Tool không hợp lệ")
    if not is_admin():
        if not allow_uac:raise PermissionError('Kiểm tra hệ thống cần quyền Admin.')
        from .elevation import request
        return request('system-check',{'tool':tool},notify=notify,stop=stop)
    arg = args[tool]
    result = run([system_tool(arg[0]), *arg[1:]], timeout=7200,maintenance=tool=='winsxs',stop=stop)
    return {"exit": result.returncode, "stdout": result.stdout, "stderr": result.stderr,'cancel_requested':getattr(result,'interrupted',False)}

