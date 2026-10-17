"""Render the implemented UI with fixture metadata; no C: scan or cleanup."""
import os
os.environ['QT_QPA_PLATFORM']='offscreen'
import json
from pathlib import Path
import sys
import tempfile
import time
from PySide6.QtCore import QTimer,Qt
from PySide6.QtWidgets import QApplication,QMessageBox
from dgm import config,cleanup,__version__

project=Path(__file__).resolve().parents[1]
fixture=Path(tempfile.mkdtemp(prefix='gui140-',dir=project/'artifacts'))
config.ROOT=fixture/'app';cfg=config.defaults()
root=fixture/'disk-data';root.mkdir()
cfg['profiles']['daily']={'roots':[str(root)],'excludes':[],'engine':'native','strict_browsers':False,'threshold_mb':0}
config.save(cfg)
import dgm.background as background
background.status=lambda:{'task':{'installed':False,'enabled':False},'service':{'installed':False,'enabled':False},'scan':{'running':False}}
from test_v2 import entry,snapshot
from dgm.store import Store
from test_scope_partitions import configure
from dgm.gui import Window
from dgm.schedule import add
from dgm.logging_utils import setup
known=cleanup.rules()
for key,info in known.items():
    if not info.get('tool'):info['roots']=[str(fixture/'cache'/key)]
cleanup.rules=lambda:known
cache=fixture/'cache/pip-cache';cache.mkdir(parents=True)
for i in range(120):
    path=cache/f'wheel-{i:03}.bin';path.write_bytes(b'x'*(1000*(i+1)))
    age=time.time()-20*86400;os.utime(path,(age,age))
plan=cleanup.plan(['pip-cache','nvidia-update'],compact=True)
s=Store();logs=root/'Logs';chrome=root/'Chrome';library=root/'Library'
old=[entry(logs,'folder'),entry(logs/'service.log',value=480_000_000),entry(logs/'old.log',value=620_000_000),entry(root/'gone.bin',value=80_000_000),entry(library,'folder')]
old.extend(entry(library/f'file-{i:03}.dat',value=(i+1)*1_000_000) for i in range(60))
a=snapshot(s,root,old,name='daily');configure(s,a,strict_browsers=True)
new=[entry(logs,'folder'),entry(logs/'service.log',value=1_300_000_000),entry(logs/'old.log',value=80_000_000),entry(root/'new.bin',value=40_000_000),entry(chrome,'folder'),entry(chrome/'cache',value=2_000_000_000),entry(library,'folder')]
new.extend(entry(library/f'file-{i:03}.dat',value=(i+2)*1_000_000) for i in range(60))
b=snapshot(s,root,new,name='daily');configure(s,b,strict_browsers=False);s.close()
add({'name':'QA • Theo dõi hàng ngày','profile':'daily','mode':'daily','times':['08:00','14:00','20:00']})
add({'name':'QA • Cuối tuần','profile':'daily','mode':'weekly','weekdays':[6],'times':['09:00'],'enabled':False})
app=QApplication.instance() or QApplication([]);errors=[]
QMessageBox.warning=lambda *args:errors.append(str(args[-1]))
w=Window();w.resize(1440,1000);w.show()
def pump():
    until=time.monotonic()+20
    while w.workers and time.monotonic()<until:app.processEvents();time.sleep(.005)
    app.processEvents()
    if w.workers or errors:raise RuntimeError(str(errors) or 'Fixture UI worker did not finish')
pump();w.threshold.setValue(0);w.compare();pump()
w.cleanup_panel.loaded(plan);w.cleanup_panel.rule_boxes['pip-cache'].setChecked(True)
out=Path(os.environ.get('DGM_QA_CAPTURE_DIR',str(project/'artifacts/gui-140-implemented')));out.mkdir(exist_ok=True)
def save(index,name):
    w.tabs.setCurrentIndex(index);pump()
    for _ in range(5):app.processEvents()
    w.status.setText(f'ẢNH QA {__version__} • metadata minh họa trên D: • không quét / xóa dữ liệu C:')
    assert w.grab().save(str(out/name))
save(0,'implemented-check.png')
w.browser.sections.setCurrentIndex(1);pump();save(0,'implemented-private.png')
save(2,'implemented-cleanup.png')
w.cleanup_panel.tabs.setCurrentIndex(1);save(2,'implemented-protected.png');w.cleanup_panel.tabs.setCurrentIndex(0)
save(1,'implemented-system-update.png');w.system_tabs.setCurrentIndex(1);save(1,'implemented-system-tools.png')
w.system_tabs.setCurrentIndex(2);w.storage_panel.refresh();pump();save(1,'implemented-system-storage.png')
save(3,'implemented-schedule.png')
(config.home()/'logs/app.log').write_text('2026 INFO SCAN 2 begin (QA sample)\n2026 WARNING SCAN 2 coverage limited (QA sample)\n2026 ERROR CLEANUP changed file skipped, no deletion (QA sample)\nTraceback detail example\n2026 DEBUG command arguments example (QA sample)\n',encoding='utf-8')
w.log_panel.refresh(True);save(4,'implemented-logs.png')
from dgm.task_journal import Journal
pending=Journal('scan',{'name':'daily','p':cfg['profiles']['daily']});pending.save(state='interrupted',owner=None,percent=70,stage='Đã chụp dữ liệu; chờ tổng hợp')
w.log_panel.tasks.signature=None;w.log_panel.tasks.refresh();save(4,'implemented-resume.png')
from dgm.task_ui import CloseTasksDialog
from dgm.elevation import owner
pending.save(state='running',owner=owner())
dialog=CloseTasksDialog(w);dialog.show();app.processEvents();assert dialog.grab().save(str(out/'implemented-close-dialog.png'));dialog.reject()
pending.save(state='interrupted',owner=None)
save(5,'implemented-history.png')
w.tabs.setCurrentIndex(0);w.resize(1120,780);save(0,'implemented-compact.png')
for timer in w.findChildren(QTimer):timer.stop()
pump();w.close();app.processEvents()
print(json.dumps({'images':str(out),'fixture':str(fixture),'functional_gui_fixture':True,'production_data_unchanged':True,'errors':errors},ensure_ascii=False))
