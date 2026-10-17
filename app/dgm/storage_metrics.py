"""Cheap, scoped measurements of the application's own data and log files."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

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
    return {"sampled_at": _stamp(), "data_files": data, "log_files": logs}


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
        "note": (
            "Đo kích thước file (st_size) của dữ liệu riêng Disk Growth Monitor (SQLite và WAL/SHM) cùng log app/audit đã xoay vòng; "
            "không phải dung lượng file được quét hay dung lượng cấp phát NTFS. Chênh lệch là ước lượng footprint phát sinh trong thao tác; "
            "SQLite có thể tái sử dụng trang trống và log có thể xoay vòng. Mẫu sau được lấy trước khi ghi "
            "chính phần thống kê này vào lịch sử, nên không tự cộng phần metadata đo."
        ),
    }
