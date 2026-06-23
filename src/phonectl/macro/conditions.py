"""Minimal pure condition evaluator (extended in Plan 6.2)."""
from __future__ import annotations

_OPS = {"eq": lambda a, b: a == b, "ne": lambda a, b: a != b,
        "lt": lambda a, b: a < b, "gt": lambda a, b: a > b}


def evaluate(spec, ctx) -> bool:
    t = spec.get("type")
    if t == "always":
        return True
    if t == "never":
        return False
    if t == "variable":
        scopes = ctx["scopes"]
        left = scopes.get(spec["var"])
        return _OPS[spec.get("op", "eq")](left, spec.get("value"))
    raise NotImplementedError(f"condition {t!r} lands in Plan 6.2")
