"""Composite provider registry — selects the best provider per capability."""
from __future__ import annotations

from phonectl import capabilities as caps_mod
from phonectl import errors


# Provider exceptions that are POLICY REFUSALS, not runtime failures (Finding 13): the companion
# refusing because an app is guarded or its STOP is engaged must propagate — falling through to
# ADB would bypass the very protection the companion enforced.
_NO_FALLBACK = (
    errors.StoppedError,
    errors.GuardedActionError,
    errors.ConfirmationRequiredError,
    errors.StaleSnapshotError,
    errors.RateLimitError,
    errors.BusyError,
    errors.DeviceLockedError,
)


class ProviderRegistry:
    def __init__(self, providers) -> None:
        self._providers = list(providers)
        self._last_used: str | None = None
        self._last_fallback: list = []

    @property
    def last_used(self) -> str | None:
        return self._last_used

    @property
    def last_fallback(self) -> list:
        """Providers skipped on the most recent delegated call: [{provider, error}, ...]."""
        return self._last_fallback

    def for_capability(self, cap: str):
        for p in self._providers:
            if p.capabilities().get(cap):
                return p
        return None

    def capabilities(self) -> dict:
        merged = caps_mod.make()
        for p in self._providers:
            for k, v in p.capabilities().items():
                if v:
                    merged[k] = True
        return merged

    def capabilities_by_provider(self) -> list:
        return [
            {"provider": type(p).__name__, "caps": p.capabilities()}
            for p in self._providers
        ]

    @property
    def serial(self):
        p = self.for_capability("requires_adb")
        return getattr(p, "serial", None) if p else None

    @serial.setter
    def serial(self, value) -> None:
        p = self.for_capability("requires_adb")
        if p is not None:
            p.serial = value

    def _require(self, cap: str):
        p = self.for_capability(cap)
        if p is None:
            raise errors.CapabilityUnavailableError(
                f"no provider registered for capability {cap!r}"
            )
        self._last_used = type(p).__name__
        return p

    def _delegate(self, cap: str, call):
        """Run ``call`` against providers advertising ``cap`` in priority order, falling through
        to the next one on runtime failure (Finding 13: a companion that advertises a capability
        but dies mid-request must not hard-fail when ADB can serve). Policy refusals
        (_NO_FALLBACK) propagate. ``last_used`` reports who actually served the call;
        ``last_fallback`` records who was skipped and why."""
        candidates = [p for p in self._providers if p.capabilities().get(cap)]
        if not candidates:
            raise errors.CapabilityUnavailableError(
                f"no provider registered for capability {cap!r}"
            )
        self._last_fallback = []
        last_exc = None
        for p in candidates:
            try:
                result = call(p)
                self._last_used = type(p).__name__
                return result
            except _NO_FALLBACK:
                self._last_used = type(p).__name__
                raise
            except Exception as exc:
                self._last_fallback.append({"provider": type(p).__name__, "error": str(exc)})
                last_exc = exc
        raise last_exc

    # Backend Protocol delegation
    def ui_dump(self) -> str:
        return self._delegate("observe_ui_tree", lambda p: p.ui_dump())

    def window_dump(self) -> str:
        return self._delegate("observe_ui_tree", lambda p: p.window_dump())

    def wm_size(self):
        return self._delegate("observe_ui_tree", lambda p: p.wm_size())

    def screencap(self, path: str) -> str:
        return self._delegate("observe_screenshot", lambda p: p.screencap(path))

    def input_tap(self, x: int, y: int) -> None:
        self._delegate("act_tap", lambda p: p.input_tap(x, y))

    def input_text(self, text: str) -> None:
        self._delegate("act_type", lambda p: p.input_text(text))

    def input_swipe(self, x1, y1, x2, y2, ms: int = 200) -> None:
        self._delegate("act_tap", lambda p: p.input_swipe(x1, y1, x2, y2, ms))

    def input_key(self, keycode: str) -> None:
        self._delegate("act_key", lambda p: p.input_key(keycode))

    def launch(self, package: str) -> None:
        self._delegate("launch_app", lambda p: p.launch(package))

    def get_state(self) -> str:
        return self._require("requires_adb").get_state()

    def __getattr__(self, name: str):
        # Delegate ADB-specific helpers (wake, keyguard, lock_state, mdns_services, …)
        # to the first ADB-capable provider without requiring Protocol entries for each.
        p = self.for_capability("requires_adb")
        if p is None:
            raise AttributeError(
                f"ProviderRegistry has no attribute {name!r} and no ADB provider is registered"
            )
        return getattr(p, name)
