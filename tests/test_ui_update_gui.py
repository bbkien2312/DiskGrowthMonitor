import json
import os
import threading
from pathlib import Path
from PySide6.QtCore import Qt,QPoint
from PySide6.QtTest import QTest
from dgm.config import home
from dgm.store import Store
from dgm.browser_ui import HEADERS,SORT_COLUMNS
from dgm.presentation import CHANGES
from test_v2 import entry,snapshot
from test_v2_gui import gui,pump


def test_three_columns_fit_default_narrow_resize_drag_and_reset(gui):
    app,w=gui;tree=w.tree
    assert w.scan_options.isVisible()
    w.scan_options_toggle.setChecked(False);app.processEvents()
    assert w.scan_options.isVisible() and w.engine.isVisible() and w.excludes.isVisible()
    default=tree.default_widths()
    assert tree.columnWidth(0)>=tree.fontMetrics().horizontalAdvance('System Volume Information')+tree.indentation()+10
    assert sum(tree.columnWidth(i) for i in range(3))<=tree.viewport().width()
    for width in (1000,1200,1800,1440):
        w.resize(width,940);app.processEvents()
        assert sum(tree.columnWidth(i) for i in range(3))<=tree.viewport().width()
        assert tree.columnWidth(1)>=90 and tree.columnWidth(2)>=105
    header=tree.header();before=tree.columnWidth(0)
    point=QPoint(header.sectionViewportPosition(0)+before-1,header.height()//2)
    QTest.mousePress(header.viewport(),Qt.MouseButton.LeftButton,pos=point)
    QTest.mouseMove(header.viewport(),point-QPoint(25,0),delay=20)
    QTest.mouseRelease(header.viewport(),Qt.MouseButton.LeftButton,pos=point-QPoint(25,0));app.processEvents()
    assert tree.columnWidth(0)<=before-15
    assert sum(tree.columnWidth(i) for i in range(3))==tree.viewport().width()
    w.save_layout();saved=json.loads((home()/'data/ui-settings.json').read_text(encoding='utf-8'))
    assert 'splitter/scan-v3' in saved and 'columns/details-v5' in saved
    w.reset_layout();app.processEvents()
    assert tree.columnWidth(0)>=default[0]-5
    assert SORT_COLUMNS[4]=='delta' and HEADERS[5]=='Loại'
    assert not w.table.wordWrap() and w.table.textElideMode()==Qt.TextElideMode.ElideRight


def test_status_multi_filter_scope_paging_and_summary_unchanged(gui,isolated):
    app,w=gui;store=Store();root=isolated/'virtual';branch=root/'System Volume Information'
    items=[entry(branch,'folder')]+[entry(branch/f'file-{i:04}.bin',value=1000) for i in range(1400)]+[entry(root/'outside',value=999)]
    snapshot(store,root,items,name='daily')
    snapshot(store,root,[{**r,'logical':2000 if i%2 else 500} if r['kind']=='file' else r for i,r in enumerate(items)],name='daily')
    w.refresh_history();w.threshold.setValue(0);w.kind.setCurrentIndex(w.kind.findData('file'));w.compare();pump(app,lambda:not w.workers)
    b=w.browser;overview=[m.text() for m in b.metrics]
    b.change.select(['grown']);b.select_scope(str(branch));pump(app,lambda:not w.workers)
    assert b.model.total==700 and b.model.loaded==20
    b.page_size.setCurrentIndex(b.page_size.findData(100));b.load_more();pump(app,lambda:not w.workers)
    assert b.model.loaded==120 and all(r['change']=='grown' for rows in b.model.chunks.values() for r in rows)
    b.sort_clicked(4);pump(app,lambda:not w.workers)
    assert b.model.loaded==120 and [m.text() for m in b.metrics]==overview
    b.change.select([]);b.reload();pump(app,lambda:not w.workers)
    assert b.model.total==0 and b.model.loaded==0
    b.change.select(CHANGES);b.reload();pump(app,lambda:not w.workers)
    assert b.model.total==1400
    assert b.metrics[2].caption.text()=='CHÊNH LỆCH'
    assert b.metrics[2].private_value.text()=='= 0 B'
    store.close()


def test_realtime_toggle_no_overlapping_status_calls_and_no_scan(gui,monkeypatch):
    import dgm.background as background
    app,w=gui;entered=threading.Event();release=threading.Event();calls=[]
    def status():
        calls.append(1);entered.set();release.wait(30)
        return {'task':{'installed':False},'service':{'installed':False},'scan':{'running':False}}
    monkeypatch.setattr(background,'status',status)
    w.set_realtime(False);w.poll_realtime();app.processEvents();assert not calls
    try:
        w.set_realtime(True);pump(app,entered.is_set)
        for _ in range(8):w.poll_realtime()
        assert len(calls)==1 and all(c.isChecked() for c in w.realtime_boxes)
    finally:release.set()
    pump(app,lambda:not w.workers)
    assert w.scan_worker is None
    w.set_realtime(False);assert not any(c.isChecked() for c in w.realtime_boxes)


def test_installed_runner_other_copy_not_mutable_and_open_is_read_only(gui,monkeypatch):
    import dgm.background as background
    app,w=gui;w.background_done({'task':{'installed':True,'enabled':True,'running':False,'owned':False,
        'actions':[{'path':'D:/Other copy/DiskGrowthMonitor.exe'}]},'service':{'installed':False},'scan':{'running':False},'checked_at':'2026-10-15T00:00:00+00:00'})
    for combo,label,buttons in w.background_panels:
        assert 'bản app khác' in label.text() and not buttons['enable'].isEnabled() and not buttons['disable'].isEnabled()
        assert 'Other copy' in label.toolTip()
    calls=[];monkeypatch.setattr(background,'open_manager',lambda kind:calls.append(kind))
    w.open_background_manager('task');assert calls==['task']


def test_update_panel_check_is_read_only_and_one_update_button(gui,monkeypatch):
    from dgm import app_update
    app,w=gui;panel=w.app_update_panel;calls=[]
    release={'version':'99.0.0','current':'1.1.0','available':True,'notes':'Latest notes','mode':'source','size':100}
    monkeypatch.setattr(app_update,'check',lambda:calls.append('check') or release)
    assert not calls and panel.update_button.isEnabled()
    panel.check();pump(app,lambda:not w.workers)
    assert calls==['check'] and panel.update_button.isEnabled()
    assert panel.notes.toPlainText()=='Latest notes' and 'giữ nguyên lịch sử' in panel.label.text()
    assert panel.update_button.text()=='Cập nhật' and panel.progress_bar.value()==100
    assert not hasattr(panel,'download_button') and not hasattr(panel,'install_button')


def test_visual_qa_with_owned_synthetic_data(gui,isolated):
    app,w=gui;store=Store();root=isolated/'virtual';branch=root/'System Volume Information'
    old=[entry(branch,'folder')]+[entry(branch/f'Tệp có tên dài để xem đường dẫn số {i:03}.bin',value=20_000_000+i) for i in range(120)]
    snapshot(store,root,old,name='daily');snapshot(store,root,[{**r,'logical':r['logical']+1_000_000} if r['kind']=='file' else r for r in old],name='daily')
    w.refresh_history();w.threshold.setValue(0);w.compare();pump(app,lambda:not w.workers)
    w.resize(1440,1000);w.browser.tree.expandItem(w.tree.topLevelItem(0));pump(app,lambda:not w.workers)
    assert f'Đã tải {w.browser.model.loaded:,}/{w.browser.model.total:,} dòng' in w.status.text()
    artifact=os.environ.get('DGM_QA_IMAGES')
    if artifact:
        folder=Path(artifact);folder.mkdir(parents=True,exist_ok=True)
        assert w.grab().save(str(folder/'scan-layout.png'))
        w.tabs.setCurrentIndex(1);app.processEvents();assert w.grab().save(str(folder/'system-layout.png'))
    store.close()
