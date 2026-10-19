"""Contract tests for the approved GUI with the columnar backend underneath."""
import json
import os
import time

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication,QMessageBox
from dgm import config,production_core as core
from dgm.main_ui import MainWindow
from dgm.columnar_ingest import import_entries
from dgm.columnar_gui_backend import Sessions,summary_row
from test_columnar_core import entry,meta


@pytest.fixture
def restored(isolated,monkeypatch):
    cfg=config.load();cfg.update(scan_backend='columnar',history_format='jsonl');config.save(cfg)
    monkeypatch.setattr(MainWindow,'doctor',lambda self:None)
    monkeypatch.setattr(MainWindow,'refresh_background',lambda self:None)
    errors=[]
    monkeypatch.setattr(QMessageBox,'warning',lambda parent,title,text,*_:errors.append(text))
    return core.repository(),errors


def wait(app,window):
    until=time.monotonic()+20;idle=0
    while time.monotonic()<until:
        app.processEvents();time.sleep(.005)
        idle=idle+1 if not window.workers else 0
        if idle>=4:break
    assert not window.workers


def pair(repo,count=310):
    folders=[entry(p,0,0,kind='folder') for p in ('C:\\data','C:\\data\\a','C:\\data\\b')]
    older=folders+[entry(f'C:\\data\\{"a" if i<150 else "b"}\\file-{i:04}.dat',i+100,4096*(i+1)) for i in range(count)]
    newer=folders+[entry(f'C:\\data\\{"a" if i<150 else "b"}\\file-{i:04}.dat',i+1000,4096*(count-i)) for i in range(count)]
    coverage={'source':'native','unknown_roots':[],'error_regions':[]}
    a=import_entries(older,meta(profile='daily',started='2026-01-01T00:00:00+00:00',coverage=coverage),repository=repo)
    b=import_entries(newer,meta(profile='daily',started='2026-01-02T00:00:00+00:00',coverage=coverage),repository=repo)
    return a,b


def test_old_gui_global_sort_variable_pages_scope_and_evicted_page(restored):
    repo,errors=restored;a,b=pair(repo,510)
    app=QApplication.instance() or QApplication([]);window=MainWindow();window.show()
    try:
        wait(app,window);ui=window.browser
        assert not window.sessions.queries # Catalog-only startup, including history.
        window.threshold.setValue(0);ui.kind.setCurrentIndex(ui.kind.findData('file'));ui.compare();wait(app,window)
        query=window.sessions.queries[(a['id'],b['id'])]
        assert ui.model.loaded==20 and ui.model.total==510 and ui.sections.currentIndex()==0
        ui.sort_clicked(3);wait(app,window) # Mới, allocated, descending across all 510.
        assert ui.model.row_at(0)['path'].endswith('file-0000.dat')
        assert ui.model.row_at(19)['path'].endswith('file-0019.dat')
        ui.page_size.setCurrentIndex(ui.page_size.findData(100));ui.load_more();wait(app,window)
        assert ui.model.loaded==120 and ui.model.row_at(119)['path'].endswith('file-0119.dat')
        ui.sort_clicked(3);wait(app,window) # Ascending, preserve 120, not sort loaded 120.
        assert ui.model.loaded==120 and ui.model.row_at(0)['path'].endswith('file-0509.dat')
        assert ui.model.row_at(119)['path'].endswith('file-0390.dat')
        for _ in range(4):ui.load_more();wait(app,window)
        assert ui.model.loaded==510 and sum(map(len,ui.model.chunks.values()))<=ui.model.cache_limit
        # The original virtual model can reload a page evicted from its LRU.
        if 0 in ui.model.chunks:ui.model.chunks.pop(0)
        assert ui.model.row_at(0) is None;wait(app,window)
        assert ui.model.row_at(0)['path'].endswith('file-0509.dat')
        ui.select_scope('C:\\data\\a');wait(app,window)
        assert ui.model.total==150 and all('\\a\\' in r['path'] for rows in ui.model.chunks.values() for r in rows)
        assert ui.model.row_at(0)['path'].endswith('file-0149.dat')
        ui.page_size.setCurrentIndex(ui.page_size.findData(1000));ui.load_more();wait(app,window)
        assert ui.model.loaded==150 and not ui.model.next_cursor
        assert window.sessions.queries[(a['id'],b['id'])] is query # No rejoin on pages/sort/filter.
        ui.measurement.setCurrentIndex(ui.measurement.findData('logical'));wait(app,window)
        assert ui.model.row_at(0)['path'].endswith('file-0000.dat') # Same ascending column, different measure.
        assert not errors
    finally:wait(app,window);window.close();app.processEvents()
    assert repo.footprint()['work_bytes']==0


