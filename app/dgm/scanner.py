from __future__ import annotations
from .config import inside
import csv
import ctypes
import json
import logging
import os
import shutil
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from .config import Policy, home, load, normalize, scope_hash
from .platform_ops import run, is_admin, ProcessCancelled as Cancelled
from .store import Store


class ScanLock:
    def __init__(self, stop=None):
        self.stop = stop or threading.Event()
        self.watch_done = threading.Event()
        self.interrupt = None

    def __enter__(self):
        from .app_update import check_scan_allowed
        check_scan_allowed()
        self.path = home() / "data/worker.lock"
        import psutil
        try:
            self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                raw = self.path.read_text()
                lock = json.loads(raw)
                pid = lock["pid"] if isinstance(lock, dict) else int(lock)
                live = psutil.pid_exists(pid)
                if live and isinstance(lock, dict):
                    live = abs(psutil.Process(pid).create_time() - lock["created"]) < 0.01
                if live:
                    raise RuntimeError(f"Một tác vụ đang chạy (PID {pid})")
            except (ValueError, FileNotFoundError, psutil.NoSuchProcess):
                pass
            self.path.unlink(missing_ok=True)
            self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        import uuid
        self.owner = {"pid": os.getpid(), "created": psutil.Process().create_time(), "token": uuid.uuid4().hex}
        os.write(self.fd, json.dumps(self.owner).encode())
        try: check_scan_allowed()
        except BaseException:
            os.close(self.fd); self.path.unlink(missing_ok=True); raise
        def watch():
            while not self.watch_done.wait(0.15):
                try:
                    request = json.loads((home() / "data/cancel-request.json").read_text(encoding="utf-8"))
                    if request == self.owner:
                        self.stop.set()
                        if self.interrupt:
                            self.interrupt()
                except (OSError, ValueError):
                    pass
        self.monitor = threading.Thread(target=watch, daemon=True)
        self.monitor.start()
        return self

    def __exit__(self, *_):
        self.watch_done.set(); self.monitor.join(timeout=1)
        os.close(self.fd)
        self.path.unlink(missing_ok=True)


def allocated(path, st):
    if os.name != "nt":
        return getattr(st, "st_blocks", (st.st_size + 511) // 512) * 512
    fn = ctypes.WinDLL("kernel32", use_last_error=True).GetCompressedFileSizeW
    fn.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_uint32)]
    fn.restype = ctypes.c_uint32
    high = ctypes.c_uint32()
    ctypes.set_last_error(0)
    low = fn(str(path), ctypes.byref(high))
    if low == 0xFFFFFFFF and ctypes.get_last_error():
        return None
    return (high.value << 32) | low


def metadata(path: Path, kind):
    st = path.stat(follow_symlinks=False)
    ident = f"{st.st_dev}:{st.st_ino}" if st.st_ino else ""
    size = st.st_size if kind == "file" else 0
    alloc = allocated(path, st) if kind == "file" else 0
    return {"path": str(path), "parent": str(path.parent), "kind": kind, "logical": size,
            "allocated": alloc if alloc is not None else 0, "created": getattr(st, "st_birthtime", st.st_ctime),
            "modified": st.st_mtime, "identity": ident, "attrs": getattr(st, "st_file_attributes", 0),
            "status": "ok", "note": "allocated unknown" if alloc is None else ""}


def native_entries(policy, store, scan_id, stop, notify):
    count = 0
    for root in policy.roots:
        start = Path(root)
        if policy.excluded(start):
            continue
        stack = [start]
        while stack:
            if stop.is_set():
                raise Cancelled()
            current = stack.pop()
            if policy.excluded(current):
                continue
            try:
                record = metadata(current, "folder" if current.is_dir() else "file")
                if current.is_symlink() or record["attrs"] & 0x400:
                    record["kind"], record["note"] = "link", "Reparse point: không đi theo"
                    yield record
                    continue
                yield record
                if record["kind"] != "folder":
                    continue
                with os.scandir(current) as items:
                    for item in items:
                        if stop.is_set():
                            raise Cancelled()
                        path = Path(item.path)
                        # Exclusion happens BEFORE stat/open/descending into the item.
                        if policy.excluded(path):
                            continue
                        try:
                            is_dir = item.is_dir(follow_symlinks=False)
                            if is_dir:
                                stack.append(path)
                            else:
                                entry = metadata(path, "file")
                                if item.is_symlink() or entry["attrs"] & 0x400:
                                    entry["kind"], entry["logical"], entry["allocated"] = "link", 0, 0
                                    entry["note"] = "Reparse/cloud: không đọc nội dung"
                                yield entry
                            count += 1
                            logging.getLogger("dgm").debug("METADATA %s", path)
                            if count % 1000 == 0:
                                notify("progress", {"count": count, "path": str(current)})
                        except OSError as exc:
                            store.error(scan_id, path, str(exc))
                            logging.getLogger("dgm").debug("SKIP %s %s", path, exc)
            except OSError as exc:
                store.error(scan_id, current, str(exc))


