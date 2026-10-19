"""Regression: manual #1 walk, manual #2 MFT, scheduled #3 walk."""
import json
import os
import threading
import time
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
from typer.testing import CliRunner

from dgm import config, production_core as core
from dgm.columnar_ingest import import_entries
from dgm.comparison_policy import choose_measure
from test_columnar_core import entry, meta


@pytest.fixture
def parity_repo(isolated):
    cfg = config.load()
    cfg.update(scan_backend='columnar', history_format='jsonl')
    cfg['profiles']['daily'].update(roots=['C:\\data'], mft=False, strict_browsers=False)
    config.save(cfg)
    return core.repository()


def three_scans(repo, count=130):
    rows = []
    for number in (1, 2, 3):
        entries = [entry('C:\\data', 0, 0, kind='folder'), entry('C:\\data\\sub', 0, 0, kind='folder')]
        entries += [entry(f'C:\\data\\sub\\file-{i:03}.bin', 25_000_000 + i*1000 + number*100,
                          20_000_000+i*1000 if number==2 else None) for i in range(count)]
        rows.append(import_entries(entries, meta(profile='daily', engine='wiztree', mft=number==2,
            started=f'2026-10-19T13:2{number}:00+00:00'), repository=repo))
    return rows


def test_measurement_policy_only_falls_back_for_wholly_missing_allocation():
    def scan(ident, unknown, **kw):
        return {'id':ident, 'summary':dict(files=10, allocated_unknown_files=unknown,
            logical_unknown_files=0, logical=1000, **kw)}
    a, b = scan(1,10), scan(2,0)
    assert choose_measure(b,a)['measurement']=='logical'
    assert choose_measure(b,a)['missing_allocation_scans']==[1]
    assert choose_measure(b,a,'allocated')['measurement']=='allocated'
    assert choose_measure(b,scan(3,1))['measurement']=='allocated'
    assert choose_measure({'id':4,'summary':{'files':0,'allocated':0,'logical':0}})['measurement']=='allocated'
    with pytest.raises(ValueError):choose_measure(b,a,'invalid')


def test_repeated_display_switch_keeps_both_sets_of_status_counts():
    from dgm.main_ui import ColumnarBrowser
    mode=['logical']
    browser=SimpleNamespace(measurement=SimpleNamespace(currentData=lambda:mode[0]))
    physical=[{'kind':'file','change':'unknown','presence':'both','count':10}]
    logical=[{'kind':'file','change':'grown','presence':'both','count':10}]
    raw={'counts':physical,'counts_logical':logical}
    viewed=ColumnarBrowser.view_summary(browser,raw)
    assert viewed['counts']==logical and raw['counts']==physical
    mode[0]='allocated'
    restored=ColumnarBrowser.view_summary(browser,viewed)
    assert restored['counts']==physical and restored['common_counts']==physical


@pytest.mark.parametrize('old,new',[(1,2),(1,3),(2,3)])
def test_all_three_pairs_auto_compare_without_rewriting_snapshots(parity_repo,old,new):
    scans=three_scans(parity_repo)
    fingerprints=[s['sha256'] for s in scans]
    with core.query(new,old) as query:
        page=core.page(query,kind='file',section='common',minimum=10_000_000,limit=20)
        assert page['measurement']=='logical' and page['automatic_measurement']
        assert page['total']==130 and len(page['rows'])==20
        assert all(row['change']=='grown' and row['delta']==(new-old)*100 for row in page['rows'])
        assert all(row['presence']=='both' for row in page['rows'])
        assert query.summary()['partition_valid'] and query.summary()['common_rows']==132
        assert query.summary()['private_rows']==0
        explicit=core.page(query,kind='file',section='common',minimum=10_000_000,measure='allocated')
        assert explicit['total']==130 and explicit['measurement']=='allocated'
        assert not explicit['automatic_measurement']
        assert all(row['delta'] is None for row in explicit['rows'])
    assert fingerprints==[parity_repo.get(i)['sha256'] for i in (1,2,3)]
    for scan in scans:parity_repo.verify(scan)
    assert parity_repo.footprint()['work_bytes']==0


