import json
import threading
from pathlib import Path
import pytest
from dgm.store import Store
from dgm.comparison import Comparisons
from dgm.config import defaults, home, Policy


def entry(path,kind='file',value=0,identity='',modified=1):
    path=Path(path)
    return {'path':str(path),'parent':str(path.parent),'kind':kind,'logical':value,'allocated':value,
            'created':1,'modified':modified,'identity':identity,'attrs':0,'status':'ok','note':''}


def snapshot(store,root,items,name='demo',status='complete',coverage=False,engine='native',aggregate=True,root_value=0):
    p={'roots':[str(root)],'engine':engine,'strict_browsers':True}
    sid=store.begin_scan(name,'same-scope',engine,p)
    store.put(sid,[entry(root,'folder',root_value),*items])
    if aggregate:store.aggregate(sid)
    store.finish(sid,status,{**store.totals(sid),'coverage_unverified':coverage})
    return sid


def test_default_daily_profile_is_full_drive_fast_wiztree_and_includes_browser_metadata():
    p=defaults()['profiles']['daily']
    assert p['roots']==['C:\\'] and p['engine']=='wiztree' and p['mft'] is True
    assert p['strict_browsers'] is False and p['excludes']==[]
    assert not Policy(p).excluded(r'C:\Users\example\AppData\Local\Google\Chrome\User Data')


def test_history_gc_indexes_path_members_before_collecting_orphans(isolated):
    s=Store();root=isolated/'virtual';sid=snapshot(s,root,[entry(root/'orphan.bin',value=25)])
    s.db.execute('DELETE FROM scans WHERE id=?',(sid,));s.db.commit()
    events=[];s.gc(notify=lambda event,data:events.append((event,data)))
    plan=s.db.execute('EXPLAIN QUERY PLAN SELECT 1 FROM members WHERE path_id=?',(1,)).fetchall()
    assert any('members_path' in row[3] for row in plan)
    assert s.db.execute('SELECT 1 FROM paths WHERE path=?',(str(root/'orphan.bin'),)).fetchone() is None
    assert any('tối ưu chỉ mục' in data['message'] for _,data in events)
    s.close()


@pytest.fixture
def dataset(isolated):
    root=isolated/'not-enumerated'
    s=Store();a=snapshot(s,root,[entry(root/f'f{i:03}',value=11_000_000+i*10) for i in range(95)])
    b=snapshot(s,root,[entry(root/f'f{i:03}',value=11_000_000+i*20) for i in range(1,100)])
    cache=Comparisons();pair=cache.prepare(a,b)
    yield s,cache,pair,root,a,b
    cache.close();s.close()


@pytest.mark.parametrize('sort',['delta','old','new','allocated','created','modified','kind','change','path'])
@pytest.mark.parametrize('direction',['asc','desc'])
def test_cursor_full_global_order_and_nulls(dataset,sort,direction):
    s,c,pair,root,a,b=dataset;rows=[];cursor=None
    while True:
        page=c.page(pair,limit=20,cursor=cursor,sort=sort,direction=direction)
        assert len(page['rows'])<=20 and page['total']==101
        rows+=page['rows'];cursor=page['cursor']
        if not cursor:break
    assert len(rows)==101 and len({r['path_id'] for r in rows})==101
    values=[(r['_key'],r['path_id']) for r in rows]
    assert values==sorted(values,reverse=direction=='desc')


def test_summary_complete_paging_does_not_rebuild(dataset):
    s,c,pair,root,a,b=dataset;trace=[];c.db.set_trace_callback(trace.append)
    first=c.page(pair,kind='file');second=c.page(pair,kind='file',cursor=first['cursor'])
    assert c.prepare(a,b)==pair
    assert c.summary(pair)['rows']==101 and len(second['rows'])==20
    assert sum('SELECT COUNT(*) FROM' in q for q in trace)==0
    assert not any(q.startswith('INSERT INTO diff_rows') for q in trace)
    assert set(r['path_id'] for r in first['rows']).isdisjoint(r['path_id'] for r in second['rows'])
    with pytest.raises(ValueError,match='Con trỏ'):
        c.page(pair,cursor=first['cursor'],kind='folder')


