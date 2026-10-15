from datetime import datetime, timezone
import json
from typer.testing import CliRunner
from dgm.cli import app
from dgm.schedule import next_time, add, preview


def test_daily_multiple_times():
    spec = {"mode": "daily", "times": ["08:00", "14:00", "20:00"], "timezone": "Asia/Ho_Chi_Minh"}
    point = datetime(2026, 10, 14, 2, 0, tzinfo=timezone.utc)
    assert next_time(spec, point) == datetime(2026, 10, 14, 7, 0, tzinfo=timezone.utc)


def test_month_end_keeps_anchor():
    spec = {"mode": "months", "interval": 1, "anchor": "2026-01-31T02:00:00+00:00", "timezone": "Asia/Ho_Chi_Minh"}
    point = datetime(2026, 2, 28, 3, 0, tzinfo=timezone.utc)
    assert next_time(spec, point) == datetime(2026, 3, 31, 2, 0, tzinfo=timezone.utc)


def test_leap_year():
    spec = {"mode": "years", "interval": 1, "anchor": "2024-02-29T02:00:00+00:00", "timezone": "Asia/Ho_Chi_Minh"}
    point = datetime(2027, 3, 1, 0, 0, tzinfo=timezone.utc)
    assert next_time(spec, point) == datetime(2028, 2, 29, 2, 0, tzinfo=timezone.utc)


def test_cli_gui_same_profile_storage(isolated):
    runner = CliRunner()
    root = isolated / "documents"; root.mkdir(); (root / "a").write_bytes(b"x" * 100)
    result = runner.invoke(app, ["profile", "set", "demo", "--root", str(root), "--engine", "native"])
    assert result.exit_code == 0, result.output
    result = runner.invoke(app, ["scan", "--profile", "demo"])
    assert result.exit_code == 0, result.output
    from dgm.store import Store
    s = Store(); rows = s.scans("demo"); s.close()
    assert rows[0]["status"] == "complete"
    result = runner.invoke(app, ["compare", str(rows[0]["id"])])
    assert result.exit_code == 0 and "Lần đầu quét" in result.output