def test_auto_global_sort_cursor_offset_and_folder_scope(parity_repo):
    three_scans(parity_repo)
    with core.query(3,1) as query:
        options=dict(kind='file',minimum=10_000_000,sort='new',direction='desc',root='C:\\data\\sub')
        first=core.page(query,limit=20,**options)
        second=core.page(query,limit=100,cursor=first['cursor'],**options)
        assert second['loaded']==120 and second['total']==130
        combined=first['rows']+second['rows']
        assert len({r['item_id'] for r in combined})==120
        assert [r['after'] for r in combined]==sorted((r['after'] for r in combined),reverse=True)
        offset=core.page(query,offset=20,limit=100,**options)
        assert [r['item_id'] for r in offset['rows']]==[r['item_id'] for r in second['rows']]
        tree=query.tree(measure='auto')
        assert tree['measurement']=='logical' and tree['rows'][0]['after'] is not None


def test_first_scan_has_new_sizes_but_no_invented_old_or_delta(parity_repo):
    three_scans(parity_repo)
    with core.query(1,baseline=True) as query:
        page=core.page(query,kind='file',minimum=10_000_000)
        assert page['measurement']=='logical' and page['total']==130
        assert all(r['change']=='first_scan' and r['baseline'] for r in page['rows'])
        assert all(r['before'] is None and r['after']>10_000_000 and r['delta'] is None for r in page['rows'])
        assert page['summary']['common_new']['files']==0
        assert page['summary']['private_new']['files']==130
        assert page['summary']['private_new']['allocated'] is None


def test_unknown_measurements_survive_threshold_but_known_small_does_not(parity_repo):
    first=import_entries([entry('C:\\data\\unknown',1,None),entry('C:\\data\\small',1,1)],meta(),repository=parity_repo)
    with core.query(first['id'],baseline=True) as query:
        result=core.page(query,kind='file',minimum=10_000_000,measure='allocated')
        assert [r['path'] for r in result['rows']]==['C:\\data\\unknown']
        assert result['rows'][0]['after'] is None and result['minimum_note']


def test_cli_auto_and_explicit_measure_match_gui_policy(parity_repo):
    from dgm.cli import app
    three_scans(parity_repo);runner=CliRunner()
    for old,new in ((1,2),(1,3),(2,3)):
        result=runner.invoke(app,['compare',str(new),'--older',str(old),'--kind','file'])
        assert result.exit_code==0,result.output
        page=json.loads(result.stdout)
        assert page['measurement']=='logical' and page['requested_measure']=='auto' and page['total']==130
    for args in (['entries','1','--kind','file'],['tree','--newer','3','--older','1']):
        result=runner.invoke(app,args);assert result.exit_code==0,result.output
        page=json.loads(result.stdout)
        assert page['measurement']=='logical' and page['rows'] and page['rows'][0]['after'] is not None


def test_scheduler_tick_and_run_now_use_identical_saved_options(parity_repo,monkeypatch):
    from dgm import schedule
    spec=schedule.add({'profile':'daily','mode':'daily','times':['09:00'],'timezone':'Asia/Ho_Chi_Minh'})
    cfg=config.load();cfg['schedules'][0]['next_run']='2026-01-01T00:00:00+00:00';config.save(cfg)
    calls=[]
    def fake(name,p,**kw):
        calls.append((name,p));return {'status':'complete','scan_id':1}
    monkeypatch.setattr(schedule,'scan',fake)
    schedule.tick();schedule.run_now(spec['id'])
    assert len(calls)==2 and calls[0]==calls[1]
    assert calls[0][1]==config.profile('daily') and calls[0][1]['mft'] is False
    cfg=config.load();cfg['profiles']['daily']['mft']=True;config.save(cfg)
    schedule.run_now(spec['id'])
    assert calls[-1][1]['mft'] is True


def settle(app,window):
    deadline=time.monotonic()+25;idle=0
    while time.monotonic()<deadline:
        app.processEvents();time.sleep(.005)
        idle=idle+1 if not window.workers else 0
        if idle>=5:return
    raise AssertionError('GUI workers did not finish')


