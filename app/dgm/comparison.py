"""Indexed, bounded disk cache of immutable comparisons. Pages never redo diff."""
from __future__ import annotations
import hashlib
import json
import logging
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from .config import home, load, normalize, comparison_path_key
from .store import Store, now
from .platform_ops import ProcessCancelled
from .presence import Presence, enrich_presence

_build_lock = threading.RLock()
@contextmanager
def _locked(stop):
    while not _build_lock.acquire(timeout=.05):
        if stop.is_set(): raise ProcessCancelled()
    try:
        if stop.is_set(): raise ProcessCancelled()
        yield
    finally: _build_lock.release()

VERSION = 8
SCHEMA_VERSION = 4
SORTS = {"delta": "COALESCE(delta,-9223372036854775808)", "old": "COALESCE(before,-1)", "new": "COALESCE(after,-1)",
         "created": "created", "modified": "modified", "allocated": "COALESCE(allocated_delta,-9223372036854775808)",
         "kind": "kind", "change": "change", "path": "name_rank"}
SCOPE_SORTS = {"delta": "delta_key", "old": "before_key", "new": "after_key", "created": "created",
               "modified": "modified", "allocated": "allocated_key", "kind": "kind", "change": "change", "path": "name_rank"}
SORT_INDEX = {"delta":"diff_signed_delta", "old":"diff_old", "new":"diff_new", "created":"diff_created",
              "modified":"diff_modified", "allocated":"diff_allocated", "kind":"diff_kind", "change":"diff_change", "path":"diff_name"}


