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
from .config import home, load, normalize
from .store import Store, now
from .platform_ops import ProcessCancelled

_build_lock = threading.RLock()
@contextmanager
def _locked(stop):
    while not _build_lock.acquire(timeout=.05):
        if stop.is_set(): raise ProcessCancelled()
    try:
        if stop.is_set(): raise ProcessCancelled()
        yield
    finally: _build_lock.release()

VERSION = 2
SORTS = {"delta": "COALESCE(delta,-9223372036854775808)", "old": "COALESCE(before,-1)", "new": "COALESCE(after,-1)",
         "created": "created", "modified": "modified", "allocated": "COALESCE(allocated,-1)",
         "kind": "kind", "change": "change", "path": "name_rank"}
SCOPE_SORTS = {"delta": "delta_key", "old": "before_key", "new": "after_key", "created": "created",
               "modified": "modified", "allocated": "allocated_key", "kind": "kind", "change": "change", "path": "name_rank"}
SORT_INDEX = {"delta":"diff_signed_delta", "old":"diff_old", "new":"diff_new", "created":"diff_created",
              "modified":"diff_modified", "allocated":"diff_allocated", "kind":"diff_kind", "change":"diff_change", "path":"diff_name"}


class Comparisons:
    def __init__(self, stop=None, notify=None):
        self.stop = stop or threading.Event(); self.notify = notify or (lambda *a: None)
        self.path = home() / "data/comparisons.sqlite"
        self.db = sqlite3.connect(f"file:{self.path.as_posix()}?mode=rwc", uri=True, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL"); self.db.execute("PRAGMA cache_size=-16384")
        self.db.execute("ATTACH DATABASE ? AS source", (f"file:{(home() / 'data/history.sqlite').as_posix()}?mode=ro",))
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS pairs(id INTEGER PRIMARY KEY,older INTEGER,newer INTEGER,version INTEGER,
          created TEXT,status TEXT,summary TEXT,touched TEXT,names_ready INTEGER DEFAULT 0,UNIQUE(older,newer,version));
        CREATE TABLE IF NOT EXISTS diff_rows(pair_id INTEGER,path_id INTEGER,parent_id INTEGER,kind TEXT,
          old_revision INTEGER,new_revision INTEGER,before INTEGER,after INTEGER,delta INTEGER,abs_delta INTEGER,
          max_size INTEGER,allocated INTEGER,created REAL,modified REAL,change TEXT,other_path_id INTEGER,name_rank INTEGER DEFAULT 0,
          PRIMARY KEY(pair_id,path_id)) WITHOUT ROWID;
        CREATE TABLE IF NOT EXISTS scopes(id INTEGER PRIMARY KEY,pair_id INTEGER,root_id INTEGER,touched TEXT,UNIQUE(pair_id,root_id));
        CREATE TABLE IF NOT EXISTS scope_items(scope_id INTEGER,path_id INTEGER,before_key INTEGER,after_key INTEGER,
          abs_delta INTEGER,allocated_key INTEGER,created REAL,modified REAL,kind TEXT,change TEXT,name_rank INTEGER,delta_key INTEGER,
          PRIMARY KEY(scope_id,path_id)) WITHOUT ROWID;
        CREATE INDEX IF NOT EXISTS diff_parent ON diff_rows(pair_id,parent_id,abs_delta DESC,path_id DESC);
        CREATE INDEX IF NOT EXISTS diff_delta ON diff_rows(pair_id,abs_delta,path_id);
        CREATE INDEX IF NOT EXISTS diff_signed_delta ON diff_rows(pair_id,COALESCE(delta,-9223372036854775808),path_id);
        CREATE INDEX IF NOT EXISTS diff_old ON diff_rows(pair_id,COALESCE(before,-1),path_id);
        CREATE INDEX IF NOT EXISTS diff_new ON diff_rows(pair_id,COALESCE(after,-1),path_id);
        CREATE INDEX IF NOT EXISTS diff_created ON diff_rows(pair_id,created,path_id);
        CREATE INDEX IF NOT EXISTS diff_modified ON diff_rows(pair_id,modified,path_id);
        CREATE INDEX IF NOT EXISTS diff_allocated ON diff_rows(pair_id,COALESCE(allocated,-1),path_id);
        CREATE INDEX IF NOT EXISTS diff_change ON diff_rows(pair_id,change,path_id);
        CREATE INDEX IF NOT EXISTS diff_kind ON diff_rows(pair_id,kind,path_id);
        CREATE INDEX IF NOT EXISTS diff_name ON diff_rows(pair_id,name_rank,path_id);
        CREATE INDEX IF NOT EXISTS diff_children_new ON diff_rows(pair_id,parent_id,kind,COALESCE(after,-1),path_id);
        CREATE TABLE IF NOT EXISTS query_counts(fingerprint TEXT PRIMARY KEY,pair_id INTEGER,total INTEGER);
        """)
        with _build_lock:
            try:
                self.db.execute('BEGIN IMMEDIATE')
                if 'delta_key' not in [r[1] for r in self.db.execute('PRAGMA table_info(scope_items)')]:
                    self.db.execute('ALTER TABLE scope_items ADD COLUMN delta_key INTEGER')
                    self.db.execute('UPDATE scope_items SET delta_key=COALESCE((SELECT c.delta FROM diff_rows c JOIN scopes t ON t.pair_id=c.pair_id WHERE t.id=scope_items.scope_id AND c.path_id=scope_items.path_id),-9223372036854775808)')
                for column in SCOPE_SORTS.values():
                    self.db.execute(f"CREATE INDEX IF NOT EXISTS scope_{column} ON scope_items(scope_id,{column},path_id)")
                self.db.commit()
            except BaseException:
                self.db.rollback();self.db.close();raise
        self.db.set_progress_handler(self._progress, 4000)
        self.last_pulse = time.monotonic(); self.stage = "Chuẩn bị so sánh"

    def _progress(self):
        if self.stop.is_set():
            return 1
        if time.monotonic() - self.last_pulse > 2:
            self.notify("info", {"message": self.stage + " • vẫn đang xử lý"})
            logging.getLogger("dgm").debug("COMPARE heartbeat %s", self.stage); self.last_pulse = time.monotonic()
        return 0

    def close(self):
        self.db.set_progress_handler(None, 0); self.db.close()

    def overview(self, older, newer):
        store = Store(readonly=True)
        try:
            b = store.scan(newer); a = store.scan(older) if older else None
            if b["status"] not in ("complete", "partial") or (a and a["status"] not in ("complete", "partial")):
                raise ValueError("Lượt đang chạy/bị hủy/lỗi không dùng để so sánh.")
            if a and (a["scope"] != b["scope"] or a["started"] >= b["started"]):
                raise ValueError("Mốc cũ phải trước mốc mới và cùng phạm vi/bộ loại trừ.")
            return {"older": a, "newer": b, "old_totals": json.loads(a["summary"] or "{}") if a else None,
                    "new_totals": json.loads(b["summary"] or "{}")}
        finally:
            store.close()

    def prepare(self, older, newer):
        overview = self.overview(older, newer)
        with _locked(self.stop):
            existing = self.db.execute("SELECT * FROM pairs WHERE older=? AND newer=? AND version=?", (older or 0, newer, VERSION)).fetchone()
            if existing and existing["status"] == "ready":
                self.db.execute("UPDATE pairs SET touched=? WHERE id=?", (now(), existing["id"])); self.db.commit()
                return existing["id"]
            self.trim(keep=existing["id"] if existing else None, target_pairs=1)
            self.db.execute("INSERT OR IGNORE INTO pairs(older,newer,version,created,status,touched) VALUES(?,?,?,?,?,?)", (older or 0, newer, VERSION, now(), "building", now()))
            pair = self.db.execute("SELECT id FROM pairs WHERE older=? AND newer=? AND version=?", (older or 0, newer, VERSION)).fetchone()[0]
            self.db.commit()
            try:
                self.db.execute("DELETE FROM diff_rows WHERE pair_id=?", (pair,))
                self.db.execute("DELETE FROM query_counts WHERE pair_id=?",(pair,))
                self.stage = "Đang lập chỉ mục chi tiết so sánh (một lần)"
                self.notify("info", {"message": self.stage})
                uncertain = bool(older and (overview["old_totals"].get("coverage_unverified") or overview["new_totals"].get("coverage_unverified")))
                self.db.execute("""INSERT INTO diff_rows(pair_id,path_id,parent_id,kind,old_revision,new_revision,before,after,
                delta,abs_delta,max_size,allocated,created,modified,change)
                SELECT ?,n.path_id,CASE WHEN pp.id=p.id THEN NULL ELSE pp.id END,r.kind,o.revision_id,n.revision_id,
                q.logical,r.logical,CASE WHEN ?=0 THEN NULL ELSE r.logical-COALESCE(q.logical,0) END,
                CASE WHEN ?=0 THEN -1 ELSE ABS(r.logical-COALESCE(q.logical,0)) END,MAX(r.logical,COALESCE(q.logical,0)),
                r.allocated,r.created,r.modified,
                CASE WHEN ?=0 THEN 'first_scan' WHEN o.path_id IS NULL THEN CASE WHEN ? THEN 'unknown' ELSE 'new' END
                  WHEN r.status IN ('changed-during-scan','unavailable-after-export') OR q.status IN ('changed-during-scan','unavailable-after-export') THEN 'unknown'
                  WHEN r.identity<>'' AND q.identity<>'' AND (r.identity LIKE 'wiz:%')=(q.identity LIKE 'wiz:%') AND r.identity<>q.identity THEN 'replaced'
                  WHEN r.logical>q.logical THEN 'grown' WHEN r.logical<q.logical THEN 'shrunk'
                  WHEN r.modified<>q.modified OR r.created<>q.created OR r.attrs<>q.attrs THEN 'modified' ELSE 'same' END
                FROM source.members n JOIN source.paths p ON p.id=n.path_id JOIN source.revisions r ON r.id=n.revision_id
                LEFT JOIN source.paths pp ON pp.path=p.parent
                LEFT JOIN source.members o ON o.scan_id=? AND o.path_id=n.path_id LEFT JOIN source.revisions q ON q.id=o.revision_id
                WHERE n.scan_id=?""", (pair, older or 0, older or 0, older or 0, int(uncertain), older or 0, newer))
                if older:
                    self.db.execute("""INSERT INTO diff_rows(pair_id,path_id,parent_id,kind,old_revision,new_revision,before,after,
                    delta,abs_delta,max_size,allocated,created,modified,change)
                    SELECT ?,o.path_id,CASE WHEN pp.id=p.id THEN NULL ELSE pp.id END,r.kind,o.revision_id,NULL,r.logical,NULL,
                    -r.logical,r.logical,r.logical,NULL,r.created,r.modified,CASE WHEN ? THEN 'unknown' ELSE 'missing' END
                    FROM source.members o JOIN source.paths p ON p.id=o.path_id JOIN source.revisions r ON r.id=o.revision_id
                    LEFT JOIN source.paths pp ON pp.path=p.parent LEFT JOIN source.members n ON n.scan_id=? AND n.path_id=o.path_id
                    WHERE o.scan_id=? AND n.path_id IS NULL""", (pair, int(uncertain), newer, older))
                    if not uncertain:
                        self._moves(pair, older, newer)
                    self._inaccessible(pair, older, newer)
                self.db.execute("UPDATE diff_rows SET delta=NULL,abs_delta=-1 WHERE pair_id=? AND change='unknown'", (pair,))
                counts = [dict(r) for r in self.db.execute("SELECT kind,change,COUNT(*) AS count FROM diff_rows WHERE pair_id=? GROUP BY kind,change", (pair,))]
                summary = {**overview, "counts": counts, "rows": sum(r["count"] for r in counts), "comparison_version": VERSION}
                self.db.execute("UPDATE pairs SET status='ready',summary=?,touched=?,names_ready=0 WHERE id=?", (json.dumps(summary, ensure_ascii=False), now(), pair))
                self.db.commit()
                logging.getLogger("dgm").info("COMPARE ready old=%s new=%s rows=%s", older, newer, summary["rows"])
                self.trim(keep=pair)
                return pair
            except BaseException as exc:
                self.db.set_progress_handler(None, 0); self.db.rollback()
                self.db.execute("UPDATE pairs SET status='failed' WHERE id=?", (pair,)); self.db.commit()
                if self.stop.is_set() and isinstance(exc, sqlite3.OperationalError):
                    raise ProcessCancelled() from exc
                raise

    def _moves(self, pair, older, newer):
        for label, scan in (("old", older), ("new", newer)):
            self.db.execute(f"DROP TABLE IF EXISTS temp.uid_{label}")
            self.db.execute(f"""CREATE TEMP TABLE uid_{label} AS SELECT r.identity,r.kind,MIN(m.path_id) AS path_id,
            MIN(r.created) AS created,MIN(r.logical) AS logical,COUNT(*) AS count FROM source.members m
            JOIN source.revisions r ON r.id=m.revision_id WHERE m.scan_id=? AND r.identity<>'' GROUP BY r.identity,r.kind""", (scan,))
            self.db.execute(f"CREATE INDEX temp.uid_{label}_key ON uid_{label}(identity,kind)")
        self.db.execute("DROP TABLE IF EXISTS temp.moves")
        self.db.execute("""CREATE TEMP TABLE moves AS SELECT a.path_id AS old_id,b.path_id AS new_id,a.logical AS before,b.logical AS after
        FROM uid_old a JOIN uid_new b ON b.identity=a.identity AND b.kind=a.kind
        JOIN diff_rows x ON x.pair_id=? AND x.path_id=a.path_id AND x.change='missing'
        JOIN diff_rows y ON y.pair_id=? AND y.path_id=b.path_id AND y.change='new'
        WHERE a.count=1 AND b.count=1 AND ABS(a.created-b.created)<0.001""", (pair, pair))
        self.db.execute("CREATE UNIQUE INDEX temp.move_new ON moves(new_id)"); self.db.execute("CREATE UNIQUE INDEX temp.move_old ON moves(old_id)")
        self.db.execute("""UPDATE diff_rows SET change='moved',before=(SELECT before FROM moves WHERE new_id=path_id),
        delta=after-(SELECT before FROM moves WHERE new_id=path_id),abs_delta=ABS(after-(SELECT before FROM moves WHERE new_id=path_id)),
        max_size=MAX(after,(SELECT before FROM moves WHERE new_id=path_id)),
        other_path_id=(SELECT old_id FROM moves WHERE new_id=path_id) WHERE pair_id=? AND path_id IN (SELECT new_id FROM moves)""", (pair,))
        self.db.execute("""UPDATE diff_rows SET change='moved_from',delta=0,abs_delta=0,
        other_path_id=(SELECT new_id FROM moves WHERE old_id=path_id) WHERE pair_id=? AND path_id IN (SELECT old_id FROM moves)""", (pair,))

    def _inaccessible(self, pair, older, newer):
        # Prefix ranges mark descendants; ancestors are explicitly indexed IDs.
        for row in self.db.execute("SELECT DISTINCT path FROM source.errors WHERE scan_id IN (?,?)", (older, newer)).fetchall():
            path = row[0]; prefix = path.rstrip("\\/") + "\\"
            self.db.execute("""UPDATE diff_rows SET change='unknown' WHERE pair_id=? AND path_id IN
            (SELECT id FROM source.paths WHERE path=? OR (path>=? AND path<?))""", (pair, path, prefix, prefix + chr(0x10ffff)))
            for ancestor in Path(path).parents:
                self.db.execute("UPDATE diff_rows SET change='unknown' WHERE pair_id=? AND kind='folder' AND path_id=(SELECT id FROM source.paths WHERE path=?)", (pair, str(ancestor)))

    def names(self, pair):
        if self.db.execute("SELECT names_ready FROM pairs WHERE id=?", (pair,)).fetchone()[0]:
            return
        with _locked(self.stop):
            self.stage = "Đang lập thứ tự đường dẫn (một lần)"; self.notify("info", {"message": self.stage})
            if self.db.execute("SELECT names_ready FROM pairs WHERE id=?",(pair,)).fetchone()[0]:return
            self.db.execute("DROP TABLE IF EXISTS temp.name_order")
            self.db.execute("""CREATE TEMP TABLE name_order AS SELECT c.path_id,ROW_NUMBER() OVER(ORDER BY p.path COLLATE NOCASE,c.path_id) AS rank
            FROM diff_rows c JOIN source.paths p ON p.id=c.path_id WHERE c.pair_id=?""", (pair,))
            self.db.execute("CREATE UNIQUE INDEX temp.name_id ON name_order(path_id)")
            self.db.execute("UPDATE diff_rows SET name_rank=(SELECT rank FROM name_order WHERE path_id=diff_rows.path_id) WHERE pair_id=?", (pair,))
            self.db.execute("UPDATE scope_items SET name_rank=(SELECT c.name_rank FROM diff_rows c JOIN scopes s ON s.pair_id=c.pair_id WHERE s.id=scope_id AND c.path_id=scope_items.path_id) WHERE scope_id IN (SELECT id FROM scopes WHERE pair_id=?)", (pair,))
            self.db.execute("UPDATE pairs SET names_ready=1 WHERE id=?", (pair,)); self.db.commit()

    def scope(self, pair, root_id):
        row = self.db.execute("SELECT id FROM scopes WHERE pair_id=? AND root_id=?", (pair, root_id)).fetchone()
        if row:
            self.db.execute("UPDATE scopes SET touched=? WHERE id=?", (now(),row[0])); self.db.commit()
            return row[0]
        self.stage = "Đang lập chỉ mục nhánh thư mục (một lần)"; self.notify("info", {"message": self.stage})
        self.db.execute("INSERT OR IGNORE INTO scopes(pair_id,root_id,touched) VALUES(?,?,?)", (pair, root_id, now()))
        scope = self.db.execute("SELECT id FROM scopes WHERE pair_id=? AND root_id=?", (pair, root_id)).fetchone()[0]
        self.db.execute("""WITH RECURSIVE children(id) AS (VALUES(?) UNION ALL
        SELECT c.path_id FROM children d CROSS JOIN diff_rows c INDEXED BY diff_parent
        ON c.pair_id=? AND c.parent_id=d.id WHERE c.path_id<>d.id)
        INSERT OR IGNORE INTO scope_items(scope_id,path_id,before_key,after_key,abs_delta,allocated_key,created,modified,kind,change,name_rank,delta_key)
        SELECT ?,c.path_id,COALESCE(c.before,-1),COALESCE(c.after,-1),c.abs_delta,
        COALESCE(c.allocated,-1),c.created,c.modified,c.kind,c.change,c.name_rank,COALESCE(c.delta,-9223372036854775808) FROM children d
        JOIN diff_rows c ON c.pair_id=? AND c.path_id=d.id""", (root_id, pair, scope, pair))
        self.db.commit()
        for stale in self.db.execute("SELECT id FROM scopes WHERE pair_id=? AND id<>? ORDER BY touched DESC LIMIT -1 OFFSET 2", (pair, scope)).fetchall():
            self.db.execute("DELETE FROM scope_items WHERE scope_id=?", (stale[0],)); self.db.execute("DELETE FROM scopes WHERE id=?", (stale[0],))
        self.db.commit(); return scope

    def page(self, pair, *, limit=20, cursor=None, sort="delta", direction="desc", minimum=0, kind="all", changes="all", search="", root=None, recursive=True, folders_only=False):
        if minimum<0: raise ValueError("Ngưỡng dung lượng phải >=0")
        minimum=int(minimum)
        if sort not in SORTS or direction not in ("asc", "desc") or not 1 <= limit <= 1000:
            raise ValueError("Cột sắp xếp/hướng/số dòng không hợp lệ")
        record = self.db.execute("SELECT * FROM pairs WHERE id=? AND status='ready'", (pair,)).fetchone()
        if not record:
            raise ValueError("Chỉ mục so sánh chưa sẵn sàng; chuẩn bị lại từ snapshot.")
        if sort == "path":
            self.names(pair)
        fingerprint = hashlib.sha256(json.dumps([pair,sort,direction,minimum,kind,changes,search,root,recursive,folders_only]).encode()).hexdigest()
        if cursor and cursor.get("filter") != fingerprint:
            raise ValueError("Con trỏ thuộc bộ lọc/mốc khác; tải lại từ đầu.")
        clauses, args = ["c.pair_id=?"], [pair]
        effective_root=root
        summary=json.loads(record["summary"])
        roots=json.loads(summary["newer"]["config"])["roots"]
        # Clicking the only volume/root selects the already indexed whole
        # scope. Do not copy a million rows into a redundant subtree index.
        if root and recursive and len(roots)==1 and normalize(root)==normalize(roots[0]):
            effective_root=None
        effective_sort = "new" if not record["older"] and sort == "delta" else sort
        joins = f"diff_rows c INDEXED BY {SORT_INDEX[effective_sort]} JOIN source.paths p ON p.id=c.path_id"
        column = SORTS[sort]
        if not record["older"] and sort == "delta":
            column = SORTS["new"]
        key = "c." + column if "(" not in column else column.replace("before", "c.before").replace("after", "c.after").replace("allocated", "c.allocated").replace("delta","c.delta")
        if effective_root:
            found = self.db.execute("SELECT id FROM source.paths WHERE path=?", (str(Path(effective_root)),)).fetchone()
            if not found:
                return {"rows": [], "cursor": None, "total": 0, "pair_id": pair}
            if recursive:
                scope = self.scope(pair, found[0])
                joins = f"scope_items s INDEXED BY scope_{SCOPE_SORTS[effective_sort]} JOIN diff_rows c ON c.pair_id=? AND c.path_id=s.path_id JOIN source.paths p ON p.id=c.path_id"
                args = [pair, pair, scope]; clauses.append("s.scope_id=?")
                column = SCOPE_SORTS["new" if not record["older"] and sort == "delta" else sort]; key = "s." + column
            else:
                clauses.append("c.parent_id=?"); args.append(found[0])
                index = "diff_children_new" if folders_only and effective_sort=="new" else "diff_parent"
                joins = f"diff_rows c INDEXED BY {index} JOIN source.paths p ON p.id=c.path_id"
        if folders_only:
            clauses.append("c.kind='folder'")
        elif kind != "all":
            clauses.append("c.kind=?"); args.append(kind)
        if changes != "all":
            clauses.append("c.change=?"); args.append(changes)
        if minimum:
            clauses.append("(c.kind<>'file' OR c.max_size>?)"); args.append(minimum)
        if search:
            clauses.append("p.path LIKE ? ESCAPE '!' "); args.append("%" + search.replace("!","!!").replace("%","!%").replace("_","!_") + "%")
        where = " AND ".join(clauses)
        count = self.db.execute("SELECT total FROM query_counts WHERE fingerprint=?",(fingerprint,)).fetchone()
        if count:
            total=count[0]
        elif not any((effective_root,folders_only,minimum,search)):
            total=sum(x['count'] for x in summary['counts'] if (kind=='all' or x['kind']==kind) and (changes=='all' or x['change']==changes))
        else:
            count_joins=joins if search else joins.replace(" JOIN source.paths p ON p.id=c.path_id","")
            total = self.db.execute("SELECT COUNT(*) FROM " + count_joins + " WHERE " + where, args).fetchone()[0]
        if not count:
            self.db.execute("INSERT OR REPLACE INTO query_counts VALUES(?,?,?)",(fingerprint,pair,total)); self.db.commit()
        page_args = list(args)
        if cursor:
            where += f" AND ({key},c.path_id) {'>' if direction=='asc' else '<'} (?,?)"
            page_args += [cursor["value"], cursor["path_id"]]
        sql = f"""SELECT c.*,p.path,p.parent,r.logical,r.identity,r.attrs,r.status,r.note,op.path AS other_path,{key} AS _key
        FROM {joins} JOIN source.revisions r ON r.id=COALESCE(c.new_revision,c.old_revision)
        LEFT JOIN source.paths op ON op.id=c.other_path_id WHERE {where}
        ORDER BY {key} {direction},c.path_id {direction} LIMIT ?"""
        rows = [dict(r) for r in self.db.execute(sql, (*page_args, limit + 1))]
        more = len(rows) > limit; rows = rows[:limit]
        next_cursor = {"value": rows[-1]["_key"], "path_id": rows[-1]["path_id"], "filter": fingerprint} if more else None
        return {"rows": rows, "cursor": next_cursor, "total": total, "pair_id": pair}

    def details(self, row):
        revision = row["new_revision"] or row["old_revision"]
        return dict(self.db.execute("SELECT * FROM source.revisions WHERE id=?", (revision,)).fetchone())

    def summary(self, pair):
        return json.loads(self.db.execute("SELECT summary FROM pairs WHERE id=?", (pair,)).fetchone()[0])

    def history(self):
        return [dict(r) for r in self.db.execute("SELECT id,older,newer,created,status,summary FROM pairs ORDER BY id DESC")]

    def delete(self, pair):
        for row in self.db.execute("SELECT id FROM scopes WHERE pair_id=?",(pair,)).fetchall():
            self.db.execute("DELETE FROM scope_items WHERE scope_id=?",(row[0],))
        self.db.execute("DELETE FROM scopes WHERE pair_id=?",(pair,))
        self.db.execute("DELETE FROM diff_rows WHERE pair_id=?",(pair,))
        self.db.execute("DELETE FROM query_counts WHERE pair_id=?",(pair,))
        self.db.execute("DELETE FROM pairs WHERE id=?",(pair,)); self.db.commit()

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
        if used > load().get("comparison_cache_mb", 768) * 1_000_000 and target_pairs > 1:
            self.trim(keep=keep, target_pairs=1)


def comparison_page(older, newer, stop=None, notify=None, **options):
    cache = Comparisons(stop, notify)
    try:
        pair = cache.prepare(older, newer)
        return {**cache.page(pair, **options), "summary": cache.summary(pair)}
    finally:
        cache.close()
