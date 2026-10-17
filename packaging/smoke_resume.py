"""Frozen CLI checkpoint smoke: synthetic D: data only, no UAC/install/delete."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading


def main():
    parser=argparse.ArgumentParser();parser.add_argument('executable',type=Path);args=parser.parse_args()
    executable=args.executable.resolve();project=Path(__file__).resolve().parents[1]
    fixture=Path(tempfile.mkdtemp(prefix='resume-binary-',dir=project/'artifacts'))
    root=fixture/'files';root.mkdir();file=root/'one.bin';file.write_bytes(b'a'*100)
    from dgm import config,__version__
    config.ROOT=fixture/'app';cfg=config.defaults()
    p={'roots':[str(root)],'engine':'native','strict_browsers':True,'threshold_mb':0,'mft':False,'excludes':[]}
    cfg['profiles']['daily']=p;config.save(cfg)
    env=dict(os.environ,DGM_HOME=str(config.ROOT),PYTHONUTF8='1',QT_QPA_PLATFORM='offscreen',
             LOCALAPPDATA=str(fixture/'local'),APPDATA=str(fixture/'roaming'),TEMP=str(fixture/'tmp'),TMP=str(fixture/'tmp'))
    (fixture/'tmp').mkdir()
    def cli(*items,ok=True):
        result=subprocess.run([str(executable),'--home',str(config.ROOT),*map(str,items)],
            cwd=fixture,env=env,capture_output=True,encoding='utf-8',timeout=60)
        if ok:
            if result.returncode not in (0,2) or not result.stdout.strip():raise RuntimeError(result.stderr[-3000:])
            return json.loads(result.stdout)
        assert result.returncode!=0
        return result
    checks=[]
    first=cli('scan','--profile','daily');assert first['logical']==100 and first['provenance']['app_version']==__version__
    assert first['small_files']==1;checks.append('frozen native scan: allocation, small-file totals and app provenance')
    from dgm.task_journal import Journal
    from dgm.store import Store
    from dgm.history_backend import close_indexes,snapshots
    store=Store();row=store.scan(first['scan_id'],effective=False)
    checkpoint=Journal('scan',{'name':'daily','p':p})
    with snapshots([first['scan_id']]):
        checkpoint.capture(store,first['scan_id'],json.loads(row['summary']),'daily',p,threading.Event())
    checkpoint.save(state='interrupted',owner=None,percent=70,stage='Synthetic completed-capture interruption')
    store.close();close_indexes();file.write_bytes(b'b'*900);(root/'new.bin').write_bytes(b'c'*500)
    assert any(r['id']==checkpoint.row['id'] for r in cli('tasks','list'))
    resumed=cli('tasks','resume',checkpoint.row['id'])
    assert resumed['logical']==100 and resumed['resumed'] and resumed['captured_at']==row['started']
    assert cli('tasks','show',checkpoint.row['id'])['state']=='complete'
    checks.append('frozen resume uses checksummed capture after index removal and live filesystem change, preserving capture time')
    comparison=cli('compare',resumed['scan_id'],'--older',first['scan_id'],'--minimum-mb','0','--sort','disk_new')
    assert comparison['summary']['verified_common_allocated_delta']==0
    assert all(v is not False for side in comparison['summary']['measurement_checks'].values() for v in side.values())
    checks.append('frozen comparison: physical ordering and common/private byte conservation')
    restart=Journal('scan',{'name':'daily','p':p});restart.save(state='interrupted',owner=None)
    current=cli('tasks','resume',restart.row['id']);assert current['logical']==1400 and not current.get('resumed')
    checks.append('frozen incomplete scan restarts and records current files in a new snapshot')
    store=Store();bad=Journal('scan',{'name':'daily','p':p})
    source=store.scan(current['scan_id'],effective=False)
    with snapshots([current['scan_id']]):
        bad.capture(store,current['scan_id'],json.loads(source['summary']),'daily',p,threading.Event())
    bad.save(state='interrupted',owner=None);bad.artifact('capture.jsonl.gz').write_bytes(b'broken fixture')
    store.close();close_indexes();cli('tasks','resume',bad.row['id'],ok=False)
    cli('tasks','discard',bad.row['id'],'--yes')
    assert len(cli('history'))==3 and file.exists() and (root/'new.bin').exists()
    checks.append('frozen corrupt capture refused; discard preserves three snapshots and all real fixture files')
    import csv
    wiz_profile={**p,'engine':'wiztree','strict_browsers':False,'mft':True}
    wiz=Journal('scan',{'name':'wiz-fixture','p':wiz_profile})
    wiz.save(scan_context={'started':row['started'],'source_scan':first['scan_id'],
        'summary':{'files':0,'folders':0,'volumes':{},'root_overview':{},
                   'provenance':{'app_version':__version__},'scan_method':{'engine':'wiztree','mft':True,'admin':True}}})
    exported=fixture/'completed-wiz.csv'
    with exported.open('w',encoding='utf-8',newline='') as stream:
        writer=csv.writer(stream);writer.writerow(['File Name','Size','Allocated','Attributes','MFT Record Number'])
        writer.writerow([str(root)+'\\',700,4096,16,1]);writer.writerow([str(file),700,4096,0,2])
    wiz.adopt_source(0,exported,config.volume_identity(root));wiz.save(state='interrupted',owner=None)
    cfg=config.load();cfg['engines']['wiztree']=str(fixture/'not-installed.exe');config.save(cfg)
    imported=cli('tasks','resume',wiz.row['id'])
    assert imported['logical']==700 and imported['resumed'] and imported['captured_at']==row['started']
    checks.append('frozen completed WizTree MFT CSV resumes without installed engine or new UAC/live scan')
    result={'version':__version__,'fixture_only':True,'fixture':str(fixture),'checks':checks,
            'gui_and_installer_uac_tested_here':False,'production_C_untouched':True}
    destination=project/'artifacts/resume-binary-results.json';destination.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
