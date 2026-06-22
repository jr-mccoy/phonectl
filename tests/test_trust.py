from phonectl import trust
from phonectl.providers.transport import LoopbackTransport


def test_negotiate_returns_capabilities_and_version():
    t = LoopbackTransport({"handshake": lambda p: {
        "version": 2, "capabilities": {"act_gesture_native": True, "act_set_text_native": False},
        "stopped": False}})
    hs = trust.negotiate(t)
    assert hs.reachable is True
    assert hs.version == 2
    assert hs.capabilities["act_gesture_native"] is True
    assert hs.stopped is False


def test_negotiate_unreachable_is_safe_default():
    t = LoopbackTransport({}, available=False)
    hs = trust.negotiate(t)
    assert hs.reachable is False
    assert hs.capabilities == {}


def test_companion_stopped_true():
    t = LoopbackTransport({"handshake": lambda p: {
        "version": 1, "capabilities": {}, "stopped": True}})
    assert trust.companion_stopped(t) is True
