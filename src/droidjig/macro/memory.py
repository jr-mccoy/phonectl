# src/droidjig/macro/memory.py
"""Narrow, redacted, user-controlled memory stores (strategy §25, D12)."""
from __future__ import annotations


from droidjig import state
from droidjig.config import config_dir

try:
    from droidjig.redact import redact_text
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
    return state.read_json(_dir() / f"{store}.json", {})


def write(store, data) -> None:
    _check(store)
    state.write_json(_dir() / f"{store}.json", _redact(data))


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


_RETRYABLE = {"busy", "rate_limited", "observe_failed", "stale_snapshot"}


def capture_selector(record) -> None:
    if record.get("outcome") != "ok":
        return
    target = record.get("target") or {}
    sel = target.get("selector")
    if not sel or "matched_i" not in target:
        return
    ctx = record.get("context") or {}
    key = f"{ctx.get('package', '?')}|{ctx.get('app_version', '?')}|{ctx.get('locale', '?')}"
    update("selectors", key, {"selector": sel, "matched_i": target["matched_i"]})


def capture_failure(record) -> None:
    if record.get("outcome") not in _RETRYABLE:
        return
    key = f"{record.get('verb', '?')}|{record.get('outcome')}"
    failures = read("failures")
    failures[key] = failures.get(key, 0) + 1
    write("failures", failures)


def capture_from_runs(records) -> None:
    for r in records:
        if r.get("kind") == "action":
            capture_selector(r)
            capture_failure(r)
