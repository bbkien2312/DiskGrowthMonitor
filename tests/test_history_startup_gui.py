import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import json
import pytest
from PySide6.QtWidgets import QApplication,QMessageBox
from dgm import config,history_logs
from dgm.gui import Window
from dgm.history_backend import close_indexes
from dgm.history_index import Index
from dgm.history_migration import migrate
from dgm.store import Store
from dgm.comparison import Comparisons
from test_history_startup import seed,forbidden
from test_v2_gui import pump


@pytest.fixture
def startup_gui(isolated,monkeypatch):
    import dgm.background as background
    monkeypatch.setattr(background,'status',lambda:{'task':{'installed':False},'service':{'installed':False},'scan':{'running':False}})
    monkeypatch.setattr(QMessageBox,'warning',lambda *a:pytest.fail(str(a[-1])))
    app=QApplication.instance() or QApplication([]);windows=[]
    yield app,windows
    for w in windows:
        for worker in list(w.workers):worker.stop.set()
        pump(app,lambda:not w.workers);w.close();app.processEvents()
    close_indexes()


@pytest.mark.parametrize('tree_mode',['selected','latest'])
def test_startup_reuses_saved_summary_without_payload_load(startup_gui,monkeypatch,tree_mode):
    app,windows=startup_gui;a,b=seed();cache=Comparisons();cache.prepare(a,b);cache.close()
    migrate(remove_old=True);close_indexes()
    (config.home()/'data/ui-settings.json').write_text(json.dumps({'tree/mode':tree_mode}),encoding='utf-8')
    with monkeypatch.context() as patch:
        patch.setattr(history_logs,'read',forbidden);patch.setattr(Index,'ensure',forbidden)
        patch.setattr(Comparisons,'__init__',forbidden)
        patch.setattr(Store,'_path_keys',forbidden)
        w=Window();windows.append(w);w.show();pump(app,lambda:not w.workers)
        assert w.newer.currentData()==b and w.browser.pair_id is None and w.browser.model.loaded==0
        assert w.history_panel.scans.rowCount()==2 and w.history_panel.pairs.rowCount()==1
        assert w.browser.current_summary['verified_delta']==10
        assert 'Tóm tắt đã lưu' in w.browser.info.text()
        # Formatting a saved overview is not a request to rebuild its details.
        w.browser.units.setCurrentIndex(1-w.browser.units.currentIndex())
        w.browser.measurement.setCurrentIndex(1-w.browser.measurement.currentIndex())
        pump(app,lambda:not w.workers)
        assert w.browser.pair_id is None and w.browser.model.loaded==0
        s=Store(readonly=True)
        try:assert s.db.execute('SELECT COUNT(*) FROM members').fetchone()[0]==0
        finally:s.close()
    # Explicit comparison still loads the entire correct pair and pages it.
    w.threshold.setValue(0);w.compare();pump(app,lambda:not w.workers)
    assert w.browser.current_summary['verified_delta']==10 and w.browser.pair_id
    assert w.browser.model.loaded>0


def test_first_launch_converts_legacy_once_then_second_launch_skips_it(startup_gui,monkeypatch):
    app,windows=startup_gui;seed();cfg=config.load();cfg.pop('history_format');config.save(cfg)
    # A compatibility backend keeps the source visible until conversion commits.
    assert (config.home()/'data/history.sqlite').exists()
    w=Window();windows.append(w);w.show()
    pump(app,lambda:(config.home()/'data/history/catalog.json').exists() and not w.workers,timeout=20)
    assert not (config.home()/'data/history.sqlite').exists()
    assert w.history_panel.scans.rowCount()==2
    w.close();app.processEvents();windows.remove(w)
    with monkeypatch.context() as patch:
        patch.setattr(history_logs,'read',forbidden);patch.setattr(history_logs,'write',forbidden)
        w2=Window();windows.append(w2);w2.show();pump(app,lambda:not w2.workers)
        assert w2.history_panel.scans.rowCount()==2 and w2.browser.pair_id is None
