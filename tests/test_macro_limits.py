from phonectl.macro import limits as L


def test_cooldown_blocks():
    ok, reason = L.allow("m", {"cooldown_seconds": 300}, now=1000.0, history=[900.0])
    assert ok is False and "cooldown" in reason


def test_cooldown_passes_after_window():
    ok, _ = L.allow("m", {"cooldown_seconds": 300}, now=1300.0, history=[900.0])
    assert ok is True


def test_max_runs_per_hour():
    hist = [3600.0 + i for i in range(5)]  # 5 fires in the last hour
    ok, reason = L.allow("m", {"max_runs_per_hour": 5}, now=3700.0, history=hist)
    assert ok is False and "per_hour" in reason


def test_no_limits_allows():
    ok, _ = L.allow("m", {}, now=1.0, history=[])
    assert ok is True


def test_record_and_load_roundtrip(tmp_path):
    p = tmp_path / "h.json"
    L.record("m", now=10.0, store_path=p)
    L.record("m", now=20.0, store_path=p)
    assert L.load(p)["m"] == [10.0, 20.0]
