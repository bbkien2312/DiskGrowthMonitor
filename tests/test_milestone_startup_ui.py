"""Startup/profile and milestone regression tests; no network or real scans."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import threading
import time

import pytest
from PySide6.QtWidgets import QApplication,QMessageBox

from dgm import app_update,config,production_core as core
from dgm.columnar_ingest import import_entries,metadata_for
from dgm.main_ui import MainWindow
from test_columnar_core import entry


def settle(app,predicate):
    until=time.monotonic()+15
    while time.monotonic()<until:
        app.processEvents()
        if predicate():return
        time.sleep(.005)
    raise AssertionError('GUI did not settle')


@pytest.fixture
def gui_factory(isolated,monkeypatch):
    cfg=config.load()
    cfg.update(scan_backend='columnar',history_format='jsonl')
    cfg['profiles']['MrK']=dict(cfg['profiles']['daily'])
    cfg['schedules']=[dict(id='qa',enabled=True,profile='MrK',name='mrk',mode='daily',
                           interval=1,times=['09:00'],next_run='2026-11-12T09:00:00+07:00')]
    config.save(cfg)
    monkeypatch.setattr(MainWindow,'doctor',lambda self:None)
    monkeypatch.setattr(MainWindow,'refresh_background',lambda self:None)
    errors=[]
    monkeypatch.setattr(QMessageBox,'warning',lambda p,t,m,*args:errors.append(m))
    app=QApplication.instance() or QApplication([])
    windows=[]
    def open_window():
        window=MainWindow();windows.append(window);window.show()
        settle(app,lambda:not window.workers)
        return window
    yield app,open_window,errors
    for window in windows:
        for worker in window.workers:worker.stop.set()
        settle(app,lambda:not window.workers)
        window.close()
        app.processEvents()


def capture(repo,stamp,allocated,used,profile='MrK'):
    capacity=161290907648
    meta=metadata_for(['C:\\'],profile=profile,mft=True,engine='wiztree',started=stamp,
        volume_map={'c:':'volume-A'},
        volume_snapshots=[{'root':'C:\\','capacity':capacity,'used':used,'free':capacity-used}])
    return import_entries([entry('C:\\',0,0,kind='folder'),
                           entry('C:\\demo.bin',allocated,allocated)],meta,repository=repo)


def test_startup_prefers_scheduled_profile_then_remembers_manual_choice(gui_factory):
    app,open_window,errors=gui_factory
    repo=core.repository()
    daily=capture(repo,'2026-11-04T22:11:51+07:00',10,20,profile='daily')
    newer=capture(repo,'2026-11-11T09:03:06+07:00',15,25)
    first=open_window()
    assert first.profile_combo.currentText()=='MrK'
    assert first.newer.currentData()==newer['id']
    first.profile_combo.setCurrentText('daily')
    assert first.newer.currentData()==newer['id'] and first.older.findData(daily['id'])>=0
    first.close()
    reopened=open_window()
    assert reopened.profile_combo.currentText()=='daily' and reopened.newer.currentData()==newer['id']
    assert not errors


def test_latest_follows_publication_but_manual_old_selection_is_pinned(gui_factory):
    app,open_window,errors=gui_factory
    repo=core.repository()
    a=capture(repo,'2026-11-08T09:03:00+07:00',10,20)
    b=capture(repo,'2026-11-09T09:03:00+07:00',15,25)
    w=open_window();ui=w.browser
    ui.compare();settle(app,lambda:not w.workers)
    assert ui.current_summary['newer']['id']==b['id']
    c=capture(repo,'2026-11-10T09:03:00+07:00',20,30)
    w.refresh_history(load_tree=False)
    assert w.newer.currentData()==c['id'] and ui.current_summary is None
    assert ui.metrics[2].value.text()=='—'
    w.newer.setCurrentIndex(w.newer.findData(b['id']))
    assert w.older.findData(c['id'])==-1 and w.older.findData(b['id'])==-1
    assert ui.selected_tree_ids()==(a['id'],b['id'])
    d=capture(repo,'2026-11-11T09:03:00+07:00',25,35)
    w.refresh_history(load_tree=False)
    assert w.newer.currentData()==b['id']
    w.newer.setCurrentIndex(0)
    assert w.newer.currentData()==d['id']
    assert not errors


def test_saved_byte_shapes_for_november_pairs_render_distinct_results(gui_factory):
    app,open_window,errors=gui_factory
    repo=core.repository()
    specs=[(8,157088804864,158626713600),(9,151861616640,153407746048),
           (10,148562182144,150142971904),(11,149912723456,151523823616)]
    rows=[capture(repo,f'2026-11-{day:02}T09:03:00+07:00',files,used) for day,files,used in specs]
    w=open_window();ui=w.browser
    for index in range(3):
        w.older.setCurrentIndex(w.older.findData(rows[index]['id']))
        assert ui.current_summary is None
        ui.compare()
        settle(app,lambda:not w.workers)
        summary=ui.current_summary
        observed=specs[3][1]-specs[index][1]
        assert summary['observed_disk_delta']==observed and summary['accounting_valid']
        assert summary['disk_changes'][0]['used_delta']==specs[3][2]-specs[index][2]
        assert ui.metrics[2].value.text()==ui.delta_text(observed)
        assert ui.metrics[2].value.text().endswith(' GB') and '11/11 09:03' in ui.pair_header.text()
        assert ui.metrics[0].value.text() in ui.pair_header.text()
        assert ui.metrics[1].value.text() in ui.pair_header.text()
        assert ui.pair_header.parentWidget() is ui.result_tabs.parentWidget()
        assert '11/11 09:03' not in ui.info.text()
        ui.splitter.setSizes([sum(ui.splitter.sizes())-430,430])
        app.processEvents()
        caption=' '.join(ui.pair_header.display_lines())
        assert all(ui.metrics[i].value.text() in caption for i in (0,1))
        assert all(f"#{r['id']}" in caption for r in (rows[index],rows[3]))
        assert ui.tree.topLevelItemCount()==1
        assert ui.tree.topLevelItem(0).text(2)==ui.delta_text(observed)
        assert ui.delta_text(observed) in w.status.text()
    assert not errors


def test_summary_tab_is_lazy_and_preserves_detail_rows(gui_factory,monkeypatch):
    from dgm.columnar_gui_backend import Handle
    app,open_window,errors=gui_factory
    repo=core.repository()
    calls=[]
    original=Handle.folder_highlights
    def record(self,pair,measure):
        calls.append((pair,measure))
        return original(self,pair,measure)
    monkeypatch.setattr(Handle,'folder_highlights',record)
    def snapshot(day,grown,new_folder):
        meta=metadata_for(['C:\\'],profile='MrK',mft=True,engine='wiztree',
            started=f'2026-11-{day:02}T09:03:00+07:00',volume_map={'c:':'volume-A'},
            coverage={'source':'export','unknown_roots':[],'error_regions':[]})
        folder='new' if new_folder else 'old'
        return import_entries([entry('C:\\',0,0,kind='folder'),
            entry('C:\\growth',0,0,kind='folder'),entry('C:\\growth\\a',grown,grown*2),
            entry(f'C:\\{folder}',0,0,kind='folder'),entry(f'C:\\{folder}\\b',500,1000)],
            meta,repository=repo)
    old=snapshot(10,100,False);new=snapshot(11,400,True)
    w=open_window();ui=w.browser
    w.threshold.setValue(0)
    ui.compare();settle(app,lambda:not w.workers)
    assert calls==[]
    # No highlight depends on this folder filter or threshold.
    ui.select_scope('C:\\growth');settle(app,lambda:not w.workers)
    detail=(ui.model.loaded,ui.model.total,ui.root)
    assert detail[:2]==(1,1)
    ui.result_tabs.setCurrentIndex(1)
    settle(app,lambda:not w.workers)
    assert calls==[((old['id'],new['id']),'allocated')]
    assert ui.summary_panel.isVisible() and not ui.table.isVisible()
    assert not ui.detail_paging.isVisible()
    groups=ui.summary_panel.groups
    assert '600 B' in groups['grown'][1].topLevelItem(0).text(1)
    assert 'C:\\new' in groups['new'][1].topLevelItem(0).text(0)
    assert groups['missing'][1].topLevelItem(0).text(2)=='Đã xác minh'
    assert 'Cấp phát file' in ui.summary_panel.overview.text()
    app.processEvents()
    for key in ('grown','new','missing'):
        table=groups[key][1]
        assert table.viewport().rect().contains(table.visualItemRect(table.topLevelItem(0)))
    ui.result_tabs.setCurrentIndex(0)
    assert ui.table.isVisible() and ui.detail_paging.isVisible()
    assert (ui.model.loaded,ui.model.total,ui.root)==detail
    ui.result_tabs.setCurrentIndex(1)
    assert len(calls)==1
    ui.units.setCurrentIndex(0);settle(app,lambda:not w.workers)
    assert len(calls)==1
    ui.measurement.setCurrentIndex(ui.measurement.findData('logical'))
    settle(app,lambda:not w.workers)
    assert len(calls)==2 and calls[-1][-1]=='logical'
    assert '300 B' in groups['grown'][1].topLevelItem(0).text(1)
    ui.older.setCurrentIndex(ui.older.findData(0))
    assert not groups['grown'][1].topLevelItemCount()
    ui.compare();settle(app,lambda:not w.workers)
    assert 'Mốc ban đầu' in ui.summary_panel.overview.text()
    assert len(calls)==2 and not errors


def test_prior_uses_real_time_and_excludes_equal_timestamp(gui_factory):
    repo=core.repository()
    a=capture(repo,'2026-11-08T15:00:00+07:00',10,20)  # 08:00 UTC
    b=capture(repo,'2026-11-08T09:00:00+00:00',15,25)
    capture(repo,'2026-11-08T16:00:00+07:00',16,26)  # same instant as b
    assert core.previous(b['id'],repo)==a['id']


def test_startup_update_notifies_once_without_download_and_toggle_persists(gui_factory,monkeypatch):
    app,open_window,errors=gui_factory
    calls=[]
    release=dict(version='99.0.0',available=True,notes='Synthetic release')
    monkeypatch.setattr(app_update,'check',lambda:calls.append('check') or release)
    monkeypatch.setattr(app_update,'start_update',lambda *_a,**_kw:pytest.fail('Unexpected install'))
    w=open_window();panel=w.app_update_panel
    assert panel.auto_check.isChecked()
    panel.schedule_startup_check()
    settle(app,lambda:w.update_notice.isVisible())
    panel.schedule_startup_check()
    assert not panel._startup_timer.isActive() and calls==['check']
    assert '99.0.0' in w.update_notice_label.text()
    w.open_update_notice()
    assert w.tabs.currentIndex()==1 and w.system_tabs.currentIndex()==0
    panel.auto_check.setChecked(False)
    assert not w.update_notice.isVisible()
    w.close()
    other=open_window();other.app_update_panel.schedule_startup_check()
    assert not other.app_update_panel.auto_check.isChecked()
    assert not other.app_update_panel._startup_timer.isActive() and not errors


def test_startup_network_failure_is_quiet(gui_factory,monkeypatch):
    app,open_window,errors=gui_factory
    called=threading.Event()
    def offline():
        called.set()
        raise OSError('Offline')
    monkeypatch.setattr(app_update,'check',offline)
    w=open_window()
    w.app_update_panel._probe_startup()
    settle(app,lambda:called.is_set() and not w.app_update_panel._startup_poll.isActive())
    assert not w.update_notice.isVisible() and not errors


def test_scheduled_scan_identifies_its_profile_in_result_and_history(gui_factory,tmp_path,caplog):
    from dgm import schedule
    from dgm.store import Store
    import json
    app,open_window,errors=gui_factory
    root=tmp_path/'scheduled-scan-files';root.mkdir()
    (root/'fixture.bin').write_bytes(b'abc')
    cfg=config.load();cfg['profiles']['MrK'].update(engine='native',mft=False,roots=[str(root)])
    config.save(cfg)
    events=[]
    with caplog.at_level('INFO',logger='dgm'):
        result=schedule.run_now('qa',notify=lambda e,d:events.append((e,d)))
    assert result['status']=='complete' and result['profile']=='MrK'
    assert any('profile MrK' in row.get('message','') for _,row in events)
    assert 'Đã lưu mốc #1 · profile MrK' in caplog.text
    store=Store(readonly=True)
    try:
        stored=store.db.execute("SELECT result FROM jobs WHERE schedule_id='qa'").fetchone()[0]
        assert json.loads(stored)['profile']=='MrK'
    finally:store.close()
    w=open_window()
    assert 'MrK' in w.history_panel.scans.item(0,0).text()
    assert not errors
