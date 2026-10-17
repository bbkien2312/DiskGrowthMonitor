from __future__ import annotations
from .config import inside
import csv
import ctypes
import json
import logging
import os
import re
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
    from .windows_metadata import allocation
    return allocation(path,getattr(st,'st_file_attributes',0))


def metadata(path: Path, kind, st=None):
    st = st if st is not None else path.stat(follow_symlinks=False)
    ident = f"{st.st_dev}:{st.st_ino}" if st.st_ino else ""
    size = st.st_size if kind == "file" else 0
    alloc = allocated(path, st) if kind == "file" else 0
    reason=''
    if alloc is None and os.name=='nt':
        from .windows_metadata import allocation_failure
        reason=allocation_failure()
    return {"path": str(path), "parent": str(path.parent), "kind": kind, "logical": size,
            "allocated": alloc, "created": getattr(st, "st_birthtime", st.st_ctime),
            "modified": st.st_mtime, "identity": ident, "attrs": getattr(st, "st_file_attributes", 0),
            "status": "metadata-partial" if alloc is None else "ok", "note": ('allocated unknown; '+reason) if alloc is None else ""}


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
                                # Windows DirEntry.stat may omit st_ino/st_dev;
                                # a full stat is needed for hardlink/rename identity.
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
        value = datetime.fromisoformat(normalize_export_date(text))
        return (value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value).timestamp()
    except ValueError:
        return 0


