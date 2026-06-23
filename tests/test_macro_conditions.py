# tests/test_macro_conditions.py
import pytest

from phonectl import errors
from phonectl.macro import conditions as C
from phonectl.macro import variables as V


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
