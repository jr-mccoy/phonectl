import json as _json

import pytest

from droidjig.providers import notifications as nmod
from droidjig.providers.notifications import NotificationsProvider
from droidjig.providers.transport import LoopbackTransport

COMPANION_RAW = {
    "key": "0|com.msg|42|tag|10123", "package": "com.msg",
    "title": "Alice", "text": "see you at 6?", "category": "msg", "post_time": 1718900000000,
    "actions": [{"title": "Reply", "remote_input": True}, {"title": "Mark read"}],
}


def test_parse_notification_companion_sets_can_reply():
    n = nmod.parse_notification(COMPANION_RAW, source="companion")
    assert n["key"] == "0|com.msg|42|tag|10123"
    assert n["can_reply"] is True
    assert n["can_dismiss"] is True
    assert "Reply" in n["actions"]


def test_parse_notification_termux_is_read_only():
    raw = {"id": 7, "packageName": "com.msg", "title": "Alice", "content": "hi"}
    n = nmod.parse_notification(raw, source="termux")
    assert n["can_reply"] is False
    assert n["can_dismiss"] is False
    assert n["package"] == "com.msg"


def test_capabilities_full_with_companion():
    p = NotificationsProvider(transport=LoopbackTransport({}))
    caps = p.capabilities()
    assert caps["notifications_reply"] is True
    assert caps["notifications_dismiss"] is True


def test_capabilities_listonly_with_termux():
    class FakeTermux:
        def is_available(self): return True
    p = NotificationsProvider(transport=None, termux=FakeTermux())
    caps = p.capabilities()
    assert caps["observe_notifications"] is True
    assert caps["notifications_reply"] is False


def test_capabilities_empty_when_nothing_available():
    p = NotificationsProvider(transport=LoopbackTransport({}, available=False), termux=None)
    assert all(v is False for v in p.capabilities().values())


def test_list_companion_normalizes_items():
    def handler(_p):
        return {"notifications": [COMPANION_RAW]}
    p = NotificationsProvider(transport=LoopbackTransport({"notifications_list": handler}))
    items = p.list()
    assert len(items) == 1
    assert items[0]["can_reply"] is True
    assert items[0]["package"] == "com.msg"


def test_list_filters_by_package():
    raw2 = dict(COMPANION_RAW, key="k2", package="com.other")
    def handler(_p):
        return {"notifications": [COMPANION_RAW, raw2]}
    p = NotificationsProvider(transport=LoopbackTransport({"notifications_list": handler}))
    assert [n["package"] for n in p.list(package="com.msg")] == ["com.msg"]


def test_list_termux_fallback_is_read_only():
    class FakeTermux:
        def is_available(self): return True
        def notifications_list(self):
            return [{"id": 1, "packageName": "com.msg", "title": "A", "content": "hi"}]
    p = NotificationsProvider(transport=None, termux=FakeTermux())
    items = p.list()
    assert items[0]["can_reply"] is False


def test_wait_returns_first_match(monkeypatch):
    calls = {"n": 0}
    def handler(_p):
        calls["n"] += 1
        if calls["n"] < 3:
            return {"notifications": []}
        return {"notifications": [COMPANION_RAW]}
    p = NotificationsProvider(transport=LoopbackTransport({"notifications_list": handler}))
    t = {"now": 0.0}
    out = p.wait(package="com.msg", timeout=10.0, poll=1.0,
                 _clock=lambda: t["now"], _sleep=lambda s: t.__setitem__("now", t["now"] + s))
    assert out is not None and out["package"] == "com.msg"


def test_wait_times_out_returns_none():
    def handler(_p):
        return {"notifications": []}
    p = NotificationsProvider(transport=LoopbackTransport({"notifications_list": handler}))
    t = {"now": 0.0}
    out = p.wait(text_contains="never", timeout=3.0, poll=1.0,
                 _clock=lambda: t["now"], _sleep=lambda s: t.__setitem__("now", t["now"] + s))
    assert out is None


def test_reply_calls_companion():
    seen = {}
    def handler(p):
        seen.update(p); return {"sent": True}
    p = NotificationsProvider(transport=LoopbackTransport({"notifications_reply": handler}))
    out = p.reply("0|com.msg|42|tag|10123", "on my way")
    assert out["sent"] is True
    assert seen == {"key": "0|com.msg|42|tag|10123", "text": "on my way"}


def test_reply_unavailable_without_companion():
    class FakeTermux:
        def is_available(self): return True
        def notifications_list(self): return []
    p = NotificationsProvider(transport=None, termux=FakeTermux())
    with pytest.raises(Exception):
        p.reply("k", "hi")


def test_dismiss_calls_companion():
    seen = {}
    def handler(p):
        seen.update(p); return {"dismissed": True}
    p = NotificationsProvider(transport=LoopbackTransport({"notifications_dismiss": handler}))
    assert p.dismiss("k1")["dismissed"] is True
    assert seen == {"key": "k1"}
