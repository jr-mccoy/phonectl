import pytest

from phonectl import errors
from phonectl.macro import schema


def test_validate_ok_minimal():
    assert schema.validate({"name": "m", "actions": [{"type": "tap", "target": {"i": 0}}]}) == []


def test_validate_missing_name():
    errs = schema.validate({"actions": []})
    assert any("name" in e for e in errs)


def test_validate_unknown_top_level_key():
    errs = schema.validate({"name": "m", "actions": [], "bogus": 1})
    assert any("bogus" in e for e in errs)


def test_validate_unknown_step_type():
    errs = schema.validate({"name": "m", "actions": [{"type": "frobnicate"}]})
    assert any("frobnicate" in e for e in errs)


def test_validate_if_requires_condition_and_then():
    errs = schema.validate({"name": "m", "actions": [{"type": "if"}]})
    assert any("condition" in e for e in errs) and any("then" in e for e in errs)


def test_validate_recurses_into_nested_steps():
    errs = schema.validate({"name": "m", "actions": [
        {"type": "if", "condition": {"type": "device_unlocked"},
         "then": [{"type": "nope"}]}]})
    assert any("nope" in e for e in errs)


def test_parse_applies_defaults():
    m = schema.parse({"name": "m", "actions": []})
    assert m.name == "m" and m.version == 1
    assert m.permissions == {} and m.policy == {} and m.limits == {}


def test_parse_raises_on_invalid():
    with pytest.raises(errors.MacroValidationError):
        schema.parse({"actions": []})
