import json
import os
import threading
from pathlib import Path
from unittest.mock import patch
import pytest
from dgm.config import Policy, browser_path, scope_hash
from dgm.scanner import scan, metadata, allocated
from dgm.store import Store


def prof(root):
    return {"roots": [str(root)], "excludes": [], "strict_browsers": True, "engine": "native"}


def test_browser_names_precise():
    assert browser_path(r"C:\Users\Name\AppData\Local\Google\Chrome\Cache")
    assert browser_path(r"C:\Program Files (x86)\Microsoft\EdgeCore\abc")
    assert browser_path(r"C:\Windows\WinSxS\amd64_microsoft-edge-webview_xyz")
    assert not browser_path(r"D:\Projects\knowledge\edge-detection")


def test_baseline_growth_and_small_files(isolated):
    root = isolated / "documents"; root.mkdir()
    (root / "small.txt").write_bytes(b"a" * 200)
    first = scan("demo", prof(root))
    assert first["status"] == "complete" and first["first_scan"]
    (root / "small.txt").write_bytes(b"b" * 400)
    (root / "new.txt").write_bytes(b"c" * 500)
    second = scan("demo", prof(root))
    s = Store()
    rows = {Path(x["path"]).name: x for x in s.compare(first["scan_id"], second["scan_id"])}
    assert rows["small.txt"]["delta"] == 200
    assert rows["new.txt"]["change"] == "new"
    assert rows["documents"]["after"] == 900
    s.close()


def test_threshold_crossing_is_growth_not_new(isolated):
    root = isolated / "documents"; root.mkdir()
    file = root / "growing.dat"
    with file.open("wb") as f:
        f.truncate(9_000_000)
    first = scan("demo", prof(root))
    with file.open("wb") as f:
        f.truncate(12_000_000)
    second = scan("demo", prof(root))
    s = Store()
    row = next(x for x in s.compare(first["scan_id"], second["scan_id"]) if x["kind"] == "file")
    assert row["change"] == "grown" and row["delta"] == 3_000_000
    s.close()


def test_excluded_browser_not_traversed_or_statted(isolated, monkeypatch):
    root = isolated / "documents"; root.mkdir()
    chrome = root / "Chrome"; chrome.mkdir(); (chrome / "secret.dat").write_bytes(b"secret")
    edge = root / "EdgeCore"; edge.mkdir(); (edge / "secret.dat").write_bytes(b"secret")
    (root / "allowed.txt").write_text("hello")
    import dgm.scanner as scanner
    real = scanner.metadata
    visited = []
    def tracked(path, kind):
        visited.append(str(path))
        assert not browser_path(path)
        return real(path, kind)
    monkeypatch.setattr(scanner, "metadata", tracked)
    result = scan("demo", prof(root))
    assert result["files"] == 1
    assert all(not browser_path(p) for p in visited)


def test_nested_roots_not_double_counted(isolated):
    root = isolated / "documents"; child = root / "child"; child.mkdir(parents=True)
    (child / "a").write_bytes(b"x" * 100)
    p = prof(root); p["roots"].append(str(child))
    result = scan("demo", p)
    assert result["logical"] == 100


def test_hardlinks_count_physical_once(isolated):
    root = isolated / "documents"; root.mkdir()
    a = root / "a.dat"; a.write_bytes(b"x" * 8192)
    try:
        os.link(a, root / "b.dat")
    except OSError:
        pytest.skip("Hardlink unsupported")
    result = scan("demo", prof(root))
    assert result["logical"] == 16384
    assert result["allocated"] == allocated(a, a.stat())


def test_cancel_not_baseline(isolated):
    root = isolated / "documents"; root.mkdir()
    first = scan("demo", prof(root))
    stop = threading.Event(); stop.set()
    cancelled = scan("demo", prof(root), stop)
    assert cancelled["status"] == "cancelled"
    third = scan("demo", prof(root))
    assert third["previous"] == first["scan_id"]


def test_partial_missing_is_unknown(isolated, monkeypatch):
    root = isolated / "documents"; denied = root / "blocked"; denied.mkdir(parents=True)
    (denied / "file.dat").write_bytes(b"x" * 100)
    first = scan("demo", prof(root))
    real = os.scandir
    def deny(path):
        if Path(path) == denied:
            raise PermissionError("test inaccessible")
        return real(path)
    monkeypatch.setattr(os, "scandir", deny)
    second = scan("demo", prof(root))
    assert second["status"] == "partial"
    s = Store()
    row = next(x for x in s.compare(first["scan_id"], second["scan_id"]) if x["path"].endswith("file.dat"))
    assert row["change"] == "unknown" and row["delta"] is None
    s.close()


def test_scope_change_rejected(isolated):
    root = isolated / "documents"; root.mkdir()
    p = prof(root); a = scan("demo", p)
    p["excludes"] = ["*.tmp"]; b = scan("demo", p)
    s = Store()
    with pytest.raises(ValueError):
        s.compare(a["scan_id"], b["scan_id"])
    s.close()

