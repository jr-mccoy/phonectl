import pytest
from droidjig import errors, ui_parser
from droidjig.providers.accessibility import AccessibilityProvider
from droidjig.providers.transport import LoopbackTransport


# --- Task 2: capabilities ---

def test_capabilities_all_relevant_true_when_available():
    p = AccessibilityProvider(LoopbackTransport({}))
    caps = p.capabilities()
    for key in ("observe_ui_native", "observe_ui_events", "act_set_text_native",
                "act_gesture_native", "act_semantic_action",
                "observe_ui_tree", "act_tap", "act_type", "act_key", "launch_app"):
        assert caps[key] is True


def test_capabilities_all_false_when_unavailable():
    p = AccessibilityProvider(LoopbackTransport({}, available=False))
    assert all(v is False for v in p.capabilities().values())


def test_companion_advertises_observe_screenshot():
    # Finding 16 evolution: the companion never writes outside its own storage — the
    # `screenshot` RPC returns the PNG bytes over the (token-authenticated) socket and
    # the Python side persists them under ITS storage, so no cross-UID dead-end remains.
    p = AccessibilityProvider(LoopbackTransport({}))
    assert p.capabilities()["observe_screenshot"] is True


# --- Task 3: native tree + ui_dump ---

def _native_handler(_params):
    return {
        "windows": [{"id": 1, "type": "application", "package": "com.android.settings",
                     "nodes": [{"node_id": "n1", "text": "Network & internet",
                                "class": "android.widget.TextView", "content_desc": "",
                                "bounds": [0, 200, 1080, 320], "clickable": True,
                                "enabled": True}]}]
    }


def test_ui_dump_returns_parseable_compat_xml():
    p = AccessibilityProvider(LoopbackTransport({"observe_native": _native_handler}))
    elements = ui_parser.parse_elements(p.ui_dump())
    assert any(e.get("text") == "Network & internet" for e in elements)


# --- Task 4: gesture dispatch + ACTION_SET_TEXT ---

class RecordingTransport(LoopbackTransport):
    def __init__(self):
        self.sent = []
        super().__init__({
            "gesture": self._ok, "key": self._ok, "set_text": self._ok, "launch": self._ok,
        })

    def _ok(self, params):
        return {"applied": True}

    def request(self, method, params, *, request_id, timeout):
        self.sent.append((method, params))
        return super().request(method, params, request_id=request_id, timeout=timeout)


def test_input_tap_sends_tap_gesture():
    t = RecordingTransport()
    AccessibilityProvider(t).input_tap(100, 220)
    assert ("gesture", {"type": "tap", "x": 100, "y": 220}) in t.sent


def test_set_text_native_uses_action_set_text_mode():
    t = RecordingTransport()
    AccessibilityProvider(t).set_text_native("n2", "hello")
    method, params = t.sent[-1]
    assert method == "set_text"
    assert params == {"node_id": "n2", "text": "hello", "mode": "set"}


def test_input_text_uses_type_mode():
    t = RecordingTransport()
    AccessibilityProvider(t).input_text("hi")
    assert t.sent[-1] == ("set_text", {"text": "hi", "mode": "type"})


# --- key pre-flight: only companion-performable keys reach the socket ---

def test_input_key_sends_supported_global_keys():
    t = RecordingTransport()
    p = AccessibilityProvider(t)
    for kc in ("KEYCODE_BACK", "KEYCODE_HOME", "KEYCODE_APP_SWITCH",
               "KEYCODE_NOTIFICATIONS", "KEYCODE_QUICK_SETTINGS", "RECENTS", "back"):
        p.input_key(kc)
        assert t.sent[-1] == ("key", {"keycode": kc})


def test_input_key_refuses_unsupported_keycode_without_rpc():
    # The companion can only perform global actions; arbitrary keycodes (ENTER, TAB,
    # DPAD…) must fall to ADB immediately, not cost a doomed socket round trip.
    t = RecordingTransport()
    p = AccessibilityProvider(t)
    with pytest.raises(errors.CapabilityUnavailableError):
        p.input_key("KEYCODE_ENTER")
    assert all(m != "key" for m, _ in t.sent)


