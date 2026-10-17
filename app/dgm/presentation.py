"""Shared human-readable scan labels and non-color-only direction indicators."""
from datetime import datetime
from zoneinfo import ZoneInfo

STATUS = {"running": "Đang xử lý", "complete": "Hoàn tất", "partial": "Hoàn tất • có giới hạn", "cancelled": "Đã dừng", "failed": "Lỗi"}
CHANGES = {"first_scan": "Mốc ban đầu", "grown": "Tăng", "shrunk": "Giảm", "new": "Thêm mới", "missing": "Mất đi / không còn thấy", "same": "Không đổi", "unknown": "Chưa xác định", "modified": "Đã sửa", "replaced": "Bị thay thế", "moved": "Đổi tên / di chuyển", "moved_from": "Đã chuyển đi"}
_binary=False


def set_units(binary=False):
    global _binary
    _binary=bool(binary)


def size(value):
    if value is None:
        return "—"
    n = float(value)
    base=1024 if _binary else 1000
    for unit in (("B","KiB","MiB","GiB","TiB") if _binary else ("B", "KB", "MB", "GB", "TB")):
        if abs(n) < base:
            return f"{n:,.2f} {unit}"
        n /= base
    return f"{n:,.2f} {'PiB' if _binary else 'PB'}"


def local_time(value):
    return datetime.fromisoformat(value).astimezone(ZoneInfo("Asia/Ho_Chi_Minh")).strftime("%d/%m/%Y %H:%M:%S UTC+07") if value else "Chưa có"


def scan_name(row):
    return f"{row['profile']} — Lần quét {row.get('ordinal', row['id']):02d}"


def scan_label(row):
    import json
    summary = json.loads(row.get("summary") or "{}")
    return f"{scan_name(row)} • {local_time(row['started'])} • {row['engine']}" + (" • phục hồi" if summary.get("recovered") else "")


def change_text(delta):
    if delta is None:
        return "—"
    if delta > 0:
        return "↑ +" + size(delta)
    if delta < 0:
        return "↓ −" + size(abs(delta))
    return "→ 0 B"


def change_color(delta):
    return "#667085" if delta is None or delta == 0 else "#138454" if delta > 0 else "#d34242"


def _signed_size(value):
    if value is None:
        return "—"
    return ("+" if value > 0 else "−" if value < 0 else "") + size(abs(value))


def storage_delta(storage):
    if not storage:
        return "—"
    data = storage.get("data", {}).get("delta_bytes")
    logs = storage.get("logs", {}).get("delta_bytes")
    if data is None and logs is None:
        return "—"
    return f"{_signed_size(data)} / {_signed_size(logs)}"


def storage_tooltip(storage):
    if not storage:
        return "Bản ghi cũ chưa có số liệu dung lượng data/log."
    lines = []
    for label, key in (("Data app (SQLite/WAL/SHM)", "data"), ("Log app/audit", "logs")):
        section = storage.get(key, {})
        if not section:
            continue
        lines.append(
            f"{label}: {size(section.get('before_bytes'))} → {size(section.get('after_bytes'))} "
            f"(Δ {_signed_size(section.get('delta_bytes'))})"
        )
    if storage.get("note"):
        lines.append(storage["note"])
    return "\n".join(lines)
