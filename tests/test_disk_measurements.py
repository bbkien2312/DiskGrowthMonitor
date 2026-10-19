import copy
import csv
import json
import os
import time

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import pytest
from typer.testing import CliRunner

from dgm.disk_measurements import size, delta, captured_volumes, reconcile
from dgm.columnar_store import Repository
from dgm.columnar_ingest import import_entries
from dgm.columnar_compare import Comparison
from test_columnar_core import entry,meta
from test_columnar_compare import captured,pair


def test_windows_property_bytes_do_not_become_164gb_used():
    assert size(161290907648)=='150 GiB'
    assert size(149131018240)=='138 GiB'
    assert size(148535681024)=='138 GiB'
    assert size(163802063620)=='152 GiB'
    assert size(163802063620,'decimal')=='164 GB'
    assert size(None)=='—' and delta(None)=='?'
    assert delta(-1048576)=='↓ −1 MiB'


def disk_snapshot(used=149150740480, known=148535681024, unknown=0):
    scan=meta()
    scan['scope']['roots']=['C:\\']
    scan['summary']={'allocated_known':known,'allocated_unknown_files':unknown}
    scan['overview']=[{'root':'C:\\','capacity':161290907648,'free':161290907648-used,'used':used}]
    return scan


def test_disk_reconciliation_preserves_true_residual_not_small_files():
    scan=disk_snapshot();before=copy.deepcopy(scan)
    row=reconcile(scan)[0]
    assert row['whole_volume'] and row['residual']==615059456 and row['status']=='unassigned'
    assert row['used']==149150740480 and row['capacity']==161290907648
    assert scan==before
    assert 'small_files' not in row and 'small_logical' not in row


def test_unknown_allocations_partial_scope_and_negative_residual_not_hidden():
    row=reconcile(disk_snapshot(unknown=7))[0]
    assert row['status']=='unknown-allocation' and row['residual'] is None
    assert row['unexplained_upper_bound']==615059456
    scan=disk_snapshot();scan['scope']['roots']=['C:\\Users']
    assert reconcile(scan)[0]['status']=='partial-scope' and reconcile(scan)[0]['residual'] is None
    scan=disk_snapshot(used=100,known=120)
    row=reconcile(scan)[0]
    assert row['status']=='mismatch' and row['residual']==-20


def test_captured_volume_never_uses_today_or_logical_for_missing_used(monkeypatch):
    import dgm.disk_measurements as module
    monkeypatch.setattr(module,'read_volume_usage',lambda *a:pytest.fail('Never probe live disk for historical values'))
    scan=meta();scan['summary']={'logical':163802063620,'allocated_known':1,'allocated_unknown_files':0}
    assert captured_volumes(scan)==[] and reconcile(scan)==[]
    scan=disk_snapshot();scan['volume_snapshots']=[{'root':'C:\\','capacity':999,'used':900,'free':99}]
    row=captured_volumes(scan)[0]
    assert row['source']=='wiztree-csv' and row['used']==149150740480
    scan['overview'][0]['free']=0
    assert not captured_volumes(scan)[0]['valid']
    assert reconcile(scan)[0]['residual'] is None


@pytest.mark.parametrize('capacity,free', [('bad',1),(100,True),(None,10),(-1,10),(10,20)])
def test_invalid_captured_space_stays_unknown_without_arithmetic_error(capacity,free):
    scan=disk_snapshot()
    scan['overview']=[{'root':'C:\\','capacity':capacity,'free':free,'used':None}]
    row=reconcile(scan)[0]
    assert not row['valid'] and row['residual'] is None


def test_physical_sort_filter_and_status_can_disagree_with_logical(tmp_path):
    repo=Repository(tmp_path/'repo')
    root=entry('C:\\data',kind='folder',size=0,allocated=0)
    with pair(repo,[root,entry('C:\\data\\a',100,8000),entry('C:\\data\\b',50,1000)],
              [root,entry('C:\\data\\a',1000,2000),entry('C:\\data\\b',40,9000)]) as query:
        page=query.page(kind='file',sort='new')
        assert page['measurement']=='allocated'
        assert [r['after'] for r in page['rows']]==[9000,2000]
        assert [r['change'] for r in page['rows']]==['grown','shrunk']
        assert [r['delta'] for r in page['rows']]==[8000,-6000]
        assert query.page(kind='file',changes='grown')['rows'][0]['path'].endswith('b')
        logical=query.page(kind='file',measure='logical',sort='new')
        assert logical['rows'][0]['after']==1000 and logical['rows'][0]['change']=='grown'
        tree=query.tree()['rows'][0]
        assert tree['after']==11000 and tree['delta']==2000
        assert tree['logical_after']==1040
        with pytest.raises(ValueError,match='Con trỏ'):
            cursor=query.page(limit=1)['cursor'];query.page(limit=1,measure='logical',cursor=cursor)


