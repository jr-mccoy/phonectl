"""Companion-APK transport seam — request/response with request-id + stale-response protection."""
from __future__ import annotations

import uuid
from typing import Protocol, runtime_checkable


def next_request_id() -> str:
    return uuid.uuid4().hex


@runtime_checkable
class Transport(Protocol):
    def request(self, method: str, params: dict, *, request_id: str, timeout: float) -> dict: ...
    def ping(self, *, timeout: float = 1.0) -> bool: ...


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
