"""Companion-APK transport seam — request/response with request-id + stale-response protection."""
from __future__ import annotations

import json as _json
import socket as _socket
import time as _time
import uuid
from typing import Protocol, runtime_checkable

_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def next_request_id() -> str:
    return uuid.uuid4().hex


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
    """Loopback TCP transport — newline-delimited JSON with stale-response protection."""

    def __init__(self, host: str, port: int, *, version: int = 1, connect=None,
                 token=None) -> None:
        if host not in _LOOPBACK:
            raise ValueError(f"companion transport is loopback-only; refusing host {host!r}")
        self._host, self._port, self._version = host, port, version
        self._token = token
        self._connect = connect or (lambda h, p, t: _SocketConn(h, p, t))

    def request(self, method: str, params: dict, *, request_id: str, timeout: float) -> dict:
        req = {"method": method, "params": params or {},
               "request_id": request_id, "timeout": timeout,
               "version": self._version}
        # Shared-secret auth (Finding 2). Loopback is not a UID boundary on Android:
        # both the daemon RPC and the companion socket require this token per-request.
        if self._token is not None:
            req["token"] = self._token
        line = _json.dumps(req)
        conn = self._connect(self._host, self._port, timeout)
        deadline = _time.monotonic() + timeout
        try:
            conn.sendline(line)
            while _time.monotonic() < deadline:
                raw = conn.readline()
                if not raw:
                    break
                try:
                    resp = _json.loads(raw)
                except _json.JSONDecodeError:
                    continue
                if resp.get("request_id") == request_id:
                    return resp
            return {"ok": False, "request_id": request_id,
                    "error": {"code": "timeout", "message": f"no response for {method!r}"}}
        finally:
            conn.close()

    def ping(self, *, timeout: float = 1.0) -> bool:
        rid = next_request_id()
        resp = self.request("ping", {}, request_id=rid, timeout=timeout)
        return bool(resp.get("ok"))


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
