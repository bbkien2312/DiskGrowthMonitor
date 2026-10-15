"""Render synthetic data only; does not scan C: or alter the user's config."""
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
import time
from pathlib import Path
from dgm import config

project = Path(__file__).resolve().parents[1]
config.ROOT = project / "data/revision-preview-owned"
cfg = config.defaults()
fixture = config.ROOT.parent / "revision-preview-fixture-owned"
fixture.mkdir(exist_ok=True)
file = fixture / "build-output.apk"
file.write_bytes(b"sample")
cfg["profiles"]["daily"] = {"roots": [str(fixture)], "engine": "wiztree", "strict_browsers": False, "mft": False, "threshold_mb": 0}
config.save(cfg)
from dgm.scanner import scan
scan("daily", {**cfg["profiles"]["daily"], "engine": "native"})
file.write_bytes(b"sample build output enlarged")
scan("daily", {**cfg["profiles"]["daily"], "engine": "native"})
from PySide6.QtWidgets import QApplication
from dgm.gui import Window
app = QApplication([]); w = Window(); w.resize(1320, 860); w.reset_layout(); w.scan_options_toggle.setChecked(False); w.help_boxes["scan"].toggle.setChecked(False); w.show()
deadline = time.monotonic() + 20
while w.workers and time.monotonic() < deadline:
    app.processEvents(); time.sleep(.01)
assert not w.workers
for index, name in enumerate(("scan", "system", "cleanup", "schedule", "log", "history")):
    w.tabs.setCurrentIndex(index); app.processEvents()
    if index==5:
        w.history_panel.refresh()
        while w.workers: app.processEvents(); time.sleep(.01)
    assert w.grab().save(str(config.home() / f"{name}.png"))
w.tabs.setCurrentIndex(0); w.scan_options_toggle.setChecked(True); w.help_boxes["scan"].toggle.setChecked(True); app.processEvents()
assert w.grab().save(str(config.home() / "scan-help.png"))
print("Rendered size:", w.size().width(), w.size().height(), "table height:", w.table.height())
w.close(); app.processEvents()
