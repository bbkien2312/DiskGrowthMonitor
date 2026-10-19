"""Physical/logical presentation and volume reconciliation, without live scans.

All calculations retain integer bytes. Captured measurements never fall back to
today's disk usage. Live probes are explicit and only call a volume-space API.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import ntpath
import os
from pathlib import Path
import shutil

MEASURES = {'allocated': 'Trên đĩa', 'logical': 'Logical — kích thước file'}


def size(value, unit='binary'):
    if value is None:
        return '—'
    if unit not in ('binary', 'decimal'):
        raise ValueError('Đơn vị phải là binary hoặc decimal')
    base = 1024 if unit == 'binary' else 1000
    labels = ('B', 'KiB', 'MiB', 'GiB', 'TiB', 'PiB') if unit == 'binary' else ('B', 'KB', 'MB', 'GB', 'TB', 'PB')
    amount = abs(Decimal(value))
    for index,label in enumerate(labels):
        if amount < base or label == labels[-1]:
            # Windows-style integer shortening. In particular 138.89 GiB is
            # shown as 138, not 139; the exact byte value is always available.
            number=int(amount) if unit=='binary' else int(amount.quantize(Decimal('1'),rounding=ROUND_HALF_UP))
            if number>=base and index<len(labels)-1:
                number,label=1,labels[index+1]
            return ('−' if value < 0 else '') + f'{number:,} {label}'
        amount /= base


def delta(value, unit='binary', *, partial=False):
    if value is None:
        return '?'
    text = '↑ +' + size(value, unit) if value > 0 else '↓ −' + size(-value, unit) if value < 0 else '= 0 B'
    return ('≈ ' if partial else '') + text


def exact(value):
    return 'Chưa rõ dung lượng; không coi là 0 byte' if value is None else f'{value:,} byte'


def snapshot_text(row, side, unit='binary'):
    if side == 'old' and row.get('baseline'):
        return '—'
    if row['presence'] not in (side, 'both'):
        issue = row.get(side + '_issue') or ''
        return '∅' if issue in ('scope', 'browser') else '?' if issue else '×'
    value = row['before' if side == 'old' else 'after']
    return '?' if value is None else size(value, unit)


def anchor(path):
    if str(path).startswith('/') and not str(path).startswith('//'):
        return '/'
    value = str(path).replace('/', '\\')
    drive = ntpath.splitdrive(value)[0]
    if drive:
        return drive.rstrip('\\') + '\\'
    return Path(path).anchor or '/'


def read_volume_usage(roots):
    """One disk-usage API call per drive, never enumerate/open captured files."""
    result = []
    for root in sorted({anchor(path) for path in roots}):
        try:
            usage = shutil.disk_usage(root)
            result.append({'root': root, 'capacity': usage.total, 'free': usage.free,
                           'used': usage.used, 'source': 'windows-api' if os.name == 'nt' else 'volume-api',
                           'captured_at': datetime.now(timezone.utc).isoformat()})
        except OSError as exc:
            result.append({'root': root, 'capacity': None, 'free': None, 'used': None,
                           'source': 'volume-api', 'error': str(exc)})
    return result


def captured_volumes(scan):
    """Read ONLY historical evidence. CSV drive records outrank API samples."""
    records = {}
    legacy = scan.get('legacy_summary') or {}
    for root, values in (legacy.get('volumes') or {}).items():
        records[anchor(root).lower()] = {'root': anchor(root), 'capacity': values.get('total'),
            'free': values.get('free'), 'used': values.get('used'), 'source': 'legacy-windows-api',
            'captured_at': scan.get('started')}
    for value in scan.get('volume_snapshots') or []:
        records[anchor(value['root']).lower()] = dict(value)
    for value in scan.get('overview') or []:
        if any(value.get(name) is not None for name in ('capacity', 'free', 'used')):
            records[anchor(value['root']).lower()] = {key: value.get(key) for key in ('root', 'capacity', 'free', 'used')}
            records[anchor(value['root']).lower()].update(source='wiztree-csv', captured_at=scan.get('started'))
    for record in records.values():
        capacity, free, used = (record.get(name) for name in ('capacity', 'free', 'used'))
        if used is None and all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in (capacity, free)):
            record['used'] = used = capacity - free
        record['valid'] = (all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in (capacity, free, used))
                           and used + free == capacity)
        if not record['valid']:
            record['warning'] = 'Thiếu hoặc mâu thuẫn số đo Windows; không suy ra Used từ Logical'
    return list(records.values())


def reconcile(scan, allocations=None):
    """Keep an unexplained remainder signed and distinct from small files."""
    from .columnar_store import CapturedPaths
    paths = CapturedPaths(scan)
    scope = scan.get('scope') or {}
    roots = scope.get('roots') or []
    anchors = {anchor(root).lower() for root in roots}
    buckets = {row['volume']: row for row in (allocations or [])}
    rows = []
    for disk in captured_volumes(scan):
        record = dict(disk)
        volume_root = anchor(disk['root'])
        key = paths.key(volume_root).split('|', 1)[0]
        bucket = buckets.get(key)
        if bucket is None and len(anchors) == 1 and volume_root.lower() in anchors:
            if len(buckets) == 1:
                bucket = next(iter(buckets.values()))
            elif not buckets:
                summary = scan.get('summary') or {}
                bucket = {'allocated_known': summary.get('allocated_known'),
                          'allocated_unknown_files': summary.get('allocated_unknown_files')}
        bucket = bucket or {}
        known, unknown = bucket.get('allocated_known'), bucket.get('allocated_unknown_files')
        full = any(paths.canonical(root).rstrip('\\/').lower() == paths.canonical(volume_root).rstrip('\\/').lower() for root in roots)
        record.update(whole_volume=full, allocated_known=known, allocated_unknown_files=unknown,
                      residual=None, unexplained_upper_bound=None, status='unknown')
        if not full:
            record.update(status='partial-scope', note='Chỉ quét thư mục; Used là của cả ổ, không phải dung lượng thư mục')
        elif not record['valid'] or known is None or unknown is None:
            record['note'] = 'Thiếu số đo; giữ chưa rõ, không thay bằng Logical hoặc 0'
        elif unknown:
            record.update(status='unknown-allocation', note='Có file thiếu cấp phát; chưa tính được phần còn lại chính xác')
            if record['used'] >= known:
                record['unexplained_upper_bound'] = record['used'] - known
        else:
            residual = record['used'] - known
            record.update(residual=residual, status='unassigned' if residual >= 0 else 'mismatch',
                note='Used chưa quy được vào các file đã đo; có thể gồm metadata NTFS, mục loại trừ và vùng không đọc được. Không gán cho file nhỏ.' if residual >= 0 else
                     'Cấp phát file vượt Used đã chụp; cần đối chiếu thời điểm/nguồn. Không cắt hoặc ép số cho khớp.')
        rows.append(record)
    return rows
