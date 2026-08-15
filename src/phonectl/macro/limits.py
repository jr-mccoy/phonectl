"""Pure per-macro fire limits (cooldown + max_runs_per_hour), persisted history."""
from __future__ import annotations

from phonectl import state


def allow(name, macro_limits, *, now, history):
    macro_limits = macro_limits or {}
    history = history or []
    cooldown = macro_limits.get("cooldown_seconds")
    if cooldown and history and (now - max(history)) < cooldown:
        return False, "cooldown"
    per_hour = macro_limits.get("max_runs_per_hour")
    if per_hour is not None:
        recent = [t for t in history if now - t < 3600]
        if len(recent) >= per_hour:
            return False, "per_hour"
    return True, ""


def load(store_path) -> dict:
    return state.read_json(store_path, {})


def record(name, *, now, store_path) -> None:
    data = load(store_path)
    hist = [t for t in data.get(name, []) if now - t < 3600]
    hist.append(now)
    data[name] = hist
    state.write_json(store_path, data)
