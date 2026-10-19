import json
import os
import time
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import pytest
from PySide6.QtCore import QPoint
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication,QMessageBox
from dgm import config,production_core as core
from dgm.operation_progress import Timeline
from dgm.columnar_jobs import Jobs
from dgm.main_ui import MainWindow
from test_columnar_core import entry,meta
from dgm.columnar_ingest import import_entries


def settle(app,window):
    until=time.monotonic()+25;idle=0
    while time.monotonic()<until:
        app.processEvents();time.sleep(.005)
        idle=idle+1 if not window.workers else 0
        if idle>=5:break
    assert not window.workers


def test_phase_clock_monotonic_and_multi_snapshot_milestones():
    clock=[0.];events=[]
    timer=Timeline('scan',lambda e,d:events.append(d),clock=lambda:clock[0],finish_on_done=False)
    timer('progress',{'stage':'collect','percent':5,'message':'Thu thập'})
    clock[0]=3.;timer('progress',{'stage':'read_source','percent':10,'message':'Đọc CSV'})
    clock[0]=5.;timer('progress',{'stage':'done','percent':100,'message':'Đã lưu mốc 1'})
    assert timer.state=='running' and timer.ended is None
    clock[0]=7.;timer('progress',{'stage':'read_source','percent':10,'message':'Đọc mốc tiếp'})
    clock[0]=10.;result=timer.finish()
    assert result['seconds']==10 and result['state']=='complete'
    assert [r['seconds'] for r in result['stages']]==[3.,2.,3.]
    assert all(r['state']=='complete' for r in result['stages'])
    assert [r['percent'] for r in events]==sorted(r['percent'] for r in events)
    assert events[-1]['percent']==100


@pytest.mark.parametrize('state',['cancelled','failed'])
def test_stopped_stage_never_claims_100_percent(state):
    clock=[0.];timer=Timeline('comparison',clock=lambda:clock[0])
    timer('progress',{'stage':'join','percent':25,'message':'Ghép mốc'})
    clock[0]=2.;report=timer.finish(state)
    assert report['state']==state and report['stages'][0]['state']==state and timer.percent==25


def test_native_job_persists_phase_times_in_small_journal(isolated,tmp_path):
    cfg=config.load();cfg['scan_backend']='columnar';cfg['history_format']='jsonl';config.save(cfg)
    files=tmp_path/'owned';files.mkdir();(files/'one.txt').write_text('fixture')
    events=[];result=core.scan('daily',{'roots':[str(files)],'engine':'native','mft':False},notify=lambda e,d:events.append((e,d)))
    report=result['timing'];job=Jobs(core.repository()).get(result['job_id'])
    assert report==job['timing'] and report['state']=='complete'
    assert {'prepare','collect','validate','normalize','hardlinks','folders','write','verify'}<=set(r['key'] for r in report['stages'])
    assert sum(r['seconds'] for r in report['stages'])<=report['seconds']+.001
    assert all(r['seconds']>=0 for r in report['stages']) and events[-1][1]['percent']==100


def test_update_manifest_covers_parquet_and_journals_not_ram_spill(isolated):
    cfg=config.load();cfg['scan_backend']='columnar';cfg['history_format']='jsonl';config.save(cfg)
    repo=core.repository();scan=import_entries([entry('C:\\data\\one')],meta(),repository=repo)
    jobs=Jobs(repo);job=jobs.create('legacy',{'source_home':str(config.home()),'ids':[]})
    scratch=repo.work/'diagnostic-only';scratch.mkdir();(scratch/'temp.bin').write_bytes(b'temporary')
    from dgm.app_update import columnar_manifest
    proof=columnar_manifest()
    assert any(key.endswith('.parquet') for key in proof) and any(key.endswith('job.json') for key in proof)
    assert not any('/work/' in key for key in proof)
    assert len(proof)==3 # catalog, immutable snapshot, small journal; no duplicate backup.


