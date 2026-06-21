# phonectl Structured-Result MCP Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Plan 2.3 of the platform roadmap** (`docs/superpowers/phonectl-platform-roadmap.md`). Third plan of Phase
2. Depends on **Plan 1.1** (`results`/`errors`/`capabilities`), **Plan 1.2** (selectors + tree/relations),
**Plan 2.1** (`runtime.run_action` funnel), and **Plan 2.2** (`policy.explain`). It **supersedes
`2026-06-21-phonectl-mcp-server.md`**, re-targeting that plan's handler/registry/transport design onto the
**Plan-1 result envelope** instead of CLI tuples, and adding selector-aware + dry-run + expected-hash tool
args plus capability/policy/audit/stop tools (strategy §10, §20, §21).

**Goal:** Expose phonectl as native agent tools over stdio MCP. Every tool returns the **structured result
envelope** (never a CLI return code or a raw traceback); action tools route through `runtime.run_action` (so
the single-writer/audit/policy/rate gates apply identically to MCP and CLI); observation tools accept
selectors and emit tree/relations; and there are first-class `phone_capabilities`, `phone_find`,
`phone_policy_explain`, `phone_audit_query`, `phone_stop`/`phone_resume` tools so an autonomous agent can
discover capabilities, understand *why* an action is blocked, and trigger the emergency stop.

**Architecture:** A new module `mcp_server.py` with three layers, cleanly separated so the **handlers are
unit-testable without the MCP SDK**: (1) **handlers** — plain Python functions `(build, **args) -> dict`
returning envelopes, composing the already-shipped `observer`/`actuator`/`runtime`/`policy`/`capabilities`/
`audit` seams; (2) a **tool registry** `TOOLS` mapping stable tool names to `{schema, handler}` plus a pure
`call_tool(name, args, build)` dispatcher; (3) a **gated transport adapter** `serve()` that lazily imports
the optional `mcp` SDK (FastMCP) and registers each `TOOLS` entry, raising a clean
`CapabilityUnavailableError` with an install hint when the SDK is absent. The MCP SDK is an **optional extra**
(`pip install phonectl[mcp]`) — the stdlib-only runtime invariant holds; the SDK is never imported at module
load, only inside `serve()`. `cli.py` gains `phonectl mcp` to launch the server.

**Tech Stack:** Python 3 (stdlib only for handlers/registry/tests: `json`, `typing`); the optional `mcp`
SDK (FastMCP) only for the live transport; `pytest` for tests; `adb` remains the only external *runtime*
dependency for actually driving a phone.

## Global Constraints

- **stdlib-only at runtime** (Python ≥ 3.9; `pytest` dev-only). The `mcp` SDK is an **optional extra**,
  imported lazily inside `serve()` — never at module top, never by the handlers or the registry.
- **ONLY `adb_backend.py` may touch adb/subprocess.** `mcp_server` composes `observer`/`actuator`/`runtime`/
  `policy`/`capabilities`/`audit` and the injected `build`; it never calls `adb`.
- **`ui_parser.py` stays pure** (untouched).
- **Element index / selector / `(x,y)`** targeting — every action tool accepts all three plus `expected_hash`
  for stale-snapshot protection.
- **Every actuator `act()` re-observes** — unchanged; action tools return the post-action snapshot inside the
  envelope's `data`.
- **Modes + kill-switch + risk policy + rate limits gate every mutating tool** — because action tools call
  `runtime.run_action`, not `actuator` directly. No safety logic is re-implemented in the MCP layer.
- **Structured-result invariant (Plan 1.1):** EVERY tool returns a `results.ok/err` envelope. The transport
  adapter serializes the envelope as the tool result; it never raises a Python traceback to the MCP client.
- **Injectable seams** — handlers take `build` (default `cli.build_runtime`); tests pass a fake build and a
  fake transport. Isolate via `monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))`.
- **One commit per task. TDD order is non-negotiable.**

## Shared conventions established by this plan

- **Tool names are stable, namespaced strings** (they are the agent-facing contract — do not rename once
  shipped): `phone_observe_ui`, `phone_find`, `phone_capabilities`, `phone_tap`, `phone_type`, `phone_swipe`,
  `phone_key`, `phone_launch`, `phone_policy_explain`, `phone_audit_query`, `phone_stop`, `phone_resume`.
  (`phone_reconnect`/`phone_diagnostics` are registered **only when** the backend/diagnostics seams exist,
  gated via `hasattr` — opportunistic on Plan 1.3/1.4.)
- **Handler signature:** `handler(build, **args) -> dict` (an envelope). Handlers never raise for expected
  failures; they catch `errors.PhonectlError` into `results.err(...)` (the read-only ones; action handlers
  inherit this from `run_action`).
