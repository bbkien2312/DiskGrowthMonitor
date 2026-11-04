"""Snapshot arithmetic, distinct from evidence that a path was added/deleted."""
from __future__ import annotations

COMPARISON_VERSION = 2


def difference(old, new):
    return None if old is None or new is None else new - old


def snapshot_changes(old, new):
    """Never substitute today's usage or logical bytes for allocated bytes."""
    before = (old or {}).get('summary') or {}
    after = new.get('summary') or {}
    return {key: difference(before.get(key), after.get(key)) if old else None
            for key in ('logical', 'allocated')}


def same_scope(old,new):
    if not old:return None
    def normalized(scan):
        s=scan.get('scope') or {}
        pathset=lambda key: sorted(str(p).replace('/','\\').rstrip('\\').casefold() for p in s.get(key) or [])
        return pathset('roots'),pathset('excludes'),pathset('implicit_excludes'),bool(s.get('strict_browsers'))
    if normalized(old)!=normalized(new):return False
    a,b=old.get('volume_map') or {},new.get('volume_map') or {}
    if any(anchor in b and str(identity).casefold()!=str(b[anchor]).casefold() for anchor,identity in a.items()):return False
    return True


def disk_changes(old_rows, new_rows, old_scan=None, new_scan=None):
    key=lambda root:root.replace('/','\\').rstrip('\\').casefold()
    old = {key(r['root']): r for r in old_rows or []}
    rows = []
    for new in new_rows or []:
        before = old.get(key(new['root']))
        old_id=((old_scan or {}).get('volume_map') or {}).get(new['root'][:2].lower())
        new_id=((new_scan or {}).get('volume_map') or {}).get(new['root'][:2].lower())
        same_volume=bool(old_id and new_id and str(old_id).casefold()==str(new_id).casefold())
        valid = bool(before and before.get('valid') and new.get('valid') and
                     before.get('whole_volume') and new.get('whole_volume') and
                     before.get('capacity') == new.get('capacity') and same_volume)
        used = difference(before.get('used'), new.get('used')) if valid else None
        files = difference(before.get('allocated_known'), new.get('allocated_known')) if valid and not (
            before.get('allocated_unknown_files') or new.get('allocated_unknown_files')) else None
        rows.append({'root': new['root'], 'used_delta': used, 'files_delta': files,
                     'unassigned_delta': difference(files, used), 'valid': valid,
                     'note': 'Số đo đã lưu trong hai lượt; phần chưa quy vào file không phải file nhỏ.'})
    return rows
