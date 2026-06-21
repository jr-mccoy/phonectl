> **SUPERSEDED 2026-06-22** — folded into `docs/superpowers/phonectl-platform-roadmap.md` (Phase 2.2; rate-limit + guarded-package generalized into the risk ledger). Task-level re-homing is in `docs/superpowers/plans/2026-06-22-phonectl-remaining-plans-meta-plan.md`. Kept for traceability; do not execute as-is.

# phonectl Safety Completeness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the spec §9 safety model by adding rate limiting (cap actions/min to bound runaway loops) and guarded-package denylist enforcement (refuse/force-confirm on banking/purchase screens) to the existing `cli._do_action` funnel so every action verb is covered uniformly.

**Architecture:** A new pure-as-possible `ratelimit.py` decides "is another action allowed?" given recent action timestamps (read from `actions.jsonl`), a per-minute cap, and an injected `now()` clock — no subprocess, no adb. A new `errors.py` holds the typed-error hierarchy (`PhonectlError` base, plus `RateLimitError`/`GuardedActionError`). The guarded-package check reuses the existing cheap path `observer.parse_focused_app(backend.window_dump())` to read the *current* foreground package BEFORE acting (because actuators only re-observe AFTER acting). Both gates live inside `cli._do_action`, after the kill-switch check, before injection, so `tap`/`type`/`swipe`/`key`/`launch` are gated identically.

**Tech Stack:** Python 3 (stdlib only: `json`, `time`, `os`, `pathlib`), `pytest` for tests. No new runtime dependencies.

## Global Constraints

- stdlib-only at runtime (Python >=3.9; pytest dev-only).
- ONLY `adb_backend.py` may touch adb/subprocess.
- `ui_parser.py` stays pure (no I/O).
- element index `i` is the primary target.
- every actuator `act()` re-observes.
- tests isolate via `monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))`.
- one commit per task.
- TDD order is non-negotiable.

## Dependencies

- Shares `src/phonectl/errors.py` with the **resilience** plan (sequenced first). The resilience plan creates `errors.py` with `PhonectlError`/`ObserveError`/`DeviceLockedError`. This plan ADDS `RateLimitError` and `GuardedActionError`. **Task 1 below is written defensively: create `errors.py` if absent, else append the two classes** — so this plan stands alone whether or not resilience has landed.
- Builds on the shipped observe→act→core (`cli._do_action`, `audit.log_action`/`actions.jsonl`, `observer.parse_focused_app`, `config.load`/`get_mode`). These already exist in the repo.

## Decision record (read before implementing)

- **Config keys added:** `rate_limit_per_min` (int; `0` or absent = unlimited; suggested released-build default `60`) and `guarded_packages` (list of package-name prefixes, e.g. `["com.android.vending", "com.bank"]`; absent/empty = no guarding). These do not collide with existing `serial`/`mode` or the resilience plan's `last_port`.
- **Rate limit is COUNT-based over a sliding 60s window:** allow the action iff the number of already-logged actions whose `ts` is within `(now - 60, now]` is **strictly less than** `rate_limit_per_min`. `actions.jsonl` is the source of truth (it is the same log every executed action already appends to — dry-run does not log, so dry-run never consumes budget, which is correct).
- **Guarded-package policy = force-confirm (not hard refuse).** Spec §9 says "refuses **or** forces confirm"; we choose force-confirm because it is recoverable (a human can pass `--yes`) and still blocks unattended `auto`-mode loops on a banking screen. So: if the current foreground package matches the denylist AND `--yes` was not given, raise `GuardedActionError` and return a dedicated exit code; with `--yes`, the action proceeds and is audit-logged as normal. This holds **even in `auto` mode** (the whole point — `auto` must not bypass the guard).
- **New exit codes (extend the existing scheme — 0 ok, 1 wait-for/doctor fail, 2 kill-switch/arg, 3 confirm-without-yes):** `4` = rate limited (`RateLimitError`), `5` = guarded action blocked (`GuardedActionError`). These are stable, dedicated, nonzero codes an agent can branch on.
- **Gate ordering inside `_do_action`:** (1) kill switch [existing, rc 2] → (2) rate limit [rc 4] → (3) mode==confirm-without-yes [existing, rc 3] → (4) build runtime + `ensure()` → (5) guarded-package read of CURRENT foreground [rc 5] → (6) dry-run short-circuit [existing] → (7) inject + log. Rate limit is checked before connecting (cheap, no device round-trip needed). The guarded-package check needs a device read (`window_dump`), so it runs after `ensure()`.

