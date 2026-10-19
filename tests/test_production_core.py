import json
import os
import threading
import time

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import pytest
from typer.testing import CliRunner
from dgm import config,production_core as core
from dgm.columnar_ingest import import_entries
from dgm.columnar_store import Repository
from test_columnar_core import entry,meta,legacy_fixture


@pytest.fixture
def production(isolated):
    cfg=config.load();cfg['scan_backend']='columnar';cfg['history_format']='jsonl';config.save(cfg)
    return core.repository()


def test_main_repository_adopts_preview_without_copy_or_id_reset(isolated):
    root=config.home()/'data/parquet-preview/data/columnar-preview'
    preview=Repository(root)
    first=import_entries([entry('C:\\data\\a')],meta(),repository=preview)
    cfg=config.load();cfg['scan_backend']='columnar';config.save(cfg)
    main=core.repository()
    assert main.production and main.root==preview.root and main.get(first['id'])==first
    assert not (config.home()/'data/columnar/snapshots').exists()


def test_production_cli_history_compare_tree_export_use_same_store(production,tmp_path):
    from dgm.cli import app
    a=import_entries([entry('C:\\data',kind='folder',size=0,allocated=0),entry('C:\\data\\a',10,8192)],
        meta(profile='daily',started='2026-01-01T00:00:00+00:00'),repository=production)
    b=import_entries([entry('C:\\data',kind='folder',size=0,allocated=0),entry('C:\\data\\a',1000,4096)],
        meta(profile='daily',started='2026-01-02T00:00:00+00:00'),repository=production)
    runner=CliRunner()
    def call(args):
        result=runner.invoke(app,args)
        assert result.exit_code==0,result.output
        return json.loads(result.stdout)
    assert call(['history'])[0]['id']==b['id']
    page=call(['compare',str(b['id']),'--minimum-mb','0','--kind','file'])
    assert page['measurement']=='allocated' and page['rows'][0]['delta']==-4096
    assert call(['compare',str(b['id']),'--minimum-mb','0','--kind','file','--measure','logical'])['rows'][0]['delta']==990
    assert call(['tree','--newer',str(b['id'])])['rows'][0]['after']==4096
    target=tmp_path/'export.csv'
    assert call(['export',str(b['id']),str(target)])['backend']=='parquet' and target.exists()
    assert len(call(['comparison-history']))>=1
    assert production.footprint()['work_bytes']==0


def test_production_scan_lock_stop_journal_and_scheduler(production,tmp_path,monkeypatch):
    from dgm import scanner,schedule
    from dgm.scan_control import current,request_stop
    root=tmp_path/'files';root.mkdir();(root/'a.txt').write_text('example')
    p={'roots':[str(root)],'engine':'native','mft':False,'strict_browsers':False,'excludes':[]}
    original=scanner.native_entries
    observed=[]
    def read(policy,sink,ident,stop,notify):
        observed.append(current()['running'])
        yield from original(policy,sink,ident,stop,notify)
    monkeypatch.setattr(scanner,'native_entries',read)
    result=schedule.scan('daily',p,allow_uac=False)
    assert observed==[True] and result['backend']=='parquet' and result['files']==1
    assert not current()['running'] and production.get(result['scan_id'])['scope']['implicit_excludes']==[str(config.home())]
    def cancel(policy,sink,ident,stop,notify):
        assert request_stop()['requested']
        deadline=time.monotonic()+2
        while not stop.is_set() and time.monotonic()<deadline:time.sleep(.01)
        yield from original(policy,sink,ident,stop,notify)
    monkeypatch.setattr(scanner,'native_entries',cancel)
    stopped=core.scan('daily',p)
    assert stopped['status']=='cancelled' and not current()['running']
    from dgm.columnar_jobs import Jobs
    jobs=Jobs(production);assert jobs.get(stopped['job_id'])['state']=='stopped'
    monkeypatch.setattr(scanner,'native_entries',read)
    resumed=jobs.execute(stopped['job_id'])
    assert resumed[0]['summary']['files']==1


