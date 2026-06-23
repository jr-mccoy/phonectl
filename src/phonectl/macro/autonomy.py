# src/phonectl/macro/autonomy.py
"""Progressive-autonomy grant ledger + pure decide (confirm by default)."""
from __future__ import annotations

import json

from phonectl.config import config_dir

_ORDER = ["low", "medium", "high", "critical"]


def _rank(level):
    return _ORDER.index(level)


def live_grants(records, *, now) -> list:
    grants = {}
    revoked_macros = set()
    revoked_ids = set()
    for r in records:
        if r.get("kind") == "grant":
            grants[r["id"]] = r
        elif r.get("kind") == "revoke":
            if r.get("id"):
                revoked_ids.add(r["id"])
            if r.get("macro"):
                revoked_macros.add(r["macro"])
    out = []
    for gid, g in grants.items():
        if gid in revoked_ids or g.get("macro") in revoked_macros:
            continue
        exp = g.get("expires_at")
        if exp is not None and exp <= now:
            continue
        out.append(g)
    return out


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
    p = _path()
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]


def append(record) -> None:
    with open(_path(), "a") as f:
        f.write(json.dumps(record) + "\n")
