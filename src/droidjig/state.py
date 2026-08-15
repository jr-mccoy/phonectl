"""Crash-safe JSON state files.

Every droidjig state file lives on a phone, where `kill -9`, a dead battery and a
full disk are routine rather than exotic. Two properties follow, and this module is
the single place that provides them:

- **Reads never raise.** A corrupt, truncated, empty, unreadable or wrong-shaped file
  degrades to "no state" instead of taking down the command that touched it. Losing a
  rate-limit history is recoverable; a `JSONDecodeError` traceback out of
  `droidjig doctor` — the command whose whole job is diagnosing a broken install — is not.
- **Writes are atomic.** Content goes to a temp file in the same directory, is flushed to
  disk, and is published with `os.replace`, which is atomic on POSIX. A reader therefore
  sees either the old file or the new one, never a half-written one, and an interrupted
  write leaves the previous good state intact.

`runtime._load_idempotency` had the read half of this ("corrupt/unreadable store must
never block actions") long before the rest of the codebase did; this generalizes it.
"""
from __future__ import annotations

import copy
import json
import os
import tempfile
from pathlib import Path


def read_json(path, default):
    """The object stored at ``path``, or a deep copy of ``default``.

    Returns the default whenever the file is missing, empty, corrupt, unreadable, or
    holds a different top-level type than the default (a caller expecting a dict must
    never be handed a list). The copy matters: callers mutate what they get back, and a
    shared default would leak one call's mutations into every later call.
    """
    try:
        raw = Path(path).read_text()
    except (OSError, ValueError, UnicodeDecodeError):
        return copy.deepcopy(default)
    try:
        value = json.loads(raw)
    except ValueError:
        return copy.deepcopy(default)
    if default is not None and not isinstance(value, type(default)):
        return copy.deepcopy(default)
    return value


def write_json(path, obj, *, indent=None, _replace=None) -> None:
    """Write ``obj`` to ``path`` atomically.

    The temp file is created in the destination directory so the final `os.replace`
    stays within one filesystem (rename across devices is not atomic, and fails). The
    fsync is what makes this survive power loss rather than merely process death.

    Write errors propagate — a caller that cannot persist state should hear about it —
    but the previous file is left untouched and no temp file is left behind.
    """
    replace = os.replace if _replace is None else _replace   # resolved late, so tests can spy
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=f".{p.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(obj, f, indent=indent)
            f.flush()
            os.fsync(f.fileno())
        replace(tmp, str(p))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_jsonl(path) -> list:
    """The JSON objects in an append-only ``.jsonl`` file, skipping unusable lines.

    Appends are not atomic, so a crash mid-append tears the final line; blank and
    malformed lines, and rows that are not JSON objects, are skipped so the complete
    records around them stay readable. Callers index these rows as dicts.
    """
    try:
        raw = Path(path).read_text()
    except (OSError, ValueError, UnicodeDecodeError):
        return []
    rows = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows
