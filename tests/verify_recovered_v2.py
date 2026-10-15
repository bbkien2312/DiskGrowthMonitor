"""Verify existing imported history only. No filesystem scans."""
import json
import time
from dgm.config import home
from dgm.store import Store
from dgm.comparison import Comparisons
from dgm.presentation import scan_label

store=Store(readonly=True)
old=store.scan(27);new=store.scan(28)
assert old['status']=='cancelled' and new['status']=='partial'
assert json.loads(new['summary'])['recovered']
assert old['started']==new['started']
store.db.execute('ATTACH DATABASE ? AS backup',(f"file:{(home()/'data/backups/history-before-020.sqlite').as_posix()}?mode=ro",))
changed=store.db.execute('SELECT COUNT(*) FROM members m LEFT JOIN backup.members b ON b.scan_id=m.scan_id AND b.path_id=m.path_id WHERE m.scan_id=27 AND (b.revision_id IS NULL OR m.revision_id<>b.revision_id)').fetchone()[0]
assert changed==0
assert store.db.execute('SELECT COUNT(*) FROM members WHERE scan_id=27').fetchone()[0]==967292
root=store.db.execute("SELECT r.logical,r.allocated FROM members m JOIN paths p ON p.id=m.path_id JOIN revisions r ON r.id=m.revision_id WHERE m.scan_id=28 AND p.path='C:\\'").fetchone()
total=store.totals(28);assert root['logical']==total['logical'] and root['allocated']==total['allocated']
print('Verified',scan_label(new),total,'source_members_unchanged',changed,flush=True)
cache=Comparisons();start=time.perf_counter();pair=cache.prepare(None,28)
print('real_baseline_index_seconds',round(time.perf_counter()-start,4),flush=True)
start=time.perf_counter();page=cache.page(pair,minimum=10_000_000)
print('real_first_20_seconds',round(time.perf_counter()-start,4),'total',page['total'],flush=True)
start=time.perf_counter();page=cache.page(pair,minimum=10_000_000,cursor=page['cursor'])
print('real_next_20_seconds',round(time.perf_counter()-start,4),flush=True)
cache.close();store.close()
