from phonectl import risk


def _snap(package="com.x", elements=None):
    return {"app": {"package": package}, "elements": elements or []}


def test_benign_screen_is_low():
    snap = _snap(elements=[{"text": "Wi-Fi", "content_desc": "", "password": False}])
    out = risk.classify(snap, "tap", {"i": 0})
    assert out["level"] == "low" and out["reasons"] == []


def test_guarded_package_is_high():
    snap = _snap(
        package="com.android.vending",
        elements=[{"text": "Buy", "content_desc": "", "password": False}],
    )
    out = risk.classify(
        snap, "tap", {"i": 0}, guarded_packages=["com.android.vending"]
    )
    signals = {r["signal"] for r in out["reasons"]}
    assert "guarded_package" in signals
    assert out["level"] == "critical"


def test_password_field_is_high():
    snap = _snap(elements=[{"text": "", "content_desc": "Password", "password": True}])
    out = risk.classify(snap, "type", {"text": "<x>"})
    assert out["level"] == "high"
    assert {r["signal"] for r in out["reasons"]} == {"password_field"}


def test_payment_keyword_is_critical():
    snap = _snap(
        elements=[
            {"text": "Confirm payment of $42", "content_desc": "", "password": False}
        ]
    )
    out = risk.classify(snap, "tap", {"i": 3})
    assert out["level"] == "critical"
    assert any(r["signal"] == "payment_keyword" for r in out["reasons"])


def test_destructive_keyword_is_critical():
    snap = _snap(elements=[{"text": "Factory reset", "content_desc": "", "password": False}])
    assert risk.classify(snap, "tap", {"i": 1})["level"] == "critical"


def test_otp_like_content_is_medium():
    snap = _snap(
        elements=[{"text": "Your code is 482913", "content_desc": "", "password": False}]
    )
    out = risk.classify(snap, "tap", {"i": 0})
    assert out["level"] == "medium"
    assert any(r["signal"] == "otp_like_content" for r in out["reasons"])


def test_packages_clear_classifies_critical():
    snap = {"app": {}, "elements": []}
    result = risk.classify(snap, "packages_clear", "com.example")
    assert result["level"] == "critical"
    assert any(r["signal"] == "critical_verb" for r in result["reasons"])


def test_packages_stop_classifies_high():
    snap = {"app": {}, "elements": []}
    result = risk.classify(snap, "packages_stop", "com.example")
    assert result["level"] == "high"
    assert any(r["signal"] == "high_risk_verb" for r in result["reasons"])


def test_intent_broadcast_classifies_high():
    snap = {"app": {}, "elements": []}
    result = risk.classify(snap, "intent_broadcast", "com.example.ACTION")
    assert result["level"] == "high"


def test_notifications_reply_is_high_risk():
    from phonectl import risk
    assert "notifications_reply" in risk.HIGH_RISK_VERBS


def test_intent_start_is_high_risk():
    assert "intent_start" in risk.HIGH_RISK_VERBS
    snap = {"app": {}, "elements": []}
    result = risk.classify(snap, "intent_start", "com.android.settings/.wifi.WifiSettings")
    assert result["level"] == "high"
    assert any(r["signal"] == "high_risk_verb" for r in result["reasons"])


def test_intent_start_tel_is_critical():
    snap = {"app": {}, "elements": []}
    result = risk.classify(snap, "intent_start", "tel:+15551234567")
    assert result["level"] == "critical"
    assert any(r["signal"] == "critical_intent" for r in result["reasons"])


def test_intent_start_call_action_is_critical():
    snap = {"app": {}, "elements": []}
    result = risk.classify(snap, "intent_start", "android.intent.action.CALL")
    assert result["level"] == "critical"
    assert any(r["signal"] == "critical_intent" for r in result["reasons"])


def test_intent_start_sms_is_critical():
    snap = {"app": {}, "elements": []}
    for target in ("sms:5551234", "smsto:5551234", "mmsto:5551234"):
        result = risk.classify(snap, "intent_start", target)
        assert result["level"] == "critical", target
        assert any(r["signal"] == "critical_intent" for r in result["reasons"]), target


def test_intent_broadcast_call_scheme_is_critical():
    snap = {"app": {}, "elements": []}
    result = risk.classify(snap, "intent_broadcast", "tel:911")
    assert result["level"] == "critical"


def test_benign_intent_start_stays_high_not_critical():
    snap = {"app": {}, "elements": []}
    result = risk.classify(snap, "intent_start", "geo:37.422,-122.084")
    assert result["level"] == "high"
    assert not any(r["signal"] == "critical_intent" for r in result["reasons"])


def test_install_keyword_trimmed_no_confirmation_fatigue():
    # "allow"/"grant"/"send"/"subscribe" no longer force a high-risk classification.
    for word in ("Allow", "Grant access", "Send", "Subscribe now"):
        snap = _snap(elements=[{"text": word, "content_desc": "", "password": False}])
        out = risk.classify(snap, "tap", {"i": 0})
        assert out["level"] == "low", word
    # a genuine install prompt still classifies high.
    snap = _snap(elements=[{"text": "Install this app?", "content_desc": "", "password": False}])
    assert risk.classify(snap, "tap", {"i": 0})["level"] == "high"


def test_verb_risk_matrix():
    snap = {"app": {}, "elements": []}
    expected = {
        "tap": "low",
        "type": "low",
        "swipe": "low",
        "key": "low",
        "launch": "low",
        "intent_start": "high",
        "intent_broadcast": "high",
        "packages_stop": "high",
        "notifications_reply": "high",
        "packages_clear": "critical",
    }
    for verb, level in expected.items():
        assert risk.classify(snap, verb, "com.example")["level"] == level, verb
