import json
import threading
import time
import psutil
from .scanner import scan
from .store import Store, now
from .platform_ops import reset_measurements, measurements


def benchmark(name, p, engines=("native", "wiztree"), repeats=3, notify=None, cancel=None):
    from .storage_metrics import begin as begin_storage, finish as finish_storage
    storage_meter = begin_storage("benchmark")
    cancel = cancel or threading.Event()
    results = []
    reference = None
    for round_number in range(repeats):
        for engine in list(engines)[round_number % len(engines):] + list(engines)[:round_number % len(engines)]:
            if cancel.is_set():
                break
            if p.get("strict_browsers", False) and engine != "native":
                if round_number == 0:
                    results.append({"engine": engine, "status": "skipped", "reason": "Profile cấm đọc browser metadata; không chạy MFT toàn ổ"})
                continue
            stop = threading.Event()
            samples = {"peak_rss": 0, "peak_cpu_percent": 0}
            current = psutil.Process()
            cpu_start = sum(current.cpu_times()[:2])
            io_start = current.io_counters()

            def poll():
                while not stop.wait(0.05):
                    processes = [current]
                    try:
                        processes += current.children(recursive=True)
                    except psutil.Error:
                        pass
                    ram, cpu = 0, 0
                    for process in processes:
                        try:
                            ram += process.memory_info().rss
                            cpu += process.cpu_percent(None)
                        except psutil.Error:
                            pass
                    samples["peak_rss"] = max(samples["peak_rss"], ram)
                    samples["peak_cpu_percent"] = max(samples["peak_cpu_percent"], cpu)

            thread = threading.Thread(target=poll, daemon=True)
            thread.start()
            started = time.perf_counter()
            reset_measurements()
            try:
                result = scan(f"benchmark-{name}", {**p, "engine": engine}, stop=cancel, notify=notify)
                if result.get('before_start'):
                    results.append({'engine':engine,'status':'cancelled','error':result.get('message','Đã hủy cấp quyền')});cancel.set();continue
                signature = (result["files"], result["folders"], result.get("logical"), result.get("allocated"))
                if engine == "native" and result["status"] == "complete":
                    reference = signature
                s = Store()
                previous = s.previous(result["scan_id"])
                # Execute the compare query as part of the end-to-end measurement.
                if result["status"] in ("complete", "partial"):
                    s.compare(previous, result["scan_id"], limit=1000)
                s.close()
                cpu = sum(current.cpu_times()[:2]) - cpu_start
                io = current.io_counters()
                children = measurements()
                results.append({"engine": engine, "round": round_number + 1, "status": result["status"],
                                "total_seconds": round(time.perf_counter() - started, 3), "scan_seconds": result["seconds"],
                                "cpu_seconds_parent": round(cpu, 3), "read_bytes_parent": io.read_bytes - io_start.read_bytes,
                                "write_bytes_parent": io.write_bytes - io_start.write_bytes, "files": result["files"],
                                "folders": result["folders"], "errors": result.get("errors", 0), **samples,
                                "logical": result.get("logical"), "allocated": result.get("allocated"),
                                "accuracy_matches_reference": signature == reference if reference is not None else None,
                                "scan_storage": result.get("storage"),
                                "cpu_seconds_total": round(cpu + sum(x["cpu_seconds"] for x in children), 3),
                                "read_bytes_total": io.read_bytes - io_start.read_bytes + sum(x["read_bytes"] for x in children),
                                "write_bytes_total": io.write_bytes - io_start.write_bytes + sum(x["write_bytes"] for x in children),
                                "engine_peak_rss": max([x["peak_rss"] for x in children], default=0), "subprocess_metrics": children,
                                "note": "RAM tree sampled; direct engine CPU/I/O measured using Windows process handles; OS cache not reset"})
            except Exception as exc:
                results.append({"engine": engine, "round": round_number + 1, "status": "failed", "error": str(exc)})
            finally:
                stop.set()
                thread.join()
    storage = finish_storage(storage_meter)
    for item in results:
        item["benchmark_storage"] = storage
    store = Store()
    store.db.execute("INSERT INTO benchmarks(created,content) VALUES(?,?)", (now(), json.dumps(results, ensure_ascii=False)))
    store.db.commit()
    store.close()
    return results

