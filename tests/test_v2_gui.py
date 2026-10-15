import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import json
import time
import pytest
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtCore import Qt
from dgm.gui import Window
from dgm.config import home,load,save
from dgm.browser_ui import DetailModel
from dgm.log_ui import COLORS
from test_v2 import entry,snapshot
from dgm.store import Store
from dgm.comparison import Comparisons

def pump(app,condition,timeout=10):
    until=time.monotonic()+timeout
    while not condition() and time.monotonic()<until:app.processEvents();time.sleep(.005)
    app.processEvents();assert condition()

@pytest.fixture
def gui(isolated,monkeypatch):
    import dgm.background as background
    monkeypatch.setattr(background,'status',lambda:{'task':{'installed':False},'service':{'installed':False},'scan':{'running':False}})
    monkeypatch.setattr(QMessageBox,'warning',lambda *a:pytest.fail(str(a[-1])))
    app=QApplication.instance() or QApplication([]);w=Window();w.show();pump(app,lambda:not w.workers)
    yield app,w
    for worker in list(w.workers):worker.stop.set()
    pump(app,lambda:not w.workers);w.close();app.processEvents()


def test_virtual_lru_400_rows_refetches_and_does_not_reset_cursor(gui):
    app,w=gui;model=DetailModel();requests=[];model.need_page.connect(requests.append)
    for group in range(21):
        model.accept(group,{'rows':[{'path':str(i)} for i in range(20)],'total':1000,'cursor':{'value':group}})
    assert model.rowCount()==420 and len(model.chunks)==20 and sum(map(len,model.chunks.values()))==400
    assert model.row_at(0) is None and model.row_at(0) is None and requests==[0]
    cursor=model.next_cursor
    model.accept(0,{'rows':[{'path':'loaded'} for i in range(20)],'total':1000,'cursor':{'value':'old'}})
    assert model.row_at(0)['path']=='loaded' and model.next_cursor==cursor and len(model.chunks)==20


def test_tree_latest_independent_old_new_and_scope(gui,isolated):
    app,w=gui;s=Store();root=isolated/'virtual'
    a=snapshot(s,root,[entry(root/'f',value=10)]);b=snapshot(s,root,[entry(root/'f',value=20)]);c=snapshot(s,root,[entry(root/'f',value=40)])
    cfg=load();cfg['profiles']['demo']={'roots':[str(root)],'engine':'native','strict_browsers':True,'threshold_mb':0};save(cfg)
    w.refresh_profiles();w.profile_combo.setCurrentText('demo');pump(app,lambda:not w.workers)
    assert w.browser.tree_ids==(b,c)
    w.older.setCurrentIndex(w.older.findData(a));w.newer.setCurrentIndex(w.newer.findData(b));w.compare();pump(app,lambda:not w.workers)
    model=w.browser.model;row=next(r for chunk in model.chunks.values() for r in chunk if r['kind']=='file')
    assert row['delta']==10 and w.tree.topLevelItem(0).text(2).startswith('↑ +20')
    w.browser.select_scope(str(root));pump(app,lambda:not w.workers)
    assert w.browser.ids==(a,b) and w.browser.tree_ids==(b,c) and w.newer.currentData()==b
    w.history_panel.refresh();pump(app,lambda:not w.workers)
    assert w.history_panel.scans.rowCount()==3 and w.history_panel.pairs.rowCount()>=2
    s.close()


def test_history_baseline_opens_explicit_no_old(gui,isolated):
    app,w=gui;s=Store();root=isolated/'virtual'
    a=snapshot(s,root,[entry(root/'f',value=10)],name='daily');b=snapshot(s,root,[entry(root/'f',value=20)],name='daily')
    cache=Comparisons();baseline=cache.prepare(None,b);cache.close()
    w.refresh_history();w.history_panel.refresh();pump(app,lambda:not w.workers)
    panel=w.history_panel;panel.tabs.setCurrentIndex(1)
    index=next(i for i,r in enumerate(panel.pair_rows) if r['id']==baseline)
    panel.pairs.setCurrentCell(index,0);panel.open_selected();pump(app,lambda:not w.workers)
    assert w.browser.ids==(None,b) and w.older.currentData()==0
    s.close()


def test_log_labels_colors_multiline_filter_and_refresh(gui):
    app,w=gui;path=home()/'logs/app.log'
    path.write_text('2026 DEBUG detail\n2026 INFO summary\n2026 WARNING beware\n2026 ERROR broken\nTraceback continuation\n',encoding='utf-8')
    panel=w.log_panel;panel.refresh(True);app.processEvents();panel.colors.rehighlight();app.processEvents()
    block=panel.view.document().firstBlock();actual=[]
    while block.isValid():
        formats=block.layout().formats();actual.append(formats[0].format.foreground().color().name() if formats else '')
        block=block.next()
    assert actual[:5]==[COLORS['DEBUG'],COLORS['INFO'],COLORS['WARNING'],COLORS['ERROR'],COLORS['ERROR']]
    panel.level.setCurrentText('ERROR');panel.refresh(True)
    assert 'DEBUG' not in panel.view.toPlainText() and 'Traceback continuation' in panel.view.toPlainText()
    with path.open('a',encoding='utf-8') as file:file.write('2026 ERROR another\n')
    panel.refresh();assert 'another' in panel.view.toPlainText()


