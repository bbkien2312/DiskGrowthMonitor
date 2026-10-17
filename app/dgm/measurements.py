"""Snapshot-only measurements. Never reconcile old captures with today's disk."""
from .config import comparison_path_key


def provenance(summary):
    p = (summary or {}).get('provenance', {})
    contract = (summary or {}).get('contract', {})
    norm = (summary or {}).get('normalization', {}).get('version') or (summary or {}).get('canonical_normalization_version')
    return f"App {p.get('app_version') or '?'} · dữ liệu v{contract.get('version') or '?'} · chuẩn hóa v{norm or '?'}"


def amount(totals, mode='allocated'):
    totals = totals or {}
    if not totals:
        return None, 0
    return totals.get(mode), totals.get('allocated_unknown_files' if mode == 'allocated' else 'logical_unknown_files', 0)


def reconciliation(summary, roots):
    """A residual is unattributed, NOT a fabricated small-file measurement."""
    summary = summary or {}
    roots = [comparison_path_key(r) for r in roots]
    result = []
    volumes = summary.get('volumes', {})
    for volume, values in volumes.items():
        whole = comparison_path_key(volume) in roots
        source = next((v for k, v in summary.get('root_overview', {}).items()
                       if comparison_path_key(k) == comparison_path_key(volume)), {})
        use_export = source.get('export_used') is not None
        used = source.get('export_used') if use_export else values.get('used')
        # A combined multi-volume sum cannot be assigned to each volume.
        measured = summary.get('allocated') if whole and len(volumes) == 1 else None
        residual = used - measured if used is not None and measured is not None else None
        result.append({'volume': volume, 'total': values.get('total'), 'used': used,
                       'used_end': values.get('used'), 'measured': measured,
                       'residual': residual, 'whole_volume': whole,
                       'basis': 'export' if use_export else 'scan-end',
                       'warning': 'Số file đã đo vượt Windows cùng mốc; cần đối soát, không ép số.' if residual is not None and residual < 0 else '',
                       'note': 'Phần chưa quy được vào file; không gán cho file ≤10 MB, không xác định tăng/giảm theo đường dẫn.'})
    return result


def partition_checks(summary):
    checks = {}
    for side in ('old', 'new'):
        full = summary.get('full_' + side + '_totals')
        if not full:
            continue
        common = summary.get(side + '_totals') or {}
        private = summary.get('private_' + side + '_totals') or {}
        checks[side] = {}
        for key in ('logical', 'allocated', 'files'):
            values = [x.get(key) for x in (full, common, private)]
            checks[side][key] = None if any(v is None for v in values) else values[0] == values[1] + values[2]
    return checks