def normalize_export_date(text):
    # WizTree honors a Windows date separator that can itself be a space.
    # Normalize just YYYY<sep>MM<sep>DD, not time or timezone separators.
    value=re.sub(r'^(\d{4})[ /.-](\d{2})[ /.-](\d{2})(?=[ T]|$)',r'\1-\2-\3',text.strip())
    return value.replace('Z','+00:00')


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
        archived=False;export_ok=False
        try:
            notify("info", {"message": f"WizTree đang xuất dữ liệu: {root} • có thể Dừng quét"})
            res = run(args, timeout=1800, stop=stop)
            if res.returncode != 0 or not output.exists():
                raise RuntimeError(f"WizTree export thất bại: {res.returncode} {res.stderr}")
            export_ok=True
            notify("info", {"message": "Đang nhập kết quả WizTree vào lịch sử..."})
            from .config import volume_identity
            identity_prefix = f"wiz:{volume_identity(root)}:" if policy.p.get("mft", False) else ""
            with output.open(encoding="utf-8-sig", newline="") as f:
                total_bytes=output.stat().st_size;read_bytes=0
                def lines():
                    nonlocal read_bytes
                    for line in f:
                        read_bytes+=len(line.encode('utf-8'))
                        yield line
                reader = csv.reader(lines())
                headers = None
                required_length=0
                count = 0
                audit={'root':root,'rows':0,'source_files':0,'source_logical':0,'source_allocated':0,
                       'included_files':0,'included_logical':0,'included_allocated':0,
                       'excluded_files':0,'excluded_logical':0,'excluded_allocated':0,
                       'reparse_logical_removed':0,'reparse_allocated_removed':0}
                for cols in reader:
                    if stop.is_set():
                        raise Cancelled()
                    if not cols:
                        continue
                    if headers is None:
                        if cols[0].strip().casefold() == "file name":
                            aliases={'createddate':'created','mftrecno':'mft record number','mftrecordnumber':'mft record number',
                                     'lastaccessdate':'accessed','modifieddate':'modified'}
                            headers = [aliases.get(x.strip().casefold(),x.strip().casefold()) for x in cols]
                            audit['columns']=headers
                            missing={'file name','size','allocated','attributes'}-set(headers)
                            if missing:raise ValueError('CSV WizTree thiếu cột bắt buộc: '+', '.join(sorted(missing)))
                            required_length=max(headers.index(k) for k in ('file name','size','allocated','attributes'))+1
                        elif match:=re.search(r'Generated by WizTree\s+([\d.]+)',cols[0]):audit['tool_version']=match[1]
                        continue
                    row = dict(zip(headers, cols))
                    # Capacity columns only appear on drive/root records. File
                    # records legitimately omit trailing optional CSV columns.
                    if len(cols)<required_length or len(cols)>len(headers):
                        raise ValueError(f'CSV WizTree sai số cột: cần ít nhất {required_length}, nhận {len(cols)}.')
                    raw = row.get("file name", "")
                    if not raw:
                        continue
                    path = str(Path(raw))
                    kind = "folder" if raw.endswith(("\\", "/")) else "file"
                    alloc = row.get("allocated", "")
                    is_link = len(alloc) > 1 and alloc.startswith("0")
                    try:
                        dates={}
                        for field in ('created','modified'):
                            text=row.get(field,'').strip()
                            if not text:dates[field]=None;continue
                            value=datetime.fromisoformat(normalize_export_date(text))
                            dates[field]=None if value.year<=1900 else (value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value).timestamp()
                        entry = {"path": path, "parent": str(Path(path).parent), "kind": kind,
                               "logical": int(row.get("size", "")), "allocated": int(alloc),
                               "created": dates['created'], "modified": dates['modified'],
                               "identity": row.get("mft record number", ""), "attrs": int(row.get("attributes", "0")),
                               "note": "WizTree export; coverage cannot be proven complete"}
                        if entry['logical']<0 or entry['allocated']<0:raise ValueError('Dung lượng âm')
                        audit['rows']+=1
                        export_allocated=0 if is_link else entry['allocated']
                        if kind=='file':
                            audit['source_files']+=1;audit['source_logical']+=entry['logical'];audit['source_allocated']+=export_allocated
                        if not policy.in_scope(raw):
                            if kind=='file':
                                audit['excluded_files']+=1;audit['excluded_logical']+=entry['logical'];audit['excluded_allocated']+=export_allocated
                            continue
                        if kind=='file':
                            audit['included_files']+=1;audit['included_logical']+=entry['logical'];audit['included_allocated']+=entry['allocated']
                            if entry['attrs']&0x400:
                                audit['reparse_logical_removed']+=entry['logical'];audit['reparse_allocated_removed']+=entry['allocated']
                        if kind == "folder" and normalize(path) == normalize(root):
                            from .config import inside
                            trusted = not policy.p.get("excludes") and not inside(home(), root)
                            notify("overview", {"root": root, "logical": entry["logical"], "allocated": entry["allocated"],
                                                "files": int(row.get("files", "0") or 0), "folders": int(row.get("folders", "0") or 0), "trusted": trusted,
                                                'export_capacity':int(row['drivecapacity']) if row.get('drivecapacity') else None,
                                                'export_free':int(row['freespace']) if row.get('freespace') else None,
                                                'export_used':int(row['usedspace']) if row.get('usedspace') else None})
                        if policy.p.get("mft", False):
                            # MFT exports are already metadata snapshots. Stat-ing every
                            # path again defeats the fast engine and changes timestamps.
                            record = next((value for key,value in row.items() if value and key.replace(' ','').replace('.','').replace('_','') in ('mftrecordnumber','mftrecordno','mftrecno')), "")
                            entry["identity"] = identity_prefix + record if record else ""
                            if is_link and not record:
                                entry['allocated']=None
                                entry['note']+='; thiếu MFT ID để đối soát cấp phát hardlink'
                            yield entry
                        else:
                            from .engines import enrich
                            yield enrich(entry)
                    except ValueError as exc:
                        raise ValueError(f"CSV WizTree không tương thích: {exc}") from exc
                    count += 1
                    if count % 5000 == 0:
                        notify("progress", {"count": count, "path": root,
                            "fraction":(index+min(1,read_bytes/max(1,total_bytes)))/len(policy.roots)})
                if headers is None:
                    raise ValueError("Không nhận dạng được schema CSV WizTree")
            from .source_archive import retain
            audit['source']=retain(output,engine='wiztree',root=root,options=args,stop=stop)
            archived=True
            notify('source-audit',audit)
        finally:
            if export_ok and not archived and not stop.is_set() and output.exists():
                try:
                    from .source_archive import retain
                    source=retain(output,engine='wiztree',root=root,options=args,stop=stop)
                    notify('source-audit',{'root':root,'parse_failed':True,'source':source})
                except Exception:
                    logging.getLogger('dgm').warning('Không giữ được nguồn CSV sau lỗi nhập',exc_info=True)
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
    with ScanLock(stop) as lock, StoreContext(stop,external_notify) as store:
        lock.interrupt = store.db.interrupt
        # Owning the global lock means earlier 'running' rows are orphaned.
        store.db.execute("UPDATE scans SET status='failed',finished=? WHERE status='running'", (datetime.now(timezone.utc).isoformat(),))
        store.db.commit()
        scan_id = store.begin_scan(name, scope_hash(p), engine, p)
        from .snapshot_contract import canonical_entry, describe, coverage, SEMANTICS
        summary = {"files": 0, "folders": 0, "volumes": {}, "root_overview": {}, "phase": "preparing",
                   "measurement_semantics": SEMANTICS, "contract": describe(engine),
                   "scope_note": "Tổng thư mục được đối chiếu từ file một lần khi nhập; logical theo đường dẫn, byte cấp phát tính hardlink một lần. So sánh không chuẩn hóa lại snapshot mới."}
        from .config import ROOT,volume_identity
        summary['effective_scope']={'roots':policy.roots,'excludes':p.get('excludes',[]),
            'strict_browsers':p.get('strict_browsers',False),'implicit_excludes':[str(ROOT)],
            'volumes':sorted({volume_identity(r) for r in policy.roots})}
        summary['scan_method']={'engine':engine,'mft':bool(engine=='wiztree' and p.get('mft')),'admin':is_admin()}
        last_log = [0.0]
        def notify(event, data):
            if event=='source-audit':
                summary.setdefault('ingestion_audit',[]).append(data)
                store.record_stage(scan_id,summary)
            if event == "overview":
                summary["root_overview"][data["root"]] = data
                store.record_stage(scan_id, summary)
            if event == "info":
                logging.getLogger("dgm").info("SCAN %s phase %s", scan_id, data["message"])
            elif event == "progress" and time.monotonic() - last_log[0] > 2:
                logging.getLogger("dgm").debug("SCAN %s progress %s", scan_id, data); last_log[0] = time.monotonic()
            external_notify(event, data)
            if event=='progress':
                detail=f"{data.get('count',0):,}"+(f"/{data['total']:,}" if data.get('total') else '')+' mục • '+str(data.get('path',''))
                if summary['phase']=='importing':
                    task.advance(data.get('fraction',0),1 if 'fraction' in data else None,detail)
                elif summary['phase']=='aggregating':task.advance(data.get('count',0),data.get('total'),detail)
        from .progress import TaskProgress
        task=TaskProgress('scan',external_notify,[('prepare','Chuẩn bị lịch sử quét',10),
            ('collect','Đọc và nhập file/thư mục',65),('totals','Tổng hợp dung lượng thư mục',92),('save','Lưu snapshot và số đo data/log',100)])
        notify("started", {"scan_id": scan_id, "engine": engine})
        logging.getLogger("dgm").info("SCAN %s %s %s", scan_id, name, engine)
        status = "complete"
        batch = []
        try:
            store.db.set_progress_handler(lambda: int(stop.is_set()), 1000)
            if stop.is_set():
                raise Cancelled()
            notify("info", {"message": "Đang chuẩn bị lịch sử quét..."})
            task.stage('prepare')
            store.prune(notify=notify)
            task.complete('Đã chuẩn bị lịch sử và áp dụng giới hạn lưu')
            summary['timings']={'prepare_seconds':round(time.perf_counter()-start,4)}
            collect_started=time.perf_counter()
            from .engines import windirstat_entries, gdu_entries
            factories = {"wiztree": wiztree_entries, "windirstat": windirstat_entries, "gdu": gdu_entries}
            summary["phase"] = "importing"
            task.stage('collect',f'Công cụ: {engine}; đang đọc danh sách mục')
            iterator = native_entries(policy, store, scan_id, stop, notify) if engine == "native" else factories[engine](policy, cfg, stop, notify)
            for entry in iterator:
                if stop.is_set():
                    raise Cancelled()
                entry = canonical_entry(entry)
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
            task.complete(f"Đã nhập {summary['files']:,} file và {summary['folders']:,} thư mục/liên kết")
            summary['timings']['collect_import_seconds']=round(time.perf_counter()-collect_started,4)
            totals_started=time.perf_counter()
            summary["import_complete"] = True; summary["phase"] = "aggregating"
            store.record_stage(scan_id, summary)
            task.stage('totals')
            store.db.set_progress_handler(lambda: int(stop.is_set()), 1000)
            notify("info", {"message": "Đối chiếu tổng thư mục một lần lúc nhập theo chuẩn chung; giữ tổng export gốc để đối chiếu."})
            store.aggregate(scan_id, stop=stop, notify=notify)
            summary['hardlinks']=store.hardlink_stats
            if store.orphan_branches:summary.setdefault('data_warnings',[]).append(f'{store.orphan_branches} nhánh thiếu thư mục cha duy nhất; cộng về tổ tiên đã lưu gần nhất.')
            summary["folder_totals_source"] = "canonical-files-v3"
            task.complete('Đã xử lý hardlink và tổng dung lượng thư mục')
            error_count = store.db.execute("SELECT COUNT(*) FROM errors WHERE scan_id=?", (scan_id,)).fetchone()[0]
            summary["errors"] = error_count
            if error_count or engine != "native":
                status = "partial"
            summary["coverage_unverified"] = engine != "native"
            summary['coverage'] = coverage(engine, policy.roots,
                [dict(row) for row in store.db.execute('SELECT path,message FROM errors WHERE scan_id=?', (scan_id,))])
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
            summary['identity_unique_logical_estimate']=None if summary['logical'] is None else summary['logical']-summary['hardlinks']['repeated_logical']
            summary['identity_unknown_files']=summary['files']-summary['hardlinks']['identified_paths']
            if summary.get('ingestion_audit'):
                audits=summary['ingestion_audit']
                expected=sum(x['included_logical']-x['reparse_logical_removed'] for x in audits)
                imported_allocated=sum(x['included_allocated']-x['reparse_allocated_removed'] for x in audits)
                summary['ingestion_check']={'expected_logical':expected,'stored_logical':summary['logical'],
                    'logical_matches':expected==summary['logical'],'pre_dedupe_allocated':imported_allocated,
                    'stored_allocated':summary['allocated'],'hardlink_allocation_removed':None if summary['allocated'] is None else imported_allocated-summary['allocated'],
                    'note':'Chỉ cộng file; loại trừ/reparse được ghi riêng. Số cấp phát hardlink được chuyển về một đường dẫn ổn định trong phạm vi.'}
                if expected!=summary['logical']:
                    summary.setdefault('data_warnings',[]).append('Size sau nhập không khớp nhật ký nguồn; không coi đây là snapshot đã kiểm chứng.')
            summary['timings']['totals_seconds']=round(time.perf_counter()-totals_started,4)
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
        if status in ('complete','partial'):
            from .normalization import VERSION as NORMALIZATION_VERSION
            summary['canonical_normalization_version']=NORMALIZATION_VERSION
            task.stage('save')
            summary['completed_steps']=[*task.completed,'Đã lưu snapshot và số đo data/log']
        summary["seconds"] = round(time.perf_counter() - start, 3)
        summary["alerts"] = []
        for volume, values in summary["volumes"].items():
            if values["free"] < cfg.get("alerts", {}).get("free_gb", 10) * 1_000_000_000:
                from .presentation import size
                summary["alerts"].append(f"{volume}: dung lượng trống thấp (≈ {size(values['free'])})")
        store.finish(scan_id, status, summary)
        previous = store.previous(scan_id)
        summary["first_scan"] = previous is None
        result = {"scan_id": scan_id, "status": status, "previous": previous, **summary}
        logging.getLogger("dgm").info("SCAN finished %s", result)
        summary["storage"] = finish_storage(storage_meter)
        result["storage"] = summary["storage"]
        store.finish(scan_id, status, summary)
        if status in ('complete','partial'):task.finish(f"Đã lưu {summary['files']:,} file • {summary['folders']:,} thư mục/liên kết")
        else:task.emit(summary.get('error',''),label='Đã dừng quét' if status=='cancelled' else 'Quét bị lỗi ở bước hiện tại')
        notify("finished", result)
        return result


class StoreContext:
    def __init__(self,stop=None,notify=None):
        self.stop,self.notify=stop,notify

    def __enter__(self):
        # A pre-cancelled scan still records its cancelled history row, without
        # starting a potentially large legacy index migration first.
        self.store = Store(stop=self.stop,notify=self.notify,
            migrate_keys=not (self.stop is not None and self.stop.is_set()))
        return self.store

    def __exit__(self, *_):
        self.store.close()
