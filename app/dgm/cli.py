from __future__ import annotations
import json
import os
from pathlib import Path
from typing import Optional
import typer

app = typer.Typer(help="Theo dõi dung lượng Windows — CLI/GUI dùng chung dữ liệu", no_args_is_help=True, add_completion=False)
profiles = typer.Typer(help="Phạm vi và engine quét")
cleanup_app = typer.Typer(help="Preview và xóa các mục đã duyệt")
schedules = typer.Typer(help="Lịch chạy và worker nền")
files = typer.Typer(help="Thao tác file/thư mục")
ai = typer.Typer(help="Tạo câu hỏi và mở AI web")
app_updates = typer.Typer(help='Kiểm tra/tải/cài bản app chính thức; không dùng pip hoặc ghi đè source')
storage_app=typer.Typer(help='Dung lượng riêng của app: xem trước, dọn backup/installer, thu gọn cache; không tự xóa lịch sử')
app.add_typer(profiles, name="profile")
app.add_typer(cleanup_app, name="cleanup")
app.add_typer(schedules, name="schedule")
app.add_typer(files, name="file")
app.add_typer(ai, name="ai")
app.add_typer(app_updates, name='app-update')
app.add_typer(storage_app,name='storage')


@storage_app.command('report')
def storage_report():
    from .storage_maintenance import storage_report as report
    output(report())


@storage_app.command('cleanup-updates')
def storage_cleanup(keep:Optional[int]=None,yes:bool=typer.Option(False,'--yes'),preview:bool=False):
    from .storage_maintenance import update_plan,cleanup_updates
    plan=update_plan(keep=keep)
    if preview:output(plan);return
    if not yes:
        output(plan)
        if not typer.confirm('Xóa các file backup/installer trong danh sách? Không thể hoàn tác; lịch sử hiện tại giữ nguyên.'):
            raise typer.Exit()
    output(cleanup_updates(keep=keep,preview=plan))


@storage_app.command('compact-cache')
def storage_compact(yes:bool=typer.Option(False,'--yes')):
    from .storage_maintenance import compact_comparison_cache
    if not yes and not typer.confirm('Thu gọn cache SQLite, giữ kết quả và lịch sử? Cần đủ chỗ tạm.'):
        raise typer.Exit()
    output(compact_comparison_cache())


@storage_app.command('clear-index')
def storage_clear_index(yes:bool=typer.Option(False,'--yes')):
    from .history_backend import clear_indexes
    if not yes and not typer.confirm('Xóa chỉ mục tạm? Log lịch sử giữ nguyên; lần so sánh sau cần đọc lại log.'):
        raise typer.Exit()
    output(clear_indexes())


@storage_app.command('migrate-history')
def storage_migrate_history(yes:bool=typer.Option(False,'--yes'),retire_old:bool=typer.Option(False,'--retire-old',help='Sau xác minh, dọn SQLite cũ, CSV nguồn và backup cập nhật hoàn tất; giữ log metadata.')):
    from .history_migration import preview,migrate
    plan=preview()
    if not plan['needed']:output(plan);return
    if not yes:output(plan)
    if not yes and not typer.confirm('Chuyển lịch sử metadata sang JSONL nén, xác minh trước khi bỏ nguồn SQLite cũ?'):
        raise typer.Exit()
    output(migrate(remove_old=retire_old,notify=admin_progress))


@storage_app.command('retire-legacy')
def storage_retire_legacy(yes:bool=typer.Option(False,'--yes')):
    from .history_migration import retire_legacy
    if not yes and not typer.confirm('Xác minh lại các log và bỏ đúng SQLite cũ không thay đổi?'):
        raise typer.Exit()
    output(retire_legacy())


