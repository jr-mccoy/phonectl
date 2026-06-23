"""Pure schedule math: seconds until the next fire (no real clock, no sleep)."""
from __future__ import annotations

from datetime import timedelta


def _parse_hm(at):
    h, m = at.split(":")
    h, m = int(h), int(m)
    if not (0 <= h < 24 and 0 <= m < 60):
        raise ValueError(at)
    return h, m


def validate(spec) -> list:
    t = spec.get("type")
    if t == "schedule.time":
        try:
            _parse_hm(spec.get("at", ""))
        except (ValueError, AttributeError):
            return [f"invalid schedule.time 'at': {spec.get('at')!r}"]
        return []
    if t == "schedule.interval":
        if not isinstance(spec.get("every_seconds"), (int, float)) or spec["every_seconds"] <= 0:
            return ["schedule.interval requires a positive 'every_seconds'"]
        return []
    return []


def next_fire(spec, *, now):
    t = spec.get("type")
    if t == "schedule.interval":
        return float(spec["every_seconds"])
    if t != "schedule.time":
        return None
    h, m = _parse_hm(spec["at"])
    target = now.replace(hour=h, minute=m, second=0, microsecond=0)
    weekdays = spec.get("weekdays")
    if target <= now:
        target = target + timedelta(days=1)
    if weekdays:
        for _ in range(8):
            if target.weekday() in weekdays and target > now:
                break
            target = target + timedelta(days=1)
    return (target - now).total_seconds()
