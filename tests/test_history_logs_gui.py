import time
import threading
import pytest
from PySide6.QtWidgets import QApplication,QMessageBox
from dgm import config
from dgm.store import Store
from dgm.gui import Window
from dgm.history_backend import database_path,close_indexes
from test_v2 import entry,snapshot
from test_v2_gui import pump,gui


@pytest.fixture
def log_gui(isolated,monkeypatch):
    import dgm.background as background
    cfg=config.load();cfg['history_format']='jsonl';cfg['source_archive_limit_mb']=0;config.save(cfg)
    monkeypatch.setattr(background,'status',lambda:{'task':{'installed':False},'service':{'installed':False},'scan':{'running':False}})
    monkeypatch.setattr(QMessageBox,'warning',lambda *a:pytest.fail(str(a[-1])))
    app=QApplication.instance() or QApplication([]);w=Window();w.show();pump(app,lambda:not w.workers)
    yield app,w
    for worker in list(w.workers):worker.stop.set()
    pump(app,lambda:not w.workers);w.close();app.processEvents();close_indexes()


def test_gui_log_compare_clear_index_and_compare_again(log_gui,isolated,monkeypatch):
    app,w=log_gui;root=isolated/'virtual';store=Store()
    snapshot(store,root,[entry(root/'keep',value=10),entry(root/'gone',value=7)],name='daily')
    snapshot(store,root,[entry(root/'keep',value=20),entry(root/'new',value=3)],name='daily');store.close()
    w.threshold.setValue(0);w.refresh_history();w.compare();pump(app,lambda:not w.workers)
    assert w.browser.current_summary['verified_delta']==6
    monkeypatch.setattr(QMessageBox,'question',lambda *a:QMessageBox.StandardButton.Yes)
    w.storage_panel.clear_index();pump(app,lambda:not w.workers)
    assert w.browser.pair_id is None
    w.compare();pump(app,lambda:not w.workers)
    assert w.browser.current_summary['verified_delta']==6
    w.close();app.processEvents()
    assert not database_path('history.sqlite',create=False).exists()


def test_gui_metadata_remains_responsive_while_log_index_warms(log_gui,isolated,monkeypatch):
    from dgm.history_index import Index
    app,w=log_gui;root=isolated/'virtual';store=Store()
    snapshot(store,root,[entry(root/'keep',value=10)],name='daily')
    snapshot(store,root,[entry(root/'keep',value=20)],name='daily');store.close()
    entered=threading.Event();release=threading.Event();original=Index.ensure
    def delayed(self,ids,**kwargs):
        if ids:entered.set();release.wait(10)
        return original(self,ids,**kwargs)
    monkeypatch.setattr(Index,'ensure',delayed)
    w.refresh_history();w.compare();pump(app,entered.is_set)
    try:
        started=time.perf_counter();assert w.latest_signature()
        assert time.perf_counter()-started<.5
    finally:release.set()
    pump(app,lambda:not w.workers)


def test_gui_legacy_conversion_preserves_scan_numbers(gui,isolated,monkeypatch):
    app,w=gui;root=isolated/'virtual';store=Store()
    a=snapshot(store,root,[entry(root/'a',value=10)],name='daily')
    b=snapshot(store,root,[entry(root/'a',value=20)],name='daily');store.close()
    monkeypatch.setattr(QMessageBox,'question',lambda *a:QMessageBox.StandardButton.Yes)
    w.storage_panel.migrate();pump(app,lambda:not w.workers)
    assert config.load()['history_format']=='jsonl'
    s=Store();assert [r['id'] for r in s.scans()]==[b,a];s.close()
    assert not (config.home()/'data/history.sqlite').exists()


def test_gui_can_retry_retiring_unchanged_legacy_after_log_conversion(gui,isolated,monkeypatch):
    from dgm.history_migration import migrate
    app,w=gui;root=isolated/'virtual';store=Store();snapshot(store,root,[entry(root/'a',value=10)],name='daily');store.close()
    migrate(remove_old=False)
    w.storage_panel.refresh();pump(app,lambda:not w.workers)
    assert not w.storage_panel.retire_button.isHidden()
    monkeypatch.setattr(QMessageBox,'question',lambda *a:QMessageBox.StandardButton.Yes)
    w.storage_panel.retire_legacy();pump(app,lambda:not w.workers)
    assert not (config.home()/'data/history.sqlite').exists() and (config.home()/'data/history/catalog.json').exists()
