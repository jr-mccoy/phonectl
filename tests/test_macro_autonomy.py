# tests/test_macro_autonomy.py
from phonectl.macro import autonomy
from phonectl.macro.schema import parse


def _m(name="m", require_confirm=False):
    return parse({"name": name, "actions": [], "policy": {"require_confirm": require_confirm}})


def test_no_grant_defaults_to_confirm():
    assert autonomy.decide(_m(), "high", grants=[], now=10.0) == "confirm"
    assert autonomy.decide(_m(), "low", grants=[], now=10.0) == "confirm"


def test_live_grant_allows_up_to_max_risk():
    grants = [{"macro": "m", "max_risk": "high", "expires_at": None}]
    assert autonomy.decide(_m(), "high", grants, now=10.0) == "allow"
    assert autonomy.decide(_m(), "medium", grants, now=10.0) == "allow"


def test_grant_below_action_risk_still_confirms():
    grants = [{"macro": "m", "max_risk": "medium", "expires_at": None}]
    assert autonomy.decide(_m(), "high", grants, now=10.0) == "confirm"


def test_critical_denied_without_explicit_critical_grant():
    grants = [{"macro": "m", "max_risk": "high", "expires_at": None}]
    assert autonomy.decide(_m(), "critical", grants, now=10.0) == "deny"
    grants_crit = [{"macro": "m", "max_risk": "critical", "expires_at": None}]
    assert autonomy.decide(_m(), "critical", grants_crit, now=10.0) == "confirm"


def test_require_confirm_forces_confirm_even_with_grant():
    grants = [{"macro": "m", "max_risk": "high", "expires_at": None}]
    assert autonomy.decide(_m(require_confirm=True), "high", grants, now=10.0) == "confirm"


def test_live_grants_drops_expired_and_revoked():
    records = [
        {"kind": "grant", "id": "g1", "macro": "m", "max_risk": "high", "expires_at": 5.0},
        {"kind": "grant", "id": "g2", "macro": "n", "max_risk": "high", "expires_at": None},
        {"kind": "revoke", "macro": "n", "revoked_at": 8.0},
    ]
    live = autonomy.live_grants(records, now=10.0)
    assert live == []  # g1 expired, g2 revoked


def test_regrant_after_revoke_is_live():
    records = [
        {"kind": "grant", "id": "g1", "macro": "m", "max_risk": "high", "expires_at": None},
        {"kind": "revoke", "macro": "m", "revoked_at": 5.0},
        {"kind": "grant", "id": "g2", "macro": "m", "max_risk": "high", "expires_at": None},
    ]
    live = autonomy.live_grants(records, now=10.0)
    assert len(live) == 1
    assert live[0]["id"] == "g2"


def test_revoke_by_id_drops_only_that_grant():
    records = [
        {"kind": "grant", "id": "g1", "macro": "m", "max_risk": "high", "expires_at": None},
        {"kind": "grant", "id": "g2", "macro": "m", "max_risk": "high", "expires_at": None},
        {"kind": "revoke", "id": "g1", "revoked_at": 5.0},
    ]
    live = autonomy.live_grants(records, now=10.0)
    assert [g["id"] for g in live] == ["g2"]
