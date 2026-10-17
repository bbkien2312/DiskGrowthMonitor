import json
import threading
from pathlib import Path
import pytest
from dgm import config
from dgm.store import Store
from dgm.comparison import Comparisons,VERSION
from dgm.snapshot_contract import canonical_entry,describe
from test_v2 import entry,snapshot
from test_ui_update_core import release,session


def test_reopen_cache_has_no_index_rebuild_or_mass_update(isolated,monkeypatch):
    import sqlite3
    s=Store();root=isolated/'virtual';a=snapshot(s,root,[entry(root/'f',value=10)])
    b=snapshot(s,root,[entry(root/'f',value=20)])
    c=Comparisons();pair=c.prepare(a,b);c.scope(pair,c.page(pair,kind='folder')['rows'][0]['path_id']);c.close()
    trace=[];connect=sqlite3.connect
    def tracked(*args,**kw):
        db=connect(*args,**kw);db.set_trace_callback(trace.append);return db
    monkeypatch.setattr(sqlite3,'connect',tracked)
    for _ in range(3):
        c=Comparisons();assert c.prepare(a,b)==pair;c.page(pair);c.close()
    assert not any(q.startswith(('DROP INDEX','UPDATE scope_items','UPDATE pairs SET summary')) for q in trace)
    assert not any('CREATE TEMP TABLE compare_' in q for q in trace)
    s.close()


def test_legacy_key_migration_cancel_rolls_back_and_retries_without_losing_history(isolated,monkeypatch):
    import dgm.store as store_module
    from dgm.platform_ops import ProcessCancelled
    s=Store(migrate_keys=False);root=isolated/'legacy';sid=s.begin_scan('daily','scope','native',{'roots':[str(root)]})
    s.put(sid,[entry(root/f'f{i}',value=5) for i in range(2000)]);s.finish(sid,'complete',s.totals(sid));s.close()
    stop=threading.Event();original=store_module.comparison_path_key;calls=[0]
    def cancelling(path):
        calls[0]+=1
        if calls[0]>20:stop.set()
        return original(path)
    monkeypatch.setattr(store_module,'comparison_path_key',cancelling)
    with pytest.raises(ProcessCancelled):Store(stop=stop)
    s=Store(readonly=True)
    assert s.scan(sid)['status']=='complete' and s.totals(sid)['files']==2000
    assert not s.db.execute("SELECT 1 FROM sqlite_master WHERE name='store_schema'").fetchone()
    s.close();monkeypatch.setattr(store_module,'comparison_path_key',original)
    s=Store();assert s.db.execute('SELECT COUNT(*) FROM paths WHERE path_key IS NULL').fetchone()[0]==0
    assert s.totals(sid)['logical']==10000;s.close()


@pytest.mark.parametrize('first,second',[('wiztree','native'),('native','gdu'),('gdu','windirstat'),('windirstat','wiztree')])
def test_cross_engine_contract_has_no_case_time_or_folder_false_delta(isolated,monkeypatch,first,second):
    import dgm.scanner as scanner
    import dgm.engines as engines
    root=isolated/'virtual';p={'roots':[str(root)],'strict_browsers':False,'mft':False}
    call=[0]
    def rows(*args):
        newer=bool(call[0]);call[0]+=1
        folder=root/('sub' if newer else 'Sub')
        yield {**entry(root,'folder',99999),'modified':1.99 if newer else 1}
        yield entry(folder,'folder',700 if newer else 900)
        file=entry(folder/('FILE.bin' if newer else 'file.BIN'),value=123)
        file['modified']=1.99 if newer else 1
        file['identity']='wiz:vol:7' if (second if newer else first)=='wiztree' else 'native:7'
        yield file
    monkeypatch.setattr(scanner,'wiztree_entries',rows)
    monkeypatch.setattr(scanner,'native_entries',rows)
    monkeypatch.setattr(engines,'gdu_entries',rows)
    monkeypatch.setattr(engines,'windirstat_entries',rows)
    a=scanner.scan('demo',{**p,'engine':first});b=scanner.scan('demo',{**p,'engine':second})
    assert a['logical']==b['logical']==123
    c=Comparisons();pair=c.prepare(a['scan_id'],b['scan_id'])
    rows=c.page(pair)['rows']
    assert len(rows)==3 and all(r['delta']==0 and r['change']=='same' for r in rows)
    assert not c.db.execute('SELECT 1 FROM normalized_scans').fetchone()
    assert c.summary(pair)['compatibility']['cross_engine']
    c.close()


