# phonectl Daemon Process + JSON-RPC/Socket API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Plan 5.1 of the platform roadmap** (`docs/superpowers/phonectl-platform-roadmap.md`). First plan of
Phase 5. Implements the Phase-5.0 daemon design (`docs/superpowers/specs/2026-06-22-phonectl-daemon-event-runtime-design.md`). Depends on **Plan 2.1** (the `runtime.run_action` single-writer
funnel), **Plan 3.1** (the `ProviderRegistry` + `cli.build_runtime`), and **Plan 4.3**
(`providers/transport.py` — `SocketTransport` + `next_request_id`, the loopback newline-JSON framing it
reuses). **Plan 5.2** lands on top of this one (snapshot cache + event bus).

**Goal:** make a **daemon the single writer and event broker** for all phone actions, with CLI and MCP as
frontends (strategy §22). Concretely, this plan ships: (1) a daemon process (`phonectl daemon`) that builds
the provider runtime **once** and keeps it warm; (2) a **loopback JSON-RPC API** over the Plan-4.3 framing;
(3) **serialized mutating execution** (one global write lock in the daemon, plus the in-process
`run_action` lock for the no-daemon path); (4) **durable run records** (`runs.jsonl`) layered over audit
v2; (5) **frontend auto-routing** so the CLI transparently routes through a reachable daemon or runs
in-process when none is found. The daemon **MUST NOT** be required for v1 primitives — daemonization is a
**compatible evolution**: default builds with no daemon stay byte-for-byte unchanged and every existing
test stays green.

**Architecture:** a new package `src/phonectl/daemon/`. `daemon/discovery.py` reads/writes/removes
`$PHONECTL_HOME/daemon.json` (`{pid, host, port, version, started_at}`) and exposes `discover()` (read +
ping; stale files failing ping are ignored). `daemon/rpc.py` is a method registry (`register` /
`dispatch(name, params, ctx) -> results-envelope`; unknown method → `results.err(UnknownMethodError)`).
`daemon/server.py` is the `DaemonServer`: it calls `cli.build_runtime(cfg)` once, keeps
`(registry, session, conn)` warm, exposes a synchronous `handle_line(line) -> line` that parses the request
envelope and dispatches, serializes **mutating** methods under a global `threading.Lock` (single writer),
and reuses `run_action` verbatim by passing it a `build=` callable that returns the warm triple.
`daemon/records.py` builds + appends one durable run record per action to `runs.jsonl`.
`daemon/client.py` is the `DaemonClient` over `SocketTransport`. `cli.py` gains `_dispatch(method, params,
in_process_fn)` (route via daemon when reachable, else in-process) and a `phonectl daemon`
start/status/stop command group. The accept loop binds **loopback TCP only** (`127.0.0.1`); tests drive
`handle_line` directly and never open a real socket.

**Tech Stack:** Python 3 (stdlib only: `socket`, `json`, `threading`, `selectors`, `signal`, `os`, `time`,
`uuid`); `pytest` for tests; no new runtime deps. Termux:Boot autostart and companion foreground-service
hosting are noted as **seams only** — `phonectl daemon` runs foreground for now.

## Global Constraints

- **stdlib-only at runtime.** The daemon uses `socket`, `json`, `threading`, `selectors`, `signal`, `os`,
  `time`, `uuid` — all stdlib. No third-party deps.
- **Backend isolation.** The daemon talks to the phone **only** through the warm `ProviderRegistry` built
  by `cli.build_runtime`; it never calls `adb`/`subprocess` directly. The RPC layer speaks JSON over
  loopback, nothing more.
- **`ui_parser.py` stays pure** and untouched.
- **Index / selector / `(x,y)` targeting preserved.** RPC `act`/`find`/`observe` carry the same targeting
  contract; the daemon adds no new targeting model.
- **Every `act()` re-observes.** Mutating RPCs route through `runtime.run_action`, which already
  re-observes and returns the post-action snapshot — the daemon does not fork that path.
- **Modes + kill-switch + risk gate every mutating action.** Unchanged: acts go through `run_action`, so
  `audit.kill_switch_active`, `config.get_mode`, and the risk policy still gate them. The daemon adds no
  bypass.
- **Local-only.** The server binds/listens on **loopback** (`127.0.0.1`) exclusively; `daemon_host` is
  validated and a non-loopback host is rejected with a clear error. Never bind `0.0.0.0`.
- **Structured-result invariant (Plan 1.1).** Every RPC response is a `results.ok`/`results.err` envelope
  carrying `request_id` + `version`.
- **Compatible evolution.** With no daemon running, `discover()` returns `None` and every frontend falls
  back to today's in-process path — existing CLI/MCP behavior and tests are unchanged.
- **Injectable seams.** `DaemonServer(cfg, *, build=cli.build_runtime, now=time.time, ...)`;
  `discovery` functions take an explicit `home`/path-free `PHONECTL_HOME`; `DaemonClient(*, transport=...)`.
  Tests inject fakes and isolate state via `monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))`.
- **One commit per task. TDD order is non-negotiable.**

## Shared conventions established by this plan

- **New package** `src/phonectl/daemon/` (`__init__.py` + `discovery.py`, `rpc.py`, `server.py`,
  `records.py`, `client.py`).
- **Discovery file:** `$PHONECTL_HOME/daemon.json` = `{"pid", "host", "port", "version", "started_at"}`,
  written on start and removed on clean stop. There is **no static daemon port in config**; the port is
  chosen at start and published here.
- **Wire framing (reused from Plan 4.3):** newline-delimited JSON. Request line:
  `{"method", "params", "request_id", "timeout", "version"}`. Response line: the `results` envelope
  (`{"ok", ..., "request_id", "version", "data"|"error"}`).
- **RPC methods:** `ping`, `status`, `observe`, `act`, `find`, `capabilities`, `policy_explain`,
  `audit_query`, `stop`, `resume`. `act` routes its verb through `runtime.run_action`. Mutating methods
  (`act`, `stop`, `resume`) serialize under the daemon's global write lock; read-only methods do not.
- **Run records:** `$PHONECTL_HOME/runs.jsonl`, one record per action: `action_id`, `parent_task_id`
  (optional), `request_id`, `verb`, `target`, `provider`, `snapshot_before`, `snapshot_after`
  (`None` until Plan 5.2 wires the cache), `risk` (`risk_level`/`decision`/`reasons`), `retries`,
  `outcome`, `user_approved`. This **extends** audit v2 (`actions.jsonl` is unchanged); it is a new
  record/parent-ID layer, not a replacement.
- **New additive error codes (in `errors.py` this plan):** `DaemonUnreachableError` (code
  `"daemon_unreachable"`), `UnknownMethodError` (code `"unknown_method"`).
- **Config keys added:** `daemon_host` (default `"127.0.0.1"`, loopback-only — reject non-loopback with a
  clear error), `daemon_autostart` (bool, default `false`). **No `daemon_port`.**
- **Protocol version:** `PROTOCOL_VERSION = 1` (the daemon RPC version published in `daemon.json` and
  echoed in every response).

---

