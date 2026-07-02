"""Notification provider — companion NotificationListenerService or read-only Termux:API."""
from __future__ import annotations

import time

from phonectl import capabilities as caps_mod
from phonectl import errors
from phonectl.providers.transport import next_request_id, raise_companion_error


def parse_notification(raw: dict, *, source: str) -> dict:
    if source == "termux":
        return {
            "key": str(raw.get("id", "")),
            "package": raw.get("packageName") or raw.get("package", ""),
            "title": raw.get("title", "") or "",
            "text": raw.get("content") or raw.get("text", "") or "",
            "category": raw.get("category"),
            "post_time": int(raw.get("when", 0) or 0),
            "actions": [],
            "can_reply": False,
            "can_dismiss": False,
        }
    actions = raw.get("actions", []) or []
    can_reply = any(a.get("remote_input") for a in actions)
    return {
        "key": raw.get("key", ""),
        "package": raw.get("package", ""),
        "title": raw.get("title", "") or "",
        "text": raw.get("text", "") or "",
        "category": raw.get("category"),
        "post_time": int(raw.get("post_time", 0) or 0),
        "actions": [a.get("title", "") for a in actions],
        "can_reply": bool(can_reply),
        "can_dismiss": True,
    }


def _matches(n, package, title_contains, text_contains) -> bool:
    if package is not None and n["package"] != package:
        return False
    if title_contains is not None and title_contains not in n["title"]:
        return False
    if text_contains is not None and text_contains not in n["text"]:
        return False
    return True


class NotificationsProvider:
    def __init__(self, transport=None, termux=None, *, timeout: float = 2.0) -> None:
        self._t = transport
        self._termux = termux
        self._timeout = timeout

    def _companion_ok(self) -> bool:
        try:
            return self._t is not None and bool(self._t.ping())
        except Exception:  # noqa: BLE001
            return False

    def _termux_ok(self) -> bool:
        return self._termux is not None and bool(self._termux.is_available())

    def is_available(self) -> bool:
        return self._companion_ok() or self._termux_ok()

    def capabilities(self) -> dict:
        if self._companion_ok():
            return caps_mod.make(observe_notifications=True, notifications_wait=True,
                                 notifications_reply=True, notifications_dismiss=True)
        if self._termux_ok():
            return caps_mod.make(observe_notifications=True)
        return caps_mod.make()

    def _call(self, method: str, params: dict | None = None) -> dict:
        rid = next_request_id()
        resp = self._t.request(method, params or {}, request_id=rid, timeout=self._timeout)
        if resp.get("request_id") != rid:
            raise errors.ObserveError("stale companion response for notifications")
        if not resp.get("ok"):
            raise_companion_error(resp.get("error", {}))
        return resp.get("data", {})

    def list(self, package: str | None = None) -> list:
        if self._companion_ok():
            data = self._call("notifications_list", {})
            items = [parse_notification(r, source="companion")
                     for r in data.get("notifications", [])]
        elif self._termux_ok():
            items = [parse_notification(r, source="termux")
                     for r in self._termux.notifications_list()]
        else:
            raise errors.CapabilityUnavailableError("no notification source available")
        if package is not None:
            items = [n for n in items if n["package"] == package]
        return items

    def wait(self, *, package=None, title_contains=None, text_contains=None,
             timeout: float = 30.0, poll: float = 1.0,
             _clock=time.monotonic, _sleep=time.sleep):
        deadline = _clock() + timeout
        while True:
            for n in self.list():
                if _matches(n, package, title_contains, text_contains):
                    return n
            if _clock() >= deadline:
                return None
            _sleep(poll)

    def reply(self, key: str, text: str) -> dict:
        if not self._companion_ok():
            raise errors.CapabilityUnavailableError(
                "notification reply requires the companion APK (Termux:API cannot reply)")
        return self._call("notifications_reply", {"key": key, "text": text})

    def dismiss(self, key: str) -> dict:
        if not self._companion_ok():
            raise errors.CapabilityUnavailableError(
                "notification dismiss requires the companion APK")
        return self._call("notifications_dismiss", {"key": key})
