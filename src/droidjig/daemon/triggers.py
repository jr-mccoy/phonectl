"""Daemon TriggerManager: drain the event bus, match macros, gate, enqueue runs."""
from __future__ import annotations

import time
from datetime import datetime

from droidjig.config import config_dir
from droidjig.macro import conditions as conditions_mod
from droidjig.macro import limits as limits_mod
from droidjig.macro import registry as registry_mod
from droidjig.macro import scheduler as scheduler_mod
from droidjig.macro import triggers as triggers_mod
from droidjig.macro import variables as V

# Map from bus event type (underscore) -> set of dotted trigger type(s) it satisfies.
#
# The event bus (daemon/events.py) uses underscore names (e.g. "notification_posted"),
# but the macro trigger vocabulary uses dotted names (e.g. "notification.posted") so
# that user-facing macro docs are human-readable.  TriggerManager.step() normalizes
# bus names to dotted names before calling triggers_mod.matches().
#
# "ui_changed": the bus does not distinguish element/text/activity granularity, so a
# single ui_changed event is a candidate for all UI trigger types.  The trigger's own
# filters (text_regex, selector, …) do the actual discrimination.
#
# Dotted names that are ALREADY dotted (legacy test fixtures) pass through unchanged
# via the CANDIDATES.get(bus_type, {bus_type}) fallback — so existing unit tests keep
# working with no changes.
_BUS_TO_DOTTED: dict[str, frozenset[str]] = {
    "notification_posted": frozenset({"notification.posted"}),
    "notification_removed": frozenset({"notification.removed"}),
    "clipboard_changed": frozenset({"clipboard.changed"}),
    "ui_changed": frozenset({
        "ui.element_appears", "ui.text_appears", "ui.element_disappears",
        "activity.changed", "app.opened", "app.closed",
    }),
}


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
            bus_type = event.get("type", "")
            # Resolve the set of dotted trigger-type names that this bus event can satisfy.
            # Dotted names (legacy test fixtures) pass through unchanged via the fallback.
            candidate_dotted = _BUS_TO_DOTTED.get(bus_type, frozenset({bus_type}))
            for macro in macros:
                if not triggers_mod.is_event_driven(macro.trigger):
                    continue
                trigger_type = macro.trigger.get("type")
                if trigger_type not in candidate_dotted:
                    continue
                # Present the event to the pure matcher with the dotted type it expects.
                # Use a shallow copy so we don't mutate the shared event dict.
                normalized_event = dict(event, type=trigger_type)
                if not triggers_mod.matches(macro.trigger, normalized_event):
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
                # Pass the dotted trigger type (macro-spec vocab) to the engine, not the raw bus name.
                # unattended=True: auto-fired macros must stay gated (D11) — no grant → confirmation_required.
                self._engine.run(macro, trigger=trigger_type, unattended=True,
                                 scopes=V.Scopes(macro=dict(macro.variables),
                                                 trigger=event.get("data", {})))
                fired.append(macro.name)
        return fired


class Scheduler:
    """Fire scheduled macros (schedule.interval, schedule.time) when due."""

    def __init__(self, engine, *, registry=registry_mod, next_fire=scheduler_mod.next_fire,
                 now=datetime.now):
        self._engine = engine
        self._registry = registry
        self._next_fire = next_fire
        self._now = now
        self._armed = {}  # name -> absolute fire time (seconds timestamp)

    def due(self, now_dt=None) -> list:
        now_dt = now_dt or self._now()
        fired = []
        for macro in self._registry.list_enabled():
            if not (macro.trigger and macro.trigger.get("type", "").startswith("schedule")):
                continue
            delay = self._next_fire(macro.trigger, now=now_dt)
            if delay is None:
                continue
            armed = self._armed.get(macro.name)
            target = now_dt.timestamp() + delay
            if armed is None:
                # First call: arm the macro (set the fire time)
                self._armed[macro.name] = target
                continue
            if now_dt.timestamp() >= armed:
                # unattended=True: scheduler fires must stay gated (D11) — no grant → confirmation_required.
                self._engine.run(macro, trigger=macro.trigger["type"], unattended=True)
                self._armed[macro.name] = now_dt.timestamp() + (delay or 0)
                fired.append(macro.name)
        return fired
