# tests/test_macro_registry.py
import pytest

from droidjig import errors
from droidjig.macro import registry


def _doc(name="m"):
    return {"name": name, "trigger": {"type": "notification.posted",
            "filters": {"package_in": ["com.x"]}},
            "actions": [{"type": "tap", "target": {"i": 0}}]}


def test_enable_then_list(tmp_path, monkeypatch):
    monkeypatch.setenv("DROIDJIG_HOME", str(tmp_path))
    registry.enable(_doc("a"))
    names = [m.name for m in registry.list_enabled()]
    assert names == ["a"]


def test_disable_removes_from_enabled(tmp_path, monkeypatch):
    monkeypatch.setenv("DROIDJIG_HOME", str(tmp_path))
    registry.enable(_doc("a"))
    registry.disable("a")
    assert registry.list_enabled() == []
    assert any(m["name"] == "a" and not m["enabled"] for m in registry.all())


def test_enable_rejects_bad_trigger(tmp_path, monkeypatch):
    monkeypatch.setenv("DROIDJIG_HOME", str(tmp_path))
    bad = {"name": "b", "trigger": {"type": "telepathy"}, "actions": []}
    with pytest.raises(errors.TriggerError):
        registry.enable(bad)


def test_enable_rejects_invalid_schedule(tmp_path, monkeypatch):
    monkeypatch.setenv("DROIDJIG_HOME", str(tmp_path))
    bad = {"name": "s", "trigger": {"type": "schedule.time", "at": "99:99"}, "actions": []}
    with pytest.raises(errors.MacroValidationError):
        registry.enable(bad)
