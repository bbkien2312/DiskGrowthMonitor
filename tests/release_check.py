"""Read-only release verification and generated QA reports, except app-owned metadata migration."""
import json
import statistics
import time
from pathlib import Path
from dgm.config import ROOT, scope_hash
from dgm.store import Store

s = Store()
# The first baseline was captured before stable-volume fingerprinting was added.
# Its observed scope remains the same; upgrade its app-owned scope metadata.
row = s.scan(13)
cfg = json.loads(row["config"])
s.db.execute("UPDATE scans SET scope=? WHERE id=13", (scope_hash(cfg),))
s.db.commit()
t = time.perf_counter()
rows = s.compare(None, 13, limit=1000)
query_seconds = time.perf_counter() - t
assert len(rows) == 1000
assert s.db.execute("SELECT COUNT(*) FROM entries WHERE scan_id=13").fetchone()[0] == 800611
bad = s.db.execute("SELECT path FROM entries WHERE scan_id=13 AND (path LIKE '%\\Chrome\\%' OR path LIKE '%\\Edge\\%' OR path LIKE '%\\EdgeCore\\%') LIMIT 1").fetchone()
assert bad is None
benchmarks = json.loads(s.db.execute("SELECT content FROM benchmarks ORDER BY id DESC LIMIT 1").fetchone()[0])
summary = {}
for name in ("native", "wiztree", "windirstat", "gdu"):
    runs = [x for x in benchmarks if x["engine"] == name and "total_seconds" in x]
    assert len(runs) == 3 and all(x["accuracy_matches_reference"] for x in runs)
    summary[name] = {"median_total_seconds": statistics.median(x["total_seconds"] for x in runs),
                     "median_peak_tree_mb": statistics.median(x["peak_rss"] / 1e6 for x in runs),
                     "median_cpu_seconds": statistics.median(x["cpu_seconds_total"] for x in runs),
                     "files": runs[0]["files"], "folders": runs[0]["folders"]}
report = {"baseline": {"id": 13, "status": row["status"], **json.loads(row["summary"])},
          "query_1000_rows_seconds": round(query_seconds, 3), "benchmark_folder_only": summary,
          "note": "Synthetic 2004 files + 41 folders. Includes export, metadata enrichment, DB, compare; not full-volume MFT."}
(ROOT / "data/release-check.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"query_seconds": query_seconds, "benchmark": summary}, indent=2))
s.close()