def test_legacy_migration_is_explicit_idempotent_preserves_source(production,tmp_path):
    source=tmp_path/'old'
    legacy_fixture(source,[entry('C:\\data\\a')])
    plan=core.legacy_plan(source,production)
    assert len(plan['pending_ids'])==1
    originals={str(p):p.read_bytes() for p in (source/'data/history').rglob('*') if p.is_file()}
    results=core.migrate_legacy(source_home=source)
    assert results and not core.legacy_plan(source,production)['pending_ids']
    assert core.migrate_legacy(source_home=source)==[]
    assert originals=={str(p):p.read_bytes() for p in (source/'data/history').rglob('*') if p.is_file()}


def test_production_uac_routes_to_columnar_not_legacy(production,monkeypatch):
    from dgm import platform_ops,elevation
    monkeypatch.setattr(platform_ops,'is_admin',lambda:False)
    calls=[]
    monkeypatch.setattr(elevation,'request',lambda action,payload,**kw:calls.append((action,payload)) or {'status':'test'})
    p={'roots':['C:\\'],'engine':'wiztree','mft':True,'strict_browsers':False}
    assert core.scan('daily',p)['status']=='test'
    assert calls[0][0]=='columnar_scan' and calls[0][1]['profile']==config.normalize_profile(p)
    elevation.validate(*calls[0])
    with pytest.raises(PermissionError):core.scan('daily',p,allow_uac=False)


def test_main_gui_keeps_feature_tabs_with_columnar_inspection(production,monkeypatch):
    from PySide6.QtWidgets import QApplication,QMessageBox
    from dgm.main_ui import MainWindow
    from dgm.history_backend import close_indexes
    monkeypatch.setattr(MainWindow,'doctor',lambda self:None)
    monkeypatch.setattr(MainWindow,'refresh_background',lambda self:None)
    errors=[]
    monkeypatch.setattr(QMessageBox,'warning',lambda parent,title,text:errors.append(text))
    import_entries([entry('C:\\data',kind='folder',size=0,allocated=0),entry('C:\\data\\a',100000000,8192)],meta(profile='daily'),repository=production)
    app=QApplication.instance() or QApplication([])
    window=MainWindow();window.show()
    def wait():
        until=time.monotonic()+20
        while window.workers and time.monotonic()<until:app.processEvents();time.sleep(.01)
        assert not window.workers
    try:
        app.processEvents()
        assert [window.tabs.tabText(i) for i in range(window.tabs.count())]==['Kiểm tra','Hệ thống','Dọn dẹp','Lịch chạy','Nhật ký','Lịch sử']
        from dgm.browser_ui import DiskBrowser,PAGE_SIZES
        from dgm.history_ui import HistoryPanel
        assert isinstance(window.browser,DiskBrowser) and isinstance(window.history_panel,HistoryPanel)
        assert not hasattr(window,'fast') and not window.sessions.queries
        assert window.browser.isVisible() and window.tree.isVisible()
        assert [window.browser.page_size.itemData(i) for i in range(window.browser.page_size.count())]==list(PAGE_SIZES)
        assert window.browser.sections.count()==2 and len(window.browser.metrics)==4
        assert window.browser.compare_help and window.browser.legend
        window.threshold.setValue(0);window.compare();wait()
        assert not errors and window.sessions.queries and window.browser.model.rowCount()>0
        assert window.tree.topLevelItemCount()>0
        assert all(card.caption.text()!='—' for card in window.browser.metrics)
        assert core.comparison_history()
        window.poll_scan_state();window.render_background()
        assert not errors
        window.tabs.setCurrentIndex(5);app.processEvents()
        assert window.history_panel.scans.isVisible()
    finally:
        for worker in window.workers:worker.stop.set()
        wait();window.close();app.processEvents();close_indexes()
    assert production.footprint()['work_bytes']==0


