"""Every mutating test is confined to a disposable pytest fixture."""
import os
from pathlib import Path
import sys
import threading

import pytest

from dgm import app_move as move, app_move_paths as paths


@pytest.fixture
def layout(isolated,monkeypatch):
    old = isolated/'source-volume'
    dest = isolated/'destination-volume'
    old.mkdir();dest.mkdir()
    source = old/'Example data'
    source.mkdir()
    (source/'empty').mkdir()
    (source/'settings.json').write_text('{"profile":"sample"}',encoding='utf-8')
    (source/'model.bin').write_bytes(b'test model' * 1024)
    # Exercise the actual Windows junction with two temp roots. Cross-volume
    # validation is covered separately, plus a manual E: → F: fixture smoke.
    monkeypatch.setattr(move,'local_volume',lambda p:'new' if paths.inside(p,dest) else 'old')
    return source,dest


def test_preview_does_not_write_and_validates_paths(layout):
    source,dest = layout
    preview = move.plan([source],dest)
    assert preview['entries'][0]['target']==str(dest/source.name)
    assert preview['entries'][0]['files']==2 and preview['bytes']>0
    assert not list(dest.iterdir()) and not move.history()
    with pytest.raises(ValueError,match='cha hoặc'):
        move.plan([source,source/'empty'],dest)
    with pytest.raises(ValueError,match='hai phân vùng'):
        move.plan([source],source.parent)
    (dest/source.name).mkdir()
    with pytest.raises(ValueError,match='ghi đè'):
        move.plan([source],dest)


@pytest.mark.skipif(sys.platform!='win32',reason='Windows junction/ACL')
def test_copy_verified_and_undo_preserves_target(layout):
    source,dest = layout
    preview = move.plan([source],dest)
    record = move.execute(preview)
    row = record['entries'][0]
    backup,target = Path(row['backup']),Path(row['target'])
    assert record['id']==preview['id'] and record['state']=='complete'
    assert source.is_junction() and source.resolve()==target
    assert (source/'model.bin').read_bytes()==(backup/'model.bin').read_bytes()
    assert (target/'empty').is_dir()
    assert move.history()[0]['id']==record['id']
    (target/'new.bin').write_bytes(b'keep')
    with pytest.raises(ValueError,match='thay đổi'):
        move.undo(record['id'])
    assert source.is_junction() and (target/'new.bin').is_file()
    (target/'new.bin').unlink()
    assert move.undo(record['id'])['state']=='undone'
    assert not source.is_junction() and not backup.exists()
    assert (source/'settings.json').read_text()=='{"profile":"sample"}'
    assert target.is_dir()


@pytest.mark.skipif(sys.platform!='win32',reason='Windows junction/ACL')
def test_retire_blocks_changed_file_and_keeps_additional_target_file(layout):
    source,dest = layout
    record = move.execute(move.plan([source],dest))
    row = record['entries'][0]
    backup,target = Path(row['backup']),Path(row['target'])
    (target/'new.bin').write_bytes(b'new')
    (target/'settings.json').write_bytes(b'different')
    with pytest.raises(ValueError,match='đổi ở đích'):
        move.retire(record['id'],permanent=True)
    assert backup.exists()
    (target/'settings.json').write_bytes((backup/'settings.json').read_bytes())
    assert move.retire(record['id'],permanent=True)['state']=='retired'
    assert not backup.exists() and source.is_junction()
    assert (source/'new.bin').read_bytes()==b'new'


def test_cancel_after_copy_but_before_switch_preserves_source(layout,monkeypatch):
    source,dest = layout
    preview = move.plan([source],dest)
    event = threading.Event()
    original = move._snapshot
    def snapshot(root,**kwargs):
        result = original(root,**kwargs)
        if kwargs.get('copy_to') is not None:
            event.set()
        return result
    monkeypatch.setattr(move,'_snapshot',snapshot)
    with pytest.raises(paths.ProcessCancelled):
        move.execute(preview,stop=event)
    assert (source/'settings.json').is_file() and not source.is_symlink()
    assert move.history()[0]['state']=='interrupted'


@pytest.mark.skipif(sys.platform!='win32',reason='Windows junction/ACL')
def test_switch_failure_restores_original(layout,monkeypatch):
    source,dest = layout
    second = source.with_name('Second data')
    second.mkdir();(second/'test').write_bytes(b'keep')
    preview = move.plan([source,second],dest)
    original = move._junction
    def junction(path,target):
        if path==second:
            raise RuntimeError('synthetic junction failure')
        return original(path,target)
    monkeypatch.setattr(move,'_junction',junction)
    with pytest.raises(RuntimeError,match='synthetic'):
        move.execute(preview)
    assert not source.is_junction() and (source/'model.bin').is_file()
    assert (second/'test').is_file() and not second.is_junction()
    assert move.history()[0]['state']=='interrupted'


