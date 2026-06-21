"""Per-provider capability discovery helpers."""
from __future__ import annotations

CAPABILITY_KEYS = (
    "observe_ui_tree",
    "observe_screenshot",
    "act_tap",
    "act_type",
    "act_key",
    "launch_app",
    "send_intent",
    "read_notifications",
    "reply_notifications",
    "read_clipboard",
    "write_clipboard",
    "write_secure_settings",
    "persistent_events",
    "requires_adb",
    "requires_accessibility",
    "requires_notification_listener",
)


def make(**flags) -> dict:
    unknown = set(flags) - set(CAPABILITY_KEYS)
    if unknown:
        raise ValueError(f"unknown capability keys: {sorted(unknown)}")
    return {key: bool(flags.get(key, False)) for key in CAPABILITY_KEYS}


def describe(caps: dict) -> str:
    have = [k for k in CAPABILITY_KEYS if caps.get(k)]
    miss = [k for k in CAPABILITY_KEYS if not caps.get(k)]
    return f"available: {', '.join(have) or '(none)'}\nunavailable: {', '.join(miss) or '(none)'}"