def test_physical_global_paging_20_then_100_and_folder_scope(tmp_path):
    folders=[entry('C:\\data',kind='folder',size=0,allocated=0),entry('C:\\data\\sub',kind='folder',size=0,allocated=0)]
    old=folders+[entry(f'C:\\data\\sub\\f{i:03d}',1000-i,1) for i in range(150)]
    new=folders+[entry(f'C:\\data\\sub\\f{i:03d}',1000-i,i+1) for i in range(150)]
    with pair(Repository(tmp_path/'repo'),old,new) as query:
        first=query.page(kind='file',sort='new',root='C:\\data\\sub',limit=20)
        more=query.page(kind='file',sort='new',root='C:\\data\\sub',limit=100,cursor=first['cursor'])
        assert [r['after'] for r in first['rows']+more['rows']]==list(range(150,30,-1))
        assert more['loaded']==120 and first['total']==150


def test_missing_physical_metadata_is_not_zero_or_logical(tmp_path):
    root=entry('C:\\data',kind='folder',size=0,allocated=0)
    with pair(Repository(tmp_path/'repo'),[root,entry('C:\\data\\a',10,None)],
              [root,entry('C:\\data\\a',100,4096)]) as query:
        row=query.page(kind='file')['rows'][0]
        assert row['before'] is None and row['after']==4096 and row['delta'] is None
        assert row['change']=='unknown' and row['logical_delta']==90
        assert query.tree()['rows'][0]['partial_delta']


def test_cli_defaults_to_allocated_and_logical_is_explicit(isolated,tmp_path):
    from dgm.cli import app
    repo=Repository()
    a=import_entries([entry('C:\\data\\a',100,9000)],captured(when='2026-01-01T00:00:00+00:00'),repository=repo)
    b=import_entries([entry('C:\\data\\a',1000,2000)],captured(when='2026-01-02T00:00:00+00:00'),repository=repo)
    runner=CliRunner();result=runner.invoke(app,['fast','compare',str(b['id']),str(a['id'])])
    assert result.exit_code==0,result.output
    body=json.loads(result.stdout)
    assert body['measurement']=='allocated' and body['rows'][0]['delta']==-7000
    result=runner.invoke(app,['fast','compare',str(b['id']),str(a['id']),'--measure','logical'])
    assert json.loads(result.stdout)['rows'][0]['delta']==900


def test_gui_disk_metrics_and_unit_switch_do_not_requery_or_rescan(isolated,tmp_path,monkeypatch):
    from PySide6.QtWidgets import QApplication
    import dgm.columnar_ui as ui
    monkeypatch.setattr(ui,'read_volume_usage',lambda roots:[{'root':'C:\\','capacity':161290907648,'used':149131018240,'free':12159889408}])
    app=QApplication.instance() or QApplication([]);repo=Repository(tmp_path/'repo')
    m=disk_snapshot();m['started']='2026-01-01T00:00:00+00:00'
    scan=import_entries([entry('C:\\',kind='folder',size=0,allocated=0),entry('C:\\big',163802063620,148535681024)],m,repository=repo)
    window=ui.Window(repo);window.show()
    def wait():
        deadline=time.monotonic()+20
        while window.worker and time.monotonic()<deadline:app.processEvents();time.sleep(.01)
        assert window.worker is None
    try:
        window.compare();wait()
        assert window.measure.currentData()=='allocated'
        assert 'Used 138 GiB' in window.live_label.text()
        assert 'Tổng 150 GiB' in window.disk_metrics.text() and 'Đã dùng 138 GiB' in window.disk_metrics.text()
        assert window.tree.topLevelItem(0).text(1)=='138 GiB'
        assert '164 GB' not in window.metrics.text() and '164 GB' not in window.disk_metrics.text()
        assert '148,535,681,024 byte' in window.metrics.toolTip()
        disk=window.query.summary()['disks_new'][0]
        disk['valid']=False;disk['used']='broken'
        window.update_metrics()
        assert 'Đã dùng ?' in window.disk_metrics.text() and 'mâu thuẫn' in window.disk_metrics.text()
        disk['valid']=True;disk['used']=149150740480;window.update_metrics()
        token=window.query.token
        window.measure.setCurrentIndex(window.measure.findData('logical'));wait()
        assert window.query.token==token and window.tree.topLevelItem(0).text(1)=='152 GiB'
        monkeypatch.setattr(window.query,'page',lambda **kw:pytest.fail('Changing unit must not query snapshots'))
        window.units.setCurrentIndex(window.units.findData('decimal'));app.processEvents()
        assert window.tree.topLevelItem(0).text(1)=='164 GB' and 'Logical' in window.metrics.text()
        assert '149 GB' in window.disk_metrics.text() and '164 GB' not in window.disk_metrics.text()
        assert window.query.token==token
    finally:
        window.close();app.processEvents()
    assert repo.footprint()['work_bytes']==0
