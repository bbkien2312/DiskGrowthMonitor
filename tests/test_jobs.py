from datetime import datetime, timedelta, timezone
from dgm.config import load, save
from dgm.schedule import tick
from dgm.store import Store


def test_due_schedule_once_and_never_auto_delete(isolated, monkeypatch):
    cfg = load()
    cfg["schedules"] = [{"id": "test", "profile": "daily", "mode": "daily", "interval": 1,
                         "times": ["09:00"], "timezone": "Asia/Ho_Chi_Minh", "enabled": True,
                         "next_run": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(), "cleanup_rules": []}]
    save(cfg)
    calls = []
    import dgm.schedule as schedules
    monkeypatch.setattr(schedules, "scan", lambda *a, **k: calls.append(a) or {"scan_id": 1, "status": "complete"})
    assert len(tick()) == 1
    assert tick() == []
    assert len(calls) == 1


def test_live_claim_prevents_duplicate_worker(isolated, monkeypatch):
    import json, os, psutil
    cfg = load()
    due = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    cfg["schedules"] = [{"id": "test", "profile": "daily", "mode": "daily", "times": ["09:00"], "timezone": "Asia/Ho_Chi_Minh", "enabled": True, "next_run": due}]
    save(cfg)
    s = Store()
    s.db.execute("INSERT INTO jobs VALUES(?,?,?,?)", ("test", due, due, json.dumps({"status": "running", "pid": os.getpid(), "process_created": psutil.Process().create_time()})))
    s.db.commit(); s.close()
    assert tick() == []
