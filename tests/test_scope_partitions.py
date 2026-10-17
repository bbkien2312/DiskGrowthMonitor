"""Automatic scope partitions, old metadata upgrades and measurement conservation."""
import json
import pytest
from typer.testing import CliRunner
from dgm.store import Store
from dgm.comparison import Comparisons
from dgm.normalization import Normalizations,VERSION
from dgm.snapshot_scope import option_label
from test_v2 import entry,snapshot


def configure(store,sid,**updates):
    scan=store.scan(sid,effective=False);cfg=json.loads(scan['config']);cfg.update(updates)
    store.db.execute('UPDATE scans SET config=?,scope=? WHERE id=?',(json.dumps(cfg),'new-hash-'+str(sid),sid));store.db.commit()


def measured_scope(store,sid,root,strict=True,**updates):
    t=json.loads(store.scan(sid,effective=False)['summary']);t.update(updates)
    t['effective_scope']={'roots':[str(root)],'excludes':[],'strict_browsers':strict,'implicit_excludes':[], 'volumes':['known-volume']}
    store.record_stage(sid,t)


def test_legacy_missing_scope_metadata_is_not_incompatible(isolated):
    s=Store();root=isolated/'virtual';a=snapshot(s,root,[entry(root/'f',value=10)])
    b=snapshot(s,root,[entry(root/'f',value=20),entry(root/'brand-new',value=7)])
    configure(s,b);measured_scope(s,b,root)
    assert s.previous(b)==a
    c=Comparisons();pair=c.prepare(a,b);t=c.summary(pair)
    assert t['old_totals']['logical']==10 and t['new_totals']['logical']==20
    assert next(r for r in c.page(pair,kind='file')['rows'] if r['path'].endswith('brand-new'))['change']=='new'
    assert c.page(pair,kind='file',section='private')['rows'][0]['change']=='new'
    assert t['verified_delta']==17
    assert any('chưa ghi nhận' in text for text in t['scope_differences'])
    c.close();s.close()


def test_browser_differences_do_not_invent_growth(isolated):
    s=Store();root=isolated/'virtual';browser=root/'Users'/'Person'/'AppData'/'Local'/'Google'/'Chrome'
    a=snapshot(s,root,[entry(root/'gone',value=8),entry(root/'keep',value=10)])
    b=snapshot(s,root,[entry(root/'keep',value=15),entry(root/'new',value=4),entry(browser,'folder'),entry(browser/'Cache.bin',value=90)])
    configure(s,b,strict_browsers=False,mft=True)
    c=Comparisons();pair=c.prepare(a,b);t=c.summary(pair)
    assert (t['old_totals']['logical'],t['new_totals']['logical'])==(10,15)
    assert t['private_new_totals']['logical']==94 and t['verified_delta']==1
    common=c.page(pair,kind='file',section='common')['rows'];private=c.page(pair,kind='file',section='private')['rows']
    assert {r['change'] for r in common}=={'grown'}
    assert {r['change'] for r in private}=={'new','missing','not_scanned'}
    assert next(r for r in private if r['reason']=='browser')['delta'] is None
    assert 'MFT: Không' in option_label(s.scan(b))  # Native checkbox isn't MFT evidence.
    folder=next(r for r in c.page(pair,kind='folder')['rows'] if r['path']==str(root))
    assert folder['after']==109 and folder['private_after']==94 and folder['delta']==1
    assert t['full_new_totals']['logical']==t['new_totals']['logical']+t['private_new_totals']['logical']
    c.close();s.close()


def test_missing_allocation_does_not_remove_known_size_from_common(isolated):
    s=Store();root=isolated/'virtual';a=snapshot(s,root,[entry(root/'f',value=10)])
    b=snapshot(s,root,[{**entry(root/'f',value=20),'allocated':None}])
    c=Comparisons();pair=c.prepare(a,b);t=c.summary(pair)
    r=c.page(pair,kind='file')['rows'][0]
    assert r['delta']==10 and r['allocated_delta'] is None
    assert t['new_totals']['allocated'] is None and t['new_totals']['allocated_unknown_files']==1
    assert t['private_new_totals']['files']==0
    assert t['new_totals']['allocated_known']==0
    c.close();s.close()


def test_unknown_coverage_is_private_but_shared_measured_files_compare(isolated):
    s=Store();root=isolated/'virtual';denied=root/'blocked'
    a=snapshot(s,root,[entry(denied,'folder'),entry(denied/'old-only',value=80),entry(denied/'known',value=10)])
    b=snapshot(s,root,[entry(denied,'folder'),entry(denied/'known',value=12)])
    t=json.loads(s.scan(b)['summary']);t.update(coverage={'unknown_roots':[str(denied)]});s.record_stage(b,t)
    c=Comparisons();pair=c.prepare(a,b);t=c.summary(pair)
    assert (t['old_totals']['logical'],t['new_totals']['logical'])==(10,12)
    assert t['private_old_totals']['logical']==80
    assert c.page(pair,kind='file')['rows'][0]['delta']==2
    r=c.page(pair,kind='file',section='private')['rows'][0]
    assert r['delta'] is None and 'coverage' in r['reason']
    c.close();s.close()


