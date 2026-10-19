"""Opt-in Parquet repository and bounded, process-private DuckDB sessions.

This module never imports Store, opens SQLite or upgrades the production history.
Snapshots are immutable; the only shared write is a small, atomically published catalog.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import ntpath
import os
from pathlib import Path
import shutil
import tempfile
import threading
import time
import uuid

from .platform_ops import ProcessCancelled

FORMAT = 'dgm-parquet-preview-1'
SCHEMA = (
    ('row_id', 'BIGINT'), ('path', 'VARCHAR'), ('parent', 'VARCHAR'),
    ('path_key', 'VARCHAR'), ('parent_key', 'VARCHAR'), ('kind', 'VARCHAR'),
    ('logical', 'BIGINT'), ('allocated', 'BIGINT'), ('created', 'DOUBLE'),
    ('modified', 'DOUBLE'), ('identity', 'VARCHAR'), ('attrs', 'BIGINT'),
    ('status', 'VARCHAR'), ('note', 'VARCHAR'),
)
DEFAULT_LIMITS = {'memory_mb': 768, 'temp_mb': 256, 'source_mb': 512,
                  'history_mb': 1024, 'reserve_mb': 128, 'threads': 8}
_locks = {}
_locks_guard = threading.Lock()


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def safe_path(path, *, beneath=None):
    """Reject existing junction/symlink ancestors, including on Windows."""
    path = Path(path).absolute()
    if beneath is not None and not path.is_relative_to(Path(beneath).absolute()):
        raise ValueError('Đường dẫn vượt khỏi kho Parquet thử nghiệm')
    for item in (path, *path.parents):
        if item.exists() and (item.is_symlink() or getattr(item, 'is_junction', lambda: False)()):
            raise ValueError('Kho Parquet không cho phép junction/symlink: ' + str(item))
    if path.resolve() != path:
        raise ValueError('Đường dẫn chưa được chuẩn hóa an toàn: ' + str(path))
    return path


def atomic_json(path, value):
    path = safe_path(path)
    raw = json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')
    temporary = safe_path(path.with_name(path.name + '.' + uuid.uuid4().hex + '.part'))
    try:
        with temporary.open('xb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        # Windows readers (GUI catalog/job refresh, AV) may briefly deny
        # FILE_SHARE_DELETE. Keep the staged bytes and retry the atomic rename;
        # never truncate the live JSON or fall back to a non-atomic write.
        for attempt in range(25):
            try:
                temporary.replace(path)
                break
            except PermissionError:
                if attempt==24:raise
                time.sleep(.01)
    finally:
        temporary.unlink(missing_ok=True)


def read_json(path, default=None):
    path = safe_path(path)
    if not path.exists():
        return default
    if path.stat().st_size > 20 * 1024 * 1024:
        raise ValueError('Catalog/tiến độ lớn bất thường; không nạp tự động')
    return json.loads(path.read_text(encoding='utf-8'))


def owner():
    import psutil
    return {'pid': os.getpid(), 'created': psutil.Process().create_time()}


def alive(value):
    import psutil
    try:
        return abs(psutil.Process(value['pid']).create_time() - value['created']) < .01
    except psutil.AccessDenied:
        return True
    except (psutil.NoSuchProcess, KeyError, TypeError, ValueError):
        return False


class CapturedPaths:
    """Lexical Windows identity; never stat a captured path to normalize it."""
    def __init__(self, metadata):
        self.meta = metadata
        self.scope = metadata.get('scope') or {}
        self.volume_map = dict(metadata.get('volume_map') or {})
        roots = self.scope.get('roots') or []
        volumes = self.scope.get('volumes') or []
        anchors = {self.anchor(root) for root in roots}
        if len(volumes) == 1:
            for anchor in anchors:
                self.volume_map.setdefault(anchor, volumes[0])
        self.sensitive = [self.canonical(p) for p in self.scope.get('case_sensitive_roots', [])]

    @staticmethod
    def canonical(path):
        path = str(path)
        if path.startswith('/') and not path.startswith('//'):
            import posixpath
            return posixpath.normpath(path)
        path = path.replace('/', '\\')
        literal = path.startswith('\\\\?\\') and any(part.endswith(('.', ' ')) for part in path[4:].split('\\') if part)
        if path.startswith('\\\\?\\UNC\\') and not literal:
            path = '\\\\' + path[8:]
        elif path.startswith('\\\\?\\') and len(path) > 6 and path[5] == ':' and not literal:
            path = path[4:]
        if path.startswith('\\\\.\\') or '\x00' in path:
            raise ValueError('Đường dẫn thiết bị/NUL không phải bản ghi file thông thường')
        if not ntpath.isabs(path) or not ntpath.splitdrive(path)[0]:
            raise ValueError('Cần đường dẫn tuyệt đối từ bản quét: ' + path)
        if not literal:
            drive,tail=ntpath.splitdrive(path)
            # Win32 aliases trim trailing dots/spaces in path components.
            # WizTree walk exports extensionless files with an extra dot.
            tail='\\'.join(part if part in ('.','..') else part.rstrip(' .') for part in tail.split('\\'))
            path=drive+tail
        return ntpath.normpath(path)

    @classmethod
    def anchor(cls, path):
        path = cls.canonical(path)
        if path.startswith('/'):return '/'
        drive=ntpath.splitdrive(path)[0].lower()
        if drive.startswith('\\\\?\\') and len(drive)==6 and drive[5]==':':drive=drive[4:]
        return drive

    def key(self, path):
        path = self.canonical(path)
        anchor = self.anchor(path)
        volume = str(self.volume_map.get(anchor) or 'mount:' + anchor).lower()
        if path.startswith('/'):
            relative = path.lstrip('/')
        else:
            relative = ntpath.splitdrive(path)[1].lstrip('\\')
            sensitive = next((r for r in self.sensitive if path.lower() == r.lower() or path.lower().startswith(r.lower().rstrip('\\') + '\\')), None)
            if sensitive:
                prefix = ntpath.splitdrive(sensitive)[1].lstrip('\\')
                relative = prefix.lower() + relative[len(prefix):]
            else:
                # lower(), not casefold(): do not collapse e.g. ß and ss.
                relative = ntpath.normcase(relative)
        return volume + '|' + relative

    def parent(self, path):
        parent = self.display_parent(path)
        return None if parent == self.canonical(path) else self.key(parent)

    def display_parent(self, path):
        path = self.canonical(path)
        if path.startswith('/'):
            import posixpath
            parent = posixpath.dirname(path)
        else:
            parent = ntpath.dirname(path)
        return parent


def parent_key(key):
    volume, relative = key.split('|', 1)
    if not relative:
        return None
    separator = '/' if relative.startswith('/') or ('/' in relative and '\\' not in relative) else '\\'
    parent = relative.rsplit(separator, 1)[0] if separator in relative else ''
    return volume + '|' + parent


class Repository:
    def __init__(self, root=None, *, limits=None):
        if root is None:
            from .config import home
            root = home() / 'data/columnar-preview'
        self.root = safe_path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.snapshots = safe_path(self.root / 'snapshots', beneath=self.root)
        self.work = safe_path(self.root / 'work', beneath=self.root)
        self.snapshots.mkdir(exist_ok=True)
        self.work.mkdir(exist_ok=True)
        self.catalog_path = self.root / 'catalog.json'
        self.limits = {**DEFAULT_LIMITS, **(limits or {})}
        for key, value in self.limits.items():
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError('Giới hạn tài nguyên phải là số nguyên dương: ' + key)
        if self.limits['threads'] > 64 or self.limits['memory_mb'] > 65536:
            raise ValueError('Giới hạn luồng/RAM không hợp lệ')
        with self.locked():
            catalog = self.catalog()
            if not self.catalog_path.exists():
                atomic_json(self.catalog_path, catalog)

    @contextmanager
    def locked(self):
        with _locks_guard:
            lock = _locks.setdefault(str(self.root), threading.RLock())
        with lock:
            path = safe_path(self.root / 'catalog.lock', beneath=self.root)
            with path.open('a+b') as stream:
                if stream.seek(0, 2) == 0:
                    stream.write(b'0'); stream.flush()
                deadline = time.monotonic() + 30
                while True:
                    try:
                        stream.seek(0)
                        if os.name == 'nt':
                            import msvcrt
                            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                        else:
                            import fcntl
                            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except OSError:
                        if time.monotonic() > deadline:
                            raise RuntimeError('Kho Parquet đang được cập nhật bởi tác vụ khác')
                        time.sleep(.03)
                try:
                    yield
                finally:
                    stream.seek(0)
                    if os.name == 'nt':
                        import msvcrt
                        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(stream, fcntl.LOCK_UN)

    def catalog(self):
        value = read_json(self.catalog_path, {'format': FORMAT, 'next_id': 1, 'scans': []})
        if value.get('format') != FORMAT or not isinstance(value.get('scans'), list):
            raise ValueError('Không đúng catalog Parquet thử nghiệm; không chuyển/xóa dữ liệu tự động')
        return value

    def list(self):
        return sorted(self.catalog()['scans'], key=lambda row: row['id'], reverse=True)

    def get(self, ident):
        for row in self.catalog()['scans']:
            if row['id'] == int(ident):
                return row
        raise ValueError('Không có mốc Parquet #' + str(ident))

    def path(self, scan):
        name = scan['file']
        if not isinstance(name, str) or len(name) != 40 or not name.endswith('.parquet'):
            raise ValueError('Tên snapshot không hợp lệ')
        try:
            uuid.UUID(hex=name[:-8])
        except ValueError as exc:
            raise ValueError('Tên snapshot không hợp lệ') from exc
        return safe_path(self.snapshots / name, beneath=self.snapshots)

    def check_space(self, extra=0):
        free = shutil.disk_usage(self.root).free
        if free < extra + self.limits['reserve_mb'] * 1024 * 1024:
            raise RuntimeError('Thiếu dung lượng dự phòng; không tự xóa lịch sử để tiếp tục')

    def verify(self, scan):
        path = self.path(scan)
        stat = path.stat()
        if stat.st_size != scan['bytes'] or sha256(path) != scan['sha256']:
            raise ValueError('Snapshot Parquet không khớp checksum: #' + str(scan['id']))
        return path

    def publish(self, staged, metadata, summary):
        staged = safe_path(staged, beneath=self.work)
        if staged.suffix != '.parquet':
            raise ValueError('Chỉ publish bản Parquet đã kiểm chứng')
        size = staged.stat().st_size
        proof = sha256(staged)
        with self.locked():
            catalog = self.catalog()
            fingerprint = metadata.get('source_fingerprint')
            if fingerprint:
                existing = next((r for r in catalog['scans'] if r.get('source_fingerprint') == fingerprint), None)
                if existing:
                    self.verify(existing)
                    return {**existing, 'already_imported': True}
            used = sum(int(r['bytes']) for r in catalog['scans'])
            if used + size > self.limits['history_mb'] * 1024 * 1024:
                raise RuntimeError('Đạt giới hạn lịch sử Parquet; chọn mốc để dọn hoặc tăng ngân sách, không tự xóa mốc cũ')
            self.check_space(size)
            name = uuid.uuid4().hex + '.parquet'
            destination = safe_path(self.snapshots / name, beneath=self.snapshots)
            row = {**metadata, 'id': catalog['next_id'], 'file': name, 'format': FORMAT,
                   'schema_version': 1, 'bytes': size, 'sha256': proof, 'summary': summary,
                   'published': utc_now()}
            # Do not leave a published-looking catalog pointing at a partial file.
            staged.replace(destination)
            try:
                catalog['next_id'] += 1
                catalog['scans'].append(row)
                atomic_json(self.catalog_path, catalog)
            except BaseException:
                destination.unlink(missing_ok=True)
                raise
            return row

    def delete(self, identifiers):
        wanted = {int(value) for value in identifiers}
        if not wanted:
            return {'removed_bytes': 0, 'deleted': []}
        with self.locked():
            catalog = self.catalog()
            found = [row for row in catalog['scans'] if row['id'] in wanted]
            if len(found) != len(wanted):
                raise ValueError('Danh sách xóa có mốc không tồn tại; chưa xóa gì')
            for lease in self.work.glob('session-*/owner.json'):
                value = read_json(lease, {})
                if alive(value.get('owner', {})) and wanted.intersection(value.get('scans', [])):
                    raise RuntimeError('Mốc đang được đọc; đóng cặp so sánh trước khi xóa')
            paths = [self.path(row) for row in found]
            catalog['scans'] = [row for row in catalog['scans'] if row['id'] not in wanted]
            atomic_json(self.catalog_path, catalog)
            removed = 0
            pending = []
            for path in paths:
                try:
                    size = path.stat().st_size
                    path.unlink(); removed += size
                except OSError:
                    pending.append(path.name)
            return {'deleted': sorted(wanted), 'removed_bytes': removed,
                    'pending_orphans': pending, 'legacy_history_preserved': True}

    def footprint(self):
        catalog = self.catalog()
        work_bytes = sum(path.stat().st_size for path in self.work.rglob('*') if path.is_file() and not path.is_symlink())
        return {'snapshots': len(catalog['scans']), 'snapshot_bytes': sum(r['bytes'] for r in catalog['scans']),
                'catalog_bytes': self.catalog_path.stat().st_size, 'work_bytes': work_bytes,
                'persistent_database_bytes': 0, 'limits': dict(self.limits)}

    def cleanup_abandoned(self):
        """Remove only verified, dead sessions in this repository's work tree."""
        removed, names = 0, []
        with self.locked():
            for path in self.work.glob('session-*'):
                target = safe_path(path, beneath=self.work)
                if target.parent != self.work or not target.is_dir():
                    continue
                lease = read_json(target / 'owner.json', {})
                token = lease.get('token')
                if not isinstance(token, str) or len(token) != 32 or not lease.get('owner') or alive(lease['owner']):
                    continue
                items = list(target.rglob('*'))
                for item in items:
                    safe_path(item, beneath=target)
                amount = sum(p.stat().st_size for p in items if p.is_file())
                shutil.rmtree(target)
                removed += amount; names.append(target.name)
        return {'removed_bytes': removed, 'sessions': names, 'snapshots_preserved': True}


