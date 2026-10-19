"""Small restart journals for the opt-in core; no production history mutations."""
from __future__ import annotations

from pathlib import Path
import shutil
import threading
import uuid
from contextlib import nullcontext

from .columnar_store import Repository, atomic_json, read_json, safe_path, owner, alive, sha256, utc_now
from .columnar_ingest import metadata_for, import_entries, import_wiztree, import_legacy
from .platform_ops import ProcessCancelled, run, is_admin


class Jobs:
    def __init__(self, repository=None):
        self.repo = repository or Repository()
        self.root = safe_path(self.repo.root / 'jobs', beneath=self.repo.root)
        self.root.mkdir(exist_ok=True)

    def directory(self, ident):
        if not isinstance(ident, str) or len(ident) != 32 or any(c not in '0123456789abcdef' for c in ident):
            raise ValueError('ID tác vụ không hợp lệ')
        return safe_path(self.root / ident, beneath=self.root)

    def get(self, ident):
        value = read_json(self.directory(ident) / 'job.json')
        if not value or value.get('id') != ident or value.get('operation') not in ('scan', 'legacy', 'csv'):
            raise ValueError('Không nhận dạng được tác vụ thử nghiệm')
        return value

    def list(self):
        result = []
        for path in self.root.glob('*/job.json'):
            value = self.get(path.parent.name)
            value['running'] = value.get('state') == 'running' and alive(value.get('owner') or {})
            if value['state'] == 'running' and not value['running']:
                value['state'] = 'interrupted'
            result.append(value)
        return sorted(result, key=lambda r: r['created'], reverse=True)

    def create(self, operation, arguments):
        if operation not in ('scan', 'legacy', 'csv'):
            raise ValueError('Loại tác vụ không hỗ trợ')
        ident = uuid.uuid4().hex
        directory = self.directory(ident)
        directory.mkdir()
        value = {'id': ident, 'operation': operation, 'arguments': arguments,
                 'created': utc_now(), 'updated': utc_now(), 'state': 'pending', 'sources': []}
        atomic_json(directory / 'job.json', value)
        return value

    def source_bytes(self):
        return sum(safe_path(p, beneath=self.root).stat().st_size for p in self.root.glob('*/*') if p.is_file() and p.name != 'job.json')

    def _save(self, value):
        value['updated'] = utc_now()
        atomic_json(self.directory(value['id']) / 'job.json', value)

    def execute(self, ident, *, stop=None, notify=None):
        stop = stop or threading.Event()
        notify = notify or (lambda *args: None)
        with self.repo.locked():
            value = self.get(ident)
            if value['state'] == 'complete':
                return [self.repo.get(i) for i in value['snapshots']]
            if any(row['running'] for row in self.list()):
                raise RuntimeError('Một tác vụ thử nghiệm đang chạy; dừng tác vụ đó trước')
            value.update(state='running', owner=owner(), error=None)
            self._save(value)
        try:
            from .operation_progress import Timeline
            timeline=Timeline('scan' if value['operation']=='scan' else 'import',notify,ident=ident,finish_on_done=False)
            notify=timeline
            notify('progress',{'percent':0,'stage':'prepare','message':'Chuẩn bị tác vụ và kiểm tra phạm vi/quyền'})
            args = value['arguments']
            if value['operation'] == 'legacy':
                results = import_legacy(args['source_home'], args['ids'], repository=self.repo, stop=stop, notify=notify)
            elif value['operation'] == 'csv':
                metadata = metadata_for(args['roots'], engine='wiztree', scope=args.get('scope'),mft=args.get('mft',False))
                results = [import_wiztree(args['source'], metadata, timezone_name=args['timezone'], repository=self.repo, stop=stop, notify=notify)]
            else:
                from .scanner import ScanLock
                with ScanLock(stop) if getattr(self.repo, 'production', False) else nullcontext():
                    results = [self._scan(value, stop, notify)]
            timing=timeline.finish('complete',message='Đã lưu metadata và hoàn tất tác vụ')
            value.update(state='complete', snapshots=[r['id'] for r in results], owner=None,timing=timing)
            self._save(value)
            # Only tool outputs created inside this exact job are disposable.
            for source in value['sources']:
                path = safe_path(self.directory(ident) / source['file'], beneath=self.directory(ident))
                if path.name not in ('job.json',) and path.is_file():
                    path.unlink()
            return results
        except BaseException as exc:
            if 'timeline' in locals():value['timing']=timeline.finish('cancelled' if isinstance(exc,(ProcessCancelled,KeyboardInterrupt)) else 'failed')
            value.update(state='stopped' if isinstance(exc, (ProcessCancelled, KeyboardInterrupt)) else 'failed',
                         error=str(exc) or type(exc).__name__, owner=None)
            self._save(value)
            raise

    def _scan(self, job, stop, notify):
        from .config import Policy, volume_identity, home
        profile = job['arguments']['profile']
        engine = profile.get('engine', 'wiztree')
        if engine not in ('wiztree', 'native', 'gdu', 'windirstat'):
            raise ValueError('Engine không hỗ trợ')
        policy = Policy(profile)
        if not policy.roots:
            raise ValueError('Cần ít nhất một phạm vi')
        if engine != 'native' and profile.get('strict_browsers'):
            raise ValueError('Chế độ không đọc Chrome/Edge phải dùng Native; engine ngoài không bảo đảm loại trừ trước khi đọc')
        if engine == 'wiztree' and profile.get('mft', True) and not is_admin():
            raise ValueError('MFT cần Admin. Mở app để nhận UAC, hoặc dùng CLI trong terminal Admin')
        directory = self.directory(job['id'])
        if not job.get('metadata'):
            volume_map = {Path(root).anchor.lower().rstrip('\\'): volume_identity(root) for root in policy.roots}
            scope = {'roots': policy.roots, 'excludes': list(profile.get('excludes') or []),
                     'strict_browsers': profile.get('strict_browsers', False), 'implicit_excludes': [str(home())],
                     'volumes': sorted(set(volume_map.values()))}
            job['metadata'] = metadata_for(policy.roots, engine=engine, profile=job['arguments'].get('name', 'preview'),
                scope=scope, volume_map=volume_map, mft=engine == 'wiztree' and profile.get('mft', True))
            self._save(job)
        metadata = job['metadata']
        metadata['source_fingerprint'] = 'job:' + job['id']
        existing = next((r for r in self.repo.list() if r.get('source_fingerprint') == metadata['source_fingerprint']), None)
        if existing:
            self.repo.verify(existing)
            return existing
        if not job.get('sources'):
            metadata['started'] = utc_now()
        if not job.get('sources'):
            from .disk_measurements import read_volume_usage
            metadata['volume_snapshots'] = read_volume_usage(policy.roots)
        if engine == 'native':
            # An interrupted traversal starts afresh. Do not combine metadata
            # from two live traversals or claim that an incomplete capture is valid.
            from .scanner import native_entries
            metadata['started'] = utc_now()
            metadata['errors'] = []
            metadata['coverage'] = {'source': 'native', 'unknown_roots': [], 'error_regions': []}
            class ErrorSink:
                def error(self, _, path, message):
                    metadata['errors'].append({'path': str(path), 'message': message})
                    metadata['coverage']['error_regions'].append(str(path))
            return import_entries(native_entries(policy, ErrorSink(), 0, stop, notify), metadata,
                                  repository=self.repo, stop=stop, notify=notify)
        sources = []
        exe = Path(job['arguments']['engines'].get(engine) or '')
        if not exe.is_file():
            raise ValueError('Chưa cấu hình executable ' + engine)
        for index, root in enumerate(policy.roots):
            extension = '.csv' if engine == 'wiztree' else '.json'
            path = safe_path(directory / f'export-{index}{extension}', beneath=directory)
            previous = next((r for r in job['sources'] if r.get('file') == path.name), None)
            if previous and path.is_file() and path.stat().st_size == previous['bytes'] and sha256(path) == previous['sha256']:
                notify('info', {'message': f'Dùng lại export hoàn tất: {root}; không quét lại'})
            else:
                path.unlink(missing_ok=True)
                if engine == 'wiztree':
                    command = [str(exe), root, f'/export={path}', '/exportfolders=1', '/exportfiles=1', '/exportalldates=1',
                               '/exportUTCTime=1', '/exportmftrecno=1', '/exportdrivecapacity=1',
                               '/admin=1' if profile.get('mft', True) else '/admin=0']
                elif engine == 'gdu':
                    private_config = directory / 'gdu.yaml'
                    if not private_config.exists():
                        private_config.touch()
                    command = [str(exe), root, '--non-interactive', '--no-progress', '--no-delete', '--no-view-file',
                               '--no-spawn-shell', '--config-file', str(private_config), '--output-file', str(path)]
                else:
                    command = [str(exe), '/SaveTo', str(path), root]
                notify('progress', {'percent': 5, 'stage':'collect','message': f'{engine} đang thu thập {root}; chưa nhập snapshot'})
                done, tool_stop = threading.Event(), threading.Event()
                exceeded = []
                def watch():
                    while not done.wait(.1):
                        if stop.is_set():
                            tool_stop.set(); return
                        if self.source_bytes() > self.repo.limits['source_mb'] * 1024 * 1024:
                            exceeded.append(True); tool_stop.set(); return
                monitor = threading.Thread(target=watch, daemon=True)
                monitor.start()
                try:
                    result = run(command, timeout=1800, stop=tool_stop)
                    if result.returncode or not path.is_file():
                        raise RuntimeError(f'{engine} export lỗi: {result.returncode}: {result.stderr[-1000:]}')
                except BaseException:
                    path.unlink(missing_ok=True)
                    if exceeded:
                        raise ValueError('Nguồn export vượt ngân sách tạm; đã dừng tool và bỏ export chưa hoàn tất')
                    raise
                finally:
                    done.set(); monitor.join()
                if self.source_bytes() > self.repo.limits['source_mb'] * 1024 * 1024:
                    path.unlink(missing_ok=True)
                    raise ValueError('Nguồn export vượt ngân sách tạm')
                proof = {'file': path.name, 'bytes': path.stat().st_size, 'sha256': sha256(path)}
                job['sources'] = [r for r in job['sources'] if r.get('file') != path.name] + [proof]
                self._save(job)
            sources.append(path)
        notify('progress', {'percent': 25, 'stage':'read_source','message': 'Đã có export hoàn tất; nhập trực tiếp, không stat lại từng đường dẫn'})
        if engine == 'wiztree':
            return import_wiztree(sources, metadata, timezone_name='UTC', repository=self.repo, stop=stop, notify=notify)
        return import_entries(self._external_entries(sources, engine, policy, stop), metadata,
                              repository=self.repo, stop=stop, notify=notify)

    @staticmethod
    def _external_entries(sources, engine, policy, stop):
        import ijson
        from .engines import gdu_stream
        from .scanner import stamp
        for source in sources:
            def items():
                if engine == 'gdu':
                    for path, folder, row in gdu_stream(source):
                        yield {'path': str(path), 'parent': str(path.parent), 'kind': 'folder' if folder else 'file',
                               'logical': row['asize'], 'allocated': None, 'created': None, 'modified': row.get('mtime'),
                               'identity': '', 'attrs': None, 'note': 'gdu: không có số cấp phát Windows/identity; không đoán bằng 0'}
                else:
                    with source.open('rb') as stream:
                        for row in ijson.items(stream, 'item'):
                            if 'Name' not in row or 'Logical Size' not in row:
                                raise ValueError('WinDirStat cần schema English có Name/Logical Size')
                            path = Path(row['Name'])
                            flags = int(row.get('WinDirStat Attributes', '0'), 16)
                            yield {'path': str(path), 'parent': str(path.parent), 'kind': 'folder' if flags & 4 else 'file',
                                   'logical': row['Logical Size'], 'allocated': None, 'created': None,
                                   'modified': stamp(row['Last Change']) if row.get('Last Change') else None,
                                   'identity': '', 'attrs': None, 'note': 'WinDirStat export; cấp phát nguồn='+str(row.get('Physical Size'))+'; thiếu ID hardlink, không đoán tổng Đĩa'}
            for entry in items():
                if stop.is_set():
                    raise ProcessCancelled()
                if policy.in_scope(entry['path']):
                    yield entry

    def discard(self, ident):
        with self.repo.locked():
            value = self.get(ident)
            if value.get('state') == 'running' and alive(value.get('owner') or {}):
                raise RuntimeError('Dừng tác vụ trước khi bỏ điểm tiếp tục')
            directory = self.directory(ident)
            for item in directory.rglob('*'):
                safe_path(item, beneath=directory)
            shutil.rmtree(directory)
        return {'discarded': ident, 'snapshots_preserved': True}