---

### Task 1: `errors.py` — add `RateLimitError` and `GuardedActionError`

**Files:**
- Create (if absent) or Modify: `src/phonectl/errors.py`
- Test: `tests/test_errors.py`

**Interfaces:**
- Produces:
  - `class PhonectlError(Exception)` — base for all phonectl typed errors (created here only if the file does not already exist).
  - `class RateLimitError(PhonectlError)` — raised when the per-minute action cap is exceeded.
  - `class GuardedActionError(PhonectlError)` — raised when the current foreground package is on the guarded denylist and the action was not explicitly confirmed.
- Consumes: nothing.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_errors.py
from phonectl import errors


def test_hierarchy_rate_and_guarded_subclass_base():
    assert issubclass(errors.RateLimitError, errors.PhonectlError)
    assert issubclass(errors.GuardedActionError, errors.PhonectlError)
    assert issubclass(errors.PhonectlError, Exception)


def test_errors_carry_message():
    assert str(errors.RateLimitError("slow down")) == "slow down"
    assert str(errors.GuardedActionError("blocked")) == "blocked"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_errors.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'phonectl.errors'` (if resilience has not landed) or `AttributeError: module 'phonectl.errors' has no attribute 'RateLimitError'` (if it has).

- [ ] **Step 3: Write minimal implementation**

If `src/phonectl/errors.py` does **not** exist, create it with the base class and the two new classes:

```python
# src/phonectl/errors.py
class PhonectlError(Exception):
    """Base class for all phonectl typed errors."""


class RateLimitError(PhonectlError):
    """Raised when the per-minute action cap is exceeded."""


class GuardedActionError(PhonectlError):
    """Raised when acting on a guarded (denylisted) foreground package without --yes."""
```

If `src/phonectl/errors.py` **already exists** (the resilience plan landed first), do NOT recreate the base; append only the two classes at the end of the file, keeping the existing `PhonectlError`/`ObserveError`/`DeviceLockedError` untouched:

```python
# src/phonectl/errors.py  (append below the existing classes)
class RateLimitError(PhonectlError):
    """Raised when the per-minute action cap is exceeded."""


class GuardedActionError(PhonectlError):
    """Raised when acting on a guarded (denylisted) foreground package without --yes."""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_errors.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/errors.py tests/test_errors.py
