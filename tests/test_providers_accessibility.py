import pytest
from phonectl import errors, ui_parser
from phonectl.providers.accessibility import AccessibilityProvider
from phonectl.providers.transport import LoopbackTransport


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
    ("handler_error", errors.PhonectlError),              # anything else stays typed but generic
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
