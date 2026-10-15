"""One recoverable local metadata backup before the v2 migration."""
import sqlite3
from pathlib import Path

root = Path(__file__).resolve().parents[1]
destination = root / "data/backups/history-before-020.sqlite"
destination.parent.mkdir(exist_ok=True)
if not destination.exists():
    source = sqlite3.connect(f"file:{(root / 'data/history.sqlite').as_posix()}?mode=ro", uri=True)
    backup = sqlite3.connect(destination)
    source.backup(backup, pages=4096)
    print("Backup:", destination, "scans:", backup.execute("SELECT COUNT(*) FROM scans").fetchone()[0])
    backup.close(); source.close()
else:
    print("Existing backup preserved:", destination)