git commit -m "feat: typed errors RateLimitError and GuardedActionError"
```

---

### Task 2: `ratelimit.py` — pure sliding-window allow-check + jsonl timestamp reader

**Files:**
- Create: `src/phonectl/ratelimit.py`
- Test: `tests/test_ratelimit.py`

**Interfaces:**
- Produces:
  - `recent_timestamps(path, now: float, window: float = 60.0) -> list[float]` — read `path` (a `Path` or str to an `actions.jsonl`-shaped file), parse each line's `"ts"` (float), and return only those `ts` with `now - window < ts <= now`. Returns `[]` if the file is missing. Tolerates blank lines and malformed JSON lines (skips them) so a partially written log never crashes the gate.
  - `is_allowed(timestamps: list[float], limit: int) -> bool` — pure: returns `True` if `limit <= 0` (unlimited) OR `len(timestamps) < limit`; else `False`.
  - `check(path, limit: int, now: float, window: float = 60.0) -> bool` — convenience: `is_allowed(recent_timestamps(path, now, window), limit)`. The CLI calls this.
- Consumes: nothing from phonectl (stdlib `json` only). No subprocess, no `config_dir()` import — the caller passes the path so this module stays trivially testable and side-effect-free apart from reading the file it is handed.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_ratelimit.py
import json
from phonectl import ratelimit


def _write_log(path, timestamps):
    with open(path, "w") as f:
        for ts in timestamps:
            f.write(json.dumps({"ts": ts, "verb": "tap", "target": {}, "app": "", "hash": ""}) + "\n")


def test_is_allowed_unlimited_when_limit_zero_or_negative():
    assert ratelimit.is_allowed([1.0] * 1000, 0) is True
    assert ratelimit.is_allowed([1.0] * 1000, -5) is True


def test_is_allowed_strictly_below_limit():
    assert ratelimit.is_allowed([1.0, 2.0], 3) is True   # 2 < 3
    assert ratelimit.is_allowed([1.0, 2.0, 3.0], 3) is False  # 3 not < 3


def test_recent_timestamps_filters_to_window(tmp_path):
    log = tmp_path / "actions.jsonl"
    # now=100.0, window=60 -> keep ts in (40.0, 100.0]
    _write_log(log, [10.0, 39.9, 40.0, 41.0, 99.0, 100.0])
    got = ratelimit.recent_timestamps(log, now=100.0, window=60.0)
    assert got == [41.0, 99.0, 100.0]   # 40.0 excluded (boundary is exclusive on the old side)


def test_recent_timestamps_missing_file_is_empty(tmp_path):
    assert ratelimit.recent_timestamps(tmp_path / "nope.jsonl", now=100.0) == []


def test_recent_timestamps_skips_blank_and_malformed_lines(tmp_path):
    log = tmp_path / "actions.jsonl"
    log.write_text(
        json.dumps({"ts": 95.0, "verb": "tap"}) + "\n"
        "\n"
        "{not json}\n"
        + json.dumps({"verb": "tap"}) + "\n"   # no ts key -> skipped
        + json.dumps({"ts": 96.0, "verb": "key"}) + "\n"
    )
    assert ratelimit.recent_timestamps(log, now=100.0, window=60.0) == [95.0, 96.0]


def test_check_combines_read_and_decision(tmp_path):
    log = tmp_path / "actions.jsonl"
    _write_log(log, [98.0, 99.0])           # 2 actions in window
    assert ratelimit.check(log, limit=3, now=100.0) is True   # 2 < 3 -> allowed
    assert ratelimit.check(log, limit=2, now=100.0) is False  # 2 not < 2 -> blocked
    assert ratelimit.check(log, limit=0, now=100.0) is True   # unlimited
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_ratelimit.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'phonectl.ratelimit'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/ratelimit.py
import json


def recent_timestamps(path, now: float, window: float = 60.0) -> list:
    """Return the ts values in (now - window, now] from an actions.jsonl-shaped file.

    Pure read: no subprocess, no clock. Missing file -> []. Blank/malformed/ts-less
    lines are skipped so a partially written log never crashes the rate-limit gate.
    """
    out = []
    try:
        with open(path) as f:
            lines = f.readlines()
    except FileNotFoundError:
        return out
    floor = now - window
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except (ValueError, TypeError):
            continue
        ts = rec.get("ts")
        if not isinstance(ts, (int, float)):
            continue
        if floor < ts <= now:
            out.append(float(ts))
    return out


def is_allowed(timestamps: list, limit: int) -> bool:
    """Pure decision: limit<=0 means unlimited; else allow iff count strictly below limit."""
    if limit <= 0:
        return True
    return len(timestamps) < limit


def check(path, limit: int, now: float, window: float = 60.0) -> bool:
    return is_allowed(recent_timestamps(path, now, window), limit)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_ratelimit.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/ratelimit.py tests/test_ratelimit.py
git commit -m "feat: pure sliding-window ratelimit check over actions.jsonl"
```

---

### Task 3: guarded-package helper — pure denylist match in `observer`

**Files:**
- Modify: `src/phonectl/observer.py` (append after `parse_focused_app`, currently ends at line 14)
- Test: `tests/test_observer.py` (append; file currently ends after `test_session_resolve_returns_center`)

**Interfaces:**
- Produces:
  - `observer.is_guarded(package: str, guarded: list) -> bool` — pure: returns `True` if `package` is non-empty and equals or starts with any prefix in `guarded`. Empty `package` or empty/absent `guarded` -> `False`. Prefix match (not exact) so `"com.bank"` guards `"com.bank.app"` and `"com.bank"`.
- Consumes: nothing new; this is a pure string helper colocated with the existing `parse_focused_app` cheap-path reader so the CLI gets "current foreground package -> is it guarded?" from one module. No I/O.

Rationale for placement: the spec's cheap path for the *current* foreground package is `observer.parse_focused_app(backend.window_dump())`. Keeping `is_guarded` next to it means the guard's read + match logic lives in the I/O-free observer-helper layer; `cli._do_action` just wires `window_dump()` (a backend call — the only adb touch) into these pure functions.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_observer.py  (append below the existing tests)
def test_is_guarded_prefix_match():
    assert observer.is_guarded("com.bank.app", ["com.bank"]) is True
    assert observer.is_guarded("com.bank", ["com.bank"]) is True          # exact also matches
    assert observer.is_guarded("com.android.vending", ["com.android.vending"]) is True


