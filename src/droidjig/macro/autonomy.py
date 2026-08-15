# src/droidjig/macro/autonomy.py
"""Progressive-autonomy grant ledger + pure decide (confirm by default)."""
from __future__ import annotations

import json
import uuid

from droidjig import state
from droidjig.config import config_dir

_ORDER = ["low", "medium", "high", "critical"]


def _rank(level):
    return _ORDER.index(level)


def live_grants(records, *, now) -> list:
    state = {}  # id -> grant, updated in ledger order
    for r in records:
        if r.get("kind") == "grant":
            state[r["id"]] = r
        elif r.get("kind") == "revoke":
            if r.get("id"):
                state.pop(r["id"], None)
            if r.get("macro"):
                state = {gid: g for gid, g in state.items()
                         if g.get("macro") != r["macro"]}
    return [g for g in state.values()
            if not (g.get("expires_at") is not None and g["expires_at"] <= now)]


def decide(macro, action_risk, grants, *, now) -> str:
    covering = [g for g in grants
                if g.get("macro") == macro.name and _rank(g["max_risk"]) >= _rank(action_risk)]
    if action_risk == "critical":
        # critical always needs an explicit one-time human approval; allow only as far as confirm
        return "confirm" if any(g["max_risk"] == "critical" for g in covering) else "deny"
    if macro.policy.get("require_confirm"):
        return "confirm"
    return "allow" if covering else "confirm"


def _path():
    return config_dir() / "autonomy.jsonl"


def read_ledger() -> list:
    return state.read_jsonl(_path())


def append(record) -> None:
    with open(_path(), "a") as f:
        f.write(json.dumps(record) + "\n")


def grant(macro_name, *, max_risk, scope="all", expires_at=None, now, gen_id=None) -> dict:
    if max_risk not in _ORDER:
        raise ValueError(f"bad max_risk {max_risk!r}")
    rec = {"kind": "grant", "id": (gen_id or (lambda: "g_" + uuid.uuid4().hex))(),
           "macro": macro_name, "max_risk": max_risk, "scope": scope,
           "granted_at": now, "expires_at": expires_at}
    append(rec)
    return rec


def revoke(*, macro=None, grant_id=None, now) -> None:
    append({"kind": "revoke", "id": grant_id, "macro": macro, "revoked_at": now})


def list_live(*, now) -> list:
    return live_grants(read_ledger(), now=now)
