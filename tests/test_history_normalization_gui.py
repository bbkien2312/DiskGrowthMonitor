import json
from PySide6.QtWidgets import QMessageBox
from test_v2_gui import gui,pump
from test_v2 import entry,snapshot
from dgm.store import Store


def test_history_preview_apply_original_workflow(gui,isolated,monkeypatch):
    app,w=gui;s=Store();root=isolated/'virtual';sid=snapshot(s,root,[entry(root/'f',value=30)],name='daily',aggregate=False,root_value=900)
    original=s.scan(sid,effective=False)
    w.history_panel.refresh();pump(app,lambda:not w.workers)
    panel=w.history_panel;panel.select_legacy();panel.normalize_selected();dialog=panel.normalization_dialog
    dialog.preview();pump(app,lambda:not w.workers)
    assert dialog.reports and not dialog.reports[0]['applied'] and dialog.apply_button.isEnabled()
    assert s.scan(sid)==original
    monkeypatch.setattr(QMessageBox,'question',lambda *a:QMessageBox.StandardButton.Yes)
    dialog.apply();pump(app,lambda:not w.workers)
    assert json.loads(s.scan(sid)['summary'])['logical']==30 and s.scan(sid,effective=False)==original
    assert panel.scans.columnCount()==12
    assert panel.scans.horizontalHeaderItem(11).text()=='Tùy chọn / phương thức'
    dialog.restore();pump(app,lambda:not w.workers)
    assert s.scan(sid)==original
    dialog.close();s.close()


def test_notification_click_restores_hidden_and_minimized_window(gui):
    app,w=gui
    w.hide();app.processEvents();assert not w.isVisible()
    w.tray.messageClicked.emit();app.processEvents();assert w.isVisible()
    w.showMinimized();app.processEvents();assert w.isMinimized()
    w.tray.messageClicked.emit();app.processEvents();assert not w.isMinimized() and w.isVisible()


def test_status_is_resizable_progress_full_width_and_long_summary_scrolls(gui):
    app,w=gui
    w.progress('task-progress',{'id':'long-status','task':'normalization','percent':45,
        'stage':'Chuẩn hóa metadata lịch sử','detail':'Đang kiểm chứng dữ liệu '+'x'*150,
        'completed':['Đã hoàn thành bước '+str(i) for i in range(15)]})
    app.processEvents();w.workspace_splitter.setSizes([600,220]);app.processEvents()
    assert w.scan_progress.width()>w.width()*.7
    assert w.completed_steps.maximumHeight()>1000
    w.save_layout();assert 'splitter/status-v1' in w.preferences.values
