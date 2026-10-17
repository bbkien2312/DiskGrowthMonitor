import gzip
import json
import os
from pathlib import Path
import threading
import time
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox,QInputDialog
from typer.testing import CliRunner
from dgm import cleanup
from dgm.config import home,load,save
from dgm.store import Store
from dgm.cleanup_storage import location,prune,claim
from dgm.history_presentation import normalization,coverage,needs_normalization,pair_explanation
from dgm.manual_inspection import inspect_folder
from dgm.platform_ops import ProcessCancelled
from test_cleanup import old_file
from test_v2_gui import gui,pump


def cache_fixture(isolated,monkeypatch,count=25):
    root=isolated/'cache';root.mkdir()
    for i in range(count):old_file(root/f'file-{i:04}.bin',b'x'*(i+1))
    info={'title':'Cache thử nghiệm','roots':[str(root)],'days':7,'risk':'Chỉ file thử trên D:','group':'Cache / chẩn đoán','what':'Metadata thử','when':'Chỉ test','keep':'Giữ file ngoài fixture','method':'Từng file'}
    monkeypatch.setattr(cleanup,'rules',lambda:{'test-cache':info})
    return root


def test_plan_catalog_is_small_compressed_metadata_and_page_global(isolated,monkeypatch):
    root=cache_fixture(isolated,monkeypatch,150)
    p=cleanup.plan(['test-cache'],compact=True)
    assert p['entries']==[] and p['entry_count']==150 and len(list(root.iterdir()))==150
    s=Store();raw=s.db.execute('SELECT content FROM plans WHERE id=?',(p['id'],)).fetchone()[0];s.close()
    assert len(raw)<4000 and 'file-0149' not in raw
    assert location(p['entry_archive']['name']).is_file()
    page=cleanup.plan_page(p['id'],limit=20)
    assert page['total']==150 and [r['size'] for r in page['entries']]==list(range(150,130,-1))
    more=cleanup.plan_page(p['id'],offset=20,limit=100)
    assert [r['size'] for r in more['entries']]==list(range(130,30,-1))
    asc=cleanup.plan_page(p['id'],sort='path',direction='asc',limit=20)
    assert Path(asc['entries'][0]['path']).name=='file-0000.bin'
    assert len(cleanup.get_plan(p['id'])['entries'])==150


def test_selection_rewrites_metadata_and_only_selected_fixture_is_deleted(isolated,monkeypatch):
    root=cache_fixture(isolated,monkeypatch,3);p=cleanup.plan(['test-cache'],compact=True)
    cleanup.select(p['id'],[str(root/'file-0001.bin')],compact=True)
    assert cleanup.get_plan(p['id'],compact=True)['entry_count']==1
    result=cleanup.apply(p['id'],p['confirmation'])
    assert result['deleted_bytes']==2 and result['counts']['deleted']==1
    assert not (root/'file-0001.bin').exists() and len(list(root.iterdir()))==2
    with pytest.raises(ValueError):cleanup.apply(p['id'],p['confirmation'])


def test_outside_selection_is_rejected_without_changing_original(isolated,monkeypatch):
    root=cache_fixture(isolated,monkeypatch,2);p=cleanup.plan(['test-cache'],compact=True)
    with pytest.raises(ValueError):cleanup.select(p['id'],[str(isolated/'not-approved')])
    assert cleanup.get_plan(p['id'],compact=True)['entry_archive']==p['entry_archive']
    assert len(list(root.iterdir()))==2
    with pytest.raises(ValueError):cleanup.select(p['id'],[])


def test_corrupt_plan_archive_never_deletes(isolated,monkeypatch):
    root=cache_fixture(isolated,monkeypatch,2);p=cleanup.plan(['test-cache'],compact=True)
    location(p['entry_archive']['name']).write_bytes(b'corrupt')
    with pytest.raises(ValueError,match='thay đổi'):cleanup.apply(p['id'],p['confirmation'])
    assert len(list(root.iterdir()))==2


def test_claim_rejects_stale_selection_and_claim_failure_is_recorded(isolated,monkeypatch):
    root=cache_fixture(isolated,monkeypatch,2);p=cleanup.plan(['test-cache'],compact=True)
    cleanup.select(p['id'],[str(root/'file-0000.bin')],compact=True)
    with pytest.raises(ValueError):
        with claim(p):pytest.fail('Must not execute stale selection')
    current=cleanup.get_plan(p['id'],compact=True)
    with pytest.raises(RuntimeError):
        with claim(current):raise RuntimeError('fixture failure')
    assert cleanup.get_plan(p['id'],compact=True)['status']=='failed'
    assert len(list(root.iterdir()))==2


