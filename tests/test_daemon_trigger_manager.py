# tests/test_daemon_trigger_manager.py
from phonectl.daemon.triggers import TriggerManager
from phonectl.macro import registry


class FakeEngine:
    def __init__(self):
        self.runs = []

    def run(self, macro, **kw):
        self.runs.append((macro.name, kw.get("trigger")))
        return {"ok": True, "data": {"run_id": "run_x"}}


def _poll_factory(events):
    def poll(since, max_):
        batch = [e for e in events if e["seq"] > since]
        cursor = max([e["seq"] for e in batch], default=since)
        return {"events": batch, "cursor": cursor}
    return poll


def _ev(seq, type_, **data):
    return {"seq": seq, "type": type_, "ts": 0.0, "source": "x", "data": data}


def test_matching_event_fires_macro(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    registry.enable({"name": "reply", "trigger": {"type": "notification.posted",
                     "filters": {"package_in": ["com.x"]}},
                     "actions": [{"type": "tap", "target": {"i": 0}}]})
    eng = FakeEngine()
    mgr = TriggerManager(eng, poll=_poll_factory([_ev(1, "notification.posted", package="com.x")]),
                         now=lambda: 100.0)
    fired = mgr.step()
    assert fired == ["reply"] and eng.runs == [("reply", "notification.posted")]


def test_non_matching_event_does_not_fire(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    registry.enable({"name": "reply", "trigger": {"type": "notification.posted",
                     "filters": {"package_in": ["com.x"]}}, "actions": []})
    eng = FakeEngine()
    mgr = TriggerManager(eng, poll=_poll_factory([_ev(1, "notification.posted", package="com.other")]),
                         now=lambda: 100.0)
    assert mgr.step() == [] and eng.runs == []


def test_cooldown_suppresses_second_fire(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    registry.enable({"name": "reply", "trigger": {"type": "clipboard.changed"},
                     "limits": {"cooldown_seconds": 300},
                     "actions": [{"type": "tap", "target": {"i": 0}}]})
    eng = FakeEngine()
    events = [_ev(1, "clipboard.changed"), _ev(2, "clipboard.changed")]
    mgr = TriggerManager(eng, poll=_poll_factory(events), now=lambda: 100.0)
    fired = mgr.step()
    assert fired == ["reply"]  # second event within cooldown is suppressed


def test_real_bus_notification_envelope_fires(tmp_path, monkeypatch):
    """Real bus envelopes use underscore names; the daemon must normalize to dotted names."""
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    registry.enable({"name": "wa_alert", "trigger": {"type": "notification.posted",
                     "filters": {"package_in": ["com.whatsapp"]}},
                     "actions": [{"type": "tap", "target": {"i": 0}}]})
    eng = FakeEngine()
    # Real bus envelope — type is "notification_posted" (underscore), not dotted
    real_event = {
        "seq": 1, "type": "notification_posted", "ts": 0.0,
        "source": "notifications",
        "data": {"package": "com.whatsapp", "title": "Mom", "text": "call me"},
    }
    mgr = TriggerManager(eng, poll=_poll_factory([real_event]), now=lambda: 100.0)
    fired = mgr.step()
    assert fired == ["wa_alert"], "macro must fire on real bus notification_posted envelope"
    assert eng.runs == [("wa_alert", "notification.posted")]


def test_conditions_gate_suppresses_fire(tmp_path, monkeypatch):
    """A macro whose conditions do not hold must not fire even when the trigger matches."""
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    registry.enable({"name": "gated", "trigger": {"type": "notification.posted"},
                     "conditions": [{"type": "never"}],
                     "actions": [{"type": "tap", "target": {"i": 0}}]})
    eng = FakeEngine()
    # Real bus envelope that would match the trigger after normalization
    real_event = {
        "seq": 1, "type": "notification_posted", "ts": 0.0,
        "source": "notifications",
        "data": {"package": "com.example", "title": "hi", "text": "there"},
    }
    mgr = TriggerManager(eng, poll=_poll_factory([real_event]), now=lambda: 100.0)
    fired = mgr.step()
    assert fired == [], "conditions=never must suppress the macro"
    assert eng.runs == [], "engine.run must not be called when conditions gate"