@pytest.fixture
def window(isolated,monkeypatch):
    cfg=config.load();cfg['scan_backend']='columnar';cfg['history_format']='jsonl';config.save(cfg)
    from dgm.ui_helpers import Preferences
    prefs=Preferences();prefs.set('splitter/status-v2',[800,42]);prefs.set('splitter/status-v3',[800,42])
    monkeypatch.setattr(MainWindow,'doctor',lambda s:None);monkeypatch.setattr(MainWindow,'refresh_background',lambda s:None)
    errors=[];monkeypatch.setattr(QMessageBox,'warning',lambda p,t,message,*_:errors.append(message))
    app=QApplication.instance() or QApplication([]);old_font=QFont(app.font());w=MainWindow();w.show();settle(app,w)
    yield app,w,errors
    for worker in w.workers:worker.stop.set()
    settle(app,w);w.close();app.processEvents();app.setFont(old_font)


@pytest.mark.parametrize('size',[(1440,940),(1152,700),(800,600),(640,480)])
def test_footer_all_rows_fit_at_small_sizes_with_saved_42px(window,size):
    app,w,errors=window;w.resize(*size)
    w.status.setText('Nạp chi tiết · đang truy vấn 100 mục theo thứ tự toàn tập')
    w.status_panel.legacy_details('Đã xong: '+'Rất nhiều bước hoàn thành '*50)
    w.scan_progress.setValue(96);w.scan_progress.setFormat('%p% · Đang nạp bảng chi tiết')
    for expanded in (False,True,False):
        w.status_details_button.setChecked(expanded);settle(app,w)
        assert w.status_scroll.isVisible()==expanded
        host=w.status_panel
        for widget in (w.status,w.scan_progress,w.status_details_button):
            top=widget.mapTo(host,QPoint(0,0)).y()
            assert top>=0 and top+widget.height()<=host.height()
        assert w.status.height()>=w.status.fontMetrics().height()
        assert w.scan_progress.height()>=w.scan_progress.fontMetrics().height()+8
        if expanded:
            assert w.status_scroll.geometry().bottom()<host.height()
            assert w.status_scroll.height()>=105
    assert not errors


def test_timing_report_counts_whole_pair_hidden_and_live_pipeline_completion(window):
    app,w,errors=window;repo=w.repo
    root=entry('C:\\data',0,0,kind='folder')
    cov={'source':'native','unknown_roots':[],'error_regions':[]}
    old=import_entries([root,entry('C:\\data\\grow',100,4096),entry('C:\\data\\shrink',200,8192),entry('C:\\data\\gone',100,4096)],
        meta(profile='daily',started='2026-01-01T00:00:00+00:00',coverage=cov),repository=repo)
    new=import_entries([root,entry('C:\\data\\grow',200,8192),entry('C:\\data\\shrink',100,4096),entry('C:\\data\\added',100,4096)],
        meta(profile='daily',started='2026-01-02T00:00:00+00:00',coverage=cov),repository=repo)
    w.refresh_history();w.threshold.setValue(0);w.newer.setCurrentIndex(w.newer.findData(new['id']));w.compare();settle(app,w)
    assert not errors and w.scan_progress.value()==100
    assert not w.status_scroll.isVisible() and w.status_details_button.isVisible()
    text=w.completed_steps.text()
    for label in ('Tăng:','Giảm:','Mới: 1','Mất: 1','Không đổi:','Không quét:','Chưa xác định:','Nạp gốc cây','Truy vấn / nạp trang'):
        assert label in text
    assert '923' not in text # No fabricated sample total.
    assert w.status_panel.flow_finished is not None
    comparison=w.status_panel.operations['comparison']
    assert comparison['state']=='complete' and {'checksum','join','folders','summary'}<=set(r['key'] for r in comparison['stages'])
    # Selecting a status subset changes the table, not the whole-pair counters.
    w.browser.sections.setCurrentIndex(1);settle(app,w)
    w.browser.change.select(['new']);w.browser.reload();settle(app,w)
    assert w.browser.model.total==1 and 'Mất: 1' in w.completed_steps.text()
    w.status_details_button.click();settle(app,w);assert w.status_scroll.isVisible()