### Task 1: Additive errors + `daemon` package + discovery file

**Files:**
- Modify: `src/phonectl/errors.py`
- Create: `src/phonectl/daemon/__init__.py`, `src/phonectl/daemon/discovery.py`
- Test: `tests/test_daemon_discovery.py`

**Interfaces:**
- `errors.DaemonUnreachableError` (code `"daemon_unreachable"`, `retryable=True`),
  `errors.UnknownMethodError` (code `"unknown_method"`).
- `discovery.write(info: dict) -> Path` — writes `$PHONECTL_HOME/daemon.json` (host validated loopback).
- `discovery.read() -> dict | None` — parsed `daemon.json` or `None` if absent/corrupt.
- `discovery.remove() -> None` — deletes the file if present (idempotent).
- `discovery.discover(*, ping=...) -> dict | None` — read the file; ping the advertised endpoint; return
  the info dict iff reachable, else `None` (stale/unpingable files are ignored, not removed here).
- `discovery.LOOPBACK = {"127.0.0.1", "localhost", "::1"}`; non-loopback host on `write` → `ValueError`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_daemon_discovery.py
import json

import pytest

from phonectl import errors
from phonectl.daemon import discovery


def test_daemon_unreachable_and_unknown_method_codes():
    assert errors.DaemonUnreachableError().code == "daemon_unreachable"
    assert errors.UnknownMethodError().code == "unknown_method"


def test_write_read_remove_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    info = {"pid": 4321, "host": "127.0.0.1", "port": 8799, "version": 1, "started_at": 1.0}
    path = discovery.write(info)
    assert path.exists()
    assert discovery.read()["port"] == 8799
    discovery.remove()
    assert discovery.read() is None
    discovery.remove()  # idempotent


