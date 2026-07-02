"""IntentProvider — start activities and send broadcasts via the best provider."""
from __future__ import annotations

from phonectl import errors, results, runtime as rt


class IntentProvider:
    def __init__(self, registry) -> None:
        self._registry = registry

    def start(self, *, action=None, data=None, component=None,
              extras=None, build, yes=False, cfg=None) -> dict:
        if self._registry.for_capability("intent_start") is None:
            return results.err(
                errors.CapabilityUnavailableError("intent_start not available"),
                capability="intent.start",
            )
        # Structured target: the risk classifier inspects action/data for
        # dialer/SMS markers (tel:, sms:, ACTION_CALL) — a bare component
        # string would hide them.
        target = {
            k: v
            for k, v in {"action": action, "data": data, "component": component}.items()
            if v
        } or "(intent)"
        return rt.run_action(
            "intent_start",
            lambda backend, session: (
                backend.intent_start(action=action, data=data,
                                     component=component, extras=extras),
                {},
            )[1],
            target,
            build=build, yes=yes, cfg=cfg,
        )

    def broadcast(self, action: str, *, extras=None,
                  build, yes=False, cfg=None) -> dict:
        if self._registry.for_capability("intent_broadcast") is None:
            return results.err(
                errors.CapabilityUnavailableError("intent_broadcast not available"),
                capability="intent.broadcast",
            )
        return rt.run_action(
            "intent_broadcast",
            lambda backend, session: (backend.intent_broadcast(action, extras=extras), {})[1],
            action,
            build=build, yes=yes, cfg=cfg,
        )
