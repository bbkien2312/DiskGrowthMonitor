"""Opt-in CLI; every command uses the same repository/query service as its GUI."""
from pathlib import Path
from typing import Optional
import json
import typer

app = typer.Typer(help='Lõi Parquet thử nghiệm riêng; không thay lịch sử/bản cài cũ', no_args_is_help=True)


def output(value):
    typer.echo(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def progress(event, data):
    typer.echo(f'[{event.upper()}] ' + (str(data.get('percent', '')) + '% ' if 'percent' in data else '') + data.get('message', ''), err=True)


@app.command('history')
def history():
    from .columnar_store import Repository
    output(Repository().list())


@app.command('import-legacy')
def legacy(source_home: Path, ids: list[int] = typer.Option(..., '--id')):
    from .columnar_jobs import Jobs
    jobs = Jobs()
    job = jobs.create('legacy', {'source_home': str(source_home.absolute()), 'ids': ids})
    output(jobs.execute(job['id'], notify=progress))


@app.command('import-csv')
def csv(source: Path, root: list[str] = typer.Option(..., '--root'), timezone: str = typer.Option(..., '--timezone'),
        mft: bool = typer.Option(False,'--mft',help='Chỉ bật nếu CSV thật sự được quét MFT admin; walk có ID giả')):
    from .columnar_jobs import Jobs
    jobs = Jobs()
    job = jobs.create('csv', {'source': str(source.absolute()), 'roots': root, 'timezone': timezone,'mft':mft})
    output(jobs.execute(job['id'], notify=progress))


@app.command('scan')
def scan(profile: str = 'daily', engine: Optional[str] = None,
         root: list[str] = typer.Option([], '--root'),
         exclude: list[str] = typer.Option([], '--exclude'),
         mft: Optional[bool] = typer.Option(None, '--mft/--no-mft'),
         strict_browsers: Optional[bool] = typer.Option(None, '--strict-browsers/--include-browsers')):
    from .config import load
    from .columnar_jobs import Jobs
    cfg = load()
    if profile not in cfg['profiles']:
        raise typer.BadParameter('Không có profile ' + profile)
    p = dict(cfg['profiles'][profile])
    if engine:
        p['engine'] = engine
    if root:p['roots'] = root
    if exclude:p['excludes'] = exclude
    if mft is not None:p['mft'] = mft
    if strict_browsers is not None:p['strict_browsers'] = strict_browsers
    jobs = Jobs()
    job = jobs.create('scan', {'name': profile, 'profile': p, 'engines': cfg['engines']})
    output(jobs.execute(job['id'], notify=progress))


@app.command('configure')
def configure(engine: str, executable: Path):
    from .config import load,save
    if engine not in ('wiztree','gdu','windirstat') or not executable.is_file():
        raise typer.BadParameter('Engine hoặc executable không hợp lệ')
    cfg=load();cfg['engines'][engine]=str(executable.absolute());save(cfg)
    output({'engine':engine,'executable':cfg['engines'][engine]})


@app.command('compare')
def compare(newer: int, older: Optional[int] = typer.Argument(None), limit: int = 20,
            sort: str = 'abs_delta', direction: str = 'desc', root: Optional[str] = None,
            section: str = 'all', changes: str = 'all', kind: str = 'all', minimum: int = 0,
            search: str = '', offset: int = 0, measure: str = 'allocated'):
    from .columnar_compare import Comparison
    import base64
    if offset < 0:
        raise typer.BadParameter('Offset không âm')
    with Comparison(newer, older, notify=progress) as query:
        options = dict(sort=sort, direction=direction, root=root, section=section,
                       changes=changes, kind=kind, minimum=minimum, search=search, measure=measure)
        cursor = None
        if offset:
            # CLI is a fresh process/session. Create a session-bound cursor from
            # the requested offset, with exactly the same validated filters.
            normalized = dict(**options, recursive=True, presence='all', folders_only=False, root_keys=None)
            cursor = base64.urlsafe_b64encode(json.dumps({'offset': offset, 'signature': query._fingerprint(normalized)}).encode()).decode()
        output(query.page(limit=limit, cursor=cursor, **options))


@app.command('tree')
def tree(newer: int, older: Optional[int] = typer.Argument(None), root: Optional[str] = None, limit: int = 100, measure: str = 'allocated'):
    from .columnar_compare import Comparison
    with Comparison(newer, older, notify=progress) as query:
        output(query.tree(root, limit=limit, measure=measure))


@app.command('storage')
def storage():
    from .columnar_jobs import Jobs
    jobs = Jobs()
    output({**jobs.repo.footprint(), 'job_source_bytes': jobs.source_bytes()})


@app.command('jobs')
def jobs():
    from .columnar_jobs import Jobs
    output(Jobs().list())


@app.command('clear-temp')
def clear_temp():
    from .columnar_store import Repository
    output(Repository().cleanup_abandoned())


@app.command('resume')
def resume(ident: str):
    from .columnar_jobs import Jobs
    output(Jobs().execute(ident, notify=progress))


@app.command('discard-job')
def discard_job(ident: str, yes: bool = typer.Option(False, '--yes')):
    from .columnar_jobs import Jobs
    if yes or typer.confirm('Bỏ journal/export của tác vụ thử nghiệm? Snapshot và nguồn ngoài giữ nguyên.'):
        output(Jobs().discard(ident))


@app.command('delete')
def delete(ids: list[int] = typer.Argument(...), yes: bool = typer.Option(False, '--yes')):
    from .columnar_store import Repository
    if yes or typer.confirm(f'Xóa các mốc Parquet {ids}? Không xóa lịch sử JSONL gốc; không thể hoàn tác bản Parquet.'):
        output(Repository().delete(ids))


@app.command('gui')
def gui():
    from .columnar_ui import start
    start()
