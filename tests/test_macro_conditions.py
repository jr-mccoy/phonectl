# tests/test_macro_conditions.py
import pytest
from datetime import datetime

from droidjig import errors
from droidjig.macro import conditions as C
from droidjig.macro import variables as V


def _ctx(**kw):
    base = {"scopes": V.Scopes(), "snapshot": {}, "device": {}, "now": None}
    base.update(kw)
    return base


def test_foreground_package():
    ctx = _ctx(snapshot={"app": "com.example"})
    assert C.evaluate({"type": "foreground_package", "equals": "com.example"}, ctx) is True
    assert C.evaluate({"type": "foreground_package", "equals": "com.other"}, ctx) is False


def test_battery_min_and_charging():
    ctx = _ctx(device={"battery": 40, "charging": True})
    assert C.evaluate({"type": "battery_min", "percent": 15}, ctx) is True
    assert C.evaluate({"type": "battery_min", "percent": 80}, ctx) is False
    assert C.evaluate({"type": "charging"}, ctx) is True


def test_screen_contains():
    ctx = _ctx(snapshot={"elements": [{"text": "Send code"}, {"text": "Cancel"}]})
    assert C.evaluate({"type": "screen_contains", "text_regex": "send"}, ctx) is True
    assert C.evaluate({"type": "screen_contains", "text_regex": "delete"}, ctx) is False


def test_all_hold():
    ctx = _ctx(snapshot={"app": "com.example"}, device={"battery": 50})
    specs = [{"type": "foreground_package", "equals": "com.example"},
             {"type": "battery_min", "percent": 15}]
    assert C.all_hold(specs, ctx) is True


def test_unknown_condition_raises():
    with pytest.raises(errors.TriggerError):
        C.evaluate({"type": "vibes"}, _ctx())


def test_selector_exists():
    elements = [{"text": "OK", "i": 0}]
    ctx = _ctx(snapshot={"elements": elements})
    assert C.evaluate({"type": "selector_exists", "selector": {"text": "OK"}}, ctx) is True
    assert C.evaluate({"type": "selector_exists", "selector": {"text": "Cancel"}}, ctx) is False


def test_risk_below():
    # benign snapshot: no sensitive elements, no guarded package, tap verb — risk classifies "low"
    ctx = _ctx(snapshot={"app": {"package": "com.example"}, "elements": []})
    spec = {"type": "risk_below", "level": "high", "action": {"verb": "tap", "target": {"i": 0}}}
    assert C.evaluate(spec, ctx) is True


def test_time_window_overnight():
    # 23:30 is inside the 22:00–06:00 overnight window
    night = datetime(2024, 1, 1, 23, 30)
    ctx_night = _ctx()
    ctx_night["now"] = night
    spec = {"type": "time_window", "after": "22:00", "before": "06:00"}
    assert C.evaluate(spec, ctx_night) is True

    # 12:00 is outside the overnight window
    noon = datetime(2024, 1, 1, 12, 0)
    ctx_noon = _ctx()
    ctx_noon["now"] = noon
    assert C.evaluate(spec, ctx_noon) is False
