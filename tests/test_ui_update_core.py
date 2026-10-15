"""Owned fixtures only. Network, UAC and OS runner mutations are mocked."""
import hashlib
import io
import json
from pathlib import Path
import threading
import pytest
from typer.testing import CliRunner
from dgm import app_update as update
from dgm.cli import app
from dgm.config import home
from dgm.comparison import Comparisons
from dgm.store import Store
from test_v2 import snapshot,entry


@pytest.fixture
def release():
    data=b'fixture installer, never executable'
    digest=hashlib.sha256(data).hexdigest()
    return data,{'version':'99.0.0','tag':'v99.0.0','sha256':digest,'size':len(data),
        'url':f'https://github.com/{update.REPO}/releases/download/v99.0.0/installer.exe',
        'available':True,'current':'1.1.0','mode':'installed','notes':'Fixture'}


def raw_release(release):
    return {'tag_name':release['tag'],'html_url':f'https://github.com/{update.REPO}/releases/tag/{release["tag"]}',
        'draft':False,'prerelease':False,'body':'test','assets':[{'name':'installer.exe','size':release['size'],
        'browser_download_url':release['url'],'digest':'sha256:'+release['sha256']}]}


def test_release_check_only_trusted_stable_and_no_mutation(isolated,release,monkeypatch):
    from dgm import updates
    data,rel=release;raw=raw_release(rel);before=(home()/'data/config.json').read_bytes();calls=[]
    monkeypatch.setattr(updates,'fetch',lambda url:calls.append(url) or raw)
    result=update.check()
    assert result['available'] and result['mode']=='source' and result['sha256']==rel['sha256']
    assert calls==[update.API] and (home()/'data/config.json').read_bytes()==before
    assert not (home()/'data/updates').exists()


@pytest.mark.parametrize('mutation',[{'prerelease':True},{'draft':True},{'tag_name':'v99.0.0-rc1'},
    {'html_url':'https://github.com/attacker/fake/releases/tag/v99.0.0'},{'assets':[]}])
def test_reject_untrusted_or_unstable_release(release,mutation):
    _,rel=release;raw=raw_release(rel);raw.update(mutation)
    with pytest.raises((ValueError,KeyError)):update.parse_release(raw)


@pytest.mark.parametrize('url',['http://github.com/bbkien2312/DiskGrowthMonitor/releases/download/v99.0.0/installer.exe',
    'https://github.com.evil.test/bbkien2312/DiskGrowthMonitor/releases/download/v99.0.0/installer.exe',
    'https://github.com/other/repo/releases/download/v99.0.0/installer.exe',
    f'https://github.com/{update.REPO}/releases/download/v99.0.0/installer.exe?redirect=evil'])
def test_reject_bad_asset_urls(url):
    with pytest.raises(ValueError):update.asset_url(url,'v99.0.0','installer.exe')


def test_checksum_fallback_is_exact_and_bounded(isolated,release,monkeypatch):
    from dgm import updates
    _,rel=release;raw=raw_release(rel);raw['assets'][0].pop('digest')
    raw['assets'].append({'name':'SHA256SUMS.txt','browser_download_url':rel['url'].replace('installer.exe','SHA256SUMS.txt')})
    monkeypatch.setattr(updates,'fetch',lambda _:raw)
    monkeypatch.setattr(update,'open_asset',lambda _:io.BytesIO((rel['sha256']+'  installer.exe\n').encode()))
    assert update.check()['sha256']==rel['sha256']
    monkeypatch.setattr(update,'open_asset',lambda _:io.BytesIO(b'bad sums'))
    with pytest.raises(ValueError,match='Checksum'):update.check()


