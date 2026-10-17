"""Cheap, scoped measurements of the application's own data and log files."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import os
import time

from .config import home


def _stamp():
    return datetime.now(timezone.utc).isoformat()


def _files(operation):
    root = home()
    databases = {
        "scan": ("history.sqlite",),
        "comparison": ("comparisons.sqlite","normalization.sqlite"),
        "benchmark": ("history.sqlite", "comparisons.sqlite","normalization.sqlite"),
        "normalization":("normalization.sqlite",),
    }[operation]
    data = {}
    from .history_backend import database_path
    for database in databases:
        for suffix in ("", "-wal", "-shm"):
            path = database_path(database,create=False).with_name(database+suffix)
            try:
                data[str(path.relative_to(root)).replace('\\','/')] = path.stat().st_size
            except FileNotFoundError:
                data[str(path.relative_to(root)).replace('\\','/')] = 0
    for path in (root/'data/history').rglob('*'):
        if path.is_file():data[str(path.relative_to(root)).replace('\\','/')]=path.stat().st_size
    if operation in ('scan','benchmark'):
        for path in (root/'data/exports/archive').glob('*.csv.gz'):
            try:data[str(path.relative_to(root))]=path.stat().st_size
            except FileNotFoundError:pass

    logs = {}
    log_root = root / "logs"
    for pattern in ("app.log*", "debug.log*", "audit.jsonl*"):
        for path in log_root.glob(pattern):
            if path.is_file():
                try:
                    logs[f"logs/{path.name}"] = path.stat().st_size
                except FileNotFoundError:
                    # A rotated log may disappear between glob and stat.
                    continue
    return data, logs


def _snapshot(operation):
    data, logs = _files(operation)
    return {"sampled_at": _stamp(), "data_files": data, "log_files": logs,'app_footprint':app_footprint()}


def app_footprint(max_seconds=1,max_files=10000):
    """Measure the self-excluded app separately; never include it in scan totals."""
    root=home();stack=[root/'data',root/'logs'];groups={};count=0;errors=[];started=time.monotonic();complete=True
    while stack:
        if count>=max_files or time.monotonic()-started>max_seconds:complete=False;break
        folder=stack.pop()
        try:
            if folder.is_symlink() or getattr(folder.lstat(),'st_file_attributes',0)&0x400:complete=False;continue
            with os.scandir(folder) as entries:
                for entry in entries:
                    if count>=max_files or time.monotonic()-started>max_seconds:complete=False;break
                    st=entry.stat(follow_symlinks=False)
                    if entry.is_symlink() or getattr(st,'st_file_attributes',0)&0x400:complete=False;continue
                    if entry.is_dir(follow_symlinks=False):stack.append(Path(entry.path));continue
                    count+=1;parts=Path(entry.path).relative_to(root).parts
                    group='logs' if parts[0]=='logs' else 'update_backups' if parts[1]=='updates' and 'backup' in parts else 'updates' if parts[1]=='updates' else 'history' if parts[1]=='history' else 'temporary_indexes' if parts[1]=='tmp' else 'cleanup_plans' if parts[1]=='cleanup' else 'legacy_or_other'
                    groups[group]=groups.get(group,0)+st.st_size
        except OSError as exc:
            complete=False
            if len(errors)<10:errors.append(str(exc))
    return {'total_bytes':sum(groups.values()),'groups':groups,'complete':complete,'files_measured':count,'errors':errors,
            'self_excluded':True,'note':'Đo riêng data/log app; backup là thành phần của tổng, không cộng lại vào Size cây quét.'}


def begin(operation):
    if operation not in ("scan", "comparison", "benchmark","normalization"):
        raise ValueError(f"Unsupported storage measurement: {operation}")
    return {"operation": operation, "before": _snapshot(operation)}


def _section(before, after):
    names = sorted(set(before) | set(after))
    files = {
        name: {
            "before_bytes": before.get(name, 0),
            "after_bytes": after.get(name, 0),
            "delta_bytes": after.get(name, 0) - before.get(name, 0),
        }
        for name in names
    }
    return {
        "before_bytes": sum(before.values()),
        "after_bytes": sum(after.values()),
        "delta_bytes": sum(after.values()) - sum(before.values()),
        "files": files,
    }


def finish(meter):
    after = _snapshot(meter["operation"])
    before = meter["before"]
    return {
        "operation": meter["operation"],
        "sampled_before": before["sampled_at"],
        "sampled_after": after["sampled_at"],
        "data": _section(before["data_files"], after["data_files"]),
        "logs": _section(before["log_files"], after["log_files"]),
        'app_footprint':{'before':before.get('app_footprint'),'after':after['app_footprint'],
                         'delta_bytes':after['app_footprint']['total_bytes']-before['app_footprint']['total_bytes'] if before.get('app_footprint',{}).get('complete') and after['app_footprint']['complete'] else None},
        "note": (
            "Đo kích thước file (st_size) của dữ liệu riêng Disk Growth Monitor (SQLite và WAL/SHM) cùng log app/audit đã xoay vòng; "
            "không phải dung lượng file được quét hay dung lượng cấp phát NTFS. Chênh lệch là ước lượng footprint phát sinh trong thao tác; "
            "SQLite có thể tái sử dụng trang trống và log có thể xoay vòng. Mẫu sau được lấy trước khi ghi "
            "chính phần thống kê này vào lịch sử, nên không tự cộng phần metadata đo."
        ),
    }
