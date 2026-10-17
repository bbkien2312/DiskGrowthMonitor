"""Owned fixtures only: no cleanup/scan/update of any real installed app."""
import json
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import pytest
from typer.testing import CliRunner
from dgm import storage_maintenance as storage,storage_safety as safety,app_update
from dgm.config import home
from dgm.cli import app
from dgm.store import Store
from dgm.comparison import Comparisons
from test_v2 import entry,snapshot


def session(index, *, phase='complete', pending=False, backup=True, shared_installer=None):
    base=home()/'data/updates';folder=base/f'session-{index:032x}';folder.mkdir(parents=True)
    installer=shared_installer or base/f'99.0.{index}-{index:012x}'/'installer.exe'
    installer.parent.mkdir(parents=True,exist_ok=True);installer.write_bytes(b'installer')
    data={'id':folder.name,'root':str(home()),'phase':phase,'restore_pending':pending,'installer':str(installer),'errors':[]}
    path=folder/'session.json';path.write_text(json.dumps(data),encoding='utf-8')
    os.utime(path,ns=(1_000_000_000*index,1_000_000_000*index))
    if backup:
        (folder/'backup').mkdir();(folder/'backup/history.sqlite').write_bytes(b'backup-'+str(index).encode())
        (folder/'backup/config.json').write_text('{}',encoding='utf-8')
    return folder,installer


def test_default_cleanup_keeps_one_backup_and_current_history(isolated):
    db=Store();db.close();history=home()/'data/history.sqlite';before=history.read_bytes()
    old,first=session(1);new,second=session(2)
    plan=storage.update_plan()
    assert str(new/'backup') in plan['retained_backups'] and plan['reclaimable_bytes']>0
    result=storage.cleanup_updates(preview=plan)
    assert result['removed_files']==4 and not result['errors']
    assert not (old/'backup').exists() and (new/'backup/history.sqlite').exists()
    assert not first.exists() and not second.exists()
    assert (old/'session.json').exists() and history.read_bytes()==before
    assert storage.cleanup_updates()['removed_bytes']==0


@pytest.mark.parametrize('phase,pending',[('failed',False),('prepared',False),('waiting',False),('complete',True)])
def test_failed_active_recovery_sessions_and_shared_installer_protected(isolated,phase,pending):
    good,installer=session(1)
    protected,_=session(2,phase=phase,pending=pending,shared_installer=installer)
    storage.cleanup_updates(keep=0)
    assert installer.exists() and (protected/'backup/history.sqlite').exists()
    assert not (good/'backup').exists()


def test_success_without_backup_does_not_consume_keep_slot(isolated):
    old,_=session(1);session(2,backup=False)
    storage.cleanup_updates()
    assert (old/'backup/history.sqlite').exists()


def test_changed_file_is_not_deleted_after_preview(isolated):
    old,installer=session(1);plan=storage.update_plan(keep=0)
    installer.write_bytes(b'different content')
    backup=old/'backup/history.sqlite';backup.write_bytes(b'changed backup')
    storage.cleanup_updates(keep=0,preview=plan)
    assert installer.exists() and backup.exists()


def test_unknown_backup_file_and_malformed_session_fail_closed(isolated):
    folder,installer=session(1);(folder/'backup/keep-user.txt').write_text('keep')
    other,_=session(2);(other/'session.json').write_text('{broken')
    storage.cleanup_updates(keep=0)
    assert (folder/'backup/keep-user.txt').exists() and (folder/'backup/history.sqlite').exists()
    assert (other/'backup/history.sqlite').exists() and installer.exists()


def test_active_lease_and_maintenance_lock_block_cleanup(isolated,monkeypatch):
    folder,installer=session(1)
    monkeypatch.setattr(app_update,'active_update',lambda:True)
    with pytest.raises(RuntimeError,match='Đang cập nhật'):storage.cleanup_updates(keep=0)
    monkeypatch.setattr(app_update,'active_update',lambda:False)
    with storage.maintenance_lock():
        with pytest.raises(RuntimeError,match='bảo trì'):storage.cleanup_updates(keep=0)
    assert installer.exists() and (folder/'backup/history.sqlite').exists()


