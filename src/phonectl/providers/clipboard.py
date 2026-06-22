"""ClipboardProvider — reads/writes clipboard via the best available provider."""
from __future__ import annotations

from phonectl import errors, results


class ClipboardProvider:
    def __init__(self, registry) -> None:
        self._registry = registry

    def read(self) -> dict:
        p = self._registry.for_capability("read_clipboard")
        if p is None:
            return results.err(
                errors.CapabilityUnavailableError("clipboard read is not available via ADB"),
                capability="clipboard.read",
                user_action=(
                    "Install Termux:API and run 'phonectl setup termux-api' to enable clipboard read."
                ),
            )
        try:
            text = p.clipboard_read()
            return results.ok(
                capability="clipboard.read",
                provider=type(p).__name__,
                data={"text": text},
            )
        except Exception as e:
            return results.err(
                (errors.ObserveError.code, str(e)), capability="clipboard.read"
            )

    def write(self, text: str, *, build, yes: bool = False, cfg=None) -> dict:
        from phonectl import runtime
        return runtime.run_action(
            "clipboard_write",
            lambda backend, session: (backend.clipboard_write(text), {"text": text})[1],
            repr(text[:20]),
            build=build,
            yes=yes,
            cfg=cfg,
        )

    def clear(self, *, build, yes: bool = False, cfg=None) -> dict:
        return self.write("", build=build, yes=yes, cfg=cfg)
