"""Event bus — monotonic-seq publish + cursor-based poll (no I/O, no threads here)."""
from __future__ import annotations

import itertools
import threading
import time

EVENT_TYPES = frozenset({
    "ui_changed", "notification_posted",
    "action_started", "action_finished", "lifecycle",
})


class EventBus:
    def __init__(self, *, now=time.time) -> None:
        self._now = now
        self._seq = itertools.count(1)
        self._events: list[dict] = []
        self._cv = threading.Condition()

    @property
    def latest_seq(self) -> int:
        return self._events[-1]["seq"] if self._events else 0

    def publish(self, type: str, data: dict, *, source: str) -> dict:
        if type not in EVENT_TYPES:
            raise ValueError(f"unknown event type {type!r}")
        event = {
            "seq": next(self._seq),
            "type": type,
            "ts": self._now(),
            "source": source,
            "data": dict(data or {}),
        }
        with self._cv:
            self._events.append(event)
            self._cv.notify_all()
        return event

    def poll(self, since: int = 0, *, max: int = 100) -> dict:
        with self._cv:
            return self._poll_locked(since, max=max)

    def wait(self, since: int = 0, *, max: int = 100, timeout_ms: int = 1000) -> dict:
        deadline = time.monotonic() + (timeout_ms if timeout_ms > 0 else 0) / 1000.0
        with self._cv:
            out = self._poll_locked(since, max=max)
            while not out["events"] and timeout_ms > 0:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._cv.wait(remaining)
                out = self._poll_locked(since, max=max)
            return out

    def _poll_locked(self, since: int = 0, *, max: int = 100) -> dict:
        newer = [e for e in self._events if e["seq"] > since][:max]
        cursor = newer[-1]["seq"] if newer else since
        return {"events": newer, "cursor": cursor}
