import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication


def test_gui_five_tabs_and_shared_core(isolated):
    from dgm.gui import Window
    app = QApplication.instance() or QApplication([])
    window = Window()
    assert window.tabs.count() == 6
    assert not window.strict.isChecked() and window.mft.isChecked()
    assert window.threshold.value() == 10
    assert window.rule_boxes["pip-cache"].isChecked() is False
    import time
    deadline = time.monotonic() + 5
    while window.workers and time.monotonic() < deadline:
        app.processEvents(); time.sleep(0.01)
    assert not window.workers
    window.close()
