"""Companion-APK transport seam — request/response with request-id + stale-response protection."""
from __future__ import annotations

import json as _json
import socket as _socket
import threading as _threading
import time as _time
import uuid
from typing import Protocol, runtime_checkable

_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def next_request_id() -> str:
    return uuid.uuid4().hex


# Companion error codes -> the typed phonectl error hierarchy. `stopped` matters most: the
# companion dispatcher now enforces its STOP flag on-device (Finding 3), and callers must see
# that as the same StoppedError the local kill switch raises, not a generic failure.
def raise_companion_error(err: dict):
    """Raise the typed error matching a companion error envelope's ``code``."""
    from phonectl import errors
    code = (err or {}).get("code", "")
    message = (err or {}).get("message", "companion error")
    exc = {
        "stopped": errors.StoppedError,
        "guarded_action": errors.GuardedActionError,
        "capability_disabled": errors.CapabilityUnavailableError,
        "unauthorized": errors.UnauthorizedError,
        "unknown_method": errors.UnknownMethodError,
        # Finding 9: the observation the action was reasoned over no longer matches the tree.
        "stale_generation": errors.StaleSnapshotError,
    }.get(code, errors.PhonectlError)
    raise exc(message)


@runtime_checkable
class Transport(Protocol):
    def request(self, method: str, params: dict, *, request_id: str, timeout: float) -> dict: ...
    def ping(self, *, timeout: float = 1.0) -> bool: ...


class _SocketConn:
    def __init__(self, host, port, timeout):
        self._sock = _socket.create_connection((host, port), timeout=timeout)
        self._sock.settimeout(timeout)
        self._f = self._sock.makefile("rw", encoding="utf-8", newline="\n")

    def sendline(self, s):
        self._f.write(s + "\n")
        self._f.flush()

    def readline(self):
        return self._f.readline()

    def close(self):
        try:
            self._f.close()
        finally:
            self._sock.close()


