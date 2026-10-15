import os
import time
from pathlib import Path
import pytest
from dgm import cleanup


def fixture_rule(monkeypatch, root):
    monkeypatch.setattr(cleanup, "rules", lambda: {"test-cache": {"title": "Test", "roots": [str(root)], "days": 7, "risk": "test only"}})


def old_file(path, content=b"cache"):
    path.write_bytes(content)
    old = time.time() - 20 * 86400
    os.utime(path, (old, old))


def test_preview_never_deletes_and_requires_confirmation(isolated, monkeypatch):
    root = isolated / "cache"; root.mkdir(); file = root / "a.bin"; old_file(file)
    fixture_rule(monkeypatch, root)
    p = cleanup.plan(["test-cache"])
    assert file.exists() and len(p["entries"]) == 1
    with pytest.raises(ValueError):
        cleanup.apply(p["id"], "yes")
    result = cleanup.apply(p["id"], p["confirmation"])
    assert not file.exists() and result["deleted_bytes"] == 5


def test_changed_after_preview_is_skipped(isolated, monkeypatch):
    root = isolated / "cache"; root.mkdir(); file = root / "a.bin"; old_file(file)
    fixture_rule(monkeypatch, root)
    p = cleanup.plan(["test-cache"])
    file.write_bytes(b"changed")
    result = cleanup.apply(p["id"], p["confirmation"])
    assert file.exists() and result["items"][0]["status"] == "skipped"


def test_nvidia_requires_update_confirmation(isolated):
    p = cleanup.plan(["nvidia-update"])
    assert p["blocked"] and p["entries"] == []


def test_new_file_not_selected(isolated, monkeypatch):
    root = isolated / "cache"; root.mkdir(); (root / "active.bin").write_bytes(b"active")
    fixture_rule(monkeypatch, root)
    p = cleanup.plan(["test-cache"])
    assert p["entries"] == []

