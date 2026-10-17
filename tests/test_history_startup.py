"""One-time conversion/retirement and metadata-only startup regressions."""
import json
import threading
import pytest
from dgm import config,history_logs
from dgm.history_backend import close_indexes,log_mode,comparison_history
from dgm.history_migration import migrate,retire_legacy,automatic_needed
from dgm.store import Store
from dgm.comparison import Comparisons
from test_v2 import entry,snapshot


def seed():
    root=config.home()/'virtual';store=Store()
    try:
        a=snapshot(store,root,[entry(root/'f',value=10)],name='daily')
        b=snapshot(store,root,[entry(root/'f',value=20)],name='daily')
    finally:store.close()
    return a,b


def forbidden(*args,**kwargs):
    pytest.fail('Opening metadata must not read or rebuild full history')


def test_jsonl_preference_never_masks_existing_sqlite_history(isolated):
    a,b=seed();cfg=config.load();cfg['history_format']='jsonl';config.save(cfg)
    assert not log_mode() and automatic_needed()
    s=Store(readonly=True)
    try:assert [r['id'] for r in s.scans()]==[b,a]
    finally:s.close()
    assert not (config.home()/'data/history/catalog.json').exists()


def test_conversion_and_retirement_are_idempotent_without_payload_reads(isolated,monkeypatch):
    seed();result=migrate(remove_old=True)
    assert result['verified'] and result['legacy_removed_bytes']>0
    manifest=json.loads((config.home()/'data/history/migration.json').read_text(encoding='utf-8'))
    assert manifest['retirement']['pending'] is False
    before={p.name:p.read_bytes() for p in (config.home()/'data/history/scans').glob('*.gz')}
    monkeypatch.setattr(history_logs,'read',forbidden);monkeypatch.setattr(history_logs,'write',forbidden)
    assert not automatic_needed()
    again=migrate(remove_old=True)
    assert again['already_converted'] and not again['migrated'] and again['legacy_removed_bytes']==0
    assert retire_legacy()['already_retired']
    assert {p.name:p.read_bytes() for p in (config.home()/'data/history/scans').glob('*.gz')}==before


def test_retry_finishes_pending_retirement_without_reexport(isolated,monkeypatch):
    seed();migrate(remove_old=False)
    assert (config.home()/'data/history.sqlite').exists() and automatic_needed()
    monkeypatch.setattr(history_logs,'write',forbidden)
    result=migrate(remove_old=True)
    assert result['already_converted'] and result['legacy_removed_bytes']>0
    assert not (config.home()/'data/history.sqlite').exists()


def test_retirement_corrupt_log_preserves_original(isolated):
    seed();migrate(remove_old=False);source=config.home()/'data/history.sqlite';original=source.read_bytes()
    next((config.home()/'data/history/scans').glob('*.gz')).write_bytes(b'corrupt')
    with pytest.raises((ValueError,OSError)):migrate(remove_old=True)
    assert source.read_bytes()==original


def test_cancel_retirement_keeps_sqlite(isolated):
    from dgm.platform_ops import ProcessCancelled
    seed();migrate(remove_old=False);stop=threading.Event();stop.set()
    with pytest.raises(ProcessCancelled):retire_legacy(stop=stop)
    assert (config.home()/'data/history.sqlite').exists()


def test_conversion_only_deletes_original_databases_not_backups_or_exports(isolated):
    seed();extra=config.home()/'data/updates/keep-backup';extra.mkdir(parents=True)
    backup=extra/'history.sqlite';backup.write_bytes(b'USER BACKUP')
    csv=config.home()/'data/source.csv';csv.write_text('keep export')
    migrate(remove_old=True)
    assert backup.read_bytes()==b'USER BACKUP' and csv.read_text()=='keep export'


def test_auto_conversion_respects_opt_out_and_explicit_legacy_choice(isolated):
    seed();assert not automatic_needed()
    cfg=config.load();cfg.pop('history_format');config.save(cfg);assert automatic_needed()
    cfg['history_auto_migrate']=False;config.save(cfg);assert not automatic_needed()


def test_orphan_auxiliary_database_is_not_automatically_deleted(isolated):
    cfg=config.load();cfg['history_format']='jsonl';config.save(cfg)
    path=config.home()/'data/comparisons.sqlite';path.write_bytes(b'unknown old database')
    assert not automatic_needed() and path.read_bytes()==b'unknown old database'


def test_read_summary_uses_catalog_without_opening_comparison_engine(isolated,monkeypatch):
    a,b=seed();cache=Comparisons();pair=cache.prepare(a,b);cache.close()
    migrate(remove_old=True);close_indexes()
    monkeypatch.setattr(Comparisons,'__init__',forbidden)
    monkeypatch.setattr(history_logs,'read',forbidden)
    rows=comparison_history()
    assert len(rows)==1 and rows[0]['id']==pair and json.loads(rows[0]['summary'])['verified_delta']==10
    assert rows[0]['status']=='archived'
    assert not (config.home()/'data/tmp/history-index/history.sqlite').exists()


def test_legacy_summary_read_does_not_create_or_upgrade_database(isolated):
    a,b=seed();assert comparison_history()==[]
    cache=Comparisons();cache.prepare(a,b);cache.close()
    path=config.home()/'data/comparisons.sqlite';before=path.read_bytes()
    assert json.loads(comparison_history()[0]['summary'])['verified_delta']==10
    assert path.read_bytes()==before


def test_changed_legacy_is_not_retried_automatically_or_deleted(isolated,monkeypatch):
    import sqlite3
    seed();migrate(remove_old=False)
    source=config.home()/'data/history.sqlite'
    db=sqlite3.connect(source);db.execute("UPDATE scans SET profile='new legacy writes'");db.commit();db.close()
    assert not automatic_needed()
    monkeypatch.setattr(history_logs,'read',forbidden)
    with pytest.raises(RuntimeError,match='đã thay đổi'):retire_legacy()
    assert source.exists()


def test_legacy_write_during_verification_is_preserved(isolated,monkeypatch):
    import sqlite3
    seed();migrate(remove_old=False);source=config.home()/'data/history.sqlite'
    verify=history_logs.verify;changed=[]
    def racing(path,**kwargs):
        result=verify(path,**kwargs)
        if not changed:
            db=sqlite3.connect(source);db.execute("UPDATE scans SET profile='changed during verification'");db.commit();db.close()
            changed.append(True)
        return result
    monkeypatch.setattr(history_logs,'verify',racing)
    with pytest.raises(RuntimeError,match='vừa thay đổi'):retire_legacy()
    assert source.exists()


def test_missing_published_log_is_never_replaced_with_empty_metadata(isolated):
    from dgm.history_backend import manager
    a,b=seed();migrate(remove_old=True);s=Store();index=manager()
    path=index.scan_path(a);backup=path.read_bytes();catalog=(index.folder/'catalog.json').read_bytes();path.unlink()
    try:
        with pytest.raises(ValueError,match='không tạo log rỗng'):index.publish_scan(s.db,a)
        assert not path.exists() and (index.folder/'catalog.json').read_bytes()==catalog
    finally:
        path.write_bytes(backup);s.close();close_indexes()


def test_cli_repeated_migrate_with_retirement_is_a_fast_noop(isolated,monkeypatch):
    from typer.testing import CliRunner
    from dgm.cli import app
    seed();migrate(remove_old=True)
    monkeypatch.setattr(history_logs,'read',forbidden)
    result=CliRunner().invoke(app,['storage','migrate-history','--yes','--retire-old'])
    assert result.exit_code==0,result.output
    assert json.loads(result.stdout)['already_converted']
