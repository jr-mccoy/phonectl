# phonectl Event Bus + Snapshot Cache Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Plan 5.2 of the platform roadmap** (`docs/superpowers/phonectl-platform-roadmap.md`). Second plan of
Phase 5 (daemon & event runtime). Depends on **Plan 5.1**
(`docs/superpowers/plans/phonectl-plan-5.1-daemon-process-and-rpc-api.md` — the daemon process, the
`daemon/server.py` RPC dispatcher with its synchronously-testable `handle_line(line) -> line`, the warm
`(ProviderRegistry, Session, Connection)`, the global single-writer lock, and the durable `runs.jsonl`
records), **Plan 4.1** (`AccessibilityProvider.poll_events` cursor contract), **Plan 4.2**
(`NotificationsProvider.list()`), **Plan 1.1** (`errors`/`results`/`capabilities`), and **Plan 1.2**
(stale-snapshot protection). Reads with the Phase-5.0 spec
(`docs/superpowers/specs/2026-06-22-phonectl-daemon-event-runtime-design.md`).

**Goal:** Turn the Plan-5.1 daemon into a real **single writer *and* event broker** (strategy §22). This
plan adds two pillars and wires them into the existing RPC dispatcher: (1) the **snapshot cache** —
monotonic snapshot IDs (`snap_1`, `snap_2`, …), invalidation on every act (the re-observe invariant made
explicit), a foreground-package accessor, and **stale-index protection** that rejects an index-based act
whose expected `snapshot_id` no longer matches the current cache id or whose foreground app changed
(strategy §21, ties to Plan 1.2's `StaleSnapshotError`); and (2) the **event bus** — a monotonic-sequence
pub/sub broker (`{"seq","type","ts","source","data"}`) with `events_poll(since, max)` reusing the Plan 4.1
`poll_events` cursor contract, fed by a step-wise **provider poller** that drains
`AccessibilityProvider.poll_events` (→ `ui_changed`) and `NotificationsProvider` (→ `notification_posted`)
into the bus, plus internal `action_started`/`action_finished`/`lifecycle` events the daemon publishes
itself. CLI/MCP/macros subscribe via the daemon's `events_poll` RPC. **No daemon is required for the v1
primitives** — every existing test stays green and the cache/bus modules are pure and unit-testable
without threads, sockets, or a device.

**Architecture:** Two new pure-ish modules under the Plan-5.1 daemon package:
`src/phonectl/daemon/snapshots.py` (`SnapshotCache`) and `src/phonectl/daemon/events.py` (`EventBus`),
each taking an **injectable id-counter / clock** so tests are deterministic. A third new module
`src/phonectl/daemon/poller.py` (`EventPoller`) is a plain object with a `drain_once(sources)` /
`tick()` method that pulls from injected provider sources and publishes onto the bus, advancing each
source's cursor — **driven manually in tests; no real thread or sleep**. `daemon/server.py` (created in
5.1) is extended: it holds one `SnapshotCache` and one `EventBus` alongside its warm registry/session;
its `observe` RPC mints+returns a `snapshot_id`; its `act` path captures `snapshot_before`, runs the
mutating action through `runtime.run_action` under the single-writer lock, mints `snapshot_after`,
populates both on the envelope **and** backfills the `runs.jsonl` fields 5.1 left as `None`, and publishes
`action_started`/`action_finished`. A new `events_poll` RPC returns `{"events", "cursor"}`.

**Tech Stack:** Python 3 (stdlib only: `json`, `threading`, `time`, `itertools`, `typing`); `pytest` for
tests; no new runtime deps. The daemon transport/discovery/loopback-only guarantees are all owned by Plan
5.1; this plan adds no new sockets.

## Global Constraints

- **stdlib-only at runtime.** `SnapshotCache`, `EventBus`, and `EventPoller` use only `json`,
  `itertools`, `threading`, `time`, and `typing`. No third-party deps.
- **Backend isolation.** Event sources are reached **only through providers** held by the warm
  `ProviderRegistry` (`AccessibilityProvider.poll_events`, `NotificationsProvider.list`); the bus/cache
  never call `adb`/`subprocess` and never import `adb_backend`. `ui_parser.py` stays pure and untouched.
- **Every `act()` re-observes — snapshot invalidation is the mechanism.** An act invalidates the prior
  cached snapshot and mints a fresh id from the post-action snapshot; `snapshot_before`/`snapshot_after`
  on the act envelope make the re-observe invariant observable.
- **Stale-index protection runs BEFORE the index act resolves.** When an act carries an expected
  `snapshot_id`, `SnapshotCache.validate(expected, current_foreground)` is consulted **before** the action
  is dispatched; a mismatch (id changed, or foreground package changed) raises
  `errors.StaleSnapshotError` → `results.err` with a re-observe `user_action`. (Plan 1.2 / strategy §21.)
- **Single writer.** Mutating acts continue to route through `runtime.run_action` under the Plan-5.1
  global single-writer lock; the cache/bus add no second writer. `observe` and `events_poll` are reads.
- **Structured-result invariant (Plan 1.1).** Every RPC returns a `results.ok/err` envelope.
- **Injectable seams.** `SnapshotCache(*, id_counter=..., now=...)`, `EventBus(*, now=...)`,
  `EventPoller(bus, *, sources=...)`; the poller is driven step-wise (`drain_once`) — **no real threads,
  sleeps, or sockets in tests**. Provider sources are fakes feeding canned events. Isolate state via
  `monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))`.
- **No daemon required for v1.** The modules are independently importable/testable; existing CLI/MCP tests
  remain green (the cache/bus only activate inside `daemon/server.py`).
- **One commit per task. TDD order is non-negotiable.**

