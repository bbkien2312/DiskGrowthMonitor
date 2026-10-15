"""Synthetic metadata only. Never enumerate user disks or browser folders."""
import json
import sqlite3
import threading
import pytest
from dgm.comparison import Comparisons
from dgm.store import Store
from test_v2 import entry,snapshot


@pytest.fixture
def large_pair(isolated):
    root=isolated/'virtual';branch=root/'chosen';other=root/'elsewhere';store=Store()
    items=[entry(branch,'folder'),entry(branch/'nested','folder'),entry(other,'folder')]
    items += [entry(branch/f'f{i:04}',value=11_000_000+i%37,modified=i%17+1) for i in range(1200)]
    items += [entry(branch/'nested'/f'n{i:04}',value=15_000_000+i) for i in range(60)]
    items += [entry(other/f'z{i:04}',value=25_000_000+i) for i in range(1300)]
    old=snapshot(store,root,items);new=snapshot(store,root,[{**r,'logical':r['logical']+4096,'allocated':r['allocated']+4096} if r['kind']=='file' else r for r in items])
    cache=Comparisons();pair=cache.prepare(old,new)
    yield cache,pair,root,branch,old,new
    cache.close();store.close()


@pytest.mark.parametrize('sort',['path','new','old','delta','allocated','created','modified','kind','change'])
@pytest.mark.parametrize('direction',['asc','desc'])
def test_mixed_page_sizes_follow_full_global_order(large_pair,sort,direction):
    cache,pair,*_=large_pair;rows=[];cursor=None
    for count in (20,100,1000,50,1000,500):
        page=cache.page(pair,limit=count,cursor=cursor,sort=sort,direction=direction)
        rows+=page['rows'];cursor=page['cursor']
        if cursor is None:break
    assert len(rows)==2564 and len({r['path_id'] for r in rows})==len(rows)
    keys=[(r['_key'],r['path_id']) for r in rows]
    assert keys==sorted(keys,reverse=direction=='desc')


@pytest.mark.parametrize('recursive,count',[(True,1260),(False,1200)])
def test_folder_names_never_build_whole_pair_order(large_pair,recursive,count):
    cache,pair,root,branch,*_=large_pair
    page=cache.page(pair,root=str(branch),recursive=recursive,kind='file',sort='path',direction='asc')
    assert page['total']==count and len(page['rows'])==20
    assert cache.db.execute('SELECT names_ready FROM pairs WHERE id=?',(pair,)).fetchone()[0]==0
    assert cache.db.execute('SELECT COUNT(*) FROM diff_rows WHERE pair_id=? AND name_rank<>0',(pair,)).fetchone()[0]==0
    assert cache.db.execute('SELECT names_ready FROM scopes WHERE pair_id=?',(pair,)).fetchone()[0]==1
    assert all(r['path'].startswith(str(branch)) for r in page['rows'])
    before=cache.db.execute('SELECT path_id,name_rank FROM scope_items ORDER BY path_id').fetchall()
    cache.page(pair,sort='path')
    after=cache.db.execute('SELECT path_id,name_rank FROM scope_items ORDER BY path_id').fetchall()
    assert [tuple(r) for r in before]==[tuple(r) for r in after]
    more=cache.page(pair,root=str(branch),recursive=recursive,kind='file',sort='path',direction='asc',limit=1000,cursor=page['cursor'])
    assert not {r['path_id'] for r in page['rows']} & {r['path_id'] for r in more['rows']}


def test_counts_reused_when_changing_sort_or_direction(large_pair):
    cache,pair,root,branch,*_=large_pair
    first=cache.page(pair,root=str(branch),kind='file',minimum=10_000_000,sort='new')
    trace=[];cache.db.set_trace_callback(trace.append)
    for sort,direction in [('new','asc'),('path','asc'),('path','desc'),('allocated','desc')]:
        assert cache.page(pair,root=str(branch),kind='file',minimum=10_000_000,sort=sort,direction=direction)['total']==first['total']
    assert not any('SELECT COUNT(*) FROM' in q for q in trace)


def test_name_order_interruption_is_atomic_and_retryable(large_pair):
    cache,pair,root,branch,*_=large_pair
    scope=cache.scope(pair,cache.db.execute('SELECT id FROM source.paths WHERE path=?',(str(branch),)).fetchone()[0])
    calls=[0]
    def interrupt():
        calls[0]+=1
        return int(calls[0]>30)
    cache.db.set_progress_handler(interrupt,1)
    with pytest.raises(sqlite3.OperationalError,match='interrupted'):cache.names(pair,scope)
    cache.db.set_progress_handler(cache._progress,4000)
    assert cache.db.execute('SELECT names_ready FROM scopes WHERE id=?',(scope,)).fetchone()[0]==0
    cache.names(pair,scope)
    assert cache.db.execute('SELECT names_ready FROM scopes WHERE id=?',(scope,)).fetchone()[0]==1