def test_threshold_and_literal_search(dataset):
    s,c,pair,root,a,b=dataset
    page=c.page(pair,kind='file',minimum=11_001_800)
    assert page['total']==9 and all(max(r['before'] or 0,r['after'] or 0)>11_001_800 for r in page['rows'])
    assert c.page(pair,search='%')['total']==0


def test_recursive_scope_tree_children_and_independent_totals(isolated):
    root=isolated/'virtual';s=Store()
    items=[entry(root/f'd{i}','folder') for i in range(45)]+[entry(root/'d1'/'nested','folder'),entry(root/'d1'/'nested'/'file',value=20)]
    a=snapshot(s,root,items);items[-1]['logical']=50;items[-1]['allocated']=50;b=snapshot(s,root,items)
    s.db.execute('UPDATE scans SET started=(SELECT started FROM scans WHERE id=?) WHERE id=?',(a,b));s.db.commit()
    assert s.previous(b)==a
    c=Comparisons();pair=c.prepare(a,b)
    first=c.page(pair,root=str(root),recursive=False,folders_only=True,sort='new')
    assert first['total']==45 and len(first['rows'])==20
    second=c.page(pair,root=str(root),recursive=False,folders_only=True,sort='new',cursor=first['cursor'])
    assert len(second['rows'])==20
    branch=c.page(pair,root=str(root/'d1'))
    assert branch['total']==3 and c.summary(pair)['rows']==48
    c.page(pair,root=str(root))
    assert c.db.execute('SELECT COUNT(*) FROM scopes').fetchone()[0]==1
    c.page(pair,root=str(root/'d1'),sort='path')
    assert c.page(pair,root=str(root/'d1'))['total']==3
    c.close();s.close()


def test_recursive_query_plan_indexes_each_parent(dataset):
    s,c,pair,root,a,b=dataset
    plan=c.db.execute('EXPLAIN QUERY PLAN WITH RECURSIVE children(id) AS (VALUES(1) UNION ALL SELECT c.path_id FROM children d CROSS JOIN diff_rows c INDEXED BY diff_parent ON c.pair_id=? AND c.parent_id=d.id WHERE c.path_id<>d.id) SELECT count(*) FROM children',(pair,)).fetchall()
    assert any('parent_id=?' in r[3] for r in plan)


def test_ordinals_persist_ids_not_reused_same_profile_previous(isolated):
    s=Store();root=isolated/'virtual';a=snapshot(s,root,[]);b=snapshot(s,root,[])
    s.db.execute('DELETE FROM scans WHERE id=?',(b,));s.db.commit();s.close();s=Store()
    c=snapshot(s,root,[]);other=snapshot(s,root,[],name='other');d=snapshot(s,root,[])
    assert s.scan(c)['ordinal']==3 and c>b
    assert s.previous(d)==c and s.previous(other) is None
    s.close()


def test_empty_snapshot_totals_are_zero(isolated):
    s=Store();sid=s.begin_scan('empty','scope','native',{'roots':[]})
    assert s.totals(sid)=={'logical':0,'allocated':0,'files':0,'folders':0,'links':0};s.close()


def test_cache_archive_history_preserved_and_rebuilt(dataset):
    s,c,pair,root,a,b=dataset;summary=c.summary(pair)
    c.trim(target_pairs=0)
    assert c.history()[0]['status']=='archived' and c.summary(pair)==summary
    assert c.prepare(a,b)==pair and c.page(pair)['total']==101
    c.delete(pair);assert not c.history()
    assert s.totals(a)['files']==95


def test_concurrent_cache_schema_upgrade(dataset):
    from concurrent.futures import ThreadPoolExecutor
    s,c,pair,root,a,b=dataset
    root_id=c.db.execute('SELECT id FROM source.paths WHERE path=?',(str(root),)).fetchone()[0]
    c.scope(pair,root_id)
    c.db.execute('DROP INDEX scope_delta_key');c.db.execute('ALTER TABLE scope_items DROP COLUMN delta_key');c.db.commit()
    def open_cache(_):
        cache=Comparisons()
        try:
            assert 'delta_key' in [r[1] for r in cache.db.execute('PRAGMA table_info(scope_items)')]
            return cache.page(pair)['total']
        finally:cache.close()
    with ThreadPoolExecutor(max_workers=4) as pool:assert list(pool.map(open_cache,range(4)))==[101]*4
    assert c.db.execute('SELECT COUNT(*) FROM scope_items WHERE delta_key IS NULL').fetchone()[0]==0