@pytest.fixture
def parity_window(parity_repo,monkeypatch):
    from PySide6.QtWidgets import QApplication,QMessageBox
    from dgm.main_ui import MainWindow
    from dgm.history_backend import close_indexes
    monkeypatch.setattr(MainWindow,'doctor',lambda self:None)
    monkeypatch.setattr(MainWindow,'refresh_background',lambda self:None)
    errors=[];monkeypatch.setattr(QMessageBox,'warning',lambda p,t,message,*_:errors.append(message))
    three_scans(parity_repo)
    app=QApplication.instance() or QApplication([]);window=MainWindow();window.show();settle(app,window)
    yield app,window,errors
    for worker in window.workers:worker.stop.set()
    settle(app,window);window.close();app.processEvents();close_indexes()
    assert not errors


def test_gui_three_pairs_and_baseline_show_sizes_in_tree_and_details(parity_window):
    from PySide6.QtCore import Qt
    app,window,_=parity_window;ui=window.browser
    for old,new in ((1,2),(1,3),(2,3),(0,1)):
        ui.newer.setCurrentIndex(ui.newer.findData(new));ui.older.setCurrentIndex(ui.older.findData(old))
        ui.compare();settle(app,window)
        assert ui.measurement.currentData()=='logical'
        assert ui.query_options['measure']=='logical' and ui.measurement_notice.isVisible()
        assert ui.model.loaded==20 and ui.model.total>=130
        assert ui.tree.topLevelItem(0).text(1) not in ('?','—')
        assert ui.model.data(ui.model.index(0,3)) not in ('?','—')
        assert 'Logical' in window.status_panel.results
        counts=ui.current_summary['counts_logical']
        grown=sum(r['count'] for r in counts if r['change']=='grown')
        assert f'{grown:,} tăng' in window.status.text()
        assert ui.metrics[1].caption.text()=='KÍCH THƯỚC FILE MỚI'
        if old==0:
            assert ui.model.private and ui.model.data(ui.model.index(0,0))=='●'
            assert ui.model.data(ui.model.index(0,2))=='—'
            assert ui.model.data(ui.model.index(0,4))=='—' and ui.model.data(ui.model.index(0,6))=='—'
            assert ui.metrics[0].value.text()=='—' and ui.metrics[2].private_value.text()=='—'
            assert 'Mốc ban đầu' in ui.model.data(ui.model.index(0,4),Qt.ItemDataRole.ToolTipRole)
            assert ui.tree.topLevelItem(0).text(2)=='—'
        else:
            ui.load_more();settle(app,window);assert ui.model.loaded==40
    # An explicit disk view remains possible, with unknown values clearly shown.
    ui.measurement.setCurrentIndex(ui.measurement.findData('allocated'));settle(app,window)
    assert ui.measurement.currentData()=='allocated' and ui.model.total>=130
    assert ui.model.data(ui.model.index(0,3))=='?' and ui.measurement_notice.isVisible()
    # A new pair with full allocation resets the per-pair override and uses disk bytes.
    ui.newer.setCurrentIndex(ui.newer.findData(2));ui.older.setCurrentIndex(ui.older.findData(0))
    ui.compare();settle(app,window)
    assert ui.measurement.currentData()=='allocated' and not ui.measurement_notice.isVisible()


@pytest.mark.parametrize('choice,expected',[('save',True),('saved',False),('cancel',None)])
def test_manual_and_scheduled_gui_resolve_dirty_profile_explicitly(parity_window,monkeypatch,choice,expected):
    from dgm import schedule
    app,window,_=parity_window
    schedule.add({'profile':'daily','mode':'daily','times':['09:00'],'timezone':'Asia/Ho_Chi_Minh'})
    window.refresh_schedules();window.mft.setChecked(True)
    questions=[];calls=[]
    monkeypatch.setattr(window,'ask_run_profile',lambda *args:questions.append(args) or choice)
    def fake_scan(name,p,*args,**kwargs):
        calls.append(dict(p));return {'status':'cancelled'}
    monkeypatch.setattr(schedule,'scan',fake_scan)
    def fake_job(fn,done,**kw):
        fn(SimpleNamespace(stop=threading.Event(),event=SimpleNamespace(emit=lambda *args:None)))
    monkeypatch.setattr(window,'job',fake_job)
    window.begin_scan();window.run_schedule_now()
    if expected is None:
        assert not calls and config.profile('daily')['mft'] is False
    else:
        assert len(calls)==2 and all(p['mft'] is expected for p in calls)
        assert calls[0]==calls[1]==config.profile('daily')
        if choice=='save':assert 'MFT bật' in window.schedule_table.item(0,1).text()
    assert questions