def test_ui_review_digest_rejects_a_different_selection(isolated,monkeypatch):
    from dgm.elevation import digest
    root=cache_fixture(isolated,monkeypatch,2);p=cleanup.plan(['test-cache'],compact=True)
    cleanup.select(p['id'],[str(root/'file-0000.bin')],compact=True)
    with pytest.raises(ValueError,match='hiển thị'):
        cleanup.select(p['id'],[str(root/'file-0000.bin')],compact=True,expected_digest=digest(p))
    assert len(list(root.iterdir()))==2


def test_plan_summary_retention_is_bounded_without_touching_user_files(isolated,monkeypatch):
    root=cache_fixture(isolated,monkeypatch,0);cfg=load();cfg['cleanup_max_summaries']=2;save(cfg)
    first=cleanup.plan(['test-cache'],compact=True);second=cleanup.plan(['test-cache'],compact=True)
    with pytest.raises(RuntimeError,match='hạn mức'):cleanup.plan(['test-cache'],compact=True)
    s=Store()
    for p in (first,second):
        content=json.loads(s.db.execute('SELECT content FROM plans WHERE id=?',(p['id'],)).fetchone()[0]);content['expires']=time.time()-1
        s.db.execute('UPDATE plans SET content=? WHERE id=?',(json.dumps(content),p['id']))
    s.db.commit();s.close()
    third=cleanup.plan(['test-cache'],compact=True)
    s=Store();assert s.db.execute('SELECT COUNT(*) FROM plans').fetchone()[0]==2;s.close()
    assert root.exists() and cleanup.get_plan(third['id'],compact=True)['status']=='pending'


def test_expiry_only_removes_app_metadata_not_cache_or_scan_history(isolated,monkeypatch):
    root=cache_fixture(isolated,monkeypatch,2);p=cleanup.plan(['test-cache'],compact=True)
    s=Store();content=json.loads(s.db.execute('SELECT content FROM plans WHERE id=?',(p['id'],)).fetchone()[0]);content['expires']=time.time()-1
    s.db.execute('UPDATE plans SET content=? WHERE id=?',(json.dumps(content),p['id']));s.db.commit();prune(s);s.close()
    assert not location(p['entry_archive']['name']).exists() and len(list(root.iterdir()))==2
    assert cleanup.get_plan(p['id'],compact=True)['status']=='expired'
    assert cleanup.plan_page(p['id'])['total']==0
    with pytest.raises(ValueError):cleanup.apply(p['id'],p['confirmation'])


def test_plan_size_cap_and_cancel_leave_fixture_untouched(isolated,monkeypatch):
    root=cache_fixture(isolated,monkeypatch,3);cfg=load();cfg['cleanup_index_limit_mb']=0;save(cfg)
    with pytest.raises(RuntimeError,match='hạn mức'):cleanup.plan(['test-cache'],compact=True)
    assert len(list(root.iterdir()))==3 and not list((home()/'data/tmp/cleanup-index').glob('*.sqlite'))
    stop=threading.Event();stop.set()
    with pytest.raises(ProcessCancelled):cleanup.plan(['test-cache'],stop=stop)


@pytest.mark.parametrize('part',['.venv','.git','node_modules','DriverStore','WinSxS','Package Cache'])
def test_protected_subtree_not_in_cleanup_plan(isolated,monkeypatch,part):
    root=cache_fixture(isolated,monkeypatch,0);protected=root/part;protected.mkdir();old_file(protected/'old.bin')
    assert cleanup.plan(['test-cache'])['entries']==[]
    assert (protected/'old.bin').exists()


@pytest.mark.parametrize('name',['state.sqlite','state.sqlite-wal','state.db-shm','NTUSER.DAT','state.db'])
def test_active_transaction_file_not_a_cleanup_candidate(isolated,monkeypatch,name):
    root=cache_fixture(isolated,monkeypatch,0);old_file(root/name)
    assert cleanup.plan(['test-cache'])['entries']==[] and (root/name).exists()


def test_project_under_temp_is_preserved_as_a_whole(isolated,monkeypatch):
    root=cache_fixture(isolated,monkeypatch,0);project=root/'build-project';project.mkdir()
    old_file(project/'pyproject.toml');old_file(project/'main.py')
    assert cleanup.plan(['test-cache'])['entries']==[] and (project/'main.py').exists()


