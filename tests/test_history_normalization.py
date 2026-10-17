import gzip
import hashlib
import json
import threading
from pathlib import Path

import pytest
from typer.testing import CliRunner
from dgm.config import home
from dgm.store import Store
from dgm.normalization import Normalizations
from dgm.comparison import Comparisons
from dgm.platform_ops import ProcessCancelled
from test_v2 import entry,snapshot


def test_preview_apply_original_and_summary_tree_use_same_data(isolated):
    store=Store();root=isolated/'virtual'
    sid=snapshot(store,root,[entry(root/'sub','folder',999),entry(root/'sub'/'f',value=32)],aggregate=False,root_value=9999)
    original=store.scan(sid,effective=False);norm=Normalizations()
    report=norm.inspect(sid,force=True)
    assert report['after']['logical']==32 and report['changed_rows']==2 and not report['applied']
    assert norm.current(sid) is None and store.scan(sid)==original
    norm.inspect(sid,apply=True)
    assert store.scan(sid,effective=False)==original
    assert json.loads(store.scan(sid)['summary'])['logical']==32
    cache=Comparisons();pair=cache.prepare(None,sid);row=next(r for r in cache.page(pair,kind='folder')['rows'] if r['path']==str(root))
    assert row['after']==cache.summary(pair)['private_new_totals']['logical']==32
    norm.restore([sid]);assert store.scan(sid)==original
    pair=cache.prepare(None,sid);row=next(r for r in cache.page(pair,kind='folder')['rows'] if r['path']==str(root))
    assert row['after']==9999 and norm.current(sid) is None
    cache.close();norm.close();store.close()


def test_legacy_lost_hardlink_allocation_stays_unknown_not_zero(isolated):
    store=Store();root=isolated/'virtual'
    files=[{**entry(root/name,value=100,identity='wiz:vol:7'),'allocated':0} for name in ('a','z')]
    sid=snapshot(store,root,files,aggregate=False);norm=Normalizations();report=norm.inspect(sid,apply=True)
    assert report['damaged_groups']==1 and report['state']=='needs-source'
    assert report['after']['logical']==200 and report['after']['allocated'] is None
    assert report['hardlinks']=={'extra_paths':1,'repeated_logical':100}
    cache=Comparisons();pair=cache.prepare(None,sid)
    assert cache.summary(pair)['private_new_totals']['allocated'] is None
    assert all(r['allocated_after'] is None for r in cache.page(pair,kind='folder')['rows'])
    cache.close();norm.close();store.close()


def test_allocation_moves_to_owner_and_not_double_counted(isolated):
    store=Store();root=isolated/'virtual'
    files=[{**entry(root/'a',value=100,identity='same'),'allocated':0},
           {**entry(root/'z',value=100,identity='same'),'allocated':4096}]
    sid=snapshot(store,root,files,aggregate=False);norm=Normalizations();report=norm.inspect(sid,apply=True)
    assert report['after']['logical']==200 and report['after']['allocated']==4096
    cache=Comparisons();pair=cache.prepare(None,sid)
    rows={Path(r['path']).name:r for r in cache.page(pair,kind='file')['rows']}
    assert rows['a']['allocated_after']==4096 and rows['z']['allocated_after']==0
    cache.close();norm.close();store.close()


def test_null_dates_and_reparse_normalize_without_restat(isolated,monkeypatch):
    store=Store();root=isolated/'virtual'
    files=[{**entry(root/'junction',value=10000),'attrs':1024,'created':0,'modified':0},entry(root/'f',value=30)]
    sid=snapshot(store,root,files,aggregate=False)
    norm=Normalizations()
    import dgm.scanner as scanner
    monkeypatch.setattr(scanner,'metadata',lambda *a:pytest.fail('Live metadata must never enter historical normalization'))
    report=norm.inspect(sid,apply=True)
    assert report['after']['logical']==30 and report['after']['links']==1
    cache=Comparisons();pair=cache.prepare(None,sid)
    link=next(r for r in cache.page(pair)['rows'] if r['path']==str(root/'junction'))
    assert link['kind']=='link' and link['after']==0
    cache.close();norm.close();store.close()


