"""Install/test/upgrade/uninstall only in a fresh app-owned D/workspace fixture.

No C scan, system cleanup, UAC, service/task mutation or recursive directory delete.
Portable installer mode avoids Start-menu/registry integration during this test.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

root=Path(__file__).resolve().parent.parent
installer=root/'dist/installer.exe'
base=Path(tempfile.mkdtemp(prefix='installer-smoke-',dir=root/'data')).resolve()
assert base.is_relative_to((root/'data').resolve())
target=base/'Installed App';fixture=base/'scan-fixture';fixture.mkdir()
env=os.environ.copy()
for name in ('DGM_HOME','PYTHONPATH','PYTHONHOME'):env.pop(name,None)
env.update(TEMP=str(base),TMP=str(base),PYTHONUTF8='1',QT_QPA_PLATFORM='offscreen',PYTHONNOUSERSITE='1')
report={'version':'1.0.0','checks':[],'fixture_only':True}

def execute(argv,expected=0):
    result=subprocess.run([str(x) for x in argv],env=env,cwd=base,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=120,
                          creationflags=0x08000000 if os.name=='nt' else 0)
    if result.returncode!=expected:raise RuntimeError(f'{Path(argv[0]).name} exit={result.returncode}: {result.stdout[-2000:]} {result.stderr[-2000:]}')
    return result.stdout

def install():
    execute([installer,'/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/PORTABLE=1',f'/DIR={target}',f'/LOG={base / "installer.log"}'])

install()
assert (target/'DiskGrowthMonitor.exe').is_file() and (target/'dgm.exe').is_file()
assert not (target/'data/config.json').exists() and not (target/'data/history.sqlite').exists()
assert not list(target.glob('**/WizTree*.exe')) and not list(target.glob('**/WinDirStat.exe'))
assert (target/'.venv/licenses/Qt/LGPL-3.0-only.txt').is_file()
report['checks'].append('fresh install contains no personal config/history/tools')
exe=target/'dgm.exe'
def cli(*args):return json.loads(execute([exe,*map(str,args)]))
doctor=cli('doctor')
assert doctor['app_version']=='1.0.0' and doctor['frozen'] and doctor['isolated'] and all(v!='missing' for v in doctor['packages'].values())
report['checks'].append('frozen CLI, private runtime and package metadata')
(fixture/'large.bin').write_bytes(b'a'*11_000_001)
(fixture/'small.txt').write_text('fixture',encoding='utf-8')
cli('profile','set','daily','--root',fixture,'--engine','native')
first=cli('scan');assert first['status']=='complete' and first['files']==2
(fixture/'large.bin').write_bytes(b'a'*13_000_001)
second=cli('scan');assert second['status']=='complete' and second['files']==2
comparison=cli('compare',second['scan_id'],'--older',first['scan_id'],'--limit','20')
assert any(row.get('delta')==2_000_000 for row in comparison['rows'])
report['checks'].append('two native fixture scans and +2000000-byte comparison')
gui=cli('gui','--smoke-test')
assert '1.0.0' in gui['title'] and gui['tabs']==6 and gui['image_saved']
execute([target/'DiskGrowthMonitor.exe','gui','--smoke-test'])
report['checks'].append('console and windowed GUI bootloaders render and close normally')
config=target/'data/config.json';history=target/'data/history.sqlite'
config_hash=hashlib.sha256(config.read_bytes()).hexdigest()
scan_ids=[item['id'] for item in cli('history')]
install()
assert hashlib.sha256(config.read_bytes()).hexdigest()==config_hash
assert [item['id'] for item in cli('history')]==scan_ids
report['checks'].append('upgrade preserves config and both snapshots')
assert cli('uninstall-check')['safe_to_uninstall']
execute([target/'unins000.exe','/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART',f'/LOG={base / "uninstaller.log"}'])
assert config.is_file() and history.is_file() and not exe.exists() and not (target/'DiskGrowthMonitor.exe').exists()
report['checks'].append('uninstall removes app binaries and preserves data/logs')
output=root/'dist/release-test-results.json'
output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False,indent=2))
print(f'Fixture/logs retained at {base}')