def test_is_guarded_no_match_and_empty_inputs():
    assert observer.is_guarded("com.example.notes", ["com.bank"]) is False
    assert observer.is_guarded("", ["com.bank"]) is False                  # empty package never guarded
    assert observer.is_guarded("com.bank.app", []) is False                # empty denylist guards nothing
    assert observer.is_guarded("com.bank.app", None) is False              # absent denylist guards nothing
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_observer.py -v`
Expected: FAIL — `AttributeError: module 'phonectl.observer' has no attribute 'is_guarded'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/phonectl/observer.py`:

```python
def is_guarded(package: str, guarded) -> bool:
    if not package or not guarded:
        return False
    return any(package == p or package.startswith(p) for p in guarded)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_observer.py -v`
Expected: PASS (existing observer tests + 2 new)

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/observer.py tests/test_observer.py
git commit -m "feat: pure is_guarded denylist prefix-match helper in observer"
```

---

### Task 4: wire rate limiting into `cli._do_action`

**Files:**
- Modify: `src/phonectl/cli.py` (imports at lines 4-8; `_do_action` at lines 42-60)
- Test: `tests/test_cli.py` (append; file currently ends at line 108)

**Interfaces:**
- Consumes: `ratelimit.check` (Task 2), `config.load`/`get_mode` (existing), `config.config_dir` for the `actions.jsonl` path, an injectable clock.
- Produces: `_do_action` now returns exit code `4` and prints a clear message when the action would exceed `rate_limit_per_min`; otherwise unchanged. A module-level `_now = time.time` indirection is added so tests can monkeypatch the clock (mirrors the existing `_make_backend` monkeypatch seam).

Note on testing the clock: `actions.jsonl` is written by `audit.log_action` using `time.time()`. To keep tests deterministic, the rate-limit gate reads timestamps and compares against `cli._now()`; tests monkeypatch BOTH the log timestamps (by pre-seeding `actions.jsonl`) and `cli._now`. Pre-seeding the log directly (not via real `time.time()`) is the repo-consistent "scripted timestamps" approach.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cli.py  (append below the existing tests)
def _seed_actions_log(home, timestamps):
    import json as _json
    with open(home / "actions.jsonl", "w") as f:
        for ts in timestamps:
            f.write(_json.dumps({"ts": ts, "verb": "tap", "target": {}, "app": "", "hash": ""}) + "\n")


def test_tap_rate_limited_returns_4_and_does_not_inject(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    config.save({"rate_limit_per_min": 2})
    _seed_actions_log(tmp_path, [950.0, 980.0])   # 2 actions already in the (940, 1000] window
    monkeypatch.setattr(cli, "_now", lambda: 1000.0)
    fb = FakeBackend()
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: fb)
    rc = cli.main(["tap", "--xy", "100", "200"])
    out = capsys.readouterr().out
    assert rc == 4
    assert fb.calls == []                          # blocked before injection
    assert "rate" in out.lower()


def test_tap_allowed_when_under_rate_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    config.save({"rate_limit_per_min": 3})
    _seed_actions_log(tmp_path, [950.0, 980.0])   # 2 < 3 -> allowed
    monkeypatch.setattr(cli, "_now", lambda: 1000.0)
    fb = FakeBackend()
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: fb)
    rc = cli.main(["tap", "--xy", "100", "200"])
    assert rc == 0
    assert ("tap", 100, 200) in fb.calls


def test_old_actions_outside_window_do_not_count(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    config.save({"rate_limit_per_min": 2})
    _seed_actions_log(tmp_path, [100.0, 200.0])   # both older than (940, 1000] -> 0 in window
    monkeypatch.setattr(cli, "_now", lambda: 1000.0)
    fb = FakeBackend()
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: fb)
    rc = cli.main(["tap", "--xy", "1", "2"])
    assert rc == 0
    assert ("tap", 1, 2) in fb.calls


