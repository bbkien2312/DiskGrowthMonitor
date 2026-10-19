import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
import psutil
import pytest
from typer.testing import CliRunner
from dgm.config import load, home
from dgm.platform_ops import run, ProcessCancelled
from dgm.scanner import scan, ScanLock
from dgm.store import Store
from dgm.cli import app


def test_run_cancels_only_owned_process_tree(isolated):
    ready = isolated / "child.pid"
    child = "import time; time.sleep(30)"
    code = f"import subprocess,sys,time,pathlib; p=subprocess.Popen([sys.executable,'-c',{child!r}]); pathlib.Path({str(ready)!r}).write_text(str(p.pid)); time.sleep(30)"
    stop = threading.Event()
    sentinel = subprocess.Popen([sys.executable, "-c", child])
    errors = []
    def execute():
        try:
            run([sys.executable, "-c", code], timeout=20, stop=stop)
        except BaseException as exc:
            errors.append(exc)
    worker = threading.Thread(target=execute); worker.start()
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and worker.is_alive() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.exists(), errors
        pid = int(ready.read_text()); process = psutil.Process(pid)
        stop.set(); worker.join(timeout=4)
        assert not worker.is_alive()
        assert len(errors) == 1 and isinstance(errors[0], ProcessCancelled)
        # The process can exit between is_running() and status() on Windows.
        gone,alive=psutil.wait_procs([process],timeout=2)
        assert not alive
        assert sentinel.poll() is None
    finally:
        stop.set(); worker.join(timeout=4)
        sentinel.terminate(); sentinel.wait(timeout=5)


def test_timeout_kills_owned_child(isolated):
    ready = isolated / "timeout-child.pid"
    code = f"import subprocess,sys,time,pathlib; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); pathlib.Path({str(ready)!r}).write_text(str(p.pid)); time.sleep(30)"
    with pytest.raises(subprocess.TimeoutExpired):
        run([sys.executable, "-c", code], timeout=0.8, stop=threading.Event())
    pid = int(ready.read_text())
    assert not psutil.pid_exists(pid)


def test_cancel_during_external_export_then_restart(isolated, monkeypatch):
    import dgm.scanner as scanner
    root = isolated / "files"; root.mkdir(); (root / "a").write_bytes(b"abc")
    exe = isolated / "wiz.exe"; exe.write_bytes(b"test")
    cfg = load(); cfg["engines"]["wiztree"] = str(exe)
    from dgm.config import save
    save(cfg)
    slow = [True]
    entered = threading.Event()
    def exporter(argv, **kwargs):
        output = next(str(arg).split("=", 1)[1] for arg in argv if str(arg).startswith("/export="))
        csv = f"File Name,Size,Allocated,Modified,Attributes\n{root}\\,0,0,,0\n{root / 'a'},3,3,,0\n"
        code = f"from pathlib import Path; import time; Path({output!r}).write_text({csv!r}, encoding='utf-8'); time.sleep({30 if slow[0] else 0})"
        entered.set()
        return run([sys.executable, "-c", code], **kwargs)
    monkeypatch.setattr(scanner, "run", exporter)
    p = {"roots": [str(root)], "strict_browsers": False, "engine": "wiztree", "mft": False}
    stop = threading.Event(); results = []
    worker = threading.Thread(target=lambda: results.append(scan("fixture", p, stop))); worker.start()
    assert entered.wait(5); time.sleep(0.2); stop.set(); worker.join(timeout=5)
    assert not worker.is_alive() and results[0]["status"] == "cancelled"
    assert not (home() / "data/worker.lock").exists()
    assert not list((home() / "data/exports").glob("*.csv"))
    slow[0] = False
    result = scan("fixture", p)
    assert result["status"] == "partial" and result["files"] == 1 and result["first_scan"]


def test_cancel_aggregation_then_restart(isolated, monkeypatch):
    root = isolated / "files"; root.mkdir(); (root / "a").write_bytes(b"abc")
    p = {"roots": [str(root)], "engine": "native", "strict_browsers": True}
    original = Store.aggregate
    def cancel(self, scan_id, stop=None, notify=None):
        stop.set(); return original(self, scan_id, stop)
    monkeypatch.setattr(Store, "aggregate", cancel)
    result = scan("fixture", p)
    assert result["status"] == "cancelled"
    monkeypatch.setattr(Store, "aggregate", original)
    assert scan("fixture", p)["status"] == "complete"


def test_token_scoped_remote_stop_and_no_stale_stop(isolated):
    from dgm.scan_control import request_stop, current
    stop = threading.Event()
    with ScanLock(stop):
        assert current()["cancellable"]
        assert request_stop()["requested"]
        assert stop.wait(2)
    newer = threading.Event()
    with ScanLock(newer):
        assert not newer.wait(0.35)
    assert not current()["running"]


def test_default_wiztree_and_no_silent_scope_fallback(isolated):
    assert load()["profiles"]["daily"]["engine"] == "wiztree"
    with pytest.raises(ValueError, match="không tự đổi engine"):
        scan("fixture", {"roots": [str(isolated)], "engine": "wiztree", "strict_browsers": True})


def test_compare_rejects_cancelled_first_scan_and_reversed(isolated):
    root = isolated / "files"; root.mkdir()
    p = {"roots": [str(root)], "engine": "native"}
    stop = threading.Event(); stop.set()
    c = scan("fixture", p, stop)
    a, b = scan("fixture", p), scan("fixture", p)
    s = Store()
    with pytest.raises(ValueError):
        s.compare(None, c["scan_id"])
    with pytest.raises(ValueError, match="Mốc cũ"):
        s.compare(b["scan_id"], a["scan_id"])
    s.close()


