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
app.add_typer(profiles, name="profile")
app.add_typer(cleanup_app, name="cleanup")
app.add_typer(schedules, name="schedule")
app.add_typer(files, name="file")
app.add_typer(ai, name="ai")


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
                engine: str = "wiztree", strict_browsers: bool = True, threshold_mb: float = 10, mft: bool = False):
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
            cursor: Optional[str] = None, baseline: bool = False):
    from .store import Store
    from .comparison import Comparisons
    s = Store(readonly=True)
    try: previous = None if baseline else older if older is not None else s.previous(newer)
    finally: s.close()
    if offset < 0 or minimum_mb < 0 or (offset and cursor):
        raise typer.BadParameter("Offset/ngưỡng >=0; dùng cursor hoặc offset, không đồng thời.")
    cache = Comparisons()
    try:
        pair = cache.prepare(previous,newer)
        position=json.loads(cursor) if cursor else None
        options=dict(sort=sort,direction=direction,minimum=int(minimum_mb*1_000_000),kind=kind,changes=changes,search=search,root=root,recursive=recursive)
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
def overview(newer: int, older: Optional[int] = None):
    from .store import Store
    from .comparison import Comparisons
    store=Store(readonly=True)
    try: previous=older if older is not None else store.previous(newer)
    finally: store.close()
    cache=Comparisons()
    try: output(cache.overview(previous,newer))
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


@app.command("comparison-delete")
def comparison_delete(pair_id: int):
    from .comparison import Comparisons
    cache=Comparisons()
    try:
        if typer.confirm("Xóa metadata/cache của so sánh này? Không xóa snapshot hay file trên ổ."):
            cache.delete(pair_id); output({"deleted_comparison":pair_id})
    finally: cache.close()


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
    targets={str(home()/name).casefold() for name in ('DiskGrowthMonitor.exe','dgm.exe')}
    for process in psutil.process_iter(['pid','name']):
        if process.pid==os.getpid() or (process.info['name'] or '').casefold() not in ('diskgrowthmonitor.exe','dgm.exe'):continue
        try:
            if process.exe().casefold() in targets:active.append(process.pid)
        except psutil.AccessDenied:blocked=True
        except psutil.NoSuchProcess:pass
    output({'safe_to_uninstall':not blocked and not active,'running_app_pids':active,'background':state,
            'note':'Đóng app/dừng quét, gỡ Task/Service trong app trước khi gỡ cài đặt. Dữ liệu và log sẽ được giữ lại.'})
    if blocked or active:raise typer.Exit(2)


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


@app.command("snapshot-delete")
def snapshot_delete(scan_id: int):
    from .store import Store
    s = Store()
    row = s.scan(scan_id)
    if typer.confirm(f"Xóa metadata snapshot #{scan_id} ({row['profile']})? Không xóa file đã quét."):
        s.db.execute("DELETE FROM scans WHERE id=?", (scan_id,)); s.db.commit()
        output({"deleted_snapshot": scan_id})
    s.close()


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
def cleanup_plan(rule: list[str] = typer.Option(..., "--rule"), update_done: bool = False):
    from .cleanup import plan
    output(invoke_action(plan,rule,update_done,notify=admin_progress))


@cleanup_app.command("show")
def cleanup_show(plan_id: str):
    from .cleanup import get_plan
    output(get_plan(plan_id))


@cleanup_app.command("list")
def cleanup_list():
    from .store import Store
    s = Store()
    output([dict(x) for x in s.db.execute("SELECT id,created,status FROM plans ORDER BY created DESC")])
    s.close()


@cleanup_app.command("select")
def cleanup_select(plan_id: str, path: list[str] = typer.Option(..., "--path")):
    from .cleanup import select
    output(select(plan_id, path))


@cleanup_app.command("apply")
def cleanup_apply(plan_id: str, confirm: Optional[str] = None):
    from .cleanup import apply, get_plan
    p = get_plan(plan_id)
    if confirm is None:
        typer.echo(f"{len(p['entries'])} mục; {p['bytes'] / 1e9:.3f} GB. Cache được xóa vĩnh viễn.")
        confirm = typer.prompt(f"Gõ chính xác {p['confirmation']}")
    result=invoke_action(apply,plan_id,confirm,notify=admin_progress);output(result)
    if result.get('cancelled'):raise typer.Exit(130)


@schedules.command("list")
def schedule_list():
    from .schedule import listing
    output(listing())


@schedules.command("add")
def schedule_add(profile: str = "daily", mode: str = "daily", interval: int = 1,
                 times: str = "09:00", weekdays: str = "0", cleanup_rule: list[str] = typer.Option([], "--cleanup-rule")):
    from .schedule import add
    output(add({"profile": profile, "mode": mode, "interval": interval, "times": times.split(","),
                "weekdays": [int(x) for x in weekdays.split(",")], "cleanup_rules": cleanup_rule}))


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
def background_status():
    from .background import status
    output(status())


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
