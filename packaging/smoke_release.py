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
import sys
import shutil
import uuid
import psutil
from dgm import __version__

root=Path(__file__).resolve().parent.parent
installer=Path(sys.argv[1]).resolve() if len(sys.argv)>1 else root/'dist/installer.exe'
base=Path(tempfile.mkdtemp(prefix='installer-smoke-',dir=root/'data')).resolve()
assert base.is_relative_to((root/'data').resolve())
target=base/'Installed App';fixture=base/'scan-fixture';fixture.mkdir()
env=os.environ.copy()
for name in ('DGM_HOME','PYTHONPATH','PYTHONHOME'):env.pop(name,None)
env.update(TEMP=str(base),TMP=str(base),PYTHONUTF8='1',QT_QPA_PLATFORM='offscreen',PYTHONNOUSERSITE='1')
report={'version':__version__,'installer_sha256':hashlib.sha256(installer.read_bytes()).hexdigest(),'checks':[],'fixture_only':True}

def execute(argv,expected=0):
    result=subprocess.run([str(x) for x in argv],env=env,cwd=base,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=120,
                          creationflags=0x08000000 if os.name=='nt' else 0)
    if result.returncode!=expected:raise RuntimeError(f'{Path(argv[0]).name} exit={result.returncode}: {result.stdout[-2000:]} {result.stderr[-2000:]}')
    return result.stdout

def install():
    execute([installer,'/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/PORTABLE=1',f'/DIR={target}',f'/LOG={base / "installer.log"}'])