def test_disable_task_is_not_remove(isolated, monkeypatch):
    import dgm.schedule as schedules
    calls = []
    monkeypatch.setattr(schedules, "run", lambda argv, **kw: calls.append(argv) or subprocess.CompletedProcess(argv, 0, "", ""))
    monkeypatch.setattr(schedules, "task_status", lambda: {"installed": True, "enabled": False})
    result = schedules.enable_task(False)
    assert result["installed"] and result["enabled"] is False
    assert "/Change" in calls[0] and "/DISABLE" in calls[0] and "/Delete" not in calls[0]


def test_update_check_read_only_and_offline(isolated, monkeypatch):
    import dgm.updates as updates
    before = (home() / "data/config.json").read_bytes()
    monkeypatch.setattr(updates, "fetch", lambda *a: {"info": {"version": "999.0"}})
    result = updates.check_updates(["typer"])
    assert result[0]["status"] == "Có bản mới (chưa cài)"
    assert (home() / "data/config.json").read_bytes() == before
    def fail(*a):
        raise OSError("offline")
    monkeypatch.setattr(updates, "fetch", fail)
    assert "Không kiểm tra được" in updates.check_updates(["typer"])[0]["status"]


def test_cli_new_commands_and_default(isolated, monkeypatch):
    runner = CliRunner()
    root = isolated / "files"; root.mkdir()
    result = runner.invoke(app, ["profile", "set", "demo", "--root", str(root)])
    assert result.exit_code == 0 and '"engine": "wiztree"' in result.output
    assert runner.invoke(app, ["scan-status"]).exit_code == 0
    assert runner.invoke(app, ["scan-stop"]).exit_code == 0
    assert runner.invoke(app, ["packages"]).exit_code == 0
    import dgm.updates as updates
    monkeypatch.setattr(updates, "fetch", lambda *a: {"info": {"version": "999.0"}})
    result = runner.invoke(app, ["check-updates", "--package", "typer"])
    assert result.exit_code == 0 and "999.0" in result.output


def test_task_status_distinguishes_missing_from_denied(isolated, monkeypatch):
    import dgm.schedule as schedules
    import pywintypes, win32com.client
    class Scheduler:
        def Connect(self):
            pass
        def GetFolder(self, name):
            raise pywintypes.com_error(-2147352567, "Exception", (0, None, None, None, 0, -2147024894), None)
    monkeypatch.setattr(win32com.client, "Dispatch", lambda _: Scheduler())
    assert schedules.task_status()["installed"] is False
    class Denied(Scheduler):
        def GetFolder(self, name):
            raise pywintypes.com_error(-2147024891, "Denied", None, None)
    monkeypatch.setattr(win32com.client, "Dispatch", lambda _: Denied())
    with pytest.raises(RuntimeError, match="Không đọc được"):
        schedules.task_status()


def test_service_disable_changes_startup_and_stops_not_uninstalls(isolated, monkeypatch):
    import dgm.windows_service as services
    import win32service
    exe = isolated / "wrapper.exe"; exe.write_bytes(b"fake")
    monkeypatch.setattr(services, "service_path", lambda: exe)
    monkeypatch.setattr(services, "is_admin", lambda: True)
    monkeypatch.setattr(services, "status", lambda: {"installed": True, "running": True})
    calls, config_calls = [], []
    monkeypatch.setattr(services, "run", lambda argv, **kw: calls.append(argv) or subprocess.CompletedProcess(argv, 0, "", ""))
    monkeypatch.setattr(win32service, "OpenSCManager", lambda *a: 1)
    monkeypatch.setattr(win32service, "OpenService", lambda *a: 2)
    monkeypatch.setattr(win32service, "CloseServiceHandle", lambda *a: None)
    monkeypatch.setattr(win32service, "ChangeServiceConfig", lambda *a: config_calls.append(a))
    services.control("disable")
    assert config_calls[0][2] == win32service.SERVICE_DISABLED
    assert calls == [[exe, "stop"]]
    assert exe.exists() and (home() / "data/agent.stop").exists()


def test_mft_task_install_requests_scoped_elevation(isolated, monkeypatch):
    import dgm.background as background
    from dgm.config import save
    cfg = load(); cfg["profiles"]["daily"]["mft"] = True; save(cfg)
    calls = []
    monkeypatch.setattr(background, "is_admin", lambda: False)
    monkeypatch.setattr(background, "status", lambda: {'task':{'installed':False}})
    monkeypatch.setattr(background, "elevated", lambda kind, action: calls.append((kind, action)) or {"installed": True})
    assert background.control("task", "install")["installed"]
    assert calls == [("task", "install")]


def test_exported_drive_root_total_not_counted_twice(isolated):
    from dgm.scanner import metadata
    s = Store()
    scan_id = s.begin_scan("fixture", "test", "wiztree", {"roots": ["Z:\\"]})
    folder = {"path": "Z:\\", "parent": "Z:\\", "kind": "folder", "logical": 10, "allocated": 10, "created": 0, "modified": 0}
    file = {"path": "Z:\\a", "parent": "Z:\\", "kind": "file", "logical": 10, "allocated": 10, "created": 0, "modified": 0}
    s.put(scan_id, [folder, file]); s.aggregate(scan_id)
    root = s.db.execute("SELECT logical,allocated FROM entries WHERE scan_id=? AND path=?", (scan_id, "Z:\\")).fetchone()
    assert tuple(root) == (10, 10)
    s.close()
