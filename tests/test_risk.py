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
