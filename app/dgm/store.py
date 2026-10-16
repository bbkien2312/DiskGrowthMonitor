from __future__ import annotations
import json
import sqlite3
import hashlib
import logging
import time
from datetime import datetime, timezone
from .config import home, load


def now():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path=None, readonly=False):
        self.path = path or home() / "data/history.sqlite"
        self.db = sqlite3.connect(f"file:{self.path.as_posix()}?mode=ro" if readonly else self.path, timeout=30, uri=readonly)
        self.db.row_factory = sqlite3.Row
        if not readonly:
            self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.create_function("dgm_hash", -1, lambda *args: hashlib.sha256(json.dumps(args, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest(), deterministic=True)
        if readonly:
            return
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS scans(id INTEGER PRIMARY KEY, profile TEXT, scope TEXT, engine TEXT,
          started TEXT, finished TEXT, status TEXT, config TEXT, summary TEXT);
        CREATE TABLE IF NOT EXISTS errors(scan_id INTEGER, path TEXT, message TEXT,
          FOREIGN KEY(scan_id) REFERENCES scans(id) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS plans(id TEXT PRIMARY KEY, created TEXT, status TEXT, content TEXT, result TEXT);
        CREATE TABLE IF NOT EXISTS benchmarks(id INTEGER PRIMARY KEY, created TEXT, content TEXT);
        CREATE TABLE IF NOT EXISTS jobs(schedule_id TEXT PRIMARY KEY, last_run TEXT, next_run TEXT, result TEXT);
        """)
        self._normalized_entries()
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS scan_numbers(scan_id INTEGER PRIMARY KEY,profile TEXT,ordinal INTEGER,
          UNIQUE(profile,ordinal));
        CREATE TABLE IF NOT EXISTS sequence_counters(profile TEXT PRIMARY KEY,next_number INTEGER);
        CREATE TABLE IF NOT EXISTS scan_id_counter(id INTEGER PRIMARY KEY CHECK(id=1),next_id INTEGER NOT NULL);
        """)
        missing = self.db.execute("SELECT id,profile FROM scans WHERE id NOT IN (SELECT scan_id FROM scan_numbers) ORDER BY started,id").fetchall()
        for row in missing:
            self._number(row[0], row[1])
        self.db.execute("INSERT OR IGNORE INTO scan_id_counter VALUES(1,(SELECT COALESCE(MAX(scan_id),0)+1 FROM scan_numbers))")
        self.db.commit()

    def _number(self, scan_id, profile):
        row = self.db.execute("SELECT next_number FROM sequence_counters WHERE profile=?", (profile,)).fetchone()
        ordinal = row[0] if row else 1
        self.db.execute("INSERT INTO scan_numbers VALUES(?,?,?)", (scan_id, profile, ordinal))
        self.db.execute("INSERT OR REPLACE INTO sequence_counters VALUES(?,?)", (profile, ordinal + 1))

    def _normalized_entries(self):
        legacy = self.db.execute("SELECT type FROM sqlite_master WHERE name='entries'").fetchone()
        migrate = legacy is not None and legacy[0] == "table"
        if migrate:
            self.db.execute("ALTER TABLE entries RENAME TO entries_legacy")
            self.db.commit()
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS paths(id INTEGER PRIMARY KEY,path TEXT UNIQUE,parent TEXT);
        CREATE INDEX IF NOT EXISTS paths_parent ON paths(parent);
        CREATE TABLE IF NOT EXISTS revisions(id INTEGER PRIMARY KEY,key TEXT UNIQUE,path_id INTEGER,
          kind TEXT,logical INTEGER,allocated INTEGER,created REAL,modified REAL,identity TEXT,attrs INTEGER,status TEXT,note TEXT);
        CREATE INDEX IF NOT EXISTS revisions_identity ON revisions(identity);
        CREATE TABLE IF NOT EXISTS members(scan_id INTEGER,path_id INTEGER,revision_id INTEGER,
          PRIMARY KEY(scan_id,path_id),FOREIGN KEY(scan_id) REFERENCES scans(id) ON DELETE CASCADE);
        CREATE INDEX IF NOT EXISTS members_revision ON members(revision_id);
        CREATE VIEW IF NOT EXISTS entries AS SELECT m.scan_id,p.path,p.parent,r.kind,r.logical,r.allocated,r.created,r.modified,
          r.identity,r.attrs,r.status,r.note FROM members m JOIN paths p ON p.id=m.path_id JOIN revisions r ON r.id=m.revision_id;
        CREATE TRIGGER IF NOT EXISTS entries_insert INSTEAD OF INSERT ON entries BEGIN
          INSERT OR IGNORE INTO paths(path,parent) VALUES(NEW.path,NEW.parent);
          INSERT OR IGNORE INTO revisions(key,path_id,kind,logical,allocated,created,modified,identity,attrs,status,note)
            VALUES(dgm_hash(NEW.path,NEW.kind,NEW.logical,NEW.allocated,NEW.created,NEW.modified,NEW.identity,NEW.attrs,NEW.status,NEW.note),
              (SELECT id FROM paths WHERE path=NEW.path),NEW.kind,NEW.logical,NEW.allocated,NEW.created,NEW.modified,NEW.identity,NEW.attrs,NEW.status,NEW.note);
          INSERT OR REPLACE INTO members VALUES(NEW.scan_id,(SELECT id FROM paths WHERE path=NEW.path),
            (SELECT id FROM revisions WHERE key=dgm_hash(NEW.path,NEW.kind,NEW.logical,NEW.allocated,NEW.created,NEW.modified,NEW.identity,NEW.attrs,NEW.status,NEW.note)));
        END;
        CREATE TRIGGER IF NOT EXISTS entries_update INSTEAD OF UPDATE ON entries BEGIN
          INSERT OR IGNORE INTO revisions(key,path_id,kind,logical,allocated,created,modified,identity,attrs,status,note)
            VALUES(dgm_hash(NEW.path,NEW.kind,NEW.logical,NEW.allocated,NEW.created,NEW.modified,NEW.identity,NEW.attrs,NEW.status,NEW.note),
              (SELECT id FROM paths WHERE path=NEW.path),NEW.kind,NEW.logical,NEW.allocated,NEW.created,NEW.modified,NEW.identity,NEW.attrs,NEW.status,NEW.note);
          UPDATE members SET revision_id=(SELECT id FROM revisions WHERE key=dgm_hash(NEW.path,NEW.kind,NEW.logical,NEW.allocated,NEW.created,NEW.modified,NEW.identity,NEW.attrs,NEW.status,NEW.note))
            WHERE scan_id=OLD.scan_id AND path_id=(SELECT id FROM paths WHERE path=OLD.path);
        END;
        CREATE TRIGGER IF NOT EXISTS entries_delete INSTEAD OF DELETE ON entries BEGIN
          DELETE FROM members WHERE scan_id=OLD.scan_id AND path_id=(SELECT id FROM paths WHERE path=OLD.path);
        END;
        """)
        pending = self.db.execute("SELECT 1 FROM sqlite_master WHERE name='entries_legacy'").fetchone()
        if pending:
            # Restartable migration: the old table stays until row counts agree.
            self.db.execute("INSERT INTO entries SELECT * FROM entries_legacy")
            old_count = self.db.execute("SELECT COUNT(*) FROM entries_legacy").fetchone()[0]
            count = self.db.execute("SELECT COUNT(*) FROM members").fetchone()[0]
            if old_count != count:
                raise RuntimeError("Snapshot migration count mismatch; legacy data retained")
            self.db.execute("DROP TABLE entries_legacy")
            self.db.commit()
            self.db.execute("VACUUM")

    def close(self):
        self.db.close()

    def begin_scan(self, name, scope, engine, profile):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            next_id=self.db.execute('SELECT next_id FROM scan_id_counter WHERE id=1').fetchone()[0]
            self.db.execute('UPDATE scan_id_counter SET next_id=? WHERE id=1',(next_id+1,))
            cur = self.db.execute("INSERT INTO scans(id,profile,scope,engine,started,status,config) VALUES(?,?,?,?,?,?,?)",
                                  (next_id, name, scope, engine, now(), "running", json.dumps(profile)))
            scan_id=cur.lastrowid;self._number(scan_id,name);self.db.commit()
            return scan_id
        except BaseException:
            self.db.rollback();raise

    def put(self, scan_id, entries):
        # Three indexed joins per batch, rather than executing view triggers and
        # hashing the same metadata twice for every individual file.
        self.db.execute("CREATE TEMP TABLE IF NOT EXISTS ingest(path TEXT,parent TEXT,kind TEXT,logical INTEGER,allocated INTEGER,created REAL,modified REAL,identity TEXT,attrs INTEGER,status TEXT,note TEXT,key TEXT)")
        self.db.execute("DELETE FROM ingest")
        values = []
        for e in entries:
            metadata = (e["path"], e["kind"], e["logical"], e["allocated"], e["created"], e["modified"], e.get("identity", ""), e.get("attrs", 0), e.get("status", "ok"), e.get("note", ""))
            key = hashlib.sha256(json.dumps(metadata, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
            values.append((e["path"], e["parent"], *metadata[1:], key))
        self.db.executemany("INSERT INTO ingest VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", values)
        self.db.execute("INSERT OR IGNORE INTO paths(path,parent) SELECT path,parent FROM ingest")
        self.db.execute("""INSERT OR IGNORE INTO revisions(key,path_id,kind,logical,allocated,created,modified,identity,attrs,status,note)
        SELECT i.key,p.id,i.kind,i.logical,i.allocated,i.created,i.modified,i.identity,i.attrs,i.status,i.note
        FROM ingest i JOIN paths p ON p.path=i.path""")
        self.db.execute("INSERT OR REPLACE INTO members SELECT ?,p.id,r.id FROM ingest i JOIN paths p ON p.path=i.path JOIN revisions r ON r.key=i.key", (scan_id,))
        self.db.commit()

    def error(self, scan_id, path, message):
        self.db.execute("INSERT INTO errors VALUES(?,?,?)", (scan_id, str(path), str(message)))

    def finish(self, scan_id, status, summary):
        self.db.execute("UPDATE scans SET finished=?,status=?,summary=? WHERE id=?",
                        (now(), status, json.dumps(summary, ensure_ascii=False), scan_id))
        self.db.commit()

    def dedupe(self, scan_id, stop=None):
        from .platform_ops import ProcessCancelled
        def check():
            if stop is not None and stop.is_set():
                raise ProcessCancelled()
        check()
        self.db.execute("DROP TABLE IF EXISTS temp.owners")
        self.db.execute("""CREATE TEMP TABLE owners AS SELECT r.identity,MIN(p.path) AS path
        FROM members m JOIN revisions r ON r.id=m.revision_id JOIN paths p ON p.id=m.path_id
        WHERE m.scan_id=? AND r.kind='file' AND r.identity<>'' GROUP BY r.identity""", (scan_id,))
        self.db.execute("CREATE UNIQUE INDEX temp.owner_identity ON owners(identity)")
        duplicates = self.db.execute("""SELECT p.path,p.parent,r.* FROM members m JOIN revisions r ON r.id=m.revision_id
        JOIN paths p ON p.id=m.path_id JOIN owners o ON o.identity=r.identity
        WHERE m.scan_id=? AND r.kind='file' AND p.path<>o.path AND r.allocated<>0""", (scan_id,)).fetchall()
        for start in range(0, len(duplicates), 2000):
            check(); batch = [{**dict(r), "allocated": 0} for r in duplicates[start:start + 2000]]
            self.put(scan_id, batch)
        check()

    def aggregate(self, scan_id, stop=None, notify=None):
        from .platform_ops import ProcessCancelled
        notify = notify or (lambda *a: None)
        def check():
            if stop is not None and stop.is_set(): raise ProcessCancelled()
        notify("info", {"message": "Đang xử lý hardlink bằng chỉ mục..."})
        self.dedupe(scan_id, stop)
        notify("info", {"message": "Đang cộng dung lượng một lượt từ thư mục con lên cha..."})
        # Direct file totals computed once. No SUM over a million members for
        # each directory. Folder records alone are held in memory.
        self.db.execute("DROP TABLE IF EXISTS temp.direct_totals")
        self.db.execute("""CREATE TEMP TABLE direct_totals AS SELECT p.parent,SUM(r.logical) AS logical,SUM(r.allocated) AS allocated
        FROM members m JOIN paths p ON p.id=m.path_id JOIN revisions r ON r.id=m.revision_id
        WHERE m.scan_id=? AND r.kind='file' GROUP BY p.parent""", (scan_id,))
        self.db.execute("CREATE UNIQUE INDEX temp.direct_parent ON direct_totals(parent)")
        folders = self.db.execute("""SELECT p.id AS path_id,pp.id AS parent_id,p.path,p.parent,r.*,
        COALESCE(t.logical,0) AS direct_logical,COALESCE(t.allocated,0) AS direct_allocated
        FROM members m JOIN paths p ON p.id=m.path_id JOIN revisions r ON r.id=m.revision_id
        LEFT JOIN paths pp ON pp.path=p.parent LEFT JOIN direct_totals t ON t.parent=p.path
        WHERE m.scan_id=? AND r.kind='folder' ORDER BY LENGTH(p.path) DESC""", (scan_id,)).fetchall()
        totals = {r["path_id"]: [r["direct_logical"], r["direct_allocated"]] for r in folders}
        last = time.monotonic()
        for index, r in enumerate(folders):
            check(); parent = r["parent_id"]
            if parent in totals and parent != r["path_id"]:
                totals[parent][0] += totals[r["path_id"]][0]; totals[parent][1] += totals[r["path_id"]][1]
            if time.monotonic() - last > 0.5:
                notify("progress", {"count": index + 1, "total": len(folders), "path": "Tổng hợp thư mục"}); last = time.monotonic()
        batch = []
        for index, r in enumerate(folders):
            check(); logical, allocated = totals[r["path_id"]]
            if logical != r["logical"] or allocated != r["allocated"]:
                batch.append({**dict(r), "logical": logical, "allocated": allocated})
            if len(batch) >= 2000:
                self.put(scan_id, batch); batch.clear()
                notify("progress", {"count": index + 1, "total": len(folders), "path": "Ghi tổng thư mục"})
        if batch:
            self.put(scan_id, batch)
        self.db.commit()

    def totals(self, scan_id):
        return dict(self.db.execute("""SELECT COALESCE(SUM(CASE WHEN r.kind='file' THEN r.logical ELSE 0 END),0) AS logical,
        COALESCE(SUM(CASE WHEN r.kind='file' THEN r.allocated ELSE 0 END),0) AS allocated,
        COALESCE(SUM(r.kind='file'),0) AS files,COALESCE(SUM(r.kind='folder'),0) AS folders,COALESCE(SUM(r.kind='link'),0) AS links
        FROM members m JOIN revisions r ON r.id=m.revision_id WHERE m.scan_id=?""", (scan_id,)).fetchone())

    def record_stage(self, scan_id, summary):
        self.db.execute("UPDATE scans SET summary=? WHERE id=?", (json.dumps(summary, ensure_ascii=False), scan_id)); self.db.commit()

    def gc(self, notify=None):
        notify = notify or (lambda *args: None)
        indexed = self.db.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name='members_path'").fetchone()
        if not indexed:
            notify("info", {"message": "Đang tối ưu chỉ mục lịch sử lần đầu; thao tác này chỉ cần thực hiện một lần..."})
            self.db.execute("CREATE INDEX IF NOT EXISTS members_path ON members(path_id)")
        notify("info", {"message": "Đang dọn dữ liệu lịch sử không còn liên kết..."})
        self.db.execute("DELETE FROM revisions WHERE NOT EXISTS (SELECT 1 FROM members WHERE revision_id=revisions.id)")
        self.db.execute("DELETE FROM paths WHERE NOT EXISTS (SELECT 1 FROM members WHERE path_id=paths.id)")
        self.db.commit()

    def delete_scan(self, scan_id):
        return self.delete_scans([scan_id])

    def delete_scans(self, scan_ids):
        scan_ids=sorted({int(scan_id) for scan_id in scan_ids})
        if not scan_ids:return 0
        existing={row['id']:row['status'] for row in self.db.execute('SELECT id,status FROM scans')}
        missing=set(scan_ids)-existing.keys()
        if missing:raise ValueError('Không tìm thấy lượt quét: '+', '.join(f'#{scan_id}' for scan_id in sorted(missing)))
        running=[scan_id for scan_id in scan_ids if existing[scan_id]=='running']
        if running:raise ValueError('Không thể xóa lượt quét đang chạy: '+', '.join(f'#{scan_id}' for scan_id in running))
        self.db.execute('BEGIN IMMEDIATE')
        try:
            for offset in range(0,len(scan_ids),500):
                batch=scan_ids[offset:offset+500];marks=','.join('?' for _ in batch)
                self.db.execute(f'DELETE FROM scans WHERE id IN ({marks})',batch)
                self.db.execute(f'DELETE FROM scan_numbers WHERE scan_id IN ({marks})',batch)
            self.db.commit()
            self.gc()
            return len(scan_ids)
        except BaseException:
            self.db.rollback();raise

    def clear_scans(self):
        if self.db.execute("SELECT 1 FROM scans WHERE status='running' LIMIT 1").fetchone():
            raise ValueError('Dừng lượt quét đang chạy trước khi xóa lịch sử.')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self.db.execute('DELETE FROM scans');self.db.execute('DELETE FROM scan_numbers');self.db.commit();self.gc()
            self.db.execute('VACUUM');self.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        except BaseException:
            self.db.rollback();raise

    def scans(self, profile=None):
        where, args = ("WHERE s.profile=?", (profile,)) if profile else ("", ())
        return [dict(x) for x in self.db.execute(f"SELECT s.*,n.ordinal FROM scans s LEFT JOIN scan_numbers n ON n.scan_id=s.id {where} ORDER BY s.id DESC", args)]

    def scan(self, scan_id):
        row = self.db.execute("SELECT s.*,n.ordinal FROM scans s LEFT JOIN scan_numbers n ON n.scan_id=s.id WHERE s.id=?", (scan_id,)).fetchone()
        if row is None:
            raise ValueError("Không tìm thấy snapshot")
        return dict(row)

    def previous(self, scan_id):
        curr = self.scan(scan_id)
        row = self.db.execute("SELECT id FROM scans WHERE (started<? OR (started=? AND id<?)) AND scope=? AND profile=? AND status IN ('complete','partial') ORDER BY started DESC,id DESC LIMIT 1", (curr["started"],curr["started"],scan_id,curr["scope"],curr["profile"])).fetchone()
        return row[0] if row else None

    def entries(self, scan_id, minimum=0, kind="all", search="", limit=1000, offset=0):
        clauses = ["scan_id=?", "(kind='folder' OR logical>=?)", "path LIKE ?"]
        args = [scan_id, minimum, "%" + search + "%"]
        if kind != "all":
            clauses.append("kind=?")
            args.append(kind)
        return [dict(x) for x in self.db.execute("SELECT * FROM entries WHERE " + " AND ".join(clauses) + " ORDER BY logical DESC,path LIMIT ? OFFSET ?", (*args, limit, offset))]

    def compare(self, older, newer, limit=2000, offset=0, kind="all", search="", changes="all"):
        from .comparison import Comparisons
        cache = Comparisons()
        try:
            pair = cache.prepare(older, newer)
            cursor = None; output = []; skipped = 0
            while len(output) < limit:
                page = cache.page(pair, limit=min(1000, max(1, limit - len(output))), cursor=cursor, kind=kind, search=search, changes=changes)
                rows = page["rows"]
                drop = min(max(0, offset - skipped), len(rows)); skipped += drop
                output.extend(rows[drop:]); cursor = page["cursor"]
                if cursor is None:
                    break
            return output[:limit]
        finally:
            cache.close()

    def _legacy_compare(self, older, newer, limit=2000, offset=0, kind="all", search="", changes="all"):
        b = self.scan(newer)
        if b["status"] not in ("complete", "partial"):
            raise ValueError("Lượt quét đang chạy/lỗi/bị hủy không dùng để so sánh.")
        if older is None:
            return [{**x, "before": None, "after": x["logical"], "delta": None, "change": "first_scan"} for x in self.entries(newer, search=search, kind=kind, limit=limit, offset=offset)]
        a = self.scan(older)
        if datetime.fromisoformat(a["started"]) >= datetime.fromisoformat(b["started"]):
            raise ValueError("Mốc A phải được quét trước mốc B; chọn lại hoặc đổi hai mốc.")
        if a["scope"] != b["scope"]:
            raise ValueError("Hai snapshot khác phạm vi hoặc bộ loại trừ; không so sánh trực tiếp.")
        if a["status"] not in ("complete", "partial") or b["status"] not in ("complete", "partial"):
            raise ValueError("Snapshot lỗi/bị hủy không dùng để kết luận thay đổi.")
        inaccessible = [r[0] for r in self.db.execute("SELECT path FROM errors WHERE scan_id IN (?,?)", (older, newer))]
        from .config import inside
        sql = """SELECT n.*,o.logical AS before,n.logical AS after,n.logical-COALESCE(o.logical,0) AS delta,
        CASE WHEN o.path IS NULL THEN 'new' WHEN n.identity<>'' AND o.identity<>'' AND n.identity<>o.identity THEN 'replaced'
        WHEN n.logical>o.logical THEN 'grown' WHEN n.logical<o.logical THEN 'shrunk'
        WHEN n.modified<>o.modified OR n.created<>o.created OR n.attrs<>o.attrs THEN 'modified' ELSE 'same' END AS change
        FROM entries n LEFT JOIN entries o ON o.scan_id=? AND o.path=n.path WHERE n.scan_id=?
        UNION ALL SELECT o.*,o.logical AS before,NULL AS after,-o.logical AS delta,'missing' AS change
        FROM entries o LEFT JOIN entries n ON n.scan_id=? AND n.path=o.path WHERE o.scan_id=? AND n.path IS NULL"""
        clauses, args = ["path LIKE ?"], ["%" + search + "%"]
        if kind != "all":
            clauses.append("kind=?")
            args.append(kind)
        rows = self.db.execute("SELECT * FROM (" + sql + ") WHERE " + " AND ".join(clauses) + " ORDER BY ABS(delta) DESC,path", (older, newer, newer, older, *args))
        result = []
        uncertain_export = any(json.loads(row["summary"] or "{}").get("coverage_unverified", False) for row in (a, b))
        for row in rows:
            d = dict(row)
            if d["status"] in ("changed-during-scan", "unavailable-after-export") or (uncertain_export and d["change"] in ("new", "missing")) or any(inside(d["path"], p) or (d["kind"] == "folder" and inside(p, d["path"])) for p in inaccessible):
                d["change"], d["delta"] = "unknown", None
            elif d["change"] in ("new", "missing") and d["identity"]:
                other_id = older if d["change"] == "new" else newer
                counterpart = self.db.execute("SELECT path,logical,created FROM entries WHERE scan_id=? AND identity=? AND kind=? LIMIT 2", (other_id, d["identity"], d["kind"])).fetchall()
                if len(counterpart) == 1 and abs(counterpart[0]["created"] - d["created"]) < 0.001:
                    other = counterpart[0]
                    d["other_path"] = other["path"]
                    if d["change"] == "new":
                        d["change"], d["before"], d["delta"] = "moved", other["logical"], d["logical"] - other["logical"]
                    else:
                        d["change"], d["delta"] = "moved_from", 0
            if changes != "all" and d["change"] != changes:
                continue
            if offset:
                offset -= 1
                continue
            result.append(d)
            if len(result) >= limit:
                break
        return result

    def export(self, scan_id, destination):
        import csv
        with open(destination, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            cur = self.db.execute("SELECT * FROM entries WHERE scan_id=? ORDER BY path", (scan_id,))
            writer.writerow([x[0] for x in cur.description])
            writer.writerows(cur)

    def prune(self, notify=None):
        notify = notify or (lambda *args: None)
        notify("info", {"message": "Đang kiểm tra các mốc lịch sử cần giữ..."})
        cfg = load()
        cutoff = datetime.now(timezone.utc).timestamp() - cfg["retention_days"] * 86400
        rows = self.scans()
        kept = {}
        total = {}
        for index, row in enumerate(rows):
            valid = row["status"] in ("complete", "partial")
            count = kept.get(row["scope"], 0)
            total[row["scope"]] = total.get(row["scope"], 0) + 1
            if valid:
                kept[row["scope"]] = count + 1
            age = datetime.fromisoformat(row["started"]).timestamp() < cutoff
            protected = valid and count < 2
            if not protected and row["status"] != "running" and (age or total[row["scope"]] > cfg["max_snapshots"]):
                self.db.execute("DELETE FROM scans WHERE id=?", (row["id"],))
        self.db.commit()
        self.gc(notify=notify)
        self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")

