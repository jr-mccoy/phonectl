"""Composite provider registry — selects the best provider per capability."""
from __future__ import annotations

from phonectl import capabilities as caps_mod
from phonectl import errors


class ProviderRegistry:
    def __init__(self, providers) -> None:
        self._providers = list(providers)
        self._last_used: str | None = None

    @property
    def last_used(self) -> str | None:
        return self._last_used

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

    def _require(self, cap: str):
        p = self.for_capability(cap)
        if p is None:
            raise errors.CapabilityUnavailableError(
                f"no provider registered for capability {cap!r}"
            )
        self._last_used = type(p).__name__
        return p

    # Backend Protocol delegation
    def ui_dump(self) -> str:
        return self._require("observe_ui_tree").ui_dump()

    def window_dump(self) -> str:
        return self._require("observe_ui_tree").window_dump()

    def wm_size(self):
        return self._require("observe_ui_tree").wm_size()

    def screencap(self, path: str) -> str:
        return self._require("observe_screenshot").screencap(path)

    def input_tap(self, x: int, y: int) -> None:
        self._require("act_tap").input_tap(x, y)

    def input_text(self, text: str) -> None:
        self._require("act_type").input_text(text)

    def input_swipe(self, x1, y1, x2, y2, ms: int = 200) -> None:
        self._require("act_tap").input_swipe(x1, y1, x2, y2, ms)

    def input_key(self, keycode: str) -> None:
        self._require("act_key").input_key(keycode)

    def launch(self, package: str) -> None:
        self._require("launch_app").launch(package)

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
