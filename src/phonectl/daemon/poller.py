"""Step-wise provider -> EventBus drainer. No threads or sleeps; driven by drain_once()."""
from __future__ import annotations


class EventPoller:
    def __init__(self, bus, *, ui_source=None, notif_source=None) -> None:
        self._bus = bus
        self._ui = ui_source
        self._notif = notif_source
        self._ui_cursor = 0
        self._seen_keys: set = set()

    def drain_once(self, *, max_events: int = 50) -> int:
        published = 0
        if self._ui is not None:
            batch = self._ui.poll_events(self._ui_cursor, max_events=max_events)
            for ev in batch.get("events", []):
                self._bus.publish("ui_changed", ev, source="accessibility")
                published += 1
            self._ui_cursor = batch.get("cursor", self._ui_cursor)
        if self._notif is not None:
            for note in self._notif.list():
                key = note.get("key")
                if key in self._seen_keys:
                    continue
                self._seen_keys.add(key)
                self._bus.publish("notification_posted", note, source="notifications")
                published += 1
        return published

    def tick(self, *, max_events: int = 50) -> int:
        return self.drain_once(max_events=max_events)
