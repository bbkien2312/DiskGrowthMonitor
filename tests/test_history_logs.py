import gzip
import json
import threading
from pathlib import Path
import pytest
from dgm import config,history_logs as logs
from dgm.history_backend import close_indexes,database_path
from dgm.store import Store
from dgm.scanner import scan
from dgm.comparison import Comparisons


@pytest.fixture
def log_history(isolated):
    cfg=config.load();cfg['history_format']='jsonl';cfg['source_archive_limit_mb']=0;config.save(cfg)
    yield isolated
    close_indexes()


def test_jsonl_checksums_unicode_nulls_and_corruption(isolated):
    path=isolated/'metadata.jsonl.gz';data=[['đường dẫn',None,123],['file',0,True]]
    report=logs.write(path,{'purpose':'test'},iter(data))
    assert report['records']==2
    assert [value for kind,value in logs.read(path) if kind=='record']==data
    encoded=gzip.decompress(path.read_bytes()).replace(b'123',b'124')
    path.write_bytes(gzip.compress(encoded))
    with pytest.raises(ValueError,match='Checksum'):logs.verify(path)


def test_jsonl_cancel_leaves_no_published_log(isolated):
    path=isolated/'cancel.jsonl.gz';stop=threading.Event();stop.set()
    from dgm.platform_ops import ProcessCancelled
    with pytest.raises(ProcessCancelled):logs.write(path,{},iter([[1]]),stop=stop)
    assert not path.exists() and not list(isolated.glob('*.part'))


def test_scan_is_text_metadata_without_file_contents_and_reopens(log_history):
    root=log_history/'fixture';root.mkdir();source=root/'a';source.write_text('SECRET CONTENT NOT A METADATA FIELD')
    result=scan('daily',{'roots':[str(root)],'engine':'native','strict_browsers':True})
    home=config.home();path=home/'data/history/scans'/f"scan-{result['scan_id']:08d}.jsonl.gz"
    assert path.exists() and b'SECRET CONTENT' not in gzip.decompress(path.read_bytes())
    assert not (home/'data/history.sqlite').exists()
    from dgm.history_backend import manager
    manager().close()
    assert not database_path('history.sqlite',create=False).exists()
    store=Store();assert store.scan(result['scan_id'])['status']=='complete'
    assert len(store.entries(result['scan_id']))==2
    store.close()


def test_compare_and_normalization_survive_discarding_all_indexes(log_history):
    root=log_history/'fixture';root.mkdir();file=root/'a';file.write_bytes(b'a'*20)
    p={'roots':[str(root)],'engine':'native','strict_browsers':True}
    first=scan('daily',p);file.write_bytes(b'b'*30);second=scan('daily',p)
    cache=Comparisons();pair=cache.prepare(first['scan_id'],second['scan_id'])
    before=cache.page(pair,kind='file')['rows'][0]
    assert before['delta']==10;cache.close();close_indexes()
    cache=Comparisons();assert cache.history()
    pair=cache.prepare(first['scan_id'],second['scan_id']);after=cache.page(pair,kind='file')['rows'][0]
    assert after['delta']==10 and after['path']==before['path']
    cache.close()


def test_log_delete_removes_only_selected_metadata(log_history):
    root=log_history/'fixture';root.mkdir();(root/'a').write_text('keep')
    p={'roots':[str(root)],'engine':'native','strict_browsers':True}
    first=scan('daily',p);second=scan('daily',p)
    store=Store();store.delete_scans([first['scan_id']]);store.close();close_indexes()
    store=Store();assert [r['id'] for r in store.scans()]==[second['scan_id']]
    assert (root/'a').read_text()=='keep'
    assert not (config.home()/'data/history/scans'/f"scan-{first['scan_id']:08d}.jsonl.gz").exists()
    store.close()