@pytest.mark.parametrize('round_number',[0,1,2])
def test_main_gui_native_scan_opens_result_and_releases_cache(production,tmp_path,monkeypatch,round_number):
    from PySide6.QtWidgets import QApplication,QMessageBox
    from dgm.main_ui import MainWindow
    from dgm.history_backend import close_indexes
    root=tmp_path/'scan-fixture';root.mkdir();(root/'test.txt').write_text('local test only')
    cfg=config.load();cfg['profiles']['daily'].update(roots=[str(root)],engine='native',mft=False);config.save(cfg)
    monkeypatch.setattr(MainWindow,'doctor',lambda self:None)
    monkeypatch.setattr(MainWindow,'refresh_background',lambda self:None)
    monkeypatch.setattr(MainWindow,'ask_run_profile',lambda *args:'save')
    errors=[];monkeypatch.setattr(QMessageBox,'warning',lambda parent,title,text:errors.append(text))
    app=QApplication.instance() or QApplication([]);window=MainWindow();window.show()
    try:
        window.threshold.setValue(0);window.begin_scan()
        until=time.monotonic()+20
        while (window.workers or not window.sessions.queries) and time.monotonic()<until:
            app.processEvents();time.sleep(.01)
        assert not errors and not window.workers and window.sessions.queries
        assert production.list()[0]['summary']['files']==1
        assert window.browser.model.rowCount()>0
        window.threshold.setValue(20)
        until=time.monotonic()+10
        while window.workers and time.monotonic()<until:app.processEvents();time.sleep(.01)
        assert window.browser.query_options['minimum']==20_000_000 and window.browser.model.rowCount()==0
        window.clear_comparison()
        assert not window.sessions.queries and production.footprint()['work_bytes']==0
    finally:
        for worker in window.workers:worker.stop.set()
        while window.workers:app.processEvents();time.sleep(.01)
        window.close();app.processEvents();close_indexes()


def test_main_close_stops_active_core_worker_before_exiting(production,monkeypatch):
    from PySide6.QtWidgets import QApplication
    from dgm.main_ui import MainWindow
    from dgm.history_backend import close_indexes
    from dgm.platform_ops import ProcessCancelled
    monkeypatch.setattr(MainWindow,'doctor',lambda self:None)
    monkeypatch.setattr(MainWindow,'refresh_background',lambda self:None)
    monkeypatch.setattr(MainWindow,'ask_close_tasks',lambda self:'stop')
    app=QApplication.instance() or QApplication([]);window=MainWindow();window.show()
    entered=threading.Event();closed=[];window.closed.connect(lambda:closed.append(True))
    def hold(worker):
        entered.set();worker.stop.wait(5)
        if worker.stop.is_set():raise ProcessCancelled('Stopped safely')
    window.job(hold,lambda _:None)
    assert entered.wait(2)
    window.close()
    until=time.monotonic()+10
    while not closed and time.monotonic()<until:app.processEvents();time.sleep(.01)
    assert closed and not window.workers and not window.isVisible()
    close_indexes();assert production.footprint()['work_bytes']==0


def test_atomic_journal_retries_windows_reader_lock(tmp_path,monkeypatch):
    from pathlib import Path
    from dgm.columnar_store import atomic_json
    target=tmp_path/'job.json';target.write_text('{"state":"old"}')
    replace=Path.replace;attempts=[]
    def busy_once(path,destination):
        if destination==target:
            attempts.append(True)
            if len(attempts)==1:raise PermissionError('A concurrent Windows reader holds the target')
        return replace(path,destination)
    monkeypatch.setattr(Path,'replace',busy_once)
    atomic_json(target,{'state':'complete'})
    assert len(attempts)==2 and json.loads(target.read_text())=={'state':'complete'}
    assert not list(tmp_path.glob('*.part'))
