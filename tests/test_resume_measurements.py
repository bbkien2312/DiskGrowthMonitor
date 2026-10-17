import json
import threading
from pathlib import Path
import pytest
from dgm import config
from dgm.scanner import scan
from dgm.task_journal import list_tasks,read,resume,discard,path_for,Journal
from dgm.history_backend import close_indexes
from dgm.platform_ops import ProcessCancelled
from dgm.measurements import reconciliation,partition_checks,provenance
from dgm.store import Store
from dgm.comparison import Comparisons
from test_v2 import entry,snapshot


def profile(root,engine='native'):
    return {'roots':[str(root)],'engine':engine,'strict_browsers':engine=='native','excludes':[],'mft':engine=='wiztree'}


def pending(action='scan'):
    return next(r for r in list_tasks() if r['action']==action and r['state']=='interrupted')


def test_reconcile_actual_37_numbers_without_fabricating_small_files():
    data={'logical':170688338704,'allocated':154893795328,'small_files':684767,'small_logical':72428619965,
          'volumes':{'C:\\':{'total':161290907648,'used':155753582592}},
          'root_overview':{'C:\\':{'export_used':155533979648}}}
    r=reconciliation(data,['C:\\'])[0]
    assert r['residual']==640184320 and r['basis']=='export'
    assert r['measured']<r['used']<r['total']<data['logical']
    assert data['small_logical']!=r['residual'] and data['small_files']==684767
    assert '≤10 MB' in r['note']
    assert reconciliation(data,['C:\\Windows'])[0]['measured'] is None
    data['allocated']=160000000000
    assert reconciliation(data,['C:\\'])[0]['warning']
    data['allocated']=None
    assert reconciliation(data,['C:\\'])[0]['residual'] is None


def test_provenance_does_not_invent_old_app_version():
    assert 'App ?' in provenance({'canonical_normalization_version':2})
    assert 'App 1.4.0' in provenance({'provenance':{'app_version':'1.4.0'},'contract':{'version':3}})


@pytest.mark.parametrize('log_mode',[False,True])
def test_resume_captured_native_after_indexes_closed_does_not_read_live_files(isolated,monkeypatch,log_mode):
    cfg=config.load();cfg['history_format']='jsonl' if log_mode else 'sqlite';config.save(cfg)
    root=isolated/'files';root.mkdir();file=root/'one';file.write_bytes(b'a'*100)
    aggregate=Store.aggregate
    def interrupt(self,scan_id,stop=None,notify=None):
        stop.set();raise ProcessCancelled()
    monkeypatch.setattr(Store,'aggregate',interrupt)
    first=scan('daily',profile(root));assert first['status']=='cancelled'
    saved=pending();assert saved['capture'] and saved['source_scan']==first['scan_id']
    close_indexes();file.write_bytes(b'b'*900)
    monkeypatch.setattr(Store,'aggregate',aggregate)
    import dgm.scanner as scanner
    monkeypatch.setattr(scanner,'native_entries',lambda *a,**k:pytest.fail('Resumed capture must not scan live files'))
    result=resume(saved['id'])
    assert result['logical']==100 and result['resumed'] is True
    assert result['source_scan']==first['scan_id'] and result['provenance']['app_version']
    assert result['captured_at']==first['captured_at'] and result['small_files']==1
    assert read(saved['id'])['state']=='complete'
    assert not (path_for(saved['id'])/'capture.jsonl.gz').exists()
    store=Store();assert store.scan(first['scan_id'])['status']=='cancelled';store.close();close_indexes()


def test_unfinished_native_scan_restarts_from_beginning(isolated,monkeypatch):
    root=isolated/'files';root.mkdir();file=root/'one';file.write_bytes(b'a'*100)
    import dgm.scanner as scanner
    original=scanner.native_entries
    def interrupted(policy,store,ident,stop,notify):
        yield entry(file,value=100)
        stop.set();raise ProcessCancelled()
    monkeypatch.setattr(scanner,'native_entries',interrupted)
    assert scan('daily',profile(root))['status']=='cancelled'
    row=pending();assert not row.get('capture')
    file.write_bytes(b'a'*400);monkeypatch.setattr(scanner,'native_entries',original)
    result=resume(row['id']);assert result['logical']==400 and not result.get('resumed')


