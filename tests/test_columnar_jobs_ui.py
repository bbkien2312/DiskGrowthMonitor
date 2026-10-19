import csv
import json
import os
from pathlib import Path
import threading
import time

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import pytest
from typer.testing import CliRunner

from dgm.columnar_store import Repository, MemorySession, atomic_json
from dgm.columnar_jobs import Jobs
from dgm.columnar_ingest import import_entries
from dgm.columnar_compare import Comparison
from dgm.platform_ops import ProcessCancelled
from test_columnar_core import entry,meta,legacy_fixture
from test_columnar_compare import captured,pair


def test_fast_cli_history_and_global_offset(isolated,tmp_path):
    from dgm.cli import app
    repo=Repository()
    a=import_entries([entry(f'C:\\data\\f{i:03d}',i+1) for i in range(45)],captured(when='2026-01-01T00:00:00+00:00'),repository=repo)
    runner=CliRunner()
    result=runner.invoke(app,['fast','compare',str(a['id']),'--sort','new','--limit','20','--offset','20','--measure','logical'])
    assert result.exit_code==0,result.output
    # Typer's merged stream includes progress messages before JSON.
    body=json.loads(result.stdout)
    assert len(body['rows'])==20 and body['rows'][0]['after']==25 and body['loaded']==40
    assert runner.invoke(app,['fast','history']).exit_code==0
    assert not list(repo.root.rglob('*.sqlite*'))


def test_fast_native_job_uses_single_metadata_stream_and_private_home(isolated,tmp_path):
    root=tmp_path/'capture';root.mkdir();(root/'a').write_bytes(b'abc')
    jobs=Jobs(Repository(tmp_path/'repo'))
    job=jobs.create('scan',{'profile':{'engine':'native','roots':[str(root)],'strict_browsers':False,'mft':False},'engines':{}})
    results=jobs.execute(job['id'])
    assert results[0]['summary']['logical']==3
    assert results[0]['summary']['folders']==1
    assert jobs.get(job['id'])['state']=='complete'
    assert jobs.execute(job['id'])[0]['id']==results[0]['id']
    assert jobs.source_bytes()==0
    assert not list(jobs.repo.root.rglob('*.sqlite*'))


def test_fast_job_stop_resume_keeps_original_metadata(tmp_path):
    source=tmp_path/'source';legacy_fixture(source,[entry('C:\\data\\a',42)])
    jobs=Jobs(Repository(tmp_path/'repo'))
    job=jobs.create('legacy',{'source_home':str(source),'ids':[36]})
    stop=threading.Event();stop.set()
    with pytest.raises(ProcessCancelled):jobs.execute(job['id'],stop=stop)
    assert jobs.get(job['id'])['state']=='stopped'
    result=jobs.execute(job['id'])
    assert result[0]['summary']['logical']==42
    jobs.discard(job['id'])
    assert len(jobs.repo.list())==1
    assert (source/'data/history/catalog.json').exists()


def test_fast_export_reuses_finished_source_after_import_cancel(isolated,tmp_path,monkeypatch):
    import dgm.columnar_jobs as module
    calls=[]
    exe=tmp_path/'wiz.exe';exe.touch()
    def export(command,**kwargs):
        from subprocess import CompletedProcess
        calls.append(command)
        path=next(arg.split('=',1)[1] for arg in command if arg.startswith('/export='))
        with open(path,'w',encoding='utf-8',newline='') as stream:
            writer=csv.writer(stream);writer.writerow(['File Name','Size','Allocated','Attributes','MFTRecNo'])
            writer.writerow(['C:\\data\\',100,4096,16,1]);writer.writerow(['C:\\data\\a',10,4096,0,2])
        return CompletedProcess(command,0,'','')
    monkeypatch.setattr(module,'run',export)
    monkeypatch.setattr(module,'is_admin',lambda:True)
    jobs=Jobs(Repository(tmp_path/'repo'))
    job=jobs.create('scan',{'profile':{'roots':['C:\\data'],'engine':'wiztree','mft':False},'engines':{'wiztree':str(exe)}})
    real=module.import_wiztree
    def canceled(*args,**kwargs):raise ProcessCancelled('test after export')
    monkeypatch.setattr(module,'import_wiztree',canceled)
    with pytest.raises(ProcessCancelled):jobs.execute(job['id'])
    assert jobs.get(job['id'])['sources'] and jobs.source_bytes()>0
    monkeypatch.setattr(module,'import_wiztree',real)
    result=jobs.execute(job['id'])
    assert len(calls)==1 and result[0]['summary']['logical']==10
    assert jobs.source_bytes()==0


