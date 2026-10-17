"""Durable, allowlisted work checkpoints; never replay log strings or deletions."""
from contextvars import ContextVar
from functools import wraps
import hashlib
import inspect
import json
import os
from pathlib import Path
import re
import threading
import time
import uuid

from .config import home
from .platform_ops import ProcessCancelled

CURRENT = ContextVar('dgm_task_checkpoint', default=None)
SCHEMA = 1
ACTIONS = {'scan', 'comparison', 'normalization', 'benchmark'}
LABELS = {'scan': 'Quét', 'comparison': 'So sánh', 'normalization': 'Chuẩn hóa', 'benchmark': 'Benchmark'}


def folder():
    from .storage_maintenance import safe
    path = safe(home() / 'data/tasks')
    path.mkdir(exist_ok=True)
    return path


def path_for(ident):
    from .storage_maintenance import safe
    if not re.fullmatch(r'[0-9a-f]{32}', str(ident)):
        raise ValueError('Mã tác vụ không hợp lệ')
    return safe(folder() / ident)


def _alive(row):
    from .elevation import alive
    return alive(row.get('owner') or {}) if row.get('owner') else False


def read(ident):
    from .storage_maintenance import safe
    path=safe(path_for(ident) / 'task.json')
    if path.stat().st_size>4_000_000:raise ValueError('Checkpoint metadata quá lớn')
    row = json.loads(path.read_text(encoding='utf-8'))
    if row.get('schema') != SCHEMA or row.get('id') != ident or row.get('action') not in ACTIONS:
        raise ValueError('Checkpoint không được hỗ trợ; không thực thi')
    if row.get('state') not in ('running','interrupted','complete') or not isinstance(row.get('payload'),dict):
        raise ValueError('Trạng thái/payload checkpoint không hợp lệ')
    if row.get('state') == 'running' and not _alive(row):
        row = {**row, 'state': 'interrupted', 'detail': 'Tiến trình đã kết thúc ngoài dự kiến; kiểm tra điểm lưu trước khi tiếp tục.'}
    return row


def list_tasks(include_complete=False):
    result = []
    for path in folder().iterdir():
        if not re.fullmatch(r'[0-9a-f]{32}', path.name):
            continue
        try:
            row = read(path.name)
            if include_complete or row['state'] != 'complete':
                result.append(row)
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return sorted(result, key=lambda r: r.get('updated', 0), reverse=True)


