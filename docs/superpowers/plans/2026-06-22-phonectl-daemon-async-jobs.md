# phonectl Daemon Async Job Model — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the daemon's slow device RPCs (`act`/`observe`/`find`) pollable async jobs run by one background worker, so a healthy-but-slow daemon never falsely reports `daemon_unreachable` and never silently double-executes.

**Architecture:** A new `JobRegistry` (in `daemon/jobs.py`) holds a bounded FIFO queue and a single worker. The server's `act`/`observe`/`find` RPC handlers submit a job and return a `job_id` immediately; the actual device work runs in the worker (refactored handler bodies, guarded by `_write_lock`). The frontend submits then polls `job_poll` until terminal (block-and-poll), with `--detach` + `phonectl job <id>` for fire-and-forget. Retries dedupe by `idempotency_key`. A new `shutdown` RPC actually terminates the daemon (distinct from the `stop` kill-switch sentinel).

**Tech Stack:** Python 3.10+, stdlib only (`threading`, `collections.deque`, `uuid`, `json`, `socket`/`selectors`), `pytest`.

## Global Constraints

- **Python ≥ 3.10** (project floor; `X | None` unions are used).
- **stdlib only** — no new third-party dependencies.
- **Daemon is loopback-only** — never bind/connect a non-loopback host (existing invariant).
- **TDD** — write the failing test first, watch it fail, implement minimally, watch it pass, commit.
- **Result envelopes** come from `phonectl.results.ok(...)` / `phonectl.results.err(...)`; typed errors live in `phonectl.errors`.
- **Existing suite is 434 passing, 1 skipped** — keep it green; the regression task updates tests changed by the new contract.
- **Config** is a flat dict from `config.load()` over `config.DEFAULTS`; read with `cfg.get("key", default)`.

---

## File Structure

- **Create** `src/phonectl/daemon/jobs.py` — `Job` dataclass + `JobRegistry` (submit/get/run_next/start/stop, dedupe, bounded queue, single worker).
- **Create** `tests/test_daemon_jobs.py` — unit tests for the registry.
- **Modify** `src/phonectl/errors.py` — add `JobTimeoutError`.
- **Modify** `src/phonectl/config.py` — add `act_timeout`, `sync_timeout`, `poll_interval`, `job_queue_max`, `idempotency_ttl` to `DEFAULTS`.
- **Modify** `src/phonectl/daemon/rpc.py` — drop `act` from `MUTATING` (act is now an async submit; the worker owns the write lock).
- **Modify** `src/phonectl/daemon/server.py` — refactor `act`/`observe`/`find` bodies into `_run_act/_run_observe/_run_find`; thin submit handlers; `_run_job` (lock + dispatch); wire `JobRegistry`; add `job_poll` and `shutdown` RPCs; start/stop the worker in serve lifecycle.
- **Modify** `src/phonectl/daemon/client.py` — distinguish connection-refused from read-timeout; add `submit_and_wait`.
- **Modify** `src/phonectl/cli.py` — async routing in `_dispatch`; auto `idempotency_key`; `--detach`; `phonectl job <id>`; `daemon stop` → `shutdown`.
- **Modify** `tests/test_config.py`, `tests/test_daemon_server.py`, `tests/test_daemon_client.py`, `tests/test_cli.py` — new + updated tests.

---

## Task 1: Config defaults for async jobs

