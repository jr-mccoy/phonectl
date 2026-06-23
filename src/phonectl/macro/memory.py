# src/phonectl/macro/memory.py
"""Narrow, redacted, user-controlled memory stores (strategy §25, D12)."""
from __future__ import annotations

import json

from phonectl.config import config_dir

try:
    from phonectl.redact import redact_text
except Exception:  # pragma: no cover - redact ships in 2.1
    def redact_text(s):
        return s

STORES = ("device", "apps", "prefs", "selectors", "failures")


def _dir():
    d = config_dir() / "memory"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _check(store):
    if store not in STORES:
        raise ValueError(f"unknown memory store {store!r}")


def _redact(value):
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {k: _redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v) for v in value]
    return value


def read(store) -> dict:
    _check(store)
    p = _dir() / f"{store}.json"
    if not p.exists():
        return {}
    return json.loads(p.read_text())


def write(store, data) -> None:
    _check(store)
    (_dir() / f"{store}.json").write_text(json.dumps(_redact(data)))


def update(store, key, value) -> None:
    data = read(store)
    data[key] = _redact(value)
    write(store, data)


def export() -> dict:
    return {s: read(s) for s in STORES if (_dir() / f"{s}.json").exists()}


def delete(store=None) -> None:
    targets = [store] if store else list(STORES)
    for s in targets:
        _check(s)
        p = _dir() / f"{s}.json"
        if p.exists():
            p.unlink()
