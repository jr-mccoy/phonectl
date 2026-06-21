import json
import time
from phonectl.config import config_dir


def kill_switch_active() -> bool:
    return (config_dir() / "STOP").exists()


def log_action(
    verb: str,
    target: dict,
    result: dict,
    request_id: str | None = None,
    cfg: dict | None = None,
) -> None:
    rec = {
        "ts": time.time(),
        "verb": verb,
        "target": target,
        "request_id": request_id,
        "app": (result.get("app", {}) or {}).get("package", ""),
        "hash": result.get("hash", ""),
    }
    with open(config_dir() / "actions.jsonl", "a") as f:
        f.write(json.dumps(rec) + "\n")
