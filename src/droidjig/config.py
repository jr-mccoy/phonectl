import os
from pathlib import Path

from droidjig import state

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
    "scan_range": [32768, 61000],  # Linux/Android ephemeral band adbd binds within (self-heal scan)
    "ensure_ttl": 5.0,          # trust the last good connection check this long; 0 = re-check every call
    "action_observe_ttl": 0.0,  # reuse a pre-action snapshot at most this old; 0 = always re-observe (default)
}

# None-defaulted keys that must still coerce to int (their default is None, so the
# isinstance(default, int) branch below can't catch them).
_NUMERIC_NONE_KEYS = frozenset({"companion_port"})


#: The project was named `phonectl` before 0.1.0. These fallbacks keep an existing
#: install working across the rename — that directory holds the paired companion
#: token, the device serial and the audit log, so silently starting from an empty
#: config would present as data loss. Removable at 1.0.
_LEGACY_NAME = "phonectl"
_LEGACY_HOME_ENV = "PHONECTL_HOME"


def config_dir() -> Path:
    base = os.environ.get("DROIDJIG_HOME") or os.environ.get(_LEGACY_HOME_ENV)
    if base:
        d = Path(base)
    else:
        d = Path.home() / ".config" / "droidjig"
        legacy = Path.home() / ".config" / _LEGACY_NAME
        # Adopt the pre-rename directory in place rather than migrating it: a copy
        # could half-finish, and leaving it untouched keeps a downgrade possible.
        if not d.exists() and legacy.exists():
            d = legacy
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path() -> Path:
    return config_dir() / "config.json"


def load() -> dict:
    """Defaults overlaid with the stored config.

    A corrupt config.json falls back to defaults rather than raising: config is loaded
    by every command, so raising here would take out `droidjig doctor` — the command
    whose job is diagnosing a broken install. The fallback is also the safe direction,
    since `mode` reverts to `confirm` (see `get_mode`).
    """
    base = dict(DEFAULTS)
    base.update(state.read_json(_path(), {}))
    return base


def save(cfg: dict) -> None:
    state.write_json(_path(), cfg, indent=2)


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
    elif key in _NUMERIC_NONE_KEYS:
        value = int(raw)
    else:  # None/str default -> keep as string (e.g. companion_token, serial)
        value = raw
    cfg[key] = value
    return cfg
