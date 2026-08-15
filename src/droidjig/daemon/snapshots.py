"""Snapshot cache — monotonic ids, invalidation, foreground checks (no I/O)."""
from __future__ import annotations

import itertools
import time

from droidjig import errors


class SnapshotCache:
    def __init__(self, *, id_counter=None, now=time.time) -> None:
        self._counter = id_counter if id_counter is not None else itertools.count(1)
        self._now = now
        self._store: dict[str, dict] = {}
        self._current_id: str | None = None

    def put(self, snapshot: dict) -> str:
        snapshot_id = f"snap_{next(self._counter)}"
        self._store[snapshot_id] = snapshot
        self._current_id = snapshot_id
        return snapshot_id

    def get(self, snapshot_id: str) -> dict | None:
        return self._store.get(snapshot_id)

    @property
    def current_id(self) -> str | None:
        return self._current_id

    def foreground_of(self, snapshot_id: str) -> str | None:
        snap = self._store.get(snapshot_id)
        if not snap:
            return None
        return (snap.get("app") or {}).get("package")

    @property
    def current_foreground(self) -> str | None:
        if self._current_id is None:
            return None
        return self.foreground_of(self._current_id)

    def validate(self, expected_id: str | None, *, current_foreground: str | None) -> None:
        if expected_id is None:
            return
        if expected_id != self._current_id:
            raise errors.StaleSnapshotError(
                f"snapshot {expected_id} is stale (current is {self._current_id})"
            )
        pinned_fg = self.foreground_of(expected_id)
        if (pinned_fg is not None and current_foreground is not None
                and pinned_fg != current_foreground):
            raise errors.StaleSnapshotError(
                f"foreground changed since {expected_id}: {pinned_fg} -> {current_foreground}"
            )
