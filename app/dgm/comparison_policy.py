"""Choose a display dimension from captured metadata, never today's filesystem."""
from __future__ import annotations

import json


def choose_measure(newer, older=None, requested='auto'):
    if requested not in ('auto', 'allocated', 'logical'):
        raise ValueError('Chỉ số phải là auto, allocated hoặc logical')
    scans = [s for s in (older, newer) if s is not None]

    def totals(scan):
        value = scan.get('summary') or {}
        return json.loads(value) if isinstance(value, str) else value

    def unavailable(scan, dimension):
        summary = totals(scan)
        files = summary.get('files', 0)
        unknown = summary.get(dimension + '_unknown_files')
        return bool(files and (unknown >= files if unknown is not None else summary.get(dimension) is None))

    missing = [s['id'] for s in scans if unavailable(s, 'allocated')]
    logical_available = all(not unavailable(s, 'logical') for s in scans)
    fallback = requested == 'auto' and bool(missing) and logical_available
    measure = 'logical' if fallback else 'allocated' if requested == 'auto' else requested
    note = ''
    if missing:
        names = ', '.join('#' + str(ident) for ident in missing)
        note = f'Mốc {names} thiếu toàn bộ số đo chiếm trên đĩa theo file. '
        note += ('Tự dùng Kích thước file cho cả cặp; không phải Windows đã dùng.' if fallback else
                 'Số đo chưa rõ không phải 0 B; không thể kết luận chênh lệch trên đĩa đầy đủ.')
    return {'requested_measure': requested, 'measurement': measure, 'automatic_measurement': fallback,
            'missing_allocation_scans': missing, 'measurement_note': note}
