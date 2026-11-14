from __future__ import annotations
import importlib.metadata
import json
import os
import re
import shutil
import sys
from pathlib import Path
from . import __version__,__edition__
from .config import home, load
from .platform_ops import is_admin, run, system_tool


def doctor():
    from .history_backend import log_mode,database_path
    ROOT = home()
    cfg = load()
    packages = {}
    for name in ("PySide6-Essentials", "typer", "psutil", "Send2Trash", "APScheduler", "pywin32","duckdb","numpy"):
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
    from .production_core import enabled
    return {"app_version": __version__, 'edition':__edition__,"home": str(ROOT), "python": sys.version, "executable": sys.executable,
            "base_prefix": sys.base_prefix, "venv_prefix": sys.prefix, "isolated": bool(getattr(sys,'frozen',False)) or sys.prefix != sys.base_prefix,
            "admin": is_admin(), "frozen":bool(getattr(sys,'frozen',False)), "packages": packages, "engines": engines, "disks": disks,
            "database_bytes": database_path('history.sqlite',create=False).stat().st_size if database_path('history.sqlite',create=False).exists() else 0,
            'history_format':'parquet' if enabled() else 'jsonl.gz' if log_mode() else 'legacy-sqlite'}


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
    return {"tool":tool,"exit": result.returncode, "stdout": result.stdout, "stderr": result.stderr,'cancel_requested':getattr(result,'interrupted',False)}


def diagnostic_rows(tool, text):
    """Extract only recognized fields. Preserve original output separately."""
    keys = {
        'winsxs': {'windows explorer reported size of component store':'Dung lượng Explorer báo',
                   'actual size of component store':'Dung lượng thực',
                   'shared with windows':'Dùng chung với Windows',
                   'backups and disabled features':'Sao lưu / tính năng tắt',
                   'cache and temporary data':'Cache và dữ liệu tạm',
                   'date of last cleanup':'Lần dọn cuối','number of reclaimable packages':'Gói có thể thu hồi',
                   'component store cleanup recommended':'Windows khuyên dọn'},
        'drivers': {'published name':'Tên INF','original name':'INF gốc','provider name':'Nhà cung cấp',
                    'class name':'Nhóm thiết bị','driver version':'Phiên bản','signer name':'Chữ ký',
                    'attributes':'Thuộc tính'},
        'shadowstorage': {'for volume':'Phân vùng','shadow copy storage volume':'Ổ chứa shadow',
                          'used shadow copy storage space':'Đã dùng','allocated shadow copy storage space':'Đã cấp',
                          'maximum shadow copy storage space':'Tối đa'},
    }.get(tool,{})
    rows = []
    group = 0
    for line in text.splitlines():
        match=re.match(r'^\s*([^:]+)\s*:\s*(.+?)\s*$',line)
        if not match:continue
        label,value=match.groups();name=keys.get(label.strip().casefold())
        if not name:continue
        if name in ('Tên INF','Phân vùng'):group+=1
        rows.append((str(group) if group else '—',name,value))
    return rows

