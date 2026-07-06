"""Durable run records (runs.jsonl): one record per daemon action, layered on audit v2."""
from __future__ import annotations

import json
import time

from phonectl.config import config_dir


def _path():
    return config_dir() / "runs.jsonl"


def build_record(env, params, *, action_id, now=time.time, matched_i=None, context=None) -> dict:
    """Build a durable run record for one daemon action.

    Tagged ``kind="action"`` so ``macro.memory.capture_from_runs`` recognizes it. When the target
    carried a ``selector`` and it resolved to a concrete element, ``matched_i`` is threaded into
    the (copied) target so the selector-library can learn selector -> index; explicit-index or
    coordinate targets get no ``matched_i``. ``context`` (package/app_version/locale) keys the
    learned selector; omitted when None.
    """
    ok = bool(env.get("ok"))
    risk = None
    if "risk_level" in env:
        risk = {
            "risk_level": env.get("risk_level"),
            "decision": env.get("decision"),
            "reasons": env.get("reasons"),
        }
    target = params.get("target")
    if (matched_i is not None and isinstance(target, dict)
            and target.get("selector") is not None):
        target = {**target, "matched_i": matched_i}
    rec = {
        "ts": now(),
        "action_id": action_id,
        "kind": "action",
        "parent_task_id": params.get("parent_task_id"),
        "request_id": env.get("request_id"),
        "verb": params.get("verb"),
        "target": target,
        "provider": env.get("provider"),
        "snapshot_before": None,
        "snapshot_after": env.get("data") if ok else None,
        "risk": risk,
        "retries": 0,
        "outcome": "ok" if ok else env.get("error", {}).get("code", "error"),
        "user_approved": bool(params.get("yes", False)),
    }
    if context is not None:
        rec["context"] = context
    return rec


def append(record: dict) -> None:
    with open(_path(), "a") as f:
        f.write(json.dumps(record) + "\n")


def read(limit=None) -> list:
    p = _path()
    if not p.exists():
        return []
    rows = [json.loads(line) for line in p.read_text().splitlines() if line.strip()]
    return rows[-limit:] if limit else rows