def test_fast_cancel_query_from_other_thread_leaves_session_usable(tmp_path):
    repo=Repository(tmp_path/'repo');stop=threading.Event()
    with MemorySession(repo) as session:
        timer=threading.Timer(.1,stop.set);timer.start()
        with pytest.raises(ProcessCancelled),session.operation(stop):
            session.db.execute('SELECT sum(sqrt(i::DOUBLE)) FROM range(10000000000) r(i)').fetchone()
        timer.join()
        assert session.rows('SELECT 42 AS n')==[{'n':42}]
    assert repo.footprint()['work_bytes']==0


def test_fast_cleanup_only_dead_owned_sessions(tmp_path):
    repo=Repository(tmp_path/'repo')
    with MemorySession(repo) as active:
        abandoned=repo.work/'session-dead';abandoned.mkdir();atomic_json(abandoned/'owner.json',{'token':'a'*32,'owner':{'pid':99999999,'created':0}})
        (abandoned/'cache').write_bytes(b'1234')
        unknown=repo.work/'session-unknown';unknown.mkdir();(unknown/'keep').touch()
        result=repo.cleanup_abandoned()
        assert result['removed_bytes']>=4 and result['sessions']==['session-dead']
        assert active.work.exists() and unknown.exists()


def test_fast_unknown_old_volume_alias_and_known_different_volume_not_merged(tmp_path):
    repo=Repository(tmp_path/'repo')
    old=captured(when='2026-01-01T00:00:00+00:00');old.pop('volume_map')
    new=captured(when='2026-01-02T00:00:00+00:00')
    with pair(repo,[entry('C:\\data\\a',10)],[entry('C:\\data\\a',20)],a=old,b=new) as compare:
        assert compare.page(measure='logical')['rows'][0]['delta']==10
        assert compare.summary()['volume_assumptions']
    old=captured(when='2026-02-01T00:00:00+00:00');old['volume_map']={'c:':'different-volume'}
    new=captured(when='2026-02-02T00:00:00+00:00')
    with pair(repo,[entry('C:\\data\\a',10)],[entry('C:\\data\\a',20)],a=old,b=new) as compare:
        assert compare.summary()['common_rows']==0
        assert all(r['delta'] is None and r['reason']=='volume' for r in compare.page()['rows'])


def test_fast_browser_scope_is_separate_not_false_deletion(tmp_path):
    old=captured(when='2026-01-01T00:00:00+00:00')
    new=captured(when='2026-01-02T00:00:00+00:00');new['scope']['strict_browsers']=True
    with pair(Repository(tmp_path/'repo'),[entry('C:\\data\\Chrome\\a',10)],[],a=old,b=new) as compare:
        row=compare.page()['rows'][0]
        assert row['change']=='not_scanned' and row['new_issue']=='browser' and row['delta'] is None


def test_fast_gui_async_load_paging_icons_and_scope(isolated,tmp_path):
    from PySide6.QtWidgets import QApplication
    from dgm.columnar_ui import Window,ROLE
    app=QApplication.instance() or QApplication([])
    repo=Repository(tmp_path/'repo')
    folders=[entry('C:\\data',kind='folder',size=0,allocated=0),entry('C:\\data\\sub',kind='folder',size=0,allocated=0)]
    old=import_entries(folders+[entry(f'C:\\data\\sub\\{i}',i+1) for i in range(130)],captured(when='2026-01-01T00:00:00+00:00'),repository=repo)
    new=import_entries(folders+[entry(f'C:\\data\\sub\\{i}',i+2) for i in range(130)],captured(when='2026-01-02T00:00:00+00:00'),repository=repo)
    window=Window(repo);window.show()
    def wait():
        deadline=time.monotonic()+20
        while window.worker and time.monotonic()<deadline:app.processEvents();time.sleep(.01)
        assert window.worker is None
        app.processEvents()
    window.compare();wait()
    assert window.measure.currentData()=='allocated'
    assert window.table.item(0,0).data(ROLE)['after']>=4096
    assert window.table.rowCount()==20 and window.tree.topLevelItemCount()==1
    assert not window.table.item(0,1).icon().isNull()
    window.batch.setCurrentText('100');window.reload_page(more=True);wait()
    assert window.table.rowCount()==120
    window.measure.setCurrentIndex(window.measure.findData('logical'));wait()
    window.folder_scope=window.query.scope_new.keys.key('C:\\data\\sub');window.sort='new';window.reload_page();wait()
    assert window.table.item(0,0).data(ROLE)['after']==131
    assert window.note.isVisible()
    window.close();app.processEvents()
    assert repo.footprint()['work_bytes']==0