def test_cleanup_rejects_linked_backup(isolated):
    folder,installer=session(1,backup=False);outside=isolated/'outside';outside.mkdir();keep=outside/'history.sqlite';keep.write_bytes(b'keep')
    link=folder/'backup'
    try:link.symlink_to(outside,target_is_directory=True)
    except OSError:
        import subprocess
        subprocess.run(['cmd','/d','/c','mklink','/J',str(link),str(outside)],check=True,capture_output=True,creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        plan=storage.update_plan(keep=0)
        assert not any(r['path']==str(keep) for r in plan['files'])
        storage.cleanup_updates(keep=0)
        assert keep.read_bytes()==b'keep'
    finally:
        if link.is_junction():os.rmdir(link)
        else:link.unlink()


def test_report_readonly_database_pages_and_cli_preview(isolated):
    cache=home()/'data/comparisons.sqlite'
    with sqlite3.connect(cache) as db:
        db.execute('CREATE TABLE fixture(data BLOB)');db.execute('INSERT INTO fixture VALUES(zeroblob(200000))');db.commit()
        db.execute('DELETE FROM fixture');db.commit()
    before=cache.read_bytes();session(1);session(2)
    report=storage.storage_report()
    assert report['databases']['comparisons.sqlite']['reusable_bytes']>0 and cache.read_bytes()==before
    result=CliRunner().invoke(app,['storage','cleanup-updates','--preview'])
    assert result.exit_code==0 and json.loads(result.output)['keep_backups']==1
    assert len(list((home()/'data/updates').glob('session-*/backup/history.sqlite')))==2
    result=CliRunner().invoke(app,['storage','cleanup-updates','--yes'])
    assert result.exit_code==0 and json.loads(result.output)['history_preserved']


def test_legacy_cache_compaction_preserves_rows_and_releases_pages(isolated):
    path=home()/'data/comparisons.sqlite'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE fixture(id INTEGER PRIMARY KEY,data BLOB)')
        db.executemany('INSERT INTO fixture(data) VALUES(zeroblob(100000))',[() for _ in range(20)])
        db.execute('DELETE FROM fixture WHERE id>1');db.commit()
    result=storage.compact_comparison_cache()
    assert result['reclaimed_bytes']>1_000_000
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT id,length(data) FROM fixture').fetchall()==[(1,100000)]
        assert db.execute('PRAGMA auto_vacuum').fetchone()[0]==2


def test_low_space_blocks_compaction_before_changing_cache(isolated,monkeypatch):
    path=home()/'data/comparisons.sqlite'
    with sqlite3.connect(path) as db:db.execute('CREATE TABLE keep(id INTEGER)')
    before=path.read_bytes();monkeypatch.setattr(safety.shutil,'disk_usage',lambda _:SimpleNamespace(free=100))
    with pytest.raises(safety.LowSpaceError,match='dự phòng'):storage.compact_comparison_cache()
    assert path.read_bytes()==before


def test_sqlite_guard_keeps_space_error_instead_of_interrupted(isolated,monkeypatch):
    db=sqlite3.connect(':memory:',factory=safety.SpaceConnection);guard=safety.SpaceGuard();db.space_guard=guard
    monkeypatch.setattr(safety.shutil,'disk_usage',lambda _:SimpleNamespace(free=1))
    db.set_progress_handler(guard.progress,1)
    with pytest.raises(safety.LowSpaceError,match='Không đủ dung lượng'):db.execute('SELECT 1')
    db.set_progress_handler(None,0);db.close()


def test_scan_low_space_creates_no_snapshot(isolated,monkeypatch):
    from dgm.scanner import scan
    monkeypatch.setattr(safety.shutil,'disk_usage',lambda _:SimpleNamespace(free=1))
    with pytest.raises(safety.LowSpaceError):scan('fixture',{'roots':[str(isolated)],'engine':'native'})
    assert not (home()/'data/history.sqlite').exists()


def test_cached_pair_readable_but_new_build_blocked_when_low_space(isolated,monkeypatch):
    root=isolated/'virtual';s=Store();a=snapshot(s,root,[entry(root/'f',value=10)])
    b=snapshot(s,root,[entry(root/'f',value=20)]);c=Comparisons();pair=c.prepare(a,b)
    monkeypatch.setattr(safety.shutil,'disk_usage',lambda _:SimpleNamespace(free=1))
    assert c.prepare(a,b)==pair and c.page(pair)['rows']
    with pytest.raises(safety.LowSpaceError):c.prepare(None,b)
    c.close();s.close()


def test_new_cache_incremental_reclamation_and_reserve_estimate(isolated):
    store=Store();store.close()
    cache=Comparisons()
    assert cache.db.execute('PRAGMA auto_vacuum').fetchone()[0]==2
    cache.close()
    value=safety.require_space(123)
    assert value['required_bytes']==1_024_000_123


def test_parallel_cache_schema_open_rechecks_under_sqlite_write_lock(isolated,monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    store=Store();store.close();cache=Comparisons();path=cache.path;cache.close()
    with sqlite3.connect(path) as db:db.execute('ALTER TABLE pairs DROP COLUMN dataset_signature')
    barrier=threading.Barrier(2);seen=set();original=safety.SpaceConnection.execute
    def execute(self,sql,*args,**kwargs):
        result=original(self,sql,*args,**kwargs)
        if sql=='PRAGMA table_info(pairs)' and threading.get_ident() not in seen:
            seen.add(threading.get_ident());values=result.fetchall();barrier.wait(timeout=10);return values
        return result
    monkeypatch.setattr(safety.SpaceConnection,'execute',execute)
    def opened():
        cache=Comparisons()
        try:return [r[1] for r in cache.db.execute('PRAGMA table_info(pairs)')].count('dataset_signature')
        finally:cache.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:opened(),range(2)))
    assert results==[1,1]


def test_history_read_tolerates_normalization_file_before_schema_publication(isolated):
    from dgm.normalization import Normalizations
    store=Store();root=isolated/'virtual';sid=snapshot(store,root,[entry(root/'f',value=12)])
    with sqlite3.connect(home()/'data/normalization.sqlite'):pass
    assert store.scans()[0]['id']==sid
    norm=Normalizations();norm.inspect(sid,apply=True);norm.close()
    assert store.scans()[0]['id']==sid
    store.close()