def test_cancel_rolls_back_active_overlay_and_can_retry(isolated):
    store=Store();root=isolated/'virtual';sid=snapshot(store,root,[entry(root/'f',value=50)])
    norm=Normalizations();norm.inspect(sid,apply=True);previous=norm.current(sid)['id'];norm.close()
    stop=threading.Event()
    def notify(event,data):
        if event=='task-progress' and data['stage']=='Tính lại thư mục':stop.set()
    norm=Normalizations(stop,notify)
    with pytest.raises(ProcessCancelled):norm.inspect(sid,apply=True,force=True)
    stop.clear();norm.notify=lambda *a:None;assert norm.current(sid)['id']==previous
    assert norm.inspect(sid,apply=True,force=True)['after']['logical']==50
    norm.close();store.close()


def test_overlay_revision_invalidates_only_affected_pair(isolated):
    store=Store();root=isolated/'virtual';a=snapshot(store,root,[entry(root/'f',value=10)]);b=snapshot(store,root,[entry(root/'f',value=20)])
    cache=Comparisons();pair=cache.prepare(a,b);signature=cache.db.execute('SELECT dataset_signature FROM pairs WHERE id=?',(pair,)).fetchone()[0]
    norm=Normalizations();norm.inspect(a,apply=True,force=True);norm.close()
    assert cache.prepare(a,b)==pair
    assert cache.db.execute('SELECT dataset_signature FROM pairs WHERE id=?',(pair,)).fetchone()[0]!=signature
    cache.close();store.close()


def test_clear_comparison_cache_preserves_normalization(isolated):
    store=Store();root=isolated/'virtual';sid=snapshot(store,root,[entry(root/'f',value=30)])
    cache=Comparisons();cache.prepare(None,sid);norm=Normalizations();ident=norm.current(sid)['id']
    cache.clear_all();assert norm.current(sid)['id']==ident
    cache.prepare(None,sid);assert norm.current(sid)['id']==ident
    cache.close();norm.close();store.close()


def test_common_scope_recalculates_folder_totals_and_partitions_hardlinks(isolated):
    store=Store();root=isolated/'virtual'
    files=[entry(root/'a',value=30,identity='same'),{**entry(root/'z',value=30,identity='same'),'allocated':0},entry(root/'keep',value=5)]
    a=snapshot(store,root,files);b=snapshot(store,root,files)
    cfg=json.loads(store.scan(a)['config']);cfg.update(excludes=[str(root/'a')]);store.db.execute('UPDATE scans SET config=?,scope=? WHERE id=?',(json.dumps(cfg),'different',b));store.db.commit()
    cache=Comparisons()
    pair=cache.prepare(a,b);assert cache.prepare(a,b,common=True)==pair
    summary=cache.summary(pair)
    assert summary['common_scope'] and summary['old_totals']['logical']==summary['new_totals']['logical']==65
    assert summary['old_totals']['allocated']==35
    assert summary['private_old_totals']['allocated']==0
    assert summary['old_totals']['allocated']+summary['private_old_totals']['allocated']==summary['full_old_totals']['allocated']
    folder=next(r for r in cache.page(pair,kind='folder')['rows'] if r['path']==str(root))
    assert folder['before']==folder['after']==65
    assert any(r['path']==str(root/'a') for r in cache.page(pair,section='common')['rows'])
    cache.close();store.close()


