from __future__ import annotations
import json
import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from dateutil.relativedelta import relativedelta
from .config import ROOT, home, load, save, profile
from .platform_ops import run, system_tool
from .scanner import scan
from .store import Store, now

TASK = "DiskGrowthMonitor-Local"


def next_time(spec, after=None):
    tz = ZoneInfo(spec.get("timezone", "Asia/Ho_Chi_Minh"))
    point = (after or datetime.now(timezone.utc)).astimezone(tz)
    mode = spec["mode"]
    interval = int(spec.get("interval", 1))
    if interval < 1:
        raise ValueError("Khoảng lặp phải >=1")
    times = spec.get("times", ["09:00"])
    for value in times:
        datetime.strptime(value, "%H:%M")
    if mode == "daily":
        candidates = [point.replace(hour=int(t.split(":")[0]), minute=int(t.split(":")[1]), second=0, microsecond=0) for t in times]
        valid = [x for x in candidates if x > point]
        result = min(valid) if valid else min(candidates) + timedelta(days=1)
    elif mode == "weekly":
        weekdays = spec.get("weekdays", [0])
        if not weekdays or any(int(x) not in range(7) for x in weekdays):
            raise ValueError("Thứ trong tuần phải từ 0 (thứ Hai) đến 6")
        candidates = []
        for day in range(15):
            current = point + timedelta(days=day)
            if current.weekday() in weekdays:
                for t in times:
                    candidate = current.replace(hour=int(t[:2]), minute=int(t[3:]), second=0, microsecond=0)
                    if candidate > point:
                        candidates.append(candidate)
        result = min(candidates)
    elif mode in ("hours", "days", "weeks", "months", "years"):
        anchor = datetime.fromisoformat(spec.get("anchor", now())).astimezone(tz)
        if mode in ("months", "years"):
            target_day = anchor.day
            n = 0
            result = anchor
            while result <= point:
                n += interval
                result = anchor + relativedelta(**{mode: n})
                if n > 100000:
                    raise ValueError("Anchor quá cũ")
        else:
            seconds = {"hours": 3600, "days": 86400, "weeks": 604800}[mode] * interval
            n = max(0, int((point - anchor).total_seconds() // seconds) + 1)
            result = anchor + timedelta(seconds=n * seconds)
    else:
        raise ValueError("Mode lịch không hợp lệ")
    return result.astimezone(timezone.utc)


def add(spec):
    spec = {"id": uuid.uuid4().hex[:10], "enabled": True, "mode": "daily", "interval": 1,
            "times": ["09:00"], "timezone": "Asia/Ho_Chi_Minh", "profile": "daily", "anchor": now(),
            "cleanup_rules": [], "update_done": False, **spec}
    spec['times']=sorted(set(t.strip() for t in spec['times'] if t.strip()))
    if spec['mode'] in ('daily','weekly') and not spec['times']:raise ValueError('Cần ít nhất một giờ chạy HH:MM')
    spec['weekdays']=sorted(set(int(day) for day in spec.get('weekdays',[0])))
    spec['name']=str(spec.get('name') or spec['profile']).strip()[:120]
    p = profile(spec["profile"])
    if p.get("strict_browsers", False) and p.get("engine", "native") != "native":
        raise ValueError("Cấu hình đang bảo vệ Chrome/Edge nhưng chọn công cụ ngoài native. Sửa và Lưu cấu hình trước khi thêm lịch.")
    spec["next_run"] = next_time(spec).isoformat()
    cfg = load()
    cfg["schedules"].append(spec)
    save(cfg)
    return spec


def update(schedule_id,changes):
    allowed={'name','profile','mode','interval','times','weekdays','timezone','cleanup_rules'}
    if set(changes)-allowed:raise ValueError('Trường sửa lịch không hợp lệ')
    cfg=load();spec=next((s for s in cfg['schedules'] if s['id']==schedule_id),None)
    if not spec:raise ValueError('Không tìm thấy lịch')
    candidate={**spec,**changes};candidate['times']=sorted(set(t.strip() for t in candidate['times'] if t.strip()))
    if candidate['mode'] in ('daily','weekly') and not candidate['times']:raise ValueError('Cần ít nhất một giờ chạy')
    candidate['weekdays']=sorted(set(int(day) for day in candidate.get('weekdays',[0])))
    p=profile(candidate['profile'])
    if p.get('strict_browsers') and p.get('engine','native')!='native':raise ValueError('Cấu hình bảo vệ Chrome/Edge yêu cầu native')
    timing_changed=any(candidate.get(key)!=spec.get(key) for key in ('mode','interval','times','weekdays','timezone'))
    if timing_changed:candidate['next_run']=next_time(candidate).isoformat()
    spec.update(candidate);save(cfg)
    store=Store()
    try:
        if timing_changed:store.db.execute('UPDATE jobs SET next_run=? WHERE schedule_id=?',(candidate['next_run'],schedule_id))
        store.db.commit()
    finally:store.close()
    return candidate


def describe(spec):
    mode=spec['mode'];times=', '.join(spec.get('times',[]))
    if mode=='daily':return f"{len(spec.get('times',[]))} lần/ngày • {times}"
    if mode=='weekly':
        days=', '.join(('T2','T3','T4','T5','T6','T7','CN')[day] for day in spec.get('weekdays',[]))
        return days+' • '+times
    return f"Mỗi {spec.get('interval',1)} "+{'hours':'giờ','days':'ngày','weeks':'tuần','months':'tháng','years':'năm'}.get(mode,mode)


def modify(schedule_id, enabled=None, delete=False):
    cfg = load()
    found = next((x for x in cfg["schedules"] if x["id"] == schedule_id), None)
    if not found:
        raise ValueError("Không tìm thấy lịch")
    if delete:
        cfg["schedules"].remove(found)
    elif enabled is not None:
        found["enabled"] = enabled
    save(cfg)


def listing():
    rows = load()["schedules"]
    store = Store(readonly=True)
    for row in rows:
        job = store.db.execute("SELECT * FROM jobs WHERE schedule_id=?", (row["id"],)).fetchone()
        if job:
            row["next_run"] = job["next_run"]
            row["last_run"] = job["last_run"]
            row["last_result"] = json.loads(job["result"])
    store.close()
    return rows


def preview(spec, count=10):
    rows, point = [], datetime.now(timezone.utc)
    for _ in range(count):
        point = next_time(spec, point)
        rows.append(point.astimezone(ZoneInfo(spec.get("timezone", "Asia/Ho_Chi_Minh"))).isoformat())
    return rows


def run_now(schedule_id, stop=None, notify=None):
    spec = next((s for s in load()["schedules"] if s["id"] == schedule_id), None)
    if not spec:
        raise ValueError("Không có lịch")
    result = scan(spec["profile"], profile(spec["profile"]), stop=stop, notify=notify)
    if spec.get("cleanup_rules") and result["status"] in ("complete", "partial") and not (stop and stop.is_set()):
        from .cleanup import plan
        result["pending_plan"] = plan(spec["cleanup_rules"], update_done=False,stop=stop,notify=notify,compact=True)["id"]
    store=Store()
    try:
        store.db.execute('INSERT OR REPLACE INTO jobs VALUES(?,?,?,?)',(spec['id'],now(),next_time(spec).isoformat(),json.dumps(result)))
        store.db.commit()
    finally:store.close()
    return result


def tick(stop=None):
    cfg = load()
    results = []
    for spec in cfg["schedules"]:
        if stop is not None and stop.is_set():
            break
        if not spec.get("enabled", True):
            continue
        store = Store()
        store.db.execute("BEGIN IMMEDIATE")
        row = store.db.execute("SELECT * FROM jobs WHERE schedule_id=?", (spec["id"],)).fetchone()
        next_run = datetime.fromisoformat(row["next_run"] if row else spec["next_run"])
        import psutil
        previous_result = json.loads(row["result"]) if row and row["result"] else {}
        if previous_result.get("status") == "running":
            try:
                process = psutil.Process(previous_result["pid"])
                if abs(process.create_time() - previous_result["process_created"]) < 0.01:
                    store.db.rollback(); store.close(); continue
            except (psutil.Error, KeyError):
                pass
        if next_run > datetime.now(timezone.utc):
            store.db.rollback(); store.close()
            continue
        claim = {"status": "running", "pid": os.getpid(), "process_created": psutil.Process().create_time()}
        store.db.execute("INSERT OR REPLACE INTO jobs VALUES(?,?,?,?)", (spec["id"], now(), next_run.isoformat(), json.dumps(claim)))
        store.db.commit(); store.close()
        try:
            result = scan(spec["profile"], profile(spec["profile"]), stop=stop,allow_uac=False)
            if spec.get("cleanup_rules") and result["status"] in ("complete", "partial") and not (stop and stop.is_set()):
                from .cleanup import plan
                result["pending_plan"] = plan(spec["cleanup_rules"], update_done=False,allow_uac=False,stop=stop,compact=True)["id"]
            upcoming = next_time(spec).isoformat()
            store = Store()
            store.db.execute("INSERT OR REPLACE INTO jobs VALUES(?,?,?,?)", (spec["id"], now(), upcoming, json.dumps(result)))
            store.db.commit()
            store.close()
            results.append(result)
        except RuntimeError:
            # Existing scan: leave next_run due so it retries after the active scan.
            store = Store()
            store.db.execute("UPDATE jobs SET result=? WHERE schedule_id=?", (json.dumps({"status": "deferred"}), spec["id"]))
            store.db.commit(); store.close()
            continue
        except Exception as exc:
            import logging
            logging.getLogger("dgm").exception("Scheduled job %s failed: %s", spec["id"], exc)
            store = Store()
            store.db.execute("UPDATE jobs SET next_run=?,result=? WHERE schedule_id=?", (next_time(spec).isoformat(), json.dumps({"status": "failed", "error": str(exc)}), spec["id"]))
            store.db.commit(); store.close()
    return results


def install_task():
    if os.name != "nt":
        raise RuntimeError("Chỉ hỗ trợ Windows Task Scheduler")
    from .windows_service import status
    if status().get("installed"):
        raise RuntimeError("Đã đăng ký Windows Service; gỡ service trước khi chuyển sang Task Scheduler.")
    from .platform_ops import is_admin
    needs_admin = any(p.get("mft", False) for p in load()["profiles"].values())
    if needs_admin and not is_admin():
        raise PermissionError("Cấu hình có MFT cần đăng ký Task Scheduler bằng Admin để task chạy với quyền cao nhất.")
    app_root = home()
    from .runtime import background_command,command_line
    executable,arguments=background_command('schedule','tick')
    argv=command_line(executable,arguments)
    result = run([system_tool("schtasks.exe"), "/Create", "/TN", TASK, "/TR", argv, "/SC", "MINUTE", "/MO", "5", "/RL", "HIGHEST" if is_admin() else "LIMITED", "/F"])
    if result.returncode:
        raise RuntimeError(result.stdout + result.stderr)
    return {"task": TASK, "result": result.stdout, "note": "Worker kiểm tra lịch mỗi 5 phút; chỉ quét khi đến lịch. Task user mặc định chạy khi đăng nhập."}


def remove_task():
    result = run([system_tool("schtasks.exe"), "/Delete", "/TN", TASK, "/F"])
    if result.returncode:
        raise RuntimeError(result.stdout + result.stderr)
    return {"exit": result.returncode, "message": result.stdout + result.stderr}


def task_status():
    if os.name != "nt":
        return {"installed": False, "enabled": False}
    import pythoncom, pywintypes, win32com.client
    pythoncom.CoInitialize()
    scheduler = folder = task = None
    try:
        scheduler = win32com.client.Dispatch("Schedule.Service")
        scheduler.Connect()
        folder = scheduler.GetFolder("\\")
        task = folder.GetTask(TASK)
        actions=[]
        for index in range(1,task.Definition.Actions.Count+1):
            action=task.Definition.Actions.Item(index)
            if action.Type==0:
                actions.append({'path':str(action.Path),'arguments':str(action.Arguments),'working_directory':str(action.WorkingDirectory)})
        return {"installed": True, "enabled": bool(task.Enabled), "running": task.State == 4,
                'actions':actions,
                "run_level": "HighestAvailable" if task.Definition.Principal.RunLevel == 1 else "LeastPrivilege"}
    except pywintypes.com_error as exc:
        code = exc.excepinfo[5] if exc.excepinfo and exc.excepinfo[5] else exc.hresult
        if (code & 0xFFFFFFFF) == 0x80070002:  # HRESULT_FROM_WIN32(ERROR_FILE_NOT_FOUND)
            return {"installed": False, "enabled": False, "running": False}
        raise RuntimeError(f"Không đọc được trạng thái Task Scheduler: {exc}") from exc
    finally:
        action = task = folder = scheduler = None
        pythoncom.CoUninitialize()


def enable_task(enabled=True):
    if enabled:
        from .windows_service import status
        if status().get("installed"):
            raise RuntimeError("Windows Service đã đăng ký; không bật hai bộ chạy nền cùng lúc.")
    result = run([system_tool("schtasks.exe"), "/Change", "/TN", TASK, "/ENABLE" if enabled else "/DISABLE"], timeout=15)
    if result.returncode:
        raise RuntimeError(result.stdout + result.stderr)
    return {"enabled": enabled, "note": "Tắt lịch chạy nền; lượt quét đang chạy không bị cưỡng bức kết thúc. Dùng Dừng quét để hủy lượt hiện tại." if not enabled else "Đã bật Task Scheduler", **task_status()}


def agent(stop=None):
    import threading
    stop = stop or threading.Event()
    started = time.time()
    def watch():
        while not stop.wait(0.2):
            try:
                if (home() / "data/agent.stop").stat().st_mtime >= started:
                    stop.set()
            except FileNotFoundError:
                pass
    monitor = threading.Thread(target=watch, daemon=True)
    monitor.start()
    try:
        while not stop.is_set():
            tick(stop=stop)
            stop.wait(30)
    finally:
        stop.set(); monitor.join(timeout=1)

