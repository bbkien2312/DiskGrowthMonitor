"""Portable engine adapters. They return the same schema as the native scanner."""
import json
import os
from pathlib import Path
from .config import home, normalize
from .platform_ops import run


def enrich(entry):
    from .scanner import metadata
    try:
        live = metadata(Path(entry["path"]), entry["kind"])
        # Native attributes/IDs provide consistent physical accounting and Created.
        if entry["kind"] == "file" and live["logical"] != entry["logical"]:
            entry["status"] = "changed-during-scan"
        entry.update(allocated=live["allocated"], created=live["created"], identity=live["identity"], attrs=live["attrs"])
    except OSError:
        entry["status"] = "unavailable-after-export"
        entry["identity"] = ""
    return entry


def windirstat_entries(policy, config, stop, notify):
    from .scanner import Cancelled, stamp
    exe = config["engines"]["windirstat"]
    if not exe or not Path(exe).is_file():
        raise ValueError("Chưa cấu hình WinDirStat portable")
    output = home() / "data/exports/windirstat.json"
    output.parent.mkdir(exist_ok=True)
    output.unlink(missing_ok=True)
    try:
        notify("info", {"message": "WinDirStat đang xuất dữ liệu..."})
        result = run([exe, "/SaveTo", output, *policy.roots], timeout=1800, stop=stop)
        if result.returncode or not output.exists():
            raise RuntimeError(f"WinDirStat export lỗi: {result.returncode}")
        notify("info", {"message": "Đang nhập kết quả WinDirStat..."})
        import ijson
        with output.open("rb") as stream:
            for index, row in enumerate(ijson.items(stream, "item")):
                if stop.is_set():
                    raise Cancelled()
                if "Name" not in row or "Logical Size" not in row:
                    raise ValueError("WinDirStat schema/ngôn ngữ không tương thích; chọn English cho portable")
                path = row["Name"]
                if not any(normalize(path) == normalize(r) or normalize(path).startswith(normalize(r) + "/") for r in policy.roots) or policy.excluded(path):
                    continue
                flags = int(row.get("WinDirStat Attributes", "0"), 16)
                kind = "folder" if flags & 0x00000004 else "file"
                entry = {"path": str(Path(path)), "parent": str(Path(path).parent), "kind": kind,
                         "logical": row["Logical Size"], "allocated": row.get("Physical Size", 0),
                         "created": 0, "modified": stamp(row.get("Last Change", "")), "identity": "", "attrs": 0,
                         "note": "WinDirStat + native identity/allocation enrichment; export completeness unverified"}
                yield enrich(entry)
                if index % 5000 == 0:
                    notify("progress", {"count": index, "path": path})
    finally:
        output.unlink(missing_ok=True)


def gdu_entries(policy, config, stop, notify):
    from .scanner import Cancelled
    exe = config["engines"]["gdu"]
    if not exe or not Path(exe).is_file():
        raise ValueError("Chưa cấu hình gdu portable")
    private = home() / "data/exports"
    private.mkdir(exist_ok=True)
    config_file = private / "gdu.yaml"
    if not config_file.exists():
        config_file.write_text("", encoding="utf-8")
    for root in policy.roots:
        output = private / "gdu.json"
        output.unlink(missing_ok=True)
        args = [exe, root, "--non-interactive", "--no-progress", "--no-delete", "--no-view-file", "--no-spawn-shell",
                "--config-file", config_file, "--output-file", output]
        if policy.p.get("excludes"):
            args += ["--ignore-dirs-pattern", ",".join(policy.p["excludes"])]
        try:
            notify("info", {"message": f"gdu đang xuất dữ liệu: {root}"})
            result = run(args, timeout=1800, stop=stop)
            if result.returncode or not output.exists():
                raise RuntimeError(f"gdu export lỗi: {result.returncode}")
            notify("info", {"message": "Đang nhập kết quả gdu..."})
            for count, (path, is_dir, data) in enumerate(gdu_stream(output)):
                if stop.is_set():
                    raise Cancelled()
                if not policy.in_scope(path):
                    continue
                entry = {"path": str(path), "parent": str(path.parent), "kind": "folder" if is_dir else "file",
                         "logical": int(data.get("asize", 0)), "allocated": int(data.get("dsize", 0)), "created": 0,
                         "modified": float(data.get("mtime", 0)), "identity": "", "attrs": 0,
                         "note": "gdu + native enrichment; gdu dsize is not Windows physical allocation"}
                yield enrich(entry)
                if count % 5000 == 0:
                    notify("progress", {"count": count, "path": str(path)})
        finally:
            output.unlink(missing_ok=True)


def gdu_stream(path):
    """Streaming ncdu JSON parser: memory is proportional to directory depth."""
    import ijson
    started = False
    frames = []
    builder = None
    with Path(path).open("rb") as stream:
        for prefix, event, value in ijson.parse(stream):
            if not started:
                if prefix == "item" and event == "start_array":
                    started = True
                    frames.append({"path": None, "parent": None})
                continue
            if event == "start_array":
                frames.append({"path": None, "parent": frames[-1]["path"]})
            elif event == "end_array":
                if frames:
                    frames.pop()
                if not frames:
                    break
            elif event == "start_map":
                builder = ijson.ObjectBuilder()
                builder.event(event, value)
            elif builder is not None:
                builder.event(event, value)
                if event == "end_map":
                    data = builder.value
                    frame = frames[-1]
                    is_dir = frame["path"] is None
                    parent = frame["parent"] if is_dir else frame["path"]
                    item_path = Path(data["name"]) if parent is None else parent / data["name"]
                    if is_dir:
                        frame["path"] = item_path
                    yield item_path, is_dir, data
                    builder = None
    if not started:
        raise ValueError("Không nhận dạng được cây gdu/ncdu JSON")
