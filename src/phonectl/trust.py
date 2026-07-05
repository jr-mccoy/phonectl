"""Companion trust model — handshake, per-capability toggles, emergency-stop flag."""
from __future__ import annotations

from dataclasses import dataclass, field

from phonectl.providers.transport import next_request_id


@dataclass
class Handshake:
    version: int = 0
    capabilities: dict = field(default_factory=dict)
    stopped: bool = False
    reachable: bool = False


def negotiate(transport, *, timeout: float = 2.0) -> Handshake:
    rid = next_request_id()
    try:
        resp = transport.request("handshake", {}, request_id=rid, timeout=timeout)
    except Exception:
        return Handshake()
    if resp.get("request_id") != rid or not resp.get("ok"):
        return Handshake()
    data = resp.get("data", {})
    return Handshake(
        version=int(data.get("version", 0)),
        capabilities=dict(data.get("capabilities", {})),
        stopped=bool(data.get("stopped", False)),
        reachable=True,
    )


def companion_stopped(transport, *, timeout: float = 1.0) -> bool:
    """True if the companion reports STOP — or cannot be asked (Finding 8).

    This check only runs when a companion transport is configured, so an
    unreachable/erroring companion at the moment STOP is checked fails closed
    (treated as stopped) rather than silently proceeding. "No companion
    configured" never reaches this path.
    """
    hs = negotiate(transport, timeout=timeout)
    return hs.stopped or not hs.reachable


# Backend-protocol aliases of the native capabilities the companion handshake actually
# names. These are the SAME surfaces under the registry's capability names (the compat
# tree IS observe_ui_native; a tap IS a gesture RPC), gated on-device by the same toggle
# (the companion's METHOD_CAPABILITY maps `key`→act_gesture_native, `set_text`→
# act_set_text_native). Without this derivation, Finding 5's default-deny stripped them
# as "unknown" and every observe/tap/type/key silently fell back to ADB despite a live,
# fully-enabled companion.
DERIVED_CAPABILITIES = {
    "observe_ui_tree": "observe_ui_native",
    "act_tap": "act_gesture_native",
    "act_key": "act_gesture_native",
    "act_type": "act_set_text_native",
}


def gate_capabilities(advertised: dict, enabled: dict) -> dict:
    # Unknown keys default to disabled (Finding 5): a capability the companion
    # handshake did not explicitly affirm must not be exercised. Derived keys ride
    # their source toggle; a handshake naming a derived key explicitly is authoritative.
    effective = dict(enabled)
    for derived, source in DERIVED_CAPABILITIES.items():
        if derived not in effective and effective.get(source):
            effective[derived] = True
    return {k: bool(v) and bool(effective.get(k, False)) for k, v in advertised.items()}


class GatedProvider:
    def __init__(self, inner, enabled: dict) -> None:
        self._inner = inner
        self._enabled = dict(enabled)

    def capabilities(self) -> dict:
        return gate_capabilities(self._inner.capabilities(), self._enabled)

    def __getattr__(self, name):
        return getattr(self._inner, name)
