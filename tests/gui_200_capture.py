"""Synthetic D: GUI QA screenshots only. Never enumerate real C: paths."""
import os
os.environ['QT_QPA_PLATFORM']='offscreen'
import json
from pathlib import Path
import tempfile
import time
from PySide6.QtWidgets import QApplication,QMessageBox
from dgm import config,__version__
from dgm.main_ui import MainWindow
from dgm.columnar_ingest import import_entries,metadata_for

project=Path(__file__).resolve().parents[1];out=project/'artifacts/gui-v2.0.0';out.mkdir(exist_ok=True)
config.ROOT=Path(tempfile.mkdtemp(prefix='gui-200-',dir=project/'artifacts'))
MainWindow.doctor=lambda s:None;MainWindow.refresh_background=lambda s:None
app=QApplication([]);app.setStyle('Fusion');errors=[]
QMessageBox.warning=lambda p,t,m,*_:errors.append(str(m))
window=MainWindow();repo=window.repo
def row(path,size=0,allocation=0,kind='file'):
    return {'path':path,'parent':'','kind':kind,'logical':size,'allocated':allocation,'created':1780000000.,'modified':1780000001.,'identity':'','attrs':16 if kind=='folder' else 0,'status':'ok','note':''}
folders=[row(p,kind='folder') for p in ('C:\\data','C:\\data\\logs','C:\\data\\cache')]
for day in (1,2):
    entries=folders+[row(f'C:\\data\\cache\\sample-{i:03}.bin',(i+day)*1_000_000,(i+day if i%2 else i+3-day)*1_048_576) for i in range(1,61)]
    entries += [row(f'C:\\data\\logs\\{"old" if day==1 else "new"}-{i:02}.log',20_000_000,12_582_912) for i in range(6)]
    import_entries(entries,metadata_for(['C:\\data'],profile='daily',started=f'2026-10-0{day}T00:00:00+00:00',
        coverage={'source':'native','unknown_roots':[],'error_regions':[]}),repository=repo)
def settle():
    end=time.monotonic()+30;idle=0
    while time.monotonic()<end:
        app.processEvents();time.sleep(.01);idle=idle+1 if not window.workers else 0
        if idle>=4:break
    assert not errors and not window.workers,errors
window.resize(1440,980);window.show();settle();window.setWindowTitle(window.windowTitle()+' — DỮ LIỆU MẪU QA')
window.refresh_history();window.threshold.setValue(0);window.compare();settle()
window.grab().save(str(out/'main-collapsed.png'))
window.status_details_button.setChecked(True);settle();window.workspace_splitter.setSizes([640,300]);settle()
window.grab().save(str(out/'timing-expanded.png'))
window.status_scroll.verticalScrollBar().setValue(window.status_scroll.verticalScrollBar().maximum());settle()
window.grab().save(str(out/'results-expanded.png'))
window.status_details_button.setChecked(False);window.resize(1152,700);settle()
window.grab().save(str(out/'footer-small.png'))
report={'version':__version__,'fixture_only':True,'live_C_scan':False,'title':window.windowTitle(),'errors':errors,
    'progress':window.scan_progress.value(),'footer_height':window.status_panel.height(),'details':window.completed_steps.text()}
window.close();app.processEvents();report['work_after_close']=repo.footprint()['work_bytes']
(out/'qa-result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False,indent=2))
