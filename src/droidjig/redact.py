"""Pure redaction of sensitive strings for audit payloads."""
from __future__ import annotations

import re

_MASK = "[REDACTED]"
_PATTERNS = [
    re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
    re.compile(r"(?i)(?:token|access_token|code|key)=[^&\s]+"),
    re.compile(r"\b\d{13,19}\b"),
    re.compile(r"\+?\d[\d ()\-]{7,}\d"),
    re.compile(r"\b\d{4,8}\b"),
    # High-entropy bare tokens with no `key=` prefix (pairing tokens, API keys, hashes): a run of
    # >=20 word/hyphen chars containing at least one letter AND one digit. The mixed letter+digit
    # requirement keeps letters-only identifiers (class names, long words) and pure prose intact.
    re.compile(r"\b(?=[\w-]*[A-Za-z])(?=[\w-]*\d)[\w-]{20,}\b"),
]


def redact_text(s: str) -> str:
    out = s
    for pattern in _PATTERNS:
        out = pattern.sub(_MASK, out)
    return out


def redact_value(v):
    if isinstance(v, dict):
        return {k: redact_value(val) for k, val in v.items()}
    if isinstance(v, (list, tuple)):
        return [redact_value(x) for x in v]
    if isinstance(v, str):
        return redact_text(v)
    return v
