import json
import threading
import pytest
from PySide6.QtCore import Qt
from dgm import config
from dgm.store import Store
from test_v2 import entry,snapshot
from test_v2_gui import gui,pump


def test_legacy_index_upgrade_never_blocks_gui_construction(isolated,monkeypatch):
    from PySide6.QtWidgets import QApplication
    from dgm.gui import Window
    import dgm.background as background
    monkeypatch.setattr(background,'status',lambda:{'task':{'installed':False},'service':{'installed':False},'scan':{'running':False}})
    s=Store(migrate_keys=False);root=isolated/'legacy'
    sid=s.begin_scan('daily','scope','native',{'roots':[str(root)]})
    s.put(sid,[entry(root,'folder'),entry(root/'f',value=5)]);s.finish(sid,'complete',s.totals(sid));s.close()
    original=Store._path_keys;threads=[];main=threading.get_ident()
    def tracked(self,*args,**kw):
        threads.append(threading.get_ident());assert threads[-1]!=main
        return original(self,*args,**kw)
    monkeypatch.setattr(Store,'_path_keys',tracked)
    app=QApplication.instance() or QApplication([]);w=Window();w.show()
    try:
        pump(app,lambda:not w.workers)
        assert threads and w.history_panel.loaded
    finally:
        for worker in w.workers:worker.stop.set()
        pump(app,lambda:not w.workers);w.close();app.processEvents()


def test_history_loads_at_start_and_direct_tab_without_manual_refresh(gui,isolated):
    app,w=gui;panel=w.history_panel
    assert panel.loaded
    s=Store();root=isolated/'virtual';sid=snapshot(s,root,[entry(root/'one',value=5)],name='daily');s.close()
    panel.invalidate();w.tabs.setCurrentIndex(5);pump(app,lambda:not w.workers and panel.loaded)
    assert [row['id'] for row in panel.scan_rows]==[sid]
    assert panel.scans.rowCount()==1 and '1 lượt quét' in panel.load_state.text()


def test_compare_help_opens_once_per_success_not_for_load_sort_or_tree(gui,isolated):
    app,w=gui;box=w.browser.compare_help
    assert not box.toggle.isChecked()
    s=Store();root=isolated/'virtual'
    snapshot(s,root,[entry(root/f'f{i}',value=100+i) for i in range(45)],name='daily');s.close()
    w.refresh_history();w.threshold.setValue(0);w.compare();pump(app,lambda:not w.workers)
    assert box.toggle.isChecked() and box.body.isVisible()
    assert 'Lần quét' in box.body.text()
    # Programmatic result opening did not change the preference default.
    assert not w.preferences.values.get('help/compare',False)
    box.toggle.setChecked(False)
    w.browser.load_more();pump(app,lambda:not w.workers)
    w.browser.sort_clicked(3);pump(app,lambda:not w.workers)
    assert not box.toggle.isChecked()
    w.compare();pump(app,lambda:not w.workers)
    assert box.toggle.isChecked()


def test_compact_layout_always_keeps_controls_and_gives_panes_height(gui):
    app,w=gui
    for width,height in ((1440,940),(1000,680)):
        w.resize(width,height);app.processEvents()
        for key in ('profile','scan'):
            w.help_boxes[key].toggle.setChecked(False)
        app.processEvents()
        assert all(control.isVisible() for control in (w.engine,w.roots,w.excludes,w.threshold,w.strict,w.mft))
        assert w.engine.width()<=150 and w.profile_combo.width()<=220
        assert w.table.height()>=180
        assert w.browser.splitter.height()>=(300 if height==940 else 180)
        assert w.engine.geometry().right()<=w.engine.parentWidget().width()


def test_failed_or_cancelled_one_click_update_restores_controls(gui,monkeypatch):
    from dgm import app_update
    from dgm.platform_ops import ProcessCancelled
    app,w=gui;w.tabs.setCurrentIndex(1);panel=w.app_update_panel
    def cancelled(*args,**kw):raise ProcessCancelled('Đã hủy UAC; app hiện tại vẫn hoạt động.')
    monkeypatch.setattr(app_update,'start_update',cancelled)
    panel.update();pump(app,lambda:not w.workers)
    assert 'hủy UAC' in panel.label.text()
    assert panel.update_button.isEnabled() and w.scan_button.isEnabled()
    assert all(w.tabs.isTabEnabled(i) for i in range(w.tabs.count()))
    assert w.poll_timer.isActive() and w.scan_state_timer.isActive()


def test_one_update_click_runs_once_and_can_cancel_during_download(gui,monkeypatch):
    from dgm import app_update
    from dgm.platform_ops import ProcessCancelled
    app,w=gui;w.tabs.setCurrentIndex(1);panel=w.app_update_panel;entered=threading.Event();calls=[]
    def pending(stop,notify,**kw):
        calls.append(kw);entered.set();stop.wait(5)
        if stop.is_set():raise ProcessCancelled('Đã hủy tải cập nhật')
        return {'launched':False}
    monkeypatch.setattr(app_update,'start_update',pending)
    panel.update();pump(app,entered.is_set);panel.update()
    assert len(calls)==1 and calls[0]['reopen']
    panel.cancel();pump(app,lambda:not w.workers)
    assert 'Đã hủy tải' in panel.label.text() and not w.app_update_panel.handed_off
