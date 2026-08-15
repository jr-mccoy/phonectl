"""droidjig macro engine: declarative, auditable automations (Phase 6)."""

PHONE_VERBS = frozenset({
    "tap", "type", "set_text", "swipe", "scroll_until", "launch", "key",
    "intent", "clipboard_read", "clipboard_write", "clipboard_clear",
    "notification_reply", "notification_dismiss",
})

CONTROL_STEPS = frozenset({
    "if", "switch", "for_each", "loop", "retry", "race", "try",
    "wait", "set", "confirm", "stop", "audit_note",
})
