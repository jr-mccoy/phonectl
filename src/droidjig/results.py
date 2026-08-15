"""Structured result envelope helpers for droidjig surfaces."""
from __future__ import annotations

from droidjig import errors


def ok(*, capability=None, provider=None, data=None, **extra) -> dict:
    out = {"ok": True}
    if capability is not None:
        out["capability"] = capability
    if provider is not None:
        out["provider"] = provider
    if data is not None:
        out["data"] = data
    out.update(extra)
    return out


def err(error, *, capability=None, user_action=None, **extra) -> dict:
    if isinstance(error, errors.DroidjigError):
        code = error.code
        message = str(error)
        retryable = error.retryable
        requires_user = error.requires_user
    else:
        code, message = error
        retryable = False
        requires_user = False
    body = {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
            "retryable": retryable,
            "requires_user": requires_user,
            "user_action": user_action,
        },
    }
    if capability is not None:
        body["capability"] = capability
    body.update(extra)
    return body