def stamp(text):
    if not text:
        return 0
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00").replace("/", "-")).replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return 0


def wiztree_entries(policy, config, stop, notify):
    exe = config["engines"]["wiztree"]
    if not Path(exe).is_file():
        raise ValueError("Chưa cấu hình executable WizTree")
    work = home() / "data/exports"
    work.mkdir(exist_ok=True)
    for index, root in enumerate(policy.roots):
        if stop.is_set():
            raise Cancelled()
        output = work / f"wiztree-{os.getpid()}-{index}.csv"
        output.unlink(missing_ok=True)
        args = [exe, root, f"/export={output}", "/exportfolders=1", "/exportfiles=1", "/exportalldates=1", "/exportUTCTime=1", "/exportmftrecno=1", "/exportdrivecapacity=1"]
        if policy.p.get("strict_browsers", False):
            raise ValueError("WizTree raw MFT không đáp ứng profile cấm đọc metadata trình duyệt")
        # Explicitly allow MFT only in a profile whose browser protection was disabled.
        args += ["/admin=1" if policy.p.get("mft", False) else "/admin=0"]
        if policy.p.get("excludes"):
            args += ["/filterexclude=" + "|".join("'" + x + "'" for x in policy.p["excludes"])]
        try:
            notify("info", {"message": f"WizTree đang xuất dữ liệu: {root} • có thể Dừng quét"})
            res = run(args, timeout=1800, stop=stop)
            if res.returncode != 0 or not output.exists():
                raise RuntimeError(f"WizTree export thất bại: {res.returncode} {res.stderr}")
            notify("info", {"message": "Đang nhập kết quả WizTree vào lịch sử..."})
            from .config import volume_identity
            identity_prefix = f"wiz:{volume_identity(root)}:" if policy.p.get("mft", False) else ""
            with output.open(encoding="utf-8-sig", newline="") as f:
                reader = csv.reader(f)
                headers = None
                count = 0
                for cols in reader:
                    if stop.is_set():
                        raise Cancelled()
                    if not cols:
                        continue
                    if headers is None:
                        if cols[0].strip().casefold() == "file name":
                            headers = [x.strip().casefold() for x in cols]
                        continue
                    row = dict(zip(headers, cols))
                    raw = row.get("file name", "")
                    if not raw or not policy.in_scope(raw):
                        continue
                    path = str(Path(raw))
                    kind = "folder" if raw.endswith(("\\", "/")) else "file"
                    alloc = row.get("allocated", "0")
                    is_link = len(alloc) > 1 and alloc.startswith("0")
                    try:
                        entry = {"path": path, "parent": str(Path(path).parent), "kind": kind,
                               "logical": int(row.get("size", "0")), "allocated": 0 if is_link else int(alloc),
                               "created": stamp(row.get("created", "")), "modified": stamp(row.get("modified", "")),
                               "identity": row.get("mft record number", ""), "attrs": int(row.get("attributes", "0")),
                               "note": "WizTree export; coverage cannot be proven complete"}
                        if kind == "folder" and normalize(path) == normalize(root):
                            from .config import inside
                            trusted = not policy.p.get("excludes") and not inside(home(), root)
                            notify("overview", {"root": root, "logical": entry["logical"], "allocated": entry["allocated"],
                                                "files": int(row.get("files", "0") or 0), "folders": int(row.get("folders", "0") or 0), "trusted": trusted})
                        if policy.p.get("mft", False):
                            # MFT exports are already metadata snapshots. Stat-ing every
                            # path again defeats the fast engine and changes timestamps.
                            record = next((value for key,value in row.items() if value and key.replace(' ','').replace('.','').replace('_','') in ('mftrecordnumber','mftrecordno','mftrecno')), "")
                            entry["identity"] = identity_prefix + record if record else ""
                            yield entry
                        else:
                            from .engines import enrich
                            yield enrich(entry)
                    except ValueError as exc:
                        raise ValueError(f"CSV WizTree không tương thích: {exc}") from exc
                    count += 1
                    if count % 5000 == 0:
                        notify("progress", {"count": count, "path": root})
                if headers is None:
                    raise ValueError("Không nhận dạng được schema CSV WizTree")
        finally:
            output.unlink(missing_ok=True)