## Shared conventions established by this plan

- **Snapshot IDs** are monotonic strings `f"snap_{n}"` minted by an injectable counter
  (`itertools.count(1)` by default). `SnapshotCache.current_id` is the most-recently `put` id, or `None`
  before the first observe.
- **Foreground package** for stale-checks is read from a cached snapshot's `["app"]["package"]` (the
  `observer.observe`/`session.last` shape). A `None`/missing package compares unequal only to a present
  one — a snapshot that never carried an app is treated as "foreground unknown" and does **not** spuriously
  invalidate (the id check is the hard guarantee).
- **Event envelope:** `{"seq": int, "type": str, "ts": float, "source": str, "data": dict}`. `seq` is a
  monotonic int starting at 1. **Event types:** `ui_changed`, `notification_posted`, `action_started`,
  `action_finished`, `lifecycle`.
- **`poll(since, max)` cursor contract** (identical to Plan 4.1 `poll_events`): returns
  `{"events": [...], "cursor": int}` containing events with `seq > since`, newest-bounded by `max`; the
  returned `cursor` is the largest `seq` emitted (or `since` when none) and is passed back as the next
  `since`. No busy-wait on the wire; subscribers long-poll by re-calling with the last cursor.
- **Event sources** for the poller are objects exposing the provider methods the poller knows how to
  drain: a UI source with `poll_events(since, *, max_events)` (→ `ui_changed`, `source="accessibility"`)
  and a notifications source with `list()` (→ `notification_posted`, `source="notifications"`, diffed by
  notification `key`). Sources are injected so tests feed canned data.
- **RPC additions to `daemon/server.py`:** `observe` returns `snapshot_id`; `act` returns
  `snapshot_before` and `snapshot_after` (and backfills the same two `runs.jsonl` fields 5.1 left `None`);
  new `events_poll(since, max)` RPC returns `{"events", "cursor"}`. `events_subscribe` is **documented**
  as the cursor-based long-poll contract over `events_poll`; real server-push streaming is deferred (noted).

> **Assumed Plan-5.1 symbols** (authored in parallel; reference, do not redefine): the module
> `phonectl.daemon.server` exposing a `Server` (or equivalent dispatcher object) with a
> `handle_line(line: str) -> str` method, a warm `self.registry` (`ProviderRegistry`), `self.session`
> (`Session`), `self.conn` (`Connection`), a `runs.jsonl` append on each act with `snapshot_before` /
> `snapshot_after` initialized to `None`, and act routing through `runtime.run_action`. If 5.1 named the
> dispatcher or its attributes differently, **reuse 5.1's names** — do not introduce parallel ones; the
> tasks below touch only the named seams.

---

### Task 1: `SnapshotCache` — monotonic ids, put/get/current, foreground accessor (pure)

**Files:**
- Create: `src/phonectl/daemon/__init__.py` (only if Plan 5.1 has not already created the package)
- Create: `src/phonectl/daemon/snapshots.py`
- Test: `tests/test_snapshot_cache.py` (create)

**Interfaces:**
- `SnapshotCache(*, id_counter=None, now=time.time)` — `id_counter` is an iterator of ints
  (default `itertools.count(1)`); `now` is injectable for any timestamp needs.
- `put(snapshot: dict) -> str` — caches `snapshot` under a new id `f"snap_{next(id_counter)}"`, sets it as
  current, returns the id.
- `get(snapshot_id: str) -> dict | None` — the cached snapshot or `None`.
- `current_id` (property) — the most-recent id, or `None` before any `put`.
- `current_foreground` (property) — `current snapshot["app"]["package"]` or `None`.
- `foreground_of(snapshot_id) -> str | None` — the foreground package for a given cached id.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_snapshot_cache.py
import itertools

import pytest

from phonectl.daemon.snapshots import SnapshotCache


def _snap(pkg="com.android.settings", h="abc"):
    return {"app": {"package": pkg, "activity": ".Main"}, "hash": h, "elements": []}


def test_put_mints_monotonic_ids():
    cache = SnapshotCache()
    a = cache.put(_snap())
    b = cache.put(_snap())
    assert a == "snap_1"
    assert b == "snap_2"
    assert cache.current_id == "snap_2"


def test_get_returns_cached_snapshot_and_none_for_unknown():
    cache = SnapshotCache()
    sid = cache.put(_snap(h="xyz"))
    assert cache.get(sid)["hash"] == "xyz"
    assert cache.get("snap_999") is None


def test_current_id_is_none_before_first_put():
    cache = SnapshotCache()
    assert cache.current_id is None
    assert cache.current_foreground is None


def test_foreground_accessors_read_app_package():
    cache = SnapshotCache()
    sid = cache.put(_snap(pkg="com.bank.app"))
    assert cache.current_foreground == "com.bank.app"
    assert cache.foreground_of(sid) == "com.bank.app"
    assert cache.foreground_of("snap_999") is None


def test_injectable_id_counter_is_used():
    cache = SnapshotCache(id_counter=itertools.count(100))
    assert cache.put(_snap()) == "snap_100"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_snapshot_cache.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'phonectl.daemon.snapshots'`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/daemon/__init__.py  (create only if Plan 5.1 has not)
"""phonectl daemon package — single-writer event runtime."""
```

