import json
import time
from phonectl import redact
from phonectl.config import config_dir, load


def kill_switch_active() -> bool:
    return (config_dir() / "STOP").exists()


def audit_level(cfg: dict | None = None) -> str:
    cfg = load() if cfg is None else cfg
    return cfg.get("audit_level", "redacted")


def log_action(
    verb: str,
    target: dict,
    result: dict,
    request_id: str | None = None,
    cfg: dict | None = None,
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
    }
    if level == "full":
        rec["target"] = target
    elif level == "redacted":
        rec["target"] = redact.redact_value(target)
    with open(config_dir() / "actions.jsonl", "a") as f:
        f.write(json.dumps(rec) + "\n")
