"""Shared human-readable scan labels and non-color-only direction indicators."""
from datetime import datetime
from zoneinfo import ZoneInfo
from decimal import Decimal, ROUND_HALF_UP
import json

STATUS = {"running": "Đang xử lý", "complete": "Hoàn tất", "partial": "Hoàn tất • có giới hạn", "cancelled": "Đã dừng", "failed": "Lỗi"}
CHANGES = {"first_scan": "Mốc ban đầu", "grown": "Tăng", "shrunk": "Giảm", "new": "Thêm mới", "missing": "Mất đi / không còn thấy", "same": "Không đổi", "unknown": "Chưa xác định", "modified": "Đã sửa", "replaced": "Bị thay thế", "moved": "Đổi tên / di chuyển", "moved_from": "Đã chuyển đi"}
_binary=False
_size_formatter=None
CHANGES['private']='Dữ liệu cũ • cần mở lại so sánh'
CHANGES['not_scanned']='Không quét ở mốc kia'
CHANGES['partial']='Có phần chưa rõ'


def row_status(row):
    """Status and chronology are independent; never use delta=0 as presence."""
    change=row.get('change','unknown');presence=row.get('presence')
    if presence is None:
        presence='both' if row.get('old_revision') and row.get('new_revision') else 'old' if row.get('old_revision') else 'new'
    tag={'both':'Cũ+Mới','old':'Chỉ Cũ','new':'Chỉ Mới'}.get(presence,'Chưa rõ')
    symbols={'new':'+','missing':'−','grown':'↑','shrunk':'↓','same':'=','modified':'✎','replaced':'↺',
             'moved':'↔','moved_from':'↔','not_scanned':'»','unknown':'?','partial':'≈','first_scan':'●'}
    name=CHANGES.get(change,change)
    if change=='missing':name='Không còn'
    if change=='not_scanned':name='Không quét '+('Cũ' if presence=='new' else 'Mới')
    if change=='unknown':
        sides=[label for side,label in (('old','Cũ'),('new','Mới')) if row.get(side+'_issue')]
        name='Chưa rõ'+(' '+('/'.join(sides)) if sides else '')
    return f"{symbols.get(change,'?')} {name} · {tag}"


def status_colors(change):
    return {'new':('#177245','#e9f7ee'),'missing':('#737d89','#f0f1f3'),
            'grown':('#177245','#eff8f2'),'shrunk':('#ac581c','#fff4e9'),
            'moved':('#65509b','#f1edfa'),'moved_from':('#65509b','#f1edfa'),
            'modified':('#29659b','#edf4fc'),'replaced':('#8b5391','#f8edf8'),
            'unknown':('#916514','#fff6df'),'partial':('#916514','#fff6df'),
            'not_scanned':('#425466','#f1f4f7'),'first_scan':('#29659b','#edf4fc')}.get(change,('#48596a','#f8fafc'))


def snapshot_cell(row,side):
    if side=='old' and row.get('baseline'):return '—'
    presence=row.get('presence')
    issue=row.get(side+'_issue') or ''
    exists=(presence in ('both',side)) if presence else row.get(side+'_revision') is not None
    value=row.get('before' if side=='old' else 'after')
    if not exists:
        if any(tag in issue.split('|') for tag in ('browser','scope')):return 'Không quét'
        if issue:return 'Chưa rõ'
        return 'Không có'
    return size(value) if value is not None else 'Chưa rõ'


def set_units(binary=False):
    global _binary
    _binary=bool(binary)


def set_size_formatter(formatter=None):
    global _size_formatter
    _size_formatter=formatter


def whole(value):
    """Presentation-only, conventional .5 rounds away from zero."""
    return int(Decimal(str(value)).quantize(Decimal('1'),rounding=ROUND_HALF_UP))


def display_json(value):
    """Round diagnostic UI numbers on a copy, never persisted/CLI/export data."""
    def display(item):
        if isinstance(item,float):return whole(item)
        if isinstance(item,dict):return {k:display(v) for k,v in item.items()}
        if isinstance(item,(tuple,list)):return [display(v) for v in item]
        return item
    return json.dumps(display(value),ensure_ascii=False,indent=2,default=str)


def size(value):
    if _size_formatter is not None:return _size_formatter(value,'binary' if _binary else 'decimal')
    if value is None:
        return "—"
    n = Decimal(str(value))
    base=1024 if _binary else 1000
    units=("B","KiB","MiB","GiB","TiB","PiB") if _binary else ("B","KB","MB","GB","TB","PB")
    for index,unit in enumerate(units):
        if abs(n)<base or index==len(units)-1:
            rounded=whole(n)
            if abs(rounded)>=base and index<len(units)-1:
                return f'{whole(n/base):,} {units[index+1]}'
            return f'{rounded:,} {unit}'
        n/=base


def local_time(value):
    return datetime.fromisoformat(value).astimezone(ZoneInfo("Asia/Ho_Chi_Minh")).strftime("%d/%m/%Y %H:%M:%S UTC+07") if value else "Chưa có"


def scan_name(row):
    return f"{row['profile']} — Lần quét {row.get('ordinal', row['id']):02d}"


def scan_label(row):
    import json
    summary = json.loads(row.get("summary") or "{}")
    from .snapshot_scope import option_label
    from .measurements import provenance
    return f"{scan_name(row)} • {option_label(row)} • {local_time(row['started'])} • {row['engine']} • {provenance(summary)}" + (" • phục hồi" if summary.get("recovered") else "")


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