def test_write_rejects_non_loopback(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    with pytest.raises(ValueError):
        discovery.write({"pid": 1, "host": "10.0.0.5", "port": 8799, "version": 1, "started_at": 0.0})


def test_read_corrupt_file_is_none(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    (tmp_path / "daemon.json").write_text("{not json")
    assert discovery.read() is None


def test_discover_reachable_calls_ping(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    discovery.write({"pid": 1, "host": "127.0.0.1", "port": 8799, "version": 1, "started_at": 0.0})
    seen = {}

    def fake_ping(host, port):
        seen["called"] = (host, port)
        return True

    assert discovery.discover(ping=fake_ping)["port"] == 8799
    assert seen["called"] == ("127.0.0.1", 8799)


def test_discover_stale_file_failing_ping_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    discovery.write({"pid": 1, "host": "127.0.0.1", "port": 8799, "version": 1, "started_at": 0.0})
    assert discovery.discover(ping=lambda h, p: False) is None
    assert discovery.read() is not None  # not removed, just ignored


def test_discover_no_file_is_none(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    assert discovery.discover(ping=lambda h, p: True) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_daemon_discovery.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'phonectl.daemon'` / `AttributeError: DaemonUnreachableError`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/errors.py — append

class DaemonUnreachableError(PhonectlError):
    # No reachable daemon was found; frontends fall back to the in-process path.
    code = "daemon_unreachable"
    retryable = True


class UnknownMethodError(PhonectlError):
    # The daemon received an RPC method it has no handler for.
    code = "unknown_method"
```

```python
# src/phonectl/daemon/__init__.py
"""phonectl daemon: single-writer process + loopback JSON-RPC API."""

PROTOCOL_VERSION = 1
```

```python
# src/phonectl/daemon/discovery.py
"""Daemon discovery: publish/read/remove $PHONECTL_HOME/daemon.json and probe it."""
from __future__ import annotations

import json
from pathlib import Path

from phonectl.config import config_dir

LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def _path() -> Path:
    return config_dir() / "daemon.json"


def write(info: dict) -> Path:
    host = info.get("host", "127.0.0.1")
    if host not in LOOPBACK:
        raise ValueError(f"daemon is loopback-only; refusing host {host!r}")
    p = _path()
    p.write_text(json.dumps(info))
    return p


def read() -> dict | None:
    p = _path()
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except (ValueError, OSError):
        return None


def remove() -> None:
    p = _path()
    if p.exists():
        p.unlink()


def discover(*, ping) -> dict | None:
    info = read()
    if not info:
        return None
    try:
        if ping(info["host"], info["port"]):
            return info
    except Exception:  # noqa: BLE001 — a dead endpoint must not raise to the frontend
        return None
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_daemon_discovery.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/errors.py src/phonectl/daemon/__init__.py src/phonectl/daemon/discovery.py tests/test_daemon_discovery.py
git commit -m "feat: daemon package + discovery.json + daemon_unreachable/unknown_method errors"
```

---

### Task 2: `daemon/rpc.py` — method registry + dispatch

**Files:**
- Create: `src/phonectl/daemon/rpc.py`
- Test: `tests/test_daemon_rpc.py`

**Interfaces:**
- `rpc.Registry()` — holds `name -> handler`. `handler(params: dict, ctx) -> results-envelope`.
- `Registry.register(name)` — decorator (or `register(name, fn)`); registering a duplicate name raises.
- `Registry.dispatch(name, params, ctx) -> dict` — calls the handler and returns its envelope; an unknown
  name returns `results.err(errors.UnknownMethodError(...))`; a handler that raises a `PhonectlError`
  returns `results.err(e)`; any other exception is wrapped as `results.err(("internal_error", str(e)))`.
- `MUTATING = {"act", "stop", "resume"}` — the set the server serializes under the write lock.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_daemon_rpc.py
from phonectl.daemon import rpc


def test_register_and_dispatch_ok():
    reg = rpc.Registry()

    @reg.register("ping")
    def _ping(params, ctx):
        from phonectl import results
        return results.ok(capability="daemon.ping", data={"pong": True})

    out = reg.dispatch("ping", {}, ctx=None)
    assert out["ok"] is True and out["data"]["pong"] is True


def test_unknown_method_is_error_envelope():
    reg = rpc.Registry()
    out = reg.dispatch("nope", {}, ctx=None)
    assert out["ok"] is False
    assert out["error"]["code"] == "unknown_method"


def test_handler_phonectl_error_becomes_envelope():
    reg = rpc.Registry()

    @reg.register("boom")
    def _boom(params, ctx):
        from phonectl import errors
        raise errors.CapabilityUnavailableError("nope")

    out = reg.dispatch("boom", {}, ctx=None)
    assert out["ok"] is False and out["error"]["code"] == "capability_unavailable"


def test_handler_unexpected_error_is_internal_error():
    reg = rpc.Registry()

    @reg.register("kaboom")
    def _kaboom(params, ctx):
        raise RuntimeError("unexpected")

    out = reg.dispatch("kaboom", {}, ctx=None)
    assert out["ok"] is False and out["error"]["code"] == "internal_error"


def test_duplicate_registration_raises():
    reg = rpc.Registry()
    reg.register("dup")(lambda p, c: None)
    try:
        reg.register("dup")(lambda p, c: None)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_mutating_set_contains_act_stop_resume():
    assert rpc.MUTATING == {"act", "stop", "resume"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_daemon_rpc.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'phonectl.daemon.rpc'`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/daemon/rpc.py
"""RPC method registry: name -> handler, dispatch to a results envelope."""
from __future__ import annotations

from phonectl import errors, results

MUTATING = {"act", "stop", "resume"}


class Registry:
    def __init__(self) -> None:
        self._handlers: dict = {}

    def register(self, name):
        def deco(fn):
            if name in self._handlers:
                raise ValueError(f"duplicate RPC method {name!r}")
            self._handlers[name] = fn
            return fn
        return deco

    def has(self, name) -> bool:
        return name in self._handlers

    def dispatch(self, name, params, ctx) -> dict:
        handler = self._handlers.get(name)
        if handler is None:
            return results.err(
                errors.UnknownMethodError(f"no RPC method {name!r}"),
                user_action="Call a supported method; see daemon status for the method list.",
            )
        try:
            return handler(params or {}, ctx)
        except errors.PhonectlError as e:
            return results.err(e, **getattr(e, "lock_state", {}))
        except Exception as e:  # noqa: BLE001
            return results.err(("internal_error", str(e)))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_daemon_rpc.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/daemon/rpc.py tests/test_daemon_rpc.py
git commit -m "feat: daemon RPC registry + dispatch to results envelopes"
```

---

### Task 3: `daemon/server.py` — `handle_line` + single-writer dispatch

**Files:**
- Create: `src/phonectl/daemon/server.py`
- Test: `tests/test_daemon_server.py`

**Interfaces:**
- `DaemonServer(cfg, *, build=cli.build_runtime, now=time.time, registry=None)` — `build` is the injectable
  `cli.build_runtime`; `registry` is an injectable `rpc.Registry` (defaults to a freshly-populated one).
  Constructor validates `cfg.get("daemon_host", "127.0.0.1")` is loopback (else `ValueError`).
- `DaemonServer.handle_line(line: str) -> str` — parse the request envelope; build a `ctx` for the
  handler; serialize mutating methods (`name in rpc.MUTATING`) under `self._write_lock`; return one
  response JSON line (`results` envelope) carrying the request's `request_id` + `PROTOCOL_VERSION`. A
  malformed line returns an error envelope (`("bad_request", ...)`) with `request_id=None`.
- The server registers a built-in `ping` handler in this task (returns `results.ok(data={"pong": True})`)
  so `handle_line` can be exercised end-to-end without the heavier handlers (added in Task 5).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_daemon_server.py
import json
import threading

from phonectl import config
from phonectl.daemon import PROTOCOL_VERSION
from phonectl.daemon.server import DaemonServer


def _req(method, params=None, rid="r1"):
    return json.dumps({"method": method, "params": params or {}, "request_id": rid,
                       "timeout": 2.0, "version": PROTOCOL_VERSION})


def test_handle_line_ping(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    srv = DaemonServer(config.load(), build=lambda cfg: (object(), object(), None))
    resp = json.loads(srv.handle_line(_req("ping")))
    assert resp["ok"] is True and resp["data"]["pong"] is True
    assert resp["request_id"] == "r1"
    assert resp["version"] == PROTOCOL_VERSION


def test_handle_line_unknown_method(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    srv = DaemonServer(config.load(), build=lambda cfg: (object(), object(), None))
    resp = json.loads(srv.handle_line(_req("does_not_exist")))
    assert resp["ok"] is False and resp["error"]["code"] == "unknown_method"


def test_handle_line_bad_request(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    srv = DaemonServer(config.load(), build=lambda cfg: (object(), object(), None))
    resp = json.loads(srv.handle_line("{not json"))
    assert resp["ok"] is False and resp["error"]["code"] == "bad_request"


def test_non_loopback_daemon_host_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    try:
        DaemonServer({"daemon_host": "0.0.0.0"}, build=lambda cfg: (None, None, None))
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_mutating_method_holds_write_lock(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    srv = DaemonServer(config.load(), build=lambda cfg: (object(), object(), None))
    observed = {}

    # Register a fake mutating handler that records whether the write lock is held.
    @srv.registry.register("act")
    def _act(params, ctx):
        from phonectl import results
        observed["locked"] = srv._write_lock.locked()
        return results.ok(capability="ui.act", data={})

    srv.handle_line(_req("act"))
    assert observed["locked"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_daemon_server.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'phonectl.daemon.server'`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/daemon/server.py
"""DaemonServer: warm runtime + synchronous handle_line dispatch (single writer)."""
from __future__ import annotations

import json
import threading
import time

from phonectl import results
from phonectl.daemon import PROTOCOL_VERSION
from phonectl.daemon import rpc as rpc_mod
from phonectl.daemon.discovery import LOOPBACK


class DaemonServer:
    def __init__(self, cfg, *, build=None, now=time.time, registry=None) -> None:
        host = cfg.get("daemon_host", "127.0.0.1")
        if host not in LOOPBACK:
            raise ValueError(f"daemon is loopback-only; refusing daemon_host {host!r}")
        if build is None:
            from phonectl import cli  # late import: avoid CLI import cost at module load
            build = cli.build_runtime
        self._cfg = cfg
        self._host = host
        self._build = build
        self._now = now
        self.registry = registry or rpc_mod.Registry()
        self._write_lock = threading.Lock()
        self._warm = None  # (registry, session, conn), built lazily on first use
        self._register_builtins()

    def _register_builtins(self) -> None:
        @self.registry.register("ping")
        def _ping(params, ctx):
            return results.ok(capability="daemon.ping", data={"pong": True})

    def handle_line(self, line: str) -> str:
        try:
            req = json.loads(line)
            method = req["method"]
            params = req.get("params", {})
            rid = req.get("request_id")
        except (ValueError, KeyError, TypeError):
            return self._finish(results.err(("bad_request", "malformed RPC request line")), None)

        ctx = {"server": self, "request_id": rid}
        if method in rpc_mod.MUTATING:
            with self._write_lock:
                env = self.registry.dispatch(method, params, ctx)
        else:
            env = self.registry.dispatch(method, params, ctx)
        return self._finish(env, rid)

    def _finish(self, env: dict, rid) -> str:
        env = dict(env)
        env.setdefault("request_id", rid)
        env["version"] = PROTOCOL_VERSION
        return json.dumps(env)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_daemon_server.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/daemon/server.py tests/test_daemon_server.py
git commit -m "feat: DaemonServer.handle_line — loopback-guarded single-writer RPC dispatch"
```

---

### Task 4: Warm provider lifecycle + `act` via `run_action`

**Files:**
- Modify: `src/phonectl/daemon/server.py`
- Test: `tests/test_daemon_server.py` (append)

**Interfaces:**
- `DaemonServer._warm_triple() -> (registry, session, conn)` — calls `self._build(self._cfg)` **once**,
  caches the result, and reuses it on subsequent calls. On a `conn` that supports it,
  `conn.ensure()`/`conn.reconnect()` may be invoked on demand (gate `reconnect` via `hasattr`).
- The built-in `act` handler routes through `runtime.run_action(verb, fn, target, build=<warm-returning>,
  ...)`, where the injected `build=` is a callable returning the cached warm triple (so `run_action`
  reuses one registry instead of rebuilding). `params` carry `verb`, `target`, and a serializable action
  descriptor; for this task the handler accepts an injectable `fn_for(params)` so tests can drive it
  without the full CLI selector plumbing (the real selector→`fn` mapping reuses `cli` helpers in Task 5).

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_daemon_server.py
def test_warm_triple_builds_once(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    calls = {"n": 0}

    def build(cfg):
        calls["n"] += 1
        return ("REG", "SESS", None)

    srv = DaemonServer(config.load(), build=build)
    a = srv._warm_triple()
    b = srv._warm_triple()
    assert a is b
    assert calls["n"] == 1


def test_act_reuses_one_registry_across_two_calls(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    from phonectl.providers.registry import ProviderRegistry
    from phonectl.session import Session
    from tests.test_cli import FakeBackend

    registry = ProviderRegistry([FakeBackend()])
    session = Session()

    class FakeConn:
        def ensure(self):
            pass

    build_calls = {"n": 0}

    def build(cfg):
        build_calls["n"] += 1
        return registry, session, FakeConn()

    srv = DaemonServer(config.load(), build=build)
    # auto mode so run_action does not require --yes; FakeBackend low-risk taps
    rid = "x1"
    line = json.dumps({"method": "act",
                       "params": {"verb": "tap", "target": {"i": 0}, "i": 0},
                       "request_id": rid, "timeout": 2.0, "version": PROTOCOL_VERSION})
    r1 = json.loads(srv.handle_line(line))
    r2 = json.loads(srv.handle_line(line))
    assert r1["ok"] is True and r2["ok"] is True
    assert build_calls["n"] == 1  # warm triple built once, reused by run_action
```

(If `tests/test_cli.py` does not expose a reusable `FakeBackend` low-risk tap, mirror its existing
fake — the point of the test is that `build` is invoked exactly once across two acts.)

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_daemon_server.py -v -k "warm or reuses"`
Expected: FAIL (`AttributeError: _warm_triple` / `KeyError` — no `act` handler yet beyond the Task-3 fake).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/daemon/server.py — additions

from phonectl import runtime


class DaemonServer:
    # ... (existing __init__ / _register_builtins / handle_line / _finish) ...

    def _warm_triple(self):
        if self._warm is None:
            self._warm = self._build(self._cfg)
        return self._warm

    def _register_builtins(self) -> None:
        @self.registry.register("ping")
        def _ping(params, ctx):
            return results.ok(capability="daemon.ping", data={"pong": True})

        @self.registry.register("act")
        def _act(params, ctx):
            verb = params["verb"]
            target = params.get("target", {})
            fn = self._fn_for(params)

            def warm_build(cfg):
                # run_action calls build(cfg); hand it the cached warm triple so it
                # reuses one registry/session/conn instead of rebuilding.
                return self._warm_triple()

            env = runtime.run_action(
                verb, fn, target,
                build=warm_build,
                yes=bool(params.get("yes", False)),
                cfg=self._cfg,
                request_id=ctx.get("request_id"),
                idempotency_key=params.get("idempotency_key"),
            )
            return env

    def _fn_for(self, params):
        """Map a serialized action descriptor to an actuator callable.

        Default mapping covers index/(x,y) taps for the warm-reuse smoke; Task 5
        replaces this with the shared cli selector->fn helper.
        """
        from phonectl import actuator
        verb = params["verb"]
        if verb == "tap":
            if "i" in params:
                return lambda b, s: actuator.tap(b, s, i=params["i"])
            x, y = params["x"], params["y"]
            return lambda b, s: actuator.tap(b, s, x=x, y=y)
        raise NotImplementedError(f"no fn mapping for verb {verb!r}")
```

(The `conn.ensure()`/`reconnect()` calls already happen inside `run_action` via the `build` it receives;
the warm triple's `conn` is reused, so connection recovery is per Plan 1.3, gated by `hasattr` where the
fake conn lacks `reconnect`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_daemon_server.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/daemon/server.py tests/test_daemon_server.py
git commit -m "feat: daemon warm provider lifecycle + act routed through run_action (build once)"
```

---

### Task 5: RPC handlers for observe/find/capabilities/policy_explain/audit_query/stop/resume/status

**Files:**
- Modify: `src/phonectl/daemon/server.py`
- Test: `tests/test_daemon_server.py` (append)

**Interfaces (each returns a `results` envelope; all reuse existing modules):**
- `observe` — `observer.observe(registry, session, **params)`; returns `results.ok(capability="ui.observe",
  provider=registry.last_used, data=snap)`.
- `find` — selector resolution over the latest snapshot (reuse the 1.2/3.4 find path); returns matches.
- `capabilities` — `results.ok(capability="daemon.capabilities", data=registry.capabilities())`.
- `policy_explain` — `policy.explain(session.last, params["verb"], params["target"], cfg)` wrapped in
  `results.ok`.
- `audit_query` — `audit.read_entries(limit=params.get("limit"))` wrapped in `results.ok`.
- `stop` — write the `$PHONECTL_HOME/STOP` sentinel (reuse the Plan 2.1 stop path); `results.ok`.
- `resume` — remove the sentinel; `results.ok`.
- `status` — daemon liveness summary: `{"pid", "host", "port", "protocol_version", "warm": bool,
  "methods": [...]}`; read-only, never holds the write lock.

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_daemon_server.py
def _srv(tmp_path):
    from phonectl.providers.registry import ProviderRegistry
    from phonectl.session import Session
    from tests.test_cli import FakeBackend

    registry = ProviderRegistry([FakeBackend()])
    session = Session()

    class FakeConn:
        def ensure(self):
            pass

    return DaemonServer(config.load(), build=lambda cfg: (registry, session, FakeConn()))


def test_capabilities_method(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    srv = _srv(tmp_path)
    resp = json.loads(srv.handle_line(_req("capabilities")))
    assert resp["ok"] is True and isinstance(resp["data"], dict)


def test_observe_method_returns_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    srv = _srv(tmp_path)
    resp = json.loads(srv.handle_line(_req("observe")))
    assert resp["ok"] is True and "hash" in resp["data"]


def test_stop_then_resume_toggles_sentinel(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    from phonectl import audit
    srv = _srv(tmp_path)
    assert json.loads(srv.handle_line(_req("stop")))["ok"] is True
    assert audit.kill_switch_active() is True
    assert json.loads(srv.handle_line(_req("resume")))["ok"] is True
    assert audit.kill_switch_active() is False


def test_audit_query_returns_entries(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    srv = _srv(tmp_path)
    resp = json.loads(srv.handle_line(_req("audit_query", {"limit": 5})))
    assert resp["ok"] is True and isinstance(resp["data"], list)


def test_status_method(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    srv = _srv(tmp_path)
    resp = json.loads(srv.handle_line(_req("status")))
    assert resp["ok"] is True
    assert resp["data"]["protocol_version"] == PROTOCOL_VERSION
    assert "ping" in resp["data"]["methods"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_daemon_server.py -v -k "capabilities or observe_method or stop_then or audit_query or status_method"`
Expected: FAIL (`unknown_method` for each new method).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/daemon/server.py — extend _register_builtins

from phonectl import audit, config, observer, policy


# inside _register_builtins(self):

        @self.registry.register("observe")
        def _observe(params, ctx):
            registry, session, conn = self._warm_triple()
            if hasattr(conn, "ensure"):
                conn.ensure()
            snap = observer.observe(registry, session, **{
                k: params[k] for k in ("screenshot", "snap_path", "tree", "relations")
                if k in params
            })
            return results.ok(capability="ui.observe",
                              provider=getattr(registry, "last_used", None) or "adb",
                              data=snap)

        @self.registry.register("find")
        def _find(params, ctx):
            registry, session, conn = self._warm_triple()
            if hasattr(conn, "ensure"):
                conn.ensure()
            observer.observe(registry, session)
            from phonectl import selectors  # reuse 1.2 selector resolution
            matches = selectors.find(session.last, params.get("selector", {}))
            return results.ok(capability="ui.find", data={"matches": matches})

        @self.registry.register("capabilities")
        def _capabilities(params, ctx):
            registry, _, _ = self._warm_triple()
            return results.ok(capability="daemon.capabilities", data=registry.capabilities())

        @self.registry.register("policy_explain")
        def _policy_explain(params, ctx):
            registry, session, conn = self._warm_triple()
            decision = policy.explain(session.last, params["verb"], params.get("target", {}), self._cfg)
            return results.ok(capability="policy.explain", data=decision)

        @self.registry.register("audit_query")
        def _audit_query(params, ctx):
            entries = audit.read_entries(limit=params.get("limit"))
            return results.ok(capability="audit.query", data=entries)

        @self.registry.register("stop")
        def _stop(params, ctx):
            (config.config_dir() / "STOP").write_text("stopped via daemon\n")
            return results.ok(capability="daemon.stop", data={"stopped": True})

        @self.registry.register("resume")
        def _resume(params, ctx):
            p = config.config_dir() / "STOP"
            if p.exists():
                p.unlink()
            return results.ok(capability="daemon.resume", data={"stopped": False})

        @self.registry.register("status")
        def _status(params, ctx):
            return results.ok(capability="daemon.status", data={
                "pid": __import__("os").getpid(),
                "host": self._host,
                "port": getattr(self, "_port", None),
                "protocol_version": PROTOCOL_VERSION,
                "warm": self._warm is not None,
                "methods": sorted(self.registry._handlers),
            })
```

(`find` reuses the Plan-1.2 `selectors` resolver; if the shipped function name differs, call that name —
do not introduce a second resolver. `stop`/`resume` reuse the Plan-2.1 sentinel path; if 2.1 exposed a
helper to set/clear `STOP`, call it instead of writing the file directly.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_daemon_server.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/daemon/server.py tests/test_daemon_server.py
git commit -m "feat: daemon RPC handlers — observe/find/capabilities/policy_explain/audit_query/stop/resume/status"
```

---

### Task 6: Durable run records → `runs.jsonl`

**Files:**
- Create: `src/phonectl/daemon/records.py`
- Modify: `src/phonectl/daemon/server.py` (append a record per `act`)
- Test: `tests/test_daemon_records.py`, `tests/test_daemon_server.py` (append)

**Interfaces:**
- `records.build_record(env, params, *, action_id, now) -> dict` — **pure**: derive the run record from an
  `act` results envelope + the request params. Fields: `action_id`, `parent_task_id` (from
  `params.get("parent_task_id")`), `request_id`, `verb`, `target`, `provider`, `snapshot_before` (`None`
  for now), `snapshot_after` (`env["data"]` on success, else `None`), `risk`
  (`{risk_level, decision, reasons}` from the envelope, when present), `retries` (`0` for now),
  `outcome` (`"ok"`/the error code), `user_approved` (`bool(params.get("yes"))`).
- `records.append(record) -> None` — append one JSON line to `$PHONECTL_HOME/runs.jsonl`.
- `records.read(limit=None) -> list[dict]` — read records (test helper / future `runs query`).
- Server: after each `act`, build + append a record (injectable `now`/append seam for tests).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_daemon_records.py
from phonectl.daemon import records


def test_build_record_from_ok_envelope():
    env = {"ok": True, "data": {"hash": "abc"}, "provider": "AdbBackend",
           "risk_level": "low", "request_id": "r9"}
    rec = records.build_record(
        env, {"verb": "tap", "target": {"i": 3}, "yes": True, "parent_task_id": "t1"},
        action_id="a1", now=lambda: 123.0)
    assert rec["action_id"] == "a1"
    assert rec["parent_task_id"] == "t1"
    assert rec["request_id"] == "r9"
    assert rec["verb"] == "tap" and rec["target"] == {"i": 3}
    assert rec["provider"] == "AdbBackend"
    assert rec["snapshot_before"] is None
    assert rec["snapshot_after"] == {"hash": "abc"}
    assert rec["outcome"] == "ok"
    assert rec["user_approved"] is True
    assert rec["retries"] == 0


def test_build_record_from_error_envelope():
    env = {"ok": False, "error": {"code": "guarded_action"}, "request_id": "r1"}
    rec = records.build_record(env, {"verb": "type", "target": {"text": "x"}},
                               action_id="a2", now=lambda: 1.0)
    assert rec["outcome"] == "guarded_action"
    assert rec["snapshot_after"] is None
    assert rec["user_approved"] is False


def test_append_and_read_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    records.append({"action_id": "a1", "verb": "tap"})
    records.append({"action_id": "a2", "verb": "type"})
    rows = records.read()
    assert [r["action_id"] for r in rows] == ["a1", "a2"]


# Append to tests/test_daemon_server.py
def test_act_appends_run_record(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    from phonectl.daemon import records
    srv = _srv(tmp_path)
    line = json.dumps({"method": "act", "params": {"verb": "tap", "target": {"i": 0}, "i": 0},
                       "request_id": "rr", "timeout": 2.0, "version": PROTOCOL_VERSION})
    srv.handle_line(line)
    rows = records.read()
    assert len(rows) == 1
    assert rows[0]["verb"] == "tap" and rows[0]["request_id"] == "rr"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_daemon_records.py tests/test_daemon_server.py -v -k "record"`
Expected: FAIL (`ModuleNotFoundError: phonectl.daemon.records` / no record appended).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/daemon/records.py
"""Durable run records (runs.jsonl): one record per daemon action, layered on audit v2."""
from __future__ import annotations

import json
import time

from phonectl.config import config_dir


def _path():
    return config_dir() / "runs.jsonl"


def build_record(env, params, *, action_id, now=time.time) -> dict:
    ok = bool(env.get("ok"))
    risk = None
    if "risk_level" in env:
        risk = {
            "risk_level": env.get("risk_level"),
            "decision": env.get("decision"),
            "reasons": env.get("reasons"),
        }
    return {
        "ts": now(),
        "action_id": action_id,
        "parent_task_id": params.get("parent_task_id"),
        "request_id": env.get("request_id"),
        "verb": params.get("verb"),
        "target": params.get("target"),
        "provider": env.get("provider"),
        "snapshot_before": None,   # wired by Plan 5.2 (snapshot cache)
        "snapshot_after": env.get("data") if ok else None,
        "risk": risk,
        "retries": 0,
        "outcome": "ok" if ok else env.get("error", {}).get("code", "error"),
        "user_approved": bool(params.get("yes", False)),
    }


def append(record: dict) -> None:
    with open(_path(), "a") as f:
        f.write(json.dumps(record) + "\n")


def read(limit=None) -> list:
    p = _path()
    if not p.exists():
        return []
    rows = [json.loads(line) for line in p.read_text().splitlines() if line.strip()]
    return rows[-limit:] if limit else rows
```

```python
# src/phonectl/daemon/server.py — in the _act handler, after run_action returns env:
        from phonectl.daemon import records as _records
        rec = _records.build_record(env, params, action_id=_records_id(), now=self._now)
        self._append_record(rec)
        return env
```

```python
# src/phonectl/daemon/server.py — helpers (injectable append for tests)
    def _append_record(self, rec):
        from phonectl.daemon import records as _records
        _records.append(rec)
```

(`_records_id()` is `uuid.uuid4().hex`; keep `_append_record` a method so a test can monkeypatch it.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_daemon_records.py tests/test_daemon_server.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/daemon/records.py src/phonectl/daemon/server.py tests/test_daemon_records.py tests/test_daemon_server.py
git commit -m "feat: durable run records (runs.jsonl) appended per daemon act, layered on audit v2"
```

---

### Task 7: `daemon/client.py` — `DaemonClient` over `SocketTransport`

**Files:**
- Create: `src/phonectl/daemon/client.py`
- Test: `tests/test_daemon_client.py`

**Interfaces:**
- `DaemonClient(host, port, *, transport=None, version=PROTOCOL_VERSION)` — default `transport` is a
  `SocketTransport(host, port)` (Plan 4.3); tests inject a fake.
- `DaemonClient.call(method, params=None, *, timeout=5.0) -> dict` — sends one request via the transport
  (using `next_request_id()`), returns the response envelope. A transport failure / no-match returns
  `results.err(DaemonUnreachableError(...))`.
- `DaemonClient.is_running() -> bool` — `discovery.discover(ping=...)` is truthy, where `ping` issues a
  `ping` call and checks `ok`.
- `DaemonClient.from_discovery(info) -> DaemonClient` — build a client from a `daemon.json` info dict.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_daemon_client.py
from phonectl.daemon import PROTOCOL_VERSION
from phonectl.daemon.client import DaemonClient


class FakeTransport:
    """Echoes a scripted response for each request; records sent requests."""
    def __init__(self, responder):
        self._responder = responder
        self.sent = []

    def request(self, method, params, *, request_id, timeout):
        self.sent.append((method, params))
        return self._responder(method, params, request_id)


def test_call_returns_matching_envelope():
    def responder(method, params, rid):
        return {"ok": True, "request_id": rid, "version": PROTOCOL_VERSION, "data": {"m": method}}
    c = DaemonClient("127.0.0.1", 8799, transport=FakeTransport(responder))
    out = c.call("capabilities", {})
    assert out["ok"] is True and out["data"]["m"] == "capabilities"


def test_call_unreachable_returns_daemon_unreachable():
    def responder(method, params, rid):
        raise OSError("connection refused")
    c = DaemonClient("127.0.0.1", 8799, transport=FakeTransport(responder))
    out = c.call("ping", {})
    assert out["ok"] is False and out["error"]["code"] == "daemon_unreachable"


def test_is_running_true_on_ok_ping():
    def responder(method, params, rid):
        return {"ok": True, "request_id": rid, "version": PROTOCOL_VERSION, "data": {"pong": True}}
    c = DaemonClient("127.0.0.1", 8799, transport=FakeTransport(responder))
    assert c.is_running() is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_daemon_client.py -v`
Expected: FAIL (`ModuleNotFoundError: phonectl.daemon.client`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/daemon/client.py
"""DaemonClient: frontend RPC over the Plan-4.3 SocketTransport."""
from __future__ import annotations

from phonectl import errors, results
from phonectl.daemon import PROTOCOL_VERSION
from phonectl.providers.transport import SocketTransport, next_request_id


class DaemonClient:
    def __init__(self, host, port, *, transport=None, version=PROTOCOL_VERSION) -> None:
        self._host, self._port, self._version = host, port, version
        self._transport = transport or SocketTransport(host, port, version=version)

    @classmethod
    def from_discovery(cls, info, *, transport=None):
        return cls(info["host"], info["port"], transport=transport,
                   version=info.get("version", PROTOCOL_VERSION))

    def call(self, method, params=None, *, timeout=5.0) -> dict:
        rid = next_request_id()
        try:
            resp = self._transport.request(method, params or {}, request_id=rid, timeout=timeout)
        except Exception:  # noqa: BLE001 — a dead socket maps to a structured error
            return results.err(errors.DaemonUnreachableError(f"daemon call {method!r} failed"))
        if not isinstance(resp, dict) or resp.get("request_id") not in (rid, None):
            return results.err(errors.DaemonUnreachableError("no matching daemon response"))
        if resp.get("ok") is None and "error" not in resp:
            return results.err(errors.DaemonUnreachableError("malformed daemon response"))
        return resp

    def is_running(self) -> bool:
        return bool(self.call("ping", {}, timeout=1.0).get("ok"))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_daemon_client.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/daemon/client.py tests/test_daemon_client.py
git commit -m "feat: DaemonClient over SocketTransport with daemon_unreachable fallback"
```

---

### Task 8: Frontend auto-routing in `cli.py` (`_dispatch`)

**Files:**
- Modify: `src/phonectl/cli.py`
- Test: `tests/test_cli.py` (append)

**Interfaces:**
- `cli._daemon_client(cfg) -> DaemonClient | None` — `discovery.discover(ping=...)`; build a
  `DaemonClient.from_discovery(info)` when reachable, else `None`. Injectable for tests.
- `cli._dispatch(method, params, in_process_fn, *, cfg=None) -> dict` — if `_daemon_client(cfg)` is
  reachable, return `client.call(method, params)`; **else** call `in_process_fn()` (the existing
  in-process path). Default (no daemon) behavior is byte-for-byte unchanged.
- Wire `_dispatch` into `_cmd_observe` and the action verbs (`_do_action`) so they transparently route
  through the daemon when present. The default `_daemon_client` returns `None` unless a `daemon.json` is
  written and pings `ok`, so every existing test stays green.

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_cli.py
def test_dispatch_in_process_when_no_daemon(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_daemon_client", lambda cfg: None)
    called = {"n": 0}

    def in_proc():
        called["n"] += 1
        return {"ok": True, "data": {"via": "in_process"}}

    out = cli._dispatch("observe", {}, in_proc)
    assert called["n"] == 1
    assert out["data"]["via"] == "in_process"


def test_dispatch_routes_to_daemon_when_reachable(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))

    class FakeClient:
        def call(self, method, params, **kw):
            return {"ok": True, "data": {"via": "daemon", "method": method}}

    monkeypatch.setattr(cli, "_daemon_client", lambda cfg: FakeClient())
    out = cli._dispatch("observe", {}, lambda: {"ok": True, "data": {"via": "in_process"}})
    assert out["data"]["via"] == "daemon"
    assert out["data"]["method"] == "observe"


def test_observe_command_unchanged_without_daemon(tmp_path, monkeypatch, capsys):
    # The existing observe path must be byte-for-byte unchanged when no daemon runs.
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_daemon_client", lambda cfg: None)
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: FakeBackend())
    rc = cli.main(["observe", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["ok"] is True and "hash" in out["data"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli.py -v -k "dispatch or observe_command_unchanged"`
Expected: FAIL (`AttributeError: _dispatch` / `_daemon_client`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/cli.py — additions
from phonectl.daemon import discovery as _daemon_discovery
from phonectl.daemon.client import DaemonClient


def _daemon_client(cfg):
    def _ping(host, port):
        return DaemonClient(host, port).is_running()
    info = _daemon_discovery.discover(ping=_ping)
    if info is None:
        return None
    return DaemonClient.from_discovery(info)


def _dispatch(method, params, in_process_fn, *, cfg=None):
    client = _daemon_client(cfg)
    if client is not None:
        return client.call(method, params)
    return in_process_fn()
```

```python
# src/phonectl/cli.py — _cmd_observe routed through _dispatch
def _cmd_observe(args):
    cfg = config.load()

    def in_process():
        backend, session, conn = build_runtime(cfg)
        conn.ensure()
        snap = observer.observe(backend, session, screenshot=args.screenshot,
                                snap_path=args.screenshot_path, tree=args.tree,
                                relations=args.relations)
        provider = getattr(backend, "last_used", None) or "adb"
        return results.ok(capability="ui.observe", provider=provider, data=snap)

    env = _dispatch("observe", {
        "screenshot": args.screenshot, "snap_path": args.screenshot_path,
        "tree": args.tree, "relations": args.relations,
    }, in_process, cfg=cfg)
    if getattr(args, "json", False):
        print(json.dumps(env, indent=2))
    else:
        _emit(env["data"])
    return 0
```

```python
# src/phonectl/cli.py — _do_action routed through _dispatch (daemon owns the single writer)
def _do_action(args, verb, fn, target):
    cfg = config.load()

    def in_process():
        return runtime.run_action(
            verb, fn, target, build=build_runtime,
            yes=getattr(args, "yes", False),
            cfg=cfg,
            request_id=getattr(args, "request_id", None),
            idempotency_key=getattr(args, "idempotency_key", None),
        )

    # The daemon path serializes the act; in-process keeps run_action's own lock.
    env = _dispatch("act", _act_params(args, verb, target), in_process, cfg=cfg)
    # ... (existing ok/error rendering + exit-code mapping, unchanged) ...
```

(`_act_params` packs `{verb, target, yes, request_id, idempotency_key, ...}` plus the serializable
targeting fields the daemon's `_fn_for` consumes; when no daemon runs, `in_process` ignores it and uses the
original `fn` closure — so default behavior is unchanged. Keep the existing `_do_action` rendering and
exit-code map verbatim.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli.py -v -k "dispatch or observe"`
Expected: PASS (new tests + all existing — `_daemon_client` returns `None` with no `daemon.json`).

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/cli.py tests/test_cli.py
git commit -m "feat: cli._dispatch — frontend auto-routes to a reachable daemon, else in-process"
```

---

### Task 9: `phonectl daemon` command + config keys + docs

**Files:**
- Modify: `src/phonectl/cli.py` (`daemon` command group; the accept loop; SIGINT shutdown)
- Modify: `src/phonectl/config.py` (`daemon_host`/`daemon_autostart` defaults)
- Modify: `src/phonectl/daemon/server.py` (`serve_forever`/`shutdown` lifecycle helpers; injectable socket factory)
- Modify: `README.md`, `docs/superpowers/specs/2026-06-22-phonectl-daemon-event-runtime-design.md`
- Test: `tests/test_config.py` (append), `tests/test_cli.py` (append), `tests/test_daemon_server.py` (append)

**Interfaces:**
- `config` defaults: `daemon_host = "127.0.0.1"`, `daemon_autostart = False`.
- `DaemonServer.bind(*, server_factory=socket-based) -> (host, port)` — bind a loopback TCP server on
  `daemon_host:0` (ephemeral port), publish `daemon.json` via `discovery.write`, store `self._port`.
  `server_factory` is injectable so tests use a fake (no real socket).
- `DaemonServer.serve_forever()` — accept loop calling `handle_line` per newline frame; thin, injectable.
- `DaemonServer.shutdown()` — stop accepting and `discovery.remove()` (idempotent; SIGINT-safe).
- `cli._cmd_daemon(args)` — `start` (foreground: `bind` + `serve_forever`, `SIGINT`→`shutdown`),
  `status` (print `discovery.read()` + a `DaemonClient.status` call when reachable), `stop`
  (`DaemonClient.call("stop_daemon"|"shutdown")` or remove the file when unreachable).
- Real-socket smoke (accept loop end to end) is deferred to an **on-device note**; unit tests drive
  `handle_line` + `bind`/`shutdown` with a fake socket factory.

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_config.py
def test_daemon_config_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    from phonectl import config
    cfg = config.load()
    assert cfg.get("daemon_host", "127.0.0.1") == "127.0.0.1"
    assert cfg.get("daemon_autostart", False) is False


# Append to tests/test_daemon_server.py
def test_bind_publishes_daemon_json_and_shutdown_removes_it(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    from phonectl.daemon import discovery

    class FakeServerSock:
        def __init__(self): self.closed = False
        def getsockname(self): return ("127.0.0.1", 54321)
        def close(self): self.closed = True

    sock = FakeServerSock()
    srv = DaemonServer(config.load(), build=lambda cfg: (object(), object(), None))
    host, port = srv.bind(server_factory=lambda h: sock)
    assert (host, port) == ("127.0.0.1", 54321)
    assert discovery.read()["port"] == 54321
    srv.shutdown()
    assert discovery.read() is None
    assert sock.closed is True


# Append to tests/test_cli.py
def test_daemon_status_reports_not_running(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_daemon_client", lambda cfg: None)
    rc = cli.main(["daemon", "status", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["ok"] is True and out["data"]["running"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_config.py tests/test_daemon_server.py tests/test_cli.py -v -k "daemon"`
Expected: FAIL (`AttributeError: bind`/`shutdown`; `daemon` subcommand missing).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/config.py — extend defaults surface (where defaults live)
    "daemon_host": "127.0.0.1",
    "daemon_autostart": False,
```

```python
# src/phonectl/daemon/server.py — lifecycle helpers
import os
from phonectl.daemon import discovery


class DaemonServer:
    # ... existing ...

    def bind(self, *, server_factory=None):
        if server_factory is None:
            import socket as _socket

            def server_factory(host):
                s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
                s.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
                s.bind((host, 0))   # ephemeral port; loopback only
                s.listen()
                return s

        self._sock = server_factory(self._host)
        self._port = self._sock.getsockname()[1]
        discovery.write({
            "pid": os.getpid(), "host": self._host, "port": self._port,
            "version": PROTOCOL_VERSION, "started_at": self._now(),
        })
        return self._host, self._port

    def serve_forever(self):
        self._running = True
        sel = __import__("selectors").DefaultSelector()
        sel.register(self._sock, __import__("selectors").EVENT_READ)
        try:
            while self._running:
                for key, _ in sel.select(timeout=0.5):
                    conn, _addr = key.fileobj.accept()
                    self._serve_conn(conn)
        finally:
            sel.close()

    def _serve_conn(self, conn):
        f = conn.makefile("rw", encoding="utf-8", newline="\n")
        try:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                f.write(self.handle_line(line) + "\n")
                f.flush()
        finally:
            f.close()
            conn.close()

    def shutdown(self):
        self._running = False
        if getattr(self, "_sock", None) is not None:
            self._sock.close()
            self._sock = None
        discovery.remove()
```

```python
# src/phonectl/cli.py — daemon command group
import signal


def _cmd_daemon(args):
    cfg = config.load()
    sub = getattr(args, "daemon_cmd", None)
    if sub == "start":
        from phonectl.daemon.server import DaemonServer
        srv = DaemonServer(cfg, build=build_runtime)
        host, port = srv.bind()
        signal.signal(signal.SIGINT, lambda *a: srv.shutdown())
        print(f"phonectl daemon listening on {host}:{port} (Ctrl-C to stop)")
        try:
            srv.serve_forever()
        finally:
            srv.shutdown()
        return 0
    if sub == "status":
        client = _daemon_client(cfg)
        data = {"running": client is not None}
        if client is not None:
            st = client.call("status", {})
            data.update(st.get("data", {}))
        env = results.ok(capability="daemon.status", data=data)
        print(json.dumps(env, indent=2) if getattr(args, "json", False)
              else f"daemon running={data['running']}")
        return 0
    if sub == "stop":
        client = _daemon_client(cfg)
        if client is None:
            _daemon_discovery.remove()
            print("phonectl: no running daemon")
            return 0
        client.call("stop", {})       # sets STOP; full shutdown verb is a Plan 5.2 seam
        print("phonectl: stop signalled")
        return 0
    print("usage: phonectl daemon {start|status|stop}")
    return 1
```

Register a `daemon` subparser group (`start`/`status`/`stop`, each with `--json` where relevant) wired to
`_cmd_daemon`. In `README.md` add a **Daemon** section (what the daemon is, `phonectl daemon start`,
loopback-only, `daemon.json` discovery, that frontends auto-route, that no daemon is required for
primitives, `daemon_host`/`daemon_autostart` config, and `runs.jsonl`). In the 5.0 design spec, note that
5.1 ships the process + RPC + single-writer + run records + routing, and that the snapshot cache + event
bus are 5.2. Note the **Termux:Boot / foreground-service hosting seam** (daemon runs foreground for now).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_config.py tests/test_daemon_server.py tests/test_cli.py -v -k "daemon"`
Expected: PASS.

- [ ] **Step 5: Run full suite**

Run: `pytest -v`
Expected: PASS (all prior tests unchanged — no daemon runs in the suite, so every frontend stays
in-process).

- [ ] **Step 6: Commit**

```bash
git add src/phonectl/cli.py src/phonectl/config.py src/phonectl/daemon/server.py README.md docs/superpowers/specs/2026-06-22-phonectl-daemon-event-runtime-design.md tests/test_config.py tests/test_cli.py tests/test_daemon_server.py
git commit -m "feat: phonectl daemon start/status/stop + daemon_host/daemon_autostart config + docs"
```

---

## Dependencies

**Requires:** Plan 2.1 (`runtime.run_action` single-writer funnel — reused verbatim inside the daemon),
Plan 3.1 (`ProviderRegistry` + `cli.build_runtime` — built once and kept warm), Plan 4.3
(`providers/transport.py`: `SocketTransport` + `next_request_id` + the loopback newline-JSON framing).
Opportunistic on Plan 1.3 (`conn.reconnect()` on connection loss, gated via `hasattr`) and Plan 2.2
(`policy.explain` for the `policy_explain` RPC).
**Enables:** Plan 5.2 (snapshot cache + event bus + subscriptions land on this server's warm runtime and
`runs.jsonl`), and Phase 6 macros (the daemon becomes the macro runtime's single writer + run-record sink).

## Deferred / out of scope

- **Snapshot cache + invalidation + monotonic snapshot IDs** — Plan 5.2. Until then, run records'
  `snapshot_before` is `None` and `snapshot_after` is the post-act snapshot from `run_action`.
- **Event bus / fanout / subscriptions** — Plan 5.2.
- **Termux:Boot autostart + companion foreground-service hosting** — noted as a **seam only**; `phonectl
  daemon` runs foreground for now. (`daemon_autostart` config exists but is not yet wired to a launcher.)
- **A clean `shutdown_daemon` RPC** — `daemon stop` currently signals via the `stop` (STOP-sentinel) verb;
  a dedicated process-shutdown RPC + watchdog is a 5.2 seam.
- **Auth / token on the socket** — loopback-only + Android app sandboxing is the trust boundary, matching
  Plan 4.3; an auth token lands if/when multi-client external access is needed (Phase 7).
- **Real-socket / multi-process accept-loop smoke** — unit tests drive `handle_line` + `bind`/`shutdown`
  with fakes; the live accept loop is validated by an on-device note (it is ROM/topology-specific).
- **MCP frontend routing** — the MCP server (Plan 2.3) gains daemon routing by reusing `cli._dispatch`;
  wiring it is a thin follow-up noted for 5.2, kept out of this plan to preserve the 2.3 contract.

## Notes on testability

No real socket or daemon process is needed. `DaemonServer.handle_line(line) -> line` is driven
synchronously, so every RPC (ping/status/observe/find/capabilities/policy_explain/audit_query/stop/resume/
act) is tested as a pure request→response string round-trip. `build=` is injected to return a warm triple
built from a `ProviderRegistry([FakeBackend()])`, proving the build-once / single-writer-lock behavior
without ADB. `records.build_record` is a **pure** function (clock injected) tested independently of the
append. `DaemonClient` is tested with a fake transport (echoing scripted envelopes), covering the match,
the `daemon_unreachable` fallback, and `is_running`. `cli._dispatch` is tested both branches by patching
`_daemon_client` — and the default (no `daemon.json`) keeps every existing CLI test in-process and
unchanged, satisfying the compatible-evolution invariant. `bind`/`shutdown` use an injectable
`server_factory` (a fake server socket), so `daemon.json` publish/remove is verified with no kernel socket.
All state is isolated via `monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))`. The live accept loop is the
only piece left to an on-device smoke.