# --- companion-first gestures: long-press / named swipe / fling ---

class GestureTransport(RecordingTransport):
    """RecordingTransport that also serves observe_native (for the screen size)."""

    def __init__(self):
        super().__init__()
        self._handlers["observe_native"] = lambda p: {
            "windows": [], "screen": {"width": 1080, "height": 2400}, "generation": 1,
        }


def test_input_long_press_is_a_same_point_swipe_gesture():
    t = RecordingTransport()
    AccessibilityProvider(t).input_long_press(100, 220, 900)
    assert ("gesture", {"type": "swipe", "x1": 100, "y1": 220,
                        "x2": 100, "y2": 220, "ms": 900}) in t.sent


def test_input_named_swipe_computes_coords_from_screen_size():
    t = GestureTransport()
    p = AccessibilityProvider(t)
    p.input_named_swipe("up")
    # 1080x2400: center (540, 1200), half_y = 2400 * 0.5 / 2 = 600
    assert t.sent[-1] == ("gesture", {"type": "swipe", "x1": 540, "y1": 1800,
                                      "x2": 540, "y2": 600, "ms": 400})


def test_input_named_swipe_uses_cached_screen_without_new_rpc():
    t = GestureTransport()
    p = AccessibilityProvider(t)
    p.observe_native()
    p.input_named_swipe("left", distance_pct=0.5, ms=300)
    # Exactly one observe_native — the swipe rode the cached screen size.
    assert sum(1 for m, _ in t.sent if m == "observe_native") == 1
    assert t.sent[-1] == ("gesture", {"type": "swipe", "x1": 810, "y1": 1200,
                                      "x2": 270, "y2": 1200, "ms": 300})


def test_input_named_swipe_rejects_unknown_direction_locally():
    t = GestureTransport()
    with pytest.raises(ValueError):
        AccessibilityProvider(t).input_named_swipe("sideways")
    assert all(m != "gesture" for m, _ in t.sent)


def test_input_fling_timing_mirrors_adb_backend():
    t = GestureTransport()
    AccessibilityProvider(t).input_fling("left")
    # velocity 2000 -> ms = max(50, min(400, 2_000_000 // 2000)) = 400; distance 0.6
    assert t.sent[-1] == ("gesture", {"type": "swipe", "x1": 864, "y1": 1200,
                                      "x2": 216, "y2": 1200, "ms": 400})


# --- Task 5: semantic node actions ---

def test_semantic_action_click_sends_request():
    t = RecordingTransport()
    t._handlers["semantic"] = lambda p: {"performed": p["action"]}
    out = AccessibilityProvider(t).semantic_action("n1", "click")
    assert out["performed"] == "click"
    assert t.sent[-1][0] == "semantic"


def test_semantic_action_rejects_unknown_action_locally():
    t = RecordingTransport()
    with pytest.raises(ValueError):
        AccessibilityProvider(t).semantic_action("n1", "teleport")
    assert all(m != "semantic" for m, _ in t.sent)  # never contacted companion


# --- Finding 9: actions carry the generation of the observation they were reasoned over ---

def _native_with_generation(gen):
    def handler(_params):
        data = _native_handler({})
        data["generation"] = gen
        return data
    return handler


def test_set_text_native_threads_last_observed_generation():
    t = RecordingTransport()
    t._handlers["observe_native"] = _native_with_generation(7)
    p = AccessibilityProvider(t)
    p.observe_native()
    p.set_text_native("n2", "hello")
    method, params = t.sent[-1]
    assert method == "set_text"
    assert params["generation"] == 7


def test_semantic_action_threads_last_observed_generation():
    t = RecordingTransport()
    t._handlers["observe_native"] = _native_with_generation(3)
    t._handlers["semantic"] = lambda p: {"performed": p["action"]}
    p = AccessibilityProvider(t)
    p.observe_native()
    p.semantic_action("n1", "click")
    assert t.sent[-1][1]["generation"] == 3


def test_generation_omitted_before_first_observation():
    # No observation yet -> no token to bind to; the companion allows opted-out callers.
    t = RecordingTransport()
    AccessibilityProvider(t).set_text_native("n2", "hello")
    assert "generation" not in t.sent[-1][1]


