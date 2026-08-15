from droidjig import cli
from droidjig.macro import variables as V


def test_fn_for_tap_index_resolves_via_actuator(monkeypatch):
    calls = {}
    import droidjig.actuator as actuator
    monkeypatch.setattr(actuator, "tap", lambda b, s, **kw: calls.setdefault("tap", kw) or {"hash": "h"})
    fn = cli.macro_fn_for({"type": "tap", "target": {"i": 4}}, V.Scopes())
    fn("BACKEND", "SESSION")
    assert calls["tap"]["i"] == 4


def test_fn_for_unknown_verb_raises():
    import pytest
    from droidjig import errors
    with pytest.raises(errors.MacroValidationError):
        cli.macro_fn_for({"type": "frobnicate"}, V.Scopes())
