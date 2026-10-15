"""200k virtual SQL records, no disk enumeration. Removes only its generated DBs."""
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import psutil
os.environ['QT_QPA_PLATFORM']='offscreen'
from dgm import config
from dgm.store import Store
from dgm.comparison import Comparisons

repository=Path(__file__).resolve().parents[1]
base=Path(tempfile.mkdtemp(prefix='performance-v11-',dir=repository/'data')).resolve()
assert base.is_relative_to((repository/'data').resolve())
config.ROOT=base
profile={'roots':[r'D:\DGM-V11-VIRTUAL-NOT-ENUMERATED'],'engine':'native','strict_browsers':True,'threshold_mb':10,'mft':False}
cfg=config.defaults();cfg['profiles']['daily']=profile;config.save(cfg)
os.environ['TEMP']=os.environ['TMP']=str(config.home()/'data/tmp')
store=Store();process=psutil.Process();peak=[0];done=threading.Event()
def memory():
    while not done.wait(.02):peak[0]=max(peak[0],process.memory_info().rss)
monitor=threading.Thread(target=memory,daemon=True);monitor.start()
report={'version':'1.1.0','records':200000,'synthetic_sql_only':True,'os_cache_reset':False,'timings':{}}
def measure(label,fn):
    start=time.perf_counter();result=fn();report['timings'][label]=round(time.perf_counter()-start,4)
    print(label,report['timings'][label],flush=True);return result
old=store.begin_scan('daily','v11-fixture','native',profile)
def generate():
    store.db.execute('CREATE TEMP TABLE numbers(i INTEGER PRIMARY KEY)')
    store.db.execute('WITH RECURSIVE n(i) AS (VALUES(0) UNION ALL SELECT i+1 FROM n WHERE i<199999) INSERT INTO numbers SELECT i FROM n')
    store.db.execute(r"""INSERT INTO paths(id,path,parent) SELECT i+1,
      CASE i WHEN 0 THEN 'D:\DGM-V11-VIRTUAL-NOT-ENUMERATED' WHEN 1 THEN 'D:\DGM-V11-VIRTUAL-NOT-ENUMERATED\chosen'
      WHEN 2 THEN 'D:\DGM-V11-VIRTUAL-NOT-ENUMERATED\outside' WHEN 3 THEN 'D:\DGM-V11-VIRTUAL-NOT-ENUMERATED\chosen\nested'
      ELSE 'D:\DGM-V11-VIRTUAL-NOT-ENUMERATED\'||CASE WHEN i%3<>0 THEN 'outside' WHEN i%5=0 THEN 'chosen\nested' ELSE 'chosen' END||'\f'||printf('%08d',i) END,
      CASE WHEN i=3 THEN 'D:\DGM-V11-VIRTUAL-NOT-ENUMERATED\chosen' WHEN i<4 THEN 'D:\DGM-V11-VIRTUAL-NOT-ENUMERATED'
      ELSE 'D:\DGM-V11-VIRTUAL-NOT-ENUMERATED\'||CASE WHEN i%3<>0 THEN 'outside' WHEN i%5=0 THEN 'chosen\nested' ELSE 'chosen' END END FROM numbers""")
    store.db.execute("""INSERT INTO revisions SELECT i+1,'initial:'||i,i+1,CASE WHEN i<4 THEN 'folder' ELSE 'file' END,
      CASE WHEN i<4 THEN 0 ELSE 20000000+(i*7919)%1000000 END,CASE WHEN i<4 THEN 0 ELSE 20000000+(i*7919)%1000000 END,1,1,'',0,'ok','' FROM numbers""")
    store.db.execute('INSERT INTO members SELECT ?,i+1,i+1 FROM numbers',(old,));store.db.commit()
    store.aggregate(old);store.finish(old,'complete',store.totals(old))
measure('generate_and_aggregate',generate)
new=store.begin_scan('daily','v11-fixture','native',profile)
store.db.execute('INSERT INTO members SELECT ?,path_id,revision_id FROM members WHERE scan_id=?',(new,old));store.db.commit()
store.finish(new,'complete',store.totals(new))
cache=Comparisons();pair=measure('prepare_pair',lambda:cache.prepare(old,new))
branch=profile['roots'][0]+r'\chosen'
measure('folder_name_first_20',lambda:cache.page(pair,root=branch,sort='path',direction='asc',kind='file'))
assert cache.db.execute('SELECT names_ready FROM pairs WHERE id=?',(pair,)).fetchone()[0]==0
report['folder_sort_left_global_name_index_unbuilt']=True
for count in (20,100,1000):
    measure(f'folder_name_warm_{count}',lambda n=count:cache.page(pair,root=branch,sort='path',direction='asc',kind='file',limit=n))
    measure(f'global_size_{count}',lambda n=count:cache.page(pair,sort='new',kind='file',limit=n))
measure('global_name_first_20',lambda:cache.page(pair,sort='path',direction='asc',limit=20))
from PySide6.QtWidgets import QApplication,QMessageBox
from dgm.gui import Window
QMessageBox.warning=lambda *args:(_ for _ in ()).throw(RuntimeError(str(args[-1])))
app=QApplication([]);window=Window();window.show()
def settle():
    deadline=time.monotonic()+60
    while window.workers and time.monotonic()<deadline:app.processEvents();time.sleep(.002)
    app.processEvents()
    assert not window.workers,'GUI jobs timed out'
settle();browser=window.browser
browser.kind.setCurrentIndex(browser.kind.findData('file'));browser.reload();settle()
assert browser.model.loaded==20
for count,expected in ((100,120),(1000,1120)):
    browser.page_size.setCurrentIndex(browser.page_size.findData(count))
    def load():browser.load_more();settle()
    measure(f'gui_load_{count}',load);assert browser.model.loaded==expected
def sort_rows():browser.sort_clicked(3);settle()
measure('gui_resort_keep_1120',sort_rows);assert browser.model.loaded==1120
assert sum(map(len,browser.model.chunks.values()))<=2000
report['gui_cache_limit_rows']=browser.model.cache_limit
window.grab().save(str(base/'gui-1120.png'));window.close();app.processEvents()
cache.close();store.close();done.set();monitor.join(timeout=1)
report['peak_process_rss_mb']=round(peak[0]/1e6,1)
report['note']='SQL/GUI timings on synthetic records; first index costs included where labelled. Not a guarantee for real volumes. No user disk scanned.'
output=repository/'data/performance-v11-results.json';output.write_text(json.dumps(report,indent=2),encoding='utf-8')
# Explicit known files, validated workspace above. No recursive deletion.
removed=0
for name in ('history.sqlite','history.sqlite-wal','history.sqlite-shm','comparisons.sqlite','comparisons.sqlite-wal','comparisons.sqlite-shm'):
    path=base/'data'/name
    if path.exists():removed+=path.stat().st_size;path.unlink()
print(json.dumps(report,indent=2),flush=True)
print(f'Removed {removed/1e6:.1f} MB generated metadata; recreate with this script. Preview/log directory: {base}',flush=True)