def test_unknown_allocation_propagates_to_folder_without_false_increase(isolated):
    root=isolated/'virtual';s=Store()
    a=snapshot(s,root,[entry(root/'file',value=5)])
    unknown=canonical_entry({**entry(root/'file',value=5),'allocated':None,'created':None})
    b=snapshot(s,root,[unknown]);assert s.totals(b)['allocated'] is None
    c=Comparisons();pair=c.prepare(a,b)
    assert all(r['allocated_delta'] is None and r['delta']==0 for r in c.page(pair)['rows'])
    c.close();s.close()


def test_hardlink_export_owner_is_stable_and_keeps_physical_bytes(isolated):
    root=isolated/'virtual';s=Store()
    a=entry(root/'A',value=5,identity='same');a['allocated']=0
    b=entry(root/'b',value=5,identity='same');b['allocated']=4096
    sid=snapshot(s,root,[a,b]);rows=s.entries(sid)
    assert s.totals(sid)['logical']==10 and s.totals(sid)['allocated']==4096
    assert next(r for r in rows if r['path']==str(root/'A'))['allocated']==4096
    assert next(r for r in rows if r['path']==str(root/'b'))['allocated']==0
    s.close()


def test_coverage_is_scoped_not_a_blanket_engine_rule(isolated):
    root=isolated/'virtual';s=Store()
    a=snapshot(s,root,[entry(root/'unverified','folder'),entry(root/'verified','folder')])
    b=snapshot(s,root,[entry(root/'unverified','folder'),entry(root/'verified','folder'),
        entry(root/'unverified'/'new',value=5),entry(root/'verified'/'new',value=5)])
    for sid in (a,b):
        summary=json.loads(s.scan(sid)['summary']);summary.update(coverage_unverified=True,
            coverage={'version':1,'unknown_roots':[str(root/'unverified')]});s.record_stage(sid,summary)
    c=Comparisons();pair=c.prepare(a,b);rows=c.page(pair,kind='file')['rows']
    private=c.page(pair,kind='file',section='private')['rows']
    assert next(r for r in private if 'unverified' in r['path'])['change']=='unknown'
    assert next(r for r in rows if '\\verified\\' in r['path'])['change']=='new'
    c.close();s.close()


def test_datetime_offsets_are_not_overwritten():
    from dgm.scanner import stamp
    assert stamp('2026-10-16T14:00:00+07:00')==stamp('2026-10-16T07:00:00Z')


def test_native_same_size_changes_within_one_second_are_not_discarded(isolated):
    s=Store();root=isolated/'virtual'
    a=snapshot(s,root,[canonical_entry(entry(root/'file',value=5,modified=100.1))])
    b=snapshot(s,root,[canonical_entry(entry(root/'file',value=5,modified=100.2))])
    c=Comparisons();pair=c.prepare(a,b)
    assert c.page(pair,kind='file')['rows'][0]['change']=='modified'
    c.close();s.close()


def test_windows_allocation_is_not_logical_file_length(isolated):
    import os
    if os.name!='nt':pytest.skip('Windows allocation API')
    from dgm.scanner import metadata
    file=isolated/'not-cluster-aligned.bin';file.write_bytes(b'x'*5001)
    row=metadata(file,'file')
    assert row['logical']==5001 and row['allocated'] is not None
    assert row['allocated']!=5001


def test_sparse_file_keeps_actual_allocation_instead_of_logical_size(isolated):
    import os
    if os.name!='nt':pytest.skip('Windows sparse allocation API')
    import win32file
    from dgm.scanner import metadata
    file=isolated/'owned-sparse.bin';file.touch()
    handle=win32file.CreateFile(str(file),0x40000000,7,None,3,0,None)
    try:win32file.DeviceIoControl(handle,0x900C4,None,0)
    finally:handle.Close()
    with file.open('r+b') as stream:stream.truncate(4_000_001)
    row=metadata(file,'file')
    # NTFS may retain the final partial compression unit after extending EOF.
    assert row['logical']==4_000_001 and 0<=row['allocated']<row['logical']