class Comparisons(Presence):
    def __init__(self, stop=None, notify=None):
        from .storage_safety import SpaceConnection
        from .history_backend import database_path,log_mode,manager
        self.stop = stop or threading.Event(); self.notify = notify or (lambda *a: None)
        self.path = database_path('comparisons.sqlite')
        self.db = sqlite3.connect(f"file:{self.path.as_posix()}?mode=rwc", uri=True, timeout=30,factory=SpaceConnection)
        self.db.row_factory = sqlite3.Row
        self.db.create_function("dgm_path_key", 1, comparison_path_key, deterministic=True)
        if not self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' LIMIT 1").fetchone():
            self.db.execute('PRAGMA auto_vacuum=INCREMENTAL')
        self.db.execute("PRAGMA journal_mode=WAL"); self.db.execute("PRAGMA cache_size=-16384")
        self.last_pulse = time.monotonic(); self.stage = "Chuẩn bị so sánh"
        self.db.set_progress_handler(self._progress, 4000)
        source = Store(readonly=True)
        try:
            has_keys = bool(source.db.execute("SELECT 1 FROM sqlite_master WHERE name='store_schema'").fetchone() and
                source.db.execute("SELECT 1 FROM store_schema WHERE key='path-keys-v1'").fetchone())
        finally: source.close()
        if not has_keys:
            source = Store(stop=self.stop,notify=self.notify); source.close()
        self.db.execute("ATTACH DATABASE ? AS source", (database_path('history.sqlite').as_uri()+'?mode=ro',))
        from .normalization import Normalizations
        norm=Normalizations(self.stop,self.notify);norm.close()
        self.db.execute('ATTACH DATABASE ? AS canonical', (database_path('normalization.sqlite').as_uri()+'?mode=ro',))
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS pairs(id INTEGER PRIMARY KEY,older INTEGER,newer INTEGER,version INTEGER,
          created TEXT,status TEXT,summary TEXT,touched TEXT,names_ready INTEGER DEFAULT 0,UNIQUE(older,newer,version));
        CREATE TABLE IF NOT EXISTS diff_rows(pair_id INTEGER,path_id INTEGER,parent_id INTEGER,kind TEXT,
          old_revision INTEGER,new_revision INTEGER,before INTEGER,after INTEGER,delta INTEGER,abs_delta INTEGER,
          max_size INTEGER,allocated INTEGER,allocated_before INTEGER,allocated_after INTEGER,allocated_delta INTEGER,
          created REAL,modified REAL,change TEXT,other_path_id INTEGER,name_rank INTEGER DEFAULT 0,
          PRIMARY KEY(pair_id,path_id)) WITHOUT ROWID;
        CREATE TABLE IF NOT EXISTS scopes(id INTEGER PRIMARY KEY,pair_id INTEGER,root_id INTEGER,touched TEXT,UNIQUE(pair_id,root_id));
        CREATE TABLE IF NOT EXISTS scope_items(scope_id INTEGER,path_id INTEGER,before_key INTEGER,after_key INTEGER,
          abs_delta INTEGER,allocated_key INTEGER,created REAL,modified REAL,kind TEXT,change TEXT,name_rank INTEGER,delta_key INTEGER,
          PRIMARY KEY(scope_id,path_id)) WITHOUT ROWID;
        CREATE INDEX IF NOT EXISTS diff_parent ON diff_rows(pair_id,parent_id,abs_delta DESC,path_id DESC);
        CREATE INDEX IF NOT EXISTS diff_signed_delta ON diff_rows(pair_id,COALESCE(delta,-9223372036854775808),path_id);
        CREATE INDEX IF NOT EXISTS diff_new ON diff_rows(pair_id,COALESCE(after,-1),path_id);
        CREATE INDEX IF NOT EXISTS diff_children_new ON diff_rows(pair_id,parent_id,kind,COALESCE(after,-1),path_id);
        CREATE TABLE IF NOT EXISTS query_counts(fingerprint TEXT PRIMARY KEY,pair_id INTEGER,total INTEGER);
        CREATE TABLE IF NOT EXISTS normalized_folders(scan_id INTEGER,path_id INTEGER,logical INTEGER,allocated INTEGER,
          PRIMARY KEY(scan_id,path_id)) WITHOUT ROWID;
        CREATE TABLE IF NOT EXISTS normalized_scans(scan_id INTEGER PRIMARY KEY);
        """)
        schema_version = self.db.execute('PRAGMA user_version').fetchone()[0]
        scope_columns = {r[1] for r in self.db.execute('PRAGMA table_info(scope_items)')}
        if schema_version < SCHEMA_VERSION or not {'delta_key','allocated_key'} <= scope_columns:
          with _build_lock:
            try:
                self.db.execute('BEGIN IMMEDIATE')
                columns={r[1] for r in self.db.execute('PRAGMA table_info(diff_rows)')}
                for name in ('allocated_before','allocated_after','allocated_delta'):
                    if name not in columns:self.db.execute(f'ALTER TABLE diff_rows ADD COLUMN {name} INTEGER')
                for name,declaration in (('presence',"TEXT DEFAULT 'both'"),('reason',"TEXT DEFAULT ''"),
                                         ('old_issue',"TEXT DEFAULT ''"),('new_issue',"TEXT DEFAULT ''"),('comparable','INTEGER DEFAULT 1')):
                    if name not in columns:self.db.execute(f'ALTER TABLE diff_rows ADD COLUMN {name} {declaration}')
                locked_version=self.db.execute('PRAGMA user_version').fetchone()[0]
                if locked_version<3:
                    # Optional orders are built on first use, not maintained for
                    # every row when the user only needs size/delta browsing.
                    for index in ('diff_delta','diff_allocated','diff_old','diff_created','diff_modified','diff_change','diff_kind','diff_name'):
                        self.db.execute(f'DROP INDEX IF EXISTS {index}')
                    for column in SCOPE_SORTS.values():
                        if column not in ('delta_key','after_key'):self.db.execute(f'DROP INDEX IF EXISTS scope_{column}')
                if 'delta_key' not in [r[1] for r in self.db.execute('PRAGMA table_info(scope_items)')]:
                    self.db.execute('ALTER TABLE scope_items ADD COLUMN delta_key INTEGER')
                    self.db.execute('UPDATE scope_items SET delta_key=COALESCE((SELECT c.delta FROM diff_rows c JOIN scopes t ON t.pair_id=c.pair_id WHERE t.id=scope_items.scope_id AND c.path_id=scope_items.path_id),-9223372036854775808)')
                if locked_version < 2:
                    self.db.execute('UPDATE scope_items SET allocated_key=COALESCE((SELECT c.allocated_delta FROM diff_rows c JOIN scopes t ON t.pair_id=c.pair_id WHERE t.id=scope_items.scope_id AND c.path_id=scope_items.path_id),-9223372036854775808)')
                if 'names_ready' not in [r[1] for r in self.db.execute('PRAGMA table_info(scopes)')]:
                    self.db.execute('ALTER TABLE scopes ADD COLUMN names_ready INTEGER DEFAULT 0')
                for column in ('delta_key','after_key'):
                    self.db.execute(f"CREATE INDEX IF NOT EXISTS scope_{column} ON scope_items(scope_id,{column},path_id)")
                self.db.execute(f'PRAGMA user_version={SCHEMA_VERSION}')
                self.db.commit()
            except BaseException:
                self.db.rollback();self.db.close();raise
        self.db.set_progress_handler(self._progress, 4000)
        self.last_pulse = time.monotonic(); self.stage = "Chuẩn bị so sánh"
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS folder_parts(pair_id INTEGER,path_id INTEGER,common_before INTEGER,common_after INTEGER,
            private_before INTEGER,private_after INTEGER,unverified_count INTEGER,PRIMARY KEY(pair_id,path_id)) WITHOUT ROWID;
          CREATE TRIGGER IF NOT EXISTS parts_delete AFTER DELETE ON pairs BEGIN DELETE FROM folder_parts WHERE pair_id=OLD.id; END;
          CREATE TRIGGER IF NOT EXISTS parts_archive AFTER UPDATE OF status ON pairs WHEN NEW.status='archived' BEGIN DELETE FROM folder_parts WHERE pair_id=NEW.id; END;
          -- Retain the v7 table only for retiring old caches; v8 has one
          -- indexed row set with independent presence/evidence columns.
          CREATE TABLE IF NOT EXISTS private_rows(pair_id INTEGER,path_id INTEGER,max_size INTEGER);
          CREATE UNIQUE INDEX IF NOT EXISTS private_pk ON private_rows(pair_id,path_id);
          CREATE INDEX IF NOT EXISTS private_max ON private_rows(pair_id,max_size,path_id);
          CREATE TRIGGER IF NOT EXISTS private_delete AFTER DELETE ON pairs BEGIN
            DELETE FROM private_rows WHERE pair_id=OLD.id; END;
          CREATE TRIGGER IF NOT EXISTS private_archive AFTER UPDATE OF status ON pairs WHEN NEW.status='archived' BEGIN
            DELETE FROM private_rows WHERE pair_id=NEW.id; END;
        ''')
        if 'dataset_signature' not in {r[1] for r in self.db.execute('PRAGMA table_info(pairs)')}:
            # History and the browser can open the same cache concurrently.
            # Recheck under SQLite's cross-process write lock, not only a
            # Python lock or a stale pre-lock PRAGMA result.
            try:
                self.db.execute('BEGIN IMMEDIATE')
                if 'dataset_signature' not in {r[1] for r in self.db.execute('PRAGMA table_info(pairs)')}:
                    self.db.execute('ALTER TABLE pairs ADD COLUMN dataset_signature TEXT')
                self.db.commit()
            except BaseException:
                self.db.rollback();self.db.close();raise
        if log_mode():manager().bind_comparisons(self.db)

    def _progress(self):
        if self.stop.is_set():
            return 1
        if self.db.space_guard and self.db.space_guard.progress():return 1
        if time.monotonic() - self.last_pulse > 2:
            self.notify("info", {"message": self.stage + " • vẫn đang xử lý"})
            logging.getLogger("dgm").debug("COMPARE heartbeat %s", self.stage); self.last_pulse = time.monotonic()
        return 0

    def close(self):
        self.db.set_progress_handler(None, 0); self.db.close()

    def sort_index(self, sort, *, scoped=False):
        column=SCOPE_SORTS[sort] if scoped else SORTS[sort]
        name='scope_'+column if scoped else SORT_INDEX[sort]
        if self.db.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name=?",(name,)).fetchone():return
        with _locked(self.stop):
            self.stage='Lập chỉ mục cột '+sort+' lần đầu'+(' cho nhánh thư mục' if scoped else '')
            self.notify('info',{'message':self.stage})
            table,key=('scope_items','scope_id') if scoped else ('diff_rows','pair_id')
            try:
                self.db.execute(f'CREATE INDEX IF NOT EXISTS {name} ON {table}({key},{column},path_id)')
                self.db.commit()
            except BaseException:
                self.rollback_stage();raise

    def rollback_stage(self):
        # An interrupting progress callback can interrupt ROLLBACK as well.
        # Release the transaction before allowing a retry on this connection.
        self.db.set_progress_handler(None,0)
        try:self.db.rollback()
        finally:self.db.set_progress_handler(self._progress,4000)

    def presence_sort_index(self,sort):
        # A tiny Riêng group must not require scanning millions of Chung rows
        # on every page. Build this optional order once, on the first request.
        name='presence_'+sort
        if not self.db.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name=?",(name,)).fetchone():
            with _locked(self.stop):
                self.stage='Lập thứ tự '+sort+' theo Chung/Riêng (một lần)';self.notify('info',{'message':self.stage})
                try:
                    self.db.execute(f"CREATE INDEX IF NOT EXISTS {name} ON diff_rows(pair_id,(presence='both'),{SORTS[sort]},path_id)")
                    self.db.commit()
                except BaseException:self.rollback_stage();raise
        return name

    def overview(self, older, newer, *, common=False):
        store = Store(readonly=True)
        try:
            b = store.scan(newer); a = store.scan(older) if older else None
            if b["status"] not in ("complete", "partial") or (a and a["status"] not in ("complete", "partial")):
                raise ValueError("Lượt đang chạy/bị hủy/lỗi không dùng để so sánh.")
            if a and (a['started'],a['id']) >= (b['started'],b['id']):
                raise ValueError('Mốc cũ phải trước mốc mới; hãy đổi vị trí hai mốc.')
            from .snapshot_scope import differences, common_policy
            diff=differences(a,b) if a else []
            return {"older": a, "newer": b, "old_totals": json.loads(a["summary"] or "{}") if a else None,
                    "new_totals": json.loads(b["summary"] or "{}"), 'scope_differences':diff,
                    'common_scope':common_policy(a,b) if a else None}
        finally:
            store.close()

    def _normalize_folders(self, scan_id):
        if not scan_id or self.db.execute('SELECT 1 FROM normalized_scans WHERE scan_id=?',(scan_id,)).fetchone():
            return
        self.stage='Đang chuẩn hóa tổng thư mục từ file trong snapshot'
        self.notify('info',{'message':self.stage})
        direct={row['parent']:[row['logical'] or 0,row['allocated']] for row in self.db.execute("""SELECT p.parent_key AS parent,
          SUM(r.logical) AS logical,CASE WHEN SUM(r.allocated IS NULL)>0 THEN NULL ELSE SUM(r.allocated) END AS allocated FROM source.members m
          JOIN source.paths p ON p.id=m.path_id JOIN source.revisions r ON r.id=m.revision_id
          WHERE m.scan_id=? AND r.kind='file' GROUP BY p.parent_key""",(scan_id,))}
        folders=self.db.execute("""SELECT m.path_id,p.path,p.parent FROM source.members m
          JOIN source.paths p ON p.id=m.path_id JOIN source.revisions r ON r.id=m.revision_id
          WHERE m.scan_id=? AND r.kind='folder' ORDER BY LENGTH(p.path) DESC,m.path_id""",(scan_id,)).fetchall()
        exact={row['path']:row['path_id'] for row in folders};by_key={}
        for row in folders:by_key.setdefault(comparison_path_key(row['path']),[]).append(row['path_id'])
        totals={row['path_id']:list(direct.get(comparison_path_key(row['path']),(0,0))) for row in folders}
        for index,row in enumerate(folders):
            if self.stop.is_set():raise ProcessCancelled()
            parent=exact.get(row['parent'])
            if parent is None:
                candidates=by_key.get(comparison_path_key(row['parent']),[])
                if len(candidates)==1:parent=candidates[0]
            if parent in totals and parent!=row['path_id']:
                totals[parent][0]+=totals[row['path_id']][0]
                a,b=totals[parent][1],totals[row['path_id']][1]
                totals[parent][1]=None if a is None or b is None else a+b
            if index and index%10000==0:self.notify('progress',{'count':index,'total':len(folders),'path':'Chuẩn hóa dung lượng thư mục'})
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self.db.execute('DELETE FROM normalized_folders WHERE scan_id=?',(scan_id,))
            self.db.executemany('INSERT INTO normalized_folders VALUES(?,?,?,?)',
                ((scan_id,path_id,values[0],values[1]) for path_id,values in totals.items()))
            self.db.execute('INSERT OR IGNORE INTO normalized_scans VALUES(?)',(scan_id,));self.db.commit()
        except BaseException:
            self.db.rollback();raise

    def _comparison_map(self, table, scan_id):
        self.db.execute(f'DROP TABLE IF EXISTS temp.{table}')
        self.db.execute(f'DROP TABLE IF EXISTS temp.{table}_allocation')
        self.db.execute(f'DROP TABLE IF EXISTS temp.{table}_owners')
        self.db.execute(f"""CREATE TEMP TABLE {table} AS SELECT m.path_id,p.path,p.parent,
          p.path_key,p.parent_key,m.revision_id,COALESCE(ov.kind,r.kind) AS kind,
          CASE WHEN ov.path_id IS NOT NULL THEN ov.logical ELSE r.logical END AS logical,
          CASE WHEN ov.path_id IS NOT NULL THEN ov.allocated ELSE r.allocated END AS allocated,
          CASE WHEN ? THEN CAST(CASE WHEN ov.path_id IS NOT NULL THEN ov.created ELSE r.created END AS INTEGER)
            ELSE CASE WHEN ov.path_id IS NOT NULL THEN ov.created ELSE r.created END END AS created,
          CASE WHEN ? THEN CAST(CASE WHEN ov.path_id IS NOT NULL THEN ov.modified ELSE r.modified END AS INTEGER)
            ELSE CASE WHEN ov.path_id IS NOT NULL THEN ov.modified ELSE r.modified END END AS modified,
          COALESCE(ov.identity,r.identity) AS identity,COALESCE(ov.attrs,r.attrs) AS attrs,COALESCE(ov.status,r.status) AS status,
          COUNT(*) OVER(PARTITION BY p.path_key) AS key_count
          FROM source.members m JOIN source.paths p ON p.id=m.path_id JOIN source.revisions r ON r.id=m.revision_id
          LEFT JOIN canonical.overrides ov ON ov.dataset_id=? AND ov.path_id=m.path_id WHERE m.scan_id=?""",
          (self.second_precision,self.second_precision,self.dataset_ids.get(scan_id),scan_id))
        self.db.execute(f'CREATE UNIQUE INDEX temp.{table}_id ON {table}(path_id)')
        self.db.execute(f'CREATE INDEX temp.{table}_key ON {table}(path_key)')
        self.db.execute(f'ALTER TABLE {table} ADD COLUMN parent_path_id INTEGER')
        self.db.execute(f"""UPDATE {table} AS child SET parent_path_id=(
          SELECT CASE WHEN COUNT(*)=1 THEN MIN(parent.path_id) END FROM {table} AS parent
          WHERE parent.path_key=child.parent_key)""")

    def _common_folder_totals(self, table):
        from .snapshot_scope import REASONS
        has_reasons=table.startswith('private_')
        def bits(value):
            tags=set((value or '').replace(',','|').split('|'))
            return sum(1<<i for i,k in enumerate(REASONS) if k in tags)
        extra=",GROUP_CONCAT(DISTINCT reason) AS reasons" if has_reasons else ''
        direct={r['parent_key']:[r['logical'],r['allocated'],r['n'],bits(r['reasons']) if has_reasons else 0] for r in self.db.execute(f'''SELECT parent_key,
          CASE WHEN SUM(logical IS NULL)>0 THEN NULL ELSE SUM(logical) END AS logical,
          CASE WHEN SUM(allocated IS NULL)>0 THEN NULL ELSE SUM(allocated) END AS allocated,COUNT(*) AS n {extra}
          FROM {table} WHERE kind<>'folder' GROUP BY parent_key''')}
        folders=self.db.execute(f"SELECT path_id,path_key,parent_key FROM {table} WHERE kind='folder' ORDER BY LENGTH(path) DESC").fetchall()
        from .folder_totals import rollup
        totals,_=rollup(folders,direct,self.stop)
        self.db.executemany(f'UPDATE {table} SET logical=?,allocated=? WHERE path_id=?',((v[0],v[1],pid) for pid,v in totals.items()))
        return totals

    def _map_totals(self, table, original):
        totals=dict(self.db.execute(f'''SELECT CASE WHEN SUM(kind='file' AND logical IS NULL)>0 THEN NULL
          ELSE COALESCE(SUM(CASE WHEN kind='file' THEN logical ELSE 0 END),0) END AS logical,
          CASE WHEN SUM(kind='file' AND allocated IS NULL)>0 THEN NULL
          ELSE COALESCE(SUM(CASE WHEN kind='file' THEN allocated ELSE 0 END),0) END AS allocated,
          COALESCE(SUM(kind='file'),0) AS files,COALESCE(SUM(kind='folder'),0) AS folders,COALESCE(SUM(kind='link'),0) AS links,
          COALESCE(SUM(CASE WHEN kind='file' THEN allocated ELSE 0 END),0) AS allocated_known,
          COALESCE(SUM(kind='file' AND allocated IS NULL),0) AS allocated_unknown_files,
          COALESCE(SUM(kind='file' AND logical IS NULL),0) AS logical_unknown_files FROM {table}''').fetchone())
        return {**(original or {}),**totals,'scope_note':'Tổng file ghi nhận của nhóm hiện diện; không phải Windows đã dùng',
                'common_scope':self.common_scope}

    def prepare(self, older, newer, *, common=False):
        from .history_backend import snapshots,log_mode
        if not log_mode():return self._prepare_loaded(older,newer,common=common)
        import uuid
        original=self.notify;ident=uuid.uuid4().hex;percent=[0]
        def forward(event,data):
            if event=='task-progress':
                value=int(data.get('percent',0));source=data.get('task')
                mapped=value//5 if source=='history-index' else 20+value*80//100 if source=='comparison' else percent[0]
                percent[0]=max(percent[0],mapped)
                original(event,{**data,'task':'comparison','id':ident,'percent':percent[0]})
            else:original(event,data)
        self.notify=forward
        try:
            self.overview(older,newer,common=common)
            with snapshots([older,newer],stop=self.stop,notify=forward):return self._prepare_loaded(older,newer,common=common)
        finally:self.notify=original

    def _prepare_loaded(self,older,newer,*,common=False):
        self.db.space_guard=None
        from .storage_metrics import begin as begin_storage, finish as finish_storage
        from .progress import TaskProgress
        task=TaskProgress('comparison',self.notify,[('read','Đọc hai mốc lịch sử',10),('normalize','Chuẩn bị dung lượng thư mục',30),
            ('index','Lập chỉ mục đường dẫn cũ/mới',55),('diff','Tính mức thay đổi toàn phạm vi',85),('save','Lưu so sánh và số đo data/log',100)])
        task.stage('read')
        storage_meter = begin_storage("comparison")
        overview = self.overview(older, newer,common=common)
        self.common_scope=overview['common_scope']
        task.complete('Đã đọc mốc cũ/mới và kiểm tra phạm vi Chung/Riêng')
        with _locked(self.stop):
            from .normalization import Normalizations
            norm=Normalizations(self.stop,self.notify)
            try:
                for ident in (older,newer):
                    if ident and not norm.original_enabled(ident):norm.inspect(ident,apply=True)
                self.dataset_ids={}
                for ident in (older,newer):
                    current=norm.current(ident) if ident else None
                    self.dataset_ids[ident]=current['id'] if current else None
                signature=json.dumps([self.dataset_ids[ident] for ident in (older,newer)])
            finally:norm.close()
            overview=self.overview(older,newer,common=common)
            pair_version=VERSION  # Automatic partitions are identical in CLI/GUI.
            existing = self.db.execute("SELECT * FROM pairs WHERE older=? AND newer=? AND version=?", (older or 0, newer, pair_version)).fetchone()
            if existing and existing["status"] == "ready" and existing['dataset_signature']==signature:
                summary = json.loads(existing["summary"] or "{}")
                logging.getLogger("dgm").info("COMPARE cached old=%s new=%s", older, newer)
                task.finish(f"Đã đọc cache: {summary.get('common_rows',0):,} mục Chung • {summary.get('private_rows',0):,} mục Riêng")
                return existing["id"]
            from .storage_safety import SpaceGuard
            entries=sum(int((t or {}).get('files') or 0)+int((t or {}).get('folders') or 0) for t in (overview['old_totals'],overview['new_totals']))
            self.db.space_guard=SpaceGuard(entries*1400,operation='lập cache so sánh và dữ liệu tạm')
            self.trim(keep=existing["id"] if existing else None, target_pairs=1)
            self.db.execute("INSERT OR IGNORE INTO pairs(older,newer,version,created,status,touched) VALUES(?,?,?,?,?,?)", (older or 0, newer, pair_version, now(), "building", now()))
            pair = self.db.execute("SELECT id FROM pairs WHERE older=? AND newer=? AND version=?", (older or 0, newer, pair_version)).fetchone()[0]
            self.db.commit()
            try:
                task.stage('normalize')
                # Current scans record where folder totals came from: WizTree's
                # export, or an aggregation already performed by the scanner.
                # Only pre-provenance snapshots need the expensive compatibility
                # normalization, which is cached once per snapshot.
                cross = older and overview['older']['engine'] != overview['newer']['engine']
                self.second_precision=bool(cross)
                task.complete('Đã chuẩn bị tổng thư mục cho hai mốc')
                task.stage('index')
                self.stage='Đọc bản đồ dữ liệu đã chuẩn hóa của mốc mới'
                self._comparison_map('compare_new',newer)
                self.stage='Đọc bản đồ dữ liệu đã chuẩn hóa của mốc cũ'
                self._comparison_map('compare_old',older or 0)
                self.stage='Chia vùng Chung/Riêng và đối soát tổng thư mục'
                self.partition_maps(overview)
                task.complete('Đã lập chỉ mục đường dẫn cũ/mới')
                task.stage('diff')
                self.db.execute("DELETE FROM diff_rows WHERE pair_id=?", (pair,))
                self.db.execute("DELETE FROM query_counts WHERE pair_id=?",(pair,))
                self.db.execute('DELETE FROM scope_items WHERE scope_id IN (SELECT id FROM scopes WHERE pair_id=?)',(pair,))
                self.db.execute('DELETE FROM scopes WHERE pair_id=?',(pair,))
                self.stage = "Đang lập chỉ mục chi tiết so sánh (một lần)"
                self.notify("info", {"message": self.stage})
                uncertain = False  # Evidence is applied independently of presence below.
                self.db.execute("""INSERT INTO diff_rows(pair_id,path_id,parent_id,kind,old_revision,new_revision,before,after,
                delta,abs_delta,max_size,allocated,allocated_before,allocated_after,allocated_delta,created,modified,change,other_path_id)
                SELECT ?,n.path_id,CASE WHEN n.parent_path_id=n.path_id THEN NULL ELSE n.parent_path_id END,n.kind,o.revision_id,n.revision_id,
                o.logical,n.logical,CASE WHEN ?=0 OR n.key_count>1 OR d.matches IS NOT NULL OR e.matches IS NOT NULL THEN NULL ELSE n.logical-COALESCE(o.logical,0) END,
                CASE WHEN ?=0 OR n.key_count>1 OR d.matches IS NOT NULL OR e.matches IS NOT NULL THEN -1 ELSE ABS(n.logical-COALESCE(o.logical,0)) END,MAX(n.logical,COALESCE(o.logical,0)),
                n.allocated,o.allocated,n.allocated,CASE WHEN ?=0 OR n.key_count>1 OR d.matches IS NOT NULL OR e.matches IS NOT NULL THEN NULL
                  WHEN o.path_id IS NULL THEN n.allocated ELSE n.allocated-o.allocated END,
                COALESCE(n.created,0),COALESCE(n.modified,0),
                CASE WHEN n.key_count>1 OR d.matches IS NOT NULL OR e.matches IS NOT NULL THEN 'unknown' WHEN ?=0 THEN 'first_scan' WHEN o.path_id IS NULL THEN CASE WHEN ? THEN 'unknown' ELSE 'new' END
                  WHEN n.status IN ('changed-during-scan','unavailable-after-export') OR o.status IN ('changed-during-scan','unavailable-after-export') THEN 'unknown'
                  WHEN o.path_id IS NOT NULL AND n.kind<>o.kind THEN 'replaced'
                  WHEN n.identity<>'' AND o.identity<>'' AND (n.identity LIKE 'wiz:%')=(o.identity LIKE 'wiz:%') AND n.identity<>o.identity THEN 'replaced'
                  WHEN n.logical>o.logical THEN 'grown' WHEN n.logical<o.logical THEN 'shrunk'
                  WHEN n.allocated<>o.allocated OR n.modified<>o.modified
                    OR n.created<>o.created OR n.attrs<>o.attrs THEN 'modified' ELSE 'same' END,
                NULL
                FROM compare_new n
                LEFT JOIN compare_old o ON o.path_key=n.path_key AND n.key_count=1 AND o.key_count=1
                LEFT JOIN (SELECT path_key,COUNT(*) AS matches FROM compare_old GROUP BY path_key HAVING COUNT(*)>1) d ON d.path_key=n.path_key
                LEFT JOIN (SELECT path_key,COUNT(*) AS matches FROM compare_new GROUP BY path_key HAVING COUNT(*)>1) e ON e.path_key=n.path_key
                """, (pair, older or 0, older or 0, older or 0, older or 0, int(uncertain)))
                if older:
                    self.db.execute("""INSERT OR IGNORE INTO diff_rows(pair_id,path_id,parent_id,kind,old_revision,new_revision,before,after,
                    delta,abs_delta,max_size,allocated,allocated_before,allocated_after,allocated_delta,created,modified,change)
                    SELECT ?,o.path_id,CASE WHEN o.parent_path_id=o.path_id THEN NULL ELSE o.parent_path_id END,o.kind,o.revision_id,NULL,o.logical,NULL,
                    CASE WHEN ? OR o.key_count>1 OR e.matches IS NOT NULL THEN NULL ELSE -o.logical END,
                    CASE WHEN ? OR o.key_count>1 OR e.matches IS NOT NULL THEN -1 ELSE o.logical END,
                    o.logical,o.allocated,o.allocated,NULL,CASE WHEN ? OR o.key_count>1 OR e.matches IS NOT NULL THEN NULL ELSE -o.allocated END,
                    COALESCE(o.created,0),COALESCE(o.modified,0),CASE WHEN o.key_count>1 OR e.matches IS NOT NULL OR ? THEN 'unknown' ELSE 'missing' END
                    FROM compare_old o
                    LEFT JOIN compare_new n ON n.path_key=o.path_key AND n.key_count=1 AND o.key_count=1
                    LEFT JOIN (SELECT path_key,COUNT(*) AS matches FROM compare_new GROUP BY path_key HAVING COUNT(*)>1) e ON e.path_key=o.path_key
                    WHERE n.path_id IS NULL""", (pair, int(uncertain), int(uncertain), int(uncertain), int(uncertain)))
                self.annotate_presence(pair)
                if older:self._moves(pair, older, newer)
                self.db.execute("UPDATE diff_rows SET delta=NULL,abs_delta=-1,allocated_delta=NULL WHERE pair_id=? AND change='unknown'", (pair,))
                self.finish_presence(pair,overview)
                task.complete('Đã tách có ở hai mốc/chỉ một mốc; tính thay đổi đủ bằng chứng, tách phần không quét/chưa rõ')
                task.stage('save')
                summary = {**overview, "comparison_version": VERSION,
                    "compatibility": self.compatibility(overview),
                    'completed_steps':[*task.completed,'Đã lưu kết quả so sánh và số đo data/log']}
                self.db.execute("UPDATE pairs SET status='ready',summary=?,touched=?,names_ready=0,dataset_signature=? WHERE id=?", (json.dumps(summary, ensure_ascii=False), now(),signature, pair))
                self.db.commit()
                logging.getLogger("dgm").info("COMPARE ready old=%s new=%s rows=%s verified_delta=%s unverified_files=%s", older, newer, summary["rows"],summary['verified_delta'],summary['unverified_all_files'])
                self.trim(keep=pair)
                summary["storage"] = finish_storage(storage_meter)
                self.db.execute("UPDATE pairs SET summary=?,touched=? WHERE id=?", (json.dumps(summary, ensure_ascii=False), now(), pair))
                self.db.commit()
                task.finish(f"Đã lưu {summary['common_rows']:,} mục Chung • {summary['private_rows']:,} mục Riêng")
                return pair
            except BaseException as exc:
                self.db.set_progress_handler(None, 0); self.db.rollback()
                self.db.execute("UPDATE pairs SET status='failed' WHERE id=?", (pair,)); self.db.commit()
                if self.db.space_guard and self.db.space_guard.error:raise self.db.space_guard.error from exc
                if self.stop.is_set() and isinstance(exc, sqlite3.OperationalError):
                    raise ProcessCancelled() from exc
                raise

    @staticmethod
    def compatibility(overview):
        old, new = overview.get('old_totals') or {}, overview['new_totals']
        contracts = [x.get('contract', {}) for x in (old, new)]
        engines = [overview['older']['engine'] if overview['older'] else None, overview['newer']['engine']]
        return {'engines': engines, 'contract_versions': [x.get('version') for x in contracts],
                'cross_engine': bool(engines[0] and engines[0] != engines[1]),
                'timestamp_precision': 'seconds' if engines[0] and engines[0]!=engines[1] else 'source',
                'legacy_metadata': bool(overview['older'] and not contracts[0].get('version')),
                'coverage_unverified': bool(old.get('coverage_unverified') or new.get('coverage_unverified')),
                'note': 'So sánh metadata đã lưu; thiếu quyền/metadata không được coi là dung lượng 0 hoặc file đã xóa.'}

    def _coverage_unknown(self, pair, overview):
        # New snapshots describe uncertainty per root, not by engine name.
        # Legacy snapshots retain their conservative all-scope behavior above.
        roots=set()
        for totals in (overview.get('old_totals') or {}, overview['new_totals']):
            roots.update(totals.get('coverage',{}).get('unknown_roots',[]))
        for root in roots:
            key=comparison_path_key(root);prefix=key.rstrip('\\')+'\\'
            self.db.execute("""UPDATE diff_rows SET change='unknown' WHERE pair_id=? AND change IN ('new','missing')
              AND path_id IN (SELECT id FROM source.paths WHERE path_key=? OR (path_key>=? AND path_key<?))""",
              (pair,key,prefix,prefix+'\U0010ffff'))

    def _moves(self, pair, older, newer):
        for label, scan in (("old", older), ("new", newer)):
            self.db.execute(f"DROP TABLE IF EXISTS temp.uid_{label}")
            self.db.execute(f"""CREATE TEMP TABLE uid_{label} AS SELECT identity,kind,MIN(path_id) AS path_id,
            MIN(created) AS created,MIN(logical) AS logical,MIN(allocated) AS allocated,COUNT(*) AS count
            FROM compare_{label} WHERE identity<>'' GROUP BY identity,kind""")
            self.db.execute(f"CREATE INDEX temp.uid_{label}_key ON uid_{label}(identity,kind)")
        self.db.execute("DROP TABLE IF EXISTS temp.moves")
        self.db.execute("""CREATE TEMP TABLE moves AS SELECT a.path_id AS old_id,b.path_id AS new_id,a.logical AS before,b.logical AS after,
        a.allocated AS allocated_before,b.allocated AS allocated_after
        FROM uid_old a JOIN uid_new b ON b.identity=a.identity AND b.kind=a.kind
        JOIN diff_rows x ON x.pair_id=? AND x.path_id=a.path_id AND x.change='missing'
        JOIN diff_rows y ON y.pair_id=? AND y.path_id=b.path_id AND y.change='new'
        WHERE a.count=1 AND b.count=1 AND ABS(a.created-b.created)<0.001""", (pair, pair))
        self.db.execute("CREATE UNIQUE INDEX temp.move_new ON moves(new_id)"); self.db.execute("CREATE UNIQUE INDEX temp.move_old ON moves(old_id)")
        self.db.execute("""UPDATE diff_rows SET change='moved',before=(SELECT before FROM moves WHERE new_id=path_id),
        delta=after-(SELECT before FROM moves WHERE new_id=path_id),abs_delta=ABS(after-(SELECT before FROM moves WHERE new_id=path_id)),
        allocated_before=(SELECT allocated_before FROM moves WHERE new_id=path_id),
        allocated_delta=(SELECT allocated_after-allocated_before FROM moves WHERE new_id=path_id),
        max_size=MAX(after,(SELECT before FROM moves WHERE new_id=path_id)),
        other_path_id=(SELECT old_id FROM moves WHERE new_id=path_id) WHERE pair_id=? AND path_id IN (SELECT new_id FROM moves)""", (pair,))
        self.db.execute("""UPDATE diff_rows SET change='moved_from',delta=0,abs_delta=0,allocated_delta=0,
        other_path_id=(SELECT new_id FROM moves WHERE old_id=path_id) WHERE pair_id=? AND path_id IN (SELECT old_id FROM moves)""", (pair,))

    def _inaccessible(self, pair, older, newer):
        # Prefix ranges mark descendants; ancestors are explicitly indexed IDs.
        for row in self.db.execute("SELECT DISTINCT path FROM source.errors WHERE scan_id IN (?,?)", (older, newer)).fetchall():
            path = row[0]; key=comparison_path_key(path);prefix=key.rstrip("\\")+"\\"
            self.db.execute("""UPDATE diff_rows SET change='unknown' WHERE pair_id=? AND path_id IN
            (SELECT id FROM source.paths WHERE path_key=? OR (path_key>=? AND path_key<?))""", (pair, key, prefix, prefix+'\U0010ffff'))
            for ancestor in Path(path).parents:
                self.db.execute("UPDATE diff_rows SET change='unknown' WHERE pair_id=? AND kind='folder' AND path_id IN (SELECT id FROM source.paths WHERE path_key=?)", (pair, comparison_path_key(ancestor)))

    def names(self, pair, scope=None):
        table, identifier = ('pairs',pair) if scope is None else ('scopes',scope)
        if self.db.execute(f"SELECT names_ready FROM {table} WHERE id=?", (identifier,)).fetchone()[0]:
            return
        with _locked(self.stop):
            if self.db.execute(f"SELECT names_ready FROM {table} WHERE id=?",(identifier,)).fetchone()[0]:return
            self.stage = 'Đang lập thứ tự tên '+('toàn phạm vi' if scope is None else 'chỉ trong nhánh đã chọn')+' (một lần)'
            self.notify('info',{'message':self.stage})
            rows, key = ('diff_rows','pair_id') if scope is None else ('scope_items','scope_id')
            try:
                self.db.execute('BEGIN IMMEDIATE')
                if self.db.execute(f'SELECT names_ready FROM {table} WHERE id=?',(identifier,)).fetchone()[0]:
                    self.db.commit();return
                self.db.execute('DROP TABLE IF EXISTS temp.name_order')
                self.db.execute(f"""CREATE TEMP TABLE name_order AS SELECT c.path_id,ROW_NUMBER() OVER(ORDER BY p.path COLLATE NOCASE,c.path_id) AS rank
                FROM {rows} c JOIN source.paths p ON p.id=c.path_id WHERE c.{key}=?""",(identifier,))
                self.db.execute('CREATE UNIQUE INDEX temp.name_id ON name_order(path_id)')
                self.db.execute(f"UPDATE {rows} SET name_rank=(SELECT rank FROM name_order WHERE path_id={rows}.path_id) WHERE {key}=?",(identifier,))
                self.db.execute(f'UPDATE {table} SET names_ready=1 WHERE id=?',(identifier,));self.db.commit()
            except BaseException:
                self.rollback_stage();raise

    def scope(self, pair, root_id, recursive=True):
        # Source path IDs are positive. Negative lookup keys distinguish a cached
        # direct-children name order from a recursive subtree, without replacing
        # the v1.0 cache schema or rebuilding its million-row comparisons.
        lookup=root_id if recursive else -root_id
        with _locked(self.stop):
            row=self.db.execute('SELECT id FROM scopes WHERE pair_id=? AND root_id=?',(pair,lookup)).fetchone()
            if row:
                return row[0]
            self.stage='Đang lập chỉ mục '+('nhánh thư mục' if recursive else 'các mục trực tiếp trong thư mục')+' (một lần)'
            self.notify('info',{'message':self.stage})
            try:
                self.db.execute('BEGIN IMMEDIATE')
                # Another CLI/GUI process may have committed this scope while
                # BEGIN waited for its writer. Recheck under the SQLite lock.
                row=self.db.execute('SELECT id FROM scopes WHERE pair_id=? AND root_id=?',(pair,lookup)).fetchone()
                if row:
                    self.db.execute('UPDATE scopes SET touched=? WHERE id=?',(now(),row[0]));self.db.commit();return row[0]
                self.db.execute('INSERT INTO scopes(pair_id,root_id,touched) VALUES(?,?,?)',(pair,lookup,now()))
                scope=self.db.execute('SELECT id FROM scopes WHERE pair_id=? AND root_id=?',(pair,lookup)).fetchone()[0]
                fields="""INSERT INTO scope_items(scope_id,path_id,before_key,after_key,abs_delta,allocated_key,created,modified,kind,change,name_rank,delta_key)
                SELECT ?,c.path_id,COALESCE(c.before,-1),COALESCE(c.after,-1),c.abs_delta,COALESCE(c.allocated_delta,-9223372036854775808),c.created,c.modified,c.kind,c.change,0,COALESCE(c.delta,-9223372036854775808) """
                if recursive:
                    root_key=self.db.execute('SELECT path_key FROM source.paths WHERE id=?',(root_id,)).fetchone()[0]
                    prefix=root_key.rstrip('\\')+'\\'
                    self.db.execute(fields+'''FROM source.paths p INDEXED BY paths_key
                      JOIN diff_rows c ON c.pair_id=? AND c.path_id=p.id
                      WHERE p.path_key=? OR (p.path_key>=? AND p.path_key<?)''',(scope,pair,root_key,prefix,prefix+'\U0010ffff'))
                else:
                    self.db.execute(fields+'FROM diff_rows c INDEXED BY diff_parent WHERE c.pair_id=? AND c.parent_id=?',(scope,pair,root_id))
                for stale in self.db.execute('SELECT id FROM scopes WHERE pair_id=? AND id<>? ORDER BY touched DESC,id DESC LIMIT -1 OFFSET 2',(pair,scope)).fetchall():
                    self.db.execute('DELETE FROM scope_items WHERE scope_id=?',(stale[0],));self.db.execute('DELETE FROM scopes WHERE id=?',(stale[0],))
                self.db.commit();return scope
            except BaseException:
                self.rollback_stage();raise

    def page(self, pair, *, limit=20, cursor=None, sort="delta", direction="desc", minimum=0, kind="all", changes="all", search="", root=None, recursive=True, folders_only=False, section='all', reason=None, presence='all'):
        from .history_backend import snapshots,log_mode
        if log_mode():
            info=self.db.execute('SELECT older,newer FROM pairs WHERE id=?',(pair,)).fetchone()
            if info is None:raise ValueError('Không tìm thấy cặp mốc')
            with snapshots(info,stop=self.stop,notify=self.notify):
                if self.db.execute('SELECT status FROM pairs WHERE id=?',(pair,)).fetchone()[0]!='ready':self.prepare(info[0] or None,info[1])
                return self._page_loaded(pair,limit=limit,cursor=cursor,sort=sort,direction=direction,minimum=minimum,kind=kind,changes=changes,search=search,root=root,recursive=recursive,folders_only=folders_only,section=section,reason=reason,presence=presence)
        return self._page_loaded(pair,limit=limit,cursor=cursor,sort=sort,direction=direction,minimum=minimum,kind=kind,changes=changes,search=search,root=root,recursive=recursive,folders_only=folders_only,section=section,reason=reason,presence=presence)

    def _page_loaded(self,pair,*,limit=20,cursor=None,sort='delta',direction='desc',minimum=0,kind='all',changes='all',search='',root=None,recursive=True,folders_only=False,section='all',reason=None,presence='all'):
        if section not in ('all','common','private'):raise ValueError('Phần dữ liệu không hợp lệ')
        if presence not in ('all','both','old','new'):raise ValueError('Mốc hiện diện không hợp lệ')
        from .snapshot_scope import REASONS
        if reason is not None and reason not in REASONS:raise ValueError('Lý do không hợp lệ')
        from .presentation import CHANGES
        from .progress import TaskProgress
        task=TaskProgress('tree' if folders_only else 'details',self.notify,[('scope','Chuẩn bị phạm vi và thứ tự sắp xếp',60),('count','Đếm toàn bộ kết quả bộ lọc',80),('read','Đọc trang chi tiết',100)])
        task.stage('scope',str(root) if root else 'Toàn phạm vi quét')
        if isinstance(changes, str):
            changes = None if changes == 'all' else sorted(set(x.strip() for x in changes.split(',') if x.strip()))
        else:
            changes = sorted(set(changes))
        if changes is not None and set(changes) - set(CHANGES): raise ValueError('Trạng thái lọc không hợp lệ')
        if minimum<0: raise ValueError("Ngưỡng dung lượng phải >=0")
        minimum=int(minimum)
        if sort not in SORTS or direction not in ("asc", "desc") or not 1 <= limit <= 1000:
            raise ValueError("Cột sắp xếp/hướng/số dòng không hợp lệ")
        record = self.db.execute("SELECT * FROM pairs WHERE id=? AND status='ready'", (pair,)).fetchone()
        if not record:
            raise ValueError("Chỉ mục so sánh chưa sẵn sàng; chuẩn bị lại từ snapshot.")
        filters=[pair,minimum,kind,changes,search,root,recursive,folders_only,section,reason,presence]
        fingerprint = hashlib.sha256(json.dumps(['cursor-v4',record['dataset_signature'],sort,direction,*filters]).encode()).hexdigest()
        count_key=hashlib.sha256(json.dumps(['count-v3',*filters]).encode()).hexdigest()
        if cursor and cursor.get("filter") != fingerprint:
            raise ValueError("Con trỏ thuộc bộ lọc/mốc khác; tải lại từ đầu.")
        clauses, args = ["c.pair_id=?"], [pair]
        effective_root=root
        summary=json.loads(record["summary"])
        roots=json.loads(summary["newer"]["config"])["roots"]
        # Clicking the only volume/root selects the already indexed whole
        # scope. Do not copy a million rows into a redundant subtree index.
        from .config import inside
        prior_roots=json.loads(summary['older']['config'])['roots'] if summary.get('older') else []
        if root and recursive and len(roots)==1 and normalize(root)==normalize(roots[0]) and all(inside(r,root) for r in prior_roots):
            effective_root=None
        effective_sort = "new" if not record["older"] and sort == "delta" else sort
        joins = f"diff_rows c INDEXED BY {SORT_INDEX[effective_sort]} JOIN source.paths p ON p.id=c.path_id"
        column = SORTS[sort]
        if not record["older"] and sort == "delta":
            column = SORTS["new"]
        key = "COALESCE(c."+column[len("COALESCE("):] if column.startswith("COALESCE(") else "c."+column
        if effective_root:
            found = self.db.execute("SELECT c.path_id FROM source.paths p INDEXED BY paths_key JOIN diff_rows c ON c.path_id=p.id WHERE c.pair_id=? AND p.path_key=? LIMIT 2", (pair,comparison_path_key(effective_root))).fetchall()
            if len(found)!=1:
                task.finish('Thư mục được chọn không có trong cặp so sánh')
                return {"rows": [], "cursor": None, "total": 0, "pair_id": pair}
            root_id=found[0][0]
            if recursive or sort=='path':
                scope = self.scope(pair, root_id,recursive=recursive)
                if sort=='path':self.names(pair,scope)
                self.sort_index(effective_sort,scoped=True)
                joins = f"scope_items s INDEXED BY scope_{SCOPE_SORTS[effective_sort]} JOIN diff_rows c ON c.pair_id=? AND c.path_id=s.path_id JOIN source.paths p ON p.id=c.path_id"
                args = [pair, pair, scope]; clauses.append("s.scope_id=?")
                column = SCOPE_SORTS["new" if not record["older"] and sort == "delta" else sort]; key = "s." + column
            else:
                clauses.append("c.parent_id=?"); args.append(root_id)
                index = "diff_children_new" if folders_only and effective_sort=="new" else "diff_parent"
                joins = f"diff_rows c INDEXED BY {index} JOIN source.paths p ON p.id=c.path_id"
        elif sort=='path':
            self.names(pair)
        if not effective_root:
            if section=='all':self.sort_index(effective_sort)
            else:
                index=self.presence_sort_index(effective_sort)
                joins=f'diff_rows c INDEXED BY {index} JOIN source.paths p ON p.id=c.path_id'
        if section!='all':clauses.append("(c.presence='both')=?");args.append(int(section=='common'))
        if presence!='all':clauses.append('c.presence=?');args.append(presence)
        if reason:clauses.append("('|'||REPLACE(c.reason,',','|')||'|') LIKE ?");args.append('%|'+reason+'|%')
        if folders_only:
            clauses.append("c.kind='folder'")
        else:
            if kind != "all":clauses.append("c.kind=?"); args.append(kind)
        if changes is not None:
            clauses.append('c.change IN ('+','.join('?' for _ in changes)+')' if changes else '0')
            args.extend(changes)
        if minimum:
            clauses.append("(c.kind<>'file' OR c.max_size>?)"); args.append(minimum)
        if search:
            clauses.append("p.path LIKE ? ESCAPE '!' "); args.append("%" + search.replace("!","!!").replace("%","!%").replace("_","!_") + "%")
        where = " AND ".join(clauses)
        task.complete('Đã chọn phạm vi và sắp xếp toàn tập dữ liệu')
        task.stage('count')
        count = self.db.execute("SELECT total FROM query_counts WHERE fingerprint=?",(count_key,)).fetchone()
        if count:
            total=count[0]
        elif not any((effective_root,folders_only,minimum,search,reason,presence!='all')):
            counts=summary[section+'_counts']
            total=sum(x['count'] for x in counts if (kind=='all' or x['kind']==kind) and (changes is None or x['change'] in changes))
        else:
            count_joins=joins if search else joins.replace(" JOIN source.paths p ON p.id=c.path_id","")
            total = self.db.execute("SELECT COUNT(*) FROM " + count_joins + " WHERE " + where, args).fetchone()[0]
        if not count:
            self.db.execute("INSERT OR REPLACE INTO query_counts VALUES(?,?,?)",(count_key,pair,total)); self.db.commit()
        task.complete(f'Đã đếm {total:,} mục thỏa bộ lọc')
        if not total:
            task.finish('Không có mục thỏa bộ lọc; không duyệt phần dữ liệu khác')
            return {'rows':[],'cursor':None,'total':0,'pair_id':pair}
        task.stage('read',f'Lấy tối đa {limit:,} mục')
        page_args = list(args)
        self.stage=f'Đang lấy tối đa {limit:,} mục đã sắp xếp '+('trong thư mục đã chọn' if root else 'trong toàn phạm vi')
        if cursor:
            where += f" AND ({key},c.path_id) {'>' if direction=='asc' else '<'} (?,?)"
            page_args += [cursor["value"], int(cursor["path_id"])]
        sql = f"""SELECT c.*,p.path,p.parent,CASE WHEN c.new_revision IS NOT NULL THEN c.after ELSE c.before END AS logical,
          r.identity,r.attrs,CASE WHEN c.kind='file' AND c.allocated IS NULL AND r.status='ok' THEN 'metadata-partial' ELSE r.status END AS status,r.note,op.path AS other_path,{key} AS _key,
          pr.common_before,pr.common_after,pr.private_before,pr.private_after,pr.unverified_count
        FROM {joins} JOIN source.revisions r ON r.id=COALESCE(c.new_revision,c.old_revision)
        LEFT JOIN folder_parts pr ON pr.pair_id=c.pair_id AND pr.path_id=c.path_id
        LEFT JOIN source.paths op ON op.id=c.other_path_id WHERE {where}
        ORDER BY {key} {direction},c.path_id {direction} LIMIT ?"""
        rows = [enrich_presence(dict(r)) for r in self.db.execute(sql, (*page_args, limit + 1))]
        more = len(rows) > limit; rows = rows[:limit]
        for row in rows:row['baseline']=not bool(record['older'])
        from .history_backend import log_mode
        next_cursor = {"value": rows[-1]["_key"], "path_id": str(rows[-1]["path_id"]) if log_mode() else rows[-1]["path_id"], "filter": fingerprint} if more else None
        task.finish(f'Đã tải {len(rows):,} dòng chi tiết; tổng bộ lọc {total:,} mục')
        return {"rows": rows, "cursor": next_cursor, "total": total, "pair_id": pair}

    def details(self, row):
        from .history_backend import log_mode,snapshots
        if log_mode() and row.get('pair_id'):
            info=self.db.execute('SELECT older,newer FROM pairs WHERE id=?',(row['pair_id'],)).fetchone()
            with snapshots(info,stop=self.stop,notify=self.notify):
                self.prepare(info[0] or None,info[1])
                fresh=self.db.execute('SELECT * FROM diff_rows WHERE pair_id=? AND path_id=?',(row['pair_id'],row['path_id'])).fetchone()
                if fresh is None:raise ValueError('Mục không còn trong cặp mốc này')
                return self._details_loaded(dict(fresh))
        return self._details_loaded(row)

    def _details_loaded(self,row):
        revision = row["new_revision"] or row["old_revision"]
        raw=dict(self.db.execute("SELECT * FROM source.revisions WHERE id=?", (revision,)).fetchone())
        return {**raw,'logical':row['after'] if row['new_revision'] else row['before'],
            'allocated':row.get('allocated_after') if row['new_revision'] else row.get('allocated_before'),
            'kind':row['kind'],'created':row['created'],'modified':row['modified'],'status':row.get('status',raw['status'])}

    def summary(self, pair):
        return json.loads(self.db.execute("SELECT summary FROM pairs WHERE id=?", (pair,)).fetchone()[0])

    def history(self):
        # Algorithm upgrades do not make previous comparison summaries disappear.
        return [dict(r) for r in self.db.execute("SELECT id,older,newer,created,status,summary FROM pairs WHERE id IN (SELECT MAX(id) FROM pairs GROUP BY older,newer) ORDER BY id DESC")]

    def delete(self, pair):
        for row in self.db.execute("SELECT id FROM scopes WHERE pair_id=?",(pair,)).fetchall():
            self.db.execute("DELETE FROM scope_items WHERE scope_id=?",(row[0],))
        self.db.execute("DELETE FROM scopes WHERE pair_id=?",(pair,))
        self.db.execute("DELETE FROM diff_rows WHERE pair_id=?",(pair,))
        self.db.execute("DELETE FROM query_counts WHERE pair_id=?",(pair,))
        self.db.execute("DELETE FROM pairs WHERE id=?",(pair,)); self.db.commit()

    def delete_for_scan(self, scan_id):
        return self.delete_for_scans([scan_id])

    def delete_for_scans(self, scan_ids):
        scan_ids=sorted({int(scan_id) for scan_id in scan_ids})
        if not scan_ids:return 0
        with _locked(self.stop):
            self.db.execute('BEGIN IMMEDIATE')
            try:
                pair_count=0
                for offset in range(0,len(scan_ids),400):
                    if self.stop.is_set():raise ProcessCancelled()
                    batch=scan_ids[offset:offset+400];marks=','.join('?' for _ in batch)
                    pairs=[row[0] for row in self.db.execute(f'SELECT id FROM pairs WHERE older IN ({marks}) OR newer IN ({marks})',(*batch,*batch))]
                    pair_count+=len(pairs)
                    for pair_offset in range(0,len(pairs),400):
                        pair_batch=pairs[pair_offset:pair_offset+400];pair_marks=','.join('?' for _ in pair_batch)
                        scopes=f'SELECT id FROM scopes WHERE pair_id IN ({pair_marks})'
                        self.db.execute(f'DELETE FROM scope_items WHERE scope_id IN ({scopes})',pair_batch)
                        self.db.execute(f'DELETE FROM scopes WHERE pair_id IN ({pair_marks})',pair_batch)
                        self.db.execute(f'DELETE FROM diff_rows WHERE pair_id IN ({pair_marks})',pair_batch)
                        self.db.execute(f'DELETE FROM query_counts WHERE pair_id IN ({pair_marks})',pair_batch)
                        self.db.execute(f'DELETE FROM pairs WHERE id IN ({pair_marks})',pair_batch)
                    self.db.execute(f'DELETE FROM normalized_folders WHERE scan_id IN ({marks})',batch)
                    self.db.execute(f'DELETE FROM normalized_scans WHERE scan_id IN ({marks})',batch)
                self.db.commit()
            except BaseException:
                self.db.rollback();raise
            return pair_count

    def clear_all(self):
        with _locked(self.stop):
            self.db.execute('BEGIN IMMEDIATE')
            try:
                for table in ('scope_items','scopes','diff_rows','query_counts','pairs','normalized_folders','normalized_scans'):
                    self.db.execute(f'DELETE FROM {table}')
                self.db.commit();self.db.execute('VACUUM');self.db.execute('PRAGMA main.wal_checkpoint(TRUNCATE)')
            except BaseException:
                self.db.rollback();raise

    def trim(self, keep=None, target_pairs=2):
        ready = self.db.execute("SELECT id FROM pairs WHERE status='ready' AND id<>COALESCE(?,-1) ORDER BY touched DESC", (keep,)).fetchall()
        allowed = max(0, target_pairs - (1 if keep else 0))
        for row in ready[allowed:]:
            for scope in self.db.execute("SELECT id FROM scopes WHERE pair_id=?", (row[0],)).fetchall():
                self.db.execute("DELETE FROM scope_items WHERE scope_id=?", (scope[0],))
            self.db.execute("DELETE FROM scopes WHERE pair_id=?", (row[0],))
            self.db.execute("DELETE FROM diff_rows WHERE pair_id=?", (row[0],))
            self.db.execute("DELETE FROM query_counts WHERE pair_id=?",(row[0],))
            self.db.execute("UPDATE pairs SET status='archived',names_ready=0 WHERE id=?", (row[0],))
        self.db.commit()
        used = (self.db.execute("PRAGMA page_count").fetchone()[0] - self.db.execute("PRAGMA freelist_count").fetchone()[0]) * self.db.execute("PRAGMA page_size").fetchone()[0]
        from .storage_safety import incremental_reclaim
        incremental_reclaim(self.db)
        if used > load().get("comparison_cache_mb", 768) * 1_000_000 and target_pairs > 1:
            self.trim(keep=keep, target_pairs=1)


def comparison_page(older, newer, stop=None, notify=None, **options):
    cache = Comparisons(stop, notify)
    try:
        pair = cache.prepare(older, newer)
        return {**cache.page(pair, **options), "summary": cache.summary(pair)}
    finally:
        cache.close()