def test_rate_limit_absent_means_unlimited(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    _seed_actions_log(tmp_path, [float(t) for t in range(900, 1000)])  # 100 actions in window
    monkeypatch.setattr(cli, "_now", lambda: 1000.0)
    fb = FakeBackend()
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: fb)
    rc = cli.main(["tap", "--xy", "1", "2"])      # no rate_limit_per_min key -> unlimited
    assert rc == 0
    assert ("tap", 1, 2) in fb.calls
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cli.py -v`
Expected: FAIL — `AttributeError: module 'phonectl.cli' has no attribute '_now'` (and the rate-limit assertions: `tap` still injects when it should be blocked).

- [ ] **Step 3: Write minimal implementation**

Update the imports block in `src/phonectl/cli.py`:

```python
from __future__ import annotations

import argparse
import json
import time
from phonectl import __version__, config, audit, observer, actuator, ratelimit
from phonectl.adb_backend import AdbBackend
from phonectl.session import Session
from phonectl.connection import Connection


_now = time.time  # indirection so tests can monkeypatch the clock deterministically
```

Add a rate-limit guard helper above `_do_action` and call it inside `_do_action` right after the kill-switch check (before the confirm-mode/build-runtime logic):

```python
def _rate_limit_blocked(cfg) -> int | None:
    limit = int(cfg.get("rate_limit_per_min", 0) or 0)
    if limit <= 0:
        return None
    log_path = config.config_dir() / "actions.jsonl"
    if ratelimit.check(log_path, limit, now=_now()):
        return None
    print(f"phonectl: action refused (rate limit {limit}/min exceeded)")
    return 4
```

Modify `_do_action` (lines 42-60) to insert the rate-limit gate after the kill-switch check:

```python
def _do_action(args, verb, fn, target):
    cfg = config.load()
    blocked = _guard_action(cfg)
    if blocked is not None:
        return blocked
    rl = _rate_limit_blocked(cfg)
    if rl is not None:
        return rl
    mode = config.get_mode(cfg)
    if mode == "confirm" and not args.yes:
        print(f"phonectl: {verb} {target} requires --yes in confirm mode")
        return 3
    backend, session, conn = build_runtime(cfg)
    conn.ensure()
    if mode == "dry-run":
        observer.observe(backend, session)
        print(f"phonectl: dry-run {verb} {target} (not executed)")
        return 0
    snap = fn(backend, session)
    audit.log_action(verb, target, snap)
    _emit(snap)
    return 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cli.py -v`
Expected: PASS (existing CLI tests + 4 new rate-limit tests)

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/cli.py tests/test_cli.py
git commit -m "feat: rate-limit gate in _do_action (exit 4 on exceed)"
```

---

### Task 5: wire guarded-package enforcement into `cli._do_action`

**Files:**
- Modify: `src/phonectl/cli.py` (`_do_action`, post-Task-4 version)
- Test: `tests/test_cli.py` (append)

**Interfaces:**
- Consumes: `observer.parse_focused_app` + `observer.is_guarded` (Task 3), `config` key `guarded_packages`, `errors.GuardedActionError` (Task 1), `backend.window_dump()` (existing backend method — the cheap current-foreground read).
- Produces: `_do_action` reads the CURRENT foreground package AFTER `conn.ensure()` but BEFORE injection; if it is guarded and `args.yes` is falsy, prints a clear message naming the package and returns exit code `5`. With `--yes`, the action proceeds. Applies in every mode that would otherwise inject (`auto` included); does NOT apply in `dry-run` (nothing is injected there, so there is nothing to guard — but the guard still runs before the dry-run short-circuit is harmless; we place it before dry-run so a guarded screen is reported rather than silently "dry-run ok"... see implementation note).

Implementation note on ordering vs dry-run: place the guarded-package check **after `ensure()` and before the dry-run short-circuit**. Reading `window_dump()` requires a live connection, so it must follow `ensure()`. Putting it before the dry-run branch means a guarded foreground is reported (exit 5) even in dry-run, which is the safer, more informative behavior — dry-run is a preview and the preview should surface that this screen is guarded. Confirm-mode interaction: `--yes` already satisfies confirm mode, and the same `--yes` satisfies the guard, so a single `--yes` clears both. (FakeBackend in `tests/test_cli.py` already implements `window_dump()` returning `mCurrentFocus=Window{a b com.x/.A}`, so its package is `com.x`; guarded tests use a denylist of `["com.x"]` to trigger, or a non-matching list to pass.)

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cli.py  (append below the rate-limit tests)
def test_tap_on_guarded_package_blocked_in_auto_returns_5(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    config.save({"guarded_packages": ["com.x"]})   # FakeBackend foreground is com.x
    fb = FakeBackend()
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: fb)
    rc = cli.main(["tap", "--xy", "100", "200"])   # auto mode, no --yes
    out = capsys.readouterr().out
    assert rc == 5
    assert fb.calls == []                           # guarded: not injected
    assert "com.x" in out and "guard" in out.lower()
    assert not (tmp_path / "actions.jsonl").exists()  # blocked action is not audit-logged


