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
    enabled = {"act_gesture_native": True, "act_set_text_native": False}
    gated = trust.gate_capabilities(adv, enabled)
    assert gated["act_gesture_native"] is True
    assert gated["act_set_text_native"] is False


def test_gate_capabilities_absent_toggle_defaults_disabled():
    # Finding 5: a capability without an explicit enable is off, not on.
    adv = capabilities.make(act_gesture_native=True)
    assert trust.gate_capabilities(adv, {})["act_gesture_native"] is False


def test_gated_provider_filters_and_delegates():
    class Inner:
        def capabilities(self):
            return capabilities.make(act_gesture_native=True, act_set_text_native=True)
        def semantic_action(self, *a):
            return {"performed": a}
    g = trust.GatedProvider(Inner(), {"act_gesture_native": True, "act_set_text_native": False})
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


# ── derived capabilities: the Backend-protocol aliases ride their native toggles ──

def test_gate_capabilities_derives_protocol_keys_from_native_toggles():
    # The APK handshake carries only the native keys the user toggles. The provider's
    # Backend-protocol equivalents (observe_ui_tree, act_tap, act_key, act_type) are the
    # SAME surfaces under the registry's names — gating them off as "unknown" (Finding 5)
    # silently pushed every observe/tap/type/key back to ADB despite a live companion.
    adv = capabilities.make(observe_ui_native=True, observe_ui_tree=True,
                            act_gesture_native=True, act_tap=True, act_key=True,
                            act_set_text_native=True, act_type=True)
    enabled = {"observe_ui_native": True, "act_gesture_native": True,
               "act_set_text_native": True}
    gated = trust.gate_capabilities(adv, enabled)
    assert gated["observe_ui_tree"] is True
    assert gated["act_tap"] is True
    assert gated["act_key"] is True
    assert gated["act_type"] is True


def test_gate_capabilities_derived_keys_follow_a_disabled_source():
    # Mirrors the companion's own METHOD_CAPABILITY: disabling "Perform touch gestures"
    # refuses gesture AND key on-device — the Python side must drop act_tap/act_key too.
    adv = capabilities.make(act_gesture_native=True, act_tap=True, act_key=True)
    gated = trust.gate_capabilities(adv, {"act_gesture_native": False})
    assert gated["act_tap"] is False
    assert gated["act_key"] is False


def test_gate_capabilities_explicit_derived_key_wins_over_derivation():
    # A future APK that names the derived key explicitly is authoritative.
    adv = capabilities.make(act_gesture_native=True, act_tap=True)
    gated = trust.gate_capabilities(adv, {"act_gesture_native": True, "act_tap": False})
    assert gated["act_tap"] is False


def test_gated_accessibility_provider_serves_the_tree_with_a_real_handshake():
    # End to end: with exactly the keys a real APK handshake advertises, the gated
    # provider must still advertise the registry-facing tree/gesture surface.
    from phonectl.providers.accessibility import AccessibilityProvider
    enabled = {k: True for k in (
        "observe_ui_native", "observe_ui_events", "act_gesture_native",
        "act_set_text_native", "act_semantic_action", "launch_app",
        "observe_screenshot")}
    g = trust.GatedProvider(AccessibilityProvider(LoopbackTransport({})), enabled)
    caps = g.capabilities()
    for key in ("observe_ui_tree", "act_tap", "act_type", "act_key",
                "observe_ui_native", "act_semantic_action", "launch_app",
                "observe_screenshot"):
        assert caps[key] is True, key