@pytest.mark.parametrize('summary,expected,needs',[
    ({'normalization':{'version':2,'state':'limited'}},'v2 • có giới hạn',False),
    ({'canonical_normalization_version':2,'import_complete':True},'Chuẩn ngay khi nhập v2',False),
    ({'canonical_normalization_version':1,'import_complete':True},'Chuẩn ngay khi nhập v1',False),
    ({},'Chuẩn cũ / cần kiểm tra',True),
])
def test_normalized_and_fully_read_are_not_conflated(summary,expected,needs):
    assert normalization(summary)==expected and needs_normalization(summary)==needs
    summary['errors']=23
    assert coverage(summary)=='23 vùng/lỗi đọc'


def test_manual_checks_are_bounded_readonly_and_keep_reference(isolated):
    root=isolated/'manual';root.mkdir();ref=root/'reference';ref.mkdir()
    (ref/'a.bin').write_bytes(b'abc');(root/'b.bin').write_bytes(b'abc');(root/'unique.bin').write_bytes(b'12345');(root/'empty').mkdir()
    result=inspect_folder(root,'duplicates',min_bytes=0,reference=[str(ref)])
    assert len(result['rows'])==2 and sum(row['reference'] for row in result['rows'])==1
    assert all(Path(row['path']).exists() for row in result['rows']) and result['read_only']
    large=inspect_folder(root,'large',min_bytes=0,limit=1)
    assert large['rows'][0]['size']==5
    assert inspect_folder(root,'empty')['rows'][0]['path']==str(root/'empty')
    assert inspect_folder(root,'duplicates',min_bytes=0,hash_budget=1)['complete'] is False
    assert inspect_folder(root,max_files=1,min_bytes=0)['complete'] is False
    with pytest.raises(ValueError):inspect_folder(Path(root.anchor))
    stop=threading.Event();stop.set()
    with pytest.raises(ProcessCancelled):inspect_folder(root,stop=stop)


def test_manual_hardlinks_are_not_counted_as_reclaimable_duplicates(isolated):
    root=isolated/'links';root.mkdir();(root/'a').write_bytes(b'abcd')
    try:os.link(root/'a',root/'b')
    except OSError:pytest.skip('Hardlink unavailable')
    assert inspect_folder(root,'duplicates',min_bytes=0)['rows']==[]


def test_log_events_multiline_scoped_filter_and_redaction():
    from dgm.log_reader import events,redact
    rows=events('2026 INFO SCAN 4 begin\n2026 ERROR COMPARE broken\nTraceback last line\n')
    assert len(rows)==2 and rows[0]['operation']=='Quét • 4' and rows[1]['detail'].endswith('Traceback last line')
    hidden=redact(r'C:\Users\PrivateName\cache token=secret123 password="abc" Authorization: Bearer token-value')
    assert 'PrivateName' not in hidden and 'secret123' not in hidden and 'token-value' not in hidden and '"abc"' not in hidden
    assert 'Private Name' not in redact(r'C:\Users\Private Name\cache')


def test_footprint_tracks_self_excluded_updates_and_does_not_double_count(isolated):
    from dgm.storage_metrics import app_footprint,begin,finish
    backup=home()/'data/updates/fixture/backup';backup.mkdir(parents=True);(backup/'history.sqlite').write_bytes(b'x'*31)
    meter=begin('scan');(backup/'history.sqlite').write_bytes(b'x'*37);report=finish(meter)
    assert report['app_footprint']['delta_bytes']==6
    footprint=app_footprint()
    assert footprint['groups']['update_backups']==37 and footprint['total_bytes']==sum(footprint['groups'].values())
    assert footprint['self_excluded']


def test_cli_pages_share_same_plan_and_manual_inspector(isolated,monkeypatch):
    from dgm.cli import app
    root=cache_fixture(isolated,monkeypatch,25);p=cleanup.plan(['test-cache'],compact=True)
    out=CliRunner().invoke(app,['cleanup','page',p['id'],'--limit','20','--sort','size','--direction','asc'])
    assert out.exit_code==0,out.output
    assert [row['size'] for row in json.loads(out.output)['entries']]==list(range(1,21))
    protected=CliRunner().invoke(app,['cleanup','protected']);assert protected.exit_code==0 and 'DriverStore' in protected.output
    inspected=CliRunner().invoke(app,['cleanup','inspect-folder',str(root),'--minimum-mb','0'])
    assert inspected.exit_code==0 and json.loads(inspected.output)['read_only']


