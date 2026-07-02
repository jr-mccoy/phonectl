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


from phonectl import capabilities


def test_gate_capabilities_removes_disabled():
    adv = capabilities.make(act_gesture_native=True, act_set_text_native=True)
    enabled = {"act_set_text_native": False}
    gated = trust.gate_capabilities(adv, enabled)
    assert gated["act_gesture_native"] is True
    assert gated["act_set_text_native"] is False


def test_gate_capabilities_absent_toggle_defaults_enabled():
    adv = capabilities.make(act_gesture_native=True)
    assert trust.gate_capabilities(adv, {})["act_gesture_native"] is True


def test_gated_provider_filters_and_delegates():
    class Inner:
        def capabilities(self):
            return capabilities.make(act_gesture_native=True, act_set_text_native=True)
        def semantic_action(self, *a):
            return {"performed": a}
    g = trust.GatedProvider(Inner(), {"act_set_text_native": False})
    assert g.capabilities()["act_set_text_native"] is False
    assert g.capabilities()["act_gesture_native"] is True
    assert g.semantic_action("n", "click")["performed"] == ("n", "click")


def test_companion_stopped_failclosed_when_unreachable():
    # Finding 8: a companion that is configured but unreachable at the moment
    # the STOP check runs must be read as stopped, not silently "not stopped".
    t = LoopbackTransport({}, available=False)
    assert trust.companion_stopped(t) is True


def test_companion_stopped_failclosed_when_handshake_raises():
    class ExplodingTransport:
        def request(self, *a, **kw):
            raise OSError("connection reset")

    assert trust.companion_stopped(ExplodingTransport()) is True


def test_companion_stopped_false_when_reachable_and_running():
    t = LoopbackTransport({"handshake": lambda p: {
        "version": 1, "capabilities": {}, "stopped": False}})
    assert trust.companion_stopped(t) is False
