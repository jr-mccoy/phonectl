from droidjig.daemon.events import EventBus
from droidjig.daemon.poller import EventPoller


class FakeUiSource:
    """Plan-4.1 poll_events shape: returns canned events + a monotonic cursor."""
    def __init__(self, batches):
        self._batches = list(batches)   # list of {"events":[...], "cursor":int}
    def poll_events(self, since=0, *, max_events=50):
        return self._batches.pop(0) if self._batches else {"events": [], "cursor": since}


class FakeNotifSource:
    def __init__(self, lists):
        self._lists = list(lists)       # list of list()-results per tick
    def list(self):
        return self._lists.pop(0) if self._lists else []


def test_drain_publishes_ui_events_as_ui_changed():
    bus = EventBus()
    ui = FakeUiSource([{"events": [{"seq": 1, "type": "window_state_changed", "package": "com.x"}],
                        "cursor": 1}])
    n = EventPoller(bus, ui_source=ui).drain_once()
    assert n == 1
    out = bus.poll(since=0)["events"]
    assert out[0]["type"] == "ui_changed"
    assert out[0]["source"] == "accessibility"
    assert out[0]["data"]["package"] == "com.x"


def test_drain_advances_ui_cursor_between_ticks():
    bus = EventBus()
    ui = FakeUiSource([
        {"events": [{"seq": 1, "package": "a"}], "cursor": 1},
        {"events": [{"seq": 2, "package": "b"}], "cursor": 2},
    ])
    poller = EventPoller(bus, ui_source=ui)
    poller.drain_once()
    poller.drain_once()
    pkgs = [e["data"]["package"] for e in bus.poll(since=0)["events"]]
    assert pkgs == ["a", "b"]


def test_drain_publishes_only_new_notifications():
    bus = EventBus()
    notif = FakeNotifSource([
        [{"key": "k1", "package": "com.msg", "title": "A"}],
        [{"key": "k1", "package": "com.msg", "title": "A"},
         {"key": "k2", "package": "com.msg", "title": "B"}],
    ])
    poller = EventPoller(bus, notif_source=notif)
    assert poller.drain_once() == 1   # k1 new
    assert poller.drain_once() == 1   # only k2 new (k1 already seen)
    events = bus.poll(since=0)["events"]
    assert [e["data"]["key"] for e in events] == ["k1", "k2"]
    assert all(e["type"] == "notification_posted" for e in events)
    assert all(e["source"] == "notifications" for e in events)
