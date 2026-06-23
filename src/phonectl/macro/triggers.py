"""Pure trigger matching: event-bus envelope / snapshot -> bool."""
from __future__ import annotations

import re

from phonectl import errors

EVENT_TYPES = {
    "notification.posted", "notification.removed", "ui.element_appears",
    "ui.element_disappears", "ui.text_appears", "app.opened", "app.closed",
    "activity.changed", "clipboard.changed", "power.charging_changed",
    "power.battery_level", "connectivity.wifi",
}
SCHEDULE_TYPES = {"schedule.time", "schedule.interval"}
ALL_TYPES = EVENT_TYPES | SCHEDULE_TYPES | {"manual"}


def _check(spec):
    t = spec.get("type")
    if t not in ALL_TYPES:
        raise errors.TriggerError(f"unknown trigger type {t!r}")
    return t


def is_event_driven(spec):
    return _check(spec) in EVENT_TYPES


def is_scheduled(spec):
    return _check(spec) in SCHEDULE_TYPES


def is_manual(spec):
    return _check(spec) == "manual"


def matches(spec, event) -> bool:
    t = _check(spec)
    if t not in EVENT_TYPES or event.get("type") != t:
        return False
    data = event.get("data", {}) or {}
    f = spec.get("filters", {}) or {}
    if "package" in f and data.get("package") != f["package"]:
        return False
    if "package_in" in f and data.get("package") not in f["package_in"]:
        return False
    if "text_regex" in f and not re.search(f["text_regex"], data.get("text", "") or "", re.I):
        return False
    if "selector" in f and data.get("selector") != f["selector"]:
        return False
    if "min_percent" in f and not (data.get("percent", 0) <= f["min_percent"]):
        return False
    if "ssid" in f and data.get("ssid") != f["ssid"]:
        return False
    return True
