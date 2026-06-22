"""Pure multi-signal risk classifier for observed UI snapshots."""
from __future__ import annotations

import re

_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}
_OTP_RE = re.compile(r"\b\d{4,8}\b")

DEFAULT_KEYWORDS = {
    "payment_keyword": (
        "pay",
        "payment",
        "purchase",
        "transfer",
        "checkout",
        "card number",
        "cvv",
        "bank",
        "buy",
    ),
    "destructive_keyword": (
        "factory reset",
        "erase all",
        "wipe",
        "delete account",
        "uninstall",
        "clear data",
        "force stop",
    ),
    "install_keyword": ("install", "allow", "grant", "subscribe", "send"),
}

_SIGNAL_LEVEL = {
    "payment_keyword": "critical",
    "destructive_keyword": "critical",
    "install_keyword": "high",
    "guarded_package": "high",
    "password_field": "high",
    "otp_like_content": "medium",
    "high_risk_verb": "high",
    "critical_verb": "critical",
}

HIGH_RISK_VERBS = frozenset({"packages_stop", "intent_broadcast"})
CRITICAL_VERBS = frozenset({"packages_clear"})


def _bump(level: str, candidate: str) -> str:
    return candidate if _ORDER[candidate] > _ORDER[level] else level


def classify(
    snapshot,
    verb,
    target,
    *,
    guarded_packages=(),
    keywords=DEFAULT_KEYWORDS,
) -> dict:
    level = "low"
    reasons = []
    seen = set()

    def add(signal: str, detail: str) -> None:
        nonlocal level
        if signal in seen:
            return
        seen.add(signal)
        reasons.append({"signal": signal, "detail": detail})
        level = _bump(level, _SIGNAL_LEVEL[signal])

    package = (snapshot.get("app", {}) or {}).get("package", "")
    if package and any(package.startswith(prefix) for prefix in guarded_packages):
        add("guarded_package", f"foreground package {package} is guarded")

    for element in snapshot.get("elements", []):
        if element.get("password"):
            add("password_field", "a password field is present on screen")
        text = element.get("text", "") or ""
        blob = f"{text} {element.get('content_desc', '') or ''}".lower()
        for signal, words in keywords.items():
            if any(word in blob for word in words):
                add(signal, f"screen text matches {signal}")
        if _OTP_RE.search(text):
            add("otp_like_content", "screen shows an OTP-like code")

    if verb in CRITICAL_VERBS:
        add("critical_verb", f"{verb} is a critical-risk verb")
    if verb in HIGH_RISK_VERBS:
        add("high_risk_verb", f"{verb} is a high-risk verb")

    return {"level": level, "reasons": reasons}
