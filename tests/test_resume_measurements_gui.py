import threading
from PySide6.QtCore import Qt
from dgm.browser_ui import DetailModel
from dgm.comparison_visuals import visual
from dgm.task_journal import Journal
from dgm.store import Store
from test_v2 import entry,snapshot
from test_v2_gui import gui,pump


def test_private_presence_background_and_common_delta_cell_priority(gui):
    app,w=gui;model=DetailModel()
    row={'path':'example','kind':'file','change':'grown','presence':'both','old_revision':1,'new_revision':2,
         'before':100,'after':200,'delta':100,'allocated_before':40,'allocated_after':20,'allocated_delta':-20}
    model.accept(0,{'rows':[row],'total':1,'cursor':None})
    assert model.data(model.index(0,2))=='40 B' and model.data(model.index(0,3))=='20 B'
    assert model.data(model.index(0,4),Qt.ItemDataRole.ForegroundRole).name()=='#1264b0'
    assert model.data(model.index(0,6),Qt.ItemDataRole.ForegroundRole).name()=='#c62828'
    model.private=True;row['presence']='old'
    assert {model.data(model.index(0,c),Qt.ItemDataRole.BackgroundRole).name() for c in range(9)}=={'#eff0f2'}
    row['presence']='new'
    assert {model.data(model.index(0,c),Qt.ItemDataRole.BackgroundRole).name() for c in range(9)}=={'#e9f7e9'}
    model.private=False
    for value in (None,0):
        row['delta']=value
        assert model.data(model.index(0,4),Qt.ItemDataRole.ForegroundRole).name()==visual('grown')[2]


def test_measurement_default_tree_metrics_and_switch_are_consistent(gui,isolated):
    app,w=gui;root=isolated/'virtual';s=Store()
    a=snapshot(s,root,[{**entry(root/'f',value=100_000_000),'allocated':40_000_000}],name='daily')
    b=snapshot(s,root,[{**entry(root/'f',value=200_000_000),'allocated':20_000_000}],name='daily');s.close()
    w.refresh_history();w.threshold.setValue(0);w.compare();pump(app,lambda:not w.workers)
    assert w.browser.measurement.currentData()=='allocated'
    assert w.browser.metrics[1].value.text()=='20 MB' and '−20 MB' in w.browser.metrics[2].value.text()
    assert w.tree.topLevelItem(0).text(1)=='20 MB'
    w.browser.measurement.setCurrentIndex(1);pump(app,lambda:not w.workers)
    assert w.browser.metrics[1].value.text()=='200 MB' and '+100 MB' in w.browser.metrics[2].value.text()
    assert w.tree.topLevelItem(0).text(1)=='200 MB'


def test_all_unknown_allocation_is_not_displayed_as_approximate_zero(gui,isolated):
    app,w=gui;root=isolated/'virtual';s=Store()
    snapshot(s,root,[{**entry(root/'f',value=100),'allocated':None}],name='daily')
    snapshot(s,root,[{**entry(root/'f',value=200),'allocated':None}],name='daily');s.close()
    w.refresh_history();w.threshold.setValue(0);w.compare();pump(app,lambda:not w.workers)
    assert w.browser.metrics[1].value.text()=='Chưa rõ'
    assert w.browser.metrics[2].value.text()=='Chưa rõ'


def test_close_keep_does_not_stop_and_stop_waits_for_worker(gui,monkeypatch):
    app,w=gui;released=threading.Event();closed=[];w.closed.connect(lambda:closed.append(True))
    def work(worker):
        worker.stop.wait(4);released.set();return True
    worker=w.job(work,lambda _:None)
    monkeypatch.setattr(w,'ask_close_tasks',lambda:'keep')
    w.close();assert w.isVisible() and not worker.stop.is_set() and not closed
    monkeypatch.setattr(w,'ask_close_tasks',lambda:'stop')
    w.close();assert worker.stop.is_set() and w.closing_after_stop
    pump(app,lambda:not w.workers and not w.isVisible());assert released.is_set() and closed==[True]


def test_log_tasks_survive_new_panel_and_do_not_use_rotated_text_log(gui):
    app,w=gui;journal=Journal('scan',{'name':'daily','p':{'roots':['D:\\fixture'],'engine':'native'}})
    journal.save(state='interrupted',owner=None,stage='Đã chụp dữ liệu',percent=70)
    panel=w.log_panel.tasks;panel.signature=None;w.tabs.setCurrentIndex(4);panel.refresh()
    assert panel.table.rowCount()>=1
    index=next(i for i,r in enumerate(panel.rows) if r['id']==journal.row['id'])
    panel.table.setCurrentCell(index,0);panel.update_buttons()
    assert panel.resume_button.isEnabled() and panel.table.item(index,2).text().startswith('70%')