def test_original_private_details_cards_tooltips_and_independent_tree(restored,monkeypatch):
    repo,errors=restored
    root=entry('C:\\data',0,0,kind='folder')
    coverage={'source':'native','unknown_roots':[],'error_regions':[]}
    a=import_entries([root,entry('C:\\data\\common',100,8192),entry('C:\\data\\gone',20,4096)],
        meta(profile='daily',started='2026-01-01T00:00:00+00:00',coverage=coverage),repository=repo)
    b=import_entries([root,entry('C:\\data\\common',1000,4096),entry('C:\\data\\added',30,8192)],
        meta(profile='daily',started='2026-01-02T00:00:00+00:00',coverage=coverage),repository=repo)
    c=import_entries([root,entry('C:\\data\\common',1500,12288)],
        meta(profile='daily',started='2026-01-03T00:00:00+00:00',coverage=coverage),repository=repo)
    app=QApplication.instance() or QApplication([]);window=MainWindow();window.show()
    try:
        wait(app,window);ui=window.browser;window.threshold.setValue(0)
        ui.older.setCurrentIndex(ui.older.findData(a['id']));ui.newer.setCurrentIndex(ui.newer.findData(b['id']))
        ui.compare();wait(app,window)
        summary=ui.current_summary
        assert summary['old_totals']['allocated']==8192 and summary['private_old_totals']['allocated']==4096
        assert summary['new_totals']['allocated']==4096 and summary['private_new_totals']['allocated']==8192
        assert ui.metrics[0].caption.text()=='DUNG LƯỢNG CŨ' and ui.metrics[2].caption.text()=='CHÊNH LỆCH'
        assert ui.metrics[3].caption.text()=='TOÀN PHẠM VI'
        assert all(card.common_heading.text()=='CHUNG' and card.private_heading.text()=='RIÊNG' for card in ui.metrics)
        ui.kind.setCurrentIndex(ui.kind.findData('file'));ui.reload();wait(app,window)
        row=ui.model.row_at(0)
        assert row['delta']==900 and row['allocated_delta']==-4096 and row['change']=='shrunk'
        assert '↑' in ui.model.data(ui.model.index(0,4)) and '↓' in ui.model.data(ui.model.index(0,6))
        assert ui.model.data(ui.model.index(0,4),Qt.ItemDataRole.ForegroundRole).name()=='#1264b0'
        assert ui.model.data(ui.model.index(0,6),Qt.ItemDataRole.ForegroundRole).name()=='#c62828'
        assert ui.model.data(ui.model.index(0,1),Qt.ItemDataRole.DecorationRole) is not None
        assert ui.sections.tabToolTip(1) and ui.page_size.toolTip() and ui.compare_help.body.text()
        ui.sections.setCurrentIndex(1);wait(app,window)
        assert ui.model.total==2
        byside={ui.model.row_at(i)['presence']:i for i in range(2)}
        for side,paper in (('old','#eff0f2'),('new','#e9f7e9')):
            assert ui.model.data(ui.model.index(byside[side],1),Qt.ItemDataRole.BackgroundRole).name()==paper
        ui.presence.setCurrentIndex(ui.presence.findData('old'));wait(app,window)
        assert ui.model.total==1 and ui.model.row_at(0)['path'].endswith('gone')
        opened=[];monkeypatch.setattr(window,'file_action',lambda path,action:opened.append((path,action)))
        ui.open_row(ui.model.index(0,1));assert opened==[('C:\\data\\gone','open')]
        ui.tree_mode.setCurrentIndex(ui.tree_mode.findData('latest'));wait(app,window)
        assert ui.tree_ids==(b['id'],c['id']) and ui.ids==(a['id'],b['id'])
        assert len(window.sessions.queries)==2
        ui.tree_mode.setCurrentIndex(ui.tree_mode.findData('selected'));wait(app,window)
        assert ui.tree_ids==ui.ids and len(window.sessions.queries)==1
        ui.change.select([]);ui.reload();wait(app,window);assert ui.model.total==0
        assert not errors
    finally:wait(app,window);window.close();app.processEvents()
    assert repo.footprint()['work_bytes']==0


