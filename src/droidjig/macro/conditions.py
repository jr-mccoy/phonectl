# src/droidjig/macro/conditions.py — full pure condition vocabulary
from __future__ import annotations

import re

from droidjig import errors

_OPS = {"eq": lambda a, b: a == b, "ne": lambda a, b: a != b,
        "lt": lambda a, b: a < b, "gt": lambda a, b: a > b}


def evaluate(spec, ctx) -> bool:
    t = spec.get("type")
    snap = ctx.get("snapshot") or {}
    dev = ctx.get("device") or {}
    scopes = ctx.get("scopes")
    if t == "always":
        return True
    if t == "never":
        return False
    if t == "variable":
        return _OPS[spec.get("op", "eq")](scopes.get(spec["var"]), spec.get("value"))
    if t == "foreground_package":
        return snap.get("app") == spec.get("equals")
    if t == "screen_contains":
        texts = " ".join(e.get("text", "") or "" for e in snap.get("elements", []))
        return bool(re.search(spec["text_regex"], texts, re.I))
    if t == "selector_exists":
        from droidjig import ui_parser
        return bool(ui_parser.match_selector(snap.get("elements", []), spec["selector"]))
    if t == "device_unlocked":
        return snap.get("lock_state", "unlocked") == "unlocked"
    if t == "battery_min":
        return dev.get("battery", 0) >= spec["percent"]
    if t == "charging":
        return bool(dev.get("charging"))
    if t == "wifi_ssid":
        return dev.get("ssid") == spec.get("equals")
    if t == "network_available":
        return bool(dev.get("network", True))
    if t == "time_window":
        return _in_window(ctx.get("now"), spec.get("after"), spec.get("before"))
    if t == "risk_below":
        from droidjig import risk
        action = spec.get("action", {})
        order = ["low", "medium", "high", "critical"]
        level = risk.classify(snap, action.get("verb", ""), action.get("target", {})).get("level", "low")
        return order.index(level) < order.index(spec["level"])
    if t == "last_action_ok":
        return bool(scopes.get("__last_action_ok__", True))
    raise errors.TriggerError(f"unknown condition type {t!r}")


def all_hold(spec_list, ctx) -> bool:
    return all(evaluate(s, ctx) for s in (spec_list or []))


def _in_window(now, after, before):
    if now is None or after is None or before is None:
        return True
    hm = now.strftime("%H:%M") if hasattr(now, "strftime") else str(now)
    if before < after:            # window crosses midnight
        return hm >= after or hm <= before
    return after <= hm <= before
