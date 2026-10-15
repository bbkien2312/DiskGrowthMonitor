"""Pinned portable OSS tools; all writes stay within the application directory."""
from pathlib import Path
import hashlib
import json
import urllib.request
import zipfile
from .config import ROOT, home, load, save, inside

SOURCES = {
    "windirstat": {"url": "https://github.com/windirstat/windirstat/releases/download/release/v2.9.3/WinDirStat.zip", "exe": "x64/WinDirStat.exe", "version": "2.9.3", "source": "https://github.com/windirstat/windirstat/tree/release/v2.9.3", "license": "GPL-3.0-or-later"},
    "gdu": {"url": "https://github.com/dundee/gdu/releases/download/v5.38.0/gdu_windows_amd64.exe.zip", "exe": "gdu_windows_amd64.exe", "version": "5.38.0", "source": "https://github.com/dundee/gdu/tree/v5.38.0", "license": "MIT"},
}


def install(names=("windirstat", "gdu")):
    cfg, results = load(), []
    for name in names:
        if name not in SOURCES:
            raise ValueError("Chỉ cài portable engine nguồn mở đã pin")
        info = SOURCES[name]
        destination = ROOT / ".venv/tools" / name
        destination.mkdir(parents=True, exist_ok=True)
        archive = home() / "data/tmp" / f"{name}.zip"
        if not (destination / info["exe"]).exists():
            urllib.request.urlretrieve(info["url"], archive)
            with zipfile.ZipFile(archive) as z:
                for item in z.infolist():
                    if not inside((destination / item.filename).resolve(), destination.resolve()):
                        raise ValueError("ZIP chứa đường dẫn ngoài phạm vi")
                z.extractall(destination)
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        else:
            digest = "existing installation; binary digest below"
        exe = destination / info["exe"]
        provenance = {**info, "archive_sha256": digest, "executable_sha256": hashlib.sha256(exe.read_bytes()).hexdigest()}
        (destination / "SOURCE.json").write_text(json.dumps(provenance, indent=2), encoding="utf-8")
        if name == "windirstat":
            (exe.parent / "WinDirStat.ini").write_text("[Options]\nLanguage=1033\n", encoding="utf-8")
        license_url = ("https://raw.githubusercontent.com/windirstat/windirstat/release/v2.9.3/LICENSE.md" if name == "windirstat"
                       else "https://raw.githubusercontent.com/dundee/gdu/v5.38.0/LICENSE.md")
        try:
            urllib.request.urlretrieve(license_url, destination / "LICENSE.txt")
        except Exception:
            (destination / "LICENSE-SOURCE.txt").write_text(info["license"] + "\n" + info["source"], encoding="utf-8")
        cfg["engines"][name] = str(exe)
        results.append({"engine": name, "path": str(exe), "version": info["version"]})
    save(cfg)
    return results