def test_external_new_missing_unknown_and_errors(isolated):
    s=Store();root=isolated/'virtual'
    a=snapshot(s,root,[entry(root/'old',value=30)],coverage=True)
    b=snapshot(s,root,[entry(root/'new',value=50)],coverage=True)
    c=Comparisons();pair=c.prepare(a,b);rows=c.page(pair)['rows']
    files=[r for r in rows if r['kind']=='file']
    assert all(r['change']=='unknown' and r['delta'] is None for r in files)
    c.close();s.close()


def test_cross_engine_case_variants_and_provider_folder_totals_normalize(isolated):
    root=isolated/'virtual';s=Store()
    a=snapshot(s,root,[entry(root/'Dir','folder',900),entry(root/'Dir'/'File.bin',value=100)],
               engine='wiztree',aggregate=False,root_value=900)
    b=snapshot(s,root,[entry(root/'dir','folder',700),entry(root/'dir'/'file.BIN',value=100)],
               engine='native',aggregate=False,root_value=700)
    c=Comparisons();pair=c.prepare(a,b)
    files=c.page(pair,kind='file')['rows'];folders=c.page(pair,kind='folder')['rows']
    assert len(files)==1 and files[0]['change']=='same' and files[0]['delta']==0
    folder=next(row for row in folders if row['path'].casefold()==str(root/'dir').casefold())
    assert folder['before']==100 and folder['after']==100 and folder['delta']==0 and folder['change']=='same'
    assert folder['other_path'] is None
    direct=c.page(pair,root=str(root/'DIR'),recursive=False,folders_only=True)
    assert direct['total']==0
    tree=c.page(pair,root=str(root),recursive=False,folders_only=True)
    assert [row['path'] for row in tree['rows']]==[str(root/'dir')]
    c.close();s.close()


def test_current_wiztree_pair_uses_exported_folder_totals_without_normalizing(isolated):
    root=isolated/'virtual';s=Store()
    old=snapshot(s,root,[entry(root/'Dir','folder',900),entry(root/'Dir'/'File.bin',value=100)],
                 engine='wiztree',aggregate=False,root_value=1000)
    new=snapshot(s,root,[entry(root/'Dir','folder',800),entry(root/'Dir'/'File.bin',value=100)],
                 engine='wiztree',aggregate=False,root_value=900)
    for scan_id in (old,new):
        row=s.scan(scan_id);summary=json.loads(row['summary']);summary['folder_totals_source']='wiztree-mft-v1';s.record_stage(scan_id,summary)
    c=Comparisons();pair=c.prepare(old,new)
    folder=next(row for row in c.page(pair,kind='folder')['rows'] if row['path']==str(root/'Dir'))
    assert folder['before']==900 and folder['after']==800 and folder['delta']==-100
    assert c.db.execute('SELECT COUNT(*) FROM normalized_scans').fetchone()[0]==0
    c.close();s.close()


def test_delete_multiple_snapshots_removes_dependent_pairs_and_keeps_other_snapshots(isolated):
    root=isolated/'virtual';s=Store()
    a=snapshot(s,root,[entry(root/'a.bin',value=10)])
    b=snapshot(s,root,[entry(root/'b.bin',value=20)])
    c=snapshot(s,root,[entry(root/'c.bin',value=30)])
    cache=Comparisons();cache.prepare(a,b);cache.prepare(b,c)
    assert cache.delete_for_scans([b])==2
    assert s.delete_scans([b])==1
    assert [row['id'] for row in s.scans()]==[c,a]
    assert s.db.execute('SELECT COUNT(*) FROM members WHERE scan_id=?',(b,)).fetchone()[0]==0
    assert cache.history()==[]
    with pytest.raises(ValueError,match='Không tìm thấy lượt quét'):s.delete_scans([b])
    cache.close();s.close()


def test_delete_scans_refuses_running_snapshot(isolated):
    s=Store();scan_id=s.begin_scan('daily','scope','native',{'roots':['C:\\']})
    with pytest.raises(ValueError,match='đang chạy'):s.delete_scans([scan_id])
    assert s.scan(scan_id)['status']=='running'
    s.close()


