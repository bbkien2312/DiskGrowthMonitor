import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import json
import sys
import time
import threading
from PySide6.QtCore import Qt, QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox, QHeaderView
import pytest
from dgm.config import load, save, home
from dgm.gui import Window


def pump(app, condition, timeout=5):
    until = time.monotonic() + timeout
    while not condition() and time.monotonic() < until:
        app.processEvents(); time.sleep(0.005)
    app.processEvents()
    assert condition()


@pytest.fixture
def gui(isolated, monkeypatch):
    import dgm.background as background
    monkeypatch.setattr(background, "status", lambda: {"task": {"installed": False}, "service": {"installed": False}, "scan": {"running": False}})
    app = QApplication.instance() or QApplication([])
    window = Window(); window.show()
    pump(app, lambda: not window.workers)
    yield app, window
    for worker in list(window.workers):
        worker.stop.set()
    pump(app, lambda: not window.workers)
    window.close(); app.processEvents()


def test_gui_cancel_and_restart_five_cycles(gui, monkeypatch):
    app, w = gui
    from dgm.platform_ops import run, ProcessCancelled
    import dgm.scanner as scanner
    entered = threading.Event()
    calls = []
    def fake_scan(name, p, stop, notify):
        calls.append(name); entered.set()
        try:
            run([sys.executable, "-c", "import time; time.sleep(30)"], stop=stop)
        except ProcessCancelled:
            return {"scan_id": len(calls), "status": "cancelled", "files": 0, "seconds": 0.2}
    monkeypatch.setattr(scanner, "scan", fake_scan)
    w.engine.setCurrentText("native")
    for _ in range(5):
        entered.clear(); w.begin_scan()
        assert not w.scan_button.isEnabled() and w.stop_button.isEnabled()
        pump(app, entered.is_set)
        QTest.mouseClick(w.stop_button, Qt.MouseButton.LeftButton)
        assert not w.rescan_button.isEnabled()
        pump(app, lambda: w.scan_worker is None)
        assert w.rescan_button.isEnabled() and w.scan_button.isEnabled()
        assert "Đã dừng" in w.status.text()
    assert len(calls) == 5


