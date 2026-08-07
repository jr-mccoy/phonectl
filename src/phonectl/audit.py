from __future__ import annotations

import json
import time
from pathlib import Path
from phonectl import redact
from phonectl.config import config_dir, load


def stop_file() -> Path:
    return config_dir() / "STOP"


def engage_stop(note: str = "") -> None:
    """Write the STOP sentinel (kill switch). Safe to call when already stopped."""
    stop_file().write_text(note)


def clear_stop() -> bool:
    """Remove the STOP sentinel. Returns True if a sentinel was present.

    Clearing STOP is an out-of-band, human-only recovery step (host CLI or the
    companion notification/tile). It is deliberately NOT exposed on any
    agent-facing surface (MCP tool, daemon RPC) — a kill switch the agent can
    clear is not a kill switch.
    """
    p = stop_file()
    if p.exists():
        p.unlink()
        return True
    return False


def kill_switch_active(*, extra_checks=()) -> bool:
    if stop_file().exists():
        return True
    for check in extra_checks:
        try:
            if check():
                return True
        except Exception:
            continue
    return False


def audit_level(cfg: dict | None = None) -> str:
    cfg = load() if cfg is None else cfg
    return cfg.get("audit_level", "redacted")


def log_action(
    verb: str,
    target: dict,
    result: dict,
    request_id: str | None = None,
    cfg: dict | None = None,
    outcome: str = "ok",
) -> None:
    level = audit_level(cfg)
    if level == "none":
        return
    rec = {
        "ts": time.time(),
        "verb": verb,
        "request_id": request_id,
        "app": (result.get("app", {}) or {}).get("package", ""),
        "hash": result.get("hash", ""),
        "outcome": outcome,
    }
    if level == "full":
        rec["target"] = target
    elif level == "redacted":
        rec["target"] = redact.redact_value(target)
    with open(config_dir() / "actions.jsonl", "a") as f:
        f.write(json.dumps(rec) + "\n")


def _log_path():
    return config_dir() / "actions.jsonl"


def read_entries(limit: int | None = None) -> list[dict]:
    p = _log_path()
    if not p.exists():
        return []
    entries = [json.loads(line) for line in p.read_text().splitlines() if line.strip()]
    return entries[-limit:] if limit else entries


def purge() -> int:
    p = _log_path()
    if not p.exists():
        return 0
    n = len(read_entries())
    p.unlink()
    return n


def export(path: str, *, redacted: bool = True) -> str:
    entries = read_entries()
    if redacted:
        for entry in entries:
            if "target" in entry:
                entry["target"] = redact.redact_value(entry["target"])
    with open(path, "w") as f:
        json.dump(entries, f, indent=2)
    return path