**Files:**
- Modify: `src/phonectl/config.py:5-11` (the `DEFAULTS` dict)
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `config.DEFAULTS` gains keys `act_timeout` (float 60.0), `sync_timeout` (float 15.0), `poll_interval` (float 0.5), `job_queue_max` (int 8), `idempotency_ttl` (float 300.0).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_config.py`:

```python
def test_async_job_defaults_present():
    from phonectl import config
    cfg = config.load()
    assert cfg["act_timeout"] == 60.0
    assert cfg["sync_timeout"] == 15.0
    assert cfg["poll_interval"] == 0.5
    assert cfg["job_queue_max"] == 8
    assert cfg["idempotency_ttl"] == 300.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_config.py::test_async_job_defaults_present -v`
Expected: FAIL with `KeyError: 'act_timeout'`

- [ ] **Step 3: Add the defaults**

In `src/phonectl/config.py`, extend `DEFAULTS`:

```python
DEFAULTS: dict = {
    "companion_host": "127.0.0.1",
    "companion_port": None,
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_config.py::test_async_job_defaults_present -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/config.py tests/test_config.py
git commit -m "feat(config): defaults for daemon async job model"
```

---

## Task 2: `JobTimeoutError` in the error taxonomy

**Files:**
- Modify: `src/phonectl/errors.py` (append a class near `DaemonUnreachableError`)
- Test: `tests/test_daemon_client.py` (add a small taxonomy test)

**Interfaces:**
- Produces: `errors.JobTimeoutError` with `code = "job_timeout"`, `retryable = False`, `requires_user = True`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_daemon_client.py`:

```python
def test_job_timeout_error_shape():
    from phonectl import errors
    e = errors.JobTimeoutError("still running")
    assert e.code == "job_timeout"
    assert e.retryable is False
    assert e.requires_user is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_daemon_client.py::test_job_timeout_error_shape -v`
Expected: FAIL with `AttributeError: module 'phonectl.errors' has no attribute 'JobTimeoutError'`

- [ ] **Step 3: Add the error class**

In `src/phonectl/errors.py`, after `DaemonUnreachableError`:

```python
class JobTimeoutError(PhonectlError):
    # block-and-poll exceeded act_timeout; the job keeps running server-side.
    # NOT auto-retryable: re-running could double-execute. Reattach via the job id.
    code = "job_timeout"
    retryable = False
    requires_user = True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_daemon_client.py::test_job_timeout_error_shape -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/errors.py tests/test_daemon_client.py
git commit -m "feat(errors): JobTimeoutError for daemon block-and-poll cap"
```

---

## Task 3: `JobRegistry` core — submit, dedupe, bounded queue, synchronous `run_next`

**Files:**
- Create: `src/phonectl/daemon/jobs.py`
- Test: `tests/test_daemon_jobs.py`

**Interfaces:**
- Consumes: `phonectl.results`, `phonectl.errors.BusyError`.
- Produces:
  - `Job` dataclass: fields `job_id: str`, `method: str`, `params: dict`, `status: str` (`"queued"|"running"|"done"|"error"`), `result_env: dict | None`, `idempotency_key: str | None`, `ts_created: float`, `ts_started: float | None`, `ts_finished: float | None`.
  - `JobRegistry(run_fn, *, queue_max=8, idempotency_ttl=300.0, now=time.time, new_id=...)`:
    - `submit(method: str, params: dict) -> str` — returns a `job_id`; dedupes by `params["idempotency_key"]`; raises `errors.BusyError` when pending count ≥ `queue_max`.
    - `get(job_id: str) -> Job | None`
    - `run_next(block: bool = False, timeout: float | None = None) -> bool` — runs at most one queued job synchronously via `run_fn(method, params)`; returns `True` if it ran one, `False` if none available.
  - `run_fn` signature: `run_fn(method: str, params: dict) -> dict` (a result envelope).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_daemon_jobs.py`:

```python
from phonectl import errors, results
from phonectl.daemon.jobs import Job, JobRegistry


def _ok_runner(method, params):
    return results.ok(capability="test.run", data={"method": method, "p": params})


def _counting_runner():
    calls = {"n": 0}
    def run(method, params):
        calls["n"] += 1
        return results.ok(capability="test.run", data={"n": calls["n"]})
    return run, calls


def test_submit_returns_id_and_queues():
    reg = JobRegistry(_ok_runner)
    jid = reg.submit("observe", {})
    job = reg.get(jid)
    assert job is not None
    assert job.status == "queued"
    assert job.method == "observe"


def test_run_next_executes_and_stores_result():
    reg = JobRegistry(_ok_runner)
    jid = reg.submit("act", {"verb": "tap"})
    assert reg.run_next() is True
    job = reg.get(jid)
    assert job.status == "done"
    assert job.result_env["ok"] is True
    assert job.result_env["data"]["method"] == "act"


def test_run_next_returns_false_when_empty():
    reg = JobRegistry(_ok_runner)
    assert reg.run_next() is False


def test_failed_envelope_sets_error_status():
    def fail_runner(method, params):
        return results.err(("boom", "nope"))
    reg = JobRegistry(fail_runner)
    jid = reg.submit("act", {})
    reg.run_next()
    assert reg.get(jid).status == "error"


def test_runner_exception_becomes_internal_error():
    def raiser(method, params):
        raise RuntimeError("kaboom")
    reg = JobRegistry(raiser)
    jid = reg.submit("act", {})
    reg.run_next()
    job = reg.get(jid)
    assert job.status == "error"
    assert job.result_env["error"]["code"] == "internal_error"


def test_dedupe_returns_same_job_for_inflight_key():
    run, calls = _counting_runner()
    reg = JobRegistry(run)
    j1 = reg.submit("act", {"idempotency_key": "k1"})
    j2 = reg.submit("act", {"idempotency_key": "k1"})  # still queued -> same job
    assert j1 == j2
    reg.run_next()
    assert calls["n"] == 1  # ran once


def test_dedupe_returns_finished_job_within_ttl():
    run, calls = _counting_runner()
    t = {"now": 1000.0}
    reg = JobRegistry(run, idempotency_ttl=300.0, now=lambda: t["now"])
    j1 = reg.submit("act", {"idempotency_key": "k1"})
    reg.run_next()
    t["now"] = 1100.0  # 100s later, within ttl
    j2 = reg.submit("act", {"idempotency_key": "k1"})
    assert j1 == j2
    assert calls["n"] == 1  # not re-run


def test_dedupe_expires_after_ttl():
    run, calls = _counting_runner()
    t = {"now": 1000.0}
    reg = JobRegistry(run, idempotency_ttl=300.0, now=lambda: t["now"])
    reg.submit("act", {"idempotency_key": "k1"})
    reg.run_next()
    t["now"] = 1400.0  # 400s later, past ttl
    reg.submit("act", {"idempotency_key": "k1"})
    reg.run_next()
    assert calls["n"] == 2  # re-run after expiry


def test_queue_cap_raises_busy():
    reg = JobRegistry(_ok_runner, queue_max=2)
    reg.submit("act", {"idempotency_key": "a"})
    reg.submit("act", {"idempotency_key": "b"})
    try:
        reg.submit("act", {"idempotency_key": "c"})
        assert False, "expected BusyError"
    except errors.BusyError:
        pass
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_daemon_jobs.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'phonectl.daemon.jobs'`

- [ ] **Step 3: Implement `jobs.py` (core, no thread yet)**

Create `src/phonectl/daemon/jobs.py`:

```python
"""JobRegistry: bounded FIFO of device jobs + single worker (single-writer)."""
from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass

from phonectl import errors, results


@dataclass
class Job:
    job_id: str
    method: str
    params: dict
    status: str = "queued"          # queued | running | done | error
    result_env: dict | None = None
    idempotency_key: str | None = None
    ts_created: float = 0.0
    ts_started: float | None = None
    ts_finished: float | None = None


_TERMINAL = {"done", "error"}


class JobRegistry:
    def __init__(self, run_fn, *, queue_max=8, idempotency_ttl=300.0,
                 now=time.time, new_id=None) -> None:
        self._run_fn = run_fn
        self._queue_max = queue_max
        self._ttl = idempotency_ttl
        self._now = now
        self._new_id = new_id or (lambda: uuid.uuid4().hex)
        self._jobs: dict[str, Job] = {}
        self._by_key: dict[str, str] = {}      # idempotency_key -> job_id
        self._queue: deque[str] = deque()
        self._cv = threading.Condition()
        self._stopped = False
        self._worker: threading.Thread | None = None

    # ── submission / lookup ─────────────────────────────────────────────
    def submit(self, method: str, params: dict) -> str:
        key = params.get("idempotency_key")
        with self._cv:
            existing = self._dedupe_locked(key)
            if existing is not None:
                return existing
            if len(self._queue) >= self._queue_max:
                raise errors.BusyError(
                    f"job queue full ({self._queue_max}); retry shortly")
            jid = self._new_id()
            self._jobs[jid] = Job(
                job_id=jid, method=method, params=params,
                idempotency_key=key, ts_created=self._now(),
            )
            if key is not None:
                self._by_key[key] = jid
            self._queue.append(jid)
            self._cv.notify_all()
            return jid

    def _dedupe_locked(self, key):
        if key is None:
            return None
        jid = self._by_key.get(key)
        if jid is None:
            return None
        job = self._jobs.get(jid)
        if job is None:
            return None
        if job.status not in _TERMINAL:
            return jid                                   # queued or running
        if job.ts_finished is not None and (self._now() - job.ts_finished) < self._ttl:
            return jid                                   # finished within ttl
        return None

    def get(self, job_id: str) -> Job | None:
        with self._cv:
            return self._jobs.get(job_id)

    # ── execution ───────────────────────────────────────────────────────
    def run_next(self, block: bool = False, timeout: float | None = None) -> bool:
        with self._cv:
            while not self._queue:
                if not block or self._stopped:
                    return False
                self._cv.wait(timeout=timeout)
                if self._stopped:
                    return False
            jid = self._queue.popleft()
            job = self._jobs[jid]
            job.status = "running"
            job.ts_started = self._now()
        try:
            env = self._run_fn(job.method, job.params)
        except Exception as exc:  # noqa: BLE001 — never let the worker die
            env = results.err(("internal_error", str(exc)))
        with self._cv:
            job.result_env = env
            job.status = "done" if env.get("ok") else "error"
            job.ts_finished = self._now()
        return True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_daemon_jobs.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/daemon/jobs.py tests/test_daemon_jobs.py
git commit -m "feat(daemon): JobRegistry core — submit, dedupe, bounded queue, run_next"
```

---

## Task 4: `JobRegistry` worker thread (start/stop)

**Files:**
- Modify: `src/phonectl/daemon/jobs.py` (add `start`/`stop`/`_loop`)
- Test: `tests/test_daemon_jobs.py`

**Interfaces:**
- Produces: `JobRegistry.start() -> None` (spawns a daemon worker thread looping `run_next(block=True, timeout=0.5)`); `JobRegistry.stop() -> None` (signals stop, joins the thread).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_daemon_jobs.py`:

```python
def test_worker_thread_drains_submitted_job():
    import time as _t
    reg = JobRegistry(_ok_runner)
    reg.start()
    try:
        jid = reg.submit("observe", {})
        deadline = _t.monotonic() + 2.0
        while _t.monotonic() < deadline and reg.get(jid).status != "done":
            _t.sleep(0.01)
        assert reg.get(jid).status == "done"
    finally:
        reg.stop()


def test_stop_is_idempotent_and_joins():
    reg = JobRegistry(_ok_runner)
    reg.start()
    reg.stop()
    reg.stop()  # must not raise
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_daemon_jobs.py::test_worker_thread_drains_submitted_job -v`
Expected: FAIL with `AttributeError: 'JobRegistry' object has no attribute 'start'`

- [ ] **Step 3: Add the worker methods**

Append to the `JobRegistry` class in `src/phonectl/daemon/jobs.py`:

```python
    # ── worker lifecycle ────────────────────────────────────────────────
    def start(self) -> None:
        if self._worker is not None:
            return
        self._stopped = False
        self._worker = threading.Thread(
            target=self._loop, name="phonectl-job-worker", daemon=True)
        self._worker.start()

    def _loop(self) -> None:
        while not self._stopped:
            self.run_next(block=True, timeout=0.5)

    def stop(self) -> None:
        with self._cv:
            self._stopped = True
            self._cv.notify_all()
        worker, self._worker = self._worker, None
        if worker is not None:
            worker.join(timeout=2.0)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_daemon_jobs.py -v`
Expected: PASS (11 tests)

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/daemon/jobs.py tests/test_daemon_jobs.py
git commit -m "feat(daemon): JobRegistry worker thread (start/stop)"
```

---

## Task 5: Server — refactor act/observe/find into worker run-fns + async submit + `job_poll`

**Files:**
- Modify: `src/phonectl/daemon/rpc.py:6` (`MUTATING`)
- Modify: `src/phonectl/daemon/server.py` (imports, `__init__`, `_register_builtins` for `act`/`observe`/`find`/`job_poll`, new `_run_act/_run_observe/_run_find/_run_job`)
- Test: `tests/test_daemon_server.py`

**Interfaces:**
- Consumes: `JobRegistry` (Task 3/4), `cfg["job_queue_max"]`, `cfg["idempotency_ttl"]`.
- Produces:
  - `DaemonServer.jobs: JobRegistry` attribute.
  - RPC `act`/`observe`/`find` return `results.ok(capability="daemon.job_accepted", data={"job_id": <hex>, "status": <str>})`.
  - RPC `job_poll` (params `{"job_id": str}`) returns `results.ok(capability="daemon.job_poll", data={"status": str, "result": dict | None})`; unknown id → `results.err(("unknown_job", ...))`.
  - `DaemonServer._run_job(method, params) -> dict` acquires `self._write_lock` then dispatches to `_run_act`/`_run_observe`/`_run_find`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_daemon_server.py` (the existing `_srv`, `_FakeBackend`, `_req`, `PROTOCOL_VERSION` helpers are reused). Add this local helper near the top of the file (after `_srv`):

```python
def _submit_run_poll(srv, method, params, rid="j1"):
    """Drive an async job to completion synchronously: submit -> run_next -> poll."""
    acc = json.loads(srv.handle_line(_req(method, params, rid)))
    assert acc["ok"] is True, acc
    job_id = acc["data"]["job_id"]
    assert srv.jobs.run_next() is True
    polled = json.loads(srv.handle_line(_req("job_poll", {"job_id": job_id})))
    return acc, polled
```

Then the new tests:

```python
def test_act_submit_returns_job_id(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    srv = _srv(tmp_path)
    acc = json.loads(srv.handle_line(_req("act", {"verb": "tap", "target": {"i": 0}, "i": 0})))
    assert acc["ok"] is True
    assert acc["data"]["status"] == "accepted"
    assert isinstance(acc["data"]["job_id"], str) and acc["data"]["job_id"]


def test_act_job_poll_returns_result_when_done(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    srv = _srv(tmp_path)
    _acc, polled = _submit_run_poll(srv, "act", {"verb": "tap", "target": {"i": 0}, "i": 0})
    assert polled["ok"] is True
    assert polled["data"]["status"] == "done"
    assert polled["data"]["result"]["ok"] is True


def test_observe_job_poll_returns_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    srv = _srv(tmp_path)
    _acc, polled = _submit_run_poll(srv, "observe", {})
    assert polled["data"]["status"] == "done"
    assert "hash" in polled["data"]["result"]["data"]
    assert "snapshot_id" in polled["data"]["result"]


def test_job_poll_unknown_id_is_error(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    srv = _srv(tmp_path)
    resp = json.loads(srv.handle_line(_req("job_poll", {"job_id": "nope"})))
    assert resp["ok"] is False
    assert resp["error"]["code"] == "unknown_job"


def test_act_via_worker_appends_one_run_record(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    from phonectl.daemon import records
    srv = _srv(tmp_path)
    _submit_run_poll(srv, "act", {"verb": "tap", "target": {"i": 0}, "i": 0}, rid="rr")
    rows = records.read()
    assert len(rows) == 1
    assert rows[0]["verb"] == "tap"


def test_act_is_not_in_handle_line_mutating_set():
    from phonectl.daemon import rpc as rpc_mod
    assert "act" not in rpc_mod.MUTATING
    assert "stop" in rpc_mod.MUTATING and "resume" in rpc_mod.MUTATING
```

You must also UPDATE the existing tests that assumed synchronous `act`/`observe` envelopes. Replace these existing test bodies:

- `test_act_reuses_one_registry_across_two_calls` — replace its two `handle_line(line)` + `r1/r2` assertions with two `_submit_run_poll(srv, "act", {"verb": "tap", "target": {"i": 0}, "i": 0})` calls and assert each `polled["data"]["result"]["ok"] is True`, keeping the `assert build_calls["n"] == 1`.
- `test_observe_method_returns_snapshot` — replace with `_acc, polled = _submit_run_poll(srv, "observe", {})` then `assert "hash" in polled["data"]["result"]["data"]`.
- `test_act_appends_run_record` — replace its inline submit with `_submit_run_poll(srv, "act", {"verb": "tap", "target": {"i": 0}, "i": 0}, rid="rr")` (the new `test_act_via_worker_appends_one_run_record` supersedes it; delete the old one to avoid duplication).
- `test_observe_returns_snapshot_id`, `test_second_observe_increments_snapshot_id`, `test_act_rejects_stale_snapshot_id_before_running`, `test_act_returns_before_and_after_snapshot_ids`, `test_act_backfills_runs_jsonl_snapshot_fields`, `test_act_emits_started_and_finished_events` — these assert on the action envelope. Convert each to use `_submit_run_poll(...)` and read the action envelope from `polled["data"]["result"]` instead of the direct `handle_line` return. (Snapshot ids, stale-snapshot rejection, before/after fields, and started/finished events are all produced inside `_run_act`, so they appear in `result`.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_daemon_server.py -v`
Expected: FAIL — new tests error on missing `srv.jobs` / `job_poll`; converted tests fail because `act`/`observe` no longer return the action envelope directly.

- [ ] **Step 3: Implement the server changes**

3a. In `src/phonectl/daemon/rpc.py`, drop `act` from the lock set:

```python
MUTATING = {"stop", "resume"}
```

3b. In `src/phonectl/daemon/server.py`, add the import near the top (with the other `from phonectl.daemon import ...` lines):

```python
from phonectl.daemon.jobs import JobRegistry
```

3c. In `DaemonServer.__init__`, after `self.events = EventBus()` and before the poller line, create the registry:

```python
        self.jobs = JobRegistry(
            self._run_job,
            queue_max=cfg.get("job_queue_max", 8),
            idempotency_ttl=cfg.get("idempotency_ttl", 300.0),
            now=now,
        )
```

3d. Refactor the handlers. In `_register_builtins`, REPLACE the existing `@self.registry.register("act")` block (the whole `def _act(params, ctx): ...` body through its `return env`) with a thin submit handler:

```python
        @self.registry.register("act")
        def _act(params, ctx):
            p = dict(params)
            p["request_id"] = ctx.get("request_id")
            job_id = self.jobs.submit("act", p)
            return results.ok(
                capability="daemon.job_accepted",
                data={"job_id": job_id, "status": self.jobs.get(job_id).status
                      if self.jobs.get(job_id).status != "queued" else "accepted"},
            )
```

REPLACE the existing `@self.registry.register("observe")` and `@self.registry.register("find")` blocks with thin submit handlers:

```python
        @self.registry.register("observe")
        def _observe(params, ctx):
            p = dict(params)
            p["request_id"] = ctx.get("request_id")
            job_id = self.jobs.submit("observe", p)
            return results.ok(capability="daemon.job_accepted",
                              data={"job_id": job_id, "status": "accepted"})

        @self.registry.register("find")
        def _find(params, ctx):
            p = dict(params)
            p["request_id"] = ctx.get("request_id")
            job_id = self.jobs.submit("find", p)
            return results.ok(capability="daemon.job_accepted",
                              data={"job_id": job_id, "status": "accepted"})
```

3e. Add the `job_poll` handler inside `_register_builtins` (next to `status`):

```python
        @self.registry.register("job_poll")
        def _job_poll(params, ctx):
            job = self.jobs.get(params.get("job_id"))
            if job is None:
                return results.err(
                    ("unknown_job", f"no job {params.get('job_id')!r}"),
                    user_action="Submit the action again; the job id is unknown to this daemon.",
                )
            return results.ok(
                capability="daemon.job_poll",
                data={"status": job.status, "result": job.result_env},
            )
```

3f. Add the worker run-fns as methods on `DaemonServer` (place them right after `_register_builtins`, before `_warm_triple` is fine too). `_run_act` is the OLD act handler body, now reading `request_id` from `params` and taking no `ctx`. `_run_observe`/`_run_find` are the OLD observe/find bodies:

```python
    # ── worker run-fns (executed by the JobRegistry worker) ──────────────
    def _run_job(self, method, params):
        with self._write_lock:
            if method == "act":
                return self._run_act(params)
            if method == "observe":
                return self._run_observe(params)
            if method == "find":
                return self._run_find(params)
            return results.err(("internal_error", f"no run-fn for {method!r}"))

    def _run_act(self, params):
        from phonectl import runtime
        import uuid
        verb = params["verb"]
        target = params.get("target", {})
        request_id = params.get("request_id")

        try:
            self.snapshots.validate(
                params.get("snapshot_id"),
                current_foreground=self.snapshots.current_foreground,
            )
        except errors.StaleSnapshotError as exc:
            env = results.err(
                exc,
                user_action="Re-observe (the screen changed); resolve the index against a fresh snapshot.",
                request_id=request_id,
            )
            self.events.publish(
                "action_finished",
                {"verb": verb, "target": target, "request_id": request_id,
                 "ok": False, "snapshot_after": None},
                source="daemon",
            )
            return env

        self.events.publish(
            "action_started",
            {"verb": verb, "target": target, "request_id": request_id},
            source="daemon",
        )

        fn = self._fn_for(params)
        snapshot_before = self.snapshots.current_id

        def warm_build(cfg):
            return self._warm_triple()

        env = runtime.run_action(
            verb, fn, target,
            build=warm_build,
            yes=bool(params.get("yes", False)),
            cfg=self._cfg,
            request_id=request_id,
            idempotency_key=params.get("idempotency_key"),
        )

        snapshot_after = None
        _, session, _ = self._warm_triple()
        if env.get("ok") and session.last is not None:
            snapshot_after = self.snapshots.put(session.last)
            env["snapshot_before"] = snapshot_before
            env["snapshot_after"] = snapshot_after

        self.events.publish(
            "action_finished",
            {"verb": verb, "target": target, "request_id": request_id,
             "ok": bool(env.get("ok")), "snapshot_after": snapshot_after},
            source="daemon",
        )

        from phonectl.daemon import records as _records
        rec = _records.build_record(env, params, action_id=uuid.uuid4().hex, now=self._now)
        rec["snapshot_before"] = snapshot_before
        rec["snapshot_after"] = snapshot_after
        self._append_record(rec)
        return env

    def _run_observe(self, params):
        reg, session, conn = self._warm_triple()
        if hasattr(conn, "ensure"):
            conn.ensure()
        snap = observer.observe(reg, session, **{
            k: params[k] for k in ("screenshot", "snap_path", "tree", "relations")
            if k in params
        })
        snapshot_id = self.snapshots.put(snap)
        return results.ok(
            capability="ui.observe",
            provider=getattr(reg, "last_used", None) or "adb",
            data=snap,
            snapshot_id=snapshot_id,
        )

    def _run_find(self, params):
        reg, session, conn = self._warm_triple()
        if hasattr(conn, "ensure"):
            conn.ensure()
        observer.observe(reg, session)
        matches = session.find(params.get("selector", {}))
        return results.ok(capability="ui.find", data={"matches": matches})
```

(The `_fn_for` method already exists and is reused unchanged.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_daemon_server.py -v`
Expected: PASS (existing converted tests + the 6 new tests)

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/daemon/server.py src/phonectl/daemon/rpc.py tests/test_daemon_server.py
git commit -m "feat(daemon): act/observe/find become async jobs via JobRegistry + job_poll"
```

---

## Task 6: Server — real `shutdown` RPC + worker lifecycle in serve loop

**Files:**
- Modify: `src/phonectl/daemon/server.py` (`_register_builtins` add `shutdown`; `serve_forever` start worker + cleanup in finally; make `shutdown()` stop the worker; idempotent)
- Test: `tests/test_daemon_server.py`

**Interfaces:**
- Produces: RPC `shutdown` returns `results.ok(capability="daemon.shutdown", data={"stopping": True})` and sets `self._running = False`. `DaemonServer.shutdown()` additionally calls `self.jobs.stop()` and is safe to call twice.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_daemon_server.py`:

```python
def test_shutdown_rpc_flags_not_running_and_returns_ok(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    srv = _srv(tmp_path)
    srv._running = True
    resp = json.loads(srv.handle_line(_req("shutdown")))
    assert resp["ok"] is True
    assert resp["data"]["stopping"] is True
    assert srv._running is False


def test_shutdown_method_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    srv = _srv(tmp_path)
    srv.shutdown()
    srv.shutdown()  # must not raise


def test_shutdown_in_methods_list(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    srv = _srv(tmp_path)
    resp = json.loads(srv.handle_line(_req("status")))
    assert "shutdown" in resp["data"]["methods"]
    assert "job_poll" in resp["data"]["methods"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_daemon_server.py -k shutdown -v`
Expected: FAIL — `shutdown` is not a registered method (`unknown_method`).

- [ ] **Step 3: Implement shutdown + worker lifecycle**

3a. Add the `shutdown` handler in `_register_builtins` (next to `stop`):

```python
        @self.registry.register("shutdown")
        def _shutdown(params, ctx):
            self._running = False
            return results.ok(capability="daemon.shutdown", data={"stopping": True})
```

3b. Start the worker when serving, and clean up in the finally. Replace the existing `serve_forever` body:

```python
    def serve_forever(self):
        import selectors
        self._running = True
        self.jobs.start()
        sel = selectors.DefaultSelector()
        sel.register(self._sock, selectors.EVENT_READ)
        try:
            while self._running:
                for key, _ in sel.select(timeout=0.5):
                    conn, _addr = key.fileobj.accept()
                    self._serve_conn(conn)
        finally:
            sel.close()
            self.shutdown()
```

3c. Make `shutdown()` also stop the worker and be idempotent. Replace the existing `shutdown` method:

```python
    def shutdown(self):
        from phonectl.daemon import discovery
        if not getattr(self, "_shutdown_done", False):
            self._publish_lifecycle("stopped")
        self._shutdown_done = True
        self._running = False
        self.jobs.stop()
        if self._sock is not None:
            self._sock.close()
            self._sock = None
        discovery.remove()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_daemon_server.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/daemon/server.py tests/test_daemon_server.py
git commit -m "feat(daemon): real shutdown RPC + worker lifecycle in serve loop"
```

---

## Task 7: Client — connection-refused vs timeout, and `submit_and_wait`

**Files:**
- Modify: `src/phonectl/daemon/client.py`
- Test: `tests/test_daemon_client.py`

**Interfaces:**
- Consumes: `errors.JobTimeoutError` (Task 2), server `job_poll` contract (Task 5).
- Produces: `DaemonClient.submit_and_wait(method, params, *, overall_timeout, poll_interval=0.5, sleep=time.sleep, now=time.monotonic) -> dict` — submits, polls `job_poll` until terminal, returns the inner `result` envelope; on cap returns `results.err(JobTimeoutError(...), job_id=...)`. `call()` maps `ConnectionError`/`OSError` (refused/no listener) to `DaemonUnreachableError` but a `socket.timeout`/`TimeoutError` (reachable, slow) to a `timeout` envelope.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_daemon_client.py`:

```python
import socket
import time as _time

from phonectl import results


def test_call_timeout_is_not_unreachable():
    def responder(method, params, rid):
        raise socket.timeout("timed out")
    c = DaemonClient("127.0.0.1", 8799, transport=FakeTransport(responder))
    out = c.call("status", {})
    assert out["ok"] is False
    assert out["error"]["code"] == "timeout"   # NOT daemon_unreachable


def test_call_connection_refused_is_unreachable():
    def responder(method, params, rid):
        raise ConnectionRefusedError("refused")
    c = DaemonClient("127.0.0.1", 8799, transport=FakeTransport(responder))
    out = c.call("ping", {})
    assert out["error"]["code"] == "daemon_unreachable"


def test_submit_and_wait_returns_inner_result_on_done():
    state = {"polls": 0}

    def responder(method, params, rid):
        if method == "act":
            return {"ok": True, "request_id": rid, "version": PROTOCOL_VERSION,
                    "data": {"job_id": "J1", "status": "accepted"}}
        if method == "job_poll":
            state["polls"] += 1
            status = "done" if state["polls"] >= 2 else "running"
            result = {"ok": True, "data": {"tapped": True}} if status == "done" else None
            return {"ok": True, "request_id": rid, "version": PROTOCOL_VERSION,
                    "data": {"status": status, "result": result}}
        raise AssertionError(method)

    c = DaemonClient("127.0.0.1", 8799, transport=FakeTransport(responder))
    out = c.submit_and_wait("act", {}, overall_timeout=5.0, poll_interval=0.0,
                            sleep=lambda s: None)
    assert out["ok"] is True
    assert out["data"]["tapped"] is True


def test_submit_and_wait_caps_with_job_timeout():
    def responder(method, params, rid):
        if method == "act":
            return {"ok": True, "request_id": rid, "version": PROTOCOL_VERSION,
                    "data": {"job_id": "J9", "status": "accepted"}}
        if method == "job_poll":
            return {"ok": True, "request_id": rid, "version": PROTOCOL_VERSION,
                    "data": {"status": "running", "result": None}}
        raise AssertionError(method)

    clock = {"t": 0.0}

    def fake_now():
        clock["t"] += 1.0   # each call advances 1s
        return clock["t"]

    c = DaemonClient("127.0.0.1", 8799, transport=FakeTransport(responder))
    out = c.submit_and_wait("act", {}, overall_timeout=3.0, poll_interval=0.0,
                            sleep=lambda s: None, now=fake_now)
    assert out["ok"] is False
    assert out["error"]["code"] == "job_timeout"
    assert out["job_id"] == "J9"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_daemon_client.py -v`
Expected: FAIL — `submit_and_wait` missing; `call` maps `socket.timeout` to `daemon_unreachable`.

- [ ] **Step 3: Implement client changes**

In `src/phonectl/daemon/client.py`, add imports and update `call`, then add `submit_and_wait`:

```python
import socket
import time
```

Replace the `call` method's `try/except` so timeouts are distinguished:

```python
    def call(self, method, params=None, *, timeout=5.0) -> dict:
        rid = next_request_id()
        try:
            resp = self._transport.request(method, params or {}, request_id=rid, timeout=timeout)
        except (socket.timeout, TimeoutError):
            return results.err(("timeout", f"daemon call {method!r} timed out"))
        except (ConnectionError, OSError):
            return results.err(errors.DaemonUnreachableError(f"daemon {method!r} unreachable"))
        if not isinstance(resp, dict) or resp.get("request_id") not in (rid, None):
            return results.err(errors.DaemonUnreachableError("no matching daemon response"))
        if resp.get("ok") is None and "error" not in resp:
            return results.err(errors.DaemonUnreachableError("malformed daemon response"))
        return resp
```

Add `submit_and_wait` (after `is_running`):

```python
    def submit_and_wait(self, method, params=None, *, overall_timeout,
                        poll_interval=0.5, sleep=time.sleep, now=time.monotonic) -> dict:
        acc = self.call(method, params or {})
        if not acc.get("ok"):
            return acc                                   # unreachable / busy / timeout
        job_id = acc["data"]["job_id"]
        deadline = now() + overall_timeout
        while now() < deadline:
            polled = self.call("job_poll", {"job_id": job_id})
            if not polled.get("ok"):
                return polled
            data = polled["data"]
            if data["status"] in ("done", "error"):
                return data["result"]
            sleep(poll_interval)
        return results.err(
            errors.JobTimeoutError(
                f"job {job_id} still running after {overall_timeout}s"),
            user_action=f"The action is still running. Query it with: phonectl job {job_id}",
            job_id=job_id,
        )
```

Note: the `now()`-advancing test uses `overall_timeout=3.0`; `fake_now` adds 1.0 per call, so `deadline` is set after the first `now()` (=1.0) → 4.0, and the loop's `now()` reaches 4.0 on the third call (>= deadline) → caps. This is deterministic.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_daemon_client.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/daemon/client.py tests/test_daemon_client.py
git commit -m "feat(daemon): client submit_and_wait + timeout!=unreachable mapping"
```

---

## Task 8: CLI — async routing, idempotency key, `--detach`, `job` command, `daemon stop` → `shutdown`

**Files:**
- Modify: `src/phonectl/cli.py` (`_dispatch`, `_act_params`, `_do_action`, observe dispatch, `_cmd_daemon` stop branch, new `_cmd_job`, parser additions, `_action_flags`)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `DaemonClient.submit_and_wait` (Task 7), `cfg["act_timeout"]`/`cfg["poll_interval"]`.
- Produces:
  - `_dispatch(method, params, in_process_fn, *, cfg=None, async_job=False, detach=False)` — when daemon reachable and `async_job`, routes through `submit_and_wait` (or submit-only when `detach`); else falls back to `in_process_fn`.
  - `_act_params` auto-generates `idempotency_key` (uuid4 hex) and `request_id` when absent.
  - `phonectl job <id> [--wait]` command (`_cmd_job`).
  - `phonectl daemon stop` calls the `shutdown` RPC.
  - `--detach` flag available on action verbs via `_action_flags`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cli.py` (follow the file's existing style for invoking the CLI; these use a fake daemon client injected via monkeypatch of `cli._daemon_client`):

```python
import json as _json
from phonectl import cli


class _FakeClient:
    def __init__(self, **scripted):
        self.scripted = scripted
        self.calls = []

    def submit_and_wait(self, method, params=None, *, overall_timeout,
                        poll_interval=0.5):
        self.calls.append(("submit_and_wait", method, params))
        return self.scripted["submit_and_wait"]

    def call(self, method, params=None, *, timeout=5.0):
        self.calls.append(("call", method, params))
        return self.scripted.get(method, {"ok": True, "data": {}})


def test_act_routes_through_submit_and_wait(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    fake = _FakeClient(submit_and_wait={"ok": True, "data": {"tapped": True},
                                        "capability": "ui.act"})
    monkeypatch.setattr(cli, "_daemon_client", lambda cfg: fake)
    rc = cli.main(["tap", "--xy", "10", "20", "--json"])
    assert rc == 0
    assert any(c[0] == "submit_and_wait" and c[1] == "act" for c in fake.calls)
    out = _json.loads(capsys.readouterr().out)
    assert out["data"]["tapped"] is True


def test_act_params_autogenerates_idempotency_key():
    import argparse
    args = argparse.Namespace(yes=False, request_id=None, idempotency_key=None)
    p = cli._act_params(args, "tap", {"x": 1, "y": 2})
    assert isinstance(p["idempotency_key"], str) and p["idempotency_key"]
    assert isinstance(p["request_id"], str) and p["request_id"]


def test_detach_prints_job_id(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    fake = _FakeClient(act={"ok": True, "data": {"job_id": "JID42", "status": "accepted"}})
    monkeypatch.setattr(cli, "_daemon_client", lambda cfg: fake)
    rc = cli.main(["tap", "--xy", "10", "20", "--detach"])
    assert rc == 0
    assert "JID42" in capsys.readouterr().out


def test_job_command_polls_and_prints(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    fake = _FakeClient(job_poll={"ok": True, "data": {"status": "done",
                                "result": {"ok": True, "data": {"done": True}}}})
    monkeypatch.setattr(cli, "_daemon_client", lambda cfg: fake)
    rc = cli.main(["job", "JID42", "--json"])
    assert rc == 0
    out = _json.loads(capsys.readouterr().out)
    assert out["data"]["status"] == "done"


def test_daemon_stop_calls_shutdown_rpc(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    fake = _FakeClient(shutdown={"ok": True, "data": {"stopping": True}})
    monkeypatch.setattr(cli, "_daemon_client", lambda cfg: fake)
    rc = cli.main(["daemon", "stop"])
    assert rc == 0
    assert any(c[0] == "call" and c[1] == "shutdown" for c in fake.calls)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_cli.py -k "submit_and_wait or idempotency or detach or job_command or shutdown_rpc" -v`
Expected: FAIL — `_dispatch` has no `async_job`/`detach`; no `job` subcommand; `daemon stop` calls `stop` not `shutdown`; `_act_params` doesn't autogenerate keys.

- [ ] **Step 3: Implement the CLI changes**

3a. Auto-generate keys in `_act_params` (replace the function):

```python
def _act_params(args, verb, target):
    import uuid
    p = {"verb": verb, "target": target,
         "yes": getattr(args, "yes", False),
         "request_id": getattr(args, "request_id", None) or uuid.uuid4().hex,
         "idempotency_key": getattr(args, "idempotency_key", None) or uuid.uuid4().hex}
    if isinstance(target, dict):
        p.update({k: v for k, v in target.items() if k not in p})
    return p
```

3b. Extend `_dispatch` to route async jobs (replace the function):

```python
def _dispatch(method, params, in_process_fn, *, cfg=None, async_job=False, detach=False):
    client = _daemon_client(cfg)
    if client is None:
        return in_process_fn()
    if not async_job:
        return client.call(method, params)
    if detach:
        return client.call(method, params)          # returns {job_id, status: accepted}
    cfg = cfg or config.load()
    return client.submit_and_wait(
        method, params,
        overall_timeout=cfg.get("act_timeout", 60.0),
        poll_interval=cfg.get("poll_interval", 0.5),
    )
```

3c. Route the act path through it. In `_do_action`, change the dispatch call:

```python
    detach = getattr(args, "detach", False)
    env = _dispatch("act", _act_params(args, verb, target), in_process,
                    cfg=cfg, async_job=True, detach=detach)
    if detach and env.get("ok") and "job_id" in env.get("data", {}):
        print(f"phonectl: job {env['data']['job_id']} (use: phonectl job {env['data']['job_id']})")
        return 0
```

(Insert the `detach`/job-id print just before the existing `if env["ok"]:` block.)

3d. Route the observe path. In the observe dispatch (`cli.py:131`), set `async_job=True`:

```python
    env = _dispatch("observe", {
        "screenshot": args.screenshot, "snap_path": args.screenshot_path,
        "tree": args.tree, "relations": args.relations,
    }, in_process, cfg=cfg, async_job=True)
```

3e. Add `--detach` to the shared action flags. In `_action_flags(parser)` add:

```python
    parser.add_argument("--detach", action="store_true",
                        help="submit the action and print its job id instead of waiting")
```

3f. Point `daemon stop` at `shutdown`. In `_cmd_daemon`, the `sub == "stop"` branch — replace `client.call("stop", {})` with `client.call("shutdown", {})`:

```python
    if sub == "stop":
        client = _daemon_client(cfg)
        if client is None:
            _daemon_discovery.remove()
            print("phonectl: no running daemon")
            return 0
        client.call("shutdown", {})
        print("phonectl: shutdown signalled")
        return 0
```

3g. Add the `job` command handler near `_cmd_daemon`:

```python
def _cmd_job(args):
    cfg = config.load()
    client = _daemon_client(cfg)
    if client is None:
        print("phonectl: no running daemon")
        return 1
    import time as _t
    deadline = _t.monotonic() + (cfg.get("act_timeout", 60.0) if getattr(args, "wait", False) else 0.0)
    while True:
        env = client.call("job_poll", {"job_id": args.job_id})
        if not env.get("ok"):
            print(json.dumps(env, indent=2) if getattr(args, "json", False) else f"phonectl: {env['error']['message']}")
            return 1
        status = env["data"]["status"]
        if status in ("done", "error") or not getattr(args, "wait", False) or _t.monotonic() >= deadline:
            break
        _t.sleep(cfg.get("poll_interval", 0.5))
    if getattr(args, "json", False):
        print(json.dumps(env, indent=2))
    else:
        print(f"phonectl: job {args.job_id} status={env['data']['status']}")
    return 0
```

3h. Register the `job` parser in `build_parser` (near the `daemon` parser):

```python
    jb = sub.add_parser("job")
    jb.add_argument("job_id")
    jb.add_argument("--wait", action="store_true", help="block until the job is terminal")
    jb.add_argument("--json", action="store_true")
    jb.set_defaults(func=_cmd_job)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_cli.py -v`
Expected: PASS (new tests + existing CLI tests still green)

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/cli.py tests/test_cli.py
git commit -m "feat(cli): async act/observe routing, --detach, job command, daemon stop->shutdown"
```

---

## Task 9: Full-suite regression + docs

**Files:**
- Modify: `README.md` (the daemon section — note block-and-poll, `--detach`, `phonectl job`, `daemon stop` now terminates)
- Test: entire suite

- [ ] **Step 1: Run the whole suite**

Run: `python -m pytest -q`
Expected: all pass (≥ 434 + new tests, 1 skipped). Fix any stragglers — most likely remaining `handle_line("act"/"observe")` call sites in tests not yet converted in Task 5; convert them to `_submit_run_poll`.

- [ ] **Step 2: Update README daemon section**

In `README.md`, in the daemon/events section, document: (a) `act`/`observe`/`find` are async jobs the CLI block-and-polls by default (cap = `act_timeout`); (b) `--detach` returns a job id; (c) `phonectl job <id> [--wait]`; (d) `phonectl daemon stop` now terminates the daemon (the kill-switch is still `stop`/`resume` via policy). Add the new config keys to any config table.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: README daemon async-job model (block-and-poll, --detach, job, shutdown)"
```

---

## Task 10: Live device re-validation (manual)

**Files:** none (validation only). Requires a connected device (`phonectl doctor` shows `connected`).

- [ ] **Step 1:** Start the daemon in the background: `phonectl daemon start &` (or via the harness background runner). Confirm `phonectl daemon status` → `running=True`.
- [ ] **Step 2:** Run `phonectl tap --xy 720 1500` and confirm it **completes and prints the result** (no `daemon_unreachable`), taking up to ~20s but well under `act_timeout`.
- [ ] **Step 3:** Confirm `runs.jsonl` gained **exactly one** record for that action. Run the same `tap` again and confirm one more record (no duplicate from internal retries).
- [ ] **Step 4:** Run `phonectl tap --xy 720 1500 --detach`, note the job id, then `phonectl job <id> --wait` and confirm it returns `status=done`.
- [ ] **Step 5:** Run `phonectl daemon stop` and confirm the process actually exits (`ps` shows it gone) and `phonectl daemon status` → `running=False`, discovery removed.
- [ ] **Step 6:** Record results in the session handoff / `.remember`.

---

## Self-Review (completed by plan author)

- **Spec coverage:** §3 decisions 1–6 → Tasks 1 (config), 2 (JobTimeoutError), 3–4 (queue/worker/dedupe/single-writer), 5 (async act/observe/find + job_poll), 6 (shutdown), 7 (client timeout-vs-unreachable + submit_and_wait), 8 (CLI block-and-poll, --detach, job, stop→shutdown). §4 components all mapped. §7 testing → per-task tests + Task 9. §1 bug + Task 10 live re-validation. No gaps.
- **Placeholder scan:** All steps contain runnable code. No TBD/TODO, no illustrative artifacts.
- **Type consistency:** `submit(method, params)->job_id`, `get(job_id)->Job|None`, `run_next(block,timeout)->bool`, `_run_job(method,params)->env`, `submit_and_wait(...,overall_timeout,poll_interval)->env`, `job_poll` data `{status,result}`, accepted data `{job_id,status}` — names/shapes consistent across Tasks 3–8.
