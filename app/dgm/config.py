from __future__ import annotations

import fnmatch
import hashlib
import json
import ntpath
import os
from contextvars import ContextVar
from pathlib import Path

ROOT = Path(os.environ.get("DGM_HOME", Path(__file__).resolve().parents[2])).resolve()
ENGINE_OVERRIDE=ContextVar('dgm_engine_override',default=None)
DEBUG_OVERRIDE=ContextVar('dgm_debug_override',default=None)


def home() -> Path:
    for name in ("data", "logs", "data/tmp"):
        (ROOT / name).mkdir(parents=True, exist_ok=True)
    os.environ["SQLITE_TMPDIR"] = str(ROOT / "data/tmp")
    return ROOT


def normalize(path) -> str:
    return str(path).replace("\\", "/").rstrip("/").casefold()


def comparison_path_key(path) -> str:
    """Windows path identity for comparing engine exports, retaining display spelling elsewhere."""
    return ntpath.normcase(ntpath.normpath(str(path).replace("/", "\\"))).casefold()


def inside(path, parent) -> bool:
    a, b = normalize(path), normalize(parent)
    return a == b or a.startswith(b + "/")


def browser_path(path) -> bool:
    parts = normalize(path).split("/")
    exact = {"chrome", "chrome beta", "chrome dev", "chrome sxs", "edge", "edge beta", "edge dev", "edge sxs", "edgecore", "edgeupdate", "edgewebview", "googleupdater", "microsoft-edge-webview"}
    return any(p in exact or "microsoft-edge-webview" in p or "microsoft-edge_" in p for p in parts)


def defaults() -> dict:
    return {"schema": 1, "threshold_mb": 10, "retention_days": 90, "max_snapshots": 90,
            "database_limit_mb": 4096, "debug": False,
            "source_archive_limit_mb":512,"source_archive_retention_days":90,
            "engines": {"wiztree": r"C:\Program Files\WizTree\WizTree64.exe", "windirstat": "", "gdu": ""},
            "profiles": {"daily": {"roots": ["C:\\"], "excludes": [], "strict_browsers": False, "engine": "wiztree", "mft": True}},
            "schedules": [], "alerts": {"free_gb": 10, "growth_gb": 2},
            "ai_providers": {"ChatGPT": "https://chatgpt.com/", "Gemini": "https://gemini.google.com/", "Claude": "https://claude.ai/", "Copilot": "https://copilot.microsoft.com/"}}


def load() -> dict:
    path = home() / "data/config.json"
    if not path.exists():
        save(defaults())
    cfg=json.loads(path.read_text(encoding="utf-8"))
    if ENGINE_OVERRIDE.get() is not None:cfg['engines']=dict(ENGINE_OVERRIDE.get())
    if DEBUG_OVERRIDE.get() is not None:cfg['debug']=DEBUG_OVERRIDE.get()
    return cfg


def save(config: dict):
    path = home() / "data/config.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def profile(name: str) -> dict:
    cfg = load()
    if name not in cfg["profiles"]:
        raise ValueError(f"Không có profile: {name}")
    result = dict(cfg["profiles"][name])
    result.setdefault("threshold_mb", cfg["threshold_mb"])
    return result


def scope_hash(p: dict) -> str:
    scope = {"roots": sorted(normalize(x) for x in p["roots"]), "excludes": sorted(p.get("excludes", [])),
             "strict_browsers": p.get("strict_browsers", False), "semantics": "logical-allocated-v1",
             "volumes": sorted({volume_identity(x) for x in p["roots"]}),
             "implicit_excludes":[normalize(ROOT)]}
    return hashlib.sha256(json.dumps(scope, sort_keys=True).encode()).hexdigest()


def volume_identity(path):
    if os.name == "nt":
        import ctypes
        anchor = Path(path).absolute().anchor
        buffer = ctypes.create_unicode_buffer(1024)
        if ctypes.windll.kernel32.GetVolumeNameForVolumeMountPointW(anchor, buffer, len(buffer)):
            return buffer.value
        return "unknown-volume:" + normalize(anchor)
    try:
        return "device:" + str(Path(path).stat().st_dev)
    except OSError:
        return "unknown-volume"


class Policy:
    def __init__(self, p: dict):
        self.p = p
        roots = sorted({str(Path(x).absolute()) for x in p["roots"]}, key=len)
        self.roots = [x for i, x in enumerate(roots) if not any(inside(x, prev) for prev in roots[:i])]

    def excluded(self, path) -> bool:
        if self.p.get("strict_browsers", False) and browser_path(path):
            return True
        if inside(path, ROOT):
            return True
        normalized = normalize(path)
        for pattern in self.p.get("excludes", []):
            pat = normalize(pattern)
            if inside(normalized, pat) or fnmatch.fnmatch(normalized, pat):
                return True
        return False

    def in_scope(self, path) -> bool:
        return any(inside(path, root) for root in self.roots) and not self.excluded(path)

