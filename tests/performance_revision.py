"""Compare HEAD and working-tree query costs on generated SQL, never scan a disk."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import time
import types
from contextlib import closing
import psutil
from dgm import config,__version__
from dgm.store import Store
from dgm.comparison import Comparisons
from dgm.snapshot_contract import describe

project=Path(__file__).resolve().parents[1]
base=Path(tempfile.mkdtemp(prefix='perf-approved-',dir=project/'data')).resolve()
assert base.is_relative_to((project/'data').resolve())
config.ROOT=base/'current';config.save(config.defaults())
root=r'D:\DGM-SQL-VIRTUAL-NOT-ENUMERATED'
p={'roots':[root],'engine':'native','excludes':[],'strict_browsers':False}
s=Store();a=s.begin_scan('fixture','same-scope','native',p)
def row(path,kind):
    return {'path':path,'parent':str(Path(path).parent),'kind':kind,'logical':0,'allocated':0,'created':1,'modified':1}
s.put(a,[row(root,'folder'),row(root+'\\chosen','folder'),row(root+'\\outside','folder')])
count=100000
s.db.execute('CREATE TEMP TABLE numbers(i INTEGER PRIMARY KEY)')
s.db.execute('WITH RECURSIVE n(i) AS (VALUES(0) UNION ALL SELECT i+1 FROM n WHERE i<?) INSERT INTO numbers SELECT i FROM n',(count-1,))
s.db.execute("""INSERT INTO paths(id,path,parent) SELECT i+100,
 ?||CASE WHEN i%5=0 THEN '\\outside\\f' ELSE '\\chosen\\f' END||printf('%08d',i),
 ?||CASE WHEN i%5=0 THEN '\\outside' ELSE '\\chosen' END FROM numbers""",(root,root))
s.db.execute("""INSERT INTO revisions(id,key,path_id,kind,logical,allocated,created,modified,identity,attrs,status,note)
 SELECT i+100,'first:'||i,i+100,'file',20000000+i*17,((20000000+i*17+4095)/4096)*4096,1,1,'',0,'ok','' FROM numbers""")
s.db.execute('INSERT INTO members SELECT ?,i+100,i+100 FROM numbers',(a,));s.db.commit()
def finish(sid):
    s.aggregate(sid);s.finish(sid,'complete',{**s.totals(sid),'folder_totals_source':'canonical-files-v3','contract':describe('native')})
finish(a);b=s.begin_scan('fixture','same-scope','native',p)
s.db.execute('INSERT INTO members SELECT ?,path_id,revision_id FROM members WHERE scan_id=?',(b,a))
s.db.execute('UPDATE entries SET logical=logical+1024,allocated=allocated+4096 WHERE scan_id=? AND kind=\'file\' AND path IN (SELECT path FROM paths WHERE id%100=0)',(b,));s.db.commit();finish(b)
s.close()
legacy_home=base/'baseline';legacy_home.mkdir()
(legacy_home/'data').mkdir()
with closing(sqlite3.connect(config.home()/'data/history.sqlite')) as source,closing(sqlite3.connect(legacy_home/'data/history.sqlite')) as target:source.backup(target)
raw=subprocess.run(['git','show','HEAD:app/dgm/comparison.py'],cwd=project,capture_output=True,text=True,encoding='utf-8',check=True).stdout
module=types.ModuleType('dgm._comparison_baseline');module.__package__='dgm';exec(compile(raw,'HEAD:comparison.py','exec'),module.__dict__)
report={'version':__version__,'files_per_scan':count,'sql_only':True,'os_cache_reset':False,'results':{},'fixture':str(base)}
for label,cls in (('baseline_HEAD',module.Comparisons),('working_tree',Comparisons)):
    config.ROOT=legacy_home if label=='baseline_HEAD' else base/'current'
    result={};process=psutil.Process();cpu_start=sum(process.cpu_times()[:2])
    def measure(key,function):
        started=time.perf_counter();value=function();result[key]=round(time.perf_counter()-started,5)
        print(label,key,result[key],flush=True);return value
    c=cls();pair=measure('prepare',lambda:c.prepare(a,b))
    measure('folder_scope_first20',lambda:c.page(pair,root=root+'\\chosen',kind='file'))
    for size in (20,100,1000):measure(f'global_size_{size}',lambda n=size:c.page(pair,kind='file',sort='new',limit=n))
    measure('global_name_first20',lambda:c.page(pair,kind='file',sort='path'))
    measure('folder_name_first20',lambda:c.page(pair,root=root+'\\chosen',kind='file',sort='path'))
    samples=[]
    for _ in range(5):
        started=time.perf_counter();reader=cls();reader.prepare(a,b);reader.page(pair,limit=20);reader.close();samples.append(time.perf_counter()-started)
    result['open_cached20_samples']=samples;result['open_cached20_median']=sorted(samples)[2]
    result['rss_mb']=round(process.memory_info().rss/1e6,1);result['cpu_seconds']=round(sum(process.cpu_times()[:2])-cpu_start,4)
    assert c.page(pair,kind='file')['total']==count
    c.close();report['results'][label]=result
report['note']='Same generated snapshots and scope. HEAD vs working tree; OS cache not reset, not a real-volume scan benchmark.'
config.ROOT=base/'current'
output=base/'results.json';output.write_text(json.dumps(report,indent=2),encoding='utf-8')
# Remove only the named, generated benchmark databases; keep report for review.
removed=0
for folder in (base/'current/data',base/'baseline/data'):
    for name in ('history.sqlite','history.sqlite-wal','history.sqlite-shm','comparisons.sqlite','comparisons.sqlite-wal','comparisons.sqlite-shm'):
        path=folder/name
        assert path.resolve().is_relative_to(base)
        if path.exists():removed+=path.stat().st_size;path.unlink()
print(json.dumps(report,indent=2),flush=True)
print('Report:',output,'; removed generated database MB:',round(removed/1e6,1),flush=True)
