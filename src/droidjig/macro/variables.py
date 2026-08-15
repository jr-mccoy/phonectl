"""Pure scoped-variable resolution + ${var} interpolation for macros."""
from __future__ import annotations

import re

_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
try:
    from droidjig.redact import SECRET_MASK
except Exception:  # pragma: no cover
    SECRET_MASK = "***"


class Scopes:
    _ORDER = ("runtime", "macro", "trigger", "secret")

    def __init__(self, *, runtime=None, macro=None, trigger=None, secret=None):
        self.runtime = dict(runtime or {})
        self.macro = dict(macro or {})
        self.trigger = dict(trigger or {})
        self.secret = dict(secret or {})

    def _scope(self, name):
        return getattr(self, name)

    def get(self, name, default=None):
        for s in self._ORDER:
            d = self._scope(s)
            if name in d:
                return d[name]
        return default

    def set(self, name, value, scope="runtime"):
        if scope not in self._ORDER:
            raise ValueError(f"unknown scope {scope!r}")
        self._scope(scope)[name] = value

    def is_secret(self, name):
        return name in self.secret and not any(
            name in self._scope(s) for s in ("runtime", "macro", "trigger"))


def interpolate(template, scopes) -> str:
    return _VAR.sub(lambda m: str(scopes.get(m.group(1), "")), template)


def redacted_view(scopes) -> dict:
    merged = {}
    for s in ("secret", "trigger", "macro", "runtime"):
        merged.update(scopes._scope(s))
    for k in scopes.secret:
        if scopes.is_secret(k):
            merged[k] = SECRET_MASK
    return merged
