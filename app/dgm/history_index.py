"""Shared, leased temporary indexes. The authoritative data is compressed text.

Catalogs retain only scan/job/benchmark/plan metadata and comparison summaries.
Entry rows and normalized overrides are streamed from checksummed JSONL logs.
The last process releases SQLite files; a crashed process leaves recoverable
indexes until the next owner can safely reconcile them with the logs.
"""
from __future__ import annotations
import atexit
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
import weakref
from .config import home
from . import history_logs as logs

TABLES=('scans','plans','benchmarks','jobs','scan_numbers','sequence_counters','scan_id_counter')
_managers={}
_mutexes={}


@contextmanager
def mutex(root=None):
    """OS byte lock: releases on process death; reentrant within one thread."""
    root=Path(root or home());state=_mutexes.setdefault(str(root),{'lock':threading.RLock(),'local':threading.local()})
    with state['lock']:
        local=state['local']
        if getattr(local,'depth',0):
            local.depth+=1
            try:yield
            finally:local.depth-=1
            return
        path=root/'data/history-log.lock';path.parent.mkdir(parents=True,exist_ok=True)
        from .storage_maintenance import safe
        safe(path)
        with path.open('a+b') as stream:
            if stream.seek(0,2)==0:stream.write(b'0');stream.flush()
            deadline=time.monotonic()+30
            while True:
                try:
                    stream.seek(0)
                    if os.name=='nt':
                        import msvcrt
                        msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
                    else:
                        import fcntl
                        fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic()>deadline:raise RuntimeError('Nhật ký lịch sử đang được ghi bởi tác vụ khác')
                    time.sleep(.03)
            local.depth=1
            try:yield
            finally:
                local.depth=0;stream.seek(0)
                if os.name=='nt':msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
                else:fcntl.flock(stream.fileno(),fcntl.LOCK_UN)


def read_json(path,default):
    try:return json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:return default


def write_json(path,value):
    from .elevation import atomic
    from .storage_maintenance import safe
    safe(path)
    if read_json(path,None)!=value:atomic(path,value)


def rows(db,table):
    columns=[r[1] for r in db.execute('PRAGMA table_info('+table+')')]
    return [dict(zip(columns,row)) for row in db.execute('SELECT * FROM '+table)] if columns else []


def restore_rows(db,table,values):
    for value in values:
        allowed={r[1] for r in db.execute('PRAGMA table_info('+table+')')}
        if not set(value)<=allowed:raise ValueError('Metadata log dùng schema chưa hỗ trợ: '+table)
        columns=','.join(value);marks=','.join('?' for _ in value)
        db.execute(f'INSERT OR REPLACE INTO {table}({columns}) VALUES({marks})',tuple(value.values()))


