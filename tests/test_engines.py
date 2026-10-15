import json
from pathlib import Path
import pytest
from dgm.config import Policy
from dgm.engines import windirstat_entries, gdu_entries


def test_windirstat_low_type_flags(isolated, monkeypatch):
    root = isolated / "documents"; child = root / "child"; child.mkdir(parents=True)
    (child / "f").write_bytes(b"abc")
    import dgm.engines as engines
    def fake_run(argv, **kwargs):
        Path(argv[2]).write_text(json.dumps([
            {"Name": str(root), "Logical Size": 3, "Physical Size": 3, "WinDirStat Attributes": "0x30000004"},
            {"Name": str(child), "Logical Size": 3, "Physical Size": 3, "WinDirStat Attributes": "0x20000004"},
            {"Name": str(child / "f"), "Logical Size": 3, "Physical Size": 3, "WinDirStat Attributes": "0x20000008"},
        ]), encoding="utf-8")
        return type("Result", (), {"returncode": 0})()
    monkeypatch.setattr(engines, "run", fake_run)
    exe = isolated / "engine.exe"; exe.write_bytes(b"fake")
    import threading
    rows = list(windirstat_entries(Policy({"roots": [str(root)], "strict_browsers": False}), {"engines": {"windirstat": str(exe)}}, threading.Event(), lambda *a: None))
    assert [r["kind"] for r in rows] == ["folder", "folder", "file"]


def test_rename_identity(isolated):
    from dgm.scanner import scan
    from dgm.store import Store
    root = isolated / "documents"; root.mkdir(); (root / "before.dat").write_bytes(b"x" * 50)
    p = {"roots": [str(root)], "strict_browsers": True}
    a = scan("demo", p)
    (root / "before.dat").rename(root / "after.dat")
    b = scan("demo", p)
    s = Store()
    rows = s.compare(a["scan_id"], b["scan_id"], changes="moved")
    assert len(rows) == 1 and rows[0]["delta"] == 0
    s.close()


def test_gdu_stream_nested_folders(isolated):
    from dgm.engines import gdu_stream
    root = isolated / "data"
    path = isolated / "gdu.json"
    path.write_text(json.dumps([1, 2, {}, [{"name": str(root), "asize": 3}, [{"name": "child", "asize": 3}, {"name": "f", "asize": 3}]]]), encoding="utf-8")
    rows = list(gdu_stream(path))
    assert [row[1] for row in rows] == [True, True, False]
    assert rows[2][0] == root / "child/f"