def test_allocated_delta_is_separate_from_logical_delta(isolated):
    root=isolated/'virtual';s=Store()
    old=entry(root/'file',value=100);old['allocated']=4096
    new=entry(root/'file',value=100);new['allocated']=8192
    a=snapshot(s,root,[old]);b=snapshot(s,root,[new])
    c=Comparisons();pair=c.prepare(a,b);row=c.page(pair,kind='file',sort='allocated')['rows'][0]
    assert row['delta']==0 and row['allocated_before']==4096 and row['allocated_after']==8192
    assert row['allocated_delta']==4096 and row['change']=='modified'
    c.close();s.close()


def test_case_collisions_are_unknown_not_arbitrarily_matched(isolated):
    root=isolated/'virtual';s=Store()
    a=snapshot(s,root,[entry(root/'File',value=10)])
    b=snapshot(s,root,[entry(root/'File',value=20),entry(root/'file',value=30)],aggregate=False)
    c=Comparisons();pair=c.prepare(a,b)
    rows=c.page(pair,kind='file')['rows']
    assert len(rows)==2 and all(row['change']=='unknown' and row['delta'] is None for row in rows)
    c.close();s.close()


def test_clear_comparison_cache_preserves_snapshots_and_history_clear_is_separate(isolated):
    root=isolated/'virtual';s=Store();sid=snapshot(s,root,[entry(root/'file',value=10)])
    c=Comparisons();pair=c.prepare(None,sid)
    assert c.history() and s.scans()
    s.close()
    c.clear_all()
    assert c.history()==[]
    c.close()
    s=Store(readonly=True);assert s.scan(sid)['id']==sid;s.close()
    s=Store();assert s.db.execute('SELECT COUNT(*) FROM sequence_counters').fetchone()[0]>=1
    s.clear_scans()
    assert s.scans()==[] and s.db.execute('SELECT COUNT(*) FROM scan_numbers').fetchone()[0]==0
    next_id=s.begin_scan('demo','same-scope','native',{'roots':[str(root)]})
    assert next_id>sid and s.scan(next_id)['ordinal']==2
    s.close()


def test_legacy_store_migrates_scan_id_counter_without_losing_rows(isolated):
    import sqlite3
    database=home()/'data/history.sqlite';database.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(database)
    db.executescript("""
      CREATE TABLE scans(id INTEGER PRIMARY KEY,profile TEXT,scope TEXT,engine TEXT,started TEXT,finished TEXT,status TEXT,config TEXT,summary TEXT);
      CREATE TABLE paths(id INTEGER PRIMARY KEY,path TEXT UNIQUE,parent TEXT);
      CREATE TABLE revisions(id INTEGER PRIMARY KEY,key TEXT UNIQUE,path_id INTEGER,kind TEXT,logical INTEGER,allocated INTEGER,created REAL,modified REAL,identity TEXT,attrs INTEGER,status TEXT,note TEXT);
      CREATE TABLE members(scan_id INTEGER,path_id INTEGER,revision_id INTEGER,PRIMARY KEY(scan_id,path_id));
      INSERT INTO scans VALUES(1,'legacy','scope','native','2026-01-01','2026-01-01','complete','{}','{}');
      INSERT INTO paths VALUES(1,'C:\\Fixture\\File.bin','C:\\Fixture');
      INSERT INTO revisions VALUES(1,'old-key',1,'file',10,10,1,1,'',0,'ok','');
      INSERT INTO members VALUES(1,1,1);
    """);db.close()
    store=Store()
    assert store.scan(1)['profile']=='legacy'
    second=store.begin_scan('legacy','scope','native',{})
    store.put(second,[entry(r'C:\Fixture\New.bin',value=20)])
    assert second==2 and store.db.execute('SELECT COUNT(*) FROM members WHERE scan_id=?',(second,)).fetchone()[0]==1
    store.close()


