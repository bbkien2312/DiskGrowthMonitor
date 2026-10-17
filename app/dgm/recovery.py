"""Finish an explicitly verified imported snapshot without touching filesystem data."""
import json
import logging
import threading
import time
from .scanner import ScanLock
from .store import Store
from .platform_ops import ProcessCancelled


def rebuild(scan_id, confirmed=False, stop=None, notify=None):
    stop = stop or threading.Event(); external_notify = notify or (lambda *a: None)
    from .progress import TaskProgress
    task=TaskProgress('recovery',external_notify,[('copy','Tạo snapshot từ metadata đã nhập',35),('totals','Tổng hợp lại dung lượng thư mục',90),('save','Lưu lịch sử phục hồi',100)])
    if stop.is_set(): raise ProcessCancelled()
    def notify(event,data):
        if event=="info": logging.getLogger("dgm").info("RECOVERY %s %s",scan_id,data["message"])
        external_notify(event,data)
        if event=='progress':task.advance(data.get('count',0),data.get('total'),data.get('path',''))
    with ScanLock(stop) as lock:
        from .storage_metrics import begin as begin_storage, finish as finish_storage
        storage_meter = begin_storage("scan")
        store = Store(); lock.interrupt = store.db.interrupt
        try:
            source = store.scan(scan_id); saved = json.loads(source["summary"] or "{}")
            if not saved.get("import_complete") and not confirmed:
                raise ValueError("Chưa có bằng chứng nhập xong. Phải xác nhận dữ liệu đã nhập đầy đủ trước khi phục hồi.")
            totals = store.totals(scan_id)
            if not totals["files"] or any(saved.get(k) is not None and totals[k]!=saved[k] for k in ("files","folders")):
                raise ValueError("Số file/thư mục nhập không khớp; giữ nguyên lượt cũ, không phục hồi như một kết quả đầy đủ.")
            p = json.loads(source["config"])
            new_id = store.begin_scan(source["profile"], source["scope"], source["engine"], p)
            summary = {**saved, "source_scan": scan_id, "captured_at": source["started"], "recovered": True,
                       "import_complete": False, "phase": "copying", "coverage_unverified": True,
                       "coverage_note": "Phục hồi từ metadata đã nhập sẵn, không thực hiện quét mới; độ bao phủ của lượt nguồn không thể xác minh lại.",
                       "note": "Phục hồi từ metadata đã nhập; không quét mới, không biết dung lượng trống tại thời điểm cũ nếu chưa được lưu."}
            store.record_stage(new_id, summary); started = time.monotonic()
            store.db.set_progress_handler(lambda: int(stop.is_set()), 1000)
            try:
                notify("started",{"scan_id":new_id,"engine":source["engine"]})
                notify("info",{"message":"Đang tạo tham chiếu metadata phục hồi, không quét file..."})
                task.stage('copy')
                store.db.execute("UPDATE scans SET started=? WHERE id=?", (source["started"], new_id))
                store.db.execute("INSERT INTO members SELECT ?,path_id,revision_id FROM members WHERE scan_id=?", (new_id, scan_id)); store.db.commit()
                task.complete('Đã tạo snapshot từ metadata nguồn')
                summary["import_complete"]=True;summary["phase"]="aggregating";store.record_stage(new_id,summary)
                task.stage('totals')
                store.aggregate(new_id, stop=stop, notify=notify)
                task.complete('Đã tổng hợp dung lượng và xử lý hardlink')
                task.stage('save')
                summary["folder_totals_source"]="aggregated-files-v1"
                summary.update(store.totals(new_id)); summary["phase"] = "partial"
                summary["errors"] = store.db.execute("SELECT COUNT(*) FROM errors WHERE scan_id=?", (scan_id,)).fetchone()[0]
                store.db.execute("INSERT INTO errors SELECT ?,path,message FROM errors WHERE scan_id=?", (new_id, scan_id))
                summary["seconds"] = round(time.monotonic() - started, 3)
                summary['completed_steps']=[*task.completed,'Đã lưu lịch sử phục hồi và số đo data/log']
                store.db.set_progress_handler(None, 0)
                store.finish(new_id, "partial", summary)
                logging.getLogger("dgm").info("RECOVERY source=%s new=%s seconds=%s", scan_id, new_id, summary["seconds"])
                summary["storage"] = finish_storage(storage_meter)
                store.finish(new_id, "partial", summary)
                task.finish(f"Đã lưu snapshot phục hồi: {summary['files']:,} file • {summary['folders']:,} thư mục")
                return {"scan_id": new_id, "status": "partial", **summary}
            except BaseException as exc:
                store.db.set_progress_handler(None, 0); store.db.rollback()
                cancelled=stop.is_set() or isinstance(exc,(ProcessCancelled,KeyboardInterrupt))
                status="cancelled" if cancelled else "failed";summary["phase"]=status;summary["seconds"]=round(time.monotonic()-started,3)
                store.finish(new_id,status,summary)
                if cancelled:return {"scan_id":new_id,"status":status,**summary}
                raise
        finally:
            lock.interrupt = None; store.close()
