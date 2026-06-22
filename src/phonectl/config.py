import json
import os
from pathlib import Path

DEFAULTS: dict = {
    "companion_host": "127.0.0.1",
    "companion_port": None,
    "companion_timeout": 2.0,
    "daemon_host": "127.0.0.1",
    "daemon_autostart": False,
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
    return cfg.get("mode", "auto")