def test_scope_error_identifies_browser_policy_and_common_requires_overlap(isolated):
    store=Store();root=isolated/'virtual';a=snapshot(store,root,[]);b=snapshot(store,root,[])
    cfg=json.loads(store.scan(b)['config']);cfg['strict_browsers']=False
    store.db.execute('UPDATE scans SET config=? WHERE id=?',(json.dumps(cfg),b));store.db.commit()
    cache=Comparisons()
    pair=cache.prepare(a,b)
    assert any('Chrome/Edge' in x for x in cache.summary(pair)['scope_differences'])
    assert cache.prepare(a,b,common=True)==pair
    cfg['roots']=[str(isolated/'different')];store.db.execute('UPDATE scans SET config=? WHERE id=?',(json.dumps(cfg),b));store.db.commit()
    pair=cache.prepare(a,b,common=True)
    assert cache.summary(pair)['common_scope']['roots']==[]
    cache.close();store.close()


def test_cli_audit_apply_restore_equivalent(isolated):
    from dgm.cli import app
    store=Store();root=isolated/'virtual';sid=snapshot(store,root,[entry(root/'f',value=30)],aggregate=False,root_value=700);original=store.scan(sid,effective=False)
    runner=CliRunner()
    for cmd,tail in [('snapshot-audit',[]),('snapshot-normalize',['--yes']),('snapshot-original',['--yes'])]:
        result=runner.invoke(app,[cmd,'--id',str(sid),*tail]);assert result.exit_code==0,result.output
        assert store.scan(sid,effective=False)==original
    assert store.scan(sid)==original;store.close()


def test_archive_hash_retention_and_scoped_clear(isolated):
    from dgm.source_archive import retain,clear
    from dgm.config import load,save
    data=b'File Name,Size,Allocated\nC:\\x,100,4096\n';source=isolated/'source.csv';source.write_bytes(data)
    result=retain(source,engine='wiztree',root='C:\\',options=['tool.exe'],stop=threading.Event())
    target=home()/result['path'];assert gzip.decompress(target.read_bytes())==data
    assert result['sha256']==hashlib.sha256(data).hexdigest() and result['retained']
    sentinel=target.parent/'keep.txt';sentinel.write_text('keep')
    assert clear()['files']==1 and sentinel.exists() and source.exists()
    cfg=load();cfg['source_archive_limit_mb']=0;save(cfg)
    assert not retain(source,engine='wiztree',root='C:\\',options=[],stop=threading.Event())['retained']


@pytest.mark.parametrize('prefix,expected',[('2026-10-16 12:34:56,000 [DEBUG] detail','DEBUG'),
    ('2026-10-16 12:34:56,000 ERROR failed','ERROR'),('[WARNING] warning','WARNING'),('2026 INFO old','INFO')])
def test_bracket_and_legacy_log_levels(prefix,expected):
    from dgm.log_reader import parse_lines
    rows,current=parse_lines(prefix+'\n  File "C:/DEBUG/folder.py"\nValueError: INFO in message')
    assert current==expected and all(level==expected for level,line in rows)


def test_units_keep_byte_values_and_distinguish_gib():
    from dgm.presentation import size,set_units
    try:
        set_units(False);assert size(161290907648)=='161 GB'
        set_units(True);assert size(161290907648)=='150 GiB'
    finally:set_units(False)


@pytest.mark.parametrize('separator',['/',' ','.','-'])
def test_wiztree_windows_date_separator_keeps_actual_timestamp(separator):
    from dgm.scanner import stamp
    from datetime import datetime,timezone
    assert stamp(f'2026{separator}10{separator}16 09:48:22')==datetime(2026,10,16,9,48,22,tzinfo=timezone.utc).timestamp()


def test_new_scans_reuse_ingestion_normalization_without_another_full_pass(isolated):
    from dgm.scanner import scan
    root=isolated/'fixture';root.mkdir();(root/'f').write_bytes(b'fixture')
    result=scan('demo',{'roots':[str(root)],'engine':'native','strict_browsers':False})
    norm=Normalizations();trace=[];norm.db.set_trace_callback(trace.append)
    report=norm.inspect(result['scan_id'],apply=True)
    assert report['verified_on_ingestion'] and report['changed_rows']==0
    assert not any('CREATE TEMP TABLE work' in q for q in trace)
    # Explicit manual verification is allowed and still performs a real audit.
    assert norm.inspect(result['scan_id'],force=True)['after']==report['after']
    norm.close()