def test_reobserve_updates_the_threaded_generation():
    t = RecordingTransport()
    p = AccessibilityProvider(t)
    t._handlers["observe_native"] = _native_with_generation(1)
    p.observe_native()
    t._handlers["observe_native"] = _native_with_generation(2)
    p.observe_native()
    p.set_text_native("n2", "hello")
    assert t.sent[-1][1]["generation"] == 2


def test_stale_generation_maps_to_stale_snapshot_error():
    p = AccessibilityProvider(ErrorTransport("stale_generation", "re-observe"))
    with pytest.raises(errors.StaleSnapshotError):
        p.set_text_native("n1", "hi")


# --- Finding 3: companion error envelopes map to the typed error hierarchy ---

class ErrorTransport(LoopbackTransport):
    """Fake companion that answers every request with a fixed error envelope."""

    def __init__(self, code, message="refused"):
        super().__init__({})
        self._code, self._message = code, message

    def request(self, method, params, *, request_id, timeout):
        return {"ok": False, "request_id": request_id, "version": 1,
                "error": {"code": self._code, "message": self._message}}


@pytest.mark.parametrize("code,exc", [
    ("stopped", errors.StoppedError),                     # on-device STOP gate (Finding 3)
    ("guarded_action", errors.GuardedActionError),
    ("capability_disabled", errors.CapabilityUnavailableError),
    ("unauthorized", errors.UnauthorizedError),
    ("unknown_method", errors.UnknownMethodError),
    ("handler_error", errors.DroidjigError),              # anything else stays typed but generic
])
def test_companion_error_codes_map_to_typed_errors(code, exc):
    p = AccessibilityProvider(ErrorTransport(code))
    with pytest.raises(exc):
        p.input_tap(1, 2)


def test_companion_stopped_error_is_not_swallowed_as_generic():
    p = AccessibilityProvider(ErrorTransport("stopped", "companion emergency stop is engaged"))
    with pytest.raises(errors.StoppedError):
        p.set_text_native("n1", "hi")


# --- Task 6: UI event polling ---

def test_poll_events_returns_events_and_cursor():
    def events(p):
        assert p["since"] == 0
        return {"events": [{"seq": 1, "type": "window_state_changed", "package": "com.x"}],
                "cursor": 1}
    t = LoopbackTransport({"events": events})
    out = AccessibilityProvider(t).poll_events(since=0)
    assert out["cursor"] == 1
    assert out["events"][0]["type"] == "window_state_changed"


def test_poll_events_passes_since_cursor():
    seen = {}
    def events(p):
        seen.update(p)
        return {"events": [], "cursor": 7}
    t = LoopbackTransport({"events": events})
    AccessibilityProvider(t).poll_events(since=7)
    assert seen["since"] == 7


# ── observe_dump: one native RPC per observe; screen size served from it ─────

class CountingTransport(LoopbackTransport):
    def __init__(self, handlers, **kw):
        super().__init__(handlers, **kw)
        self.calls = []

    def request(self, method, params, *, request_id, timeout):
        self.calls.append(method)
        return super().request(method, params, request_id=request_id, timeout=timeout)


def _native_with_screen(_params):
    data = _native_handler(_params)
    data["screen"] = {"width": 1080, "height": 2400}
    data["generation"] = 7
    return data


def test_observe_dump_is_a_single_native_rpc():
    t = CountingTransport({"observe_native": _native_with_screen})
    p = AccessibilityProvider(t)
    xml, window = p.observe_dump()
    assert t.calls.count("observe_native") == 1
    assert window is None                      # companion knows nothing of keyguard
    elements = ui_parser.parse_elements(xml)
    assert any(e.get("text") == "Network & internet" for e in elements)


def test_wm_size_served_from_last_observe_without_new_rpc():
    t = CountingTransport({"observe_native": _native_with_screen})
    p = AccessibilityProvider(t)
    p.observe_dump()
    assert p.wm_size() == (1080, 2400)
    assert t.calls.count("observe_native") == 1   # no second tree serialization


