"""Local, token-scoped cancellation shared by CLI, GUI and scheduled scans."""
import json
import time
import uuid
import psutil
from .config import home


def current():
    try:
        raw = json.loads((home() / "data/worker.lock").read_text())
        pid = raw["pid"] if isinstance(raw, dict) else int(raw)
        process = psutil.Process(pid)
        try:
            if isinstance(raw, dict) and abs(process.create_time() - raw["created"]) >= 0.01:
                return {"running": False}
        except psutil.AccessDenied:
            # Cancellation still requires the exact nonce in the owned lock;
            # we never terminate a process based on this unverified PID.
            pass
        return {"running": True, "pid": pid, "cancellable": isinstance(raw, dict) and bool(raw.get("token")),
                "owner": raw if isinstance(raw, dict) else None}
    except (FileNotFoundError, ValueError, KeyError, psutil.NoSuchProcess):
        return {"running": False}


def request_stop():
    state = current()
    if not state["running"]:
        return {"requested": False, "message": "Không có lượt quét đang chạy."}
    if not state["cancellable"]:
        raise RuntimeError("Lượt quét thuộc phiên bản cũ. Dừng/đóng cửa sổ cũ trước khi mở bản mới; không cưỡng bức kết thúc tiến trình.")
    path = home() / "data/cancel-request.json"
    temporary = path.with_name("cancel-request-" + uuid.uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(state["owner"]), encoding="utf-8")
    try:
        for attempt in range(5):
            try:
                temporary.replace(path)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.02)
    finally:
        temporary.unlink(missing_ok=True)
    return {"requested": True, "message": "Đã yêu cầu dừng. Chờ tiến trình giải phóng khóa trước khi quét lại."}
