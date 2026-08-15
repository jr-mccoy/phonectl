from droidjig import policy


def _snap(package="com.x", elements=None):
    return {"app": {"package": package}, "elements": elements or []}


def test_decide_uses_default_policy():
    assert policy.decide("low") == "allow"
    assert policy.decide("high") == "confirm"
    assert policy.decide("critical") == "deny"


def test_decide_respects_override():
    assert policy.decide("high", {"high": "deny"}) == "deny"


def test_decide_unknown_level_is_confirm():
    assert policy.decide("weird") == "confirm"


def test_explain_low_screen_allows():
    out = policy.explain(_snap(elements=[{"text": "Wi-Fi"}]), "tap", {"i": 0}, {})
    assert out["risk_level"] == "low" and out["decision"] == "allow"


def test_explain_payment_denies_with_reasons():
    snap = _snap(elements=[{"text": "Confirm payment"}])
    out = policy.explain(snap, "tap", {"i": 0}, {})
    assert out["risk_level"] == "critical" and out["decision"] == "deny"
    assert any(r["signal"] == "payment_keyword" for r in out["reasons"])
    assert "blocked" in out["recommended_action"]


def test_explain_honors_config_guarded_and_policy():
    snap = _snap(package="com.bank.app", elements=[{"text": "Home"}])
    cfg = {"guarded_packages": ["com.bank"], "risk_policy": {"high": "deny"}}
    out = policy.explain(snap, "tap", {"i": 0}, cfg)
    assert out["decision"] == "deny"