def test_wm_size_still_fetches_when_no_observe_ran():
    t = CountingTransport({"observe_native": _native_with_screen})
    p = AccessibilityProvider(t)
    assert p.wm_size() == (1080, 2400)
    assert t.calls.count("observe_native") == 1


def test_observe_dump_threads_generation_for_stale_protection():
    t = CountingTransport({"observe_native": _native_with_screen})
    p = AccessibilityProvider(t)
    p.observe_dump()
    assert p._last_generation == 7


# --- screencap: base64 PNG over the socket, persisted Python-side ---

PNG_BYTES = b"\x89PNG\r\n\x1a\nfakepixels"


class ScreenshotTransport(LoopbackTransport):
    def __init__(self, handlers=None):
        import base64
        self.timeouts = []
        super().__init__(handlers if handlers is not None else {
            "screenshot": lambda p: {"format": "png",
                                     "data": base64.b64encode(PNG_BYTES).decode("ascii")},
        })

    def request(self, method, params, *, request_id, timeout):
        self.timeouts.append((method, timeout))
        return super().request(method, params, request_id=request_id, timeout=timeout)


def test_screencap_decodes_base64_and_writes_the_requested_path(tmp_path):
    t = ScreenshotTransport()
    out = str(tmp_path / "snap.png")
    assert AccessibilityProvider(t).screencap(out) == out
    with open(out, "rb") as f:
        assert f.read() == PNG_BYTES


def test_screencap_uses_a_generous_timeout():
    # PNG encode + a multi-MB base64 line deserve more than the 2s RPC default.
    t = ScreenshotTransport()
    AccessibilityProvider(t).screencap("/dev/null")
    (method, timeout), = [(m, s) for m, s in t.timeouts if m == "screenshot"]
    assert timeout >= 10.0


def test_screencap_undecodable_payload_raises_observe_error(tmp_path):
    t = ScreenshotTransport({"screenshot": lambda p: {"format": "png", "data": "!!not-base64!!"}})
    out = tmp_path / "snap.png"
    with pytest.raises(errors.ObserveError):
        AccessibilityProvider(t).screencap(str(out))
    assert not out.exists()   # a broken capture must not leave a partial file behind


# --- ADB-free observe: keyguard + focus ride the native payload ---

def _native_full(showing, secure, pkg="com.android.settings",
                 activity="com.android.settings.Settings"):
    def handler(p):
        data = _native_with_screen(p)
        data["keyguard"] = {"showing": showing, "secure": secure}
        data["focus"] = {"package": pkg, "activity": activity}
        return data
    return handler


def test_observe_dump_returns_structured_window_from_native_payload():
    t = CountingTransport({"observe_native": _native_full(showing=False, secure=False)})
    xml, window = AccessibilityProvider(t).observe_dump()
    assert t.calls.count("observe_native") == 1        # still one RPC
    assert window["app"] == {"package": "com.android.settings",
                             "activity": "com.android.settings.Settings"}
    assert window["lock"] == {"lock_state": "unlocked", "can_act": True,
                              "recommended_user_action": None}


def test_observe_dump_maps_secure_keyguard_to_locked_secure():
    t = CountingTransport({"observe_native": _native_full(showing=True, secure=True)})
    _xml, window = AccessibilityProvider(t).observe_dump()
    assert window["lock"]["lock_state"] == "locked_secure"
    assert window["lock"]["can_act"] is False
    assert window["lock"]["recommended_user_action"]


def test_observe_dump_maps_insecure_keyguard_to_swipe_only():
    t = CountingTransport({"observe_native": _native_full(showing=True, secure=False)})
    _xml, window = AccessibilityProvider(t).observe_dump()
    assert window["lock"]["lock_state"] == "locked_swipe_only"
    assert window["lock"]["can_act"] is False


def test_observe_dump_window_none_when_focus_package_empty():
    # A keyguard without a usable focused package would blind the guarded_packages
    # risk signal — keep the ADB augment instead of shipping an empty app.
    t = CountingTransport({"observe_native": _native_full(showing=False, secure=False, pkg="")})
    _xml, window = AccessibilityProvider(t).observe_dump()
    assert window is None
