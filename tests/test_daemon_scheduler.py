"""Tests for daemon Scheduler: arms and fires scheduled macros."""
from datetime import datetime

from droidjig.daemon.triggers import Scheduler
from droidjig.macro import registry


class FakeEngine:
    def __init__(self):
        self.runs = []

    def run(self, macro, **kw):
        self.runs.append(macro.name)
        return {"ok": True, "data": {"run_id": "r"}}


def test_due_fires_interval_macro(tmp_path, monkeypatch):
    monkeypatch.setenv("DROIDJIG_HOME", str(tmp_path))
    registry.enable({"name": "tick", "trigger": {"type": "schedule.interval", "every_seconds": 0.0001},
                     "actions": [{"type": "tap", "target": {"i": 0}}]})
    eng = FakeEngine()
    sch = Scheduler(eng)
    # First due() arms; after the tiny interval the macro is due.
    sch.due(datetime(2026, 6, 22, 12, 0, 0))
    fired = sch.due(datetime(2026, 6, 22, 12, 0, 1))
    assert "tick" in fired and "tick" in eng.runs


def test_due_returns_empty_when_no_scheduled_macros(tmp_path, monkeypatch):
    monkeypatch.setenv("DROIDJIG_HOME", str(tmp_path))
    registry.enable({"name": "notick", "trigger": {"type": "clipboard.changed"},
                     "actions": [{"type": "tap", "target": {"i": 0}}]})
    eng = FakeEngine()
    sch = Scheduler(eng)
    fired = sch.due(datetime(2026, 6, 22, 12, 0, 0))
    assert fired == []
    assert eng.runs == []


def test_due_no_fire_before_interval(tmp_path, monkeypatch):
    monkeypatch.setenv("DROIDJIG_HOME", str(tmp_path))
    registry.enable({"name": "slow", "trigger": {"type": "schedule.interval", "every_seconds": 3600},
                     "actions": [{"type": "tap", "target": {"i": 0}}]})
    eng = FakeEngine()
    sch = Scheduler(eng)
    # Arms on first call
    sch.due(datetime(2026, 6, 22, 12, 0, 0))
    # Only 1 second later — well before 3600s
    fired = sch.due(datetime(2026, 6, 22, 12, 0, 1))
    assert "slow" not in fired
    assert eng.runs == []
