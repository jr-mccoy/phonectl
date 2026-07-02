import json
import os
from pathlib import Path

DEFAULTS: dict = {
    "companion_host": "127.0.0.1",
    "companion_port": None,
    "companion_token": None,    # shared secret paired from the companion APK UI (Finding 2)
    "companion_timeout": 2.0,
    "daemon_host": "127.0.0.1",
    "daemon_autostart": False,
    # async job model (daemon)
    "act_timeout": 60.0,        # block-and-poll wall-clock cap for async jobs
    "sync_timeout": 15.0,       # client timeout for fast synchronous RPCs
    "poll_interval": 0.5,       # job_poll cadence
    "job_queue_max": 8,         # pending-job FIFO depth; over -> BusyError
    "idempotency_ttl": 300.0,   # how long a finished job stays dedupe-eligible
}


def config_dir() -> Path:
    base = os.environ.get("PHONECTL_HOME")
    d = Path(base) if base else Path.home() / ".config" / "phonectl"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path() -> Path:
    return config_dir() / "config.json"


def load() -> dict:
    p = _path()
    base = dict(DEFAULTS)
    if p.exists():
        base.update(json.loads(p.read_text()))
    return base


def save(cfg: dict) -> None:
    _path().write_text(json.dumps(cfg, indent=2))


def get_mode(cfg: dict) -> str:
    # Safe-by-default (Finding 5): actions require confirmation until the user
    # explicitly opts into auto (config set mode auto / setup wizard).
    return cfg.get("mode", "confirm")


def coerce_and_set(cfg: dict, key: str, raw: str) -> dict:
    if key not in DEFAULTS:
        raise KeyError(f"unknown config key: {key!r}")
    default = DEFAULTS[key]
    if isinstance(default, bool):
        value = str(raw).strip().lower() in ("1", "true", "yes", "on")
    elif isinstance(default, int):
        value = int(raw)
    elif isinstance(default, float):
        value = float(raw)
    elif default is None:
        # Some keys (e.g. companion_port) default to None as an "unset"
        # sentinel but hold numeric values once configured; best-effort
        # numeric coercion so `config set companion_port 8765` stores an
        # int, falling back to the raw string for genuinely textual keys.
        try:
            value = int(raw)
        except ValueError:
            try:
                value = float(raw)
            except ValueError:
                value = raw
    else:  # str
        value = raw
    cfg[key] = value
    return cfg
