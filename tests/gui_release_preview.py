import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtWidgets import QApplication
from dgm.config import ROOT
from dgm.gui import Window

app = QApplication([])
w = Window()
w.resize(1600, 940)
w.show()
app.processEvents()
for index, name in enumerate(("scan", "system", "cleanup", "schedule", "log")):
    w.tabs.setCurrentIndex(index)
    app.processEvents()
    assert w.grab().save(str(ROOT / "data" / f"gui-{name}.png"))
print('GUI tabs rendered; snapshot', w.newer.currentData(), 'rows',w.table.rowCount())
w.close()
