# phonectl Action Serialization, Request IDs & Audit v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Plan 2.1 of the platform roadmap** (`docs/superpowers/phonectl-platform-roadmap.md`). First plan of
Phase 2 (single-writer runtime & safety policy). Depends on **Plan 1.1** for the `errors` hierarchy and the
`results` envelope, and lands on top of the **Plan 1.3** act path (whose `observe` already raises structured
lock-state). It is the **single-writer seam the daemon (Phase 5) will own** and the funnel that **Plan 2.2**
(risk ledger) and **Plan 2.3** (MCP) consult. It re-homes the first half of the superseded
`2026-06-21-phonectl-safety-completeness.md` (audit redaction/levels + the action funnel); the risk/rate
half lands in Plan 2.2.

**Goal:** Make phonectl a **single writer**. Extract the ad-hoc `cli._do_action` gate into one reusable
funnel, `runtime.run_action`, that returns the **Plan-1 result envelope** (never a bare tuple), stamps every
action with a `request_id`, serializes mutating actions through a process-local lock (a clear `busy` status
when two callers collide), supports `idempotency_key` replay, and routes the kill switch through the same
envelope as a structured **emergency stop**. Plus **audit v2**: configurable `audit_level`
(`none|metadata|redacted|full`), broader redaction (OTP/email/phone/card/URL-token/clipboard) via a new pure
`redact` module, a `request_id` on every record, and `audit tail|purge|export --redacted` subcommands.

**Architecture:** A new orchestration module `runtime.py` owns the single-writer funnel and the
request-id/idempotency/lock seams; it is the one place modes + kill-switch + (later) policy + rate limits are
checked, so the daemon becomes a compatible evolution rather than a rewrite (strategy §22). A new **pure**
module `redact.py` does string redaction with no I/O. `audit.py` gains levels + redaction + the `request_id`
field + read/purge/export helpers. `errors.py` is **additively extended** with the three single-writer
control-flow codes (`busy`, `stopped`, `confirmation_required`) — existing codes are never renamed. `cli.py`'s
`_do_action` becomes a thin adapter that calls `run_action` and maps the returned envelope to today's exit
codes (`0`/`1`/`2`/`3`). Everything stays behind injectable seams (`build`, `gen_id`, `now`, `kill_switch`,
`log`) so unit tests need no device, no wall-clock, and no real `adb`.

**Tech Stack:** Python 3 (stdlib only at runtime: `threading`, `uuid`, `re`, `json`, `time`, `argparse`);
`pytest` for tests; `adb` (android-tools) remains the only external runtime dependency.

## Global Constraints

- **stdlib-only at runtime** (Python ≥ 3.9; `pytest` dev-only). `threading`/`uuid` are stdlib — no new dep.
- **ONLY `adb_backend.py` may touch adb/subprocess.** `runtime.py` never calls `adb`; it composes
  `observer`/`actuator`/`audit` and the injected `build`.
- **`ui_parser.py` stays pure** (untouched by this plan). The new `redact.py` is also pure (`str → str`).
- **Element index `i` / selectors / raw `(x,y)`** targeting is unchanged; `run_action` is verb-agnostic and
  carries whatever `target` dict the CLI/MCP already builds.
- **Every actuator `act()` re-observes** — unchanged; `run_action` observes once before acting and returns
  the post-action snapshot inside the envelope's `data`.
- **Modes + kill-switch gate every mutating action through one funnel** — this plan *is* that funnel.
- **Every action is audited** — `run_action` logs through `audit.log_action`, now level-aware with a
  `request_id`.
- **Structured-result invariant (Plan 1.1):** `run_action` ALWAYS returns a `results.ok/err` envelope, never
  a bare tuple and never a raw traceback; typed errors from `observe`/`actuator` are caught and re-emitted as
  `results.err(...)` (lock-state spread preserved).
- **Injectable seams** — `build`, `gen_id`, `now`, `kill_switch`, `log` are parameters; tests isolate
  config/audit/kill-switch via `monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))`.
- **One commit per task. TDD order is non-negotiable:** failing test first, confirm the failure reason,
  minimal code, green, commit.

## Shared conventions established by this plan

- **`runtime.run_action(verb, fn, target, *, build, yes=False, cfg=None, request_id=None,
  idempotency_key=None, ...) -> dict`** is the canonical mutating-action funnel. Plan 2.2 inserts
  policy + rate-limit checks INSIDE it; Plan 2.3 (MCP) and the Phase-5 daemon CALL it. No surface re-checks
  modes/kill-switch on its own once it routes through `run_action`.
- **Three new stable error codes, added additively to `errors.py`** (the canonical home from Plan 1.1; this
  plan extends it, never renames):
  - `BusyError` — `code = "busy"`, `retryable = True` (another action holds the single-writer lock).
  - `StoppedError` — `code = "stopped"`, `requires_user = True` (kill switch `STOP` present / emergency stop).
  - `ConfirmationRequiredError` — `code = "confirmation_required"`, `requires_user = True` (confirm mode or,
    later, a risk-policy `confirm` decision without `--yes`).
- **`request_id`** is a short hex id (`uuid4().hex`) stamped on the envelope AND the audit record. A caller
  may pass its own (the daemon/MCP will); absent one, `run_action` generates it.
- **`idempotency_key`** (optional): a process-local cache returns the prior envelope (with
  `idempotent_replay: True`) instead of re-running. Durable cross-process idempotency is the daemon's job
  (deferred).
- **`config.json` keys added:** `audit_level` (default `"redacted"`). No collision with Plan 1.3
  (`last_port`, `probe_ports`) or Plan 2.2 (`risk_policy`, `rate_limits`, `guarded_packages`).
