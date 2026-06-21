from phonectl import ratelimit


def test_buckets_for_includes_global_verb_and_high_risk():
    assert set(ratelimit.buckets_for("tap", "low")) == {"global", "tap"}
    assert "high_risk" in ratelimit.buckets_for("tap", "critical")


def test_check_allows_under_limit_and_blocks_at_limit():
    limits = {"tap": 2, "global": 100}
    hist = [{"bucket": "tap", "ts": 0.0}, {"bucket": "global", "ts": 0.0}]
    ok, bucket = ratelimit.check(hist, "tap", "low", limits, now=1.0)
    assert ok is True and bucket is None
    hist += [{"bucket": "tap", "ts": 0.5}, {"bucket": "global", "ts": 0.5}]
    ok, bucket = ratelimit.check(hist, "tap", "low", limits, now=1.0)
    assert ok is False and bucket == "tap"


def test_check_window_expires_old_records():
    limits = {"tap": 1, "global": 100}
    hist = [{"bucket": "tap", "ts": 0.0}, {"bucket": "global", "ts": 0.0}]
    ok, _ = ratelimit.check(hist, "tap", "low", limits, now=120.0, window=60.0)
    assert ok is True


def test_high_risk_bucket_enforced():
    limits = {"high_risk": 1, "global": 100, "tap": 100}
    hist = [
        {"bucket": "high_risk", "ts": 0.0},
        {"bucket": "global", "ts": 0.0},
        {"bucket": "tap", "ts": 0.0},
    ]
    ok, bucket = ratelimit.check(hist, "tap", "critical", limits, now=1.0)
    assert ok is False and bucket == "high_risk"


def test_repeated_hash_detects_stuck_screen():
    assert ratelimit.repeated_hash(["a", "a", "a"]) is True
    assert ratelimit.repeated_hash(["a", "b", "a"]) is False
    assert ratelimit.repeated_hash(["a", "a"]) is False