@pytest.mark.parametrize('sort',['path','old','new','delta','allocated','created','modified','kind','change'])
@pytest.mark.parametrize('direction',['asc','desc'])
def test_private_global_order_subtree_and_cursor(sort,direction,isolated):
    s=Store();root=isolated/'virtual';sub=root/'excluded';other=root/'also-excluded'
    files=[entry(sub,'folder'),entry(other,'folder')]+[entry(sub/f'f-{i:03}',value=i+1) for i in range(137)]+[entry(other/'bigger',value=900)]
    a=snapshot(s,root,files);b=snapshot(s,root,[]);configure(s,b,excludes=[str(sub),str(other)])
    c=Comparisons();pair=c.prepare(a,b)
    options=dict(section='private',kind='file',sort=sort,direction=direction,root=str(sub))
    first=c.page(pair,limit=20,**options);nextpage=c.page(pair,limit=100,cursor=first['cursor'],**options)
    full=c.page(pair,limit=1000,**options)
    assert first['total']==137 and len(first['rows'])==20 and len(nextpage['rows'])==100
    assert [r['path_id'] for r in first['rows']+nextpage['rows']]==[r['path_id'] for r in full['rows'][:120]]
    assert all(str(sub) in r['path'] and r['delta'] is None for r in full['rows'])
    if sort in ('new','old'):
        values=[(r['after'] if sort=='new' else r['before']) or -1 for r in full['rows']];assert values==sorted(values,reverse=direction=='desc')
    c.close();s.close()


def test_normalization_v2_upgrades_old_overlay_and_cache_once(isolated,monkeypatch):
    s=Store();root=isolated/'virtual';sid=snapshot(s,root,[{**entry(root/'f',value=5),'allocated':None}],aggregate=False,root_value=999)
    raw=s.scan(sid,effective=False);n=Normalizations();n.inspect(sid,apply=True)
    n.db.execute('UPDATE datasets SET version=1 WHERE scan_id=?',(sid,));n.db.commit()
    assert n.current(sid) is None
    c=Comparisons();pair=c.prepare(None,sid);report=json.loads(n.current(sid)['report'])
    assert report['version']==VERSION==2 and report['after']['logical']==5
    assert report['after']['allocated_unknown_files']==1 and s.scan(sid,effective=False)==raw
    monkeypatch.setattr(c,'_comparison_map',lambda *_:pytest.fail('Cached pair must not rebuild maps'))
    assert c.prepare(None,sid)==pair
    c.clear_all();assert n.current(sid) is not None
    assert c.db.execute('SELECT COUNT(*) FROM private_rows').fetchone()[0]==0
    c.close();n.close();s.close()


def test_cli_private_matches_backend(isolated):
    from dgm.cli import app
    s=Store();root=isolated/'virtual';a=snapshot(s,root,[entry(root/'excluded',value=20)])
    b=snapshot(s,root,[]);configure(s,b,excludes=[str(root/'excluded')]);s.close()
    result=CliRunner().invoke(app,['compare',str(b),'--older',str(a),'--section','private','--kind','file','--minimum-mb','0'])
    assert result.exit_code==0,result.output
    data=json.loads(result.output);assert data['rows'][0]['change']=='not_scanned' and data['rows'][0]['before']==20
    assert data['summary']['private_old_totals']['logical']==20


def test_v1_ingestion_upgrade_marks_unknown_metadata_without_full_recalculation(isolated):
    s=Store();root=isolated/'virtual'
    sid=snapshot(s,root,[{**entry(root/'f',value=7),'allocated':None}])
    t=json.loads(s.scan(sid)['summary']);t.update(canonical_normalization_version=1,import_complete=True);s.record_stage(sid,t)
    raw=s.scan(sid,effective=False);n=Normalizations();trace=[];n.db.set_trace_callback(trace.append)
    report=n.inspect(sid,apply=True)
    assert report['verified_on_ingestion'] and report['changed_rows']==1
    assert not any('CREATE TEMP TABLE work' in sql for sql in trace)
    row=n.db.execute('SELECT status,logical,allocated FROM overrides WHERE dataset_id=?',(n.current(sid)['id'],)).fetchone()
    assert tuple(row)==('metadata-partial',7,None) and s.scan(sid,effective=False)==raw
    n.close();s.close()