def test_cleanup_gui_loads_twenty_unchecked_and_keeps_selection_between_pages(gui,isolated,monkeypatch):
    app,w=gui;root=cache_fixture(isolated,monkeypatch,120);p=cleanup.plan(['test-cache'],compact=True)
    panel=w.cleanup_panel;panel.loaded(p)
    assert panel.table.rowCount()==20 and not panel.selected and not panel.apply_button.isEnabled()
    panel.table.item(0,0).setCheckState(Qt.CheckState.Checked);selected=set(panel.selected)
    panel.move_page(1);assert panel.selected==selected and panel.table.rowCount()==20
    panel.move_page(-1);assert panel.table.item(0,0).checkState()==Qt.CheckState.Checked
    panel.sort_column(2);assert panel.rows[0]['size']==1 and panel.selected==selected
    panel.page_size.setCurrentText('100');assert panel.table.rowCount()==100 and len(list(root.iterdir()))==120


def test_cleanup_gui_wrong_confirmation_does_not_narrow_or_delete(gui,isolated,monkeypatch):
    app,w=gui;root=cache_fixture(isolated,monkeypatch,2);p=cleanup.plan(['test-cache'],compact=True)
    panel=w.cleanup_panel;panel.loaded(p);panel.table.item(0,0).setCheckState(Qt.CheckState.Checked)
    monkeypatch.setattr(QInputDialog,'getText',lambda *a:('wrong',True));panel.apply()
    assert cleanup.get_plan(p['id'],compact=True)['entry_count']==2 and len(list(root.iterdir()))==2


def test_tool_only_plan_and_selection_are_unknown_not_zero_bytes(gui,isolated,monkeypatch):
    app,w=gui;known=cleanup.rules();known['nuget-cache']['roots']=[str(isolated/'nuget-fixture')]
    monkeypatch.setattr(cleanup,'rules',lambda:known)
    p=cleanup.plan(['nuget-cache'],compact=True);panel=w.cleanup_panel;panel.loaded(p)
    assert panel.cards[0].value.text()=='Chưa ước lượng'
    assert panel.table.item(0,2).text()=='Chưa ước lượng'
    panel.table.item(0,0).setCheckState(Qt.CheckState.Checked)
    assert panel.cards[3].value.text()=='Chưa ước lượng'


def test_system_sections_schedule_editor_and_log_counts(gui,isolated):
    app,w=gui
    assert [w.system_tabs.tabText(i) for i in range(w.system_tabs.count())]==['Cập nhật app','Công cụ quét','Dung lượng app','Chẩn đoán Windows']
    w.tabs.setCurrentIndex(3);w.mode.setCurrentIndex(w.mode.findData('daily'));app.processEvents()
    assert all(box.isHidden() for box in w.weekday_boxes) and w.interval.isHidden()
    w.mode.setCurrentIndex(w.mode.findData('weekly'));app.processEvents()
    assert all(not box.isHidden() for box in w.weekday_boxes)
    w.schedule_name.setText('Tên lịch thử');w.times.setText('08:00, 14:00,20:00');w.add_schedule()
    assert w.schedule_rows[0]['name']=='Tên lịch thử' and '08:00' in w.schedule_table.item(0,2).text()
    path=home()/'logs/app.log';path.write_text('2026 INFO SCAN 4 begin\n2026 ERROR COMPARE broken\nTraceback\n',encoding='utf-8')
    w.log_panel.refresh(True);assert w.log_panel.table.rowCount()==2
    w.log_panel.search.setText('broken');assert w.log_panel.table.rowCount()==1
    assert w.log_panel.cards[0].value.text()=='1'


def test_renaming_schedule_does_not_skip_an_already_due_run(isolated):
    from dgm.schedule import add,update,listing
    cfg=load();cfg['profiles']['daily']['engine']='native';save(cfg)
    spec=add({'name':'Old name','profile':'daily','mode':'daily','times':['08:00']})
    s=Store();due='2026-01-01T01:00:00+00:00'
    s.db.execute('INSERT INTO jobs VALUES(?,?,?,?)',(spec['id'],None,due,'{}'));s.db.commit();s.close()
    update(spec['id'],{'name':'New name'})
    assert listing()[0]['next_run']==due and listing()[0]['name']=='New name'
