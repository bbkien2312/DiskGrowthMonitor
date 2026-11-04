"""Byte-conservation regressions: never lose one-sided multi-GB changes."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import json
import time
import pytest
from dgm import config,production_core as core
from dgm.columnar_ingest import import_entries,metadata_for
from dgm.columnar_compare import Comparison
from dgm.columnar_store import Repository
from test_columnar_core import entry,meta


def real_shape(repo):
    roots=[entry('C:\\',0,0,kind='folder'),entry('C:\\cache',0,0,kind='folder')]
    scans=[]
    for day,files,used in ((28,[entry('C:\\shared',159229783724,146964180992),entry('C:\\cache\\old',10226658477,9325264896)],157633343488),
                          (29,[entry('C:\\shared',159216513058,146919374848),entry('C:\\cache\\new',4686816579,3153850368)],151384604672)):
        m=metadata_for(['C:\\'],profile='daily',mft=True,volume_map={'c:':'volume-A'},
            started=f'2026-10-{day}T03:00:00+00:00',
            volume_snapshots=[{'root':'C:\\','capacity':161290907648,'used':used,'free':161290907648-used}])
        scans.append(import_entries(roots+files,m,repository=repo))
    return scans


def test_real_gap_is_conserved_even_with_old_unknown_coverage(tmp_path):
    repo=Repository(tmp_path/'repo');a,b=real_shape(repo)
    original=[s['sha256'] for s in (a,b)]
    with Comparison(b['id'],a['id'],repository=repo) as q:
        summary=q.summary();assert summary['observed_disk_delta']==-6216220672
        assert summary['verified_common_disk_delta']==-44806144
        assert summary['verified_private_disk_delta']==0
        assert summary['balance_checks']['allocated']=={'snapshot_delta':-6216220672,'file_contributions':-6216220672,'valid':True}
        assert q.tree()['rows'][0]['allocated_delta']==-6216220672
        rows=q.page(kind='file',limit=100)['rows']
        assert sum(r['allocated_delta'] for r in rows)==-6216220672
        old=next(r for r in rows if r['presence']=='old')
        assert old['change']=='unknown' and old['allocated_delta']==-9325264896
        assert old['verified_allocated_delta'] is None and old['attribution_uncertain']
        assert summary['disk_changes'][0]['used_delta']==-6248738816
        assert summary['disk_changes'][0]['unassigned_delta']==-32518144
        for filters in ({'minimum':10_000_000},{'changes':'unknown'},{'search':'cache'},{'root':'C:\\cache'}):
            q.page(limit=1,**filters)
            assert q.summary()['observed_disk_delta']==-6216220672
            assert q.tree()['rows'][0]['allocated_delta']==-6216220672
        cached=q.tree(root='C:\\')['rows'];assert next(r for r in cached if r['path'].rstrip('\\')=='C:\\cache')['allocated_delta']==-6171414528
    assert original==[repo.get(s['id'])['sha256'] for s in (a,b)]
    for s in (a,b):repo.verify(s)
    assert repo.footprint()['work_bytes']==0


@pytest.mark.parametrize('new_rows',[[],[entry('C:\\data\\sub\\new',100,40)]])
def test_entire_folder_disappears_including_all_its_children(tmp_path,new_rows):
    repo=Repository(tmp_path/'repo');root=entry('C:\\data',0,0,kind='folder')
    a=import_entries([root,entry('C:\\data\\sub',0,0,kind='folder'),entry('C:\\data\\sub\\gone',5*1024**3,4*1024**3)],meta(started='2026-01-01T00:00:00+00:00'),repository=repo)
    b=import_entries([root]+([entry('C:\\data\\sub',0,0,kind='folder')]+new_rows if new_rows else []),meta(started='2026-01-02T00:00:00+00:00'),repository=repo)
    with Comparison(b['id'],a['id'],repository=repo) as q:
        expected=(40 if new_rows else 0)-4*1024**3
        assert q.summary()['observed_disk_delta']==expected
        assert q.tree()['rows'][0]['allocated_delta']==expected


def test_hardlink_owner_changes_do_not_fake_freed_space(tmp_path):
    repo=Repository(tmp_path/'repo');root=entry('C:\\data',0,0,kind='folder')
    a=import_entries([root,entry('C:\\data\\a',100,4096,identity='same'),entry('C:\\data\\b',100,4096,identity='same')],meta(started='2026-01-01T00:00:00+00:00'),repository=repo)
    b=import_entries([root,entry('C:\\data\\b',100,4096,identity='same')],meta(started='2026-01-02T00:00:00+00:00'),repository=repo)
    with Comparison(b['id'],a['id'],repository=repo) as q:
        assert q.summary()['observed_disk_delta']==0
        assert q.tree()['rows'][0]['allocated_delta']==0
        assert q.summary()['observed_delta']==-100


def test_replacement_and_null_measurements_never_fake_a_full_total(tmp_path):
    repo=Repository(tmp_path/'repo');root=entry('C:\\data',0,0,kind='folder')
    a=import_entries([root,entry('C:\\data\\x',100,80)],meta(started='2026-01-01T00:00:00+00:00'),repository=repo)
    b=import_entries([root,entry('C:\\data\\x',0,0,kind='folder'),entry('C:\\data\\x\\child',20,None)],meta(started='2026-01-02T00:00:00+00:00'),repository=repo)
    with Comparison(b['id'],a['id'],repository=repo) as q:
        assert q.summary()['observed_delta']==-80
        assert q.summary()['observed_disk_delta'] is None
        assert q.summary()['balance_checks']['logical']['valid']
        assert q.summary()['balance_checks']['allocated']['valid'] is None
        assert q.tree()['rows'][0]['allocated_delta'] is None


@pytest.mark.parametrize('filesystem,stderr,covered',[('NTFS','',True),('exFAT','',False),('NTFS','warning',False)])
def test_mft_coverage_requires_completed_unfiltered_ntfs_proof(isolated,monkeypatch,filesystem,stderr,covered):
    import csv
    from pathlib import Path
    from types import SimpleNamespace
    from dgm import columnar_jobs as jobs_module
    from dgm.columnar_jobs import Jobs
    fake=isolated/'wiztree.exe';fake.write_bytes(b'never executed')
    repo=Repository(isolated/'repo');jobs=Jobs(repo)
    assert jobs_module.private_config_supported(fake) is False
    monkeypatch.setattr(jobs_module,'is_admin',lambda:True)
    monkeypatch.setattr(jobs_module,'filesystem_type',lambda root:filesystem)
    monkeypatch.setattr(jobs_module,'private_config_supported',lambda exe:True)
    configs=[]
    def run(command,**kw):
        destination=Path(next(s.split('=',1)[1] for s in command if s.startswith('/export=')))
        private=Path(next(s.split('=',1)[1] for s in command if s.startswith('/configfolder=')))
        assert private.is_dir() and '/filter=*' in command
        configs.append(private)
        with destination.open('w',newline='',encoding='utf-8') as f:
            writer=csv.writer(f);writer.writerow(['File Name','Size','Allocated','Attributes','MFTRecNo'])
            writer.writerow(['C:\\',100,4096,16,'5']);writer.writerow(['C:\\qa.bin',100,4096,32,'42'])
        return SimpleNamespace(returncode=0,stderr=stderr,stdout='')
    monkeypatch.setattr(jobs_module,'run',run)
    job=jobs.create('scan',{'name':'daily','profile':{'roots':['C:\\'],'engine':'wiztree','mft':True,'strict_browsers':False},'engines':{'wiztree':str(fake)}})
    snapshot=jobs.execute(job['id'])[0]
    assert bool(snapshot['coverage']['covered_roots']) is covered
    assert bool(snapshot['coverage']['unknown_roots']) is not covered
    assert all(not p.exists() for p in configs)


def test_gui_unified_totals_short_headers_tooltips_and_history(isolated,monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication,QMessageBox
    from dgm.main_ui import MainWindow
    from dgm.history_backend import close_indexes
    cfg=config.load();cfg.update(scan_backend='columnar',history_format='jsonl');config.save(cfg)
    repo=core.repository();a,b=real_shape(repo)
    monkeypatch.setattr(MainWindow,'doctor',lambda self:None)
    monkeypatch.setattr(MainWindow,'refresh_background',lambda self:None)
    errors=[];monkeypatch.setattr(QMessageBox,'warning',lambda p,t,m,*_:errors.append(m))
    app=QApplication.instance() or QApplication([]);w=MainWindow();w.show()
    def wait():
        end=time.monotonic()+20;idle=0
        while time.monotonic()<end:
            app.processEvents();time.sleep(.005);idle=idle+1 if not w.workers else 0
            if idle>=5:return
        assert not w.workers
    try:
        wait();u=w.browser;u.newer.setCurrentIndex(u.newer.findData(b['id']));u.older.setCurrentIndex(u.older.findData(a['id']));u.compare();wait()
        assert not u.sections.isVisible() and u.query_options['section']=='all'
        assert u.current_summary['observed_disk_delta']==-6216220672
        assert u.metrics[2].caption.text()=='± GHI NHẬN'
        assert '-6216220672' in u.metrics[2].toolTip()
        assert not u.metrics[0].common_heading.isVisible() and not u.metrics[0].private_value.isVisible()
        assert u.tree.topLevelItem(0).data(0,Qt.ItemDataRole.UserRole)['row']['allocated_delta']==-6216220672
        for i in (0,2,3,4,6):
            assert len(u.model.headerData(i,Qt.Orientation.Horizontal))<=10
            assert u.model.headerData(i,Qt.Orientation.Horizontal,Qt.ItemDataRole.ToolTipRole)
        u.presence.setCurrentIndex(u.presence.findData('old'));wait()
        assert u.model.total>0 and u.current_summary['observed_disk_delta']==-6216220672
        w.tabs.setCurrentIndex(5);wait()
        assert w.history_panel.pairs.rowCount() and w.history_panel.pairs.horizontalHeaderItem(3).text()=='±'
        assert not errors
    finally:
        wait();w.close();app.processEvents();close_indexes()