def test_verified_legacy_migration_preserves_comparison_and_normalization(isolated):
    from test_v2 import snapshot,entry
    from dgm.normalization import Normalizations
    from dgm.history_migration import migrate
    root=isolated/'virtual';store=Store()
    old=snapshot(store,root,[entry(root/'f',value=10)])
    new=snapshot(store,root,[entry(root/'f',value=20),entry(root/'new',value=7)])
    raw=store.scans(effective=False);store.close()
    norm=Normalizations();norm.inspect(old,force=True,apply=True);norm.close()
    cache=Comparisons();pair=cache.prepare(old,new);expected=cache.summary(pair)['verified_delta'];cache.close()
    result=migrate(remove_old=True)
    assert result['verified'] and result['legacy_removed_bytes']>0 and not (config.home()/'data/history.sqlite').exists()
    store=Store();assert store.scans(effective=False)==raw;store.close()
    cache=Comparisons();assert cache.history();pair=cache.prepare(old,new)
    assert cache.summary(pair)['verified_delta']==expected==17
    cache.close();close_indexes()
    cache=Comparisons();pair=cache.prepare(old,new)
    assert cache.summary(pair)['verified_delta']==17
    cache.close();close_indexes()


def test_migration_cancel_preserves_original_sqlite(isolated):
    from test_v2 import snapshot,entry
    from dgm.history_migration import migrate
    from dgm.platform_ops import ProcessCancelled
    store=Store();snapshot(store,isolated/'virtual',[entry(isolated/'virtual/f',value=10)]);store.close()
    source=config.home()/'data/history.sqlite';before=source.read_bytes();stop=threading.Event();stop.set()
    with pytest.raises(ProcessCancelled):migrate(remove_old=True,stop=stop)
    assert source.read_bytes()==before and config.load()['history_format']=='sqlite'
    assert not (config.home()/'data/history/catalog.json').exists()
    assert not list((config.home()/'data').glob('history-stage-*'))


def test_conversion_refuses_concurrent_legacy_change(isolated):
    from test_v2 import snapshot,entry
    from dgm.history_migration import migrate
    import sqlite3
    root=isolated/'virtual';store=Store();snapshot(store,root,[entry(root/'f',value=10)]);store.close()
    modified=[]
    def notify(event,data):
        if 'Chuyển mốc' in data.get('message','') and not modified:
            db=sqlite3.connect(config.home()/'data/history.sqlite');db.execute("UPDATE scans SET profile='changed elsewhere'");db.commit();db.close();modified.append(True)
    with pytest.raises(RuntimeError,match='vừa thay đổi'):migrate(remove_old=True,notify=notify)
    assert (config.home()/'data/history.sqlite').exists() and config.load()['history_format']=='sqlite'
    assert not (config.home()/'data/history').exists()


def test_clear_index_preserves_logs_and_rebuilds_selected_pair(log_history):
    from dgm.history_backend import clear_indexes
    from test_v2 import snapshot,entry
    root=log_history/'virtual';store=Store();a=snapshot(store,root,[entry(root/'f',value=8)]);b=snapshot(store,root,[entry(root/'f',value=13)]);store.close()
    cache=Comparisons();pair=cache.prepare(a,b);assert cache.summary(pair)['verified_delta']==5;cache.close()
    before={p.name:p.read_bytes() for p in (config.home()/'data/history/scans').glob('*.gz')}
    result=clear_indexes();assert result['reclaimed_bytes']>0 and result['remaining_index_bytes']==0
    assert {p.name:p.read_bytes() for p in (config.home()/'data/history/scans').glob('*.gz')}==before
    cache=Comparisons();pair=cache.prepare(a,b);assert cache.summary(pair)['verified_delta']==5;cache.close()


def test_switch_many_pairs_evicts_temporary_rows_without_stale_revision_ids(log_history):
    from test_v2 import snapshot,entry
    root=log_history/'virtual';store=Store();ids=[]
    for value in (10,20,30,40):ids.append(snapshot(store,root,[entry(root/'f',value=value)]))
    store.close();cache=Comparisons()
    first=cache.prepare(ids[0],ids[1]);old=cache.page(first,kind='file')['rows'][0]
    assert old['delta']==10
    cache.prepare(ids[2],ids[3])
    assert cache.db.execute('SELECT COUNT(*) FROM source.archive_loaded').fetchone()[0]<=2
    rows=cache.page(first,kind='file')['rows']
    assert len(rows)==1 and rows[0]['before']==10 and rows[0]['after']==20 and rows[0]['delta']==10
    details=cache.details(rows[0]);assert details['logical']==20
    cache.close()