install()
assert (target/'DiskGrowthMonitor.exe').is_file() and (target/'dgm.exe').is_file()
import win32api
module=win32api.LoadLibraryEx(str(target/'DiskGrowthMonitor.exe'),0,2)
try:manifest=win32api.LoadResource(module,24,1).decode('utf-8')
finally:win32api.FreeLibrary(module)
assert 'requireAdministrator' in manifest
report['checks'].append('GUI EXE manifest requires administrator before Python/data access')
assert not (target/'data/config.json').exists() and not (target/'data/history.sqlite').exists()
assert not list(target.glob('**/WizTree*.exe')) and not list(target.glob('**/WinDirStat.exe'))
assert (target/'.venv/licenses/Qt/LGPL-3.0-only.txt').is_file()
report['checks'].append('fresh install contains no personal config/history/tools')
exe=target/'dgm.exe'
def cli(*args):return json.loads(execute([exe,*map(str,args)]))
doctor=cli('doctor')
assert doctor['app_version']==__version__ and doctor['frozen'] and doctor['isolated'] and all(v!='missing' for v in doctor['packages'].values())
report['checks'].append('frozen CLI, private runtime and package metadata')
(fixture/'large.bin').write_bytes(b'a'*11_000_001)
(fixture/'small.txt').write_text('fixture',encoding='utf-8')
branch=fixture/'paging-fixture';branch.mkdir()
for index in range(1200):(branch/f'f{index:04}.bin').write_bytes(b'x'*(index%97+1))
cli('profile','set','daily','--root',fixture,'--engine','native')
first=cli('scan');assert first['status']=='complete' and first['files']==1202
(fixture/'large.bin').write_bytes(b'a'*13_000_001)
second=cli('scan');assert second['status']=='complete' and second['files']==1202
comparison=cli('compare',second['scan_id'],'--older',first['scan_id'],'--limit','20')
assert any(row.get('delta')==2_000_000 for row in comparison['rows'])
audit=cli('snapshot-audit','--id',second['scan_id'])
assert audit[0]['after']['logical']==second['logical'] and not audit[0]['applied']
normalized=cli('snapshot-normalize','--id',second['scan_id'],'--yes')
assert normalized['applied'][0]['source_preserved']
assert list((target/'data/history/normalization').glob('*.jsonl.gz'))
assert not (target/'data/tmp/history-index/history.sqlite').exists()
report['checks'].append('frozen CLI audits and normalizes history without rewriting the source snapshot')
report['checks'].append('two native fixture scans and +2000000-byte comparison')
shared=('compare',second['scan_id'],'--older',first['scan_id'],'--kind','file','--minimum-mb','0','--sort','new','--direction','desc')
page=cli(*shared,'--limit','20')
more=cli(*shared,'--limit','100','--cursor',json.dumps(page['cursor']))
last=cli(*shared,'--limit','1000','--cursor',json.dumps(more['cursor']))
rows=page['rows']+more['rows']+last['rows']
assert len(rows)==1120 and len({r['path_id'] for r in rows})==1120
assert [r['after'] for r in rows]==sorted((r['after'] for r in rows),reverse=True)
thousand=cli(*shared,'--limit','1000','--cursor',json.dumps(page['cursor']))
assert len(page['rows']+thousand['rows'])==1020
folder=cli('compare',second['scan_id'],'--older',first['scan_id'],'--root',branch,'--kind','file','--minimum-mb','0','--sort','path','--direction','asc','--limit','1000')
assert folder['total']==1200 and all(Path(r['path']).parent==branch for r in folder['rows'])
report['checks'].append('frozen CLI global 20+100+1000 paging, stable ties and folder-only name order')
cli('profile','set','daily','--root',fixture,'--engine','native','--exclude',branch)
third=cli('scan');assert third['files']==2
common=cli('compare',third['scan_id'],'--older',second['scan_id'],'--section','common','--minimum-mb','0')
private=cli('compare',third['scan_id'],'--older',second['scan_id'],'--section','private','--kind','file','--minimum-mb','0','--limit','20')
assert common['summary']['old_totals']['logical']==common['summary']['new_totals']['logical']
assert private['total']==1200 and len(private['rows'])==20
assert all(row['delta'] is None and row['change']=='not_scanned' for row in private['rows'])
assert private['summary']['private_old_totals']['logical']>0 and private['summary']['private_new_totals']['logical']==0
report['checks'].append('frozen automatic common/private scope split: 1200 excluded files are not false deletions')
gui=cli('gui','--smoke-test')
assert __version__ in gui['title'] and gui['tabs']==6 and gui['image_saved']
execute([target/'DiskGrowthMonitor.exe','gui','--smoke-test'])
report['checks'].append('console and windowed GUI bootloaders render and close normally')
config=target/'data/config.json';history=target/'data/history/catalog.json'
config_hash=hashlib.sha256(config.read_bytes()).hexdigest()
scan_ids=[item['id'] for item in cli('history')]
install()
assert hashlib.sha256(config.read_bytes()).hexdigest()==config_hash
assert [item['id'] for item in cli('history')]==scan_ids
report['checks'].append('upgrade preserves config and both snapshots')
marker=json.loads((target/'installed-app.json').read_text(encoding='utf-8'))
assert marker['update_protocol']==1 and marker['portable'] is True
assert cli('app-update','status')=={'active':False,'pending_recovery':[]}
# Same-version replacement exercises the REAL frozen coordinator without
# publishing a fake newer Release or touching the existing source Task.
# Strict greater-version gating/network/UAC/runner restoration are unit tested.
update_cache=target/'data/updates/fixture-installer';update_cache.mkdir(parents=True)
cached_installer=update_cache/'installer.exe';shutil.copy2(installer,cached_installer)
def coordinator(expected_hash,expected_exit):
    ident='session-'+uuid.uuid4().hex;folder=target/'data/updates'/ident;folder.mkdir()
    session={'id':ident,'root':str(target),'release':{'version':__version__,'size':installer.stat().st_size,'sha256':expected_hash},
        'installer':str(cached_installer),'parent':{'pid':2147483647,'created':0},'original':{},'changed':[],
        'portable':True,'phase':'waiting','restore_pending':False}
    session_path=folder/'session.json';session_path.write_text(json.dumps(session),encoding='utf-8')
    (target/'data/update.lock').write_text(json.dumps({'owner':{'pid':os.getpid(),'created':psutil.Process().create_time()},'session':ident}),encoding='utf-8')
    helper=folder/'update-helper.ps1';shutil.copy2(target/'.venv/dgm/update_helper.ps1',helper)
    powershell=Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
    execute([powershell,'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',helper,'-Session',session_path,'-Quiet'],expected=expected_exit)
    assert hashlib.sha256(config.read_bytes()).hexdigest()==config_hash
    assert [item['id'] for item in cli('history')]==scan_ids
    assert not (target/'data/update.lock').exists()
    return json.loads(session_path.read_text()),folder
outcome,session_folder=coordinator(hashlib.sha256(installer.read_bytes()).hexdigest(),0)
assert outcome['phase']=='complete' and not outcome['restore_pending']
assert not (session_folder/'backup/history.sqlite').exists() and (session_folder/'backup/config.json').is_file()
assert outcome['preserved_logs'] and not (session_folder/'backup/normalization.sqlite').exists()
assert json.loads((target/'installed-app.json').read_text())['portable'] is True
report['checks'].append('real frozen detached updater: same-version replacement; JSONL metadata logs/hash manifest preserved without database copies; portable mode, no runner mutation')
outcome,_=coordinator('0'*64,1)
assert outcome['phase']=='failed' and not outcome['restore_pending'] and exe.is_file()
report['checks'].append('real updater checksum failure: installer not executed, current app/history retained and lease cleared')
assert cli('uninstall-check')['safe_to_uninstall']
execute([target/'unins000.exe','/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART',f'/LOG={base / "uninstaller.log"}'])
assert config.is_file() and history.is_file() and not exe.exists() and not (target/'DiskGrowthMonitor.exe').exists()
report['checks'].append('uninstall removes app binaries and preserves data/logs')
output=installer.parent/'release-test-results.json'
output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False,indent=2))
print(f'Fixture/logs retained at {base}')