class SocketTransport:
    """Loopback TCP transport — newline-delimited JSON with stale-response protection.

    Connections are reused across requests: the companion Server keeps them
    open (30s idle-close), and a TCP connect + handler-thread spawn per RPC was
    the dominant companion-path overhead. A cached conn is preemptively dropped
    after REUSE_IDLE_S so a request never races the server's idle close."""

    REUSE_IDLE_S = 20.0   # < the companion Server's 30s idleTimeoutMs
    PING_TTL = 5.0        # liveness answer stays valid this long

    # Methods safe to resend once over a fresh conn when a CACHED conn turns
    # out dead mid-request: liveness/read-only, never device-mutating.
    READ_ONLY_METHODS = frozenset({
        "ping", "handshake", "version", "capabilities",
        "observe_native", "events", "notifications_list", "screenshot",
    })

    def __init__(self, host: str, port: int, *, version: int = 1, connect=None,
                 token=None, monotonic=_time.monotonic) -> None:
        if host not in _LOOPBACK:
            raise ValueError(f"companion transport is loopback-only; refusing host {host!r}")
        self._host, self._port, self._version = host, port, version
        self._token = token
        self._connect = connect or (lambda h, p, t: _SocketConn(h, p, t))
        self._monotonic = monotonic
        self._lock = _threading.Lock()   # serialize: one socket, maybe many threads
        self._conn = None
        self._conn_used_at = 0.0
        self._alive = None               # (stamped_at, bool) ping cache

    def _drop_conn(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    def _checkout(self, timeout):
        """(conn, reused) — the cached conn while it is fresh, else a new one."""
        if (self._conn is not None
                and self._monotonic() - self._conn_used_at < self.REUSE_IDLE_S):
            return self._conn, True
        self._drop_conn()
        return self._connect(self._host, self._port, timeout), False

    def request(self, method: str, params: dict, *, request_id: str, timeout: float) -> dict:
        req = {"method": method, "params": params or {},
               "request_id": request_id, "timeout": timeout,
               "version": self._version}
        # Shared-secret auth (Finding 2). Loopback is not a UID boundary on Android:
        # both the daemon RPC and the companion socket require this token per-request.
        if self._token is not None:
            req["token"] = self._token
        line = _json.dumps(req)
        with self._lock:
            try:
                return self._request_locked(method, line, request_id, timeout)
            except Exception:
                # Connect/send failure: remember the companion looked dead so
                # capability scans stop re-probing for a beat, then propagate.
                self._alive = (self._monotonic(), False)
                raise

    def _request_locked(self, method, line, request_id, timeout) -> dict:
        conn, reused = self._checkout(timeout)
        try:
            conn.sendline(line)
        except Exception:
            if not reused:
                raise
            # The cached conn died under us. Without its trailing newline the
            # NDJSON line was never dispatched, so a resend cannot double-run.
            self._drop_conn()
            conn, reused = self._connect(self._host, self._port, timeout), False
            conn.sendline(line)
        resp = self._read_response(conn, request_id, timeout)
        if resp is not None:
            self._conn = conn
            self._conn_used_at = self._monotonic()
            self._alive = (self._conn_used_at, True)
            return resp
        # No response: this conn's state is unknowable — never cache it.
        self._conn = None
        try:
            conn.close()
        except Exception:
            pass
        if reused and method in self.READ_ONLY_METHODS:
            # The send may or may not have been dispatched; read-only methods
            # are safe to replay on a fresh conn. Mutating ones are NOT: fall
            # through to the same timeout envelope a lost response yields.
            conn = self._connect(self._host, self._port, timeout)
            conn.sendline(line)
            resp = self._read_response(conn, request_id, timeout)
            if resp is not None:
                self._conn = conn
                self._conn_used_at = self._monotonic()
                self._alive = (self._conn_used_at, True)
                return resp
            try:
                conn.close()
            except Exception:
                pass
        return {"ok": False, "request_id": request_id,
                "error": {"code": "timeout", "message": f"no response for {method!r}"}}

    def _read_response(self, conn, request_id, timeout):
        deadline = _time.monotonic() + timeout
        while _time.monotonic() < deadline:
            raw = conn.readline()
            if not raw:
                return None
            try:
                resp = _json.loads(raw)
            except _json.JSONDecodeError:
                continue
            if resp.get("request_id") == request_id:
                return resp
        return None

    def ping(self, *, timeout: float = 1.0) -> bool:
        # Every registry delegation scans provider capabilities, and those scans
        # ping. Serve repeats from a short cache instead of a socket round trip;
        # any successful request() refreshes it, any failure invalidates it.
        alive = self._alive
        if alive is not None and self._monotonic() - alive[0] < self.PING_TTL:
            return alive[1]
        rid = next_request_id()
        try:
            resp = self.request("ping", {}, request_id=rid, timeout=timeout)
            ok = bool(resp.get("ok"))
        except Exception:
            ok = False
        self._alive = (self._monotonic(), ok)
        return ok


class LoopbackTransport:
    """In-process fake companion. Tests register `method -> (params -> data)` handlers."""

    def __init__(self, handlers: dict, *, version: int = 1, available: bool = True) -> None:
        self._handlers = dict(handlers)
        self._version = version
        self._available = available

    def ping(self, *, timeout: float = 1.0) -> bool:
        return self._available

    def request(self, method: str, params: dict, *, request_id: str, timeout: float) -> dict:
        handler = self._handlers.get(method)
        if handler is None:
            return {"ok": False, "request_id": request_id, "version": self._version,
                    "error": {"code": "unknown_method", "message": f"no handler for {method!r}"}}
        try:
            data = handler(params or {})
        except Exception as exc:
            return {"ok": False, "request_id": request_id, "version": self._version,
                    "error": {"code": "handler_error", "message": str(exc)}}
        return {"ok": True, "request_id": request_id, "version": self._version, "data": data}
