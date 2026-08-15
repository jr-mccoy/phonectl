"""Macro-run records: lineage + MacroRun summary, layered on runs.jsonl."""
from __future__ import annotations

import json
import time

from phonectl import state
from phonectl.config import config_dir


def _path():
    return config_dir() / "runs.jsonl"


def macro_run_record(state, macro, *, trigger="manual", now=time.time) -> dict:
    return {
        "kind": "macro_run",
        "run_id": state["run_id"],
        "macro_name": macro.name,
        "trigger": trigger,
        "outcome": state.get("outcome", "ok"),
        "steps_run": state.get("steps_run", 0),
        "started_at": state.get("started_at"),
        "ended_at": now(),
        "cancelled": state.get("cancelled", False),
    }


def append(record) -> None:
    try:
        from phonectl.daemon import records as drec  # one writer when the daemon ships
        drec.append(record)
        return
    except Exception:
        pass
    with open(_path(), "a") as f:
        f.write(json.dumps(record) + "\n")


def read(kind=None, limit=None) -> list:
    rows = state.read_jsonl(_path())
    if kind is not None:
        rows = [r for r in rows if r.get("kind") == kind]
    return rows[-limit:] if limit else rows