def test_download_cache_hash_failure_cancel_and_no_pip(isolated,release,monkeypatch):
    data,rel=release;calls=[];events=[]
    monkeypatch.setattr(update,'open_asset',lambda url:calls.append(url) or io.BytesIO(data))
    path=Path(update.download(rel,notify=lambda *args:events.append(args)))
    assert path.read_bytes()==data and events and len(calls)==1
    assert update.download(rel)==str(path) and len(calls)==1
    path.write_bytes(b'corrupt')
    monkeypatch.setattr(update,'open_asset',lambda _:io.BytesIO(b'x'*len(data)))
    with pytest.raises(ValueError,match='SHA-256'):update.download(rel)
    assert not list(path.parent.glob('*.part')) and path.read_bytes()==b'corrupt'
    stop=threading.Event();stop.set()
    from dgm.platform_ops import ProcessCancelled
    with pytest.raises(ProcessCancelled):update.download(rel,stop)
    assert not list(path.parent.glob('*.part'))


def test_downgrade_and_source_install_never_launch(isolated,release):
    _,rel=release
    with pytest.raises(RuntimeError,match='source'):update.prepare_install(rel,'nonexistent.exe')
    for version in ('1.0.0','1.1.0'):
        candidate={**rel,'version':version,'tag':'v'+version,'url':rel['url'].replace('v99.0.0','v'+version)}
        with pytest.raises(ValueError,match='Không có bản'):update.validate_release(candidate)


@pytest.fixture
def session(isolated,release,monkeypatch):
    import dgm.background as background
    _,rel=release;data=release[0]
    monkeypatch.setattr(update,'installation_mode',lambda:'installed')
    state={'scan':{'running':False},'task':{'installed':True,'running':False,'enabled':True,
        'actions':[{'path':str(home()/'DiskGrowthMonitor.exe')}]},'service':{'installed':False}}
    monkeypatch.setattr(background,'status',lambda:state)
    folder=home()/'data/updates/fixture';folder.mkdir(parents=True);target=folder/'installer.exe';target.write_bytes(data)
    (home()/'installed-app.json').write_text('{"app":"DiskGrowthMonitor","update_protocol":1}')
    ticket=update.prepare_install(rel,target)
    yield ticket,state


def test_handoff_blocks_scans_and_uac_cancel_preserves_app(session,monkeypatch):
    ticket,state=session
    from dgm.scanner import ScanLock
    with pytest.raises(RuntimeError,match='cập nhật'):
        with ScanLock():pass
    from win32com.shell import shell
    def deny(**kwargs):raise OSError('cancelled UAC')
    monkeypatch.setattr(shell,'ShellExecuteEx',deny)
    with pytest.raises(OSError):update.launch(ticket)
    assert not update.active_update() and state['task']['enabled']
    assert not (home()/'data/worker.lock').exists()


def test_stage_backup_restore_task_exact_without_remove(session,monkeypatch):
    ticket,state=session;calls=[]
    import dgm.background as background
    import dgm.schedule as schedule
    import dgm.windows_service as services
    def disable(kind,action,**kwargs):calls.append((kind,action));state[kind]['enabled']=False
    monkeypatch.setattr(background,'control',disable)
    monkeypatch.setattr(schedule,'enable_task',lambda enabled:calls.append(('restore',enabled)))
    monkeypatch.setattr(services,'restore_state',lambda _:pytest.fail('No owned service'))
    db=Store();db.close()
    result=update.stage(ticket['session'])
    assert (Path(result['backup'])/'history.sqlite').exists()
    assert (Path(result['backup'])/'config.json').read_bytes()==(home()/'data/config.json').read_bytes()
    monkeypatch.setattr(update,'__version__','99.0.0')
    result=update.finish(ticket['session'],success=True)
    assert result['updated'] and result['restored'] and calls==[('task','disable'),('restore',True)]
    assert not update.active_update()


def test_stage_partial_failure_journal_recovers_and_other_owner_not_touched(session,monkeypatch):
    ticket,state=session
    import dgm.background as background
    import dgm.schedule as schedule
    def fail(kind,action,**kw):state[kind]['enabled']=False;raise RuntimeError('mock disable failed after change')
    monkeypatch.setattr(background,'control',fail)
    with pytest.raises(RuntimeError,match='mock'):update.stage(ticket['session'])
    saved=json.loads(Path(ticket['session']).read_text());assert saved['restore_pending'] and saved['changed']==['task']
    calls=[];monkeypatch.setattr(schedule,'enable_task',lambda enabled:calls.append(enabled))
    assert update.finish(ticket['session'])['restored'] and calls==[True]
    state['task']['actions']=[{'path':'D:/other/dgm.exe'}]
    with pytest.raises(RuntimeError,match='khôi phục'):update.finish(ticket['session'])
    assert calls==[True]


