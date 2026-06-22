# phonectl Foreground-Service Transport + Emergency-Stop + Trust UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Plan 4.3 of the platform roadmap** (`docs/superpowers/phonectl-platform-roadmap.md`). Third plan of
Phase 4. Depends on **Plan 2.1** (kill-switch / `stop`/`resume` + `runtime`), and **Plan 4.1** (the
`Transport` seam + `AccessibilityProvider`/`_make_accessibility_provider`). Optional consumer: **Plan 4.2**
(notifications) — its provider activates once this plan supplies a reachable transport.

**Goal:** Give the companion APK a **persistent, low-latency transport** and the **trust + emergency-stop
UX** Accessibility demands (strategy §8.4, §11.3, §11.4). Concretely on the Python side: (1) a
`SocketTransport` (localhost TCP) implementing the Plan 4.1 `Transport` Protocol, with `request_id`,
`timeout`, protocol `version`, capability negotiation, and stale-response protection; (2) a **per-capability
toggle** surface so the companion's user-controlled grants intersect each provider's advertised
`capabilities()`; (3) **emergency-stop integration** so the companion's "Stop phonectl" control folds into
the existing `$PHONECTL_HOME/STOP` kill-switch and `phonectl stop`/`resume`. On the Android side: a design
spec for the foreground service, the persistent "Stop phonectl" notification, the Quick-Settings tile, and
the per-capability toggle UI. The Kotlin APK is built separately from that spec.

**Architecture:** `src/phonectl/providers/transport.py` (created in 4.1) gains `SocketTransport`
(stdlib `socket` + newline-delimited JSON; injectable `connect` factory for tests). A new pure-ish module
`src/phonectl/trust.py` holds the toggle/handshake model: `negotiate(transport) -> Handshake` (version +
enabled capability set + companion stop flag) and `gate_capabilities(caps, enabled) -> caps` (intersect a
provider's advertised caps with the user-enabled set). `cli._make_accessibility_provider()` (the `None`
stub from 4.1) is upgraded to build a `SocketTransport` from config (`companion_host`/`companion_port`)
when configured, wrapping the provider so it is only active when the socket answers `ping`. The companion's
stop flag is consulted by `audit.kill_switch_active()` via an injected `extra` check, so a single
"Stop phonectl" tap halts CLI, MCP, and (later) the daemon uniformly.

**Tech Stack:** Python 3 (stdlib only: `socket`, `json`, `time`, `typing`); `pytest` for tests; no new
runtime deps. The foreground service / tile are Android (Kotlin) — **design-spec only** here.

## Global Constraints

- **stdlib-only at runtime.** `SocketTransport` uses `socket` + `json`. No third-party deps.
- **Backend isolation.** The transport speaks only the companion's JSON protocol over localhost. It does
  not call `adb`/`subprocess`. Providers remain the only callers of the transport.
