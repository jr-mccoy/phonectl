import pytest
from phonectl.providers.registry import ProviderRegistry


class FakeProv:
    def __init__(self, name, caps):
        self._name = name
        self._caps = caps

    def capabilities(self):
        return self._caps


def _caps(**kw):
    from phonectl import capabilities
    return capabilities.make(**kw)


def test_capabilities_merged():
    a = FakeProv("A", _caps(act_tap=True, requires_adb=True))
    b = FakeProv("B", _caps(read_clipboard=True))
    r = ProviderRegistry([a, b])
    merged = r.capabilities()
    assert merged["act_tap"] is True
    assert merged["read_clipboard"] is True
    assert merged["observe_ui_tree"] is False


def test_for_capability_returns_first_matching():
    a = FakeProv("A", _caps(act_tap=True))
    b = FakeProv("B", _caps(act_tap=True))
    r = ProviderRegistry([a, b])
    assert r.for_capability("act_tap") is a


def test_for_capability_returns_none_when_no_match():
    a = FakeProv("A", _caps(act_tap=True))
    r = ProviderRegistry([a])
    assert r.for_capability("read_clipboard") is None


def test_capabilities_by_provider_shape():
    a = FakeProv("A", _caps(act_tap=True))
    r = ProviderRegistry([a])
    items = r.capabilities_by_provider()
    assert len(items) == 1
    assert items[0]["provider"] == "FakeProv"
    assert items[0]["caps"]["act_tap"] is True


def test_empty_registry_has_all_false_capabilities():
    r = ProviderRegistry([])
    assert all(v is False for v in r.capabilities().values())


# Task 2 tests — Backend Protocol delegation

from phonectl import errors


class FakeAdbProv:
    serial = "fake:5555"
    _tapped = None

    def capabilities(self):
        return _caps(act_tap=True, observe_ui_tree=True, requires_adb=True,
                     launch_app=True, act_type=True, act_key=True,
                     observe_screenshot=True)

    def ui_dump(self):
        return "<hierarchy></hierarchy>"

    def window_dump(self):
        return ""

    def wm_size(self):
        return (1080, 2400)

    def screencap(self, path):
        return path

    def input_tap(self, x, y):
        FakeAdbProv._tapped = (x, y)

    def input_text(self, text):
        pass

    def input_swipe(self, x1, y1, x2, y2, ms=200):
        pass

    def input_named_swipe(self, direction, distance_pct=0.5, ms=400):
        FakeAdbProv._named_swiped = (direction, distance_pct, ms)

    def input_long_press(self, x, y, duration_ms=1000):
        FakeAdbProv._long_pressed = (x, y, duration_ms)

    def input_fling(self, direction, velocity=2000):
        FakeAdbProv._flung = (direction, velocity)

    def input_key(self, keycode):
        pass

    def launch(self, package):
        pass

    def get_state(self):
        return "device"

    def wake(self):
        pass


def test_delegation_tap_sets_last_used():
    prov = FakeAdbProv()
    r = ProviderRegistry([prov])
    r.input_tap(100, 200)
    assert r.last_used == "FakeAdbProv"
    assert FakeAdbProv._tapped == (100, 200)


def test_delegation_serial_property():
    prov = FakeAdbProv()
    r = ProviderRegistry([prov])
    assert r.serial == "fake:5555"


def test_require_raises_capability_unavailable_when_no_provider():
    r = ProviderRegistry([])
    with pytest.raises(errors.CapabilityUnavailableError) as exc:
        r.input_tap(0, 0)
    assert "act_tap" in str(exc.value)


def test_getattr_delegates_adb_specific_helpers():
    prov = FakeAdbProv()
    r = ProviderRegistry([prov])
    r.wake()   # delegates via __getattr__ to FakeAdbProv.wake


def test_getattr_raises_when_no_adb_provider():
    r = ProviderRegistry([])
    with pytest.raises(AttributeError):
        r.wake()


# --- Finding 13: runtime fallback to the next capable provider ---

class BrokenProv:
    """Advertises capabilities but fails at call time (companion crashed mid-request)."""

    def __init__(self, exc=None):
        self._exc = exc or errors.ObserveError("companion died mid-request")

    def capabilities(self):
        return _caps(act_tap=True, observe_ui_tree=True)

    def ui_dump(self):
        raise self._exc

    def input_tap(self, x, y):
        raise self._exc


def test_action_falls_back_when_first_provider_raises():
    broken, adb = BrokenProv(), FakeAdbProv()
    FakeAdbProv._tapped = None
    r = ProviderRegistry([broken, adb])
    r.input_tap(3, 4)
    assert FakeAdbProv._tapped == (3, 4)
    # The provider field must report who actually served the call, not who was asked first.
    assert r.last_used == "FakeAdbProv"


