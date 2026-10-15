"""Shared human-readable scan labels and non-color-only direction indicators."""
from datetime import datetime
from zoneinfo import ZoneInfo

STATUS = {"running": "Đang xử lý", "complete": "Hoàn tất", "partial": "Hoàn tất / chưa xác minh đầy đủ", "cancelled": "Đã dừng", "failed": "Lỗi"}
CHANGES = {"first_scan": "Mốc ban đầu", "grown": "Tăng", "shrunk": "Giảm", "new": "Thêm mới", "missing": "Mất đi / không còn thấy", "same": "Không đổi", "unknown": "Chưa xác định", "modified": "Đã sửa", "replaced": "Bị thay thế", "moved": "Đổi tên / di chuyển", "moved_from": "Đã chuyển đi"}


def size(value):
    if value is None:
        return "—"
    n = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1000:
            return f"{n:,.2f} {unit}"
        n /= 1000
    return f"{n:,.2f} PB"


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