- **Action tool args** (uniform across `phone_tap/type/swipe/key/launch` where applicable): `index`,
  `selector` (object), `x`/`y`, `text` (type), `expected_hash`, `stale_ok`, `dry_run` (bool), `confirm`
  (bool — the agent asserting user approval, maps to `run_action(yes=...)`), `reason` (human string, audited),
  `idempotency_key`. `dry_run=True` is implemented as a per-call `cfg` override (`mode="dry-run"`) so no
  `run_action` change is needed.
- **`call_tool(name, args, build) -> dict`** is the pure dispatcher used by both tests and the transport; an
  unknown name returns `results.err(("unknown_tool", ...))`.
- **`pyproject.toml`** gains `[project.optional-dependencies] mcp = ["mcp>=1.0"]`; `serve()` imports it lazily.

---

### Task 1: Observation handlers — `phone_observe_ui`, `phone_find`, `phone_capabilities`

**Files:**
- Create: `src/phonectl/mcp_server.py`
- Test: `tests/test_mcp_server.py`

**Interfaces:**
- `observe_ui(build, *, tree=False, relations=False, screenshot=False, snap_path=None) -> dict` — builds,
  `conn.ensure()`, `observer.observe(...)`, returns `results.ok(capability="ui.observe", provider="adb",
  data=snap)`. Catches `errors.PhonectlError` → `results.err(e, **getattr(e,"lock_state",{}))`.
- `find(build, *, selector) -> dict` — observes with `relations=True`, runs `ui_parser.match_selector`,
  returns `results.ok(capability="ui.find", provider="adb", data={"candidates": [...], "confidence": float})`
  where each candidate is `{"i", "text", "id", "bounds", "center"}` plus relation context
  (`parent`/`siblings` from `snap["relations"]` when present). `confidence` = `1.0` for a unique match, else
  `round(1/len(matches), 3)`; empty matches → `confidence: 0.0`, `candidates: []`.
- `capabilities(build) -> dict` — `results.ok(capability="capabilities", provider="adb",
  data={"capabilities": backend.capabilities(), "summary": capabilities.describe(backend.capabilities())})`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_mcp_server.py
import pytest
from phonectl import mcp_server, capabilities as caps


class FakeConn:
    def ensure(self): pass


class FakeBackend:
    serial = "d"
    def __init__(self):
        self.taps = []
        self._xml = ("""<?xml version='1.0'?><hierarchy rotation="0">"""
            """<node index="0" text="Wi-Fi" resource-id="android:id/title" class="T" """
            """content-desc="" clickable="true" bounds="[44,380][1036,520]"/></hierarchy>""")
    def get_state(self): return "device"
    def ui_dump(self): return self._xml
    def window_dump(self): return "mCurrentFocus=Window{a b com.x/.A}"
    def wm_size(self): return (1080, 2400)
    def input_tap(self, x, y): self.taps.append((x, y))
    def input_text(self, t): self.taps.append(("text", t))
    def capabilities(self): return caps.make(observe_ui_tree=True, act_tap=True, requires_adb=True)


def make_build(backend=None):
    from phonectl.session import Session
    backend = backend or FakeBackend()
    def build(cfg): return backend, Session(), FakeConn()
    return build, backend


def test_observe_ui_returns_ok_envelope():
    build, _ = make_build()
    env = mcp_server.observe_ui(build)
    assert env["ok"] is True
    assert env["capability"] == "ui.observe"
    assert env["data"]["elements"][0]["text"] == "Wi-Fi"


def test_find_returns_candidates_and_confidence():
    build, _ = make_build()
    env = mcp_server.find(build, selector={"text": "Wi-Fi"})
    assert env["ok"] is True
    assert env["data"]["candidates"][0]["text"] == "Wi-Fi"
    assert env["data"]["confidence"] == 1.0


def test_find_empty_match_is_zero_confidence():
    build, _ = make_build()
    env = mcp_server.find(build, selector={"text": "Nope"})
    assert env["data"]["candidates"] == [] and env["data"]["confidence"] == 0.0


