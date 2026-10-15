import json
import logging
from logging.handlers import RotatingFileHandler
from .config import home
from contextvars import ContextVar

BRIDGE=ContextVar('dgm_log_bridge',default=None)

class BridgeHandler(logging.Handler):
    def __init__(self,bridge):super().__init__();self.bridge=bridge
    def emit(self,record):
        self.bridge('log',{'level':record.levelno,'message':self.format(record)})


def setup(debug=False):
    log = logging.getLogger("dgm")
    log.setLevel(logging.DEBUG if debug else logging.INFO)
    for h in log.handlers[:]:
        h.close()
        log.removeHandler(h)
    handler = BridgeHandler(BRIDGE.get()) if BRIDGE.get() else RotatingFileHandler(home() / "logs/app.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s" if BRIDGE.get() else "%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    return log


def audit(action, **fields):
    if BRIDGE.get():
        BRIDGE.get()('audit',{'action':action,'fields':fields});return
    log = logging.getLogger("dgm.audit")
    target = home() / "logs/audit.jsonl"
    for old in log.handlers[:]:
        if getattr(old, "baseFilename", "") != str(target.resolve()):
            old.close()
            log.removeHandler(old)
    if not log.handlers:
        h = RotatingFileHandler(target, maxBytes=5_000_000, backupCount=10, encoding="utf-8")
        h.setFormatter(logging.Formatter("%(message)s"))
        log.addHandler(h)
        log.setLevel(logging.INFO)
        log.propagate = False
    from datetime import datetime, timezone
    log.info(json.dumps({"time": datetime.now(timezone.utc).isoformat(), "action": action, **fields}, ensure_ascii=False))