- **`ui_parser.py` stays pure** and untouched.
- **Local-only.** The socket binds/connects to **loopback** (`127.0.0.1`) exclusively. No external host is
  ever accepted in config; reject non-loopback hosts with a clear error (strategy §11.4 "local-only
  guarantee, no network by default").
- **Kill-switch precedence.** The companion stop flag is **additive** to the existing `STOP` sentinel
  file: if **either** is active, actions are blocked. Never weaken the file kill-switch.
- **Injectable seams.** `SocketTransport(host, port, *, connect=...)`; `trust.negotiate(transport)`;
  `audit.kill_switch_active(extra_checks=...)`. Tests inject a fake connection/transport. Isolate state via
  `monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))`.
- **Structured-result invariant (Plan 1.1):** CLI `--json` and MCP return `results.ok/err`.
- **One commit per task. TDD order is non-negotiable.**

## Shared conventions established by this plan

- **Config keys added:** `companion_host` (default `"127.0.0.1"`), `companion_port` (default `null` —
  unset means no companion), `companion_timeout` (default `2.0`).
- **Wire framing:** newline-delimited JSON. One request object per line; one response object per line.
  Request: `{"method", "params", "request_id", "timeout", "version"}`. Response: the Plan 4.1 envelope
  (`{"ok", "request_id", "version", "data"/"error"}`).
- **Handshake** (`trust.negotiate`): sends `method="handshake"`; the companion returns
  `{"version", "capabilities": {cap: bool}, "stopped": bool}`. `capabilities` here is the **user-enabled
  toggle set** (what the user has granted in the APK), which the Python side intersects with each provider's
  technically-supported caps.
- **Emergency stop:** `trust.companion_stopped(transport) -> bool` is registered as an `extra_check` in
  `audit.kill_switch_active`. A `True` result blocks like the `STOP` file.

---

### Task 1: `SocketTransport` — localhost request/response with stale-response protection

**Files:**
- Modify: `src/phonectl/providers/transport.py`
- Test: `tests/test_transport.py` (append)

**Interfaces:**
- `SocketTransport(host: str, port: int, *, version: int = 1, connect=None)` — `connect` is a factory
  `(host, port, timeout) -> conn` where `conn` exposes `sendline(str)` / `readline() -> str` / `close()`.
  Default `connect` wraps `socket.create_connection` with a newline-buffered file adapter. Rejects any
  non-loopback `host` with `ValueError`.
- `request(method, params, *, request_id, timeout) -> dict` — writes one JSON line, reads response lines
  until one matches `request_id` (dropping stale/mismatched lines) or the deadline passes; on timeout
  returns `{"ok": False, "error": {"code": "timeout", ...}, "request_id": request_id}`.
- `ping(*, timeout=1.0) -> bool` — sends `method="ping"`; `True` iff a matching `ok` response arrives.

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_transport.py
from phonectl.providers.transport import SocketTransport


class FakeConn:
    """Scriptable newline conn: each request line yields the queued response line(s)."""
    def __init__(self, script):
        self._script = list(script)   # list of response-line strings to return in order
        self.sent = []
        self.closed = False

    def sendline(self, s):
        self.sent.append(s)

    def readline(self):
        return self._script.pop(0) if self._script else ""

    def close(self):
        self.closed = True


def _conn_factory(script):
    def factory(host, port, timeout):
        return FakeConn(script)
    return factory


def test_socket_transport_rejects_non_loopback():
    with pytest.raises(ValueError):
        SocketTransport("10.0.0.5", 8765)


def test_socket_transport_matches_request_id(monkeypatch):
    import json
    rid = "fixedid"
    monkeypatch.setattr("phonectl.providers.transport.next_request_id", lambda: rid)
    resp_line = json.dumps({"ok": True, "request_id": rid, "version": 1, "data": {"pong": True}})
    t = SocketTransport("127.0.0.1", 8765, connect=_conn_factory([resp_line + "\n"]))
    out = t.request("ping", {}, request_id=rid, timeout=1.0)
    assert out["ok"] is True and out["data"]["pong"] is True


def test_socket_transport_drops_stale_then_matches(monkeypatch):
    import json
    rid = "want"
    stale = json.dumps({"ok": True, "request_id": "other", "version": 1, "data": {}}) + "\n"
    good = json.dumps({"ok": True, "request_id": rid, "version": 1, "data": {"v": 1}}) + "\n"
    t = SocketTransport("127.0.0.1", 8765, connect=_conn_factory([stale, good]))
    out = t.request("m", {}, request_id=rid, timeout=1.0)
    assert out["data"]["v"] == 1


def test_socket_transport_ping_true_on_ok():
    import json
    def factory(host, port, timeout):
        # echo a matching ok for whatever id is sent
        class C(FakeConn):
            def readline(self):
                last = self.sent[-1]
                rid = json.loads(last)["request_id"]
                return json.dumps({"ok": True, "request_id": rid, "version": 1, "data": {}}) + "\n"
        return C([])
    t = SocketTransport("127.0.0.1", 8765, connect=factory)
    assert t.ping() is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_transport.py -v -k "socket"`
Expected: FAIL (`ImportError`/`AttributeError: SocketTransport`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/providers/transport.py — append

import json as _json
import socket as _socket
import time as _time

_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


class _SocketConn:
    def __init__(self, host, port, timeout):
        self._sock = _socket.create_connection((host, port), timeout=timeout)
        self._sock.settimeout(timeout)
        self._f = self._sock.makefile("rw", encoding="utf-8", newline="\n")

    def sendline(self, s):
        self._f.write(s + "\n")
        self._f.flush()

    def readline(self):
        return self._f.readline()

    def close(self):
        try:
            self._f.close()
        finally:
            self._sock.close()


class SocketTransport:
    def __init__(self, host, port, *, version=1, connect=None):
        if host not in _LOOPBACK:
            raise ValueError(f"companion transport is loopback-only; refusing host {host!r}")
        self._host, self._port, self._version = host, port, version
        self._connect = connect or (lambda h, p, t: _SocketConn(h, p, t))

    def request(self, method, params, *, request_id, timeout):
        line = _json.dumps({"method": method, "params": params or {},
                            "request_id": request_id, "timeout": timeout,
                            "version": self._version})
        conn = self._connect(self._host, self._port, timeout)
        deadline = _time.monotonic() + timeout
        try:
            conn.sendline(line)
            while _time.monotonic() < deadline:
                raw = conn.readline()
                if not raw:
                    break
                try:
                    resp = _json.loads(raw)
                except _json.JSONDecodeError:
                    continue
                if resp.get("request_id") == request_id:  # stale-response protection
                    return resp
            return {"ok": False, "request_id": request_id,
                    "error": {"code": "timeout", "message": f"no response for {method!r}"}}
        finally:
            conn.close()

    def ping(self, *, timeout=1.0):
        rid = next_request_id()
        resp = self.request("ping", {}, request_id=rid, timeout=timeout)
        return bool(resp.get("ok"))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_transport.py -v -k "socket"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/providers/transport.py tests/test_transport.py
git commit -m "feat: SocketTransport — loopback-only newline-JSON transport with stale-response protection"
```

---

### Task 2: `trust.negotiate` handshake (version + capabilities + stop flag)

**Files:**
- Create: `src/phonectl/trust.py`
- Test: `tests/test_trust.py`

**Interfaces:**
- `trust.Handshake` — dataclass `{version: int, capabilities: dict, stopped: bool, reachable: bool}`.
- `trust.negotiate(transport, *, timeout=2.0) -> Handshake` — sends `method="handshake"`; on success
  returns the populated `Handshake`; on transport failure returns `Handshake(version=0, capabilities={},
  stopped=False, reachable=False)`.
- `trust.companion_stopped(transport, *, timeout=1.0) -> bool` — convenience: `negotiate(...).stopped`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_trust.py
from phonectl import trust
from phonectl.providers.transport import LoopbackTransport


def test_negotiate_returns_capabilities_and_version():
    t = LoopbackTransport({"handshake": lambda p: {
        "version": 2, "capabilities": {"act_gesture_native": True, "act_set_text_native": False},
        "stopped": False}})
    hs = trust.negotiate(t)
    assert hs.reachable is True
    assert hs.version == 2
    assert hs.capabilities["act_gesture_native"] is True
    assert hs.stopped is False


def test_negotiate_unreachable_is_safe_default():
    t = LoopbackTransport({}, available=False)
    # ping=False, but negotiate still tries the call; handler missing -> error envelope
    hs = trust.negotiate(t)
    assert hs.reachable is False
    assert hs.capabilities == {}


def test_companion_stopped_true():
    t = LoopbackTransport({"handshake": lambda p: {
        "version": 1, "capabilities": {}, "stopped": True}})
    assert trust.companion_stopped(t) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_trust.py -v`
Expected: FAIL (`ModuleNotFoundError: phonectl.trust`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/trust.py
"""Companion trust model — handshake, per-capability toggles, emergency-stop flag."""
from __future__ import annotations

from dataclasses import dataclass, field

from phonectl.providers.transport import next_request_id


@dataclass
class Handshake:
    version: int = 0
    capabilities: dict = field(default_factory=dict)
    stopped: bool = False
    reachable: bool = False


def negotiate(transport, *, timeout: float = 2.0) -> Handshake:
    rid = next_request_id()
    try:
        resp = transport.request("handshake", {}, request_id=rid, timeout=timeout)
    except Exception:  # noqa: BLE001
        return Handshake()
    if resp.get("request_id") != rid or not resp.get("ok"):
        return Handshake()
    data = resp.get("data", {})
    return Handshake(
        version=int(data.get("version", 0)),
        capabilities=dict(data.get("capabilities", {})),
        stopped=bool(data.get("stopped", False)),
        reachable=True,
    )


def companion_stopped(transport, *, timeout: float = 1.0) -> bool:
    return negotiate(transport, timeout=timeout).stopped
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_trust.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/trust.py tests/test_trust.py
git commit -m "feat: trust.negotiate handshake (version + capability toggles + stop flag)"
```

---

### Task 3: `trust.gate_capabilities` — intersect advertised caps with user toggles

**Files:**
- Modify: `src/phonectl/trust.py`
- Test: `tests/test_trust.py` (append)

**Interfaces:**
- `trust.gate_capabilities(advertised: dict, enabled: dict) -> dict` — returns a caps dict where a key is
  `True` only if **both** `advertised[key]` and `enabled.get(key, True)` are truthy. A key absent from
  `enabled` defaults to **enabled** (the toggle set only ever *removes* grants; it never invents caps the
  provider cannot do). Pure function.
- A thin wrapper provider `trust.GatedProvider(inner, enabled)` whose `capabilities()` returns
  `gate_capabilities(inner.capabilities(), enabled)` and which delegates everything else to `inner` via
  `__getattr__`. This lets `build_runtime` wrap `AccessibilityProvider`/`NotificationsProvider` so a
  user-disabled capability disappears from the registry (and the registry transparently falls back to ADB
  or reports `CapabilityUnavailableError`).

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_trust.py
from phonectl import capabilities


def test_gate_capabilities_removes_disabled():
    adv = capabilities.make(act_gesture_native=True, act_set_text_native=True)
    enabled = {"act_set_text_native": False}
    gated = trust.gate_capabilities(adv, enabled)
    assert gated["act_gesture_native"] is True
    assert gated["act_set_text_native"] is False


def test_gate_capabilities_absent_toggle_defaults_enabled():
    adv = capabilities.make(act_gesture_native=True)
    assert trust.gate_capabilities(adv, {})["act_gesture_native"] is True


def test_gated_provider_filters_and_delegates():
    class Inner:
        def capabilities(self):
            return capabilities.make(act_gesture_native=True, act_set_text_native=True)
        def semantic_action(self, *a):
            return {"performed": a}
    g = trust.GatedProvider(Inner(), {"act_set_text_native": False})
    assert g.capabilities()["act_set_text_native"] is False
    assert g.capabilities()["act_gesture_native"] is True
    assert g.semantic_action("n", "click")["performed"] == ("n", "click")  # delegated
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_trust.py -v -k "gate or gated"`
Expected: FAIL (`AttributeError`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/trust.py — append

def gate_capabilities(advertised: dict, enabled: dict) -> dict:
    return {k: bool(v) and bool(enabled.get(k, True)) for k, v in advertised.items()}


class GatedProvider:
    def __init__(self, inner, enabled: dict) -> None:
        self._inner = inner
        self._enabled = dict(enabled)

    def capabilities(self) -> dict:
        return gate_capabilities(self._inner.capabilities(), self._enabled)

    def __getattr__(self, name):
        return getattr(self._inner, name)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_trust.py -v -k "gate or gated"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/trust.py tests/test_trust.py
git commit -m "feat: trust.gate_capabilities + GatedProvider — per-capability user toggles"
```

---

### Task 4: Emergency-stop integration into the kill-switch

**Files:**
- Modify: `src/phonectl/audit.py` (`kill_switch_active` accepts `extra_checks`)
- Modify: `src/phonectl/runtime.py` (pass the companion stop check when a transport is configured)
- Test: `tests/test_audit.py` (append), `tests/test_runtime.py` (append)

**Interfaces:**
- `audit.kill_switch_active(*, extra_checks=()) -> bool` — returns `True` if the `STOP` sentinel exists
  **or** any callable in `extra_checks` returns `True`. Backward-compatible: default `extra_checks=()`
  preserves today's behavior exactly. Exceptions raised by an extra check are swallowed and treated as
  **not** stopping (the file sentinel remains the hard guarantee; a flaky socket must not wedge the CLI).
- `runtime.run_action(...)` builds `extra_checks` from the active companion transport: when a transport is
  present, it adds `lambda: trust.companion_stopped(transport)`. When absent, no extra check (unchanged).

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_audit.py
def test_kill_switch_extra_check_blocks(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    from phonectl import audit
    assert audit.kill_switch_active() is False
    assert audit.kill_switch_active(extra_checks=[lambda: True]) is True


def test_kill_switch_extra_check_exception_is_safe(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    from phonectl import audit
    def boom():
        raise RuntimeError("socket down")
    # file sentinel absent + flaky check -> not stopped (does not wedge)
    assert audit.kill_switch_active(extra_checks=[boom]) is False


# Append to tests/test_runtime.py
def test_run_action_blocked_by_companion_stop(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    from phonectl import config, runtime
    from phonectl.providers.transport import LoopbackTransport
    from phonectl.providers.registry import ProviderRegistry
    from tests.test_cli import FakeBackend

    stop_transport = LoopbackTransport({"handshake": lambda p: {
        "version": 1, "capabilities": {}, "stopped": True}})
    registry = ProviderRegistry([FakeBackend()])

    def build(cfg):
        from phonectl.session import Session
        from phonectl.connection import Connection
        sess = Session(); conn = Connection(registry, cfg); conn.ensure = lambda: None
        return registry, sess, conn

    cfg = config.load()
    env = runtime.run_action("tap", lambda b, s: {}, "i=0", build=build, yes=True,
                             cfg=cfg, companion_transport=stop_transport)
    assert env["ok"] is False
    assert env["error"]["code"] in ("stopped", "kill_switch_active")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_audit.py tests/test_runtime.py -v -k "extra_check or companion_stop"`
Expected: FAIL (`TypeError: unexpected keyword 'extra_checks'` / `companion_transport`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/audit.py
def kill_switch_active(*, extra_checks=()) -> bool:
    if _stop_file_path().exists():
        return True
    for check in extra_checks:
        try:
            if check():
                return True
        except Exception:  # noqa: BLE001 — flaky companion must not wedge the CLI
            continue
    return False
```

```python
# src/phonectl/runtime.py — in run_action signature + the kill-switch gate
def run_action(verb, fn, target, *, yes, cfg, build=..., companion_transport=None):
    ...
    extra = []
    if companion_transport is not None:
        from phonectl import trust
        extra.append(lambda: trust.companion_stopped(companion_transport))
    if audit.kill_switch_active(extra_checks=extra):
        return results.err(errors.StoppedError("emergency stop active"),
                           capability=f"ui.{verb}", ...)
    ...
```

(Keep the existing `errors.StoppedError`/`stopped` code from Plan 2.1; only the `extra_checks` plumbing is
new. If 2.1 named the code differently, reuse that name — do not introduce a second stop code.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_audit.py tests/test_runtime.py -v`
Expected: PASS (new tests + all existing — default call sites pass no `extra_checks`/`companion_transport`).

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/audit.py src/phonectl/runtime.py tests/test_audit.py tests/test_runtime.py
git commit -m "feat: companion emergency-stop folds into kill_switch_active via extra_checks"
```

---

### Task 5: Config + `build_runtime` transport wiring + `phonectl trust status`

**Files:**
- Modify: `src/phonectl/config.py` (defaults for `companion_host`/`companion_port`/`companion_timeout`)
- Modify: `src/phonectl/cli.py` (`_make_companion_transport`, upgrade `_make_accessibility_provider`,
  `_cmd_trust_status`)
- Test: `tests/test_config.py` (append), `tests/test_cli.py` (append)

**Interfaces:**
- `config` default `companion_port = None`. `_make_companion_transport(cfg) -> SocketTransport | None` —
  returns a `SocketTransport(cfg["companion_host"], cfg["companion_port"])` when `companion_port` is set,
  else `None`.
- `_make_accessibility_provider()` (the 4.1 `None` stub) now: build the transport; if present and `ping`
  succeeds, negotiate toggles and return `trust.GatedProvider(AccessibilityProvider(transport),
  handshake.capabilities)`; else `None`. The companion transport is also threaded into `run_action` calls
  (so emergency stop applies) and into `_make_notifications_provider` (Plan 4.2).
- `phonectl trust status [--json]` — prints the handshake: reachable, version, per-capability toggle map,
  and whether the emergency stop is currently engaged. Read-only.

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_config.py
def test_companion_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    from phonectl import config
    cfg = config.load()
    assert cfg.get("companion_host", "127.0.0.1") == "127.0.0.1"
    assert cfg.get("companion_port") is None


# Append to tests/test_cli.py
def test_trust_status_reports_unreachable_without_companion(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: FakeBackend())
    monkeypatch.setattr(cli, "_make_companion_transport", lambda cfg: None)
    rc = cli.main(["trust", "status", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["ok"] is True
    assert out["data"]["reachable"] is False


def test_trust_status_reports_toggles(tmp_path, monkeypatch, capsys):
    from phonectl.providers.transport import LoopbackTransport
    t = LoopbackTransport({"handshake": lambda p: {
        "version": 3, "capabilities": {"act_gesture_native": True, "act_set_text_native": False},
        "stopped": False}})
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: FakeBackend())
    monkeypatch.setattr(cli, "_make_companion_transport", lambda cfg: t)
    rc = cli.main(["trust", "status", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert out["data"]["version"] == 3
    assert out["data"]["capabilities"]["act_set_text_native"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_config.py tests/test_cli.py -v -k "companion or trust_status"`
Expected: FAIL (`AttributeError: _make_companion_transport`; `trust` subcommand missing).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/config.py — extend DEFAULTS
    "companion_host": "127.0.0.1",
    "companion_port": None,
    "companion_timeout": 2.0,
```

```python
# src/phonectl/cli.py
from phonectl.providers.transport import SocketTransport
from phonectl import trust
from phonectl.providers.accessibility import AccessibilityProvider


def _make_companion_transport(cfg):
    port = cfg.get("companion_port")
    if not port:
        return None
    return SocketTransport(cfg.get("companion_host", "127.0.0.1"), int(port))


def _make_accessibility_provider():
    cfg = config.load()
    transport = _make_companion_transport(cfg)
    if transport is None or not transport.ping():
        return None
    hs = trust.negotiate(transport)
    return trust.GatedProvider(AccessibilityProvider(transport), hs.capabilities)


def _cmd_trust_status(args):
    cfg = config.load()
    transport = _make_companion_transport(cfg)
    if transport is None:
        data = {"reachable": False, "version": 0, "capabilities": {}, "stopped": False}
    else:
        hs = trust.negotiate(transport)
        data = {"reachable": hs.reachable, "version": hs.version,
                "capabilities": hs.capabilities, "stopped": hs.stopped}
    env = results.ok(capability="trust.status", data=data)
    if getattr(args, "json", False):
        print(json.dumps(env, indent=2))
    else:
        print(f"companion: reachable={data['reachable']} version={data['version']} "
              f"stopped={data['stopped']}")
    return 0
```

Register a `trust` subcommand group with `status`. (The 4.2 `_make_notifications_provider` should now pass
`transport=_make_companion_transport(config.load())` instead of `None`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_config.py tests/test_cli.py -v`
Expected: PASS (new tests + all existing — `companion_port` defaults to `None`, so builds stay ADB-first).

- [ ] **Step 5: Run full suite**

Run: `pytest -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/phonectl/config.py src/phonectl/cli.py tests/test_config.py tests/test_cli.py
git commit -m "feat: companion transport config + gated AccessibilityProvider + phonectl trust status"
```

---

### Task 6: Android foreground-service + trust-UX design spec

**Files:**
- Create: `android/foreground-service/SPEC.md`
- Modify: `android/accessibility-companion/SPEC.md` (cross-link the transport spec)

**Interfaces:** none (documentation only).

- [ ] **Step 1: Write the design spec**

Create `android/foreground-service/SPEC.md` (design only; input to the dedicated Kotlin build):
- **Foreground service** hosting a **loopback TCP server** speaking the newline-JSON protocol (framing,
  request/response envelope, `request_id`/`timeout`/`version`, capability negotiation, stale-response
  protection). Bind `127.0.0.1` only; never `0.0.0.0`.
- **Persistent "Stop phonectl" notification** (ongoing, non-dismissable while active) whose action sets the
  companion `stopped` flag returned by `handshake` — mirroring the `$PHONECTL_HOME/STOP` sentinel so a
  single tap halts CLI/MCP/daemon.
- **Quick-Settings tile** toggling the whole automation surface on/off (strategy §8.4).
- **Per-capability toggle UI** — the user enables/disables each capability (`observe_ui_native`,
  `act_gesture_native`, `act_set_text_native`, `notifications_reply`, …); the enabled set is returned by
  `handshake.capabilities` and intersected by `trust.gate_capabilities`.
- **Trust guarantees** (strategy §11.4): explain what is read/controlled, local-only, no network by
  default, audit visibility, password/payment-screen warnings, guarded-app behavior.
- **Lifecycle:** Termux:Boot autostart hand-off is a Phase 5 daemon concern; note the seam.

- [ ] **Step 2: Commit**

```bash
git add android/foreground-service/SPEC.md android/accessibility-companion/SPEC.md
git commit -m "docs: foreground-service transport + emergency-stop + trust-UX Android design spec"
```

---

### Task 7: Docs

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-06-20-phonectl-adb-bridge-design.md`

- [ ] **Step 1: Update docs**

In `README.md`, add a **Companion transport & trust controls** section: the `companion_host`/
`companion_port` config, that the transport is loopback-only, `phonectl trust status`, how the
"Stop phonectl" control and Quick-Settings tile map to the kill-switch, and how per-capability toggles
remove grants from the provider graph. In the design spec, record the emergency-stop precedence rule
(file sentinel OR companion stop) and the loopback-only constraint.

Run the full suite before committing:

```bash
pytest -v
```

- [ ] **Step 2: Commit**

```bash
git add README.md docs/superpowers/specs/2026-06-20-phonectl-adb-bridge-design.md
git commit -m "docs: companion transport, trust status, emergency-stop precedence, per-capability toggles"
```

---

## Dependencies

**Requires:** Plan 2.1 (kill-switch / `stop`/`resume` / `runtime.run_action`), Plan 4.1 (`Transport`
Protocol, `AccessibilityProvider`, `_make_accessibility_provider` stub to upgrade).
**Enables:** Plan 4.2's `NotificationsProvider` (now gets a live transport), and Phase 5 (the daemon owns
the persistent connection lifecycle, Termux:Boot autostart, and watchdog; the loopback server becomes the
daemon's IPC surface).

## Deferred / out of scope

- **The Kotlin foreground service / tile / toggle UI** — design spec only here.
- **TLS / auth on the socket** — loopback-only + Android app sandboxing is the trust boundary for now; an
  auth token can be added when multi-client access (daemon + external clients) lands in Phase 5.
- **WebSocket / AIDL / ContentProvider transports** — `Transport` is pluggable; alternatives are Phase 7
  if a use case needs them. Newline-JSON over loopback TCP is the baseline.
- **Daemon lifecycle / autostart / watchdog** — Phase 5 (`brainstorm → spec` required first).
- **Physical-button emergency gesture** (strategy §8.4) — Android-side, deferred to the APK build.

## Notes on testability

No real socket or device is needed: `SocketTransport` takes an injectable `connect` factory, so a
`FakeConn` scripts response lines (including stale lines, to prove the `request_id` filter). `trust`
functions are pure or transport-driven via `LoopbackTransport`. The kill-switch integration is tested at
both `audit` (extra-check semantics, including the flaky-check-is-safe rule) and `runtime` (a companion
that reports `stopped=True` blocks an action) layers. `phonectl trust status` is tested by patching
`_make_companion_transport` to inject a scripted handshake. The default config (`companion_port=None`)
keeps every existing test ADB-first and unchanged.