def test_cli_uses_shared_leased_index_then_rebuilds_after_exit(log_history):
    import subprocess,sys
    root=log_history/'fixture';root.mkdir();(root/'f').write_bytes(b'abc')
    result=scan('daily',{'roots':[str(root)],'engine':'native','strict_browsers':True})
    command=[sys.executable,'-m','dgm','--home',str(config.home()),'entries',str(result['scan_id']),'--minimum-mb','0']
    done=subprocess.run(command,capture_output=True,text=True,encoding='utf-8',timeout=30)
    assert done.returncode==0,done.stderr
    assert len(json.loads(done.stdout))==2
    assert database_path('history.sqlite',create=False).exists()
    close_indexes()
    done=subprocess.run(command,capture_output=True,text=True,encoding='utf-8',timeout=30)
    assert done.returncode==0 and len(json.loads(done.stdout))==2
    assert not database_path('history.sqlite',create=False).exists()


def test_jsonl_comparison_progress_uses_one_monotonic_pipeline(log_history):
    from test_v2 import snapshot,entry
    root=log_history/'virtual';store=Store()
    a=snapshot(store,root,[entry(root/'f',value=3)]);b=snapshot(store,root,[entry(root/'f',value=8)]);store.close();close_indexes()
    events=[];cache=Comparisons(notify=lambda e,d:events.append((e,d)));cache.prepare(a,b);cache.close()
    progress=[data for event,data in events if event=='task-progress']
    assert len({row['id'] for row in progress})==1
    assert [row['percent'] for row in progress]==sorted(row['percent'] for row in progress)
    assert progress[-1]['percent']==100


@pytest.mark.parametrize('direction',['asc','desc'])
def test_cursor_survives_index_reset_with_different_scan_load_order(log_history,direction):
    from test_v2 import snapshot,entry
    root=log_history/'virtual';store=Store()
    unrelated=snapshot(store,root,[entry(root/f'z{i}',value=9) for i in range(25)])
    older=snapshot(store,root,[entry(root/f'f{i:03}',value=10) for i in range(65)])
    newer=snapshot(store,root,[entry(root/f'f{i:03}',value=20) for i in reversed(range(65))])
    store.close();close_indexes()
    # A GUI can load an unrelated scan before CLI starts paging. The next
    # command must still resume exactly, even after the last owner exits.
    store=Store();store.entries(unrelated);store.close()
    cache=Comparisons();pair=cache.prepare(older,newer)
    expected=[r['path'] for r in cache.page(pair,kind='file',limit=1000,sort='delta',direction=direction)['rows']]
    page=cache.page(pair,kind='file',limit=20,sort='delta',direction=direction)
    assert isinstance(page['cursor']['path_id'],str)
    seen=[r['path'] for r in page['rows']];cursor=page['cursor'];cache.close();close_indexes()
    while cursor:
        cache=Comparisons()
        page=cache.page(pair,kind='file',limit=20,sort='delta',direction=direction,cursor=json.loads(json.dumps(cursor)))
        seen.extend(r['path'] for r in page['rows']);cursor=page['cursor'];cache.close();close_indexes()
    assert seen==expected and len(set(seen))==65


def test_stable_path_id_collision_aborts_without_merging_files(log_history,monkeypatch):
    from test_v2 import entry
    import dgm.store as store_module
    store=Store();root=log_history/'virtual'
    sid=store.begin_scan('daily','same-scope','native',{'roots':[str(root)],'engine':'native'})
    monkeypatch.setattr(store_module,'stable_path_id',lambda path:123)
    with pytest.raises(ValueError,match='Va chạm'):
        store.put(sid,[entry(root/'a',value=10),entry(root/'b',value=20)])
    assert store.db.execute('SELECT COUNT(*) FROM members WHERE scan_id=?',(sid,)).fetchone()[0]==0
    assert store.db.execute('SELECT COUNT(*) FROM paths').fetchone()[0]==0
    store.close()
