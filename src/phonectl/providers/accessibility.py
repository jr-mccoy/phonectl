"""AccessibilityService companion provider — native tree, events, gestures, set-text."""
from __future__ import annotations

from phonectl import capabilities as caps_mod
from phonectl import errors
from phonectl.providers.transport import next_request_id, raise_companion_error


SUPPORTED_SEMANTIC_ACTIONS = frozenset({
    "click", "long_click", "scroll_forward", "scroll_backward",
    "expand", "collapse", "dismiss",
})


class AccessibilityProvider:
    def __init__(self, transport, *, timeout: float = 2.0) -> None:
        self._t = transport
        self._timeout = timeout
        # Tree-generation token from the last observe_native (Finding 9). Threaded into
        # set_text/semantic so the companion can refuse actions reasoned over a stale tree.
        self._last_generation = None
        # Screen size from the last native payload: serving wm_size() from it
        # spares a second full-tree serialization on every observe.
        self._last_screen = None

    def is_available(self) -> bool:
        try:
            return bool(self._t.ping())
        except Exception:
            return False

    def capabilities(self) -> dict:
        if not self.is_available():
            return caps_mod.make()
        # observe_screenshot intentionally NOT advertised (Finding 16): the companion now writes
        # screenshots only under its own app storage, which this process (a different Android
        # UID) cannot read — advertising it would just dead-end registry.screencap. ADB serves it.
        return caps_mod.make(
            observe_ui_native=True, observe_ui_events=True,
            act_set_text_native=True, act_gesture_native=True, act_semantic_action=True,
            observe_ui_tree=True,
            act_tap=True, act_type=True, act_key=True, launch_app=True,
        )

    def _call(self, method: str, params: dict | None = None) -> dict:
        rid = next_request_id()
        resp = self._t.request(method, params or {}, request_id=rid, timeout=self._timeout)
        if resp.get("request_id") != rid:
            raise errors.ObserveError(
                f"stale companion response: expected {rid}, got {resp.get('request_id')}"
            )
        if not resp.get("ok"):
            raise_companion_error(resp.get("error", {}))
        return resp.get("data", {})

    # --- Task 3: native tree + compat XML ---

    def observe_native(self) -> dict:
        data = self._call("observe_native")
        if "generation" in data:
            self._last_generation = data["generation"]
        screen = data.get("screen")
        if screen and "width" in screen and "height" in screen:
            self._last_screen = (int(screen["width"]), int(screen["height"]))
        return data

    def _with_generation(self, params: dict) -> dict:
        if self._last_generation is not None:
            params["generation"] = self._last_generation
        return params

    def ui_dump(self) -> str:
        from phonectl import native_tree
        return native_tree.to_compat_xml(self.observe_native())

    def observe_dump(self):
        """Compat XML from ONE observe_native RPC; window is None because the
        companion has no view of the keyguard/focused-window record — the
        registry augments it from the ADB provider. The payload's screen size
        is cached so the wm_size() in the same observe costs no second RPC."""
        return self.ui_dump(), None

    def window_dump(self) -> str:
        return ""

    def wm_size(self):
        # Refreshed by every observe_native; between observes the physical
        # size is as constant as ADB's own wm_size cache assumes.
        if self._last_screen is not None:
            return self._last_screen
        self.observe_native()
        if self._last_screen is not None:
            return self._last_screen
        raise errors.CapabilityUnavailableError("companion did not report screen size")

    # --- Task 4: gesture dispatch + ACTION_SET_TEXT ---

    def input_tap(self, x, y):
        self._call("gesture", {"type": "tap", "x": x, "y": y})

    def input_swipe(self, x1, y1, x2, y2, ms: int = 200):
        self._call("gesture", {"type": "swipe", "x1": x1, "y1": y1, "x2": x2, "y2": y2, "ms": ms})

    def input_long_press(self, x, y, duration_ms: int = 1000):
        # A same-point stroke held for the duration — the gesture-dispatch analogue of
        # AdbBackend's `input swipe x y x y ms` long press.
        self.input_swipe(x, y, x, y, duration_ms)

    def input_named_swipe(self, direction, distance_pct: float = 0.5, ms: int = 400):
        if direction not in {"up", "down", "left", "right"}:
            raise ValueError(f"unknown swipe direction: {direction!r}")
        w, h = self.wm_size()   # cached from the last observe; one RPC at most
        cx, cy = w // 2, h // 2
        half_x = int(w * distance_pct / 2)
        half_y = int(h * distance_pct / 2)
        if direction == "up":
            self.input_swipe(cx, cy + half_y, cx, cy - half_y, ms)
        elif direction == "down":
            self.input_swipe(cx, cy - half_y, cx, cy + half_y, ms)
        elif direction == "left":
            self.input_swipe(cx + half_x, cy, cx - half_x, cy, ms)
        else:
            self.input_swipe(cx - half_x, cy, cx + half_x, cy, ms)

    def input_fling(self, direction, velocity: int = 2000):
        # Same timing curve as AdbBackend.input_fling so a provider switch keeps semantics.
        ms = max(50, min(400, 2_000_000 // velocity))
        self.input_named_swipe(direction, distance_pct=0.6, ms=ms)

    def input_key(self, keycode):
        self._call("key", {"keycode": keycode})

    def input_text(self, text):
        self._call("set_text", self._with_generation({"text": text, "mode": "type"}))

    def set_text_native(self, node_id, text):
        self._call("set_text",
                   self._with_generation({"node_id": node_id, "text": text, "mode": "set"}))

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
        return self._call("semantic",
                          self._with_generation({"node_id": node_id, "action": action}))

    # --- Task 6: UI event polling ---

    def poll_events(self, since: int = 0, *, max_events: int = 50) -> dict:
        return self._call("events", {"since": since, "max": max_events})