def test_portable_requires_explicit_confirmation_and_matching_exe(layout,monkeypatch):
    source,dest = layout
    exe = source/'Example.exe'
    exe.write_bytes(b'not executable, never launched')
    monkeypatch.setattr(move,'identify',lambda _:dict(exe=str(exe),selection=str(exe),kind='unknown'))
    with pytest.raises(ValueError,match='xác nhận riêng'):
        move.plan([source],dest,selection=exe)
    assert move.plan([source],dest,selection=exe,portable=True)['entries'][0]['role']=='portable'
    (source/'unins000.exe').write_bytes(b'not executable')
    with pytest.raises(ValueError,match='gỡ cài'):
        move.plan([source],dest,selection=exe,portable=True)


def test_browser_exclusion_does_not_descend(layout,monkeypatch):
    source,dest = layout
    browser = source/'Chrome'
    browser.mkdir()
    original = os.scandir
    def scandir(path):
        assert Path(path)!=browser
        return original(path)
    monkeypatch.setattr(os,'scandir',scandir)
    with pytest.raises(ValueError,match='Chrome/Edge'):
        move.plan([source],dest)


@pytest.mark.skipif(sys.platform!='win32',reason='Windows shell links')
def test_real_shortcut_discovery_without_launch(layout,monkeypatch):
    import subprocess
    from dgm.platform_ops import system_tool
    source,dest = layout
    exe = source/'H cut.exe'
    exe.write_bytes(b'not executable, never launched')
    shortcut = source.parent/'Example.lnk'
    roaming = source.parent/'roaming'
    data = roaming/'H cut'
    data.mkdir(parents=True)
    (data/'model.bin').write_bytes(b'data')
    monkeypatch.setenv('APPDATA',str(roaming))
    subprocess.run([system_tool(r'WindowsPowerShell\v1.0\powershell.exe'),'-NoProfile','-NonInteractive',
        '-Command',"$s=New-Object -ComObject WScript.Shell; $l=$s.CreateShortcut($env:QA_LINK); "
                   "$l.TargetPath=$env:QA_EXE; $l.Arguments=$env:QA_ARGS; $l.Save()"],
        env=dict(os.environ,QA_LINK=str(shortcut),QA_EXE=str(exe),QA_ARGS=f'--user-data-dir="{data}"'),
        check=True,capture_output=True,creationflags=subprocess.CREATE_NO_WINDOW)
    report = paths.discover(shortcut)
    assert report['app']['exe']==str(exe) and report['app']['has_shortcut_arguments']
    assert 'args' not in report['app']
    assert any(row['path']==str(data) and row['confidence']=='known' and row['bytes']==4 for row in report['rows'])
    assert any(row['role']=='portable' for row in report['rows'])


def test_cli_manual_preview_matches_backend(layout):
    from typer.testing import CliRunner
    from dgm.cli import app
    source,dest = layout
    result = CliRunner().invoke(app,['app-move','preview',str(dest),'--folder',str(source)])
    assert result.exit_code==0,result.output or result.exception
    import json
    assert json.loads(result.output)['entries'][0]['target']==str(dest/source.name)
    assert not list(dest.iterdir())


def test_gui_does_not_select_or_execute_without_approval(gui_factory,tmp_path,monkeypatch):
    from PySide6.QtCore import Qt
    from dgm.app_move_ui import QInputDialog
    from test_milestone_startup_ui import settle
    app,open_window,errors = gui_factory
    source = tmp_path/'QA source'
    dest = tmp_path/'QA destination'
    source.mkdir();dest.mkdir()
    (source/'file.dat').write_bytes(b'demo')
    monkeypatch.setattr(move,'local_volume',lambda p:'new' if paths.inside(p,dest) else 'old')
    monkeypatch.setattr(QInputDialog,'getText',lambda *_a,**_kw:(str(source),True))
    window = open_window()
    ui = window.app_move_panel
    window.tabs.setCurrentIndex(window.tabs.indexOf(ui.parentWidget()))
    ui.add_folder()
    settle(app,lambda:not window.workers)
    assert ui.table.rowCount()==1 and not ui.apply_button.isEnabled()
    assert ui.table.item(0,0).checkState()==Qt.CheckState.Unchecked
    ui.destination.setText(str(dest))
    ui.table.item(0,0).setCheckState(Qt.CheckState.Checked)
    ui.prepare();settle(app,lambda:not window.workers)
    assert ui.apply_button.isEnabled() and str(dest/source.name) in ui.table.item(0,3).text()
    monkeypatch.setattr(ui,'ask',lambda *_a,**_kw:False)
    monkeypatch.setattr(move,'execute',lambda *_a,**_kw:pytest.fail('Must not move without approval'))
    ui.apply()
    assert not list(dest.iterdir()) and not errors


from test_milestone_startup_ui import gui_factory
