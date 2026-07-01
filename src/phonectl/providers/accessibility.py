"""AccessibilityService companion provider — native tree, events, gestures, set-text."""
from __future__ import annotations

from phonectl import capabilities as caps_mod
from phonectl import errors
from phonectl.providers.transport import next_request_id


SUPPORTED_SEMANTIC_ACTIONS = frozenset({
    "click", "long_click", "scroll_forward", "scroll_backward",
    "expand", "collapse", "dismiss",
})


class AccessibilityProvider:
    def __init__(self, transport, *, timeout: float = 2.0) -> None:
        self._t = transport
        self._timeout = timeout

    def is_available(self) -> bool:
        try:
            return bool(self._t.ping())
        except Exception:
            return False

    def capabilities(self) -> dict:
        if not self.is_available():
            return caps_mod.make()
        return caps_mod.make(
            observe_ui_native=True, observe_ui_events=True, persistent_events=True,
            act_set_text_native=True, act_gesture_native=True, act_semantic_action=True,
            observe_ui_tree=True, observe_screenshot=True,
            act_tap=True, act_type=True, act_key=True, launch_app=True,
        )

    def _call(self, method: str, params: dict | None = None, *, timeout: float | None = None) -> dict:
        rid = next_request_id()
        resp = self._t.request(method, params or {}, request_id=rid, timeout=timeout or self._timeout)
        if resp.get("request_id") != rid:
            raise errors.ObserveError(
                f"stale companion response: expected {rid}, got {resp.get('request_id')}"
            )
        if not resp.get("ok"):
            err = resp.get("error", {})
            raise errors.ActionError(err.get("message", "companion error"))
        return resp.get("data", {})

    # --- Task 3: native tree + compat XML ---

    def observe_native(self) -> dict:
        return self._call("observe_native")

    def ui_dump(self) -> str:
        from phonectl import native_tree
        return native_tree.to_compat_xml(self.observe_native())

    def window_dump(self) -> str:
        return ""

    def wm_size(self):
        data = self.observe_native()
        screen = data.get("screen")
        if screen and "width" in screen and "height" in screen:
            return (int(screen["width"]), int(screen["height"]))
        raise errors.CapabilityUnavailableError("companion did not report screen size")

    # --- Task 4: gesture dispatch + ACTION_SET_TEXT ---

    def input_tap(self, x, y):
        self._call("gesture", {"type": "tap", "x": x, "y": y})

    def input_swipe(self, x1, y1, x2, y2, ms: int = 200):
        self._call("gesture", {"type": "swipe", "x1": x1, "y1": y1, "x2": x2, "y2": y2, "ms": ms})

    def input_key(self, keycode):
        self._call("key", {"keycode": keycode})

    def input_text(self, text):
        self._call("set_text", {"text": text, "mode": "type"})

    def set_text_native(self, node_id, text):
        self._call("set_text", {"node_id": node_id, "text": text, "mode": "set"})

    def launch(self, package):
        self._call("launch", {"package": package})

    def screencap(self, path):
        self._call("screencap", {"path": path})
        return path

    def get_state(self):
        return "device" if self.is_available() else "unknown"

    # --- Task 5: semantic node actions ---

    def semantic_action(self, node_id, action) -> dict:
        if action not in SUPPORTED_SEMANTIC_ACTIONS:
            raise ValueError(f"unsupported semantic action {action!r}")
        return self._call("semantic", {"node_id": node_id, "action": action})

    # --- Task 6: UI event polling ---

    def supports_events_wait(self) -> bool:
        return bool(self.capabilities().get("persistent_events"))

    def poll_events(self, since: int = 0, *, max_events: int = 50) -> dict:
        return self._call("events", {"since": since, "max": max_events})

    def wait_events(self, since: int = 0, *, max_events: int = 50, timeout_ms: int = 1000) -> dict:
        timeout_ms = max(0, min(int(timeout_ms), 30_000))
        # Give the socket a small margin beyond the companion's server-side wait timeout.
        timeout = max(self._timeout, (timeout_ms / 1000.0) + 0.5)
        return self._call(
            "events_wait",
            {"since": since, "max": max_events, "timeout_ms": timeout_ms},
            timeout=timeout,
        )
