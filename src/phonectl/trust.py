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


def gate_capabilities(advertised: dict, enabled: dict) -> dict:
    # Unknown keys default to disabled (Finding 5): a capability the companion
    # handshake did not explicitly affirm must not be exercised.
    return {k: bool(v) and bool(enabled.get(k, False)) for k, v in advertised.items()}


class GatedProvider:
    def __init__(self, inner, enabled: dict) -> None:
        self._inner = inner
        self._enabled = dict(enabled)

    def capabilities(self) -> dict:
        return gate_capabilities(self._inner.capabilities(), self._enabled)

    def __getattr__(self, name):
        return getattr(self._inner, name)
