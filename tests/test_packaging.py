import json
import sys
from pathlib import Path
from typer.testing import CliRunner
from dgm import runtime
from dgm.config import home
from dgm.cli import app


def test_source_and_frozen_background_command(isolated,monkeypatch):
    monkeypatch.setattr(sys,'frozen',False,raising=False)
    exe,args=runtime.background_command('schedule','tick')
    assert exe==home()/'.venv/Scripts/pythonw.exe' and args[:2]==['-m','dgm']
    monkeypatch.setattr(sys,'frozen',True)
    exe,args=runtime.background_command('schedule','agent')
    assert exe==home()/'DiskGrowthMonitor.exe'
    assert args==['--home',str(home()),'schedule','agent'] and '-m' not in args


def test_frozen_task_uses_own_exe_without_registering(isolated,monkeypatch):
    from dgm import schedule,windows_service,platform_ops
    monkeypatch.setattr(sys,'frozen',True,raising=False)
    monkeypatch.setattr(windows_service,'status',lambda:{'installed':False})
    monkeypatch.setattr(platform_ops,'is_admin',lambda:True)
    calls=[]
    monkeypatch.setattr(schedule,'run',lambda argv:calls.append(argv) or type('Result',(),{'returncode':0,'stdout':'fixture'})())
    schedule.install_task()
    line=calls[0][calls[0].index('/TR')+1]
    assert 'DiskGrowthMonitor.exe' in line and 'schedule tick' in line and 'python' not in line and '-m dgm' not in line


def test_frozen_service_xml_uses_own_exe_no_download(isolated,monkeypatch):
    from dgm import windows_service
    from xml.etree import ElementTree as ET
    monkeypatch.setattr(sys,'frozen',True,raising=False)
    path=windows_service.service_path();path.parent.mkdir(parents=True);path.write_bytes(b'fixture not executable')
    windows_service.prepare()
    tree=ET.parse(path.with_suffix('.xml'))
    assert tree.findtext('executable')==str(home()/'DiskGrowthMonitor.exe')
    assert tree.findtext('arguments').endswith('schedule agent') and '-m dgm' not in tree.findtext('arguments')


def test_uninstall_guard_blocks_registered_background_and_allows_idle(isolated,monkeypatch):
    import dgm.background as background
    import psutil
    monkeypatch.setattr(psutil,'process_iter',lambda *a:[])
    state={'scan':{'running':False},'task':{'installed':False},'service':{'installed':False}}
    monkeypatch.setattr(background,'status',lambda:state)
    result=CliRunner().invoke(app,['uninstall-check'])
    assert result.exit_code==0 and json.loads(result.output)['safe_to_uninstall']
    state['task']['installed']=True
    result=CliRunner().invoke(app,['uninstall-check'])
    assert result.exit_code==2 and not json.loads(result.output)['safe_to_uninstall']


def test_installer_source_never_recursively_deletes_data_or_bundles_private_data():
    text=(Path(__file__).resolve().parents[1]/'packaging/installer.iss').read_text(encoding='utf-8')
    assert '[UninstallDelete]' not in text and 'PrivilegesRequired=lowest' in text
    assert 'dist\\DiskGrowthMonitor\\*"' not in text
    assert 'CloseApplications=no' in text and 'CreateUninstallRegKey=not PortableMode' in text


def test_package_runtime_license_texts_and_no_foreign_icu():
    root=Path(__file__).resolve().parents[1]
    assert 'GNU LESSER GENERAL PUBLIC LICENSE' in (root/'packaging/qt-licenses/LGPL-3.0-only.txt').read_text(encoding='utf-8')
    spec=(root/'packaging/DiskGrowthMonitor.spec').read_text(encoding='utf-8')
    assert "'icuuc.dll','icudt78.dll'" in spec


def test_uninstall_guard_does_not_remove_or_block_another_installation_task(isolated,monkeypatch):
    import dgm.background as background
    import psutil
    monkeypatch.setattr(psutil,'process_iter',lambda *a:[])
    state={'scan':{'running':False},'task':{'installed':True,'actions':[{'path':'D:\\AnotherCopy\\DiskGrowthMonitor.exe','arguments':'schedule tick'}]},'service':{'installed':False}}
    monkeypatch.setattr(background,'status',lambda:state)
    assert CliRunner().invoke(app,['uninstall-check']).exit_code==0
    state['task']['actions'][0]['path']=str(home()/'DiskGrowthMonitor.exe')
    assert CliRunner().invoke(app,['uninstall-check']).exit_code==2


def test_service_ownership_with_spaces_and_quotes(isolated):
    from dgm.installation import owns_background
    assert owns_background('service',{'installed':True,'binary_path':'"'+str(home()/'.venv/tools/service/DiskGrowthMonitorService.exe')+'"'})
    assert not owns_background('service',{'installed':True,'binary_path':'"D:\\Other App\\.venv\\tools\\service\\DiskGrowthMonitorService.exe"'})