def test_restart_observer_starts_before_uac_and_cancel_keeps_app(session,monkeypatch):
    from dgm import app_update as update
    from dgm.elevation import ElevationCancelled
    from win32com.shell import shell
    ticket,_=session;path,state=update.load_session(ticket['session'])
    update.save_session(path,state,reopen=True)
    calls=[]
    monkeypatch.setattr(update.subprocess,'Popen',lambda argv,**kw:calls.append(('normal-observer',argv,kw)))
    def denied(**kwargs):
        calls.append(('elevate',kwargs['lpVerb']))
        exc=OSError('cancelled');exc.winerror=1223;raise exc
    monkeypatch.setattr(shell,'ShellExecuteEx',denied)
    with pytest.raises(ElevationCancelled):update.launch(ticket)
    assert calls[0][0]=='normal-observer' and calls[1]==('elevate','runas')
    assert 'update-watch.ps1' in ' '.join(calls[0][1]) and not update.active_update()


def test_old_comparison_history_remains_visible_after_algorithm_upgrade(isolated):
    s=Store();root=isolated/'virtual';sid=snapshot(s,root,[])
    c=Comparisons();pair=c.prepare(None,sid)
    c.db.execute('UPDATE pairs SET version=? WHERE id=?',(VERSION-1,pair));c.db.commit()
    assert c.history()[0]['id']==pair
    new=c.prepare(None,sid);assert new!=pair
    assert [r['id'] for r in c.history()]==[new]
    c.close();s.close()


def test_one_update_action_checks_downloads_and_hands_off_in_order(isolated,monkeypatch):
    from dgm import app_update as update
    calls=[];events=[];release={'available':True,'version':'99.0.0'}
    monkeypatch.setattr(update,'check',lambda:calls.append('check') or release)
    monkeypatch.setattr(update,'download',lambda *args:calls.append('download') or 'installer')
    monkeypatch.setattr(update,'prepare_install',lambda *args,**kw:calls.append(('prepare',kw['reopen'])) or {'ticket':1})
    monkeypatch.setattr(update,'launch',lambda ticket:calls.append('launch') or {'launched':True})
    result=update.start_update(notify=lambda event,data:events.append((event,data)),reopen=True)
    assert result['launched'] and calls==['check','download',('prepare',True),'launch']
    progress=[data['percent'] for event,data in events if event=='task-progress']
    assert progress==sorted(progress) and progress[-1]==95
    monkeypatch.setattr(update,'check',lambda:{'available':False})
    assert not update.start_update()['launched']


def test_cancel_update_after_download_does_not_launch_installer(isolated,monkeypatch):
    from dgm import app_update as update
    from dgm.platform_ops import ProcessCancelled
    stop=threading.Event()
    monkeypatch.setattr(update,'check',lambda:{'available':True})
    monkeypatch.setattr(update,'download',lambda *a:stop.set() or 'file')
    monkeypatch.setattr(update,'prepare_install',lambda *a,**kw:pytest.fail('Must not prepare after cancel'))
    with pytest.raises(ProcessCancelled):update.start_update(stop)


def test_real_four_engines_agree_on_one_owned_fixture(isolated):
    import os
    from test_live_engines import TOOLS
    from dgm.scanner import scan
    from dgm.platform_ops import is_admin
    if is_admin():pytest.skip('No privileged filesystem indexing in fixture tests')
    if not all(path.is_file() for path in TOOLS.values()):pytest.skip('Portable engines not installed')
    cfg=config.load();cfg['engines'].update({key:str(path) for key,path in TOOLS.items()});config.save(cfg)
    root=isolated/'owned-engine-fixture';(root/'sub').mkdir(parents=True)
    (root/'regular.bin').write_bytes(b'a'*5001)
    (root/'sub'/'large.bin').write_bytes(b'b'*8193)
    os.link(root/'sub'/'large.bin',root/'hardlink.bin')
    p={'roots':[str(root)],'strict_browsers':False,'mft':False,'excludes':[]}
    results=[]
    for engine in ('native','wiztree','gdu','windirstat'):
        result=scan('owned-fixture',{**p,'engine':engine});results.append(result)
        assert result['status'] in ('complete','partial'),result
    baseline=results[0]
    for result in results[1:]:
        assert (result['files'],result['logical'],result['allocated'])==(baseline['files'],baseline['logical'],baseline['allocated']),results
        c=Comparisons();pair=c.prepare(baseline['scan_id'],result['scan_id'])
        assert all(row['delta']==0 and row['allocated_delta']==0 for row in c.page(pair)['rows'])
        c.close()
