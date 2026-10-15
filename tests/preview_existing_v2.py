"""Render current history with no scan, no config/UI preference overwrite."""
import os
os.environ['QT_QPA_PLATFORM']='offscreen'
import time
from PySide6.QtWidgets import QApplication,QMessageBox
from dgm.config import home
from dgm.gui import Window

def fail(*args):raise RuntimeError(str(args[-1]))
QMessageBox.warning=fail
app=QApplication([]);w=Window();w.resize(1440,940);w.show()
until=time.monotonic()+60
while w.workers and time.monotonic()<until:app.processEvents();time.sleep(.01)
assert not w.workers
assert w.newer.currentData()==28 and w.browser.model.loaded==20
assert w.browser.model.total==228907
assert 'Đã sẵn sàng' in w.status.text()
output=home()/'data/v2-preview';output.mkdir(exist_ok=True)
for index,name in enumerate(('scan','system','cleanup','schedule','log','history')):
    w.tabs.setCurrentIndex(index);app.processEvents()
    if index==5:
        w.history_panel.refresh()
        until=time.monotonic()+20
        while w.workers and time.monotonic()<until:app.processEvents();time.sleep(.01)
        assert not w.workers
    assert w.grab().save(str(output/(name+'.png')))
w.tabs.setCurrentIndex(0);app.processEvents()
print('real-history rendered: 20 rows /',w.browser.model.total,'table height',w.table.height(),flush=True)
# Do not call Window.close: that persists UI preferences. No scan is active.
w.poll_timer.stop();w.scan_state_timer.stop();w.tray.hide();w.hide();w.deleteLater();app.processEvents()