def output(data):
    typer.echo(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def invoke_action(function,*args,**kwargs):
    from .elevation import ElevationCancelled
    try:return function(*args,**kwargs)
    except ElevationCancelled as exc:
        typer.echo(str(exc),err=True);raise typer.Exit(130)


def admin_progress(event,data):
    if event=='info':typer.echo('INFO '+data['message'],err=True)


@app.callback()
def settings(ctx:typer.Context,home: Optional[Path] = typer.Option(None, "--home", help="Thư mục chứa app/data"), debug: bool = typer.Option(False, "--debug")):
    from . import config
    if home:
        config.ROOT = home.resolve()
    cfg = config.load()
    from .logging_utils import setup
    if ctx.invoked_subcommand!='_elevated-worker':setup(debug or cfg["debug"])
    if debug:
        cfg["debug"] = True
        config.save(cfg)


@profiles.command("list")
def profile_list():
    from .config import load
    output(load()["profiles"])


@profiles.command("set")
def profile_set(name: str, root: list[str] = typer.Option(..., "--root"), exclude: list[str] = typer.Option([], "--exclude"),
                engine: str = "wiztree", strict_browsers: bool = False, threshold_mb: float = 10, mft: bool = True):
    from .config import load, save, Policy
    if engine not in ("native", "wiztree", "windirstat", "gdu") or threshold_mb < 0:
        raise typer.BadParameter("Engine/ngưỡng không hợp lệ")
    cfg = load()
    if mft and strict_browsers:
        raise typer.BadParameter("MFT không bật khi cấm đọc Chrome/Edge")
    p = {"roots": root, "excludes": exclude, "strict_browsers": strict_browsers, "engine": engine, "threshold_mb": threshold_mb, "mft": mft}
    Policy(p)
    cfg["profiles"][name] = p
    save(cfg)
    output(p)


@app.command("scan")
def scan_command(profile: str = "daily", engine: Optional[str] = None):
    from .config import profile as get_profile
    from .scanner import scan
    p = get_profile(profile)
    if engine:
        p["engine"] = engine
    def notify(event, data):
        if event in ("started", "info"):
            typer.echo(f"{event}: {data}", err=True)
    result = scan(profile, p, notify=notify)
    output(result)
    if result["status"] == "failed":
        raise typer.Exit(1)
    if result["status"] == "partial":
        raise typer.Exit(2)
    if result["status"] == "cancelled":
        raise typer.Exit(130)


@app.command("history")
def history(profile: Optional[str] = None):
    from .store import Store
    from .presentation import scan_label
    s = Store()
    output([{**r,"label":scan_label(r)} for r in s.scans(profile)])
    s.close()


@app.command("scan-stop")
def scan_stop():
    from .scan_control import request_stop
    output(request_stop())


@app.command("scan-status")
def scan_status():
    from .scan_control import current
    output(current())


@app.command("entries")
def entries(scan_id: int, minimum_mb: float = 10, kind: str = "all", search: str = "", limit: int = 1000, offset: int = 0):
    from .store import Store
    s = Store()
    output(s.entries(scan_id, int(minimum_mb * 1_000_000) + 1, kind, search, limit, offset))
    s.close()


@app.command("compare")
def compare(newer: int, older: Optional[int] = None, kind: str = "all", search: str = "", changes: str = "all",
            limit: int = 20, offset: int = 0, sort: str = "delta", direction: str = "desc",
            minimum_mb: float = 10, root: Optional[str] = None, recursive: bool = True,
            cursor: Optional[str] = None, baseline: bool = False,common:bool=typer.Option(False,'--common',help='Tương thích cũ: luôn tự chia Chung/Riêng'),
            section:str=typer.Option('all',help='all / common (hai mốc) / private (một mốc)'),reason:Optional[str]=None,
            presence:str=typer.Option('all',help='all / both / old / new: mốc có bản ghi')):
    from .store import Store
    from .comparison import Comparisons
    s = Store(readonly=True)
    try: previous = None if baseline else older if older is not None else s.previous(newer)
    finally: s.close()
    if offset < 0 or minimum_mb < 0 or (offset and cursor):
        raise typer.BadParameter("Offset/ngưỡng >=0; dùng cursor hoặc offset, không đồng thời.")
    cache = Comparisons()
    try:
        pair = cache.prepare(previous,newer,common=common)
        position=json.loads(cursor) if cursor else None
        options=dict(sort=sort,direction=direction,minimum=int(minimum_mb*1_000_000),kind=kind,changes=changes,search=search,root=root,recursive=recursive,section=section,reason=reason,presence=presence)
        while offset:
            page=cache.page(pair,limit=min(1000,offset),cursor=position,**options)
            offset-=len(page["rows"]); position=page["cursor"]
            if not position:
                output({**page,"rows":[],"summary":cache.summary(pair)}); return
        output({**cache.page(pair,limit=limit,cursor=position,**options),"summary":cache.summary(pair),"note":"Lần đầu quét" if previous is None else "So sánh snapshot"})
    finally: cache.close()


@app.command("comparison-history")
def comparison_history():
    from .comparison import Comparisons
    cache=Comparisons()
    try: output(cache.history())
    finally: cache.close()


@app.command("overview")
def overview(newer: int, older: Optional[int] = None,common:bool=typer.Option(False,'--common')):
    from .store import Store
    from .comparison import Comparisons
    store=Store(readonly=True)
    try: previous=older if older is not None else store.previous(newer)
    finally: store.close()
    cache=Comparisons()
    try: output(cache.summary(cache.prepare(previous,newer,common=common)))
    finally: cache.close()


@app.command("tree")
def tree(profile: str = "daily", root: Optional[str] = None, cursor: Optional[str] = None):
    from .store import Store
    from .comparison import Comparisons
    store=Store(readonly=True)
    try:
        valid=[r for r in store.scans(profile) if r["status"] in ("complete","partial")]
        if not valid: raise ValueError("Chưa có lượt hợp lệ.")
        latest=max(valid,key=lambda r:(r["started"],r["id"])); previous=store.previous(latest["id"])
        roots=json.loads(latest["config"])["roots"]
    finally: store.close()
    cache=Comparisons()
    try:
        pair=cache.prepare(previous,latest["id"])
        if root:
            result=cache.page(pair,root=root,recursive=False,folders_only=True,sort="new",limit=20,cursor=json.loads(cursor) if cursor else None)
        else:
            rows=[]
            for path in roots:
                row=cache.db.execute("SELECT c.*,p.path FROM diff_rows c JOIN source.paths p ON p.id=c.path_id WHERE c.pair_id=? AND p.path=?",(pair,str(Path(path)))).fetchone()
                if row: rows.append(dict(row))
            result={"rows":rows,"cursor":None,"total":len(rows)}
        output({**result,"older":previous,"newer":latest["id"],"note":"Hai lượt hợp lệ gần nhất; độc lập mốc so sánh."})
    finally: cache.close()


@app.command("snapshot-rebuild")
def snapshot_rebuild(scan_id: int, confirmed: bool = False):
    from .recovery import rebuild
    result=rebuild(scan_id,confirmed=confirmed);output(result)
    if result['status']=='cancelled': raise typer.Exit(130)


def normalization_ids(scan_ids,all_legacy):
    from .store import Store
    from .normalization import VERSION
    store=Store()
    try:
        if all_legacy:
            return [r['id'] for r in store.scans() if r['status'] in ('complete','partial') and
                    json.loads(r['summary'] or '{}').get('normalization',{}).get('version')!=VERSION]
        ids=sorted(set(scan_ids or []))
        if not ids:raise typer.BadParameter('Chọn --id (lặp lại được) hoặc --all-legacy.')
        for ident in ids:store.scan(ident)
        return ids
    finally:store.close()


@app.command('snapshot-audit')
def snapshot_audit(scan_id:Optional[list[int]]=typer.Option(None,'--id'),all_legacy:bool=False):
    from .normalization import Normalizations
    ids=normalization_ids(scan_id,all_legacy);norm=Normalizations()
    try:output([norm.inspect(ident,force=True) for ident in ids])
    finally:norm.close()


@app.command('snapshot-normalize')
def snapshot_normalize(scan_id:Optional[list[int]]=typer.Option(None,'--id'),all_legacy:bool=False,
                       yes:bool=typer.Option(False,'--yes')):
    from .normalization import Normalizations
    ids=normalization_ids(scan_id,all_legacy);norm=Normalizations()
    try:
        reports=[norm.inspect(ident,force=True) for ident in ids]
        if not yes:output({'preview':reports})
        if not yes and not typer.confirm('Áp dụng lớp chuẩn hóa? Bản gốc giữ nguyên; xem giới hạn trong báo cáo trước.'):
            raise typer.Exit()
        output({'applied':[norm.inspect(ident,apply=True,force=True) for ident in ids]})
    finally:norm.close()


@app.command('snapshot-original')
def snapshot_original(scan_id:list[int]=typer.Option(...,'--id'),yes:bool=typer.Option(False,'--yes')):
    from .normalization import Normalizations
    ids=normalization_ids(scan_id,False)
    if not yes and not typer.confirm('Dùng lại bản gốc cho các lượt đã chọn?'):raise typer.Exit()
    norm=Normalizations()
    try:output(norm.restore(ids))
    finally:norm.close()


@app.command('source-cache-clear')
def source_cache_clear(yes:bool=typer.Option(False,'--yes')):
    from .source_archive import clear
    if not yes and not typer.confirm('Xóa nguồn CSV nén? Giữ lịch sử, nhưng không thể kiểm chứng lại các nguồn đã xóa.'):
        raise typer.Exit()
    output(clear())


@app.command("comparison-delete")
def comparison_delete(pair_id: int):
    from .comparison import Comparisons
    cache=Comparisons()
    try:
        if typer.confirm("Xóa metadata/cache của so sánh này? Không xóa snapshot hay file trên ổ."):
            cache.delete(pair_id); output({"deleted_comparison":pair_id})
    finally: cache.close()


@app.command("comparison-cache-clear")
def comparison_cache_clear():
    from .comparison import Comparisons
    if not typer.confirm("Xóa toàn bộ cache so sánh? Snapshot lịch sử vẫn được giữ và cache sẽ được tạo lại khi cần."):
        raise typer.Exit()
    cache=Comparisons()
    try:
        cache.clear_all();output({"cleared":"comparison-cache","snapshots_preserved":True})
    finally:cache.close()


@app.command("history-clear")
def history_clear():
    from .comparison import Comparisons
    from .store import Store
    store=Store(readonly=True)
    try:
        count=store.db.execute("SELECT COUNT(*) FROM scans").fetchone()[0]
        if store.db.execute("SELECT 1 FROM scans WHERE status='running' LIMIT 1").fetchone():
            raise typer.BadParameter("Có lượt quét đang chạy; dừng lượt đó trước.")
    finally:store.close()
    if not typer.confirm(f"Xóa {count} snapshot, lỗi quét và cache so sánh? Không xóa file thật, cấu hình hay log."):
        raise typer.Exit()
    cache=Comparisons()
    try:cache.clear_all()
    finally:cache.close()
    store=Store()
    try:
        store.clear_scans()
        output({"cleared":"scan-history-and-comparison-cache","snapshots":count,"files_preserved":True})
    finally:store.close()


@app.command("export")
def export(scan_id: int, destination: Path):
    from .store import Store
    if destination.exists():
        raise typer.BadParameter("File đã tồn tại; chọn tên mới để tránh ghi đè")
    s = Store()
    s.export(scan_id, destination)
    s.close()
    output({"exported": destination})


@app.command("doctor")
def doctor():
    from .diagnostics import doctor as check
    output(check())


@app.command("system-check")
def system_check(tool: str):
    from .diagnostics import system_check as check
    result=invoke_action(check,tool,notify=admin_progress);output(result)
    if result.get('cancel_requested'):raise typer.Exit(130)


@app.command('admin-check')
def admin_check():
    from .elevation import probe
    output(invoke_action(probe,notify=admin_progress))


@app.command('uninstall-check',hidden=True)
def uninstall_check():
    """Read-only guard; never remove a registered worker or running app."""
    import os,psutil
    from .background import status
    from .config import home
    from .installation import owns_background
    state=status()
    blocked=bool(state['scan'].get('running') or any(owns_background(kind,state.get(kind,{})) for kind in ('task','service')))
    active=[]
    from .runtime import binary_root
    targets={str(root/name).casefold() for root in (home(),binary_root(),home()/'.dgm-app') for name in ('DiskGrowthMonitor.exe','dgm.exe')}
    for process in psutil.process_iter(['pid','name']):
        if process.pid==os.getpid() or (process.info['name'] or '').casefold() not in ('diskgrowthmonitor.exe','dgm.exe'):continue
        try:
            if process.exe().casefold() in targets:active.append(process.pid)
        except psutil.AccessDenied:blocked=True
        except psutil.NoSuchProcess:pass
    output({'safe_to_uninstall':not blocked and not active,'running_app_pids':active,'background':state,
            'note':'Đóng app/dừng quét, gỡ Task/Service trong app trước khi gỡ cài đặt. Dữ liệu và log sẽ được giữ lại.'})
    if blocked or active:raise typer.Exit(2)


@app.command('upgrade-check',hidden=True)
def upgrade_check():
    from .installation import upgrade_safety
    result=upgrade_safety(); output(result)
    if not result['safe_to_upgrade']: raise typer.Exit(2)


@app_updates.command('check')
def app_update_check():
    from .app_update import check
    output(check())


@app_updates.command('download')
def app_update_download():
    from .app_update import check,download
    release=check(); output({'release':release,'installer':download(release,notify=admin_progress)})


@app_updates.command('install')
def app_update_install(yes: bool = typer.Option(False,'--yes',help='Xác nhận đóng phiên CLI này và chạy bộ cài'),
                       destination: Optional[Path]=None):
    from .app_update import check,download,start_update,install_separately
    if not yes and not typer.confirm('Cập nhật app tại vị trí hiện có, giữ lịch sử/cấu hình và xác nhận UAC?'): raise typer.Abort()
    if destination:
        release=check();target=download(release,notify=admin_progress)
        output(install_separately(release,target,destination))
    else:output(start_update(notify=admin_progress))


@app_updates.command('status')
def app_update_status():
    from .app_update import active_update,pending_recovery
    output({'active':active_update(),'pending_recovery':pending_recovery()})


@app.command('_app-update-stage',hidden=True)
def app_update_stage(session: Path):
    from .app_update import stage
    output(stage(session))


@app.command('_app-update-finish',hidden=True)
def app_update_finish(session: Path, success: bool=False):
    from .app_update import finish
    output(finish(session,success=success))


@app_updates.command('recover')
def app_update_recover(session: Path):
    """Retry recovery from the persisted journal; Admin required for owned runners."""
    from .app_update import finish
    output(finish(session))


@app.command("packages")
def packages():
    from .updates import inventory
    output(inventory())


@app.command("check-updates")
def check_updates_command(package: list[str] = typer.Option([], "--package")):
    from .updates import check_updates
    output(check_updates(package or None))


@app.command("config")
def config_command(key: Optional[str] = typer.Argument(None), value: Optional[str] = typer.Argument(None)):
    from .config import load, save
    cfg = load()
    if key is not None and value is not None:
        if key not in ("debug", "retention_days", "max_snapshots", "database_limit_mb", "threshold_mb"):
            raise typer.BadParameter("Khóa không được sửa bằng lệnh này")
        parsed = json.loads(value)
        if key != "debug" and float(parsed) <= 0:
            raise typer.BadParameter("Giá trị phải >0")
        cfg[key] = parsed
        save(cfg)
    output(cfg)


@app.command("engine-set")
def engine_set(engine: str, executable: Path):
    from .config import load, save
    cfg = load()
    if engine not in cfg["engines"] or not executable.is_file():
        raise typer.BadParameter("Engine hoặc executable không hợp lệ")
    cfg["engines"][engine] = str(executable.resolve())
    save(cfg)
    output(cfg["engines"])


@app.command("logs")
def logs(audit: bool = False, tail: int = 100, level: Optional[str] = None, follow: bool = False):
    from .config import home
    from .log_reader import read_tail,parse_lines,LEVELS
    import time
    if level:level=level.upper()
    if (level and level not in LEVELS) or not 1<=tail<=4000:raise typer.BadParameter('DEBUG/INFO/WARNING/ERROR/CRITICAL; tail 1–4000.')
    path = home() / "logs" / ("audit.jsonl" if audit else "app.log")
    colors={'DEBUG':'cyan','INFO':'white','WARNING':'yellow','ERROR':'red','CRITICAL':'bright_red'}
    def display(rows):
        for severity,line in rows:typer.secho(line,fg=colors[severity])
    offset=0;current='INFO';pending=''
    if path.exists():
        text,offset=read_tail(path);rows,current=parse_lines(text,level,audit);display(rows[-tail:])
    else:
        typer.echo("Chưa có log")
    if follow:
        try:
            while True:
                time.sleep(1)
                if not path.exists():continue
                if path.stat().st_size<offset:offset=0;current='INFO';pending=''
                with path.open('rb') as file:
                    file.seek(offset);pending+=file.read().decode('utf-8',errors='replace');offset=file.tell()
                if '\n' in pending:
                    complete,pending=pending.rsplit('\n',1);rows,current=parse_lines(complete,level,audit,current);display(rows)
        except KeyboardInterrupt:return


@app.command('log-events')
def log_events(source:str='app.log',level:str='',operation:str='',search:str='',anonymize:bool=False):
    from .config import home
    from .log_reader import read_tail,events,redact,LEVELS
    if source not in ('app.log','debug.log','audit.jsonl') or level and level.upper() not in LEVELS:
        raise typer.BadParameter('Chọn app.log/debug.log/audit.jsonl và mức log hợp lệ')
    path=home()/'logs'/source
    rows=events(read_tail(path)[0],audit=source=='audit.jsonl')[-4000:] if path.exists() else []
    rows=[row for row in rows if (not level or row['level']==level.upper()) and (not operation or operation.casefold() in row['operation'].casefold()) and (not search or search.casefold() in row['detail'].casefold())]
    if anonymize:rows=[{key:redact(value) for key,value in row.items()} for row in rows]
    output({'source':source,'events':rows,'counts':{value:sum(row['level']==value for row in rows) for value in LEVELS},'scope':'500 KB cuối / tối đa 4.000 sự kiện; không tự gửi lên web'})


@app.command("snapshot-delete")
def snapshot_delete(scan_id: int):
    from .store import Store
    s = Store()
    row = s.scan(scan_id)
    if typer.confirm(f"Xóa metadata snapshot #{scan_id} ({row['profile']})? Không xóa file đã quét."):
        from .comparison import Comparisons
        cache=Comparisons()
        try:cache.delete_for_scan(scan_id)
        finally:cache.close()
        s.delete_scan(scan_id)
        output({"deleted_snapshot": scan_id})
    s.close()


@app.command("snapshots-delete")
def snapshots_delete(scan_id: Optional[list[int]] = typer.Option(None,"--id",help="ID lượt quét; lặp --id để chọn nhiều"),
                     all_scans: bool = typer.Option(False,"--all",help="Xóa toàn bộ lịch sử quét")):
    from .store import Store
    from .presentation import scan_name
    if all_scans and scan_id:
        raise typer.BadParameter("Dùng --all hoặc --id, không dùng đồng thời.")
    store=Store(readonly=True)
    try:
        rows=store.scans();by_id={row["id"]:row for row in rows}
        ids=sorted(by_id) if all_scans else sorted(set(scan_id or []))
        if not ids:raise typer.BadParameter("Chỉ định --id <ID> (có thể lặp) hoặc --all.")
        missing=set(ids)-by_id.keys()
        if missing:raise typer.BadParameter("Không tìm thấy ID: "+", ".join(map(str,sorted(missing))))
        running=[scan_id for scan_id in ids if by_id[scan_id]["status"]=="running"]
        if running:raise typer.BadParameter("Không xóa lượt đang chạy: "+", ".join(map(str,running)))
        labels=[scan_name(by_id[scan_id]) for scan_id in ids[:8]]
        if len(ids)>8:labels.append(f"… và {len(ids)-8} lượt khác")
    finally:store.close()
    scope="toàn bộ lịch sử" if all_scans else f"{len(ids)} lượt đã chọn"
    typer.echo("\n".join(labels))
    if not typer.confirm(f"Xóa metadata {scope} và cache so sánh phụ thuộc? File/thư mục thật, cấu hình và log được giữ."):
        raise typer.Exit()
    if all_scans and typer.prompt("Nhập XOA LICH SU để tiếp tục")!="XOA LICH SU":
        raise typer.Exit()
    from .comparison import Comparisons
    cache=Comparisons()
    try:cache.delete_for_scans(ids)
    finally:cache.close()
    store=Store()
    try:count=store.delete_scans(ids)
    finally:store.close()
    output({"deleted_snapshots":count,"comparison_cache_removed":True,"files_preserved":True})


@app.command("engine-install")
def engine_install(engine: list[str] = typer.Option(["windirstat", "gdu"], "--engine")):
    from .install_engines import install
    output(install(engine))


@app.command("benchmark")
def benchmark(profile: str = "daily", engine: list[str] = typer.Option(["native", "wiztree"], "--engine"), repeats: int = 3):
    from .config import profile as get_profile
    from .benchmark import benchmark as bench
    if repeats < 1 or repeats > 10:
        raise typer.BadParameter("Số lượt 1–10")
    output(bench(profile, get_profile(profile), engine, repeats))


@cleanup_app.command("rules")
def cleanup_rules():
    from .cleanup import rules
    output(rules())


@cleanup_app.command("plan")
def cleanup_plan(rule: list[str] = typer.Option(..., "--rule"), update_done: bool = False,
                 summary:bool=typer.Option(True,'--summary/--full',help='Mặc định chỉ tóm tắt; cleanup page tải từng trang, --full xuất tất cả metadata.')):
    from .cleanup import plan
    output(invoke_action(plan,rule,update_done,notify=admin_progress,compact=summary))


@cleanup_app.command("show")
def cleanup_show(plan_id: str,summary:bool=typer.Option(True,'--summary/--full')):
    from .cleanup import get_plan
    output(get_plan(plan_id,compact=summary))


@cleanup_app.command('page')
def cleanup_page(plan_id:str,offset:int=0,limit:int=20,sort:str='size',direction:str='desc',rule:str='',search:str=''):
    from .cleanup import plan_page
    output(plan_page(plan_id,offset=offset,limit=limit,sort=sort,direction=direction,rule=rule,search=search))


@cleanup_app.command('protected')
def cleanup_protected():
    from .cleanup_catalog import PROTECTED,REFERENCES
    output({'protected':PROTECTED,'references':REFERENCES,'note':'Không nạp script/rule ngoài hoặc tự xóa các vùng này.'})


@cleanup_app.command('inspect-folder')
def cleanup_inspect_folder(root:Path,mode:str='large',minimum_mb:int=10,reference:list[str]=typer.Option([],'--reference')):
    from .manual_inspection import inspect_folder
    output(inspect_folder(root,mode,min_bytes=minimum_mb*1_000_000,reference=reference))


@cleanup_app.command("list")
def cleanup_list():
    from .store import Store
    s = Store()
    output([dict(x) for x in s.db.execute("SELECT id,created,status FROM plans ORDER BY created DESC")])
    s.close()


@cleanup_app.command('prune')
def cleanup_prune():
    from .store import Store
    from .cleanup_storage import prune
    store=Store()
    try:prune(store)
    finally:store.close()
    output({'pruned':'expired-plan-metadata','user_files_preserved':True})


@cleanup_app.command("select")
def cleanup_select(plan_id: str, path: list[str] = typer.Option(..., "--path")):
    from .cleanup import select
    output(select(plan_id, path))


@cleanup_app.command("apply")
def cleanup_apply(plan_id: str, confirm: Optional[str] = None):
    from .cleanup import apply, get_plan
    p = get_plan(plan_id,compact=True)
    if confirm is None:
        from .presentation import size
        typer.echo(f"{p.get('entry_count',len(p['entries']))} mục; {size(p['bytes'])}. Cache được xóa vĩnh viễn; một số tool xử lý toàn bộ cache như ghi trong rule.")
        confirm = typer.prompt(f"Gõ chính xác {p['confirmation']}")
    result=invoke_action(apply,plan_id,confirm,notify=admin_progress);output(result)
    if result.get('cancelled'):raise typer.Exit(130)


@schedules.command("list")
def schedule_list():
    from .schedule import listing
    output(listing())


@schedules.command("add")
def schedule_add(profile: str = "daily", mode: str = "daily", interval: int = 1,
                 times: str = "09:00", weekdays: str = "0", cleanup_rule: list[str] = typer.Option([], "--cleanup-rule"),name:Optional[str]=None):
    from .schedule import add
    output(add({"profile": profile, "mode": mode, "interval": interval, "times": times.split(","),
                "weekdays": [int(x) for x in weekdays.split(",")], "cleanup_rules": cleanup_rule,'name':name}))


@schedules.command('edit')
def schedule_edit(schedule_id:str,name:Optional[str]=None,profile:Optional[str]=None,mode:Optional[str]=None,
                  interval:Optional[int]=None,times:Optional[str]=None,weekdays:Optional[str]=None):
    from .schedule import update
    changes={key:value for key,value in {'name':name,'profile':profile,'mode':mode,'interval':interval}.items() if value is not None}
    if times is not None:changes['times']=times.split(',')
    if weekdays is not None:changes['weekdays']=[int(value) for value in weekdays.split(',')]
    output(update(schedule_id,changes))


@schedules.command("preview")
def schedule_preview(schedule_id: str, count: int = 10):
    from .config import load
    from .schedule import preview
    spec = next((x for x in load()["schedules"] if x["id"] == schedule_id), None)
    if not spec:
        raise typer.BadParameter("Không có lịch")
    output(preview(spec, count))


@schedules.command("enable")
def schedule_enable(schedule_id: str, enabled: bool = True):
    from .schedule import modify
    modify(schedule_id, enabled)
    output({"id": schedule_id, "enabled": enabled})


@schedules.command("remove")
def schedule_remove(schedule_id: str):
    from .schedule import modify
    modify(schedule_id, delete=True)
    output({"removed": schedule_id})


@schedules.command("task")
def schedule_task(action: str):
    from .background import control
    output(invoke_action(control,'task',action,notify=admin_progress))


@schedules.command("service")
def schedule_service(action: str):
    from .background import control
    output(invoke_action(control,'service',action,notify=admin_progress))


@app.command("background-status")
def background_status(watch: bool=False, interval: float=3):
    from .background import status
    import time
    if interval<1: raise typer.BadParameter('Chu kỳ kiểm tra tối thiểu 1 giây')
    try:
        while True:
            output(status())
            if not watch:break
            time.sleep(interval)
    except KeyboardInterrupt:pass


@app.command('background-open')
def background_open(kind: str):
    from .background import open_manager
    output(open_manager(kind))


@app.command("background-control")
def background_control(kind: str, action: str):
    from .background import control
    output(invoke_action(control,kind,action,notify=admin_progress))


@schedules.command("tick")
def schedule_tick():
    from .schedule import tick
    output(tick())


@schedules.command("run")
def schedule_run(schedule_id: str):
    from .schedule import run_now
    output(run_now(schedule_id))


@schedules.command("agent")
def schedule_agent():
    from .schedule import agent
    try:
        agent()
    except KeyboardInterrupt:
        pass


@files.command("action")
def file_action(path: Path, action: str, name: Optional[str] = None):
    from .platform_ops import manual_action
    if action in ("trash", "rename") and not typer.confirm(f"{action}: {path}" + (f" → {name}" if name else "")):
        raise typer.Abort()
    manual_action(path, action, name)
    output({"path": path, "action": action, "note": "Trash trên cùng ổ chưa giải phóng dung lượng" if action == "trash" else ""})


@ai.command("question")
def ai_question(scan_id: Optional[int] = None, plan_id: Optional[str] = None, anonymize: bool = True,
                copy: bool = False, provider: Optional[str] = None):
    from .report import ai_question, open_provider
    text = ai_question(scan_id, plan_id, anonymize)
    if copy:
        import win32clipboard
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardText(text, win32clipboard.CF_UNICODETEXT)
        finally:
            win32clipboard.CloseClipboard()
    typer.echo(text)
    if provider:
        output(open_provider(provider))


@app.command("gui")
def gui(tray: bool = False,smoke_test:bool=typer.Option(False,hidden=True)):
    from .gui import start
    result=start(tray=tray,smoke_test=smoke_test)
    if smoke_test:output(result)


@app.command('_elevated-worker',hidden=True)
def elevated_worker(ticket:str=typer.Option(...,'--ticket'),digest:str=typer.Option(...,'--digest')):
    from .elevation import worker_main
    raise typer.Exit(worker_main(ticket,digest))


def main():
    import sys
    from .startup_admin import preflight
    preflight()
    from .elevation import ElevationCancelled
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    try:
        app()
    except ElevationCancelled as exc:
        typer.echo(str(exc),err=True);raise SystemExit(130)
    except (ValueError, RuntimeError, OSError) as exc:
        typer.echo(str(exc), err=True)
        raise SystemExit(1)
