"""Approved presence/evidence split: arithmetic, transitions, filters and cache."""
import json
from pathlib import Path
import pytest
from typer.testing import CliRunner
from dgm.comparison import Comparisons
from dgm.store import Store
from dgm.cli import app
from dgm.presentation import snapshot_cell
from test_v2 import entry,snapshot
from test_scope_partitions import configure


def test_presence_is_not_confidence_and_verified_private_still_counts(isolated,monkeypatch):
    s=Store();root=isolated/'virtual';browser=root/'Chrome'
    a=snapshot(s,root,[entry(root/'keep',value=10),entry(root/'gone',value=8)])
    b=snapshot(s,root,[entry(root/'keep',value=15),entry(root/'new',value=4),entry(browser,'folder'),entry(browser/'cache',value=90)])
    configure(s,b,strict_browsers=False)
    raw=[s.scan(i,effective=False) for i in (a,b)]
    import os
    from dgm.config import home
    original_scandir=os.scandir;app_home=home()
    def app_only(path):
        assert Path(path).is_relative_to(app_home),'Comparison must not read the live scan scope'
        return original_scandir(path)
    monkeypatch.setattr('os.scandir',app_only)
    c=Comparisons();pair=c.prepare(a,b);summary=c.summary(pair)
    common=c.page(pair,section='common',kind='file')['rows'];private=c.page(pair,section='private',kind='file')['rows']
    assert [Path(r['path']).name for r in common]==['keep']
    by_name={Path(r['path']).name:r for r in private}
    assert (by_name['new']['change'],by_name['new']['delta'],by_name['new']['presence'])==('new',4,'new')
    assert (by_name['gone']['change'],by_name['gone']['delta'],by_name['gone']['presence'])==('missing',-8,'old')
    assert (by_name['cache']['change'],by_name['cache']['delta'],by_name['cache']['old_issue'])==('not_scanned',None,'browser')
    assert snapshot_cell(by_name['cache'],'old')=='Không quét' and snapshot_cell(by_name['gone'],'new')=='Không có'
    assert summary['verified_common_delta']==5 and summary['verified_private_delta']==-4 and summary['verified_delta']==1
    assert summary['unverified_private_files']==1
    assert (summary['old_totals']['logical'],summary['new_totals']['logical'])==(10,15)
    assert (summary['private_old_totals']['logical'],summary['private_new_totals']['logical'])==(8,94)
    for side in ('old','new'):
        assert summary['full_'+side+'_totals']['logical']==summary[side+'_totals']['logical']+summary['private_'+side+'_totals']['logical']
    rootrow=next(r for r in c.page(pair,kind='folder')['rows'] if r['path']==str(root))
    assert rootrow['after']==109 and rootrow['delta']==1 and rootrow['change']=='partial'
    assert rootrow['common_after']==15 and rootrow['private_after']==94
    from audit_history_copy import verify_partition_rows
    from dgm.config import comparison_path_key
    assert set(verify_partition_rows(c,pair,summary,comparison_path_key(root)))=={'common_page_seconds','private_page_seconds'}
    assert [s.scan(i,effective=False) for i in (a,b)]==raw
    c.close();s.close()


def test_both_present_stays_common_even_if_setting_says_excluded(isolated):
    s=Store();root=isolated/'virtual';f=root/'observed'
    a=snapshot(s,root,[entry(f,value=10)]);b=snapshot(s,root,[entry(f,value=15)])
    configure(s,b,excludes=[str(f)])
    c=Comparisons();pair=c.prepare(a,b)
    r=c.page(pair,section='common',kind='file')['rows'][0]
    assert r['presence']=='both' and r['delta']==5 and r['comparable'] and not r['reason']
    assert c.page(pair,section='private',kind='file')['total']==0
    c.close();s.close()


def test_missing_coverage_names_the_unverified_side_not_deleted(isolated):
    s=Store();root=isolated/'virtual';denied=root/'blocked'
    a=snapshot(s,root,[entry(denied,'folder'),entry(denied/'f',value=20)])
    b=snapshot(s,root,[entry(denied,'folder')])
    totals=json.loads(s.scan(b)['summary']);totals['coverage']={'unknown_roots':[str(denied)]};s.record_stage(b,totals)
    c=Comparisons();pair=c.prepare(a,b);r=c.page(pair,section='private',kind='file')['rows'][0]
    assert r['presence']=='old' and r['change']=='unknown' and r['new_issue']=='coverage' and r['delta'] is None
    assert snapshot_cell(r,'new')=='Chưa rõ' and c.summary(pair)['verified_delta']==0
    c.close();s.close()


def test_move_parent_rollups_do_not_lose_or_double_count_bytes(isolated):
    s=Store();root=isolated/'virtual';old=root/'a';new=root/'b'
    folders=[entry(old,'folder'),entry(new,'folder')]
    a=snapshot(s,root,[*folders,entry(old/'file',value=10,identity='stable-id')])
    b=snapshot(s,root,[*folders,entry(new/'file',value=13,identity='stable-id')])
    c=Comparisons();pair=c.prepare(a,b);rows={r['path']:r for r in c.page(pair,limit=100)['rows']}
    assert rows[str(new/'file')]['change']=='moved' and rows[str(new/'file')]['delta']==3
    assert rows[str(old)]['delta']==-10 and rows[str(new)]['delta']==13 and rows[str(root)]['delta']==3
    assert c.summary(pair)['verified_delta']==3 and c.summary(pair)['verified_private_delta']==3
    c.close();s.close()