class Journal:
    def __init__(self, action=None, payload=None, ident=None):
        from . import __version__
        from .elevation import owner
        self.root = path_for(ident or uuid.uuid4().hex)
        if ident:
            self.row = read(ident)
        else:
            if action not in ACTIONS:raise ValueError('Tác vụ không cho phép tiếp tục')
            # Completed records are small; bound their retention, never discard unfinished work.
            old = list_tasks(True)
            for item in [r for r in old if r['state']=='complete']:
                try:_remove_artifacts(item)
                except OSError:pass
            for item in [r for r in old if r['state'] == 'complete'][100:]:
                discard(item['id'])
            if sum(r['state'] != 'complete' for r in old) >= 100:
                raise RuntimeError('Có 100 tác vụ dang dở; xem/bỏ checkpoint không cần trước khi tạo tác vụ mới.')
            self.root.mkdir(exist_ok=False)
            self.row = {'schema': SCHEMA, 'id': self.root.name, 'action': action, 'payload': payload,
                        'app_version': __version__, 'created': time.time(), 'percent': 0,
                        'stage': 'Chuẩn bị', 'detail': '', 'completed': [], 'state': 'running'}
        self.row.update(state='running', owner=owner(), updated=time.time(), error=None)
        self.last_write = 0
        self.save()

    def save(self, **values):
        self.row.update(values, updated=time.time())
        path = self.root / 'task.json'
        temporary = self.root / ('task.' + uuid.uuid4().hex + '.tmp')
        try:
            encoded=json.dumps(self.row, ensure_ascii=False, allow_nan=False).encode('utf-8')
            if len(encoded)>4_000_000:raise ValueError('Checkpoint JSON vượt 4 MB; giảm số lượt benchmark.')
            with temporary.open('xb') as stream:
                stream.write(encoded)
                stream.flush(); os.fsync(stream.fileno())
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        self.last_write = time.monotonic()

    def notify(self, external):
        def forward(event, data):
            if event == 'task-progress':
                self.row.update(percent=data.get('percent', 0), stage=data.get('stage', ''),
                                detail=data.get('detail', ''), completed=data.get('completed', []))
                if time.monotonic() - self.last_write > .8:
                    self.save()
            external(event, data)
        return forward

    def artifact(self, name):
        from .storage_maintenance import safe
        if not re.fullmatch(r'(capture\.jsonl\.gz|source-\d{3}\.csv)', name):
            raise ValueError('Tên tệp checkpoint không hợp lệ')
        return safe(self.root / name)

    def budget(self, extra=0):
        from .config import load
        from .storage_safety import SpaceGuard
        SpaceGuard(extra, operation='lưu điểm tiếp tục').check()
        limit = int(load().get('resume_limit_mb', 512)) * 1_000_000
        used = sum(p.stat().st_size for directory in folder().iterdir() if re.fullmatch('[0-9a-f]{32}', directory.name)
                   for p in directory.iterdir() if p.is_file() and not p.is_symlink())
        if used + extra > limit:
            raise RuntimeError('Điểm tiếp tục đạt hạn mức lưu; bỏ checkpoint không cần hoặc tăng resume_limit_mb.')

    def source(self, index):
        entry = self.row.get('sources', {}).get(str(index))
        if not entry:
            return None
        path = self.artifact(entry['file'])
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if path.stat().st_size != entry['bytes'] or digest != entry['sha256']:
            raise ValueError('Tệp xuất checkpoint hỏng/đã đổi; chọn Quét mới, không trộn dữ liệu.')
        return path

    def adopt_source(self, index, source, volume_id=None):
        self.budget(source.stat().st_size)
        destination = self.artifact(f'source-{index:03d}.csv')
        source.replace(destination)
        with destination.open('rb') as stream:
            sha = hashlib.file_digest(stream, 'sha256').hexdigest()
        sources = self.row.setdefault('sources', {})
        sources[str(index)] = {'file': destination.name, 'bytes': destination.stat().st_size, 'sha256': sha, 'volume_id':volume_id}
        self.save(sources=sources)
        return destination

    def capture(self, store, scan_id, summary, name, profile, stop,notify=None):
        from . import history_logs
        if summary.get('files',0)+summary.get('folders',0)>0 and not store.db.execute('SELECT 1 FROM entries WHERE scan_id=? LIMIT 1',(scan_id,)).fetchone():
            raise ValueError('Metadata chưa được nạp; không lưu checkpoint rỗng cho một mốc có dữ liệu.')
        self.budget()
        fields = ','.join('e.' + key for key in history_logs.FIELDS)
        def records():
            for row in store.db.execute(f'SELECT {fields} FROM entries e WHERE scan_id=?', (scan_id,)):
                yield list(row)
            for row in store.db.execute('SELECT path,message FROM errors WHERE scan_id=?', (scan_id,)):
                yield {'error': list(row)}
        scan = dict(store.db.execute('SELECT * FROM scans WHERE id=?', (scan_id,)).fetchone())
        report = history_logs.write(self.artifact('capture.jsonl.gz'),
            {'columns': history_logs.FIELDS, 'profile': profile, 'name': name, 'summary': summary,
             'captured_at': scan['started'], 'scan_id': scan_id}, records(), stop=stop,notify=notify)
        with self.artifact('capture.jsonl.gz').open('rb') as stream:
            report['artifact_sha256']=hashlib.file_digest(stream,'sha256').hexdigest()
        self.budget()
        self.save(capture=report, captured_at=scan['started'], source_scan=scan_id)
        self.remove_sources()

    def remove_sources(self):
        for item in self.row.get('sources', {}).values():
            self.artifact(item['file']).unlink(missing_ok=True)
        self.save(sources={})

    def finish(self, result=None):
        if isinstance(result, dict) and result.get('status') in ('failed', 'cancelled'):
            self.save(state='interrupted', owner=None, error=result.get('error'), result=result.get('scan_id'))
            return
        self.save(state='complete', owner=None, percent=100,
                  result=result.get('scan_id') if isinstance(result, dict) else result if isinstance(result, int) else None)
        for name in ['capture.jsonl.gz', *[v['file'] for v in self.row.get('sources', {}).values()]]:
            self.artifact(name).unlink(missing_ok=True)
        self.save(capture=None, sources={})