```python
# src/phonectl/daemon/snapshots.py
"""Snapshot cache — monotonic ids, invalidation, foreground checks (no I/O)."""
from __future__ import annotations

import itertools
import time


class SnapshotCache:
    def __init__(self, *, id_counter=None, now=time.time) -> None:
        self._counter = id_counter if id_counter is not None else itertools.count(1)
        self._now = now
        self._store: dict[str, dict] = {}
        self._current_id: str | None = None

    def put(self, snapshot: dict) -> str:
        snapshot_id = f"snap_{next(self._counter)}"
        self._store[snapshot_id] = snapshot
        self._current_id = snapshot_id
        return snapshot_id

    def get(self, snapshot_id: str) -> dict | None:
        return self._store.get(snapshot_id)

    @property
    def current_id(self) -> str | None:
        return self._current_id

    def foreground_of(self, snapshot_id: str) -> str | None:
        snap = self._store.get(snapshot_id)
        if not snap:
            return None
        return (snap.get("app") or {}).get("package")

    @property
    def current_foreground(self) -> str | None:
        if self._current_id is None:
            return None
        return self.foreground_of(self._current_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_snapshot_cache.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/daemon/__init__.py src/phonectl/daemon/snapshots.py tests/test_snapshot_cache.py
git commit -m "feat: SnapshotCache — monotonic snapshot ids + foreground accessor (pure)"
```

---

### Task 2: `observe` RPC mints + returns a `snapshot_id`

**Files:**
- Modify: `src/phonectl/daemon/server.py` (the Plan-5.1 dispatcher: hold a `SnapshotCache`, extend the
  `observe` handler)
- Test: `tests/test_daemon_server.py` (append; created by Plan 5.1)

**Interfaces:**
- `Server.__init__` constructs `self.snapshots = SnapshotCache()` alongside the warm registry/session.
- The `observe` RPC handler: after `observer.observe(...)` populates `session.last`, call
  `self.snapshots.put(session.last)` and add `snapshot_id` to the `results.ok(...)` envelope's top level
  (alongside the snapshot `data`). A second `observe` returns the next id.

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_daemon_server.py
import json

from phonectl.daemon.server import Server  # 5.1 dispatcher


def _observe_line():
    return json.dumps({"method": "observe", "params": {}, "request_id": "r1"})