def test_page_sort_global_not_loaded_twenty(gui,isolated):
    app,w=gui;s=Store();root=isolated/'virtual'
    sid=snapshot(s,root,[entry(root/f'f{i:03}',value=i+1) for i in range(70)],name='daily')
    w.refresh_history();w.threshold.setValue(0);w.kind.setCurrentIndex(w.kind.findData('file'));w.compare();pump(app,lambda:not w.workers)
    assert w.browser.model.rowCount()==20 and w.browser.model.total==70
    w.browser.sort_clicked(3);pump(app,lambda:not w.workers)
    assert w.browser.model.row_at(0)['after']==70
    w.browser.sort_clicked(3);pump(app,lambda:not w.workers)
    assert w.browser.model.row_at(0)['after']==1
    w.browser.load_more();pump(app,lambda:not w.workers)
    assert w.browser.model.rowCount()==40 and w.browser.model.row_at(39)['after']==40
    s.close()


def test_cancel_loading_preparation_then_restart(gui,isolated,monkeypatch):
    import threading
    from dgm.platform_ops import ProcessCancelled
    app,w=gui;s=Store();root=isolated/'virtual';snapshot(s,root,[entry(root/'f',value=10)],name='daily')
    original=Comparisons.prepare;entered=threading.Event()
    def pending(cache,*args):
        entered.set();cache.stop.wait(5)
        if cache.stop.is_set():raise ProcessCancelled()
        return original(cache,*args)
    monkeypatch.setattr(Comparisons,'prepare',pending)
    w.refresh_history();w.compare();pump(app,entered.is_set)
    assert w.browser.cancel_button.isEnabled()
    w.browser.cancel_loading();pump(app,lambda:not w.workers)
    assert w.browser.model.rowCount()==0 and not w.browser.cancel_button.isEnabled()
    monkeypatch.setattr(Comparisons,'prepare',original)
    w.compare();pump(app,lambda:not w.workers)
    assert w.browser.model.rowCount()==1 and 'Đã sẵn sàng' in w.status.text()
    s.close()


def test_gui_uac_declined_preserves_unsaved_fields_and_unlocks(gui,monkeypatch):
    from dgm import elevation,scanner
    app,w=gui;calls=[];phases=[]
    monkeypatch.setattr(scanner,'is_admin',lambda:False)
    def request(action,payload,stop=None,notify=None):
        calls.append((action,payload));notify('uac-state',{'phase':'prompt','operation':action})
        until=time.monotonic()+.05
        while time.monotonic()<until:time.sleep(.005)
        notify('uac-state',{'phase':'done','operation':action})
        raise elevation.ElevationCancelled('Đã hủy cấp quyền; tác vụ chưa chạy.')
    monkeypatch.setattr(elevation,'request',request)
    w.strict.setChecked(False);w.mft.setChecked(True);w.roots.setText('D:\\Unsaved-MFT-Test');w.threshold.setValue(27)
    before=(home()/'data/config.json').read_bytes()
    w.begin_scan();pump(app,lambda:not w.workers)
    assert len(calls)==1 and calls[0][1]['profile']['roots']==['D:\\Unsaved-MFT-Test']
    assert calls[0][1]['profile']['threshold_mb']==27 and calls[0][1]['profile']['mft']
    assert w.roots.text()=='D:\\Unsaved-MFT-Test' and w.mft.isChecked() and w.threshold.value()==27
    assert w.scan_button.isEnabled() and not w.admin_busy and 'hủy cấp quyền' in w.status.text()
    assert (home()/'data/config.json').read_bytes()==before


def test_admin_phase_locks_mutations_not_browsing(gui):
    app,w=gui
    w.progress('uac-state',{'phase':'prompt','operation':'system-check'})
    assert not w.scan_button.isEnabled() and all(not b.isEnabled() for b in w.system_check_buttons+w.cleanup_action_buttons)
    assert w.tabs.isEnabled() and w.table.isEnabled()
    w.progress('uac-state',{'phase':'done','operation':'system-check'})
    assert w.scan_button.isEnabled() and all(b.isEnabled() for b in w.system_check_buttons+w.cleanup_action_buttons)


def test_local_cancel_never_stops_some_other_scan(gui,monkeypatch):
    import threading
    from dgm import scan_control
    app,w=gui;entered=threading.Event()
    monkeypatch.setattr(scan_control,'request_stop',lambda:pytest.fail('Must not cancel another process from a local worker'))
    def running(worker):
        entered.set();worker.stop.wait(5)
        return {'scan_id':None,'status':'cancelled','files':0,'seconds':0,'before_start':True,'message':'Đã dừng'}
    w.job(running,w.scan_done,scan_job=True);pump(app,entered.is_set)
    w.cancel_scan();pump(app,lambda:not w.workers)
    assert w.scan_button.isEnabled()


def test_admin_stop_targets_own_non_scan_worker_and_unlocks(gui,monkeypatch):
    import dgm.elevation as uac
    app,w=gui;entered=[]
    def probe(notify=None,stop=None):
        notify('uac-state',{'phase':'prompt','operation':'admin-probe'})
        entered.append(stop);stop.wait(5)
        notify('uac-state',{'phase':'done','operation':'admin-probe'})
        raise uac.ElevationCancelled('Đã dừng tác vụ thử quyền')
    monkeypatch.setattr(uac,'probe',probe)
    w.test_admin();pump(app,lambda:w.admin_busy)
    assert w.admin_stop_button.isVisible() and w.admin_worker is not None
    w.cancel_admin();pump(app,lambda:not w.workers)
    assert entered[0].is_set() and w.admin_worker is None and not w.admin_busy
    assert not w.admin_stop_button.isVisible() and 'Đã dừng' in w.status.text()


def test_successful_admin_result_has_final_status(gui):
    app,w=gui;w.status.setText('Đã cấp quyền; đang khởi động...')
    w.system_result({'admin':True})
    assert 'Đã hoàn tất' in w.status.text() and '"admin": true' in w.system_output.toPlainText()