def test_recovery_preserves_cancelled_source_and_time(isolated):
    from dgm.recovery import rebuild
    s=Store();root=isolated/'virtual';sid=snapshot(s,root,[entry(root/'f',value=45)],status='cancelled')
    original=s.scan(sid);old_revision=s.db.execute('SELECT revision_id FROM members WHERE scan_id=?',(sid,)).fetchall()
    with pytest.raises(ValueError,match='bằng chứng'):rebuild(sid)
    result=rebuild(sid,confirmed=True)
    assert result['logical']==45 and result['recovered'] and result['coverage_unverified']
    assert result['storage']['operation']=='scan' and 'không thực hiện quét mới' in result['coverage_note']
    assert s.scan(sid)==original and s.scan(result['scan_id'])['started']==original['started']
    assert s.db.execute('SELECT revision_id FROM members WHERE scan_id=?',(sid,)).fetchall()==old_revision
    s.close()


def test_recovery_stop_before_copy_leaves_no_extra_scan(isolated):
    from dgm.recovery import rebuild
    from dgm.platform_ops import ProcessCancelled
    s=Store();root=isolated/'virtual';sid=snapshot(s,root,[entry(root/'f',value=45)],status='cancelled')
    stop=threading.Event();stop.set()
    with pytest.raises(ProcessCancelled):rebuild(sid,confirmed=True,stop=stop)
    assert len(s.scans())==1;s.close()


def test_recovery_stop_in_aggregation_then_retry(isolated,monkeypatch):
    from dgm.recovery import rebuild
    s=Store();root=isolated/'virtual';sid=snapshot(s,root,[entry(root/'f',value=45)],status='cancelled')
    stop=threading.Event()
    def notify(event,data):
        if event=='info' and 'hardlink' in data.get('message',''):stop.set()
    result=rebuild(sid,confirmed=True,stop=stop,notify=notify)
    assert result['status']=='cancelled' and s.scan(sid)['status']=='cancelled'
    assert rebuild(sid,confirmed=True)['status']=='partial';s.close()


def test_fast_mft_uses_provider_totals_without_native_stat(isolated,monkeypatch):
    import dgm.scanner as scanner
    root=isolated/'virtual';p={'roots':[str(root)],'engine':'wiztree','mft':True,'strict_browsers':False}
    monkeypatch.setattr(scanner,'is_admin',lambda:True)
    def provider(policy,cfg,stop,notify):
        notify('overview',{'root':str(root),'logical':200,'allocated':220,'trusted':True})
        yield entry(root,'folder',200)
        yield entry(root/'f',value=200)
    monkeypatch.setattr(scanner,'wiztree_entries',provider)
    monkeypatch.setattr(Store,'aggregate',lambda *a,**k:pytest.fail('Must not recompute WizTree folder sums'))
    monkeypatch.setattr(scanner,'metadata',lambda *a:pytest.fail('Must not stat every MFT file'))
    events=[];result=scanner.scan('demo',p,notify=lambda e,d:events.append(e))
    assert result['logical']==200 and result['import_complete'] and 'overview' in events


def test_cli_cursor_and_global_summary(dataset):
    from typer.testing import CliRunner
    from dgm.cli import app
    s,c,pair,root,a,b=dataset
    runner=CliRunner();result=runner.invoke(app,['compare',str(b),'--older',str(a),'--minimum-mb','0'])
    assert result.exit_code==0,result.output
    first=json.loads(result.output);assert len(first['rows'])==20 and first['summary']['rows']==101
    result=runner.invoke(app,['compare',str(b),'--older',str(a),'--minimum-mb','0','--cursor',json.dumps(first['cursor'])])
    assert result.exit_code==0,result.output
    assert not set(r['path_id'] for r in first['rows'])&set(r['path_id'] for r in json.loads(result.output)['rows'])


def test_cli_log_level_filter_shared_multiline(isolated):
    from typer.testing import CliRunner
    from dgm.cli import app
    (home()/'logs/app.log').write_text('2026 DEBUG detail\n2026 INFO summary\n2026 ERROR fail\nTraceback continuation\n',encoding='utf-8')
    result=CliRunner().invoke(app,['logs','--level','error'])
    assert result.exit_code==0 and 'ERROR' in result.output and 'Traceback continuation' in result.output
    assert 'DEBUG' not in result.output and 'summary' not in result.output


def test_sql_cancel_and_can_rebuild(dataset):
    from dgm.platform_ops import ProcessCancelled
    s,c,pair,root,a,b=dataset;stop=threading.Event();stop.set();cancel=Comparisons(stop)
    with pytest.raises(ProcessCancelled):cancel.prepare(None,b)
    cancel.close()
    assert c.prepare(None,b)
