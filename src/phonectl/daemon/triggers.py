"""Daemon TriggerManager: drain the event bus, match macros, gate, enqueue runs."""
from __future__ import annotations

import time

from phonectl.config import config_dir
from phonectl.macro import conditions as conditions_mod
from phonectl.macro import limits as limits_mod
from phonectl.macro import registry as registry_mod
from phonectl.macro import triggers as triggers_mod
from phonectl.macro import variables as V


class TriggerManager:
    def __init__(self, engine, *, poll, registry=registry_mod, limits=limits_mod,
                 conditions=conditions_mod, now=time.time, history_path=None):
        self._engine = engine
        self._poll = poll
        self._registry = registry
        self._limits = limits
        self._conditions = conditions
        self._now = now
        self._cursor = 0
        self._history_path = history_path or (config_dir() / "macro_runs_history.json")

    def step(self) -> list:
        batch = self._poll(self._cursor, 100)
        self._cursor = batch.get("cursor", self._cursor)
        fired = []
        macros = self._registry.list_enabled()
        for event in batch.get("events", []):
            for macro in macros:
                if not triggers_mod.is_event_driven(macro.trigger):
                    continue
                if not triggers_mod.matches(macro.trigger, event):
                    continue
                ctx = {"scopes": V.Scopes(macro=dict(macro.variables)),
                       "snapshot": event.get("data", {}).get("snapshot", {}),
                       "device": event.get("data", {}).get("device", {}), "now": None}
                if not self._conditions.all_hold(macro.conditions, ctx):
                    continue
                now = self._now()
                hist = self._limits.load(self._history_path).get(macro.name, [])
                ok, _ = self._limits.allow(macro.name, macro.limits, now=now, history=hist)
                if not ok:
                    continue
                self._limits.record(macro.name, now=now, store_path=self._history_path)
                self._engine.run(macro, trigger=event["type"],
                                 scopes=V.Scopes(macro=dict(macro.variables),
                                                 trigger=event.get("data", {})))
                fired.append(macro.name)
        return fired