- **Audit levels:** `none` (write nothing) · `metadata` (`ts`/`verb`/`request_id`/`app`/`hash`, no target) ·
  `redacted` (metadata + the target with sensitive strings scrubbed by `redact`) · `full` (everything raw).
  **Default is `redacted`** — and on a non-sensitive target like `{"i": 7}` or `{"selector": {"text":
  "Wi-Fi"}}` redaction is a no-op, so the existing audit/cli tests stay green.

---

### Task 1: `errors.py` — additive single-writer control-flow codes

**Files:**
- Modify: `src/phonectl/errors.py` (append three classes; do not touch the existing ones)
- Test: `tests/test_errors.py` (append below existing tests)

**Interfaces (added to the canonical Plan-1.1 module):**
- `class BusyError(PhonectlError)` — `code = "busy"`, `retryable = True`.
- `class StoppedError(PhonectlError)` — `code = "stopped"`, `requires_user = True`.
- `class ConfirmationRequiredError(PhonectlError)` — `code = "confirmation_required"`,
  `requires_user = True`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_errors.py  (append below existing tests)

def test_phase2_single_writer_codes_and_flags():
    assert errors.BusyError.code == "busy"
    assert errors.BusyError.retryable is True
    assert errors.StoppedError.code == "stopped"
    assert errors.StoppedError.requires_user is True
    assert errors.ConfirmationRequiredError.code == "confirmation_required"
    assert errors.ConfirmationRequiredError.requires_user is True
    for cls in (errors.BusyError, errors.StoppedError, errors.ConfirmationRequiredError):
        assert issubclass(cls, errors.PhonectlError)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_errors.py -v`
Expected: FAIL (`AttributeError: module 'phonectl.errors' has no attribute 'BusyError'`).

- [ ] **Step 3: Write minimal implementation**

Append to `src/phonectl/errors.py` (below `RateLimitError`):

```python
class BusyError(PhonectlError):
    # Another mutating action holds the single-writer lock (strategy §7.4).
    code = "busy"
    retryable = True


class StoppedError(PhonectlError):
    # Emergency stop: the kill-switch STOP sentinel is present (design §8).
    code = "stopped"
    requires_user = True


class ConfirmationRequiredError(PhonectlError):
    # Confirm mode (or, in Plan 2.2, a risk-policy "confirm") without --yes.
    code = "confirmation_required"
    requires_user = True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_errors.py -v`
Expected: PASS (existing tests + 1 new).

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/errors.py tests/test_errors.py
git commit -m "feat: additive single-writer error codes (busy/stopped/confirmation_required)"
```

---

### Task 2: `runtime.run_action` — single-writer funnel returning the result envelope

Extracts the `cli._do_action` gate into a reusable funnel that returns the Plan-1 envelope, stamps a
`request_id`, routes the kill switch as a structured `stopped`, honors `confirm`/`dry-run`/`auto` modes, and
catches any `PhonectlError` from `observe`/`actuator` into `results.err(...)`. The lock and idempotency seams
arrive in Tasks 3–4; this task establishes the envelope contract.

**Files:**
- Create: `src/phonectl/runtime.py`
- Test: `tests/test_runtime.py`

**Interfaces:**
- `run_action(verb, fn, target, *, build, yes=False, cfg=None, request_id=None, gen_id=_new_request_id,
  kill_switch=audit.kill_switch_active, log=audit.log_action) -> dict`:
  - `build(cfg) -> (backend, session, conn)` is the injected runtime builder (cli passes `build_runtime`).
  - `fn(backend, session) -> snapshot` is the actuator call (same lambda the CLI already constructs).
  - Order: stamp `request_id` → if `kill_switch()` return `results.err(StoppedError(...))` → if
    `mode == "confirm" and not yes` return `results.err(ConfirmationRequiredError(...))` → `build` +
    `conn.ensure()` + `observer.observe()` → if `mode == "dry-run"` return `results.ok(..., dry_run=True,
    data=session.last)` (no `fn`, no audit) → else `snap = fn(...)`, `log(verb, target, snap,
    request_id=request_id, cfg=cfg)`, return `results.ok(..., data=snap)`.
  - Any `errors.PhonectlError` raised inside (e.g. `DeviceLockedError` from `observe`) is caught and returned
    as `results.err(e, **getattr(e, "lock_state", {}), verb=verb, target=target, request_id=request_id)`.
  - Every envelope carries `capability=f"ui.{verb}"`, `provider="adb"`, `verb`, `target`, `request_id`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_runtime.py
import pytest
from phonectl import runtime, config, errors


class FakeConn:
    def ensure(self): pass


class FakeBackend:
    def __init__(self):
        self.taps = []
        self._snap = {"hash": "h1", "app": {"package": "com.x"}, "elements": []}


class FakeSession:
    def __init__(self): self.last = None
    def set_snapshot(self, snap): self.last = snap


def make_build(backend=None, observe_snap=None):
    backend = backend or FakeBackend()
    sess = FakeSession()
    def _observe(b, s, **kw):       # stand-in for observer.observe
        s.set_snapshot(observe_snap or {"hash": "h0", "app": {"package": "com.x"}, "elements": []})
        return s.last
    runtime.observer.observe = _observe   # monkeypatched per-test below instead; see note
    def build(cfg): return backend, sess, FakeConn()
    return build, backend, sess