def test_observe_returns_snapshot_id(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    srv = make_test_server(tmp_path)   # 5.1 test helper: warm Server over FakeBackend registry
    out = json.loads(srv.handle_line(_observe_line()))
    assert out["ok"] is True
    assert out["snapshot_id"] == "snap_1"


def test_second_observe_increments_snapshot_id(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    srv = make_test_server(tmp_path)
    first = json.loads(srv.handle_line(_observe_line()))
    second = json.loads(srv.handle_line(_observe_line()))
    assert first["snapshot_id"] == "snap_1"
    assert second["snapshot_id"] == "snap_2"
    assert srv.snapshots.current_id == "snap_2"
```

> Reuse the Plan-5.1 `make_test_server(tmp_path)` helper (warm `Server` over a `FakeBackend`-backed
> `ProviderRegistry`). If 5.1 named it differently, call that name — do not build a second harness.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_daemon_server.py -v -k "snapshot_id"`
Expected: FAIL (`KeyError: 'snapshot_id'` — the 5.1 `observe` envelope has no `snapshot_id` yet).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/daemon/server.py — in Server.__init__ (alongside the warm registry/session)
from phonectl.daemon.snapshots import SnapshotCache

#   self.registry, self.session, self.conn already built by Plan 5.1
self.snapshots = SnapshotCache()
```

```python
# src/phonectl/daemon/server.py — in the observe RPC handler, after observer.observe(...)
def _handle_observe(self, params, request_id):
    self.conn.ensure()
    snap = observer.observe(
        self.registry, self.session,
        tree=bool(params.get("tree")), relations=bool(params.get("relations")),
    )
    snapshot_id = self.snapshots.put(snap)
    return results.ok(
        capability="ui.observe",
        provider=getattr(self.registry, "last_used", None) or "adb",
        data=snap,
        snapshot_id=snapshot_id,
        request_id=request_id,
    )
```

(Keep the rest of the 5.1 `observe` handler intact; only the `snapshots.put` + `snapshot_id=` addition is
new. If 5.1 already returns the snapshot via `results.ok(..., data=snap)`, just thread `snapshot_id` in.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_daemon_server.py -v -k "snapshot_id"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/daemon/server.py tests/test_daemon_server.py
git commit -m "feat: daemon observe RPC mints+returns a monotonic snapshot_id"
```

---

### Task 3: Stale-index protection — `SnapshotCache.validate` + act rejects a stale `snapshot_id`

**Files:**
- Modify: `src/phonectl/daemon/snapshots.py` (`validate`)
- Modify: `src/phonectl/daemon/server.py` (the `act` handler consults `validate` BEFORE dispatch)
- Test: `tests/test_snapshot_cache.py` (append), `tests/test_daemon_server.py` (append)

**Interfaces:**
- `SnapshotCache.validate(expected_id: str | None, *, current_foreground: str | None) -> None` — raises
  `errors.StaleSnapshotError` when (a) `expected_id` is not `None` and `expected_id != self.current_id`, or
  (b) `expected_id` is the current id but `current_foreground` differs from `foreground_of(expected_id)`
  (and both are non-`None`). When `expected_id is None`, validation is a no-op (callers that don't pin a
  snapshot opt out of the check). Pure — no I/O.
- `act` RPC handler: when `params` carries `snapshot_id`, call
  `self.snapshots.validate(params["snapshot_id"], current_foreground=self.snapshots.current_foreground)`
  **before** dispatching the action; on `StaleSnapshotError`, return
  `results.err(exc, user_action="Re-observe (the screen changed); resolve the index against a fresh snapshot.")`.

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_snapshot_cache.py
from phonectl import errors


def test_validate_passes_when_expected_matches_current():
    cache = SnapshotCache()
    sid = cache.put(_snap(pkg="com.x"))
    cache.validate(sid, current_foreground="com.x")  # no raise


def test_validate_raises_when_expected_is_stale():
    cache = SnapshotCache()
    cache.put(_snap())            # snap_1
    cache.put(_snap())            # snap_2 (current)
    with pytest.raises(errors.StaleSnapshotError):
        cache.validate("snap_1", current_foreground="com.android.settings")


def test_validate_raises_when_foreground_changed():
    cache = SnapshotCache()
    sid = cache.put(_snap(pkg="com.x"))
    with pytest.raises(errors.StaleSnapshotError):
        cache.validate(sid, current_foreground="com.bank.app")


def test_validate_none_expected_is_noop():
    cache = SnapshotCache()
    cache.put(_snap())
    cache.validate(None, current_foreground="anything")  # no raise
```

```python
# Append to tests/test_daemon_server.py
def test_act_rejects_stale_snapshot_id_before_running(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    srv = make_test_server(tmp_path)
    json.loads(srv.handle_line(_observe_line()))   # snap_1 (current)
    # Pin a stale id that is no longer current.
    line = json.dumps({"method": "act",
                       "params": {"verb": "tap", "target": "i=0", "snapshot_id": "snap_0", "yes": True},
                       "request_id": "r2"})
    out = json.loads(srv.handle_line(line))
    assert out["ok"] is False
    assert out["error"]["code"] == "stale_snapshot"
    assert "re-observe" in out["error"]["user_action"].lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_snapshot_cache.py tests/test_daemon_server.py -v -k "validate or stale"`
Expected: FAIL (`AttributeError: ... 'validate'`; act envelope returns `ok=True` instead of stale error).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/daemon/snapshots.py — add to SnapshotCache
from phonectl import errors


def validate(self, expected_id, *, current_foreground) -> None:
    if expected_id is None:
        return
    if expected_id != self._current_id:
        raise errors.StaleSnapshotError(
            f"snapshot {expected_id} is stale (current is {self._current_id})"
        )
    pinned_fg = self.foreground_of(expected_id)
    if pinned_fg is not None and current_foreground is not None \
            and pinned_fg != current_foreground:
        raise errors.StaleSnapshotError(
            f"foreground changed since {expected_id}: {pinned_fg} -> {current_foreground}"
        )
```

```python
# src/phonectl/daemon/server.py — in the act RPC handler, BEFORE dispatch
def _handle_act(self, params, request_id):
    try:
        self.snapshots.validate(
            params.get("snapshot_id"),
            current_foreground=self.snapshots.current_foreground,
        )
    except errors.StaleSnapshotError as exc:
        return results.err(
            exc,
            user_action="Re-observe (the screen changed); resolve the index against a fresh snapshot.",
            request_id=request_id,
        )
    # ... existing Plan-5.1 dispatch through runtime.run_action follows ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_snapshot_cache.py tests/test_daemon_server.py -v -k "validate or stale"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/daemon/snapshots.py src/phonectl/daemon/server.py \
        tests/test_snapshot_cache.py tests/test_daemon_server.py
git commit -m "feat: stale-index protection — SnapshotCache.validate gates index acts before dispatch"
```

---

### Task 4: Snapshot invalidation + `snapshot_before`/`snapshot_after` on act (+ runs.jsonl backfill)

**Files:**
- Modify: `src/phonectl/daemon/server.py` (the `act` handler captures before-id, mints after-id, populates
  both on the envelope and the `runs.jsonl` record)
- Test: `tests/test_daemon_server.py` (append)

**Interfaces:**
- `act` handler flow (after stale-validation passes): record `snapshot_before = self.snapshots.current_id`;
  run the action via `runtime.run_action` (which re-observes into `session.last`); on success mint
  `snapshot_after = self.snapshots.put(session.last)`; add `snapshot_before` and `snapshot_after` to the
  envelope **top level**; and backfill the same two fields in the durable `runs.jsonl` record (Plan 5.1
  writes the record with both as `None` — populate them here). The fresh `snapshot_after` id supersedes the
  prior current id, which is the invalidation: any act pinning the old id now fails `validate` (Task 3).

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_daemon_server.py
def test_act_returns_before_and_after_snapshot_ids(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    srv = make_test_server(tmp_path)
    json.loads(srv.handle_line(_observe_line()))   # snap_1 current
    line = json.dumps({"method": "act",
                       "params": {"verb": "tap", "target": "i=0", "yes": True},
                       "request_id": "r3"})
    out = json.loads(srv.handle_line(line))
    assert out["ok"] is True
    assert out["snapshot_before"] == "snap_1"
    assert out["snapshot_after"] == "snap_2"
    assert out["snapshot_before"] != out["snapshot_after"]
    assert srv.snapshots.current_id == "snap_2"


def test_act_backfills_runs_jsonl_snapshot_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    srv = make_test_server(tmp_path)
    json.loads(srv.handle_line(_observe_line()))
    srv.handle_line(json.dumps({"method": "act",
                    "params": {"verb": "tap", "target": "i=0", "yes": True},
                    "request_id": "r4"}))
    runs = (tmp_path / "runs.jsonl").read_text().strip().splitlines()
    rec = json.loads(runs[-1])
    assert rec["snapshot_before"] == "snap_1"
    assert rec["snapshot_after"] == "snap_2"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_daemon_server.py -v -k "before_and_after or backfills"`
Expected: FAIL (`KeyError: 'snapshot_before'`; runs record still has `None` snapshot fields).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/daemon/server.py — in the act handler, after stale-validation
snapshot_before = self.snapshots.current_id

env = runtime.run_action(
    verb, fn, target, build=self._build, yes=yes, cfg=self.cfg,
    request_id=request_id,
)

snapshot_after = None
if env.get("ok") and self.session.last is not None:
    snapshot_after = self.snapshots.put(self.session.last)
    env["snapshot_before"] = snapshot_before
    env["snapshot_after"] = snapshot_after

# Backfill the durable run record (Plan 5.1 wrote both as None).
self._update_run_record(request_id,
                        snapshot_before=snapshot_before,
                        snapshot_after=snapshot_after)
return env
```

> `_update_run_record` is the Plan-5.1 hook that rewrites/patches the just-appended `runs.jsonl` line for
> this `request_id`. If 5.1 appends the record only at completion, populate `snapshot_before`/
> `snapshot_after` at append time instead — same fields, one write. Reuse 5.1's run-record writer; do not
> add a second `runs.jsonl` path.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_daemon_server.py -v -k "before_and_after or backfills"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/daemon/server.py tests/test_daemon_server.py
git commit -m "feat: act invalidates+mints snapshots; snapshot_before/after on envelope and runs.jsonl"
```

---

### Task 5: `EventBus` — monotonic seq, publish, cursor poll (pure)

**Files:**
- Create: `src/phonectl/daemon/events.py`
- Test: `tests/test_event_bus.py` (create)

**Interfaces:**
- `EventBus(*, now=time.time)` — `now` injectable for deterministic `ts`.
- `EVENT_TYPES` — frozenset `{"ui_changed", "notification_posted", "action_started",
  "action_finished", "lifecycle"}`.
- `publish(type: str, data: dict, *, source: str) -> dict` — assigns the next `seq` (monotonic int from 1)
  and `ts = now()`, appends `{"seq","type","ts","source","data"}`, returns the event. An unknown `type`
  raises `ValueError` before mutating the log.
- `poll(since: int = 0, *, max: int = 100) -> dict` — returns `{"events": [...], "cursor": int}` with
  events whose `seq > since` (up to `max`); `cursor` is the largest emitted `seq`, or `since` when none.
- `latest_seq` (property) — the highest assigned seq, or 0.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_event_bus.py
import itertools

import pytest

from phonectl.daemon.events import EventBus


def _clock():
    c = itertools.count(1000)
    return lambda: float(next(c))


def test_publish_assigns_monotonic_seq_and_envelope():
    bus = EventBus(now=_clock())
    e1 = bus.publish("ui_changed", {"package": "com.x"}, source="accessibility")
    e2 = bus.publish("lifecycle", {"state": "started"}, source="daemon")
    assert e1["seq"] == 1 and e2["seq"] == 2
    assert e1["type"] == "ui_changed" and e1["source"] == "accessibility"
    assert e1["ts"] == 1000.0
    assert set(e1) == {"seq", "type", "ts", "source", "data"}


def test_publish_rejects_unknown_type():
    bus = EventBus()
    with pytest.raises(ValueError):
        bus.publish("teleported", {}, source="x")
    assert bus.latest_seq == 0  # log untouched


def test_poll_filters_by_since_and_advances_cursor():
    bus = EventBus(now=_clock())
    for _ in range(3):
        bus.publish("ui_changed", {}, source="accessibility")
    out = bus.poll(since=1)
    assert [e["seq"] for e in out["events"]] == [2, 3]
    assert out["cursor"] == 3


def test_poll_respects_max_and_empty_cursor():
    bus = EventBus(now=_clock())
    for _ in range(5):
        bus.publish("ui_changed", {}, source="accessibility")
    out = bus.poll(since=0, max=2)
    assert [e["seq"] for e in out["events"]] == [1, 2]
    assert out["cursor"] == 2
    tail = bus.poll(since=5)
    assert tail["events"] == [] and tail["cursor"] == 5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_event_bus.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'phonectl.daemon.events'`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/daemon/events.py
"""Event bus — monotonic-seq pub/sub with cursor-based poll (no I/O, no threads here)."""
from __future__ import annotations

import itertools
import time

EVENT_TYPES = frozenset({
    "ui_changed", "notification_posted",
    "action_started", "action_finished", "lifecycle",
})


class EventBus:
    def __init__(self, *, now=time.time) -> None:
        self._now = now
        self._seq = itertools.count(1)
        self._events: list[dict] = []

    @property
    def latest_seq(self) -> int:
        return self._events[-1]["seq"] if self._events else 0

    def publish(self, type: str, data: dict, *, source: str) -> dict:
        if type not in EVENT_TYPES:
            raise ValueError(f"unknown event type {type!r}")
        event = {"seq": next(self._seq), "type": type, "ts": self._now(),
                 "source": source, "data": dict(data or {})}
        self._events.append(event)
        return event

    def poll(self, since: int = 0, *, max: int = 100) -> dict:
        newer = [e for e in self._events if e["seq"] > since][:max]
        cursor = newer[-1]["seq"] if newer else since
        return {"events": newer, "cursor": cursor}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_event_bus.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/daemon/events.py tests/test_event_bus.py
git commit -m "feat: EventBus — monotonic-seq publish + cursor poll (Plan 4.1 poll contract)"
```

---

### Task 6: Internal event hooks — daemon publishes action_started/finished + lifecycle

**Files:**
- Modify: `src/phonectl/daemon/server.py` (hold an `EventBus`; publish around act; lifecycle on start/stop)
- Test: `tests/test_daemon_server.py` (append)

**Interfaces:**
- `Server.__init__` constructs `self.events = EventBus()`.
- Around the `act` dispatch: `self.events.publish("action_started", {"verb", "target", "request_id"},
  source="daemon")` before `runtime.run_action`, and `self.events.publish("action_finished",
  {"verb", "target", "request_id", "ok", "snapshot_after"}, source="daemon")` after (in a `finally`, so a
  raised/blocked action still emits a finish). Stale-rejected acts (Task 3) emit start+finish too so
  subscribers see the rejection.
- Lifecycle: `Server.start()` / `Server.stop()` (the Plan-5.1 lifecycle hooks) publish
  `lifecycle` events (`{"state": "started"}` / `{"state": "stopped"}`, `source="daemon"`). If 5.1 exposes
  different lifecycle method names, hook those.

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_daemon_server.py
def test_act_emits_started_and_finished_events(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    srv = make_test_server(tmp_path)
    json.loads(srv.handle_line(_observe_line()))
    srv.handle_line(json.dumps({"method": "act",
                    "params": {"verb": "tap", "target": "i=0", "yes": True},
                    "request_id": "r5"}))
    out = srv.events.poll(since=0)
    types = [e["type"] for e in out["events"]]
    assert "action_started" in types
    assert "action_finished" in types
    started = next(e for e in out["events"] if e["type"] == "action_started")
    assert started["data"]["request_id"] == "r5"
    assert started["source"] == "daemon"
    finished = next(e for e in out["events"] if e["type"] == "action_finished")
    assert finished["data"]["ok"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_daemon_server.py -v -k "started_and_finished"`
Expected: FAIL (`AttributeError: 'Server' object has no attribute 'events'`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/daemon/server.py — Server.__init__
from phonectl.daemon.events import EventBus
self.events = EventBus()
```

```python
# src/phonectl/daemon/server.py — wrap the act dispatch
def _handle_act(self, params, request_id):
    verb, target = params["verb"], params["target"]
    try:
        self.snapshots.validate(params.get("snapshot_id"),
                                current_foreground=self.snapshots.current_foreground)
    except errors.StaleSnapshotError as exc:
        env = results.err(exc, user_action="Re-observe ...", request_id=request_id)
    else:
        self.events.publish("action_started",
                            {"verb": verb, "target": target, "request_id": request_id},
                            source="daemon")
        env = self._dispatch_act(params, request_id)  # Task 4 body
    self.events.publish("action_finished",
                        {"verb": verb, "target": target, "request_id": request_id,
                         "ok": bool(env.get("ok")), "snapshot_after": env.get("snapshot_after")},
                        source="daemon")
    return env
```

```python
# src/phonectl/daemon/server.py — lifecycle hooks (extend Plan 5.1's start/stop)
def start(self):
    super_start = getattr(self, "_start_transport", None)
    if super_start:
        super_start()
    self.events.publish("lifecycle", {"state": "started"}, source="daemon")

def stop(self):
    self.events.publish("lifecycle", {"state": "stopped"}, source="daemon")
    # ... Plan 5.1 transport teardown ...
```

(Adapt to the actual 5.1 start/stop method names; the only new behavior is the two `lifecycle` publishes.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_daemon_server.py -v -k "started_and_finished"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/daemon/server.py tests/test_daemon_server.py
git commit -m "feat: daemon publishes action_started/finished + lifecycle events onto the bus"
```

---

### Task 7: `EventPoller` — step-wise provider drainer into the bus

**Files:**
- Create: `src/phonectl/daemon/poller.py`
- Test: `tests/test_event_poller.py` (create)

**Interfaces:**
- `EventPoller(bus, *, ui_source=None, notif_source=None)` — `ui_source` exposes
  `poll_events(since, *, max_events)` (Plan 4.1); `notif_source` exposes `list()` (Plan 4.2). Either may be
  `None` (that stream is skipped).
- `drain_once(*, max_events=50) -> int` — one synchronous tick: drains the UI source from its stored cursor
  (publishing each event as `ui_changed`, `source="accessibility"`, advancing the cursor to the returned
  one), then diffs the notifications `list()` against the keys seen last tick (publishing each **new**
  notification as `notification_posted`, `source="notifications"`). Returns the count published this tick.
  **No threads, no sleeps** — the daemon's (later) background loop calls `drain_once` on an interval; tests
  call it directly. `tick()` is an alias for `drain_once()`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_event_poller.py
from phonectl.daemon.events import EventBus
from phonectl.daemon.poller import EventPoller


class FakeUiSource:
    """Plan-4.1 poll_events shape: returns canned events + a monotonic cursor."""
    def __init__(self, batches):
        self._batches = list(batches)   # list of {"events":[...], "cursor":int}
    def poll_events(self, since=0, *, max_events=50):
        return self._batches.pop(0) if self._batches else {"events": [], "cursor": since}


class FakeNotifSource:
    def __init__(self, lists):
        self._lists = list(lists)       # list of list()-results per tick
    def list(self):
        return self._lists.pop(0) if self._lists else []


def test_drain_publishes_ui_events_as_ui_changed():
    bus = EventBus()
    ui = FakeUiSource([{"events": [{"seq": 1, "type": "window_state_changed", "package": "com.x"}],
                        "cursor": 1}])
    n = EventPoller(bus, ui_source=ui).drain_once()
    assert n == 1
    out = bus.poll(since=0)["events"]
    assert out[0]["type"] == "ui_changed"
    assert out[0]["source"] == "accessibility"
    assert out[0]["data"]["package"] == "com.x"


def test_drain_advances_ui_cursor_between_ticks():
    bus = EventBus()
    ui = FakeUiSource([
        {"events": [{"seq": 1, "package": "a"}], "cursor": 1},
        {"events": [{"seq": 2, "package": "b"}], "cursor": 2},
    ])
    poller = EventPoller(bus, ui_source=ui)
    poller.drain_once()
    poller.drain_once()
    pkgs = [e["data"]["package"] for e in bus.poll(since=0)["events"]]
    assert pkgs == ["a", "b"]


def test_drain_publishes_only_new_notifications():
    bus = EventBus()
    notif = FakeNotifSource([
        [{"key": "k1", "package": "com.msg", "title": "A"}],
        [{"key": "k1", "package": "com.msg", "title": "A"},
         {"key": "k2", "package": "com.msg", "title": "B"}],
    ])
    poller = EventPoller(bus, notif_source=notif)
    assert poller.drain_once() == 1   # k1 new
    assert poller.drain_once() == 1   # only k2 new (k1 already seen)
    events = bus.poll(since=0)["events"]
    assert [e["data"]["key"] for e in events] == ["k1", "k2"]
    assert all(e["type"] == "notification_posted" for e in events)
    assert all(e["source"] == "notifications" for e in events)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_event_poller.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'phonectl.daemon.poller'`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/daemon/poller.py
"""Step-wise provider -> EventBus drainer. No threads or sleeps; driven by drain_once()."""
from __future__ import annotations


class EventPoller:
    def __init__(self, bus, *, ui_source=None, notif_source=None) -> None:
        self._bus = bus
        self._ui = ui_source
        self._notif = notif_source
        self._ui_cursor = 0
        self._seen_keys: set = set()

    def drain_once(self, *, max_events: int = 50) -> int:
        published = 0
        if self._ui is not None:
            batch = self._ui.poll_events(self._ui_cursor, max_events=max_events)
            for ev in batch.get("events", []):
                self._bus.publish("ui_changed", ev, source="accessibility")
                published += 1
            self._ui_cursor = batch.get("cursor", self._ui_cursor)
        if self._notif is not None:
            for note in self._notif.list():
                key = note.get("key")
                if key in self._seen_keys:
                    continue
                self._seen_keys.add(key)
                self._bus.publish("notification_posted", note, source="notifications")
                published += 1
        return published

    def tick(self, *, max_events: int = 50) -> int:
        return self.drain_once(max_events=max_events)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_event_poller.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/daemon/poller.py tests/test_event_poller.py
git commit -m "feat: EventPoller — step-wise drain of UI + notification providers onto the EventBus"
```

---

### Task 8: `events_poll` RPC + subscription docs + design-spec/README notes

**Files:**
- Modify: `src/phonectl/daemon/server.py` (`events_poll` RPC; construct the `EventPoller` over warm
  providers; drain it once per `events_poll` so the synchronous test path has fresh events)
- Modify: `README.md` (new "Events & snapshots" section)
- Modify: `docs/superpowers/specs/2026-06-22-phonectl-daemon-event-runtime-design.md` (record the bus/cache
  contracts; note `events_subscribe` deferral)
- Test: `tests/test_daemon_server.py` (append)

**Interfaces:**
- `events_poll` RPC: `handle_line({"method": "events_poll", "params": {"since": int, "max": int}})` →
  `results.ok(capability="events.poll", data=self.events.poll(since, max=max))` (the `{"events","cursor"}`
  shape under `data`). Before polling, the server calls `self.poller.drain_once()` once so a synchronous
  `handle_line` test sees freshly-drained provider events without a background thread.
- `Server.__init__` builds `self.poller = EventPoller(self.events, ui_source=..., notif_source=...)` from
  the warm registry's providers (the `AccessibilityProvider` for UI, the `NotificationsProvider` for
  notifications), each `None` when its provider is absent — so daemons without a companion still serve
  `events_poll` (internal `action_*`/`lifecycle` events only).
- **`events_subscribe` semantics (documented, not a new RPC here):** subscription is **cursor-based
  long-poll** — a client holds the last `cursor`, calls `events_poll(since=cursor, max=N)`, processes the
  batch, and repeats with the new cursor. Server-push / streaming (websocket-style fanout) is a later
  concern and is explicitly deferred; the cursor contract makes it a drop-in evolution.

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_daemon_server.py
def test_events_poll_returns_events_and_cursor(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    srv = make_test_server(tmp_path)
    json.loads(srv.handle_line(_observe_line()))
    srv.handle_line(json.dumps({"method": "act",
                    "params": {"verb": "tap", "target": "i=0", "yes": True},
                    "request_id": "r6"}))
    out = json.loads(srv.handle_line(json.dumps(
        {"method": "events_poll", "params": {"since": 0}, "request_id": "r7"})))
    assert out["ok"] is True
    assert "events" in out["data"] and "cursor" in out["data"]
    types = [e["type"] for e in out["data"]["events"]]
    assert "action_started" in types and "action_finished" in types
    assert out["data"]["cursor"] == out["data"]["events"][-1]["seq"]


def test_events_poll_since_cursor_returns_only_newer(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    srv = make_test_server(tmp_path)
    json.loads(srv.handle_line(_observe_line()))
    srv.handle_line(json.dumps({"method": "act",
                    "params": {"verb": "tap", "target": "i=0", "yes": True},
                    "request_id": "r8"}))
    first = json.loads(srv.handle_line(json.dumps(
        {"method": "events_poll", "params": {"since": 0}, "request_id": "r9"})))
    cursor = first["data"]["cursor"]
    again = json.loads(srv.handle_line(json.dumps(
        {"method": "events_poll", "params": {"since": cursor}, "request_id": "r10"})))
    assert again["data"]["events"] == []
    assert again["data"]["cursor"] == cursor
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_daemon_server.py -v -k "events_poll"`
Expected: FAIL (unknown method `events_poll` → 5.1 dispatcher returns a method-not-found error envelope,
so `out["data"]` / `events` assertions fail).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/daemon/server.py — Server.__init__
from phonectl.daemon.poller import EventPoller

ui_source = self.registry.for_capability("observe_ui_events")     # AccessibilityProvider or None
notif_source = self.registry.for_capability("observe_notifications")  # NotificationsProvider or None
self.poller = EventPoller(self.events, ui_source=ui_source, notif_source=notif_source)
```

```python
# src/phonectl/daemon/server.py — events_poll RPC handler + dispatch registration
def _handle_events_poll(self, params, request_id):
    self.poller.drain_once()  # fold in any pending provider events synchronously
    since = int(params.get("since", 0))
    max_n = int(params.get("max", 100))
    return results.ok(
        capability="events.poll",
        data=self.events.poll(since, max=max_n),
        request_id=request_id,
    )

# register "events_poll" -> self._handle_events_poll in the 5.1 method dispatch table
```

(`for_capability` is the Plan-3.1 registry resolver; it returns the winning provider or `None`. If the
registry exposes a different resolver name, use it.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_daemon_server.py -v -k "events_poll"`
Expected: PASS.

- [ ] **Step 5: Update README + design spec**

In `README.md`, add an **Events & snapshots** section:
- The daemon is the single writer **and** event broker: `observe` returns a `snapshot_id`; every `act`
  returns `snapshot_before`/`snapshot_after` and invalidates the prior snapshot (the re-observe invariant).
- Index acts may pin an expected `snapshot_id`; if the screen changed (id or foreground package), the
  daemon returns a `stale_snapshot` error telling the agent to re-observe (strategy §21).
- Event types (`ui_changed`, `notification_posted`, `action_started`, `action_finished`, `lifecycle`) and
  the **cursor-based** `events_poll(since, max) -> {"events", "cursor"}` subscription contract: hold the
  last `cursor`, re-poll with it as `since`. Note that real server-push streaming is deferred.

In `docs/superpowers/specs/2026-06-22-phonectl-daemon-event-runtime-design.md`, record the implemented
snapshot-cache (monotonic ids, invalidation, foreground stale-check) and event-bus (envelope, types,
poll cursor, provider poller) contracts, and the `events_subscribe` long-poll/streaming-deferred note.

- [ ] **Step 6: Run full suite**

Run: `pytest -v`
Expected: PASS (all tests — the cache/bus/poller modules are additive; no existing CLI/MCP test changes
because they don't construct the daemon `Server`).

- [ ] **Step 7: Commit**

```bash
git add src/phonectl/daemon/server.py tests/test_daemon_server.py README.md \
        docs/superpowers/specs/2026-06-22-phonectl-daemon-event-runtime-design.md
git commit -m "feat: events_poll RPC + provider poller wiring + events/snapshots docs (subscribe=long-poll)"
```

---

## Dependencies

**Requires:** Plan 5.1 (`daemon/server.py` dispatcher with `handle_line`, warm
`ProviderRegistry`/`Session`/`Connection`, single-writer lock, `runs.jsonl` records with `snapshot_before`/
`snapshot_after` = `None`, `runtime.run_action` act routing), Plan 4.1
(`AccessibilityProvider.poll_events` cursor contract + `observe_ui_events` capability), Plan 4.2
(`NotificationsProvider.list` + `observe_notifications` capability), Plan 3.1 (`ProviderRegistry` capability
resolution), Plan 1.2 (stale-snapshot protection / `StaleSnapshotError`), Plan 1.1 (`errors`/`results`/
`capabilities`).
**Enables:** Phase 6 (macros subscribe to the bus via `events_poll` and pin `snapshot_id` on index acts),
and the eventual real-streaming `events_subscribe` (a drop-in over the cursor contract).

## Deferred / out of scope

- **Real server-push streaming `events_subscribe`** — this plan ships only cursor-based long-poll over
  `events_poll`; websocket/SSE-style fanout is a later concern (the cursor contract makes it additive).
- **A background poller thread / scheduler interval** — `EventPoller.drain_once` is the synchronous
  primitive; the daemon's continuous loop + interval + watchdog belong to the broader Phase-5 daemon
  lifecycle (Plan 5.1's loop drives `drain_once`; this plan only adds the primitive and a synchronous
  drain inside `events_poll`).
- **Snapshot eviction / TTL / memory bounds** — the cache grows unbounded in v1; a ring buffer / LRU and
  event-log truncation are a follow-up once real session lengths are measured.
- **Event persistence / durable event log** — events live in-process; durable run records (`runs.jsonl`)
  remain the audit trail. A persisted event log is deferred.
- **Multi-subscriber per-client cursors with server-side bookmarks** — clients track their own cursor in
  v1.
- **The Kotlin companion event stream itself** — Plan 4.1/4.2 own the provider seam; this plan only drains
  whatever those providers return.

## Notes on testability

No device, APK, socket, thread, or sleep is needed. `SnapshotCache` and `EventBus` take an injectable
`id_counter`/`now`, so ids and timestamps are deterministic; both are pure and unit-tested in isolation
(`tests/test_snapshot_cache.py`, `tests/test_event_bus.py`). `EventPoller` is a plain object driven by
`drain_once()`; `tests/test_event_poller.py` feeds canned `poll_events` batches and `list()` results and
asserts the published envelope type/source and cursor/dedup behavior — **no real thread**. The
`daemon/server.py` additions are exercised through Plan 5.1's synchronous `handle_line(line) -> line` and
its `make_test_server(tmp_path)` warm-`Server` helper (over a `FakeBackend`-backed registry): `observe`
returns a `snapshot_id`, a stale pinned id is rejected before dispatch, an act yields distinct
`snapshot_before`/`snapshot_after` (and backfills `runs.jsonl`), acts emit `action_started`/
`action_finished`, and `events_poll` returns the `{"events","cursor"}` shape and filters by cursor.
`monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))` isolates config/audit/run-record state. Existing
CLI/MCP suites stay green because they never construct the daemon `Server`; the cache/bus/poller only
activate inside it.