def test_completed_wiztree_export_survives_cancelled_import(isolated,monkeypatch):
    import dgm.scanner as scanner
    from subprocess import CompletedProcess
    root=isolated/'files';root.mkdir();exe=isolated/'WizTree64.exe';exe.write_bytes(b'fixture')
    cfg=config.load();cfg['engines']['wiztree']=str(exe);cfg['source_archive_limit_mb']=0;config.save(cfg)
    calls=[];stop=threading.Event()
    def run(args,**kwargs):
        calls.append(args)
        output=Path(next(a.split('=',1)[1] for a in args if str(a).startswith('/export=')))
        import csv
        with output.open('w',encoding='utf-8',newline='') as stream:
            writer=csv.writer(stream);writer.writerow(['File Name','Size','Allocated','Attributes','MFT Record Number'])
            writer.writerow([str(root)+'\\',300,4096,16,1]);writer.writerow([str(root/'one'),300,4096,0,2])
        return CompletedProcess(args,0,'','')
    monkeypatch.setattr(scanner,'run',run);monkeypatch.setattr(scanner,'is_admin',lambda:True)
    def notify(event,data):
        if event=='info' and data['message']=='Đang nhập kết quả WizTree vào lịch sử...':stop.set()
    assert scan('daily',profile(root,'wiztree'),stop=stop,notify=notify)['status']=='cancelled'
    saved=pending();assert saved['sources']['0'] and not saved.get('capture')
    assert (path_for(saved['id'])/'source-000.csv').exists()
    started=saved['scan_context']['started'];exe.unlink()
    monkeypatch.setattr(scanner,'is_admin',lambda:False)
    import dgm.elevation as elevation
    monkeypatch.setattr(elevation,'request',lambda *a,**k:pytest.fail('Completed metadata must not elevate or start a new live scan'))
    result=resume(saved['id']);assert result['logical']==300 and result['allocated']==4096
    assert result['captured_at']==started and result['resumed']
    assert len(calls)==1 and read(saved['id'])['state']=='complete'
    assert not (path_for(saved['id'])/'source-000.csv').exists()


def test_corrupt_capture_refused_and_checkpoint_kept(isolated,monkeypatch):
    root=isolated/'files';root.mkdir();(root/'one').write_bytes(b'abc')
    def interrupt(self,ident,stop=None,notify=None):stop.set();raise ProcessCancelled()
    monkeypatch.setattr(Store,'aggregate',interrupt);scan('daily',profile(root))
    row=pending();path=path_for(row['id'])/'capture.jsonl.gz';path.write_bytes(b'broken')
    with pytest.raises(ValueError,match='Checksum'):resume(row['id'])
    assert read(row['id'])['state']=='interrupted' and path.exists()
    discard(row['id']);assert not path.exists() and (root/'one').exists()


def test_journal_rejects_delete_replay_path_traversal_and_live_owner(isolated):
    j=Journal('scan',{'name':'test','p':{}})
    with pytest.raises(RuntimeError,match='vẫn đang'):resume(j.row['id'])
    with pytest.raises(RuntimeError,match='đang chạy'):discard(j.row['id'])
    with pytest.raises(ValueError):path_for('../escape')
    with pytest.raises(ValueError):j.artifact('../one')
    j.save(state='interrupted',owner=None,action='cleanup-apply')
    with pytest.raises(ValueError,match='không được hỗ trợ'):read(j.row['id'])


def test_task_budget_protects_free_space(isolated):
    cfg=config.load();cfg['resume_limit_mb']=0;config.save(cfg)
    j=Journal('scan',{'name':'test','p':{}})
    with pytest.raises(RuntimeError,match='hạn mức'):j.budget()


def test_compare_resume_uses_same_snapshots_and_physical_unknowns(isolated):
    root=isolated/'virtual';store=Store()
    a=snapshot(store,root,[entry(root/'same',value=100,identity='one')])
    b=snapshot(store,root,[{**entry(root/'same',value=200,identity='one'),'allocated':None}])
    stop=threading.Event()
    def notify(event,data):
        if event=='task-progress':stop.set()
    cache=Comparisons(stop,notify)
    try:
        with pytest.raises(ProcessCancelled):cache.prepare(a,b)
    finally:cache.close()
    row=pending('comparison');pair=resume(row['id'])
    cache=Comparisons();summary=cache.summary(pair)
    assert summary['verified_common_delta']==100
    assert summary['verified_common_allocated_delta'] is None
    assert summary['unverified_common_allocated_files']==1
    assert all(v is not False for side in partition_checks(summary).values() for v in side.values())
    cache.close();store.close()


def test_partial_known_physical_delta_excludes_both_sides_of_unknown_file(isolated):
    root=isolated/'virtual';s=Store()
    a=snapshot(s,root,[{**entry(root/'unknown',value=100),'allocated':None},entry(root/'known',value=100)])
    b=snapshot(s,root,[entry(root/'unknown',value=200),entry(root/'known',value=125)])
    cache=Comparisons();pair=cache.prepare(a,b);summary=cache.summary(pair)
    assert summary['verified_common_allocated_delta']==25
    assert summary['unverified_common_allocated_files']==1
    cache.close();s.close()


def test_normalization_resume_keeps_source_and_applies_only_original_request(isolated):
    from dgm.normalization import Normalizations
    root=isolated/'virtual';s=Store();ident=snapshot(s,root,[entry(root/'one',value=100)])
    original=s.scan(ident,effective=False);stop=threading.Event()
    def notify(event,data):
        if event=='task-progress':stop.set()
    normal=Normalizations(stop,notify)
    with pytest.raises(ProcessCancelled):normal.inspect(ident,apply=False,force=True)
    normal.close();row=pending('normalization');result=resume(row['id'])
    assert result['after']['logical']==100 and not result['applied']
    assert result['after']['small_files']==1 and result['after']['small_logical']==100
    assert result['processing_provenance']['app_version']
    normal=Normalizations();assert normal.current(ident) is None
    assert s.scan(ident,effective=False)==original;normal.close();s.close()


