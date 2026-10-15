"""One million synthetic SQL records on D only; never enumerates source files."""
import json
import threading
import time
from pathlib import Path
import psutil
from dgm import config
from dgm.store import Store
from dgm.comparison import Comparisons
from dgm.platform_ops import ProcessCancelled

config.ROOT=Path(__file__).resolve().parents[1]/'data/performance-v2-release-owned'
config.save(config.defaults())
report={}; process=psutil.Process(); peak=[0]; finished=threading.Event()
def memory():
    while not finished.wait(.05): peak[0]=max(peak[0],process.memory_info().rss)
threading.Thread(target=memory,daemon=True).start()
def measure(name,fn):
    start=time.perf_counter(); cpu=process.cpu_times(); result=fn()
    report[name]={'seconds':round(time.perf_counter()-start,4),'cpu_seconds':round(sum(process.cpu_times()[:2])-sum(cpu[:2]),4),'peak_rss_mb':round(peak[0]/1e6,1)}
    print(name,report[name],flush=True); return result
store=Store()
if store.scans(): raise RuntimeError('Benchmark home already contains data; choose a new explicitly owned directory.')
p={'roots':[r'D:\DGM-SYNTHETIC-NOT-REAL'],'engine':'native','strict_browsers':True}
old=store.begin_scan('synthetic','fixture','native',p)
def generate():
    store.db.execute('CREATE TEMP TABLE numbers(i INTEGER PRIMARY KEY)')
    store.db.execute('WITH RECURSIVE n(i) AS (VALUES(0) UNION ALL SELECT i+1 FROM n WHERE i<999999) INSERT INTO numbers SELECT i FROM n')
    store.db.execute(r"""INSERT INTO paths(id,path,parent) SELECT i+1,
      CASE WHEN i=0 THEN 'D:\DGM-SYNTHETIC-NOT-REAL' WHEN i%2=0 THEN 'D:\DGM-SYNTHETIC-NOT-REAL\d1\d'||i ELSE 'D:\DGM-SYNTHETIC-NOT-REAL\d'||i END,
      CASE WHEN i>0 AND i%2=0 THEN 'D:\DGM-SYNTHETIC-NOT-REAL\d1' ELSE 'D:\DGM-SYNTHETIC-NOT-REAL' END FROM numbers WHERE i<220000""")
    store.db.execute(r"""INSERT INTO paths(id,path,parent) SELECT n.i+1,p.path||'\f'||n.i,p.path
      FROM numbers n JOIN paths p ON p.id=(n.i-220000)%219999+2 WHERE n.i>=220000""")
    store.db.execute("""INSERT INTO revisions SELECT i+1,'fixture:'||i,i+1,CASE WHEN i<220000 THEN 'folder' ELSE 'file' END,
      CASE WHEN i<220000 THEN 0 ELSE 20000000+i%17*1000 END,CASE WHEN i<220000 THEN 0 ELSE 20000000+i%17*1000 END,
      1,1,'native:'||i,0,'ok','' FROM numbers""")
    store.db.execute('INSERT INTO members SELECT ?,i+1,i+1 FROM numbers',(old,));store.db.commit()
measure('generate_1000000',generate)
measure('aggregate_1000000',lambda:store.aggregate(old))
store.finish(old,'complete',store.totals(old))
new=store.begin_scan('synthetic','fixture','native',p)
def edit():
    store.db.execute('INSERT INTO members SELECT ?,path_id,revision_id FROM members WHERE scan_id=?',(new,old))
    store.db.execute("""INSERT INTO revisions(key,path_id,kind,logical,allocated,created,modified,identity,attrs,status,note)
      SELECT 'edited:'||r.id,r.path_id,r.kind,r.logical+1024,r.allocated+1024,r.created,2,r.identity,r.attrs,r.status,r.note
      FROM revisions r JOIN members m ON m.revision_id=r.id WHERE m.scan_id=? AND r.kind='file' AND r.path_id%13=0""",(old,))
    store.db.execute("""UPDATE members SET revision_id=(SELECT id FROM revisions WHERE key='edited:'||members.revision_id)
      WHERE scan_id=? AND path_id>220000 AND path_id%13=0""",(new,))
    store.db.execute('DELETE FROM members WHERE scan_id=? AND path_id>220000 AND path_id%37=0',(new,));store.db.commit()
    store.aggregate(new);store.finish(new,'complete',store.totals(new))
measure('edit_and_aggregate',edit)
cache=Comparisons()
pair=measure('prepare_comparison_1000000',lambda:cache.prepare(old,new))
summary=cache.summary(pair);assert summary['rows']==1000000
page=measure('first_20',lambda:cache.page(pair,limit=20,minimum=10000000))
measure('next_20',lambda:cache.page(pair,limit=20,minimum=10000000,cursor=page['cursor']))
measure('warm_first_20',lambda:cache.page(pair,limit=20,minimum=10000000))
measure('file_sort_new_first_20',lambda:cache.page(pair,limit=20,kind='file',sort='new'))
measure('whole_root_20',lambda:cache.page(pair,root=p['roots'][0],limit=20))
branch=p['roots'][0]+r'\d1'
measure('big_branch_first_20',lambda:cache.page(pair,root=branch,limit=20))
measure('big_branch_warm_20',lambda:cache.page(pair,root=branch,limit=20))
measure('tree_20_folders',lambda:cache.page(pair,root=p['roots'][0],recursive=False,folders_only=True,sort='new',limit=20))
measure('path_index_once',lambda:cache.page(pair,sort='path',limit=20))
measure('path_warm_20',lambda:cache.page(pair,sort='path',limit=20))
stop=threading.Event();cancel=Comparisons(stop);timer=threading.Timer(.25,stop.set);timer.start()
start=time.perf_counter()
try:
    cancel.prepare(None,new); raise AssertionError('Cancellation did not interrupt SQL')
except ProcessCancelled: report['cancel_sql_seconds']=round(time.perf_counter()-start,4)
finally:timer.cancel();cancel.close()
report['counts']=summary['counts']; report['rows']=summary['rows']
report['database_mb']=round(store.path.stat().st_size/1e6,1);report['cache_mb']=round(cache.path.stat().st_size/1e6,1)
cache.close();store.close();finished.set()
print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)