class Index:
    def __init__(self,root):
        from .storage_maintenance import safe
        from .elevation import owner,alive
        self.root=Path(root);self.folder=safe(self.root/'data/history');self.folder.mkdir(exist_ok=True)
        self.catalog=self.folder/'catalog.json'
        self.index=safe(self.root/'data/tmp/history-index');self.index.mkdir(exist_ok=True)
        self.state_path=self.index/'owners.json';self.identity=owner();self.pins={};self.connections=weakref.WeakSet();self.closed=False;self.closing=False
        with mutex(self.root):
            catalog=read_json(self.catalog,{'schema':1,'tables':{}})
            if catalog.get('schema')!=1:raise ValueError('Phiên bản catalog lịch sử chưa hỗ trợ')
            state=read_json(self.state_path,{'app':'DiskGrowthMonitor-index','root':str(self.root),'owners':[]})
            if state.get('app')!='DiskGrowthMonitor-index' or state.get('root')!=str(self.root):raise ValueError('Thư mục chỉ mục có dữ liệu không nhận diện được')
            state['owners']=[o for o in state['owners'] if alive(o)]
            new=not (self.index/'history.sqlite').exists()
            from .store import Store
            store=Store(path=self.index/'history.sqlite')
            try:
                if new:
                    for table in TABLES:restore_rows(store.db,table,catalog.get('tables',{}).get(table,[]))
                    store.db.commit()
                store.db.execute('CREATE TABLE IF NOT EXISTS archive_loaded(scan_id INTEGER PRIMARY KEY,touched REAL)')
                store.db.commit()
                if not new and not state['owners']:self.flush_source(store.db)
            finally:store.close()
            state['owners'].append({**self.identity,'pins':[]});write_json(self.state_path,state)
            if not self.catalog.exists():write_json(self.catalog,catalog)

    def connect(self):
        db=sqlite3.connect(self.index/'history.sqlite',timeout=30);db.row_factory=sqlite3.Row
        return db

    def bind_source(self,db,readonly=False):
        self.connections.add(db)
        if not readonly:db.after_commit=lambda:self.flush_source(db)

    def flush_source(self,db):
        with mutex(self.root):
            previous=read_json(self.catalog,{'schema':1,'tables':{}})
            db.execute('BEGIN')
            try:tables={table:rows(db,table) for table in TABLES}
            finally:db.rollback()
            write_json(self.catalog,{**previous,'tables':tables})
            deleted={r['id'] for r in previous.get('tables',{}).get('scans',[])}-{r['id'] for r in tables['scans']}
            for ident in deleted:self.scan_path(ident).unlink(missing_ok=True)

    def scan_path(self,ident):return self.folder/'scans'/f'scan-{int(ident):08d}.jsonl.gz'

    def mark_loaded(self,db,ident):
        db.execute('INSERT OR REPLACE INTO archive_loaded VALUES(?,?)',(ident,time.time()));db.commit()

    def publish_scan(self,db,ident,*,stop=None,notify=None):
        with mutex(self.root):
            path=self.scan_path(ident)
            if path.exists():
                catalog=read_json(self.catalog,{'schema':1,'tables':{}})
                if str(ident) not in catalog.get('logs',{}):
                    report=logs.verify(path);report.update(bytes=path.stat().st_size,file=path.name)
                    catalog.setdefault('logs',{})[str(ident)]=report;write_json(self.catalog,catalog)
                return
            scan=dict(db.execute('SELECT * FROM scans WHERE id=?',(ident,)).fetchone())
            ordinal=db.execute('SELECT ordinal FROM scan_numbers WHERE scan_id=?',(ident,)).fetchone()[0]
            fields=','.join('e.'+key for key in logs.FIELDS)
            def records():
                for row in db.execute(f'SELECT {fields} FROM entries e WHERE scan_id=?',(ident,)):yield list(row)
                for row in db.execute('SELECT path,message FROM errors WHERE scan_id=?',(ident,)):yield {'error':list(row)}
            report=logs.write(path,{'scan':scan,'ordinal':ordinal,'columns':logs.FIELDS},records(),stop=stop,notify=notify)
            catalog=read_json(self.catalog,{'schema':1,'tables':{}})
            catalog.setdefault('logs',{})[str(ident)]=report;write_json(self.catalog,catalog)
            self.mark_loaded(db,ident)

    def _update_owner(self):
        from .elevation import alive
        state=read_json(self.state_path,{})
        owners=[o for o in state.get('owners',[]) if alive(o) and o['pid']!=self.identity['pid']]
        owners.append({**self.identity,'pins':list(self.pins)})
        state['owners']=owners;write_json(self.state_path,state)
        return {sid for owner in owners for sid in owner.get('pins',[])}

    @contextmanager
    def snapshots(self,ids,*,stop=None,notify=None):
        ids={int(i) for i in ids if i}
        with mutex(self.root):
            for ident in ids:self.pins[ident]=self.pins.get(ident,0)+1
            self._update_owner()
            try:self.ensure(ids,stop=stop,notify=notify)
            except BaseException:
                self._unpin(ids);raise
        try:yield
        finally:
            with mutex(self.root):self._unpin(ids)

    def _unpin(self,ids):
        for ident in ids:
            self.pins[ident]-=1
            if not self.pins[ident]:del self.pins[ident]
        self._update_owner()

    def ensure(self,ids,*,stop=None,notify=None):
        from .store import Store
        from .storage_safety import SpaceGuard
        store=Store(path=self.index/'history.sqlite');db=store.db
        try:
            loaded={r[0] for r in db.execute('SELECT scan_id FROM archive_loaded')}
            pinned=self._update_owner()
            stale=[r[0] for r in db.execute('SELECT scan_id FROM archive_loaded ORDER BY touched DESC') if r[0] not in pinned]
            for ident in stale[max(0,2-len(pinned)):]:
                status=db.execute("SELECT status FROM scans WHERE id=?",(ident,)).fetchone()
                if status and status[0]=='running':continue
                db.execute('DELETE FROM members WHERE scan_id=?',(ident,));db.execute('DELETE FROM errors WHERE scan_id=?',(ident,))
                db.execute('DELETE FROM archive_loaded WHERE scan_id=?',(ident,));loaded.discard(ident)
            db.commit()
            if stale:
                self.invalidate_comparisons(stale[max(0,2-len(pinned)):])
                db.execute('DELETE FROM revisions WHERE NOT EXISTS(SELECT 1 FROM members WHERE revision_id=revisions.id)')
                # Keep paths: sparse normalized overrides reference these IDs
                # during the current shared-index session. Last owner drops it.
                db.commit()
            missing=ids-loaded
            task=None;done=0
            if missing and notify:
                from .progress import TaskProgress
                total=sum(read_json(self.catalog,{}).get('logs',{}).get(str(i),{}).get('records',0) for i in missing)
                task=TaskProgress('history-index',notify,[('load','Đọc log vào chỉ mục tạm',100)]);task.stage('load')
            for ident in missing:
                path=self.scan_path(ident)
                if not path.exists():
                    raise ValueError(f'Lượt #{ident} chưa có log metadata hoàn chỉnh; không coi là dữ liệu rỗng')
                guard=SpaceGuard(path.stat().st_size*12,operation='đọc log lịch sử vào chỉ mục tạm')
                db.space_guard=guard;db.set_progress_handler(guard.progress,4000)
                batch=[]
                try:
                    db.execute('DELETE FROM members WHERE scan_id=?',(ident,));db.execute('DELETE FROM errors WHERE scan_id=?',(ident,));db.commit()
                    for kind,value in logs.read(path,stop=stop):
                        if kind=='header' and value.get('scan',{}).get('id')!=ident:raise ValueError('Log thuộc lượt khác')
                        if kind=='header' and value.get('columns')!=list(logs.FIELDS):raise ValueError('Sai cột log snapshot')
                        if kind=='end':
                            expected=read_json(self.catalog,{}).get('logs',{}).get(str(ident),{})
                            if value.get('sha256')!=expected.get('sha256') or value.get('records')!=expected.get('records'):
                                raise ValueError('Log không khớp catalog đã xác minh')
                        if kind!='record':continue
                        done+=1
                        if isinstance(value,dict) and 'error' in value:
                            db.execute('INSERT INTO errors VALUES(?,?,?)',(ident,*value['error']));continue
                        if not isinstance(value,list) or len(value)!=len(logs.FIELDS):raise ValueError('Sai cột metadata trong log')
                        batch.append(dict(zip(logs.FIELDS,value)))
                        if len(batch)>=5000:
                            store.put(ident,batch);batch.clear();guard.check()
                            if task:task.advance(done,total,f'Đang mở log mốc #{ident}: {done:,} bản ghi')
                    if batch:store.put(ident,batch)
                    self.mark_loaded(db,ident)
                except BaseException:
                    db.set_progress_handler(None,0);db.rollback()
                    db.execute('DELETE FROM members WHERE scan_id=?',(ident,));db.execute('DELETE FROM errors WHERE scan_id=?',(ident,));db.commit();raise
                finally:db.set_progress_handler(None,0)
            if task:task.finish(f'Đã mở và xác minh {len(missing)} log trong chỉ mục tạm')
        finally:store.close()

    def invalidate_comparisons(self,ids):
        path=self.index/'comparisons.sqlite'
        if not ids or not path.exists():return
        db=sqlite3.connect(path,timeout=30)
        try:
            marks=','.join('?' for _ in ids)
            pairs=[r[0] for r in db.execute(f'SELECT id FROM pairs WHERE older IN ({marks}) OR newer IN ({marks})',(*ids,*ids))]
            for pair in pairs:
                db.execute('DELETE FROM scope_items WHERE scope_id IN (SELECT id FROM scopes WHERE pair_id=?)',(pair,))
                for table in ('scopes','diff_rows','query_counts'):db.execute(f'DELETE FROM {table} WHERE pair_id=?',(pair,))
                db.execute("UPDATE pairs SET status='archived',names_ready=0 WHERE id=?",(pair,))
            db.commit()
            catalog=read_json(self.catalog,{})
            write_json(self.catalog,{**catalog,'comparisons':rows(db,'pairs')})
        finally:db.close()

    def bind_comparisons(self,db):
        self.connections.add(db)
        with mutex(self.root):
            catalog=read_json(self.catalog,{})
            if not db.execute('SELECT 1 FROM pairs LIMIT 1').fetchone():
                for pair in catalog.get('comparisons',[]):
                    restore_rows(db,'pairs',[{**pair,'status':'archived','names_ready':0}])
                db.commit()
        def flush():
            with mutex(self.root):
                catalog=read_json(self.catalog,{})
                write_json(self.catalog,{**catalog,'comparisons':rows(db,'pairs')})
        db.after_commit=flush

    def bind_normalization(self,db):
        self.connections.add(db)
        with mutex(self.root):
            catalog=read_json(self.catalog,{})
            if not db.execute('SELECT 1 FROM datasets LIMIT 1').fetchone():
                restore_rows(db,'datasets',catalog.get('normalization',{}).get('datasets',[]))
                restore_rows(db,'preferences',catalog.get('normalization',{}).get('preferences',[]));db.commit()
            db.execute('CREATE TABLE IF NOT EXISTS archive_norm_loaded(dataset_id INTEGER PRIMARY KEY)');db.commit()
        def flush():
            with mutex(self.root):
                catalog=read_json(self.catalog,{})
                current=rows(db,'datasets');old={r['id'] for r in catalog.get('normalization',{}).get('datasets',[])}
                write_json(self.catalog,{**catalog,'normalization':{'datasets':current,'preferences':rows(db,'preferences')}})
                for ident in old-{r['id'] for r in current}:
                    (self.folder/'normalization'/f'dataset-{ident:08d}.jsonl.gz').unlink(missing_ok=True)
        db.after_commit=flush

    def publish_normalization(self,db,ident):
        from .normalization import FIELDS
        path=self.folder/'normalization'/f'dataset-{ident:08d}.jsonl.gz'
        columns=','.join('o.'+f for f in FIELDS)
        records=(list(r) for r in db.execute(f'SELECT p.path,{columns} FROM overrides o JOIN source.paths p ON p.id=o.path_id WHERE dataset_id=?',(ident,)))
        logs.write(path,{'dataset_id':ident,'columns':['path',*FIELDS]},records)
        db.execute('INSERT OR IGNORE INTO archive_norm_loaded VALUES(?)',(ident,));db.commit()

    def load_normalization(self,db,ident):
        from .normalization import FIELDS
        if db.execute('SELECT 1 FROM archive_norm_loaded WHERE dataset_id=?',(ident,)).fetchone():return
        path=self.folder/'normalization'/f'dataset-{ident:08d}.jsonl.gz'
        if not path.exists():raise ValueError('Thiếu log chuẩn hóa; bản gốc vẫn còn, chưa dùng dữ liệu rỗng')
        db.execute('CREATE TEMP TABLE IF NOT EXISTS log_overrides(path TEXT,'+','.join(f+' BLOB' for f in FIELDS)+')')
        db.execute('DELETE FROM log_overrides');batch=[]
        count=0
        for kind,value in logs.read(path):
            if kind=='header' and (value.get('dataset_id')!=ident or value.get('columns')!=['path',*FIELDS]):
                raise ValueError('Log chuẩn hóa thuộc dataset/schema khác')
            if kind=='record':
                if not isinstance(value,list) or len(value)!=len(FIELDS)+1:raise ValueError('Sai cột log chuẩn hóa')
                batch.append(value);count+=1
                if len(batch)>=5000:db.executemany('INSERT INTO log_overrides VALUES('+','.join('?' for _ in value)+')',batch);batch.clear()
        if batch:db.executemany('INSERT INTO log_overrides VALUES('+','.join('?' for _ in batch[0])+')',batch)
        db.execute('INSERT OR REPLACE INTO overrides SELECT ?,p.id,'+','.join('o.'+f for f in FIELDS)+' FROM log_overrides o JOIN source.paths p ON p.path=o.path',(ident,))
        if db.execute('SELECT COUNT(*) FROM overrides WHERE dataset_id=?',(ident,)).fetchone()[0]!=count:
            db.rollback();raise ValueError('Log chuẩn hóa có đường dẫn không khớp snapshot; không áp dụng thiếu dòng')
        db.execute('INSERT INTO archive_norm_loaded VALUES(?)',(ident,));db.commit()

    def close(self):
        if self.closed:return
        with mutex(self.root):
            self.closing=True
            try:return self._close()
            finally:self.closing=False

    def _close(self):
        from .elevation import alive
        with mutex(self.root):
            for db in list(self.connections):
                try:db.after_commit=None;db.close()
                except sqlite3.Error:pass
            state=read_json(self.state_path,{})
            state['owners']=[o for o in state.get('owners',[]) if alive(o) and o['pid']!=self.identity['pid']]
            if not state['owners']:
                # Recover a committed-but-not-yet-published log after an earlier
                # crash. Do not discard the index if publication fails.
                source=self.connect()
                try:
                    for row in source.execute("SELECT id FROM scans WHERE status<>'running'").fetchall():self.publish_scan(source,row['id'])
                    self.flush_source(source)
                finally:source.close()
                normal=self.index/'normalization.sqlite'
                if normal.exists():
                    db=sqlite3.connect(normal,uri=True);db.execute('ATTACH DATABASE ? AS source',((self.index/'history.sqlite').as_uri()+'?mode=ro',))
                    try:
                        for (ident,) in db.execute('SELECT id FROM datasets').fetchall():
                            if not (self.folder/'normalization'/f'dataset-{ident:08d}.jsonl.gz').exists():self.publish_normalization(db,ident)
                    finally:db.close()
            write_json(self.state_path,state)
            if not state['owners']:
                from .storage_maintenance import safe
                # Only three explicitly owned databases/sidecars. Never recurse
                # into a user-selected folder or remove unknown files.
                for name in ('history.sqlite','comparisons.sqlite','normalization.sqlite'):
                    for suffix in ('','-wal','-shm','-journal'):
                        try:safe(self.index/(name+suffix)).unlink(missing_ok=True)
                        except OSError:pass
                if not any(self.index.glob('*.sqlite*')):
                    self.state_path.unlink(missing_ok=True)
                    try:self.index.rmdir()
                    except OSError:pass
            self.closed=True


def get_manager():
    root=home();key=str(root)
    current=_managers.get(key)
    if current is not None and not current.closed and not current.closing:return current
    with _mutexes.setdefault(key,{'lock':threading.RLock(),'local':threading.local()})['lock']:
        current=_managers.get(key)
        if current is None or current.closed:_managers[key]=Index(root)
        return _managers[key]


def close_managers():
    for manager in list(_managers.values()):
        try:manager.close()
        except (OSError,ValueError,sqlite3.Error):pass


atexit.register(close_managers)