def test_capabilities_tool_describes_backend():
    build, _ = make_build()
    env = mcp_server.capabilities(build)
    assert env["ok"] is True
    assert env["data"]["capabilities"]["requires_adb"] is True
    assert "observe_ui_tree" in env["data"]["summary"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_mcp_server.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'phonectl.mcp_server'`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/mcp_server.py
"""Structured-result MCP server (strategy §10, §20, §21). Three layers: pure
handlers returning the Plan-1 result envelope, a tool registry + dispatcher, and a
lazily-imported FastMCP transport. The handlers/registry are stdlib-only and
unit-tested without the MCP SDK; the SDK is an optional extra used only by serve()."""
from __future__ import annotations

from phonectl import (actuator, audit, capabilities as capmod, config, errors,
                      observer, policy, results, runtime, ui_parser)


def _default_build(cfg):
    from phonectl.cli import build_runtime
    return build_runtime(cfg)


def observe_ui(build=_default_build, *, tree=False, relations=False,
               screenshot=False, snap_path=None) -> dict:
    try:
        backend, session, conn = build(config.load())
        conn.ensure()
        snap = observer.observe(backend, session, tree=tree, relations=relations,
                                screenshot=screenshot, snap_path=snap_path)
        return results.ok(capability="ui.observe", provider="adb", data=snap)
    except errors.PhonectlError as e:
        return results.err(e, **getattr(e, "lock_state", {}))


def find(build=_default_build, *, selector) -> dict:
    try:
        backend, session, conn = build(config.load())
        conn.ensure()
        snap = observer.observe(backend, session, relations=True)
        matches = ui_parser.match_selector(snap["elements"], selector,
                                           snap.get("relations"))
        rel = snap.get("relations", {}) or {}
        by_i = {e["i"]: e for e in snap["elements"]}
        candidates = []
        for i in matches:
            e = by_i[i]
            candidates.append({
                "i": i, "text": e["text"], "id": e["id"],
                "bounds": e["bounds"], "center": e["center"],
                "parent": (rel.get("parent", {}) or {}).get(str(i)),
                "siblings": (rel.get("siblings", {}) or {}).get(str(i), []),
            })
        confidence = 1.0 if len(matches) == 1 else (round(1 / len(matches), 3) if matches else 0.0)
        return results.ok(capability="ui.find", provider="adb",
                          data={"candidates": candidates, "confidence": confidence})
    except errors.PhonectlError as e:
        return results.err(e, **getattr(e, "lock_state", {}))


def capabilities(build=_default_build) -> dict:
    backend, _session, _conn = build(config.load())
    caps = backend.capabilities()
    return results.ok(capability="capabilities", provider="adb",
                      data={"capabilities": caps, "summary": capmod.describe(caps)})
```

Note: `match_selector(elements, selector, relations)` is the Plan-1.2 signature (positional `relations`), as
used in `cli._cmd_wait_for`. Relation lookups key by `str(i)` because Plan 1.2 emits relation maps with
string keys.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_mcp_server.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/mcp_server.py tests/test_mcp_server.py
git commit -m "feat: MCP observation handlers (observe_ui/find/capabilities) returning envelopes"
```

---

### Task 2: Action handlers via `run_action` — `phone_tap/type/swipe/key/launch`

Action tools route through the Plan-2.1 funnel so the single-writer lock, audit, policy, and rate limits
apply identically to MCP and CLI. They accept index/selector/`(x,y)`, `expected_hash`, `dry_run`, `confirm`,
`reason`, and `idempotency_key`.

**Files:**
- Modify: `src/phonectl/mcp_server.py` (add action handlers + `_target_and_fn` helper)
- Test: `tests/test_mcp_server.py` (append)

**Interfaces:**
- `_action_cfg(dry_run) -> dict` — `config.load()`, overlaid with `{"mode": "dry-run"}` when `dry_run`.
- `tap(build=_default_build, *, index=None, selector=None, x=None, y=None, expected_hash=None,
  stale_ok=False, dry_run=False, confirm=False, reason=None, idempotency_key=None) -> dict` — builds the same
  `fn`/`target` the CLI builds, then `runtime.run_action("tap", fn, target, build=build, yes=confirm,
  cfg=_action_cfg(dry_run), idempotency_key=idempotency_key)`. `reason` (when given) is merged into `target`
  so it is audited.
- `type_text(... text, ...)`, `swipe(... x1,y1,x2,y2 ...)`, `key(... keycode ...)`, `launch(... package ...)`
  — analogous; each returns the `run_action` envelope verbatim (already structured).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_mcp_server.py  (append)
def test_tap_by_selector_routes_through_run_action(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    build, backend = make_build()
    env = mcp_server.tap(build, selector={"text": "Wi-Fi"}, confirm=True)
    assert env["ok"] is True and env["verb"] == "tap"
    assert backend.taps and backend.taps[0] == (540, 450)   # center of [44,380][1036,520]
    log = (tmp_path / "actions.jsonl").read_text()
    assert "selector" in log


def test_tap_dry_run_does_not_act(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    build, backend = make_build()
    env = mcp_server.tap(build, index=0, dry_run=True)
    assert env["ok"] is True and env["dry_run"] is True
    assert backend.taps == []                                # observed, not tapped
    assert not (tmp_path / "actions.jsonl").exists()


def test_tap_kill_switch_returns_stopped(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    (tmp_path / "STOP").write_text("")
    build, backend = make_build()
    env = mcp_server.tap(build, index=0, confirm=True)
    assert env["ok"] is False and env["error"]["code"] == "stopped"
    assert backend.taps == []


def test_type_text_routes_and_audits_redacted(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    build, backend = make_build()
    env = mcp_server.type_text(build, text="hunter2", confirm=True)
    assert env["ok"] is True
    assert ("text", "hunter2") in backend.taps
    log = (tmp_path / "actions.jsonl").read_text()
    assert "hunter2" not in log                              # CLI-style surrogate target audited
```

Note: `phone_type`'s `target` is the `<N chars>` surrogate (as the CLI builds), so the raw text never reaches
the audit log; this mirrors `test_type_redacts_text_in_audit_log`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_mcp_server.py -v`
Expected: FAIL (`mcp_server` has no `tap`/`type_text`).

- [ ] **Step 3: Write minimal implementation**

Append to `src/phonectl/mcp_server.py`:

```python
def _action_cfg(dry_run: bool) -> dict:
    cfg = config.load()
    return {**cfg, "mode": "dry-run"} if dry_run else cfg


def _with_reason(target: dict, reason) -> dict:
    return {**target, "reason": reason} if reason else target


def tap(build=_default_build, *, index=None, selector=None, x=None, y=None,
        expected_hash=None, stale_ok=False, dry_run=False, confirm=False,
        reason=None, idempotency_key=None) -> dict:
    if selector is not None:
        target = {"selector": selector}
        fn = lambda b, s: actuator.tap(b, s, selector=selector, expected_hash=expected_hash, stale_ok=stale_ok)
    elif index is not None:
        target = {"i": index}
        fn = lambda b, s: actuator.tap(b, s, i=index, expected_hash=expected_hash, stale_ok=stale_ok)
    else:
        target = {"x": x, "y": y}
        fn = lambda b, s: actuator.tap(b, s, x=x, y=y, expected_hash=expected_hash, stale_ok=stale_ok)
    return runtime.run_action("tap", fn, _with_reason(target, reason), build=build,
                              yes=confirm, cfg=_action_cfg(dry_run), idempotency_key=idempotency_key)


def type_text(build=_default_build, *, text, dry_run=False, confirm=False,
              reason=None, idempotency_key=None) -> dict:
    target = _with_reason({"text": f"<{len(text)} chars>"}, reason)
    return runtime.run_action("type", lambda b, s: actuator.type_text(b, s, text), target,
                              build=build, yes=confirm, cfg=_action_cfg(dry_run),
                              idempotency_key=idempotency_key)


def swipe(build=_default_build, *, x1, y1, x2, y2, dry_run=False, confirm=False,
          reason=None, idempotency_key=None) -> dict:
    target = _with_reason({"coords": [x1, y1, x2, y2]}, reason)
    return runtime.run_action("swipe", lambda b, s: actuator.swipe(b, s, x1, y1, x2, y2), target,
                              build=build, yes=confirm, cfg=_action_cfg(dry_run),
                              idempotency_key=idempotency_key)


def key(build=_default_build, *, keycode, dry_run=False, confirm=False,
        reason=None, idempotency_key=None) -> dict:
    target = _with_reason({"key": keycode}, reason)
    return runtime.run_action("key", lambda b, s: actuator.key(b, s, keycode), target,
                              build=build, yes=confirm, cfg=_action_cfg(dry_run),
                              idempotency_key=idempotency_key)


def launch(build=_default_build, *, package, dry_run=False, confirm=False,
           reason=None, idempotency_key=None) -> dict:
    target = _with_reason({"package": package}, reason)
    return runtime.run_action("launch", lambda b, s: actuator.launch(b, s, package), target,
                              build=build, yes=confirm, cfg=_action_cfg(dry_run),
                              idempotency_key=idempotency_key)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_mcp_server.py -v`
Expected: PASS (existing + 4 new).

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/mcp_server.py tests/test_mcp_server.py
git commit -m "feat: MCP action handlers routed through run_action (selector/dry-run/expected-hash)"
```

---

### Task 3: Meta tools — `phone_policy_explain`, `phone_audit_query`, `phone_stop`, `phone_resume`

The tools that make an agent autonomous-safe: read *why* an action is allowed/blocked, read the audit trail,
and trigger or clear the emergency stop.

**Files:**
- Modify: `src/phonectl/mcp_server.py` (add the four handlers)
- Test: `tests/test_mcp_server.py` (append)

**Interfaces:**
- `policy_explain(build=_default_build, *, verb="tap", index=None, selector=None, x=None, y=None) -> dict` —
  observes once (read-only), builds a `target`, returns `results.ok(capability="policy.explain",
  data=policy.explain(snap, verb, target, config.load()))`.
- `audit_query(*, limit=20) -> dict` — `results.ok(capability="audit.query",
  data={"entries": audit.read_entries(limit=limit)})` (entries are already redacted per `audit_level`).
- `stop() -> dict` — writes the `STOP` sentinel (`config_dir()/"STOP"`), returns
  `results.ok(capability="control.stop", data={"stopped": True})`.
- `resume() -> dict` — removes the `STOP` sentinel if present, returns
  `results.ok(capability="control.resume", data={"stopped": False})`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_mcp_server.py  (append)
from phonectl import audit
from phonectl.config import config_dir


def test_policy_explain_tool(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    class PayBackend(FakeBackend):
        def ui_dump(self):
            return ("""<?xml version='1.0'?><hierarchy rotation="0">"""
                    """<node index="0" text="Confirm payment" class="T" clickable="true" """
                    """bounds="[0,0][10,10]"/></hierarchy>""")
    build, _ = make_build(PayBackend())
    env = mcp_server.policy_explain(build, verb="tap", index=0)
    assert env["ok"] is True
    assert env["data"]["risk_level"] == "critical"
    assert env["data"]["decision"] == "deny"


def test_audit_query_returns_recent_entries(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    audit.log_action("tap", {"i": 1}, {"app": {"package": "com.x"}, "hash": "h1"})
    env = mcp_server.audit_query(limit=5)
    assert env["ok"] is True
    assert env["data"]["entries"][-1]["hash"] == "h1"


def test_stop_and_resume_toggle_kill_switch(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    assert mcp_server.stop()["data"]["stopped"] is True
    assert (config_dir() / "STOP").exists()
    assert mcp_server.resume()["data"]["stopped"] is False
    assert not (config_dir() / "STOP").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_mcp_server.py -v`
Expected: FAIL (`mcp_server` has no `policy_explain`/`audit_query`/`stop`/`resume`).

- [ ] **Step 3: Write minimal implementation**

Append to `src/phonectl/mcp_server.py`:

```python
def policy_explain(build=_default_build, *, verb="tap", index=None, selector=None,
                   x=None, y=None) -> dict:
    try:
        backend, session, conn = build(config.load())
        conn.ensure()
        snap = observer.observe(backend, session)
        if selector is not None:
            target = {"selector": selector}
        elif index is not None:
            target = {"i": index}
        elif x is not None:
            target = {"x": x, "y": y}
        else:
            target = {}
        return results.ok(capability="policy.explain", provider="adb",
                          data=policy.explain(snap, verb, target, config.load()))
    except errors.PhonectlError as e:
        return results.err(e, **getattr(e, "lock_state", {}))


def audit_query(*, limit=20) -> dict:
    return results.ok(capability="audit.query",
                      data={"entries": audit.read_entries(limit=limit)})


def stop() -> dict:
    from phonectl.config import config_dir
    (config_dir() / "STOP").write_text("")
    return results.ok(capability="control.stop", data={"stopped": True})


def resume() -> dict:
    from phonectl.config import config_dir
    p = config_dir() / "STOP"
    if p.exists():
        p.unlink()
    return results.ok(capability="control.resume", data={"stopped": False})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_mcp_server.py -v`
Expected: PASS (existing + 3 new).

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/mcp_server.py tests/test_mcp_server.py
git commit -m "feat: MCP meta tools policy_explain/audit_query/stop/resume"
```

---

### Task 4: Tool registry + `call_tool` dispatcher

A single source of truth mapping stable tool names to JSON schemas + handlers, plus a pure dispatcher used by
both the tests and the transport. This is what the transport iterates to register tools and what guarantees
every name resolves to an envelope.

**Files:**
- Modify: `src/phonectl/mcp_server.py` (add `TOOLS`, `call_tool`)
- Test: `tests/test_mcp_server.py` (append)

**Interfaces:**
- `TOOLS: dict[str, dict]` — each entry `{"description": str, "schema": dict, "handler": callable}`. `schema`
  is a minimal JSON-Schema `{"type": "object", "properties": {...}}` for the handler's kwargs (used by the
  MCP SDK to advertise the tool). Keys are the stable names from Shared Conventions.
- `call_tool(name, args, build=_default_build) -> dict` — looks up `name`; for handlers that take `build` as
  first arg, calls `handler(build, **args)`; for build-less handlers (`audit_query`/`stop`/`resume`), calls
  `handler(**args)`. Unknown name → `results.err(("unknown_tool", f"no such tool: {name}"))`. Any uncaught
  `errors.PhonectlError` (defense in depth) → `results.err(e)`.
- The registry distinguishes build vs build-less handlers via a per-entry `"needs_build": bool`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_mcp_server.py  (append)
def test_registry_lists_stable_tool_names():
    for name in ("phone_observe_ui", "phone_find", "phone_capabilities", "phone_tap",
                 "phone_type", "phone_swipe", "phone_key", "phone_launch",
                 "phone_policy_explain", "phone_audit_query", "phone_stop", "phone_resume"):
        assert name in mcp_server.TOOLS
        assert callable(mcp_server.TOOLS[name]["handler"])
        assert mcp_server.TOOLS[name]["schema"]["type"] == "object"


def test_call_tool_dispatches_observe(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    build, _ = make_build()
    env = mcp_server.call_tool("phone_observe_ui", {}, build=build)
    assert env["ok"] is True and env["capability"] == "ui.observe"


def test_call_tool_dispatches_buildless_audit(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    from phonectl import audit
    audit.log_action("tap", {"i": 1}, {"app": {}, "hash": "h"})
    env = mcp_server.call_tool("phone_audit_query", {"limit": 1})
    assert env["ok"] is True and env["data"]["entries"][0]["hash"] == "h"


def test_call_tool_unknown_name_errors():
    env = mcp_server.call_tool("phone_teleport", {})
    assert env["ok"] is False and env["error"]["code"] == "unknown_tool"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_mcp_server.py -v`
Expected: FAIL (`mcp_server` has no `TOOLS`/`call_tool`).

- [ ] **Step 3: Write minimal implementation**

Append to `src/phonectl/mcp_server.py`:

```python
_OBJ = {"type": "object"}


def _schema(**props):
    return {"type": "object", "properties": props}


_TARGET_PROPS = {
    "index": {"type": "integer"}, "selector": {"type": "object"},
    "x": {"type": "integer"}, "y": {"type": "integer"},
    "expected_hash": {"type": "string"}, "stale_ok": {"type": "boolean"},
    "dry_run": {"type": "boolean"}, "confirm": {"type": "boolean"},
    "reason": {"type": "string"}, "idempotency_key": {"type": "string"},
}

TOOLS = {
    "phone_observe_ui": {"description": "Observe the foreground UI as structured JSON.",
                         "schema": _schema(tree={"type": "boolean"}, relations={"type": "boolean"},
                                           screenshot={"type": "boolean"}),
                         "handler": observe_ui, "needs_build": True},
    "phone_find": {"description": "Resolve a selector against a fresh snapshot.",
                   "schema": _schema(selector={"type": "object"}), "handler": find, "needs_build": True},
    "phone_capabilities": {"description": "List provider capabilities.",
                           "schema": _OBJ, "handler": capabilities, "needs_build": True},
    "phone_tap": {"description": "Tap by index, selector, or coordinates.",
                  "schema": _schema(**_TARGET_PROPS), "handler": tap, "needs_build": True},
    "phone_type": {"description": "Type text into the focused field.",
                   "schema": _schema(text={"type": "string"}, dry_run={"type": "boolean"},
                                     confirm={"type": "boolean"}, reason={"type": "string"},
                                     idempotency_key={"type": "string"}),
                   "handler": type_text, "needs_build": True},
    "phone_swipe": {"description": "Swipe between two points.",
                    "schema": _schema(x1={"type": "integer"}, y1={"type": "integer"},
                                      x2={"type": "integer"}, y2={"type": "integer"},
                                      dry_run={"type": "boolean"}, confirm={"type": "boolean"}),
                    "handler": swipe, "needs_build": True},
    "phone_key": {"description": "Send a key event (back/home/recents/enter or a keycode).",
                  "schema": _schema(keycode={"type": "string"}, dry_run={"type": "boolean"},
                                    confirm={"type": "boolean"}), "handler": key, "needs_build": True},
    "phone_launch": {"description": "Launch an app by package.",
                     "schema": _schema(package={"type": "string"}, dry_run={"type": "boolean"},
                                       confirm={"type": "boolean"}), "handler": launch, "needs_build": True},
    "phone_policy_explain": {"description": "Explain the risk/policy decision for an action.",
                             "schema": _schema(verb={"type": "string"}, **_TARGET_PROPS),
                             "handler": policy_explain, "needs_build": True},
    "phone_audit_query": {"description": "Read recent (redacted) audit entries.",
                          "schema": _schema(limit={"type": "integer"}),
                          "handler": audit_query, "needs_build": False},
    "phone_stop": {"description": "Engage the emergency stop (kill switch).",
                   "schema": _OBJ, "handler": stop, "needs_build": False},
    "phone_resume": {"description": "Clear the emergency stop.",
                     "schema": _OBJ, "handler": resume, "needs_build": False},
}


def call_tool(name, args, build=_default_build) -> dict:
    entry = TOOLS.get(name)
    if entry is None:
        return results.err(("unknown_tool", f"no such tool: {name}"))
    try:
        if entry["needs_build"]:
            return entry["handler"](build, **args)
        return entry["handler"](**args)
    except errors.PhonectlError as e:
        return results.err(e, **getattr(e, "lock_state", {}))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_mcp_server.py -v`
Expected: PASS (existing + 4 new).

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/mcp_server.py tests/test_mcp_server.py
git commit -m "feat: MCP tool registry (TOOLS) + pure call_tool dispatcher"
```

---

### Task 5: Gated FastMCP transport + `phonectl mcp` CLI + optional extra

Wire the registry to the live stdio transport behind a lazy SDK import, expose `phonectl mcp`, and declare the
optional `mcp` extra. The transport adapter is tested with a fake registrar (no real SDK needed in CI).

**Files:**
- Modify: `src/phonectl/mcp_server.py` (add `serve` + `_register`)
- Modify: `src/phonectl/cli.py` (add `phonectl mcp`)
- Modify: `pyproject.toml` (optional `mcp` extra)
- Test: `tests/test_mcp_server.py`, `tests/test_cli.py` (append)

**Interfaces:**
- `_register(app, build=_default_build) -> list[str]` — for each `TOOLS` entry, register a wrapper that calls
  `call_tool(name, kwargs, build)` on `app` (FastMCP's `app.tool(name=..., description=...)` decorator);
  returns the list of registered names. `app` is injected so a fake captures registrations in tests.
- `serve(build=_default_build) -> None` — lazily `import mcp` (FastMCP); if `ImportError`, raise
  `errors.CapabilityUnavailableError("MCP SDK not installed; pip install phonectl[mcp]")`. Otherwise build a
  `FastMCP("phonectl")` app, `_register(app, build)`, and `app.run()`.
- CLI: `phonectl mcp` → `_cmd_mcp` imports `mcp_server` lazily and calls `serve()`; on
  `CapabilityUnavailableError`, print the `user_action` and return `1`.
- `pyproject.toml`: `[project.optional-dependencies]` `mcp = ["mcp>=1.0"]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_mcp_server.py  (append)
def test_register_registers_all_tools_on_fake_app(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))

    class FakeApp:
        def __init__(self): self.registered = []
        def tool(self, name=None, description=None):
            self.registered.append(name)
            def deco(fn): return fn
            return deco

    app = FakeApp()
    build, _ = make_build()
    names = mcp_server._register(app, build=build)
    assert set(names) == set(mcp_server.TOOLS)
    assert "phone_tap" in app.registered


def test_serve_raises_capability_unavailable_without_sdk(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "mcp" or name.startswith("mcp."):
            raise ImportError("no mcp")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(mcp_server.errors.CapabilityUnavailableError):
        mcp_server.serve()
```

```python
# tests/test_cli.py  (append)
def test_mcp_cli_reports_missing_sdk(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    from phonectl import mcp_server, errors
    def boom(build=None):
        raise errors.CapabilityUnavailableError("MCP SDK not installed; pip install phonectl[mcp]")
    monkeypatch.setattr(mcp_server, "serve", boom)
    rc = cli.main(["mcp"])
    out = capsys.readouterr().out
    assert rc == 1 and "phonectl[mcp]" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_mcp_server.py tests/test_cli.py -v`
Expected: FAIL (`mcp_server` has no `_register`/`serve`; CLI has no `mcp` verb).

- [ ] **Step 3: Write minimal implementation**

Append to `src/phonectl/mcp_server.py`:

```python
def _register(app, build=_default_build) -> list[str]:
    names = []
    for name, entry in TOOLS.items():
        @app.tool(name=name, description=entry["description"])
        def _tool(_name=name, **kwargs):
            return call_tool(_name, kwargs, build=build)
        names.append(name)
    return names


def serve(build=_default_build) -> None:
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as e:
        raise errors.CapabilityUnavailableError(
            "MCP SDK not installed; pip install phonectl[mcp]") from e
    app = FastMCP("phonectl")
    _register(app, build=build)
    app.run()
```

In `src/phonectl/cli.py`:

```python
def _cmd_mcp(args):
    from phonectl import mcp_server
    try:
        mcp_server.serve()
        return 0
    except errors.CapabilityUnavailableError as e:
        print(f"phonectl: {e}")
        return 1
```

```python
    # build_parser: register mcp
    m = sub.add_parser("mcp")
    m.set_defaults(func=_cmd_mcp)
```

In `pyproject.toml`, add:

```toml
[project.optional-dependencies]
mcp = ["mcp>=1.0"]
```

Note: the `_register` wrapper binds `_name=name` as a default so each registered closure dispatches its own
tool (avoiding the late-binding loop-variable trap). The real FastMCP introspects `**kwargs`; the schema in
`TOOLS` documents the accepted args for clients that read it.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_mcp_server.py tests/test_cli.py -v`
Expected: PASS (existing + 2 mcp_server + 1 cli).

- [ ] **Step 5: Run the full suite**

Run: `pytest -v`
Expected: PASS (mcp_server, cli, runtime, policy, and all prior tests).

- [ ] **Step 6: Commit**

```bash
git add src/phonectl/mcp_server.py src/phonectl/cli.py pyproject.toml tests/test_mcp_server.py tests/test_cli.py
git commit -m "feat: gated FastMCP transport, phonectl mcp verb, optional mcp extra"
```

---

### Task 6: Docs — MCP tool catalog + structured-result contract

**Files:**
- Modify: `README.md` (add an "MCP server" section)
- Modify: `docs/superpowers/specs/2026-06-20-phonectl-adb-bridge-design.md` (note the MCP surface returns the
  result envelope and routes actions through `run_action`)

**Interfaces:** none (documentation).

- [ ] **Step 1: Document the catalog**

In `README.md`: how to launch (`pip install phonectl[mcp]`, then `phonectl mcp`), the full tool catalog with
each tool's args and example envelopes (observe/find/capabilities/tap/type/policy_explain/audit_query/
stop/resume), the selector/`expected_hash`/`dry_run`/`confirm`/`reason`/`idempotency_key` action args, and a
note that **every tool returns the structured result envelope** (the agent reads `ok`/`error.code`/
`requires_user`/`risk_level`/`reasons` rather than parsing text). In the design spec, note that the **MCP
server is a thin frontend over `run_action` + the observation seams** (no safety logic duplicated) — the same
shape the Phase-5 daemon will expose.

- [ ] **Step 2: Commit**

```bash
git add README.md docs/superpowers/specs/2026-06-20-phonectl-adb-bridge-design.md
git commit -m "docs: MCP tool catalog and structured-result contract"
```

---

## Dependencies

**Plan 2.3 of the platform roadmap.** Requires **Plan 1.1** (`results`/`errors`/`capabilities`), **Plan 1.2**
(`match_selector` + relations), **Plan 2.1** (`run_action`), and **Plan 2.2** (`policy.explain`). It
supersedes `2026-06-21-phonectl-mcp-server.md`. Downstream:

- **Phase 3** providers (clipboard/intents/packages, scroll-until, extraction) add tools to the same `TOOLS`
  registry and the same `call_tool`/`serve` plumbing — no transport rewrite.
- **Phase 4** event providers (notifications/Accessibility) add `phone_notifications_*` and event-subscription
  tools; the envelope + registry contract is unchanged.
- **Phase 5** (daemon) reuses `call_tool`/the handlers behind a JSON-RPC/socket frontend; CLI, MCP, and daemon
  all funnel through `run_action`.

## Deferred / out of scope (not in this plan)

- **`phone_reconnect` / `phone_diagnostics` tools** — registered opportunistically once Plan 1.3 (`reconnect`)
  and Plan 1.4 (`diagnostics.bundle`) seams exist; add them to `TOOLS` gated via `hasattr`, with their own
  tests, when those plans land.
- **Event-subscription tools** (`phone.events.subscribe`, `phone.watch_ui`) and **macro tools**
  (`phone.macro.*`) → Phases 4–6 (require the daemon/event bus).
- **`phone.describe_screen` semantic summary** (strategy §20.1) → a Phase-3 extraction refinement.
- **A non-stdio transport** (WebSocket / socket) → the daemon (Phase 5); this plan ships stdio MCP only.
- **Real MCP-SDK integration test** — CI exercises the registry/dispatcher/transport adapter with a fake app;
  a live `phonectl mcp` round-trip against the actual SDK + a real device is a manual smoke item, flagged and
  never run in CI.

## Notes on testability

The handlers and registry are stdlib-only and fully unit-tested with a fake `build` (a `Session` + a duck-typed
`FakeBackend` serving canned XML) and `PHONECTL_HOME` isolation — no device, no real `adb`, and **no MCP SDK**.
Observation tools run the real `observer`/`ui_parser` over fixture XML; action tools run through the real
`run_action` (so kill-switch/dry-run/policy/audit are exercised end-to-end) with a fake backend recording
taps. The transport adapter is tested with a `FakeApp` capturing tool registrations, and the SDK-absent path
is tested by monkeypatching `__import__` to fail on `mcp`. The one part needing the real SDK + a device — a
live `phonectl mcp` session — is a flagged manual smoke item, never run in CI.
