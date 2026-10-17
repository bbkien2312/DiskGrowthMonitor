"""Exercise source->managed runtime and an existing-install upgrade in owned fixtures."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import uuid

from dgm import __version__,config
from dgm.scanner import scan
from dgm.comparison import Comparisons
from dgm.benchmark import benchmark

project=Path(__file__).resolve().parent.parent
installer=Path(sys.argv[1]).resolve()
legacy_installer=Path(sys.argv[2]).resolve() if len(sys.argv)>2 else None
base=Path(tempfile.mkdtemp(prefix='inplace-update-smoke-',dir=project/'data')).resolve()
assert base.is_relative_to((project/'data').resolve())
data_home=base/'Source App';data_home.mkdir()
fixture=base/'scan-fixture';fixture.mkdir();(fixture/'sample.bin').write_bytes(b'a'*11_000_001)
config.ROOT=data_home
cfg=config.defaults();cfg['profiles']['daily']={'roots':[str(fixture)],'engine':'native','strict_browsers':True,'excludes':[]};config.save(cfg)
scan('daily',cfg['profiles']['daily']);(fixture/'sample.bin').write_bytes(b'b'*12_000_001)
second=scan('daily',cfg['profiles']['daily'])
cache=Comparisons();cache.prepare(second['previous'],second['scan_id']);cache.close()
benchmark('daily',cfg['profiles']['daily'],engines=('native',),repeats=1)
source=data_home/'app/keep-source.txt';source.parent.mkdir();source.write_text('keep source',encoding='utf-8')
venv=data_home/'.venv/keep-venv.txt';venv.parent.mkdir();venv.write_text('keep venv',encoding='utf-8')
log=data_home/'logs/keep-log.txt';log.write_text('keep log',encoding='utf-8')

environment=os.environ.copy()
for key in ('DGM_HOME','DGM_SOURCE','PYTHONHOME','PYTHONPATH'):environment.pop(key,None)
environment.update(PYTHONUTF8='1',PYTHONNOUSERSITE='1',QT_QPA_PLATFORM='offscreen',TEMP=str(base),TMP=str(base))
report={'version':__version__,'installer_sha256':hashlib.sha256(installer.read_bytes()).hexdigest(),'fixture_only':True,'checks':[]}

def execute(argv,env=None):
    done=subprocess.run([str(x) for x in argv],cwd=project,env=env or environment,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=240,creationflags=0x08000000)
    if done.returncode:raise RuntimeError(f'{Path(argv[0]).name}: {done.returncode}\n{done.stdout[-3000:]}\n{done.stderr[-3000:]}')
    return done.stdout

def contents(root):
    result={}
    for database,tables in (('history.sqlite',('scans','benchmarks')),('comparisons.sqlite',('pairs',)),('normalization.sqlite',('datasets','overrides'))):
        if not (root/'data'/database).exists():continue
        with sqlite3.connect(f'file:{(root/"data"/database).as_posix()}?mode=ro',uri=True) as db:
            for table in tables:result[table]=db.execute(f'SELECT * FROM {table} ORDER BY '+('dataset_id,path_id' if table=='overrides' else 'id')).fetchall()
    return result

def install_at(path,exe):
    execute([exe,'/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/PORTABLE=1',f'/DIR={path}'])

def coordinate(root,target,bootstrap,conversion,helper_source):
    folder=root/'data/updates'/('session-'+uuid.uuid4().hex);folder.mkdir(parents=True)
    cached=root/'data/updates/fixture-download/installer.exe';cached.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(installer,cached)
    session={'id':folder.name,'root':str(root),'install_root':str(target),'source_conversion':conversion,
        'bootstrap':bootstrap,'release':{'version':__version__,'size':installer.stat().st_size,'sha256':report['installer_sha256']},
        'installer':str(cached),'parent':{'pid':2147483647,'created':0},'original':{},'changed':[],
        'portable':True,'phase':'waiting','restore_pending':False}
    path=folder/'session.json';path.write_text(json.dumps(session),encoding='utf-8')
    helper=folder/'update-helper.ps1';shutil.copy2(helper_source,helper)
    powershell=Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
    execute([powershell,'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',helper,'-Session',path,'-Quiet'])
    outcome=json.loads(path.read_text(encoding='utf-8'))
    assert outcome['phase']=='complete' and not outcome['restore_pending']
    return folder

before=contents(data_home);config_bytes=(data_home/'data/config.json').read_bytes()
target=data_home/'.dgm-app'
session=coordinate(data_home,target,{'executable':sys.executable,'arguments':['-m','dgm']},True,project/'app/dgm/update_helper.ps1')
assert contents(data_home)==before and (data_home/'data/config.json').read_bytes()==config_bytes
assert source.read_text()=='keep source' and venv.read_text()=='keep venv' and log.read_text()=='keep log'
assert (session/'backup/history.sqlite').is_file() and (session/'backup/comparisons.sqlite').is_file()
doctor=json.loads(execute([target/'dgm.exe','doctor']))
assert doctor['frozen'] and Path(doctor['home'])==data_home and doctor['app_version']==__version__
report['checks'].append('source update uses managed runtime and existing data home; scan/comparison/benchmark rows, config, logs, source and development venv preserved')
env=dict(environment,DGM_HOME=str(data_home))
forwarded=json.loads(execute([sys.executable,'-m','dgm','doctor'],env))
assert forwarded['frozen'] and Path(forwarded['home'])==data_home
report['checks'].append('same source CLI launcher automatically forwards to updated frozen runtime')
coordinate(data_home,target,{'executable':str(target/'dgm.exe'),'arguments':[]},False,target/'.venv/dgm/update_helper.ps1')
assert contents(data_home)==before and (data_home/'data/config.json').read_bytes()==config_bytes
assert Path(json.loads(execute([target/'dgm.exe','doctor']))['home'])==data_home
report['checks'].append('second update replaces the same managed runtime and retains the external data-home pointer and all history')

if legacy_installer:
    legacy=base/'Legacy App';install_at(legacy,legacy_installer)
    exe=legacy/'dgm.exe'
    execute([exe,'profile','set','daily','--root',fixture,'--engine','native'])
    first=json.loads(execute([exe,'scan']));second=json.loads(execute([exe,'scan']))
    execute([exe,'compare',second['scan_id'],'--older',first['scan_id']])
    old_contents=contents(legacy);old_config=(legacy/'data/config.json').read_bytes()
    coordinate(legacy,legacy,{'executable':str(exe),'arguments':[]},False,legacy/'.venv/dgm/update_helper.ps1')
    assert json.loads(execute([exe,'doctor']))['app_version']==__version__
    assert contents(legacy)==old_contents and (legacy/'data/config.json').read_bytes()==old_config
    report['checks'].append('real upgrade from prior installer using its OLD coordinator replaces same install directory and preserves scan/comparison history')
    execute([legacy/'unins000.exe','/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART'])
    assert contents(legacy)==old_contents
    (legacy/'data/updates/fixture-download/installer.exe').unlink()

execute([target/'unins000.exe','/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART'])
assert contents(data_home)==before and source.read_text()=='keep source' and venv.read_text()=='keep venv'
assert log.read_text()=='keep log' and not (target/'dgm.exe').exists()
(data_home/'data/updates/fixture-download/installer.exe').unlink()
report['checks'].append('uninstall uses linked data-home guard and preserves old history/source/venv/logs; owned temporary binaries and installer copies removed')

result=installer.parent/'inplace-update-test-results.json'
result.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False,indent=2));print(f'Fixture/logs retained at {base}')