class MemorySession:
    def __init__(self, repository, *, notify=None):
        import duckdb
        self.repo = repository
        self.notify = notify or (lambda *args: None)
        self.work = Path(tempfile.mkdtemp(prefix='session-', dir=repository.work))
        safe_path(self.work, beneath=repository.work)
        self.token = uuid.uuid4().hex
        self.closed = False
        self._lock = threading.RLock()
        self._local = threading.local()
        self._lease = {'owner': owner(), 'token': self.token, 'scans': []}
        atomic_json(self.work / 'owner.json', self._lease)
        try:
            self.db = duckdb.connect(':memory:', config={
                'threads': repository.limits['threads'],
                'memory_limit': f"{repository.limits['memory_mb']}MiB",
                'temp_directory': str(self.work / 'spill'),
                'max_temp_directory_size': f"{repository.limits['temp_mb']}MiB",
                'autoinstall_known_extensions': False, 'autoload_known_extensions': False,
            })
        except BaseException:
            self._remove_work(); raise

    @contextmanager
    def operation(self, stop=None):
        stop = stop or threading.Event()
        with self._lock:
            if self.closed:
                raise RuntimeError('Phiên so sánh đã đóng')
            if stop.is_set():
                raise ProcessCancelled('Đã dừng trước khi bắt đầu')
            if getattr(self._local, 'depth', 0):
                yield; return
            self._local.depth = 1
            done = threading.Event()
            def watch():
                while not done.wait(.05):
                    if stop.is_set():
                        self.db.interrupt(); return
            thread = threading.Thread(target=watch, daemon=True)
            thread.start()
            try:
                yield
                if stop.is_set():
                    raise ProcessCancelled('Đã dừng tác vụ; snapshot đã lưu được giữ nguyên')
            except Exception as exc:
                if stop.is_set():
                    raise ProcessCancelled('Đã dừng tác vụ; không sửa lịch sử') from exc
                raise
            finally:
                done.set(); thread.join(); self._local.depth = 0

    def rows(self, statement, parameters=None, *, stop=None):
        with self.operation(stop):
            cursor = self.db.execute(statement, parameters or [])
            columns = [item[0] for item in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def pin(self, scans):
        with self.repo.locked():
            self._lease['scans'] = [row['id'] for row in scans]
            atomic_json(self.work / 'owner.json', self._lease)
            return [self.repo.verify(row) for row in scans]

    def progress(self, percent, message, *, stage=None):
        data={'percent':int(percent),'message':message}
        if stage:data['stage']=stage
        self.notify('progress',data)

    def _remove_work(self):
        target = safe_path(self.work, beneath=self.repo.work)
        if target.parent != self.repo.work or not target.name.startswith('session-'):
            raise ValueError('Không đúng thư mục tạm của phiên')
        value = read_json(target / 'owner.json', {})
        if value.get('token') != self.token:
            raise ValueError('Thư mục tạm không còn thuộc phiên này')
        if any(p.is_symlink() or getattr(p, 'is_junction', lambda: False)() for p in target.rglob('*')):
            raise ValueError('Không xóa cây thư mục tạm có reparse point')
        shutil.rmtree(target)

    def close(self):
        with self._lock:
            if not self.closed:
                self.db.close(); self.closed = True
                self._remove_work()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