def test_column_mouse_drag_independent_and_persisted(gui):
    app, w = gui
    header = w.table.horizontalHeader()
    assert all(header.sectionResizeMode(i) == QHeaderView.ResizeMode.Interactive for i in range(header.count()))
    old, neighbor = header.sectionSize(0), header.sectionSize(1)
    point = QPoint(header.sectionViewportPosition(0) + old - 1, header.height() // 2)
    QTest.mousePress(header.viewport(), Qt.MouseButton.LeftButton, pos=point)
    QTest.mouseMove(header.viewport(), point + QPoint(75, 0), delay=20)
    QTest.mouseRelease(header.viewport(), Qt.MouseButton.LeftButton, pos=point + QPoint(75, 0))
    app.processEvents()
    assert header.sectionSize(0) >= old + 60
    assert header.sectionSize(1) == neighbor
    w.save_layout()
    stored = json.loads((home() / "data/ui-settings.json").read_text(encoding="utf-8"))
    assert stored["columns/details-v3"][0] == header.sectionSize(0)
    reopened = Window(); pump(app, lambda: not reopened.workers)
    assert reopened.table.horizontalHeader().sectionSize(0) == header.sectionSize(0)
    reopened.close(); app.processEvents()
    w.reset_layout(); assert header.sectionSize(0) == 300


def test_help_and_packages_and_no_config_write(gui):
    app, w = gui
    before = (home() / "data/config.json").read_bytes()
    assert w.engine.currentText() == "wiztree"
    box = w.help_boxes["scan"]
    w.scan_options_toggle.setChecked(True)
    assert not box.toggle.icon().isNull() and not box.body.isVisible()
    QTest.mouseClick(box.toggle, Qt.MouseButton.LeftButton)
    assert box.body.isVisible() and box.toggle.isChecked()
    QTest.mouseClick(box.toggle, Qt.MouseButton.LeftButton)
    assert not box.body.isVisible() and not box.toggle.isChecked()
    assert w.package_table.rowCount() >= 10
    assert not w.package_table.item(0, 0).icon().isNull()
    assert (home() / "data/config.json").read_bytes() == before
    assert w.table.height() >= 180


def test_async_compare_clear_baseline_and_positive_delta(gui, isolated):
    app, w = gui
    from dgm.scanner import scan
    root = isolated / "files"; root.mkdir(); (root / "a").write_bytes(b"a" * 100)
    p = {"roots": [str(root)], "engine": "native", "strict_browsers": True, "threshold_mb": 0}
    a = scan("demo", p)
    cfg = load(); cfg["profiles"]["demo"] = p; save(cfg)
    w.refresh_profiles(); w.profile_combo.setCurrentText("demo")
    w.compare(); pump(app, lambda: not w.workers)
    assert "mốc ban đầu" in w.scan_info.text().lower()
    (root / "a").write_bytes(b"a" * 300)
    b = scan("demo", p)
    w.refresh_history(); w.newer.setCurrentIndex(w.newer.findData(b["scan_id"]))
    w.compare(); pump(app, lambda: not w.workers)
    assert "Cũ:" in w.scan_info.text() and "Lần quét 01" in w.scan_info.text() and "Lần quét 02" in w.scan_info.text()
    index = next(i for i, row in enumerate(w.rows) if row["kind"] == "file")
    assert w.table.model().data(w.table.model().index(index,1)).startswith("↑ +")


def test_background_buttons_and_scope_confirmation(gui, monkeypatch):
    app, w = gui
    w.background_done({"task": {"installed": True, "enabled": False, "actions":[{"path":str(home()/'DiskGrowthMonitor.exe')}]}, "service": {"installed": False}, "scan": {"running": False}})
    _, label, buttons = w.background_panels[0]
    assert "Đã tắt" in label.text()
    assert buttons["enable"].isEnabled() and buttons["remove"].isEnabled()
    assert not buttons["disable"].isEnabled()
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    w.strict.setChecked(True)
    w.begin_scan()
    assert w.scan_worker is None and w.engine.currentText() == "wiztree"


def test_other_job_error_does_not_unlock_scan(gui, monkeypatch):
    app, w = gui
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: None)
    entered = threading.Event()
    def scanning(worker):
        entered.set(); worker.stop.wait(5)
        return {"scan_id": 1, "status": "cancelled", "files": 0, "seconds": 0}
    w.job(scanning, w.scan_done, scan_job=True); pump(app, entered.is_set)
    w.fail("Một tác vụ khác lỗi")
    assert not w.scan_button.isEnabled()
    w.cancel_scan(); pump(app, lambda: w.scan_worker is None)


def test_unsaved_profile_cancel(gui, monkeypatch):
    app, w = gui
    cfg = load(); cfg["profiles"]["other"] = {**cfg["profiles"]["daily"], "engine": "native"}; save(cfg)
    w.refresh_profiles(); w.roots.setText("D:\\Changed")
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Cancel)
    w.profile_combo.setCurrentText("other")
    assert w.profile_combo.currentText() == "daily" and w.roots.text() == "D:\\Changed"


def test_update_buttons_worker_success_and_failure(gui, monkeypatch):
    app, w = gui
    import dgm.updates as updates
    monkeypatch.setattr(updates, "check_updates", lambda names: [{"id": "typer", "latest": "999.0", "status": "Có bản mới (chưa cài)"}])
    w.check_updates(["typer"])
    assert not w.update_all_button.isEnabled()
    pump(app, lambda: not w.workers)
    row = next(i for i, item in enumerate(w.package_rows) if item["id"] == "typer")
    assert w.package_table.item(row, 2).text() == "999.0" and w.update_all_button.isEnabled()
    def failure(names):
        raise OSError("offline")
    monkeypatch.setattr(updates, "check_updates", failure)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: None)
    w.check_updates(["typer"]); pump(app, lambda: not w.workers)
    assert w.update_all_button.isEnabled()


def test_switch_to_empty_profile_clears_previous_rows(gui, monkeypatch):
    app, w = gui
    w.browser.model.accept(0,{"rows":[{"path":"old"}],"total":1,"cursor":None}); w.rows = [{"path": "old"}]
    w.newer.clear(); w.compare()
    assert not w.rows and w.table.model().rowCount() == 0
