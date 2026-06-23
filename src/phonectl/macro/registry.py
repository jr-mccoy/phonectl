"""Persistence for enabled macros: $PHONECTL_HOME/macros/<name>.json."""
from __future__ import annotations

import json

from phonectl import errors
from phonectl.config import config_dir
from phonectl.macro import schema, scheduler, triggers


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
    (_dir() / f"{macro.name}.json").write_text(json.dumps({**doc, "enabled": True}))


def disable(name) -> None:
    p = _dir() / f"{name}.json"
    if p.exists():
        doc = json.loads(p.read_text())
        doc["enabled"] = False
        p.write_text(json.dumps(doc))


def all() -> list:
    out = []
    for p in sorted(_dir().glob("*.json")):
        out.append(json.loads(p.read_text()))
    return out


def list_enabled() -> list:
    macros = []
    for doc in all():
        if doc.get("enabled") and doc.get("trigger") and not triggers.is_manual(doc["trigger"]):
            # Strip the "enabled" state key before parsing — schema rejects unknown top-level keys
            fields = {k: v for k, v in doc.items() if k != "enabled"}
            macros.append(schema.parse(fields))
    return macros