def scan(name, p, stop=None, notify=None, *, allow_uac=True):
    from .logging_utils import setup
    cfg = load()
    setup(cfg["debug"])
    stop, external_notify = stop or threading.Event(), notify or (lambda *a: None)
    requested = p.get("engine", "native")
    engine = requested
    if p.get("strict_browsers", False) and engine != "native":
        raise ValueError("Không đọc/quét Chrome/Edge yêu cầu công cụ native. Chọn native rõ ràng; app không tự đổi engine.")
    if engine == "wiztree" and p.get("mft", False) and not is_admin():
        if not allow_uac:raise PermissionError('Quét MFT chạy nền cần quyền Admin; cài/bật Task hoặc Service bằng Admin. Không mở UAC trong lịch nền.')
        from .elevation import request,ElevationCancelled
        try:return request('scan',{'name':name,'profile':p},stop,external_notify)
        except ElevationCancelled as exc:
            return {'scan_id':None,'status':'cancelled','files':0,'folders':0,'seconds':0,'message':str(exc),'before_start':True}
    if engine not in ("native", "wiztree", "windirstat", "gdu"):
        raise ValueError("Engine không hợp lệ")
    policy = Policy(p)
    from .storage_metrics import begin as begin_storage, finish as finish_storage
    storage_meter = begin_storage("scan")
    start = time.perf_counter()
    with ScanLock(stop) as lock, StoreContext() as store:
        lock.interrupt = store.db.interrupt
        # Owning the global lock means earlier 'running' rows are orphaned.
        store.db.execute("UPDATE scans SET status='failed',finished=? WHERE status='running'", (datetime.now(timezone.utc).isoformat(),))
        store.db.commit()
        scan_id = store.begin_scan(name, scope_hash(p), engine, p)
        summary = {"files": 0, "folders": 0, "volumes": {}, "root_overview": {}, "phase": "preparing",
                   "measurement_semantics": "dgm-logical-allocated-v2",
                   "scope_note": "Logical cộng theo từng đường dẫn file; byte cấp phát chỉ tính một lần cho hardlink. Nguồn tổng thư mục được lưu theo engine; snapshot cũ thiếu nguồn sẽ chuẩn hóa một lần khi so sánh."}
        last_log = [0.0]
        def notify(event, data):
            if event == "overview":
                summary["root_overview"][data["root"]] = data
                store.record_stage(scan_id, summary)
            if event == "info":
                logging.getLogger("dgm").info("SCAN %s phase %s", scan_id, data["message"])
            elif event == "progress" and time.monotonic() - last_log[0] > 2:
                logging.getLogger("dgm").debug("SCAN %s progress %s", scan_id, data); last_log[0] = time.monotonic()
            external_notify(event, data)
        notify("started", {"scan_id": scan_id, "engine": engine})
        logging.getLogger("dgm").info("SCAN %s %s %s", scan_id, name, engine)
        status = "complete"
        batch = []
        try:
            store.db.set_progress_handler(lambda: int(stop.is_set()), 1000)
            if stop.is_set():
                raise Cancelled()
            notify("info", {"message": "Đang chuẩn bị lịch sử quét..."})
            store.prune(notify=notify)
            from .engines import windirstat_entries, gdu_entries
            factories = {"wiztree": wiztree_entries, "windirstat": windirstat_entries, "gdu": gdu_entries}
            summary["phase"] = "importing"
            iterator = native_entries(policy, store, scan_id, stop, notify) if engine == "native" else factories[engine](policy, cfg, stop, notify)
            for entry in iterator:
                if stop.is_set():
                    raise Cancelled()
                batch.append(entry)
                summary["files" if entry["kind"] == "file" else "folders"] += 1
                if len(batch) >= 5000:
                    store.put(scan_id, batch)
                    batch.clear()
                    pages = store.db.execute("PRAGMA page_count").fetchone()[0]
                    free_pages = store.db.execute("PRAGMA freelist_count").fetchone()[0]
                    page_size = store.db.execute("PRAGMA page_size").fetchone()[0]
                    size = (pages - free_pages) * page_size
                    if size > cfg["database_limit_mb"] * 1_000_000:
                        raise RuntimeError("Database đạt giới hạn; cần giảm phạm vi/lịch sử hoặc đổi nơi lưu")
            if batch:
                store.put(scan_id, batch)
            summary["import_complete"] = True; summary["phase"] = "aggregating"
            store.record_stage(scan_id, summary)
            store.db.set_progress_handler(lambda: int(stop.is_set()), 1000)
            wiztree_totals = engine == "wiztree" and p.get("mft", False) and not p.get("excludes") and not any(inside(home(),r) for r in policy.roots)
            if wiztree_totals:
                notify("info", {"message": "Dùng tổng thư mục gốc từ WizTree; chỉ xử lý hardlink. So sánh WizTree không chuẩn hóa lại các thư mục."})
                store.dedupe(scan_id,stop)
            else:
                notify("info", {"message": "Chuẩn hóa dung lượng thư mục từ danh sách file (dùng chung cho mọi công cụ)..."})
                store.aggregate(scan_id, stop=stop, notify=notify)
            summary["folder_totals_source"] = "wiztree-mft-v1" if wiztree_totals else "aggregated-files-v1"
            error_count = store.db.execute("SELECT COUNT(*) FROM errors WHERE scan_id=?", (scan_id,)).fetchone()[0]
            summary["errors"] = error_count
            if error_count or engine != "native":
                status = "partial"
            summary["coverage_unverified"] = engine != "native"
            coverage_reasons = []
            if engine != "native":
                coverage_reasons.append(
                    f"Kết quả đến từ engine ngoài ({engine}); app không thể tự xác minh đã bao phủ toàn bộ phạm vi. "
                    "Đây là trạng thái thận trọng, không khẳng định chắc chắn đã bỏ sót; phân loại thêm/mất có thể chưa xác định."
                )
            if error_count:
                coverage_reasons.append(
                    f"Có {error_count:,} mục/vùng không đọc được; thay đổi trong các vùng đó có thể chưa đầy đủ."
                )
            if coverage_reasons:
                summary["coverage_note"] = " ".join(coverage_reasons)
            for root in policy.roots:
                try:
                    d = shutil.disk_usage(root)
                    summary["volumes"][Path(root).anchor or root] = {"total": d.total, "free": d.free, "used": d.used}
                except OSError:
                    pass
            summary.update(store.totals(scan_id))
        except (Cancelled, KeyboardInterrupt):
            status = "cancelled"
            store.db.set_progress_handler(None, 0)
            store.db.rollback()
        except Exception as exc:
            store.db.set_progress_handler(None, 0)
            store.db.rollback()
            if stop.is_set() and isinstance(exc, sqlite3.OperationalError):
                status = "cancelled"
            else:
                status = "failed"
                summary["error"] = str(exc)
                store.db.execute("DELETE FROM entries WHERE scan_id=?", (scan_id,))
                logging.getLogger("dgm").exception("SCAN failed")
        finally:
            store.db.set_progress_handler(None, 0)
            lock.interrupt = None
        summary["phase"] = status
        summary["seconds"] = round(time.perf_counter() - start, 3)
        summary["alerts"] = []
        for volume, values in summary["volumes"].items():
            if values["free"] < cfg.get("alerts", {}).get("free_gb", 10) * 1_000_000_000:
                summary["alerts"].append(f"{volume} chỉ còn {values['free']/1e9:.2f} GB trống")
        store.finish(scan_id, status, summary)
        previous = store.previous(scan_id)
        summary["first_scan"] = previous is None
        result = {"scan_id": scan_id, "status": status, "previous": previous, **summary}
        logging.getLogger("dgm").info("SCAN finished %s", result)
        summary["storage"] = finish_storage(storage_meter)
        result["storage"] = summary["storage"]
        store.finish(scan_id, status, summary)
        notify("finished", result)
        return result


class StoreContext:
    def __enter__(self):
        self.store = Store()
        return self.store

    def __exit__(self, *_):
        self.store.close()