@pytest.mark.parametrize('reverse',[False,True])
def test_file_folder_replacement_counts_file_bytes_once(isolated,reverse):
    s=Store();root=isolated/'virtual';path=root/'changed'
    left=[entry(path,value=10)];right=[entry(path,'folder'),entry(path/'child',value=4)]
    if reverse:left,right=right,left
    a=snapshot(s,root,left);b=snapshot(s,root,right)
    c=Comparisons();pair=c.prepare(a,b);expected=6 if reverse else -6
    assert c.summary(pair)['verified_delta']==expected
    rootrow=next(r for r in c.page(pair,kind='folder')['rows'] if r['path']==str(root))
    assert rootrow['delta']==expected
    if not reverse:
        changed=next(r for r in c.page(pair,kind='folder')['rows'] if r['path']==str(path))
        assert changed['delta']==-6
    c.close();s.close()


@pytest.mark.parametrize('sort',['path','old','new','delta','allocated','created','modified','kind','change'])
@pytest.mark.parametrize('direction',['asc','desc'])
def test_private_real_changes_page_and_filter_globally(isolated,sort,direction):
    s=Store();root=isolated/'virtual';sub=root/'branch'
    a=snapshot(s,root,[entry(sub,'folder')])
    b=snapshot(s,root,[entry(sub,'folder')]+[entry(sub/f'f{i:03}',value=100+i) for i in range(237)])
    c=Comparisons();pair=c.prepare(a,b)
    options=dict(section='private',presence='new',kind='file',root=str(sub),sort=sort,direction=direction)
    first=c.page(pair,limit=20,**options);second=c.page(pair,limit=100,cursor=first['cursor'],**options);whole=c.page(pair,limit=1000,**options)
    assert first['total']==237 and len(second['rows'])==100
    assert [r['path_id'] for r in first['rows']+second['rows']]==[r['path_id'] for r in whole['rows'][:120]]
    assert all(r['change']=='new' and r['delta'] is not None for r in whole['rows'])
    with pytest.raises(ValueError,match='Con trỏ'):c.page(pair,cursor=first['cursor'],section='common')
    c.close();s.close()


def test_cli_presence_and_status_filters_match_backend(isolated):
    s=Store();root=isolated/'virtual';a=snapshot(s,root,[entry(root/'gone',value=7)])
    b=snapshot(s,root,[entry(root/'new',value=9)]);s.close()
    result=CliRunner().invoke(app,['compare',str(b),'--older',str(a),'--section','private','--presence','old','--changes','missing','--kind','file','--minimum-mb','0'])
    assert result.exit_code==0,result.output
    data=json.loads(result.output);assert data['total']==1 and data['rows'][0]['presence']=='old'
    assert data['summary']['verified_delta']==2


def test_sparse_legacy_subtree_keeps_descendants_without_recorded_parent(isolated):
    s=Store();root=isolated/'virtual';branch=root/'branch';leaf=branch/'unrecorded'/'file'
    a=snapshot(s,root,[entry(branch,'folder'),entry(leaf,value=20)])
    b=snapshot(s,root,[entry(branch,'folder'),entry(leaf,value=25)])
    c=Comparisons();pair=c.prepare(a,b)
    page=c.page(pair,section='common',root=str(branch),kind='file')
    assert page['total']==1 and page['rows'][0]['delta']==5
    c.close();s.close()


def test_private_index_and_empty_common_page_do_not_walk_other_group(isolated):
    s=Store();root=isolated/'virtual'
    a=snapshot(s,root,[entry(root/f'shared{i:04}',value=i+1) for i in range(5000)])
    b=snapshot(s,root,[entry(root/f'shared{i:04}',value=i+1) for i in range(5000)]+[entry(root/f'new{i}',value=10+i) for i in range(5)])
    c=Comparisons();pair=c.prepare(a,b);trace=[]
    page=c.page(pair,section='private',kind='file',sort='new');assert page['total']==5
    plan=c.db.execute("EXPLAIN QUERY PLAN SELECT path_id FROM diff_rows INDEXED BY presence_new WHERE pair_id=? AND (presence='both')=? ORDER BY COALESCE(after,-1) DESC,path_id DESC LIMIT 20",(pair,0)).fetchall()
    assert any('SEARCH' in r[3] and 'presence_new' in r[3] and '<expr>' in r[3] for r in plan)
    c.db.set_trace_callback(trace.append)
    assert c.page(pair,section='common',presence='old',kind='file')['total']==0
    assert not any('FROM diff_rows c INDEXED' in q and 'LIMIT 21' in q for q in trace)
    assert c.prepare(a,b)==pair and not any('CREATE TEMP TABLE compare_' in q for q in trace)
    c.close();s.close()
