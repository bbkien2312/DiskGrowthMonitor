"""Real installed executables, restricted to tiny app-owned test fixtures on D:."""
from pathlib import Path
import threading
import pytest
from dgm.config import load, save, home
from dgm.platform_ops import is_admin
from dgm.scanner import scan

TOOLS = {"wiztree": Path(r"C:\Program Files\WizTree\WizTree64.exe"),
         "windirstat": Path(r"D:\DiskGrowthMonitor\.venv\tools\windirstat\x64\WinDirStat.exe"),
         "gdu": Path(r"D:\DiskGrowthMonitor\.venv\tools\gdu\gdu_windows_amd64.exe")}


@pytest.mark.parametrize("engine", TOOLS)
def test_live_export_cancel_on_import_then_restart(isolated, monkeypatch, engine):
    if not TOOLS[engine].is_file():
        pytest.skip("Portable engine not installed")
    if engine == "wiztree" and is_admin():
        pytest.skip("Do not allow privileged MFT indexing during fixture test")
    cfg = load(); cfg["engines"][engine] = str(TOOLS[engine]); save(cfg)
    root = isolated / "fixture"; root.mkdir(); (root / "a").write_bytes(b"abc")
    import dgm.engines as engines
    real = engines.enrich
    stop = threading.Event()
    def cancel(entry):
        result = real(entry); stop.set(); return result
    monkeypatch.setattr(engines, "enrich", cancel)
    p = {"roots": [str(root)], "engine": engine, "strict_browsers": False, "mft": False}
    def progress(event,data):
        if engine=='wiztree' and event=='overview':stop.set()
    result = scan("fixture", p, stop, notify=progress)
    assert result["status"] == "cancelled", result
    assert not (home() / "data/worker.lock").exists()
    monkeypatch.setattr(engines, "enrich", real)
    result = scan("fixture", p)
    assert result["status"] == "partial" and result["files"] == 1, result