def test_fallback_is_recorded_for_audit():
    r = ProviderRegistry([BrokenProv(), FakeAdbProv()])
    r.ui_dump()
    assert r.last_fallback == [
        {"provider": "BrokenProv", "error": "companion died mid-request"}
    ]
    # A call that succeeds first try leaves no fallback record.
    clean = ProviderRegistry([FakeAdbProv()])
    clean.ui_dump()
    assert clean.last_fallback == []


def test_all_capable_providers_failing_reraises_last_error():
    r = ProviderRegistry([BrokenProv(), BrokenProv()])
    with pytest.raises(errors.ObserveError):
        r.ui_dump()


def test_policy_refusals_do_not_fall_back():
    # A guarded/stopped refusal is the companion enforcing policy, not a provider failure —
    # falling through to ADB would BYPASS the protection. It must propagate.
    for exc in (errors.GuardedActionError("guarded"), errors.StoppedError("stopped")):
        refusing = BrokenProv(exc=exc)
        adb = FakeAdbProv()
        FakeAdbProv._tapped = None
        r = ProviderRegistry([refusing, adb])
        with pytest.raises(type(exc)):
            r.input_tap(1, 1)
        assert FakeAdbProv._tapped is None  # ADB was never consulted


def test_no_capable_provider_still_raises_capability_unavailable():
    r = ProviderRegistry([FakeProv("A", _caps(read_clipboard=True))])
    with pytest.raises(errors.CapabilityUnavailableError):
        r.input_tap(0, 0)


# ── companion-first gestures: long-press / named swipe / fling delegate, not __getattr__ ──

class GestureProv(FakeProv):
    """Companion-style provider that serves the full gesture surface."""

    def __init__(self):
        super().__init__("Gesture", _caps(act_tap=True))
        self.calls = []

    def input_named_swipe(self, direction, distance_pct=0.5, ms=400):
        self.calls.append(("named_swipe", direction, distance_pct, ms))

    def input_long_press(self, x, y, duration_ms=1000):
        self.calls.append(("long_press", x, y, duration_ms))

    def input_fling(self, direction, velocity=2000):
        self.calls.append(("fling", direction, velocity))


def test_gesture_verbs_delegate_to_priority_act_tap_provider():
    # These previously slipped through __getattr__ to the ADB provider even when a
    # higher-priority companion could serve them.
    companion, adb = GestureProv(), FakeAdbProv()
    FakeAdbProv._named_swiped = FakeAdbProv._long_pressed = FakeAdbProv._flung = None
    r = ProviderRegistry([companion, adb])
    r.input_long_press(10, 20, 800)
    r.input_named_swipe("up", 0.4, 350)
    r.input_fling("down", 1500)
    assert companion.calls == [("long_press", 10, 20, 800),
                               ("named_swipe", "up", 0.4, 350),
                               ("fling", "down", 1500)]
    assert FakeAdbProv._named_swiped is None
    assert FakeAdbProv._long_pressed is None
    assert FakeAdbProv._flung is None
    assert r.last_used == "GestureProv"


def test_unsupported_keycode_falls_to_adb_without_a_companion_rpc():
    from phonectl.providers.accessibility import AccessibilityProvider
    from phonectl.providers.transport import LoopbackTransport

    class RecordingTransport(LoopbackTransport):
        def __init__(self):
            self.sent = []
            super().__init__({"key": lambda p: {"applied": True}})

        def request(self, method, params, *, request_id, timeout):
            self.sent.append(method)
            return super().request(method, params, request_id=request_id, timeout=timeout)

    t = RecordingTransport()
    adb = FakeAdbProv()
    r = ProviderRegistry([AccessibilityProvider(t), adb])
    r.input_key("KEYCODE_ENTER")
    assert "key" not in t.sent          # pre-flight refused locally, no socket round trip
    assert r.last_used == "FakeAdbProv"


def test_gesture_verbs_fall_back_to_adb_on_runtime_failure():
    class DyingGestureProv(GestureProv):
        def input_long_press(self, x, y, duration_ms=1000):
            raise errors.ObserveError("companion died mid-request")

    FakeAdbProv._long_pressed = None
    r = ProviderRegistry([DyingGestureProv(), FakeAdbProv()])
    r.input_long_press(1, 2, 700)
    assert FakeAdbProv._long_pressed == (1, 2, 700)
    assert r.last_used == "FakeAdbProv"
    assert r.last_fallback and r.last_fallback[0]["provider"] == "DyingGestureProv"


# ── observe_dump delegation: combined when the provider has it, split when not ──