def test_cards_keep_correct_historical_disk_measurements_and_saved_overview(restored,monkeypatch):
    repo,errors=restored
    from dgm.columnar_ingest import metadata_for
    from dgm.columnar_gui_backend import CatalogStore
    capacity=161290907648;used=149150740480;physical=148535681024
    scan=import_entries([entry('C:\\',0,0,kind='folder'),entry('C:\\huge',163802063620,physical)],
        metadata_for(['C:\\'],profile='daily',volume_snapshots=[{'root':'C:\\','capacity':capacity,'used':used,'free':capacity-used}],
                     coverage={'source':'native','unknown_roots':[],'error_regions':[]}),repository=repo)
    app=QApplication.instance() or QApplication([]);window=MainWindow();window.show()
    try:
        wait(app,window);window.compare();wait(app,window)
        ui=window.browser
        assert '138 GiB' in ui.metrics[1].text() and '164 GB' not in ui.metrics[1].value.text()
        assert 'Sức chứa 150 GiB' in ui.info.text() and 'Windows đã dùng 138 GiB' in ui.info.text()
        assert ui.current_summary['disks_new'][0]['residual']==615059456
        saved=window.comparison_history()[0];assert json.loads(saved['summary'])['partition_valid']
        window.clear_comparison();assert not window.sessions.queries
        ui.show_saved_overview();assert 'Tóm tắt đã lưu' in ui.info.text()
        assert not window.sessions.queries # Small JSON summary, not a new query.
        assert not errors
    finally:wait(app,window);window.close();app.processEvents()
    assert repo.footprint()['work_bytes']==0


def test_imported_profile_is_selectable_without_changing_saved_scan_profile(restored):
    repo,errors=restored
    scan=import_entries([entry('C:\\data',0,0,kind='folder'),entry('C:\\data\\file')],
                        meta(profile='preview'),repository=repo)
    app=QApplication.instance() or QApplication([]);window=MainWindow();window.show()
    try:
        wait(app,window);ui=window.browser
        assert window.profile_combo.currentText()=='daily' and 'preview' not in config.load()['profiles']
        assert ui.newer.findData(scan['id'])>=0
        ui.newer.setCurrentIndex(ui.newer.findData(scan['id']));ui.compare();wait(app,window)
        assert ui.current_summary['newer']['id']==scan['id'] and 'preview' in ui.newer.currentText()
        window.history_panel.scans.setCurrentCell(0,0);window.history_panel.open_selected();wait(app,window)
        assert ui.current_summary['newer']['id']==scan['id'] and not errors
    finally:wait(app,window);window.close();app.processEvents()


def test_all_original_sort_columns_work_before_paging(restored):
    repo,_=restored;a,b=pair(repo,25)
    from dgm.browser_ui import SORT_COLUMNS
    pool=Sessions(repo);handle=pool.handle()
    try:
        ident=handle.prepare(a['id'],b['id'])
        for sort in SORT_COLUMNS+['disk_old','disk_new']:
            for direction in ('asc','desc'):
                page=handle.page(ident,sort=sort,direction=direction,kind='file',limit=20,measure='allocated')
                assert page['total']==25 and len(page['rows'])==20 and page['cursor']
                next_page=handle.page(ident,sort=sort,direction=direction,kind='file',limit=100,cursor=page['cursor'],measure='allocated')
                assert len(next_page['rows'])==5 and not next_page['cursor']
                assert len({r['path'] for r in page['rows']+next_page['rows']})==25
    finally:pool.close()
