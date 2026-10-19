"""Elevated 2.x installer QA. All installs/scans/deletes are owned D: fixtures."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import uuid
from dgm import __version__

parser=argparse.ArgumentParser();parser.add_argument('installer',type=Path);parser.add_argument('--legacy',type=Path)
args=parser.parse_args();project=Path(__file__).resolve().parents[1];installer=args.installer.resolve()
base=Path(tempfile.mkdtemp(prefix='parquet-200-smoke-',dir=project/'data')).resolve()
assert base.is_relative_to((project/'data').resolve())
target=base/'Installed App';fixture=base/'files';fixture.mkdir()
environment=os.environ.copy()
for name in ('DGM_HOME','DGM_SOURCE','PYTHONPATH','PYTHONHOME'):environment.pop(name,None)
environment.update(TEMP=str(base),TMP=str(base),PYTHONUTF8='1',PYTHONNOUSERSITE='1',QT_QPA_PLATFORM='offscreen',
                   LOCALAPPDATA=str(base/'local'),APPDATA=str(base/'roaming'))
def digest(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
report={'version':__version__,'installer_sha256':digest(installer),'fixture_only':True,'fixture_dir':str(base),'checks':[]}
update_report={**report,'checks':[]}

def execute(argv,expected=0,timeout=180):
    done=subprocess.run(list(map(str,argv)),cwd=base,env=environment,capture_output=True,encoding='utf-8',errors='replace',timeout=timeout,creationflags=0x08000000)
    if done.returncode!=expected:raise RuntimeError(f'{Path(argv[0]).name}: {done.returncode}\n{done.stdout[-4000:]}\n{done.stderr[-4000:]}')
    return done.stdout

def cli(*items,exe=None):return json.loads(execute([exe or target/'dgm.exe',*items]))
def install(exe,path):execute([exe,'/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/PORTABLE=1',f'/DIR={path}',f'/LOG={base / (path.name+"-install.log")}'])
def history_hashes(root):
    return {str(p.relative_to(root)):digest(p) for folder in (root/'data/columnar',root/'data/history')
            for p in folder.rglob('*') if p.is_file() and 'work' not in p.relative_to(folder).parts}

install(installer,target)
assert (target/'DiskGrowthMonitor.exe').is_file() and not (target/'data/config.json').exists()
assert not list(target.rglob('WizTree*.exe')) and (target/'.venv/licenses/duckdb').is_dir() and (target/'.venv/licenses/numpy').is_dir()
import win32api
module=win32api.LoadLibraryEx(str(target/'DiskGrowthMonitor.exe'),0,2)
try:manifest=win32api.LoadResource(module,24,1).decode('utf-8')
finally:win32api.FreeLibrary(module)
assert 'requireAdministrator' in manifest
report['checks'].append('clean install, separate runtime/licences incl. DuckDB/NumPy, GUI EXE requires UAC; no personal data or third-party scan tools')
doctor=cli('doctor');assert doctor['app_version']==__version__ and doctor['edition']=='Parquet + DuckDB'
assert doctor['frozen'] and doctor['isolated'] and doctor['history_format']=='parquet' and all(v!='missing' for v in doctor['packages'].values())
report['checks'].append('frozen CLI/version/edition and private runtime imports incl. DuckDB and NumPy')
(fixture/'large.bin').write_bytes(b'a'*11_000_001)
branch=fixture/'paging';branch.mkdir()
for index in range(1200):(branch/f'f{index:04}.bin').write_bytes(b'x'*(index%97+1))
cli('profile','set','daily','--root',fixture,'--engine','native')
first=cli('scan');assert first['status']=='complete' and first['files']==1201 and first['timing']['state']=='complete'
(fixture/'large.bin').write_bytes(b'a'*13_000_001)
second=cli('scan');assert second['files']==1201
common=('compare',str(second['scan_id']),'--older',str(first['scan_id']),'--minimum-mb','0','--kind','file','--measure','logical','--sort','new','--direction','desc')
page=cli(*common,'--limit','20');more=cli(*common,'--limit','100','--offset','20');last=cli(*common,'--limit','1000','--offset','120')
rows=page['rows']+more['rows']+last['rows']
assert len(rows)==1120 and len({r['path'] for r in rows})==1120 and [r['after'] for r in rows]==sorted((r['after'] for r in rows),reverse=True)
assert any(r['logical_delta']==2_000_000 for r in rows)
assert page['summary']['partition_valid'] and page['summary']['timing']['state']=='complete'
folder=cli(*common,'--root',branch,'--limit','1000');assert folder['total']==1200
assert cli('snapshot-audit','--id',str(second['scan_id']))[0]['checksum_verified']
assert cli('storage','parquet')['persistent_database_bytes']==0
assert list((target/'data/columnar/snapshots').glob('*.parquet'))
report['checks'].append('two native D: fixture scans, phase timing journals, exact logical +2000000 bytes, global 20+100+1000 offset paging, folder scope, checksum and partition checks; no persistent record database')
cli('profile','set','daily','--root',fixture,'--engine','native','--exclude',branch)
third=cli('scan');private=cli('compare',str(third['scan_id']),'--older',str(second['scan_id']),'--section','private','--kind','file','--minimum-mb','0')
assert private['total']==1200 and all(r['change']=='not_scanned' and r['delta'] is None for r in private['rows'])
report['checks'].append('excluded subtree stays private/not-scanned, never a fabricated deletion')
for executable in (target/'dgm.exe',target/'DiskGrowthMonitor.exe'):
    raw=execute([executable,'gui','--smoke-test'])
    if executable.name=='dgm.exe':
        gui=json.loads(raw);assert __version__ in gui['title'] and 'Parquet + DuckDB' in gui['title'] and gui['tabs']==6 and gui['rows']==0 and gui['image_saved']
assert not any((target/'data/columnar/work').iterdir())
report['checks'].append('both frozen Qt entry points render/close the original six-tab GUI, metadata-only startup, spill work released')

# Resume a durable journal with an already-complete snapshot: no new live scan.
jobs=cli('tasks','list','--all');completed=next(r for r in jobs if r['snapshots']==[first['scan_id']])
resumed=cli('tasks','resume',completed['id']);assert resumed[0]['id']==first['scan_id']
report['checks'].append('frozen JSON checkpoint can reopen an already-complete snapshot without rescanning')

before=history_hashes(target);cfg_hash=digest(target/'data/config.json')
install(installer,target)
assert history_hashes(target)==before and digest(target/'data/config.json')==cfg_hash
report['checks'].append('reinstall/upgrade preserves Parquet, journal, comparison summary, configuration and legacy metadata hashes')

def coordinate(root,bootstrap,conversion=False,sha=None):
    folder=root/'data/updates'/('session-'+uuid.uuid4().hex);folder.mkdir(parents=True)
    cached=root/'data/updates/fixture-download/installer.exe';cached.parent.mkdir(exist_ok=True);shutil.copy2(installer,cached)
    destination=root/'.dgm-app' if conversion else root
    session={'id':folder.name,'root':str(root),'install_root':str(destination),'source_conversion':conversion,
        'bootstrap':bootstrap,'release':{'version':__version__,'size':installer.stat().st_size,'sha256':sha or digest(installer)},
        'installer':str(cached),'parent':{'pid':2147483647,'created':0},'original':{},'changed':[],
        'portable':True,'phase':'waiting','restore_pending':False}
    path=folder/'session.json';path.write_text(json.dumps(session),encoding='utf-8')
    helper=folder/'update-helper.ps1';source=(project/'app/dgm/update_helper.ps1') if conversion else (root/'.venv/dgm/update_helper.ps1')
    shutil.copy2(source,helper)
    execute([Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',helper,'-Session',path,'-Quiet'],expected=1 if sha else 0,timeout=300)
    outcome=json.loads(path.read_text(encoding='utf-8'))
    assert outcome['phase']==('failed' if sha else 'complete') and not outcome['restore_pending']
    return outcome,folder,destination

outcome,session_folder,_=coordinate(target,{'executable':str(target/'dgm.exe'),'arguments':[]})
assert history_hashes(target)==before and digest(target/'data/config.json')==cfg_hash
assert outcome.get('preserved_columnar') and not list((session_folder/'backup').glob('*.parquet'))
update_report['checks'].append('real detached updater replaces same runtime, verifies persistent Parquet/journal hashes without copying snapshots, preserves config and portable mode; no production runners touched')
coordinate(target,{'executable':str(target/'dgm.exe'),'arguments':[]},sha='0'*64)
assert history_hashes(target)==before
update_report['checks'].append('bad installer checksum refused; installed app and all saved metadata remain intact')

if args.legacy:
    legacy=base/'Legacy App';install(args.legacy.resolve(),legacy);exe=legacy/'dgm.exe'
    oldversion=cli('doctor',exe=exe)['app_version']
    cli('profile','set','daily','--root',fixture,'--engine','native',exe=exe)
    old1=cli('scan',exe=exe);old2=cli('scan',exe=exe)
    legacy_before=history_hashes(legacy);legacy_cfg=digest(legacy/'data/config.json')
    coordinate(legacy,{'executable':str(exe),'arguments':[]})
    assert cli('doctor',exe=exe)['app_version']==__version__
    assert history_hashes(legacy)==legacy_before and digest(legacy/'data/config.json')==legacy_cfg
    assert len(cli('history',exe=exe))==0 # Old source is retained, not silently migrated.
    imported=cli('storage','migrate-columnar','--all','--yes',exe=exe)
    newhistory=cli('history',exe=exe);assert len(newhistory)==2
    p=cli('compare',str(newhistory[0]['id']),'--older',str(newhistory[1]['id']),'--minimum-mb','0',exe=exe)
    assert p['summary']['partition_valid'] and p['summary']['verified_common_disk_delta']==0
    assert history_hashes(legacy).items()>=legacy_before.items()
    update_report['checks'].append(f'real {oldversion} installer/history upgrade, no source deletion; explicit one-time JSONL -> Parquet import compares correctly')
    execute([legacy/'unins000.exe','/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART']);assert (legacy/'data/columnar/catalog.json').exists()

assert cli('uninstall-check')['safe_to_uninstall']
execute([target/'unins000.exe','/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART'])
assert history_hashes(target)==before and (target/'data/config.json').is_file() and not (target/'dgm.exe').exists()
report['checks'].append('uninstall removes only installed payload; Parquet, config, legacy logs and journals are retained')
for filename,value in (('release-test-results.json',report),('inplace-update-test-results.json',update_report)):
    (installer.parent/filename).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'fresh_checks':len(report['checks']),'update_checks':len(update_report['checks']),'fixture':str(base)},indent=2))
