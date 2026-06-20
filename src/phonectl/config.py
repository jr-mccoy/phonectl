import json
import os
from pathlib import Path


def config_dir() -> Path:
    base = os.environ.get("PHONECTL_HOME")
    d = Path(base) if base else Path.home() / ".config" / "phonectl"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path() -> Path:
    return config_dir() / "config.json"


def load() -> dict:
    p = _path()
    if not p.exists():
        return {}
    return json.loads(p.read_text())


def save(cfg: dict) -> None:
    _path().write_text(json.dumps(cfg, indent=2))


def get_mode(cfg: dict) -> str:
    return cfg.get("mode", "auto")
