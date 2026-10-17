"""Free-space checks for app-owned SQLite work, without deleting user history."""
from __future__ import annotations
import shutil
import sqlite3
import time
from .config import home, load


class LowSpaceError(RuntimeError):
    pass


def require_space(extra=0, *, operation='xử lý dữ liệu', reserve=None):
    reserve = int(load().get('storage_reserve_mb',1024))*1_000_000 if reserve is None else reserve
    required=max(0,int(extra))+max(0,int(reserve))
    free=shutil.disk_usage(home()).free
    if free<required:
        raise LowSpaceError(f'Không đủ dung lượng cho {operation}: còn {free//1_000_000:,} MB, '
            f'cần ước tính {required//1_000_000:,} MB (gồm dung lượng dự phòng). '
            'Mở Hệ thống → Dung lượng app để dọn cache/backup; không tự xóa lịch sử.')
    return {'free_bytes':free,'required_bytes':required}


class SpaceGuard:
    def __init__(self, extra=0, *, operation='xử lý dữ liệu'):
        self.operation=operation;self.error=None;self.last=0
        self.reserve=int(load().get('storage_reserve_mb',1024))*1_000_000
        require_space(extra,operation=operation,reserve=self.reserve)

    def check(self):
        require_space(operation=self.operation,reserve=self.reserve)

    def progress(self):
        if self.error:return 1
        if time.monotonic()-self.last>=1:
            self.last=time.monotonic()
            try:self.check()
            except LowSpaceError as exc:self.error=exc;return 1
        return 0


class SpaceConnection(sqlite3.Connection):
    """Preserve the meaningful error when SQLite aborts a progress callback."""
    space_guard=None
    after_commit=None

    def commit(self):
        result=super().commit()
        if self.after_commit:self.after_commit()
        return result

    def _space_execute(self, method, *args, **kwargs):
        try:return method(*args,**kwargs)
        except sqlite3.OperationalError as exc:
            if self.space_guard and self.space_guard.error:raise self.space_guard.error from exc
            raise

    def execute(self,*args,**kwargs):return self._space_execute(super().execute,*args,**kwargs)
    def executemany(self,*args,**kwargs):return self._space_execute(super().executemany,*args,**kwargs)
    def executescript(self,*args,**kwargs):return self._space_execute(super().executescript,*args,**kwargs)


def incremental_reclaim(db, *, seconds=.2):
    """Bounded reclamation on new caches; never run a full VACUUM automatically."""
    if db.in_transaction or db.execute('PRAGMA main.auto_vacuum').fetchone()[0]!=2:return 0
    before=db.execute('PRAGMA main.freelist_count').fetchone()[0]
    if before<256:return 0
    end=time.monotonic()+seconds
    while time.monotonic()<end and db.execute('PRAGMA main.freelist_count').fetchone()[0]:
        db.execute('PRAGMA main.incremental_vacuum(256)').fetchall()
    db.execute('PRAGMA main.wal_checkpoint(PASSIVE)').fetchall()
    return before-db.execute('PRAGMA main.freelist_count').fetchone()[0]
