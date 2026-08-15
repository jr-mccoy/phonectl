"""Persistence for enabled macros: $DROIDJIG_HOME/macros/<name>.json."""
from __future__ import annotations

import json

from droidjig import errors, state
from droidjig.config import config_dir
from droidjig.macro import schema, scheduler, triggers


def _dir():
    d = config_dir() / "macros"
    d.mkdir(parents=True, exist_ok=True)
    return d


def enable(doc) -> None:
    macro = schema.parse(doc)  # raises MacroValidationError on bad doc
    if macro.trigger is not None:
        triggers._check(macro.trigger)  # raises TriggerError on unknown type
        if triggers.is_scheduled(macro.trigger):
            errs = scheduler.validate(macro.trigger)
            if errs:
                raise errors.MacroValidationError("; ".join(errs))
    state.write_json(_dir() / f"{macro.name}.json", {**doc, "enabled": True})


def disable(name) -> None:
    p = _dir() / f"{name}.json"
    if p.exists():
        doc = state.read_json(p, {})
        doc["enabled"] = False
        state.write_json(p, doc)


def all() -> list:
    out = []
    for p in sorted(_dir().glob("*.json")):
        doc = state.read_json(p, {})
        if doc:
            out.append(doc)   # a torn/corrupt macro file is skipped, not fatal
    return out


def list_enabled() -> list:
    macros = []
    for doc in all():
        if doc.get("enabled") and doc.get("trigger") and not triggers.is_manual(doc["trigger"]):
            # Strip the "enabled" state key before parsing — schema rejects unknown top-level keys
            fields = {k: v for k, v in doc.items() if k != "enabled"}
            macros.append(schema.parse(fields))
    return macros