def test_run_action_success_returns_ok_envelope_and_audits(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    backend = FakeBackend()
    sess = FakeSession()
    monkeypatch.setattr(runtime.observer, "observe",
                        lambda b, s, **kw: s.set_snapshot({"hash": "h", "app": {"package": "com.x"}}))
    def build(cfg): return backend, sess, FakeConn()
    def fn(b, s):
        b.taps.append((1, 2))
        return {"hash": "after", "app": {"package": "com.x"}}
    env = runtime.run_action("tap", fn, {"x": 1, "y": 2}, build=build,
                             gen_id=lambda: "req123")
    assert env["ok"] is True
    assert env["verb"] == "tap"
    assert env["request_id"] == "req123"
    assert env["data"]["hash"] == "after"
    assert backend.taps == [(1, 2)]
    log = (tmp_path / "actions.jsonl").read_text()
    assert "req123" in log and "tap" in log


def test_run_action_kill_switch_returns_stopped_envelope(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    (tmp_path / "STOP").write_text("")
    called = []
    def build(cfg):
        called.append(True)
        raise AssertionError("build must not run when stopped")
    env = runtime.run_action("tap", lambda b, s: None, {"x": 1}, build=build)
    assert env["ok"] is False
    assert env["error"]["code"] == "stopped"
    assert env["error"]["requires_user"] is True
    assert called == []                       # short-circuited before build/observe


def test_run_action_confirm_mode_requires_yes(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    config.save({"mode": "confirm"})
    env = runtime.run_action("tap", lambda b, s: None, {"x": 1},
                             build=lambda cfg: (_ for _ in ()).throw(AssertionError("no build")),
                             yes=False)
    assert env["error"]["code"] == "confirmation_required"
    # with --yes it proceeds:
    backend = FakeBackend(); sess = FakeSession()
    monkeypatch.setattr(runtime.observer, "observe",
                        lambda b, s, **kw: s.set_snapshot({"hash": "h"}))
    env2 = runtime.run_action("tap", lambda b, s: {"hash": "x"}, {"x": 1},
                              build=lambda cfg: (backend, sess, FakeConn()), yes=True,
                              cfg={"mode": "confirm"})
    assert env2["ok"] is True


def test_run_action_dry_run_observes_but_does_not_act_or_audit(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    backend = FakeBackend(); sess = FakeSession()
    monkeypatch.setattr(runtime.observer, "observe",
                        lambda b, s, **kw: s.set_snapshot({"hash": "obs"}))
    acted = []
    env = runtime.run_action("tap", lambda b, s: acted.append(1), {"x": 1},
                             build=lambda cfg: (backend, sess, FakeConn()),
                             cfg={"mode": "dry-run"})
    assert env["ok"] is True and env["dry_run"] is True
    assert env["data"]["hash"] == "obs"
    assert acted == []
    assert not (tmp_path / "actions.jsonl").exists()


def test_run_action_catches_phonectl_error_into_envelope(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    backend = FakeBackend(); sess = FakeSession()
    def observe(b, s, **kw):
        exc = errors.DeviceLockedError("device is locked, unlock it")
        exc.lock_state = {"lock_state": "locked_secure", "can_act": False,
                          "recommended_user_action": "Unlock the phone manually."}
        raise exc
    monkeypatch.setattr(runtime.observer, "observe", observe)
    env = runtime.run_action("tap", lambda b, s: None, {"x": 1},
                             build=lambda cfg: (backend, sess, FakeConn()))
    assert env["ok"] is False
    assert env["error"]["code"] == "device_locked"
    assert env["lock_state"] == "locked_secure"     # structured state spread into envelope
    assert env["verb"] == "tap"
```

Note: tests monkeypatch `runtime.observer.observe` so no real device/`uiautomator` is touched; the funnel's
own logic (modes, kill-switch, audit, error catch) is what is under test.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_runtime.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'phonectl.runtime'`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/runtime.py
"""Single-writer action funnel (strategy §7.4, §22). Every mutating action — from
the CLI today, the MCP server and daemon later — routes through run_action, which
checks the kill switch + modes (+ policy/rate limits in Plan 2.2), serializes
writers, stamps a request_id, audits, and returns the Plan-1 result envelope. It
NEVER raises to its caller and never touches adb directly."""
from __future__ import annotations

import uuid

from phonectl import audit, config, errors, observer, results


def _new_request_id() -> str:
    return uuid.uuid4().hex


def run_action(verb, fn, target, *, build, yes=False, cfg=None, request_id=None,
               gen_id=_new_request_id, kill_switch=audit.kill_switch_active,
               log=audit.log_action) -> dict:
    cfg = config.load() if cfg is None else cfg
    rid = request_id or gen_id()
    base = {"verb": verb, "target": target, "request_id": rid}

    # Emergency stop — the hard global kill switch (design §8).
    if kill_switch():
        return results.err(
            errors.StoppedError("action refused (kill switch STOP present)"),
            user_action="Remove the $PHONECTL_HOME/STOP file to resume.", **base)

    mode = config.get_mode(cfg)
    if mode == "confirm" and not yes:
        return results.err(
            errors.ConfirmationRequiredError(f"{verb} {target} requires confirmation"),
            user_action="Re-run with --yes to confirm this action.", **base)

    try:
        backend, session, conn = build(cfg)
        conn.ensure()
        observer.observe(backend, session)
        if mode == "dry-run":
            return results.ok(capability=f"ui.{verb}", provider="adb",
                              data=session.last, dry_run=True, **base)
        snap = fn(backend, session)
        log(verb, target, snap, request_id=rid, cfg=cfg)
        return results.ok(capability=f"ui.{verb}", provider="adb", data=snap, **base)
    except errors.PhonectlError as e:
        return results.err(e, **getattr(e, "lock_state", {}), **base)
```

Note: `audit.log_action` gains the `request_id`/`cfg` kwargs in Task 5; until then this call passes
keywords the current signature does not accept. **Implement Task 5's `log_action` signature change in the
same working session before running the Task 2 success test**, or temporarily stub the extra kwargs — the
canonical order is to land Task 5's signature first if you prefer strict green-at-every-step; the commit
boundaries still hold. (The plan keeps them as separate commits; if you run Task 2's audit-asserting test
before Task 5, accept the one expected signature error and proceed, or reorder locally.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_runtime.py -v`
Expected: PASS (5 tests), assuming `audit.log_action` accepts `request_id`/`cfg` (Task 5).

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/runtime.py tests/test_runtime.py
git commit -m "feat: runtime.run_action single-writer funnel returning the result envelope"
```

---

### Task 3: Process-local single-writer lock + `busy` status

Adds the actual serialization: only one mutating action at a time within a process. A second concurrent
caller gets a structured `busy` envelope instead of racing the first.

**Files:**
- Modify: `src/phonectl/runtime.py` (module-level `threading.Lock`; wrap the act region)
- Test: `tests/test_runtime.py` (append)

**Interfaces:**
- Module-level `_action_lock = threading.Lock()`.
- `run_action` acquires it **non-blocking** around the `build → ensure → observe → fn → log` region. If the
  lock is already held, return `results.err(errors.BusyError("another action is already in progress"), ...)`
  **before** building/observing. The kill-switch and confirm checks run before the lock (cheap, no I/O). The
  lock is always released in a `finally`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_runtime.py  (append)
def test_run_action_reports_busy_when_lock_held(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    runtime._action_lock.acquire()             # simulate another in-flight writer
    try:
        env = runtime.run_action("tap", lambda b, s: None, {"x": 1},
                                 build=lambda cfg: (_ for _ in ()).throw(AssertionError("no build")))
    finally:
        runtime._action_lock.release()
    assert env["ok"] is False
    assert env["error"]["code"] == "busy"
    assert env["error"]["retryable"] is True


def test_run_action_releases_lock_after_success(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    backend = FakeBackend(); sess = FakeSession()
    monkeypatch.setattr(runtime.observer, "observe",
                        lambda b, s, **kw: s.set_snapshot({"hash": "h"}))
    runtime.run_action("tap", lambda b, s: {"hash": "x"}, {"x": 1},
                       build=lambda cfg: (backend, sess, FakeConn()))
    assert runtime._action_lock.acquire(blocking=False) is True   # lock was released
    runtime._action_lock.release()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_runtime.py -v`
Expected: FAIL (`AttributeError: module 'phonectl.runtime' has no attribute '_action_lock'`).

- [ ] **Step 3: Write minimal implementation**

In `src/phonectl/runtime.py` add `import threading` and `_action_lock = threading.Lock()` at module scope,
then wrap the act region:

```python
    if not _action_lock.acquire(blocking=False):
        return results.err(errors.BusyError("another action is already in progress"), **base)
    try:
        backend, session, conn = build(cfg)
        conn.ensure()
        observer.observe(backend, session)
        if mode == "dry-run":
            return results.ok(capability=f"ui.{verb}", provider="adb",
                              data=session.last, dry_run=True, **base)
        snap = fn(backend, session)
        log(verb, target, snap, request_id=rid, cfg=cfg)
        return results.ok(capability=f"ui.{verb}", provider="adb", data=snap, **base)
    except errors.PhonectlError as e:
        return results.err(e, **getattr(e, "lock_state", {}), **base)
    finally:
        _action_lock.release()
```

Note: a non-blocking lock means a second writer fails fast with `busy` (`retryable=True`) rather than
deadlocking; the daemon (Phase 5) replaces this with a real action queue but keeps the same `busy` contract.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_runtime.py -v`
Expected: PASS (existing + 2 new).

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/runtime.py tests/test_runtime.py
git commit -m "feat: process-local single-writer lock with structured busy status"
```

---

### Task 4: Idempotency keys (process-local replay)

A caller that passes the same `idempotency_key` twice gets the first envelope back (flagged
`idempotent_replay`) instead of re-running the action — guarding the "accidental double execution" edge
(strategy §14.4) for the MCP/daemon callers that supply keys.

**Files:**
- Modify: `src/phonectl/runtime.py` (module-level cache; short-circuit + record)
- Test: `tests/test_runtime.py` (append)

**Interfaces:**
- Module-level `_idempotency_cache: dict[str, dict] = {}`.
- `run_action(..., idempotency_key=None)`: if `idempotency_key` is set and present in the cache, return a
  copy with `idempotent_replay=True` (no kill-switch/lock/act). On a fresh successful or errored run with a
  key, store the resulting envelope before returning.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_runtime.py  (append)
def test_idempotency_key_replays_first_envelope(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    runtime._idempotency_cache.clear()
    backend = FakeBackend(); sess = FakeSession()
    monkeypatch.setattr(runtime.observer, "observe",
                        lambda b, s, **kw: s.set_snapshot({"hash": "h"}))
    runs = []
    def fn(b, s):
        runs.append(1)
        return {"hash": f"after{len(runs)}"}
    build = lambda cfg: (backend, sess, FakeConn())
    first = runtime.run_action("tap", fn, {"x": 1}, build=build, idempotency_key="k1",
                               gen_id=lambda: "req1")
    second = runtime.run_action("tap", fn, {"x": 1}, build=build, idempotency_key="k1",
                                gen_id=lambda: "req2")
    assert runs == [1]                              # fn ran exactly once
    assert first["data"]["hash"] == "after1"
    assert second["data"]["hash"] == "after1"       # replayed, not re-run
    assert second["idempotent_replay"] is True
    assert second["request_id"] == "req1"           # original request id preserved
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_runtime.py -v`
Expected: FAIL (`run_action()` has no `idempotency_key`; second call re-runs `fn`).

- [ ] **Step 3: Write minimal implementation**

In `src/phonectl/runtime.py` add `_idempotency_cache: dict = {}` at module scope, add the param, and bracket
the body:

```python
def run_action(verb, fn, target, *, build, yes=False, cfg=None, request_id=None,
               idempotency_key=None, gen_id=_new_request_id,
               kill_switch=audit.kill_switch_active, log=audit.log_action) -> dict:
    if idempotency_key is not None and idempotency_key in _idempotency_cache:
        replay = dict(_idempotency_cache[idempotency_key])
        replay["idempotent_replay"] = True
        return replay
    cfg = config.load() if cfg is None else cfg
    rid = request_id or gen_id()
    base = {"verb": verb, "target": target, "request_id": rid}
    env = _run(verb, fn, target, base, build, yes, cfg,
               kill_switch=kill_switch, log=log)     # the Task 2/3 body, extracted
    if idempotency_key is not None:
        _idempotency_cache[idempotency_key] = env
    return env
```

Extract the kill-switch/confirm/lock/act body into a private `_run(verb, fn, target, base, build, yes, cfg,
*, kill_switch, log)` helper that returns the envelope (mechanical move of the Task 3 body, using the
passed-in `base`/`cfg`). Keep `_action_lock`/`_idempotency_cache` module-level so they persist across calls
in one process.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_runtime.py -v`
Expected: PASS (existing + 1 new).

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/runtime.py tests/test_runtime.py
git commit -m "feat: process-local idempotency-key replay for run_action"
```

---

### Task 5: `redact.py` — pure sensitive-string redaction

Re-homes the redaction half of the superseded safety-completeness plan. A pure module that scrubs OTP-like
codes, emails, phone numbers, card numbers, and URL tokens out of arbitrary audit payloads.

**Files:**
- Create: `src/phonectl/redact.py`
- Test: `tests/test_redact.py`

**Interfaces (all PURE — no I/O):**
- `redact_text(s: str) -> str` — replaces matches of: email, phone (`\+?\d[\d ()-]{7,}\d`), card
  (`\b\d{13,19}\b`), OTP/code (`\b\d{4,8}\b`), URL token (`(?:token|access_token|code|key)=[^&\s]+`) with
  `"[REDACTED]"`. Order matters: longer/structured patterns (email, card, URL token) run before the bare
  numeric OTP pattern so they win.
- `redact_value(v)` — recurse: `dict` → redact each value; `list/tuple` → redact each item; `str` →
  `redact_text`; anything else returned unchanged (so `{"i": 7}` is untouched).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_redact.py
from phonectl import redact


def test_redact_text_scrubs_otp_email_phone_card_and_token():
    assert redact.redact_text("your code is 482913") == "your code is [REDACTED]"
    assert "[REDACTED]" in redact.redact_text("mail me at a.b@example.com")
    assert "[REDACTED]" in redact.redact_text("call +1 (415) 555-2671 now")
    assert "[REDACTED]" in redact.redact_text("card 4111111111111111")
    assert "[REDACTED]" in redact.redact_text("https://x.test/cb?token=abc123def")


def test_redact_text_keeps_benign_labels():
    assert redact.redact_text("Wi-Fi") == "Wi-Fi"
    assert redact.redact_text("Connected devices") == "Connected devices"


def test_redact_value_recurses_and_leaves_non_strings():
    out = redact.redact_value({"i": 7, "selector": {"text": "code 123456"}, "xs": [1, "a@b.co"]})
    assert out["i"] == 7
    assert out["selector"]["text"] == "code [REDACTED]"
    assert out["xs"][0] == 1 and "[REDACTED]" in out["xs"][1]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_redact.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'phonectl.redact'`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/redact.py
"""Pure redaction of sensitive strings for the audit log (strategy §8.3). No I/O.
Conservative by design: a benign label like "Wi-Fi" is never touched, but OTP-like
codes, emails, phone numbers, card numbers, and URL tokens are scrubbed."""
from __future__ import annotations

import re

_MASK = "[REDACTED]"
_PATTERNS = [
    re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),                 # email
    re.compile(r"(?i)(?:token|access_token|code|key)=[^&\s]+"),  # URL token
    re.compile(r"\b\d{13,19}\b"),                                # card-like
    re.compile(r"\+?\d[\d ()\-]{7,}\d"),                         # phone-like
    re.compile(r"\b\d{4,8}\b"),                                  # OTP / short code
]


def redact_text(s: str) -> str:
    out = s
    for pat in _PATTERNS:
        out = pat.sub(_MASK, out)
    return out


def redact_value(v):
    if isinstance(v, dict):
        return {k: redact_value(val) for k, val in v.items()}
    if isinstance(v, (list, tuple)):
        return [redact_value(x) for x in v]
    if isinstance(v, str):
        return redact_text(v)
    return v
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_redact.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/redact.py tests/test_redact.py
git commit -m "feat: pure redact module (OTP/email/phone/card/URL-token scrubbing)"
```

---

### Task 6: `audit.py` v2 — levels, `request_id`, redaction-aware `log_action`

Upgrades `log_action` to honor `audit_level` and stamp `request_id`, using the Task-5 `redact` module. The
default level is `redacted`, which is a no-op on the non-sensitive targets the existing tests use, so they
stay green.

**Files:**
- Modify: `src/phonectl/audit.py` (`log_action` signature + level logic; add `audit_level(cfg)` helper)
- Test: `tests/test_config_audit.py` (append below existing tests)

**Interfaces:**
- `audit_level(cfg: dict | None = None) -> str` — `cfg.get("audit_level", "redacted")`; loads config when
  `cfg` is None.
- `log_action(verb, target, result, request_id=None, cfg=None) -> None` — backward-compatible (extra kwargs
  optional). Behavior by level:
  - `none` → write nothing.
  - `metadata` → record `{ts, verb, request_id, app, hash}` (no `target`).
  - `redacted` (default) → metadata + `"target": redact.redact_value(target)`.
  - `full` → metadata + `"target": target` (raw).
  The `app`/`hash` defaulting (`""`) is unchanged from today.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_config_audit.py  (append below existing tests)
import json as _json
from phonectl import config


def test_log_action_default_redacted_scrubs_sensitive_target(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    audit.log_action("type", {"text": "code 482913"}, {"app": {"package": "com.x"}, "hash": "h"},
                     request_id="r1")
    rec = _json.loads((tmp_path / "actions.jsonl").read_text().strip())
    assert rec["request_id"] == "r1"
    assert "482913" not in _json.dumps(rec)
    assert "[REDACTED]" in rec["target"]["text"]


def test_log_action_redacted_is_noop_on_benign_target(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    audit.log_action("tap", {"selector": {"text": "Wi-Fi"}}, {"app": {"package": "com.x"}, "hash": "h"})
    rec = _json.loads((tmp_path / "actions.jsonl").read_text().strip())
    assert rec["target"] == {"selector": {"text": "Wi-Fi"}}


def test_log_action_metadata_level_drops_target(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    config.save({"audit_level": "metadata"})
    audit.log_action("tap", {"i": 7}, {"app": {"package": "com.x"}, "hash": "h"}, request_id="r2")
    rec = _json.loads((tmp_path / "actions.jsonl").read_text().strip())
    assert "target" not in rec and rec["verb"] == "tap" and rec["request_id"] == "r2"


def test_log_action_none_level_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    config.save({"audit_level": "none"})
    audit.log_action("tap", {"i": 1}, {"app": {"package": "com.x"}, "hash": "h"})
    assert not (tmp_path / "actions.jsonl").exists()


def test_log_action_full_level_keeps_raw_target(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    config.save({"audit_level": "full"})
    audit.log_action("type", {"text": "code 482913"}, {"app": {}, "hash": "h"})
    rec = _json.loads((tmp_path / "actions.jsonl").read_text().strip())
    assert rec["target"]["text"] == "code 482913"
```

The existing `test_log_action_appends_jsonl` (target `{"i": 7}`) stays green: default `redacted` leaves
non-sensitive dicts unchanged.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_config_audit.py -v`
Expected: FAIL (`log_action()` takes 3 positional args / no level handling / no `request_id`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/audit.py
import json
import time
from phonectl import redact
from phonectl.config import config_dir, load


def kill_switch_active() -> bool:
    return (config_dir() / "STOP").exists()


def audit_level(cfg: dict | None = None) -> str:
    cfg = load() if cfg is None else cfg
    return cfg.get("audit_level", "redacted")


def log_action(verb: str, target: dict, result: dict,
               request_id: str | None = None, cfg: dict | None = None) -> None:
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
    # level == "metadata": no target key
    with open(config_dir() / "actions.jsonl", "a") as f:
        f.write(json.dumps(rec) + "\n")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_config_audit.py tests/test_runtime.py -v`
Expected: PASS (existing audit tests + 5 new; runtime success/idempotency tests now log cleanly).

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/audit.py tests/test_config_audit.py
git commit -m "feat: audit v2 levels (none/metadata/redacted/full) + request_id + redaction"
```

---

### Task 7: `audit tail|purge|export` — read/purge/export helpers + CLI verbs

Adds the operator-facing audit commands (strategy §8.3). The read/purge/export logic lives in `audit.py`
(pure-ish file helpers), and `cli` exposes them as `phonectl audit tail|purge|export`.

**Files:**
- Modify: `src/phonectl/audit.py` (add `read_entries`, `purge`, `export`)
- Modify: `src/phonectl/cli.py` (add the `audit` subcommand group)
- Test: `tests/test_config_audit.py` and `tests/test_cli.py` (append)

**Interfaces:**
- `audit.read_entries(limit: int | None = None) -> list[dict]` — parse `actions.jsonl`, newest last; return
  the last `limit` records (all when None); `[]` when the file is absent.
- `audit.purge() -> int` — delete `actions.jsonl`, returning the number of removed records (0 when absent).
- `audit.export(path, *, redacted=True) -> str` — write all entries to `path` as JSON; when `redacted`,
  apply `redact.redact_value` to each record's `target` before writing (already-redacted records are
  idempotent under re-redaction); return `path`.
- CLI: `phonectl audit tail [--limit N]` prints the last N records (one JSON per line); `phonectl audit
  purge` removes the log and prints the count; `phonectl audit export <path> [--no-redact]` writes the
  bundle and prints the path.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_config_audit.py  (append)
def test_read_entries_returns_last_n(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    config.save({"audit_level": "metadata"})
    for n in range(5):
        audit.log_action("tap", {"i": n}, {"app": {"package": "com.x"}, "hash": f"h{n}"})
    last2 = audit.read_entries(limit=2)
    assert [e["hash"] for e in last2] == ["h3", "h4"]


def test_purge_removes_log_and_counts(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    audit.log_action("tap", {"i": 0}, {"app": {}, "hash": "h"})
    assert audit.purge() == 1
    assert not (tmp_path / "actions.jsonl").exists()
    assert audit.purge() == 0


def test_export_redacts_targets(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    config.save({"audit_level": "full"})
    audit.log_action("type", {"text": "code 482913"}, {"app": {}, "hash": "h"})
    out = tmp_path / "bundle.json"
    audit.export(str(out), redacted=True)
    assert "482913" not in out.read_text()
```

```python
# tests/test_cli.py  (append)
def test_audit_tail_prints_recent(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    from phonectl import audit
    audit.log_action("tap", {"i": 1}, {"app": {"package": "com.x"}, "hash": "h1"})
    rc = cli.main(["audit", "tail", "--limit", "1"])
    out = capsys.readouterr().out
    assert rc == 0 and "h1" in out


def test_audit_purge_clears(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    from phonectl import audit
    audit.log_action("tap", {"i": 1}, {"app": {}, "hash": "h"})
    rc = cli.main(["audit", "purge"])
    assert rc == 0 and not (tmp_path / "actions.jsonl").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_config_audit.py tests/test_cli.py -v`
Expected: FAIL (`audit` has no `read_entries`/`purge`/`export`; CLI has no `audit` subcommand).

- [ ] **Step 3: Write minimal implementation**

Append to `src/phonectl/audit.py`:

```python
def _log_path():
    return config_dir() / "actions.jsonl"


def read_entries(limit: int | None = None) -> list[dict]:
    p = _log_path()
    if not p.exists():
        return []
    entries = [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()]
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
        for e in entries:
            if "target" in e:
                e["target"] = redact.redact_value(e["target"])
    with open(path, "w") as f:
        json.dump(entries, f, indent=2)
    return path
```

In `src/phonectl/cli.py` add the `audit` subcommand group and handlers:

```python
def _cmd_audit(args):
    if args.audit_cmd == "tail":
        for rec in audit.read_entries(limit=args.limit):
            print(json.dumps(rec))
        return 0
    if args.audit_cmd == "purge":
        n = audit.purge()
        print(f"phonectl: purged {n} audit record(s)")
        return 0
    if args.audit_cmd == "export":
        path = audit.export(args.path, redacted=not args.no_redact)
        print(f"phonectl: exported audit log to {path}")
        return 0
    print("phonectl: audit requires tail|purge|export")
    return 2
```

```python
    # build_parser: register the audit group
    au = sub.add_parser("audit")
    ausub = au.add_subparsers(dest="audit_cmd")
    at = ausub.add_parser("tail"); at.add_argument("--limit", type=int, default=20)
    ausub.add_parser("purge")
    ae = ausub.add_parser("export"); ae.add_argument("path"); ae.add_argument("--no-redact", action="store_true")
    au.set_defaults(func=_cmd_audit)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_config_audit.py tests/test_cli.py -v`
Expected: PASS (existing + 3 audit-module + 2 CLI tests).

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/audit.py src/phonectl/cli.py tests/test_config_audit.py tests/test_cli.py
git commit -m "feat: audit tail/purge/export helpers + phonectl audit subcommands"
```

---

### Task 8: Wire `cli._do_action` through `runtime.run_action`

Replace the bespoke `_do_action` gate with a thin adapter over `run_action`, mapping the returned envelope to
today's exit codes so every existing CLI test stays green while the funnel is now the single writer. Add the
`--request-id` / `--idempotency-key` passthrough flags to the action verbs.

**Files:**
- Modify: `src/phonectl/cli.py` (`_do_action`; action subparsers; import `runtime`)
- Test: `tests/test_cli.py` (append)

**Interfaces:**
- `_do_action(args, verb, fn, target)`: call `runtime.run_action(verb, fn, target, build=build_runtime,
  yes=getattr(args, "yes", False), request_id=getattr(args, "request_id", None),
  idempotency_key=getattr(args, "idempotency_key", None))`, then map:
  - `ok` envelope → if `args.json` print the envelope; elif `dry_run` print `phonectl: dry-run {verb}
    {target} (not executed)`; else `_emit(env["data"])`; return `0`.
  - `err` with code `stopped` → print `phonectl: {message}`; return `2`.
  - `err` with code `confirmation_required` → print the message; return `3`.
  - any other `err` → if `args.json` print the envelope; else `phonectl: {message}`; return `1`.
- The kill-switch (`_guard_action`) and mode checks move OUT of `cli` and INTO `run_action`; `_guard_action`
  is deleted (its behavior is now the `stopped` envelope). Action subparsers gain `--request-id` and
  `--idempotency-key` (default None) and, where missing, keep `--json` off (only `observe`/`doctor` print
  envelopes today; action verbs print the snapshot unless `--json`). Add `--json` to the action verbs so an
  agent can request the envelope.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cli.py  (append)
def test_tap_json_emits_run_action_envelope(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: FakeBackend())
    rc = cli.main(["tap", "--xy", "1", "2", "--json"])
    out = _json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["ok"] is True and out["verb"] == "tap"
    assert "request_id" in out


def test_tap_busy_when_lock_held_maps_to_exit_1(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: FakeBackend())
    from phonectl import runtime
    runtime._action_lock.acquire()
    try:
        rc = cli.main(["tap", "--xy", "1", "2", "--json"])
        out = _json.loads(capsys.readouterr().out)
    finally:
        runtime._action_lock.release()
    assert rc == 1 and out["error"]["code"] == "busy"
```

The existing `test_tap_blocked_by_kill_switch` (rc 2), `test_tap_confirm_mode_refuses_without_yes` (rc 3),
`test_tap_dry_run_observes_but_does_not_inject` (rc 0, no log), `test_tap_auto_mode_acts_and_logs`,
`test_type_redacts_text_in_audit_log`, and `test_tap_by_text_selector_resolves_and_logs` must all stay green
through the envelope→exit-code mapping.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cli.py -v`
Expected: FAIL (`tap` has no `--json`/`--request-id`; `_do_action` does not yet call `run_action`).

- [ ] **Step 3: Write minimal implementation**

In `src/phonectl/cli.py` import `runtime`, rewrite `_do_action`, delete `_guard_action`, and add the flags:

```python
from phonectl import runtime

def _do_action(args, verb, fn, target):
    env = runtime.run_action(
        verb, fn, target, build=build_runtime,
        yes=getattr(args, "yes", False),
        request_id=getattr(args, "request_id", None),
        idempotency_key=getattr(args, "idempotency_key", None),
    )
    if env["ok"]:
        if getattr(args, "json", False):
            print(json.dumps(env, indent=2))
        elif env.get("dry_run"):
            print(f"phonectl: dry-run {verb} {target} (not executed)")
        else:
            _emit(env["data"])
        return 0
    code = env["error"]["code"]
    if getattr(args, "json", False):
        print(json.dumps(env, indent=2))
    else:
        print(f"phonectl: {env['error']['message']}")
    return {"stopped": 2, "confirmation_required": 3}.get(code, 1)
```

Add `--json`, `--request-id`, `--idempotency-key` to each action subparser (tap/type/swipe/key/launch). A
small helper keeps `build_parser` tidy:

```python
    def _action_flags(sp):
        sp.add_argument("--yes", action="store_true")
        sp.add_argument("--json", action="store_true")
        sp.add_argument("--request-id", default=None)
        sp.add_argument("--idempotency-key", default=None)
```

Apply `_action_flags(...)` to each action subparser (replacing the bare `--yes` adds), keeping the
verb-specific positional/selector args unchanged.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cli.py -v`
Expected: PASS (all existing CLI tests + 2 new; exit codes 0/1/2/3 preserved).

- [ ] **Step 5: Run the full suite**

Run: `pytest -v`
Expected: PASS (errors, results, redact, runtime, audit, cli, and all prior tests).

- [ ] **Step 6: Commit**

```bash
git add src/phonectl/cli.py tests/test_cli.py
git commit -m "refactor: route cli action verbs through runtime.run_action single-writer funnel"
```

---

### Task 9: Docs — single-writer contract, request IDs, audit v2

**Files:**
- Modify: `README.md` (add a "Single-writer runtime & audit" section)
- Modify: `docs/superpowers/specs/2026-06-20-phonectl-adb-bridge-design.md` (note the funnel + audit-level
  invariant)

**Interfaces:** none (documentation).

- [ ] **Step 1: Document the contract**

In `README.md`: the `run_action` funnel (single writer, `request_id`, `busy`/`stopped`/`confirmation_required`
codes, `--idempotency-key`), the `audit_level` config key and the four levels, the redaction patterns, and
the `phonectl audit tail|purge|export` verbs with the `--json` action envelopes. In the design spec, add a
note that **all mutating actions now route through `runtime.run_action`** (one choke-point for
modes/kill-switch/audit, the daemon's future single writer) and that **audit is level-aware with redaction by
default**.

- [ ] **Step 2: Commit**

```bash
git add README.md docs/superpowers/specs/2026-06-20-phonectl-adb-bridge-design.md
git commit -m "docs: single-writer run_action funnel, request IDs, and audit v2 levels"
```

---

## Dependencies

**Plan 2.1 of the platform roadmap.** Requires **Plan 1.1** (`errors`, `results`) and lands on the **Plan
1.3** act path (`observe` raising structured lock-state). It depends on no later plan. Downstream:

- **Plan 2.2** (risk ledger) inserts `policy`/`ratelimit` checks INSIDE `run_action` and raises
  `errors.GuardedActionError`/`RateLimitError` into the same envelope.
- **Plan 2.3** (MCP) calls `run_action` from each action tool, passing `request_id`/`idempotency_key`/
  `dry_run` and returning its envelope verbatim.
- **Phase 5** (daemon) replaces the process-local lock with a real action queue and the process-local
  idempotency cache with durable run records, but keeps the `run_action` contract (`busy`/`stopped`/
  `request_id`) so it is a compatible evolution, not a rewrite (strategy §22).

## Deferred / out of scope (not in this plan)

- **Risk classification, rate-limit buckets, cooldowns, repeated-screen-hash stop** → **Plan 2.2**. This plan
  ships only the funnel + audit; the policy/rate checks slot into `run_action` next.
- **Durable, cross-process action serialization and idempotency** (the lock and idempotency cache are
  process-local) → the **daemon** (Phase 5) owns the persistent action queue + run records.
- **Cancellation tokens / stop-current-macro** (strategy §7.4) → the macro runtime (Phase 6); the hard global
  kill switch (`stopped`) is the only stop primitive here.
- **`redact_config` for diagnostics bundles** → **Plan 1.4** (`diagnostics.py`); this plan's `redact` is
  audit-payload scoped (text/value), not config-secret scoped.

## Notes on testability

Every layer is unit-tested with injected seams and `PHONECTL_HOME` isolation — no device, no real `adb`, no
wall-clock. `run_action` is tested with a fake `build` returning duck-typed backend/session/conn and a
monkeypatched `observer.observe`, so modes, kill-switch, lock, idempotency, and the error-catch are exercised
without `uiautomator`. `redact` is pure and fixture-tested. `audit` levels are tested by writing real
`actions.jsonl` files under `tmp_path` and re-reading them. The single-writer lock is exercised by acquiring
the module lock in-test and asserting the `busy` envelope. No test sleeps or talks to a device.
