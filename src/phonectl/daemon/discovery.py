"""Daemon discovery: publish/read/remove $PHONECTL_HOME/daemon.json and probe it."""
from __future__ import annotations

import json
import secrets
from pathlib import Path

from phonectl.config import config_dir

LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def new_token() -> str:
    """A per-daemon shared secret, written into daemon.json.

    Loopback is not a trust boundary on Android (any local app with INTERNET can
    connect), so RPCs must carry this token. daemon.json lives under
    $PHONECTL_HOME — the Termux app's private storage — so other apps (different
    UIDs) cannot read the token, but the phonectl CLI/MCP (same UID) can.
    """
    return secrets.token_hex(16)


def _path() -> Path:
    return config_dir() / "daemon.json"


def write(info: dict) -> Path:
    host = info.get("host", "127.0.0.1")
    if host not in LOOPBACK:
        raise ValueError(f"daemon is loopback-only; refusing host {host!r}")
    p = _path()
    p.write_text(json.dumps(info))
    return p


def read() -> dict | None:
    p = _path()
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except (ValueError, OSError):
        return None


def remove() -> None:
    p = _path()
    if p.exists():
        p.unlink()


def discover(*, ping) -> dict | None:
    info = read()
    if not info:
        return None
    try:
        if ping(info["host"], info["port"]):
            return info
    except Exception:
        return None
    return None