def test_prepare_does_not_overwrite_live_lease(session,release,monkeypatch):
    ticket,_=session;before=update.lease_path().read_bytes();rel=release[1]
    with pytest.raises(RuntimeError,match='đang hoạt động'):update.prepare_install(rel,json.loads(Path(ticket['session']).read_text())['installer'])
    assert update.lease_path().read_bytes()==before


def test_upgrade_retains_only_verified_disabled_idle_runners(isolated,monkeypatch):
    import dgm.background as background
    from dgm.installation import upgrade_safety
    monkeypatch.setattr('psutil.process_iter',lambda *a:[])
    state={'scan':{'running':False},'task':{'installed':True,'enabled':False,'running':False,
        'actions':[{'path':str(home()/'dgm.exe')}]},'service':{'installed':False}}
    monkeypatch.setattr(background,'status',lambda:state)
    assert upgrade_safety()['safe_to_upgrade']
    assert CliRunner().invoke(app,['uninstall-check']).exit_code==2
    state['task']['enabled']=True;assert not upgrade_safety()['safe_to_upgrade']
    state['task']['actions']=[{'path':'D:/another/dgm.exe'}];assert upgrade_safety()['safe_to_upgrade']
    state['service']={'installed':None};assert not upgrade_safety()['safe_to_upgrade']


def test_multiple_statuses_global_scope_cursor_and_cli(isolated):
    root=isolated/'virtual';branch=root/'chosen';store=Store()
    items=[entry(branch,'folder')]+[entry(branch/f'f{i:04}',value=1000) for i in range(1300)]+[entry(root/'outside',value=999)]
    old=snapshot(store,root,items)
    new=snapshot(store,root,[{**r,'logical':2000 if i%3==0 else 500 if i%3==1 else r['logical']} if r['kind']=='file' else r for i,r in enumerate(items)])
    cache=Comparisons();pair=cache.prepare(old,new);rows=[];cursor=None
    for count in (20,100,1000):
        page=cache.page(pair,root=str(branch),kind='file',changes=['grown','shrunk'],limit=count,cursor=cursor,sort='new')
        rows.extend(page['rows']);cursor=page['cursor']
        if not cursor:break
    assert len(rows)==page['total'] and len(rows)>800 and {r['change'] for r in rows}=={'grown','shrunk'}
    assert all(r['parent']==str(branch) for r in rows)
    assert [(r['_key'],r['path_id']) for r in rows]==sorted([(r['_key'],r['path_id']) for r in rows],reverse=True)
    first=cache.page(pair,changes=['grown','shrunk'])
    with pytest.raises(ValueError,match='Con trỏ'):cache.page(pair,changes=['same'],cursor=first['cursor'])
    assert cache.page(pair,changes=[])['total']==0
    assert cache.page(pair,changes='grown,shrunk')['total']>=len(rows)
    assert cache.page(pair,changes=['shrunk','grown'])['total']==cache.page(pair,changes=['grown','shrunk'])['total']
    with pytest.raises(ValueError):cache.page(pair,changes=['bogus'])
    result=CliRunner().invoke(app,['compare',str(new),'--older',str(old),'--changes','grown,shrunk','--minimum-mb','0'])
    assert result.exit_code==0 and len(json.loads(result.output)['rows'])==20
    cache.close();store.close()