def test_tap_on_guarded_package_proceeds_with_yes(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    config.save({"guarded_packages": ["com.x"]})
    fb = FakeBackend()
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: fb)
    rc = cli.main(["tap", "--xy", "100", "200", "--yes"])
    assert rc == 0
    assert ("tap", 100, 200) in fb.calls            # --yes clears the guard
    assert "tap" in (tmp_path / "actions.jsonl").read_text()


def test_non_guarded_package_unaffected(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    config.save({"guarded_packages": ["com.bank"]})   # foreground com.x does not match
    fb = FakeBackend()
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: fb)
    rc = cli.main(["tap", "--xy", "1", "2"])
    assert rc == 0
    assert ("tap", 1, 2) in fb.calls


def test_guard_applies_to_launch_verb_too(tmp_path, monkeypatch):
    # Every verb funnels through _do_action, so launch is guarded identically.
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    config.save({"guarded_packages": ["com.x"]})
    fb = FakeBackend()
    # launch() needs a backend.launch method on FakeBackend; add it inline via monkeypatch.
    fb.launch = lambda pkg: fb.calls.append(("launch", pkg))
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: fb)
    rc = cli.main(["launch", "com.android.settings"])
    assert rc == 5
    assert fb.calls == []                            # guarded before launch injection
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cli.py -v`
Expected: FAIL — guarded tests: `tap`/`launch` still inject (rc 0) instead of being blocked (rc 5), because the guard is not wired yet.

- [ ] **Step 3: Write minimal implementation**

Add `errors` to the imports in `src/phonectl/cli.py`:

```python
from phonectl import __version__, config, audit, observer, actuator, ratelimit, errors
```

Add a guarded-package helper above `_do_action`:

```python
def _guarded_blocked(cfg, backend, args) -> int | None:
    guarded = cfg.get("guarded_packages") or []
    if not guarded:
        return None
    app = observer.parse_focused_app(backend.window_dump())
    pkg = app.get("package", "")
    if observer.is_guarded(pkg, guarded) and not args.yes:
        print(f"phonectl: action refused (guarded package {pkg!r}); "
              f"re-run with --yes to confirm")
        return 5
    return None
```

Modify `_do_action` to call it after `conn.ensure()` and before the dry-run branch:

```python
def _do_action(args, verb, fn, target):
    cfg = config.load()
    blocked = _guard_action(cfg)
    if blocked is not None:
        return blocked
    rl = _rate_limit_blocked(cfg)
    if rl is not None:
        return rl
    mode = config.get_mode(cfg)
    if mode == "confirm" and not args.yes:
        print(f"phonectl: {verb} {target} requires --yes in confirm mode")
        return 3
    backend, session, conn = build_runtime(cfg)
    conn.ensure()
    guarded = _guarded_blocked(cfg, backend, args)
    if guarded is not None:
        return guarded
    if mode == "dry-run":
        observer.observe(backend, session)
        print(f"phonectl: dry-run {verb} {target} (not executed)")
        return 0
    snap = fn(backend, session)
    audit.log_action(verb, target, snap)
    _emit(snap)
    return 0