class CombinedProv(FakeProv):
    def __init__(self, name="Combined"):
        super().__init__(name, _caps(observe_ui_tree=True))
        self.combined_calls = 0

    def observe_dump(self):
        self.combined_calls += 1
        return "<hierarchy/>", "mCurrentFocus=x"


class SplitProv(FakeProv):
    def __init__(self, name="Split"):
        super().__init__(name, _caps(observe_ui_tree=True))
        self.ui_calls = 0
        self.window_calls = 0

    def ui_dump(self):
        self.ui_calls += 1
        return "<hierarchy split/>"

    def window_dump(self):
        self.window_calls += 1
        return "mCurrentFocus=split"


def test_observe_dump_uses_provider_combined_form():
    p = CombinedProv()
    r = ProviderRegistry([p])
    assert r.observe_dump() == ("<hierarchy/>", "mCurrentFocus=x")
    assert p.combined_calls == 1
    assert r.last_used == "CombinedProv"


def test_observe_dump_splits_for_providers_without_combined_form():
    # A companion/native provider serves observe_ui_tree without observe_dump:
    # the registry must stay on THAT provider (its tree wins by priority), not
    # fall through to a lower-priority combined-capable one.
    split, combined = SplitProv(), CombinedProv()
    r = ProviderRegistry([split, combined])
    assert r.observe_dump() == ("<hierarchy split/>", "mCurrentFocus=split")
    assert split.ui_calls == 1 and split.window_calls == 1
    assert combined.combined_calls == 0
    assert r.last_used == "SplitProv"


def test_observe_dump_falls_back_on_runtime_failure():
    class DyingProv(FakeProv):
        def __init__(self):
            super().__init__("Dying", _caps(observe_ui_tree=True))
        def ui_dump(self):
            raise RuntimeError("companion died")
    dying, combined = DyingProv(), CombinedProv()
    r = ProviderRegistry([dying, combined])
    assert r.observe_dump() == ("<hierarchy/>", "mCurrentFocus=x")
    assert r.last_used == "CombinedProv"
    assert r.last_fallback and r.last_fallback[0]["provider"] == "DyingProv"


def test_observe_dump_no_provider_raises_capability_unavailable():
    r = ProviderRegistry([FakeProv("A", _caps(act_tap=True))])
    with pytest.raises(errors.CapabilityUnavailableError):
        r.observe_dump()


# ── window augmentation: a tree provider with no window view gets ADB's ──────

class NativeTreeProv(FakeProv):
    """Companion-style provider: native tree, no keyguard/focus knowledge."""
    def __init__(self):
        super().__init__("Native", _caps(observe_ui_tree=True))

    def observe_dump(self):
        return "<hierarchy native/>", None

    def ui_dump(self):
        return "<hierarchy native/>"

    def window_dump(self):
        return ""


class AdbLikeProv(FakeProv):
    def __init__(self):
        super().__init__("AdbLike", _caps(requires_adb=True, act_tap=True))
        self.brief_calls = 0
        self.full_calls = 0

    def window_brief(self):
        self.brief_calls += 1
        return "mCurrentFocus=Window{a b com.x/.Y}\nmDreamingLockscreen=false"

    def window_dump(self):
        self.full_calls += 1
        return "FULL DUMP mCurrentFocus=Window{a b com.x/.Y}"


def test_observe_dump_augments_missing_window_from_adb_brief():
    native, adb = NativeTreeProv(), AdbLikeProv()
    r = ProviderRegistry([native, adb])
    xml, window = r.observe_dump()
    assert xml == "<hierarchy native/>"
    assert "mCurrentFocus" in window       # lock/focus truth still reaches policy
    assert adb.brief_calls == 1
    assert adb.full_calls == 0             # the cheap filtered form was enough
    assert r.last_used == "NativeTreeProv" # the tree provider still gets credit


def test_window_dump_augments_empty_result_from_adb():
    native, adb = NativeTreeProv(), AdbLikeProv()
    r = ProviderRegistry([native, adb])
    out = r.window_dump()
    assert "mCurrentFocus" in out


def test_observe_dump_no_adb_leaves_window_none():
    native = NativeTreeProv()
    r = ProviderRegistry([native])
    xml, window = r.observe_dump()
    assert xml == "<hierarchy native/>"
    assert window is None                  # nothing to augment from; caller decides


def test_observe_dump_adb_failure_does_not_kill_the_observation():
    class DeadAdb(AdbLikeProv):
        def window_brief(self):
            raise RuntimeError("adb offline")
        def window_dump(self):
            raise RuntimeError("adb offline")
    native, adb = NativeTreeProv(), DeadAdb()
    r = ProviderRegistry([native, adb])
    xml, window = r.observe_dump()
    assert xml == "<hierarchy native/>"    # companion observation survives
    assert window is None
