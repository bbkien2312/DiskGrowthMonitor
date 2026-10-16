import json

from dgm.benchmark import benchmark
from dgm.comparison import Comparisons
from dgm.scanner import scan
from dgm.store import Store


def profile(root):
    return {"roots": [str(root)], "excludes": [], "strict_browsers": True, "engine": "native"}


def test_storage_metric_counts_only_app_owned_data_and_rotated_logs(isolated):
    from dgm.config import home
    from dgm.storage_metrics import begin, finish

    root = home()
    data = root / "data/history.sqlite"
    logs = root / "logs/app.log"
    data.write_bytes(b"x" * 100)
    logs.write_bytes(b"a" * 10)
    meter = begin("scan")
    with data.open("ab") as stream:
        stream.write(b"y" * 37)
    (root / "logs/app.log.1").write_bytes(b"r" * 5)
    report = finish(meter)
    assert report["data"]["delta_bytes"] == 37
    assert report["logs"]["delta_bytes"] == 5
    assert report["data"]["files"]["data/history.sqlite"]["after_bytes"] == 137
    assert "config.json" not in report["data"]["files"]


def test_comparison_history_records_storage_for_new_and_cached_pair(isolated):
    root = isolated / "fixture"
    root.mkdir()
    target = root / "data.bin"
    target.write_bytes(b"a" * 32)
    old = scan("demo", profile(root))
    target.write_bytes(b"b" * 64)
    new = scan("demo", profile(root))

    cache = Comparisons()
    try:
        pair = cache.prepare(old["scan_id"], new["scan_id"])
        summary = cache.summary(pair)
        assert summary["storage"]["operation"] == "comparison"
        assert "data/comparisons.sqlite" in summary["storage"]["data"]["files"]
        assert cache.prepare(old["scan_id"], new["scan_id"]) == pair
        cached = cache.summary(pair)["storage"]
        assert cached["operation"] == "comparison"
        assert cached["data"]["after_bytes"] >= 0
    finally:
        cache.close()


def test_external_engine_partial_status_explains_provenance(isolated, monkeypatch):
    root = isolated / "fixture"
    root.mkdir()
    import dgm.scanner as scanner

    def entries(policy, config, stop, notify):
        yield {"path": str(root), "parent": str(root.parent), "kind": "folder", "logical": 0,
               "allocated": 0, "created": 1, "modified": 1, "identity": "", "attrs": 0}

    monkeypatch.setattr(scanner, "wiztree_entries", entries)
    result = scan("demo", {**profile(root), "strict_browsers": False, "engine": "wiztree", "mft": False})
    assert result["status"] == "partial"
    assert result["coverage_unverified"] is True
    assert "engine ngoài (wiztree)" in result["coverage_note"]
    assert "không khẳng định chắc chắn đã bỏ sót" in result["coverage_note"]


def test_benchmark_output_and_persisted_result_include_storage(isolated):
    root = isolated / "fixture"
    root.mkdir()
    (root / "small.dat").write_bytes(b"test")
    results = benchmark("demo", profile(root), engines=("native",), repeats=1)
    assert results[0]["status"] == "complete"
    assert results[0]["scan_storage"]["operation"] == "scan"
    assert results[0]["benchmark_storage"]["operation"] == "benchmark"
    store = Store(readonly=True)
    try:
        saved = json.loads(store.db.execute("SELECT content FROM benchmarks ORDER BY id DESC LIMIT 1").fetchone()[0])
    finally:
        store.close()
    assert saved[0]["benchmark_storage"]["data"]["after_bytes"] >= 0
