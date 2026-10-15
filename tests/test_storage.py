from dgm.store import Store
from dgm.scanner import scan


def test_unchanged_revisions_reused_and_old_snapshot_preserved(isolated):
    root = isolated / "documents"; root.mkdir(); f = root / "a"; f.write_bytes(b"test")
    p = {"roots": [str(root)], "strict_browsers": True}
    a = scan("demo", p)
    s = Store(); before = s.db.execute("SELECT COUNT(*) FROM revisions").fetchone()[0]; s.close()
    b = scan("demo", p)
    s = Store(); after = s.db.execute("SELECT COUNT(*) FROM revisions").fetchone()[0]
    assert after <= before + 1
    assert len(s.entries(a["scan_id"])) == 2
    assert all(row["delta"] == 0 for row in s.compare(a["scan_id"], b["scan_id"]))
    s.close()
