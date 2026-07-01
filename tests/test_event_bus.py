import itertools

import pytest

from phonectl.daemon.events import EventBus


def _clock():
    c = itertools.count(1000)
    return lambda: float(next(c))


def test_publish_assigns_monotonic_seq_and_envelope():
    bus = EventBus(now=_clock())
    e1 = bus.publish("ui_changed", {"package": "com.x"}, source="accessibility")
    e2 = bus.publish("lifecycle", {"state": "started"}, source="daemon")
    assert e1["seq"] == 1 and e2["seq"] == 2
    assert e1["type"] == "ui_changed" and e1["source"] == "accessibility"
    assert e1["ts"] == 1000.0
    assert set(e1) == {"seq", "type", "ts", "source", "data"}


def test_publish_rejects_unknown_type():
    bus = EventBus()
    with pytest.raises(ValueError):
        bus.publish("teleported", {}, source="x")
    assert bus.latest_seq == 0  # log untouched


def test_poll_filters_by_since_and_advances_cursor():
    bus = EventBus(now=_clock())
    for _ in range(3):
        bus.publish("ui_changed", {}, source="accessibility")
    out = bus.poll(since=1)
    assert [e["seq"] for e in out["events"]] == [2, 3]
    assert out["cursor"] == 3


def test_poll_respects_max_and_empty_cursor():
    bus = EventBus(now=_clock())
    for _ in range(5):
        bus.publish("ui_changed", {}, source="accessibility")
    out = bus.poll(since=0, max=2)
    assert [e["seq"] for e in out["events"]] == [1, 2]
    assert out["cursor"] == 2
    tail = bus.poll(since=5)
    assert tail["events"] == [] and tail["cursor"] == 5


def test_wait_returns_immediate_event():
    bus = EventBus(now=_clock())
    bus.publish("ui_changed", {"package": "a"}, source="accessibility")
    out = bus.wait(since=0, timeout_ms=100)
    assert len(out["events"]) == 1
    assert out["cursor"] == 1


def test_wait_times_out_with_no_events():
    bus = EventBus()
    out = bus.wait(since=3, timeout_ms=1)
    assert out == {"events": [], "cursor": 3}


def test_wait_advances_cursor():
    bus = EventBus(now=_clock())
    bus.publish("ui_changed", {"package": "a"}, source="accessibility")
    first = bus.wait(since=0, timeout_ms=0)
    bus.publish("ui_changed", {"package": "b"}, source="accessibility")
    second = bus.wait(since=first["cursor"], timeout_ms=0)
    assert [e["data"]["package"] for e in second["events"]] == ["b"]
    assert second["cursor"] == 2