def test_rebuild_invalidates_scoped_pages_and_old_cursor(isolated):
    store=Store();root=isolated/'virtual';branch=root/'sub'
    sid=snapshot(store,root,[entry(branch,'folder'),*[entry(branch/str(i),value=i+10) for i in range(30)]])
    cache=Comparisons();pair=cache.prepare(None,sid)
    old_page=cache.page(pair,root=str(branch),kind='file',limit=20)
    assert old_page['total']==30 and old_page['cursor']
    norm=Normalizations();norm.inspect(sid,apply=True,force=True);norm.close();cache.prepare(None,sid)
    with pytest.raises(ValueError,match='Con trỏ'):cache.page(pair,root=str(branch),kind='file',cursor=old_page['cursor'])
    assert cache.db.execute('SELECT COUNT(*) FROM scopes WHERE pair_id=?',(pair,)).fetchone()[0]==0
    assert cache.page(pair,root=str(branch),kind='file')['total']==30
    cache.close();store.close()


def test_wiztree_modern_columns_optional_capacity_and_leading_zero_hardlink(isolated,monkeypatch):
    import csv
    import dgm.scanner as scanner
    from dgm.config import Policy,load,save
    root=isolated/'virtual';exe=isolated/'fixture-tool.exe';exe.write_bytes(b'fixture')
    cfg=load();cfg['engines']['wiztree']=str(exe);save(cfg)
    headers=['File Name','Size','Allocated','Modified','Attributes','Files','Folders','MFTRECNO','MFTPARENTRECNO','LASTACCESSDATE','CREATEDDATE','DRIVECAPACITY','FREESPACE']
    def export(argv,**kw):
        output=Path(next(a.split('=',1)[1] for a in argv if str(a).startswith('/export=')))
        with output.open('w',encoding='utf-8-sig',newline='') as stream:
            writer=csv.writer(stream);writer.writerow(headers)
            writer.writerow([str(root)+'\\',200,4096,'2026 10 16 09:48:22',16,2,0,'1','0','','1899 12 30 00:00:00',100000,50000])
            writer.writerow([str(root/'a'),100,'04096','2026 10 16 09:48:22',32,0,0,'7','1','','2026 10 16 09:48:22'])
            writer.writerow([str(root/'z'),100,'4096','2026 10 16 09:48:22',32,0,0,'7','1','','2026 10 16 09:48:22'])
        class Done:returncode=0
        return Done()
    monkeypatch.setattr(scanner,'run',export);events=[]
    rows=list(scanner.wiztree_entries(Policy({'roots':[str(root)],'mft':True}),cfg,threading.Event(),lambda a,b:events.append((a,b))))
    assert len(rows)==3 and rows[0]['created'] is None and rows[1]['created']>0
    assert rows[1]['identity']==rows[2]['identity'] and rows[1]['identity'].endswith(':7')
    assert rows[1]['allocated']==4096
    audit=next(data for event,data in events if event=='source-audit')
    assert audit['source_allocated']==4096 and audit['included_allocated']==8192
    assert audit['source']['retained']


def test_ingestion_hardlink_case_collision_has_only_one_physical_owner(isolated):
    store=Store();root=isolated/'virtual'
    sid=snapshot(store,root,[{**entry(root/'file',value=10,identity='same'),'allocated':4096},
        {**entry(root/'FILE',value=10,identity='same'),'allocated':4096}])
    assert store.totals(sid)['logical']==20 and store.totals(sid)['allocated']==4096
    norm=Normalizations();assert norm.inspect(sid)['after']['allocated']==4096
    norm.close();store.close()