def tracked(action, names, notify='notify'):
    def decorate(fn):
        signature = inspect.signature(fn)
        @wraps(fn)
        def wrapped(*args, **kwargs):
            if CURRENT.get() is not None:
                return fn(*args, **kwargs)
            bound = signature.bind(*args, **kwargs); bound.apply_defaults()
            if action=='scan':
                from .scanner import is_admin
                profile=bound.arguments['p']
                # The elevated child owns its journal. Do not create a second
                # pending parent task that lacks the child's captured data.
                if profile.get('engine')=='wiztree' and profile.get('mft') and not is_admin():
                    return fn(*bound.args,**bound.kwargs)
            journal = Journal(action, {key: bound.arguments[key] for key in names})
            token = CURRENT.set(journal)
            external = bound.arguments.get(notify) or (lambda *a: None)
            method_owner = bound.arguments.get('self')
            old_notify = getattr(method_owner, 'notify', None)
            if notify in signature.parameters:
                bound.arguments[notify] = journal.notify(external)
            else:
                external = old_notify or external
                method_owner.notify = journal.notify(external)
            try:
                result = fn(*bound.args, **bound.kwargs)
                cancelled = bound.arguments.get('cancel')
                if cancelled is not None and cancelled.is_set():
                    journal.save(state='interrupted', owner=None)
                else:
                    journal.finish(result)
                return result
            except BaseException as exc:
                journal.save(state='interrupted', owner=None, error=str(exc))
                raise
            finally:
                if method_owner is not None and old_notify is not None:
                    method_owner.notify = old_notify
                CURRENT.reset(token)
        return wrapped
    return decorate


def discard(ident):
    from .history_index import mutex
    with mutex():return _discard(ident)


def _discard(ident):
    row = read(ident)
    if row['state'] == 'running' and _alive(row):
        raise RuntimeError('Không thể bỏ điểm lưu của tác vụ đang chạy')
    # Explicit owned files only; never recursively remove a user-supplied path.
    _remove_artifacts(row)
    journal = object.__new__(Journal); journal.root = path_for(ident); journal.row = row
    from .storage_maintenance import safe
    safe(journal.root / 'task.json').unlink()
    try:
        journal.root.rmdir()
    except OSError:
        pass


def _remove_artifacts(row):
    from .storage_maintenance import safe
    journal=object.__new__(Journal);journal.root=path_for(row['id']);journal.row=row
    for name in ['capture.jsonl.gz', *[x['file'] for x in row.get('sources', {}).values()]]:
        journal.artifact(name).unlink(missing_ok=True)
    for path in journal.root.iterdir():
        if re.fullmatch(r'(capture\.jsonl\.gz\.[0-9a-f]{32}\.part|task\.[0-9a-f]{32}\.tmp)',path.name):
            safe(path).unlink(missing_ok=True)


def resume(ident, stop=None, notify=None):
    from .history_index import mutex
    stop = stop or threading.Event(); external = notify or (lambda *a: None)
    with mutex():
        row = read(ident)
        if row['state'] == 'complete':
            raise ValueError('Tác vụ đã hoàn tất')
        if row['state'] == 'running' and _alive(row):
            raise RuntimeError('Tác vụ vẫn đang chạy')
        journal = Journal(ident=ident)
    token = CURRENT.set(journal); forward = journal.notify(external)
    handed_off=False
    try:
        p = row['payload']; action = row['action']
        if not isinstance(p, dict):
            raise ValueError('Payload checkpoint không hợp lệ')
        if action == 'scan':
            from .scanner import scan,is_admin
            if set(p) != {'name', 'p'}: raise ValueError('Payload quét không hợp lệ')
            source_count=len(p['p'].get('roots',[]));sources=row.get('sources') or {}
            complete=bool(row.get('capture') or source_count and all(str(i) in sources for i in range(source_count)))
            handed_off=p['p'].get('engine')=='wiztree' and p['p'].get('mft') and not complete and not is_admin()
            if handed_off:
                temporary=CURRENT.set(None)
                try:result=scan(p['name'],p['p'],stop=stop,notify=forward)
                finally:CURRENT.reset(temporary)
            else:result = scan(p['name'], p['p'], stop=stop, notify=forward)
        elif action == 'comparison':
            from .comparison import Comparisons
            if set(p) != {'older', 'newer', 'common'}: raise ValueError('Payload so sánh không hợp lệ')
            cache = Comparisons(stop, forward)
            try: result = cache.prepare(**p)
            finally: cache.close()
        elif action == 'normalization':
            from .normalization import Normalizations
            if set(p) != {'scan_id', 'apply', 'force'}: raise ValueError('Payload chuẩn hóa không hợp lệ')
            normal = Normalizations(stop, forward)
            try: result = normal.inspect(**p)
            finally: normal.close()
        else:
            from .benchmark import benchmark
            if set(p) != {'name', 'p', 'engines', 'repeats'}: raise ValueError('Payload benchmark không hợp lệ')
            result = benchmark(**p, cancel=stop, notify=forward)
        if handed_off and not result.get('before_start'):journal.finish()
        elif stop.is_set() and action=='benchmark':journal.save(state='interrupted',owner=None)
        else:journal.finish(result)
        return result
    except BaseException as exc:
        journal.save(state='interrupted', owner=None, error=str(exc))
        raise
    finally:
        CURRENT.reset(token)