```

Note: `errors.GuardedActionError` is imported for callers/MCP layers that want to catch a typed error; the CLI funnel converts the guarded condition directly into exit code `5` (consistent with how the kill switch and confirm-mode map conditions to codes rather than raising out of `main`). The import keeps the typed hierarchy available to non-CLI consumers (e.g. the future MCP server) per the shared-conventions contract.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cli.py -v`
Expected: PASS (existing + rate-limit + 4 new guarded tests)

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/cli.py tests/test_cli.py
git commit -m "feat: guarded-package force-confirm gate in _do_action (exit 5)"
```

---

### Task 6: surface `rate_limit_per_min` / `guarded_packages` in docs + config helpers

**Files:**
- Modify: `src/phonectl/config.py` (append accessor helpers after `get_mode`, currently line 29)
- Test: `tests/test_config_audit.py` (append; file currently ends at line 47)

**Interfaces:**
- Produces (thin typed accessors so callers do not re-implement defaulting/coercion, and so the released-build default lives in one place):
  - `config.get_rate_limit(cfg: dict) -> int` — returns `int(cfg.get("rate_limit_per_min", 0) or 0)` (0 = unlimited).
  - `config.get_guarded_packages(cfg: dict) -> list` — returns `list(cfg.get("guarded_packages") or [])`.
- Consumes: nothing new.

Rationale: Tasks 4-5 inlined the key reads to keep each gate self-contained while landing test-first. This task extracts the two reads into named accessors and points `_rate_limit_blocked`/`_guarded_blocked` at them, removing the duplicated defaulting logic. (Pure refactor under green tests — the CLI tests from Tasks 4-5 must still pass unchanged.)

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_config_audit.py  (append below the existing tests)
def test_get_rate_limit_defaults_and_coerces(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    assert config.get_rate_limit({}) == 0                       # absent -> unlimited
    assert config.get_rate_limit({"rate_limit_per_min": 0}) == 0
    assert config.get_rate_limit({"rate_limit_per_min": 60}) == 60
    assert config.get_rate_limit({"rate_limit_per_min": None}) == 0  # null -> unlimited


def test_get_guarded_packages_defaults_to_empty_list(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    assert config.get_guarded_packages({}) == []
    assert config.get_guarded_packages({"guarded_packages": None}) == []
    assert config.get_guarded_packages({"guarded_packages": ["com.bank"]}) == ["com.bank"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_config_audit.py -v`
Expected: FAIL — `AttributeError: module 'phonectl.config' has no attribute 'get_rate_limit'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/phonectl/config.py`:

```python
def get_rate_limit(cfg: dict) -> int:
    return int(cfg.get("rate_limit_per_min", 0) or 0)


def get_guarded_packages(cfg: dict) -> list:
    return list(cfg.get("guarded_packages") or [])
```

Then point the two CLI helpers at the accessors (refactor; tests stay green):

```python
def _rate_limit_blocked(cfg) -> int | None:
    limit = config.get_rate_limit(cfg)
    if limit <= 0:
        return None
    log_path = config.config_dir() / "actions.jsonl"
    if ratelimit.check(log_path, limit, now=_now()):
        return None
    print(f"phonectl: action refused (rate limit {limit}/min exceeded)")
    return 4


def _guarded_blocked(cfg, backend, args) -> int | None:
    guarded = config.get_guarded_packages(cfg)
    if not guarded:
        return None
    app = observer.parse_focused_app(backend.window_dump())
    pkg = app.get("package", "")
    if observer.is_guarded(pkg, guarded) and not args.yes:
        print(f"phonectl: action refused (guarded package {pkg!r}); "
              f"re-run with --yes to confirm")
        return 5
    return None
```

- [ ] **Step 4: Run the full suite to verify everything passes**

Run: `pytest -v`
Expected: PASS (all files — errors, ratelimit, observer, config_audit, cli, plus the pre-existing suite). Confirm the Task 4/5 CLI tests still pass after the refactor.

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/config.py src/phonectl/cli.py tests/test_config_audit.py
git commit -m "refactor: config accessors for rate_limit_per_min and guarded_packages"
```

---

## Notes on completeness & honesty

- **Fully TDD-able at the Python level.** Every task is pure Python with deterministic tests (injected `cli._now` clock, pre-seeded `actions.jsonl`, `FakeBackend` via `monkeypatch.setattr(cli, "_make_backend", ...)`, `PHONECTL_HOME` isolation). No native/Kotlin code is involved in this plan.
- **No new runtime dependency.** `ratelimit.py` and `errors.py` are stdlib-only; the guard reuses the existing `window_dump()` backend method (the only adb touch, already in `adb_backend.py`).
- **Architecture invariants preserved.** `adb` stays in `adb_backend.py`; `ratelimit.py` is pure (reads a file path handed to it, no subprocess/clock); `observer.is_guarded` is a pure string helper; all gating is in the single `_do_action` funnel so every verb is covered uniformly; actuators still re-observe after acting (unchanged).
- **`errors.GuardedActionError`/`RateLimitError`** exist as typed errors for non-CLI consumers (the future MCP server); the CLI funnel maps the conditions to dedicated exit codes (4, 5) the same way the existing kill-switch/confirm gates map to codes 2/3.
