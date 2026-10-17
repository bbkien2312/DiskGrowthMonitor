"""Versioned, engine-neutral measurement rules. Adapters normalize on ingestion."""
from __future__ import annotations

VERSION = 3
SEMANTICS = "wiztree-logical-allocated-v3"


def canonical_entry(entry):
    """Keep unknown metadata unknown; do not turn missing allocation/dates into zero."""
    row = dict(entry)
    for field in ("logical", "allocated"):
        value = row.get(field)
        row[field] = None if value is None or value == "" else int(value)
        if row[field] is not None and row[field] < 0:
            raise ValueError(f"Negative {field}: {row['path']}")
    for field in ("created", "modified"):
        value = row.get(field)
        # Preserve native precision; cross-engine comparisons use the common
        # precision in their indexed map, never discard it from history.
        row[field] = float(value) if value and float(value)>0 else None
    row.setdefault("identity", "")
    row.setdefault("attrs", 0)
    row.setdefault("status", "ok")
    row.setdefault("note", "")
    # Reparse points must not be traversed or counted as independent targets.
    if row.get("attrs", 0) & 0x400 or row["kind"] == "link":
        row.update(kind="link", logical=0, allocated=0)
    elif row['kind']=='file' and row['allocated'] is None and row['status']=='ok':
        row['status']='metadata-partial'
        row['note']=row['note'] or 'Chưa đo được cấp phát; vẫn so sánh Size đã biết.'
    return row


def describe(engine):
    return {"version": VERSION, "semantics": SEMANTICS, "engine": engine,
            "logical": "bytes per file path, including each hardlink path",
            "allocated": "bytes on disk, one stable path per hardlink identity",
            "timestamps": "UTC epoch seconds, retain source precision; missing is null",
            "links": "reparse targets are not followed"}


def coverage(engine, roots, errors):
    """Successful export is not proof of full filesystem coverage."""
    return {"version": 1, "source": "filesystem" if engine == "native" else "export",
            "unknown_roots": list(roots) if engine != "native" else [],
            "error_regions": errors, "export_loaded": True}


def dates_differ(left, right):
    return left is not None and right is not None and int(left) != int(right)
