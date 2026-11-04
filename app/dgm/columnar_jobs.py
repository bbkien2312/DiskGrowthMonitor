"""Small restart journals for the opt-in core; no production history mutations."""
from __future__ import annotations

from pathlib import Path
import logging
import re
import shutil
import threading
import uuid
from contextlib import nullcontext
from tempfile import TemporaryDirectory

from .columnar_store import Repository, LowSpaceError, atomic_json, read_json, safe_path, owner, alive, sha256, utc_now
from .columnar_ingest import metadata_for, import_entries, import_wiztree, import_legacy
from .columnar_checkpoints import MIB, ExportLimitError, compress_export, open_export
from .platform_ops import ProcessCancelled, run, is_admin

_EXPORT = re.compile(r'export-\d+\.(?:csv|json)(?:\.gz(?:\.part)?)?')
_LOG = logging.getLogger('dgm')


def filesystem_type(root):
    import ctypes,os
    if os.name!='nt':return None
    name=ctypes.create_unicode_buffer(64)
    if ctypes.windll.kernel32.GetVolumeInformationW(str(Path(root).anchor),None,0,None,None,None,name,len(name)):
        return name.value.upper()
    return None


def private_config_supported(executable):
    """/configfolder is documented for WizTree 4.24+; unknown versions stay cautious."""
    try:
        import win32api
        v=win32api.GetFileVersionInfo(str(executable),'\\')['FileVersionMS']
        return (v>>16,v&65535)>=(4,24)
    except Exception:
        return False


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
        if operation == 'scan':
            value['temp_policy'] = 1
        atomic_json(directory / 'job.json', value)
        return value

    def source_bytes(self):
        return sum(self._size(p) for p in self.root.glob('*/export-*') if _EXPORT.fullmatch(p.name))

    def _size(self, path):
        try:
            return safe_path(path, beneath=self.root).stat().st_size
        except FileNotFoundError:
            return 0

    def _outputs(self, job):
        return [p for p in self.directory(job['id']).glob('export-*') if _EXPORT.fullmatch(p.name)]

    def _remove_outputs(self, job, keep=()):
        for path in self._outputs(job):
            if path.name not in keep:
                try:
                    safe_path(path, beneath=self.directory(job['id'])).unlink(missing_ok=True)
                except (OSError, ValueError):
                    _LOG.warning('Không dọn được export tạm của tác vụ %s', job['id'][:8])

    def checkpoints(self, exclude=None):
        """Only stat tracked exports; refreshing the GUI never reads CSV content."""
        rows = []
        for job in self.list():
            if job['id'] == exclude or job['running'] or job['state'] == 'complete':
                continue
            directory = self.directory(job['id'])
            used = sum(self._size(directory / p['file']) for p in job['sources']
                       if _EXPORT.fullmatch(p.get('file', '')))
            if used:
                rows.append(dict(job, checkpoint_bytes=used))
        return rows

    def _verified_exports(self, job, roots, engine):
        proofs = job['sources']
        if not roots or len(proofs) != len(roots):
            return []
        directory = self.directory(job['id'])
        found = []
        raw_total = 0
        for index, proof in enumerate(proofs):
            suffix = 'csv' if engine == 'wiztree' else 'json'
            base = f'export-{index}.{suffix}'
            filename = proof.get('file')
            if filename not in (base, base+'.gz') or proof.get('root', roots[index]) != roots[index]:
                return []
            path = safe_path(directory / filename, beneath=directory)
            raw_bytes = proof.get('raw_bytes') if path.suffix == '.gz' else proof.get('bytes')
            if type(raw_bytes) is not int or raw_bytes < 1:
                return []
            raw_total += raw_bytes
            try:
                if path.stat().st_size != proof['bytes'] or sha256(path) != proof['sha256']:
                    return []
            except FileNotFoundError:
                return []
            found.append(path)
        if raw_total > self.repo.limits['source_mb'] * MIB:
            raise ExportLimitError('Nguồn đã lưu lớn hơn hạn mức mỗi lượt; tăng giới hạn hoặc bỏ điểm tiếp tục.')
        return found

    def _keep_checkpoint(self, job):
        """Resolve a failed new scan, with the repository lock held."""
        profile = job['arguments']['profile']
        roots = (job.get('metadata') or {}).get('scope', {}).get('roots') or []
        directory = self.directory(job['id'])
        paths = self._verified_exports(job, roots, profile.get('engine', 'wiztree'))
        if not paths:
            self._remove_outputs(job)
            job['sources'] = []
            return 'Chưa có export đủ các phạm vi; cần quét lại.'
        if all(path.suffix == '.gz' for path in paths):
            self._remove_outputs(job, {path.name for path in paths})
            return 'Giữ nguyên điểm tiếp tục nén đã kiểm chứng.'
        old = self.checkpoints(exclude=job['id'])
        replace = len(old) == 1 and old[0]['id'] == job['arguments'].get('replace_checkpoint')
        if old and not replace:
            self._remove_outputs(job)
            job['sources'] = []
            return 'Giữ điểm tiếp tục cũ; lượt mới chỉ lưu nhật ký và cần quét lại.'
        budget = self.repo.limits['checkpoint_mb'] * MIB
        packed = []
        try:
            self.repo.check_space(budget)
            for path, proof in zip(paths, job['sources']):
                archive = safe_path(path.with_name(path.name+'.gz'), beneath=directory)
                stage = safe_path(archive.with_name(archive.name+'.part'), beneath=directory)
                stage.unlink(missing_ok=True)
                compressed = compress_export(path, stage, proof, budget)
                stage.replace(archive)
                packed.append(dict(compressed, file=archive.name))
                budget -= compressed['bytes']
        except (OSError, ValueError, LowSpaceError) as exc:
            if old:
                self._remove_outputs(job)
                job['sources'] = []
                return f'Không lưu điểm mới ({exc}); giữ nguyên điểm cũ, lượt mới cần quét lại.'
            self._remove_outputs(job, {path.name for path in paths})
            return f'Không tạo được bản nén ({exc}); bảo vệ một nguồn gốc để tiếp tục.'
        # Publish the verified checkpoint before retiring either source.
        job['sources'] = packed
        self._save(job)
        self._remove_outputs(job, {proof['file'] for proof in packed})
        if replace:
            prior = old[0]
            self._remove_outputs(prior)
            prior['sources'] = []
            prior['checkpoint_note'] = 'Điểm tiếp tục đã được thay theo lựa chọn người dùng.'
            self._save(prior)
        size = sum(proof['bytes'] for proof in packed)
        return f'Đã giữ điểm tiếp tục nén: {size/MIB:,.2f} MiB.'

    def _recover_interrupted(self, exclude):
        """Reconcile only new app-owned exports after a dead worker, not legacy CSVs."""
        for job in self.list():
            if job['id'] == exclude or not job.get('temp_policy'):
                continue
            if job['state'] == 'complete' and job['sources']:
                self._remove_outputs(job)
            elif job['state'] == 'interrupted':
                job['state'] = 'stopped'
                job['owner'] = None
                job['checkpoint_note'] = self._keep_checkpoint(job)
                self._save(job)

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
            self._recover_interrupted(ident)
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
            with self.repo.locked():
                value.update(state='complete', snapshots=[r['id'] for r in results], owner=None,timing=timing)
                value.pop('checkpoint_note', None)
                self._save(value)
                self._remove_outputs(value)
            return results
        except BaseException as exc:
            if 'timeline' in locals():value['timing']=timeline.finish('cancelled' if isinstance(exc,(ProcessCancelled,KeyboardInterrupt)) else 'failed')
            with self.repo.locked():
                if value.get('temp_policy'):
                    try:
                        value['checkpoint_note'] = self._keep_checkpoint(value)
                    except (OSError, ValueError, RuntimeError) as checkpoint_error:
                        value['checkpoint_note'] = f'Không thể xử lý điểm tiếp tục an toàn: {checkpoint_error}'
                    notify('info', {'message': value['checkpoint_note']})
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
        sources = [] if engine == 'native' else self._verified_exports(job, policy.roots, engine)
        if not sources:
            self._remove_outputs(job)
            job['sources'] = []
            metadata['started'] = utc_now()
            from .disk_measurements import read_volume_usage
            metadata['volume_snapshots'] = read_volume_usage(policy.roots)
            self._save(job)
        if engine == 'native':
            self.repo.check_space()
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
        exe = Path(job['arguments']['engines'].get(engine) or '')
        if not sources and not exe.is_file():
            raise ValueError('Chưa cấu hình executable ' + engine)
        if sources:
            notify('info', {'message': 'Dùng lại export hoàn tất đã kiểm checksum; không quét lại'})
        else:
            try:
                self.repo.check_space((self.repo.limits['source_mb'] + self.repo.limits['temp_mb']) * MIB)
            except LowSpaceError as exc:
                held = self.checkpoints(exclude=job['id'])
                size = sum(row['checkpoint_bytes'] for row in held)
                raise LowSpaceError(f'{exc} Nhật ký đang giữ {size/MIB:,.1f} MiB trong '
                                    f'{len(held)} điểm tiếp tục; chưa mở công cụ quét.') from None
        capture_roots = () if sources else policy.roots
        for index, root in enumerate(capture_roots):
            extension = '.csv' if engine == 'wiztree' else '.json'
            path = safe_path(directory / f'export-{index}{extension}', beneath=directory)
            if stop.is_set():
                raise ProcessCancelled()
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
            limit = self.repo.limits['source_mb'] * MIB
            completed_bytes = sum(p['bytes'] for p in job['sources'])
            def check_limit():
                used = completed_bytes + self._size(path)
                if used > limit:
                    raise ExportLimitError(f'Export lượt này {used/MIB:,.1f} MiB vượt hạn mức {limit/MIB:,.0f} MiB. '
                                           'Đã dừng và bỏ export chưa hoàn tất; không tính CSV của lượt cũ.')
            def watch():
                while not done.wait(.1):
                    if stop.is_set():
                        tool_stop.set(); return
                    try:
                        check_limit()
                        self.repo.check_space()
                    except (ExportLimitError, LowSpaceError, OSError) as exc:
                        exceeded.append(exc); tool_stop.set(); return
            monitor = threading.Thread(target=watch, daemon=True)
            private_config=TemporaryDirectory(prefix='wiztree-config-',dir=directory) if engine=='wiztree' else None
            if private_config:command += [f'/configfolder={private_config.name}','/filter=*']
            monitor.start()
            try:
                result = run(command, timeout=1800, stop=tool_stop)
                if result.returncode or not path.is_file():
                    raise RuntimeError(f'{engine} export lỗi: {result.returncode}: {result.stderr[-1000:]}')
                check_limit()
            except BaseException:
                path.unlink(missing_ok=True)
                if exceeded:
                    raise exceeded[0]
                raise
            finally:
                done.set(); monitor.join()
                if private_config:private_config.cleanup()
            proof = {'file': path.name, 'bytes': path.stat().st_size, 'sha256': sha256(path)}
            proof.update(root=root,full_mft=bool(engine=='wiztree' and profile.get('mft',True) and is_admin()
                and private_config_supported(exe) and Path(root)==Path(Path(root).anchor) and filesystem_type(root)=='NTFS' and not result.stderr.strip()),
                unfiltered_private_config=bool(engine=='wiztree'))
            job['sources'] = [r for r in job['sources'] if r.get('file') != path.name] + [proof]
            self._save(job)
            sources.append(path)
        # The tool may finish just as disk space falls below the reserve. Save
        # its proof first so a complete capture can still be resumed.
        self.repo.check_space()
        notify('progress', {'percent': 25, 'stage':'read_source','message': 'Đã có export hoàn tất; nhập trực tiếp, không stat lại từng đường dẫn'})
        if engine == 'wiztree':
            covered=[root for root in policy.roots if any(p.get('root')==root and p.get('full_mft') and p.get('unfiltered_private_config') for p in job['sources'])]
            metadata['coverage']={'source':'wiztree-mft' if covered else 'export','version':2,
                'covered_roots':covered,'unknown_roots':[root for root in policy.roots if root not in covered],
                'error_regions':[],'proof':{'completed_export_roots':list(policy.roots),'trusted_mft_roots':covered,
                    'requires':'checksum, private config supported, NTFS whole volume, admin and no tool warning; not an atomic snapshot'}}
            sizes = {p: r['raw_bytes'] for p, r in zip(sources, job['sources']) if p.suffix == '.gz'}
            return import_wiztree(sources, metadata, timezone_name='UTC', repository=self.repo, stop=stop, notify=notify,
                                  verified_raw_sizes=sizes)
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
                    with open_export(source) as stream:
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