@pytest.mark.parametrize('startup,running',[(2,True),(3,False),(4,False)])
def test_service_exact_startup_restore_without_reinstall(isolated,monkeypatch,startup,running):
    import win32service
    from dgm import windows_service as services
    calls=[]
    monkeypatch.setattr(services,'is_admin',lambda:True)
    monkeypatch.setattr(win32service,'OpenSCManager',lambda *a:'manager')
    monkeypatch.setattr(win32service,'OpenService',lambda *a:'service')
    monkeypatch.setattr(win32service,'CloseServiceHandle',lambda *a:None)
    monkeypatch.setattr(win32service,'ChangeServiceConfig',lambda *args:calls.append(args))
    monkeypatch.setattr(services,'control',lambda action:calls.append(action))
    monkeypatch.setattr(services,'status',lambda:{'installed':True})
    services.restore_state({'startup_type':startup,'running':running})
    assert calls[0][2]==startup and ('start' in calls)==running
    assert not any(c in ('install','uninstall') for c in calls if isinstance(c,str))


def test_source_installer_uses_empty_other_folder_only(isolated,release,monkeypatch):
    data,rel=release;folder=home()/'data/updates/fixture';folder.mkdir(parents=True);target=folder/'installer.exe';target.write_bytes(data)
    destination=isolated/'new app';destination.mkdir();calls=[]
    monkeypatch.setattr(update.subprocess,'Popen',lambda argv:calls.append(argv))
    assert update.install_separately(rel,target,destination)['source_unchanged']
    assert calls==[[str(target),'/DIR='+str(destination)]]
    with pytest.raises(ValueError):update.install_separately(rel,target,home())
    (destination/'existing.txt').write_text('keep')
    with pytest.raises(ValueError):update.install_separately(rel,target,destination)
    assert len(calls)==1


def test_offline_updater_never_launches_and_source_cli_requires_destination(isolated,monkeypatch):
    from dgm import updates
    def offline(*a):raise OSError('network offline')
    monkeypatch.setattr(updates,'fetch',offline)
    with pytest.raises(OSError,match='offline'):update.check()
    result=CliRunner().invoke(app,['app-update','install','--yes'])
    assert result.exit_code==2 and 'destination' in result.output


def test_unknown_owner_blocks_mutations_not_just_upgrade(isolated,monkeypatch):
    from dgm import background,schedule,windows_service
    from dgm.installation import owns_background,verified_background_owner,upgrade_safety
    state={'installed':True,'enabled':False,'running':False,'actions':[]}
    monkeypatch.setattr(schedule,'task_status',lambda:state)
    monkeypatch.setattr(windows_service,'status',lambda:{'installed':False})
    monkeypatch.setattr(schedule,'enable_task',lambda *a:pytest.fail('Unknown runner must not mutate'))
    monkeypatch.setattr('psutil.process_iter',lambda *a:[])
    assert owns_background('task',state) and not verified_background_owner('task',state)
    assert background.status()['task']['owner_status']=='unknown'
    with pytest.raises(RuntimeError,match='xác minh'):background.control('task','enable')
    assert not upgrade_safety()['safe_to_upgrade']


def test_background_status_reads_current_schedule_without_gui_cache(isolated,monkeypatch):
    from dgm import background,schedule,windows_service
    from dgm.config import load,save
    cfg=load();cfg['schedules']=[{'enabled':True}];save(cfg)
    monkeypatch.setattr(schedule,'task_status',lambda:{'installed':False})
    monkeypatch.setattr(windows_service,'status',lambda:{'installed':False})
    rows=[{'enabled':True,'next_run':'2026-10-15T15:00:00+00:00'},{'enabled':False,'next_run':'2026-10-14T00:00:00+00:00'}]
    monkeypatch.setattr(schedule,'listing',lambda:rows)
    assert background.status()['schedules']=={'enabled_count':1,'next_run':rows[0]['next_run']}
    rows[0]['next_run']='2026-10-16T15:00:00+00:00'
    assert background.status()['schedules']['next_run']==rows[0]['next_run']


def test_uac_cancel_is_cooperative_not_an_update_error(session,monkeypatch):
    from win32com.shell import shell
    from dgm.elevation import ElevationCancelled
    ticket,_=session
    def deny(**kw):
        error=OSError('cancelled');error.winerror=1223;raise error
    monkeypatch.setattr(shell,'ShellExecuteEx',deny)
    with pytest.raises(ElevationCancelled,match='hủy UAC'):update.launch(ticket)
    assert not update.active_update()