def test_benchmark_resume_skips_finished_rounds(isolated):
    from dgm.benchmark import benchmark
    root=isolated/'files';root.mkdir();(root/'one').write_bytes(b'test');cancel=threading.Event()
    def notify(event,data):
        if event=='task-progress' and data.get('completed'):cancel.set()
    result=benchmark('daily',profile(root),engines=('native',),repeats=2,cancel=cancel,notify=notify)
    row=pending('benchmark');assert len(row['benchmark_results'])==1 and len(result)==1
    finished=resume(row['id']);assert len(finished)==2
    assert [r['round'] for r in finished]==[1,2] and read(row['id'])['state']=='complete'


def test_cli_work_queue_does_not_replay_commands_and_shares_journal(isolated):
    from dgm.cli import app
    from typer.testing import CliRunner
    j=Journal('scan',{'name':'daily','p':profile(isolated/'never-read')});j.save(state='interrupted',owner=None)
    runner=CliRunner();out=runner.invoke(app,['tasks','list'])
    assert out.exit_code==0 and json.loads(out.stdout)[0]['id']==j.row['id']
    out=runner.invoke(app,['tasks','limit','256']);assert out.exit_code==0 and config.load()['resume_limit_mb']==256
    out=runner.invoke(app,['tasks','discard',j.row['id'],'--yes'])
    assert out.exit_code==0 and not (path_for(j.row['id'])/'task.json').exists()


def test_orphan_owner_is_interrupted_and_no_longer_blocks_discard(isolated):
    j=Journal('scan',{'name':'daily','p':{}});j.save(owner={'pid':2147483647,'created':1})
    assert read(j.row['id'])['state']=='interrupted'
    discard(j.row['id']);assert not (path_for(j.row['id'])/'task.json').exists()


def test_actual_process_crash_after_capture_can_resume_without_live_rescan(isolated):
    import subprocess,sys
    cfg=config.load();cfg['history_format']='jsonl';config.save(cfg)
    root=isolated/'files';root.mkdir();source=root/'one';source.write_bytes(b'a'*100)
    code=f'''from pathlib import Path
import os
from dgm import config
config.ROOT=Path({str(config.ROOT)!r})
from dgm.store import Store
from dgm.scanner import scan
Store.aggregate=lambda *a,**k:os._exit(91)
scan('daily',{profile(root)!r})
'''
    result=subprocess.run([sys.executable,'-c',code],capture_output=True,timeout=20)
    assert result.returncode==91,result.stderr[-2000:]
    row=pending();assert row['capture']
    source.write_bytes(b'b'*900)
    result=resume(row['id']);assert result['logical']==100 and result['resumed']
    close_indexes()


def test_discard_cleans_only_owned_crash_partials_not_unrelated_files(isolated):
    j=Journal('scan',{'name':'daily','p':{}});j.save(state='interrupted',owner=None)
    partial=j.root/('capture.jsonl.gz.'+'a'*32+'.part');partial.write_bytes(b'partial')
    unknown=j.root/'keep-user-note.txt';unknown.write_text('keep')
    discard(j.row['id']);assert not partial.exists() and unknown.read_text()=='keep'


def test_capture_refuses_unloaded_history_instead_of_publishing_empty_checkpoint(isolated):
    cfg=config.load();cfg['history_format']='jsonl';config.save(cfg)
    root=isolated/'files';root.mkdir();(root/'one').write_bytes(b'abc')
    result=scan('daily',profile(root));close_indexes()
    store=Store();original=store.scan(result['scan_id'],effective=False);j=Journal('scan',{'name':'daily','p':profile(root)})
    with pytest.raises(ValueError,match='chưa được nạp'):
        j.capture(store,result['scan_id'],json.loads(original['summary']),'daily',profile(root),threading.Event())
    assert not j.artifact('capture.jsonl.gz').exists()
    j.save(state='interrupted',owner=None);store.close();close_indexes()


@pytest.mark.parametrize('sort',['disk_old','disk_new'])
@pytest.mark.parametrize('scoped',[False,True])
def test_disk_size_sort_is_global_before_cursor_paging(isolated,sort,scoped):
    root=isolated/'virtual';sub=root/'folder';store=Store()
    items=[entry(sub,'folder')]+[{**entry(sub/f'f{i:03}',value=i+1),'allocated':1000-i} for i in range(65)]
    a=snapshot(store,root,items);b=snapshot(store,root,items)
    cache=Comparisons();pair=cache.prepare(a,b);rows=[];cursor=None
    while True:
        page=cache.page(pair,sort=sort,kind='file',root=str(sub) if scoped else None,limit=20,cursor=cursor)
        rows.extend(page['rows']);cursor=page['cursor']
        if not cursor:break
    key='allocated_before' if sort=='disk_old' else 'allocated_after'
    assert len(rows)==65 and [r[key] for r in rows]==sorted([r[key] for r in rows],reverse=True)
    cache.close();store.close()
