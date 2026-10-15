"""Read-only version checks against publishers. Never installs packages."""
import json
import logging
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from .diagnostics import doctor
from . import __version__

PACKAGES = ("PySide6-Essentials", "typer", "psutil", "Send2Trash", "APScheduler", "pywin32", "python-dateutil", "tzdata", "ijson")
REPOS = {"windirstat": "windirstat/windirstat", "gdu": "dundee/gdu"}


def inventory():
    import importlib.metadata
    info = doctor()
    rows = [{"id": "python", "kind": "runtime", "name": "Python riêng của app", "installed": sys.version.split()[0],
             "path": sys.executable, "source": "https://www.python.org/downloads/", "checkable": False}]
    for name, details in info["engines"].items():
        version = details["version"]
        if name in REPOS and not version and details["exists"]:
            from .config import home
            try:
                version = json.loads((home() / f".venv/tools/{name}/SOURCE.json").read_text())["version"]
            except (OSError, ValueError, KeyError):
                pass
        rows.append({"id": name, "kind": "engine", "name": {"wiztree": "WizTree", "windirstat": "WinDirStat", "gdu": "gdu"}[name],
                     "installed": version or ("Có file; chưa rõ phiên bản" if details["exists"] else "Chưa cài"), "path": details["path"],
                     "source": "https://diskanalyzer.com/download" if name == "wiztree" else f"https://github.com/{REPOS[name]}/releases", "checkable": True})
    for name in PACKAGES:
        try:
            version = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            version = "Chưa cài"
        rows.append({"id": name, "kind": "package", "name": name, "installed": version,
                     "path": sys.prefix, "source": f"https://pypi.org/project/{name}/", "checkable": True})
    return rows


def fetch(url, json_data=True):
    logging.getLogger("dgm").debug("UPDATE-CHECK GET %s", url)
    request = urllib.request.Request(url, headers={"User-Agent": "DiskGrowthMonitor/" + __version__, "Accept": "application/json" if json_data else "text/html"})
    with urllib.request.urlopen(request, timeout=12) as response:
        content = response.read(4_000_001)
    if len(content) > 4_000_000:
        raise ValueError("Phản hồi kiểm tra cập nhật quá lớn")
    return json.loads(content) if json_data else content.decode("utf-8", errors="replace")


def version_key(value):
    match = re.search(r"\d+(?:\.\d+)*", value)
    return tuple(int(x) for x in match[0].split(".")) if match else ()


def check_one(row):
    result = {**row, "latest": "", "status": "Không kiểm tra tự động"}
    if not row["checkable"]:
        return result
    try:
        name = row["id"]
        if name == "wiztree":
            html = fetch("https://diskanalyzer.com/download", False)
            candidates = re.findall(r"WizTree\s+(\d+\.\d+(?:\.\d+)*)", html)
            if not candidates:
                raise ValueError("Trang nhà phát hành thay đổi; xem nguồn chính thức")
            latest = max(candidates, key=version_key)
        elif name in REPOS:
            latest = fetch(f"https://api.github.com/repos/{REPOS[name]}/releases/latest")["tag_name"]
        else:
            latest = fetch(f"https://pypi.org/pypi/{name}/json")["info"]["version"]
        installed_key, latest_key = version_key(row["installed"]), version_key(latest)
        count = max(len(installed_key), len(latest_key))
        installed_key += (0,) * (count - len(installed_key)); latest_key += (0,) * (count - len(latest_key))
        result.update(latest=latest, status="Chưa cài / chưa rõ" if not version_key(row["installed"]) else
                      "Có bản mới (chưa cài)" if latest_key > installed_key else "Đang dùng bản mới nhất / mới hơn")
    except Exception as exc:
        result["status"] = "Không kiểm tra được: " + str(exc)
    return result


def check_updates(names=None):
    rows = [r for r in inventory() if names is None or r["id"] in names]
    if names is not None and set(names) - {r["id"] for r in rows}:
        raise ValueError("Tên gói/công cụ không có trong danh sách của app")
    with ThreadPoolExecutor(max_workers=4) as pool:
        return list(pool.map(check_one, rows))
