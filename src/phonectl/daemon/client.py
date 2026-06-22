"""DaemonClient: frontend RPC over the Plan-4.3 SocketTransport."""
from __future__ import annotations

from phonectl import errors, results
from phonectl.daemon import PROTOCOL_VERSION
from phonectl.providers.transport import SocketTransport, next_request_id


class DaemonClient:
    def __init__(self, host, port, *, transport=None, version=PROTOCOL_VERSION) -> None:
        self._host, self._port, self._version = host, port, version
        self._transport = transport or SocketTransport(host, port, version=version)

    @classmethod
    def from_discovery(cls, info, *, transport=None):
        return cls(info["host"], info["port"], transport=transport,
                   version=info.get("version", PROTOCOL_VERSION))

    def call(self, method, params=None, *, timeout=5.0) -> dict:
        rid = next_request_id()
        try:
            resp = self._transport.request(method, params or {}, request_id=rid, timeout=timeout)
        except Exception:
            return results.err(errors.DaemonUnreachableError(f"daemon call {method!r} failed"))
        if not isinstance(resp, dict) or resp.get("request_id") not in (rid, None):
            return results.err(errors.DaemonUnreachableError("no matching daemon response"))
        if resp.get("ok") is None and "error" not in resp:
            return results.err(errors.DaemonUnreachableError("malformed daemon response"))
        return resp

    def is_running(self) -> bool:
        return bool(self.call("ping", {}, timeout=1.0).get("ok"))
