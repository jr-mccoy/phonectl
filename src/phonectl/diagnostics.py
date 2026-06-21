"""Redacted diagnostics bundle helpers."""
from __future__ import annotations

import json
import zipfile

from phonectl.config import config_dir

_SECRET_SUBSTRINGS = ("code", "token", "secret", "password", "key", "pair")


def _is_secret(name: str) -> bool:
    low = name.lower()
    return any(s in low for s in _SECRET_SUBSTRINGS)


def redact_config(cfg: dict) -> dict:
    out = {}
    for k, v in cfg.items():
        if isinstance(v, dict):
            out[k] = redact_config(v)
        elif _is_secret(str(k)):
            out[k] = "***"
        else:
            out[k] = v
    return out


def _audit_tail(n: int = 20) -> list:
    path = config_dir() / "actions.jsonl"
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines()[-n:]:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        out.append({k: rec[k] for k in ("ts", "verb", "app", "hash") if k in rec})
    return out


def collect(backend, cfg) -> dict:
    mdns_fn = getattr(backend, "mdns_services", None)
    return {
        "config": redact_config(cfg),
        "capabilities": backend.capabilities() if hasattr(backend, "capabilities") else {},
        "state": backend.get_state(),
        "adb_version": backend.adb_version() if hasattr(backend, "adb_version") else "",
        "devices": backend.devices() if hasattr(backend, "devices") else "",
        "mdns": mdns_fn() if mdns_fn is not None else [],
        "host_shim": hasattr(backend, "host_shim_runner"),
        "audit_tail": _audit_tail(),
    }


def bundle(path, backend, cfg) -> str:
    data = collect(backend, cfg)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(data, indent=2))
        z.writestr("adb-version.txt", data["adb_version"])
        z.writestr("adb-devices.txt", data["devices"])
    return path
