> **SUPERSEDED 2026-06-22** — folded into `docs/superpowers/phonectl-platform-roadmap.md` (Phase 2.3, re-targeted onto the structured-result envelope). Task-level re-homing is in `docs/superpowers/plans/2026-06-22-phonectl-remaining-plans-meta-plan.md`. Kept for traceability; do not execute as-is.

# phonectl MCP Server Wrapper Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose the existing phonectl verbs (observe/tap/type/swipe/key/launch/wait_for, plus reconnect/doctor) as native agent tools over a stdio MCP server that routes through the same safety funnel as the CLI, without duplicating any safety logic.

**Architecture:** First extract the core of `cli._do_action` into a backend-agnostic, args-free function `run_action(verb, fn, target, *, yes, cfg) -> (rc: int, snap: dict | None)` in a new `src/phonectl/runtime.py`, and rewrite both the CLI verbs and the new MCP tools to call it — so the kill-switch / mode-gating / `Connection.ensure` / audit funnel lives in exactly one place. Then `src/phonectl/mcp_server.py` defines plain, fully-unit-tested *handler* functions (`tool_observe`, `tool_tap`, …) that call `run_action`/`observe` and return the snapshot dict; a thin FastMCP transport adapter (gated behind an OPTIONAL `mcp` extra) registers those handlers as MCP tools, keeping the core install stdlib-only.

**Tech Stack:** Python 3 (stdlib only for core: `json`, `argparse`; handlers are pure Python over the existing library), `pytest` for tests; the official Python **MCP SDK / FastMCP** (`mcp`) as an **optional** extra used solely by the transport adapter; `adb` (android-tools) remains the only external runtime dependency of the core.

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

This plan **stands alone** against the merged observe→act core (Tasks 1–9 of `2026-06-20-phonectl-observe-act-core.md`, already landed). The `_do_action` extraction is **part of this plan** (Task 1 here), not a prerequisite from another session.

Reuse of the resilience (plan 1) and safety (plan 2) work is **opportunistic**: because every MCP tool routes through the same `run_action`/`build_runtime`/`Connection.ensure` funnel as the CLI, any rate-limit / guarded-package / auto-WAKEUP behaviour those plans add to that funnel is picked up by the MCP tools automatically with no change here. This plan does not require them to have landed first; if implemented in the documented sequence (1 resilience → 2 safety → 3 setup-wizard → 4 mcp-server), the MCP tools simply inherit the richer funnel.

## File Structure (added/changed by this plan)

```
phonectl/
├── pyproject.toml                      # MODIFY: add [project.optional-dependencies] mcp
├── src/phonectl/
│   ├── runtime.py                      # NEW: run_action() shared funnel (extracted from cli._do_action)
│   ├── cli.py                          # MODIFY: _do_action delegates to runtime.run_action
│   └── mcp_server.py                   # NEW: tool handlers + gated FastMCP transport adapter
└── tests/
    ├── test_runtime.py                 # NEW: funnel behavior unit tests
    ├── test_cli.py                     # MODIFY: prove CLI behavior unchanged after refactor
    └── test_mcp_server.py              # NEW: tool-handler unit tests (no live MCP transport)
```

---

### Task 1: Extract the shared action funnel into `runtime.run_action`

Pull the body of `cli._do_action` into a new module `src/phonectl/runtime.py` as a pure, `args`-free function that returns both a return code **and** the snapshot (so MCP tools can serialize it). `cli._do_action` becomes a thin adapter over it. This is a behaviour-preserving refactor — the existing `tests/test_cli.py` must stay green.

**Files:**
- Create: `src/phonectl/runtime.py`
- Modify: `src/phonectl/cli.py` (lines 22-60: `_emit`, `_guard_action`, `_do_action`)
- Test: `tests/test_runtime.py` (Create); `tests/test_cli.py` (Modify — re-run unchanged, no edits required)

**Interfaces:**
- Consumes: `config.load() -> dict`, `config.get_mode(cfg: dict) -> str`, `audit.kill_switch_active() -> bool`, `audit.log_action(verb: str, target: dict, result: dict) -> None`, `cli.build_runtime(cfg, backend=None) -> tuple[backend, Session, Connection]`, `observer.observe(backend, session) -> dict`.
- Produces:
  - `runtime.run_action(verb: str, fn: Callable[[backend, Session], dict], target: dict, *, yes: bool, cfg: dict, build=None) -> tuple[int, dict | None]` — runs the full funnel: kill-switch check (rc 2), `confirm` mode without `yes` (rc 3), `Connection.ensure()`, `dry-run` (observes, no inject, rc 0, returns the observed snapshot), else `fn` + `audit.log_action` (rc 0, returns post-action snapshot). `build` defaults to `cli.build_runtime` and is injectable for tests. On the blocked/refused paths it returns `(rc, None)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_runtime.py
from phonectl import runtime, observer
from phonectl.session import Session
from phonectl.connection import Connection


class FakeBackend:
    def __init__(self):
        self.calls = []
        self.serial = "d"
        self._xml = ("""<?xml version='1.0'?><hierarchy rotation="0">"""
            """<node index="0" text="Wi-Fi" resource-id="android:id/title" class="T" """
            """content-desc="" clickable="true" bounds="[44,380][1036,520]"/></hierarchy>""")
    def get_state(self): return "device"
    def ui_dump(self): return self._xml
    def window_dump(self): return "mCurrentFocus=Window{a b com.x/.A}"
    def wm_size(self): return (1080, 2400)
    def input_tap(self, x, y): self.calls.append(("tap", x, y))


def _build(fb):
    def build(cfg, backend=None):
        return fb, Session(), Connection(fb, cfg)
    return build


def _tap(b, s):
    from phonectl import actuator
    return actuator.tap(b, s, x=100, y=200)


def test_run_action_auto_acts_and_returns_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    fb = FakeBackend()
    rc, snap = runtime.run_action("tap", _tap, {"x": 100, "y": 200},
                                  yes=False, cfg={}, build=_build(fb))
    assert rc == 0
    assert ("tap", 100, 200) in fb.calls
    assert snap["elements"][0]["text"] == "Wi-Fi"
    assert "tap" in (tmp_path / "actions.jsonl").read_text()


def test_run_action_kill_switch_blocks(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    (tmp_path / "STOP").write_text("")
    fb = FakeBackend()
    rc, snap = runtime.run_action("tap", _tap, {"x": 1, "y": 2},
                                  yes=False, cfg={}, build=_build(fb))
    assert rc == 2 and snap is None
    assert fb.calls == []


def test_run_action_confirm_without_yes_refuses(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    fb = FakeBackend()
    rc, snap = runtime.run_action("tap", _tap, {"x": 1, "y": 2},
                                  yes=False, cfg={"mode": "confirm"}, build=_build(fb))
    assert rc == 3 and snap is None
    assert fb.calls == []


def test_run_action_dry_run_observes_but_does_not_inject(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    fb = FakeBackend()
    rc, snap = runtime.run_action("tap", _tap, {"x": 1, "y": 2},
                                  yes=False, cfg={"mode": "dry-run"}, build=_build(fb))
    assert rc == 0
    assert fb.calls == []                              # dry-run must NOT inject
    assert snap is not None and snap["elements"][0]["text"] == "Wi-Fi"  # observed preview
    assert not (tmp_path / "actions.jsonl").exists()   # dry-run must NOT audit-log
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_runtime.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'phonectl.runtime'`)

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/runtime.py
from __future__ import annotations

from phonectl import config, audit, observer


def run_action(verb, fn, target, *, yes, cfg, build=None):
    """Shared action funnel for CLI verbs and MCP tools.

    Returns (return_code, snapshot|None). Snapshot is the post-action observe()
    in auto mode, the observed preview in dry-run, and None on the blocked/
    refused (kill-switch / confirm-without-yes) paths.
    """
    if build is None:
        from phonectl.cli import build_runtime as build
    if audit.kill_switch_active():
        print("phonectl: action refused (kill switch STOP present)")
        return 2, None
    mode = config.get_mode(cfg)
    if mode == "confirm" and not yes:
        print(f"phonectl: {verb} {target} requires --yes in confirm mode")
        return 3, None
    backend, session, conn = build(cfg)
    conn.ensure()
    if mode == "dry-run":
        snap = observer.observe(backend, session)
        print(f"phonectl: dry-run {verb} {target} (not executed)")
        return 0, snap
    snap = fn(backend, session)
    audit.log_action(verb, target, snap)
    return 0, snap
```

```python
# src/phonectl/cli.py  (replace _do_action body, lines 42-60)
def _do_action(args, verb, fn, target):
    from phonectl import runtime
    cfg = config.load()
    rc, snap = runtime.run_action(verb, fn, target, yes=args.yes, cfg=cfg,
                                  build=build_runtime)
    if rc == 0 and snap is not None and config.get_mode(cfg) != "dry-run":
        _emit(snap)
    return rc
```

Note: `_emit` and `_guard_action` stay defined in `cli.py` (other call sites and the existing kill-switch message contract). `run_action` now owns the kill-switch/confirm/dry-run prints, so `_do_action` no longer calls `_guard_action` itself; the printed strings are byte-identical to the originals, which is what keeps `tests/test_cli.py` green.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_runtime.py tests/test_cli.py -v`
Expected: PASS (4 new runtime tests + all existing CLI tests, including the confirm/dry-run/kill-switch/redaction cases, prove behaviour is unchanged)

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/runtime.py src/phonectl/cli.py tests/test_runtime.py
git commit -m "refactor: extract run_action funnel; cli._do_action delegates to it"
```

---

### Task 2: Add the optional `mcp` extra and a gated SDK-availability probe

Declare the official Python MCP SDK as an **optional** extra so the core install stays stdlib-only, and add a small gated helper so handler code (Task 3) and the transport adapter (Task 4) can be imported and unit-tested even when `mcp` is **not** installed.

**Dependency justification:** the MCP server is an *optional, agent-facing surface*. Core users (CLI, scripts) never need it; only an agent that wants phonectl verbs as native MCP tools installs `phonectl[mcp]`. The SDK is therefore the correct place for a third-party dep, gated behind an extra, never a hard runtime dependency — exactly as the architecture invariants require.

**Files:**
- Modify: `pyproject.toml` (lines 9-12: after `requires-python`, before `[project.scripts]`)
- Create: `src/phonectl/mcp_server.py` (probe helper only in this task)
- Test: `tests/test_mcp_server.py` (Create)

**Interfaces:**
- Produces:
  - `mcp_server.mcp_available() -> bool` — `True` iff the `mcp` package can be imported.
  - `mcp_server.require_mcp() -> module` — imports and returns the `mcp` module, else raises `ImportError` with an install hint (`pip install 'phonectl[mcp]'`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_mcp_server.py
import builtins
import pytest
from phonectl import mcp_server


def test_mcp_available_is_bool():
    assert isinstance(mcp_server.mcp_available(), bool)


def test_require_mcp_raises_helpful_error_when_absent(monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "mcp" or name.startswith("mcp."):
            raise ImportError("no mcp")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(ImportError) as e:
        mcp_server.require_mcp()
    assert "phonectl[mcp]" in str(e.value)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_mcp_server.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'phonectl.mcp_server'`)

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/mcp_server.py
from __future__ import annotations

import importlib


def mcp_available() -> bool:
    try:
        importlib.import_module("mcp.server.fastmcp")
        return True
    except ImportError:
        return False


def require_mcp():
    try:
        return importlib.import_module("mcp.server.fastmcp")
    except ImportError as e:  # pragma: no cover - exercised via monkeypatched import
        raise ImportError(
            "the MCP server needs the optional SDK; install it with: "
            "pip install 'phonectl[mcp]'"
        ) from e
```

```toml
# pyproject.toml  (insert after the `requires-python = ">=3.9"` line)
[project.optional-dependencies]
mcp = ["mcp>=1.2"]
```

Note: `require_mcp` uses `importlib.import_module`, so the `monkeypatch.setattr(builtins, "__import__", ...)` in the test triggers the `ImportError` branch even when `mcp` happens to be installed in the dev env. `importlib.import_module` calls `builtins.__import__` under the hood, so the patch is honored.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_mcp_server.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml src/phonectl/mcp_server.py tests/test_mcp_server.py
git commit -m "feat: optional mcp extra and gated SDK-availability probe"
```

---

### Task 3: Read-only MCP tool handlers — `observe`, `wait_for`, `doctor`

Add the non-mutating tool handlers. These do not go through `run_action` (no injection, nothing to audit) but **do** go through the same `build_runtime` + `Connection.ensure` path as the CLI's `_cmd_observe`/`_cmd_wait_for`/`_cmd_doctor`, and they return the observe snapshot (or a status dict for `doctor`) so the observe→act→observe contract holds. Handlers are plain functions, unit-tested with a `FakeBackend` — no MCP transport required.

**Files:**
- Modify: `src/phonectl/mcp_server.py`
- Test: `tests/test_mcp_server.py` (extend)

**Interfaces:**
- Consumes: `cli.build_runtime(cfg, backend=None)`, `config.load() -> dict`, `observer.observe(backend, session) -> dict`, `actuator.wait_for(backend, session, text=None, id=None, timeout=...) -> dict | None`.
- Produces (all accept an injectable `build=` defaulting to `cli.build_runtime`, mirroring `runtime.run_action`):
  - `mcp_server.tool_observe(*, screenshot: bool = False, snap_path: str | None = None, build=None) -> dict` — returns the observe snapshot.
  - `mcp_server.tool_wait_for(*, text: str | None = None, id: str | None = None, timeout: float = 5.0, build=None) -> dict` — returns the matched snapshot, or `{"error": "wait-for timed out", "matched": False}` on timeout; raises `ValueError` if neither `text` nor `id` is given.
  - `mcp_server.tool_doctor(*, build=None) -> dict` — `{"connected": bool, "serial": str | None, "state": str, "guidance": str | None}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_mcp_server.py  (append)
from phonectl import config, mcp_server
from phonectl.session import Session
from phonectl.connection import Connection, GUIDANCE


class HandlerBackend:
    def __init__(self, state="device"):
        self.calls = []
        self.serial = "127.0.0.1:5555"
        self._state = state
        self._xml = ("""<?xml version='1.0'?><hierarchy rotation="0">"""
            """<node index="0" text="Wi-Fi" resource-id="android:id/title" class="T" """
            """content-desc="" clickable="true" bounds="[44,380][1036,520]"/></hierarchy>""")
    def get_state(self): return self._state
    def ui_dump(self): return self._xml
    def window_dump(self): return "mCurrentFocus=Window{a b com.x/.A}"
    def wm_size(self): return (1080, 2400)
    def input_tap(self, x, y): self.calls.append(("tap", x, y))
    def _adb(self, *args): self.calls.append(("adb", args)); return ""


def _build(fb):
    def build(cfg, backend=None):
        return fb, Session(), Connection(fb, cfg)
    return build


def test_tool_observe_returns_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    fb = HandlerBackend()
    snap = mcp_server.tool_observe(build=_build(fb))
    assert snap["elements"][0]["text"] == "Wi-Fi"
    assert snap["app"]["package"] == "com.x"


def test_tool_wait_for_requires_target(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    with pytest.raises(ValueError):
        mcp_server.tool_wait_for(build=_build(HandlerBackend()))


def test_tool_wait_for_finds_text(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    fb = HandlerBackend()
    snap = mcp_server.tool_wait_for(text="Wi-Fi", timeout=1, build=_build(fb))
    assert any(e["text"] == "Wi-Fi" for e in snap["elements"])


def test_tool_doctor_reports_connected(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    fb = HandlerBackend(state="device")
    out = mcp_server.tool_doctor(build=_build(fb))
    assert out == {"connected": True, "serial": "127.0.0.1:5555",
                   "state": "device", "guidance": None}


def test_tool_doctor_reports_guidance_when_offline(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    fb = HandlerBackend(state="offline")
    out = mcp_server.tool_doctor(build=_build(fb))
    assert out["connected"] is False
    assert out["guidance"] == GUIDANCE
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_mcp_server.py -v`
Expected: FAIL (`AttributeError: module 'phonectl.mcp_server' has no attribute 'tool_observe'`)

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/mcp_server.py  (append)
from phonectl import config, observer, actuator
from phonectl.connection import GUIDANCE


def _build(build):
    if build is None:
        from phonectl.cli import build_runtime
        return build_runtime
    return build


def tool_observe(*, screenshot: bool = False, snap_path: str | None = None, build=None) -> dict:
    build = _build(build)
    cfg = config.load()
    backend, session, conn = build(cfg)
    conn.ensure()
    return observer.observe(backend, session, screenshot=screenshot, snap_path=snap_path)


def tool_wait_for(*, text: str | None = None, id: str | None = None,
                  timeout: float = 5.0, build=None) -> dict:
    if text is None and id is None:
        raise ValueError("wait_for requires text or id")
    build = _build(build)
    cfg = config.load()
    backend, session, conn = build(cfg)
    conn.ensure()
    snap = actuator.wait_for(backend, session, text=text, id=id, timeout=timeout)
    if snap is None:
        return {"error": "wait-for timed out", "matched": False}
    return snap


def tool_doctor(*, build=None) -> dict:
    build = _build(build)
    cfg = config.load()
    backend, session, conn = build(cfg)
    try:
        conn.ensure()
    except ConnectionError as e:
        return {"connected": False, "serial": backend.serial,
                "state": backend.get_state(), "guidance": str(e) or GUIDANCE}
    return {"connected": True, "serial": backend.serial,
            "state": backend.get_state(), "guidance": None}
```

Note: `conn.ensure()` raising `ConnectionError` carries `GUIDANCE` as its message (see `connection.py`), so `str(e)` is the guidance string; the `or GUIDANCE` fallback is belt-and-suspenders.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_mcp_server.py -v`
Expected: PASS (2 from Task 2 + 5 new = 7 tests)

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/mcp_server.py tests/test_mcp_server.py
git commit -m "feat: read-only MCP tool handlers observe/wait_for/doctor"
```

---

### Task 4: Mutating MCP tool handlers — `tap`, `type`, `swipe`, `key`, `launch`, `reconnect`

Add the action handlers. Each builds the same `(verb, fn, target)` triple the CLI builds and routes through `runtime.run_action`, so the kill-switch / mode / audit funnel is shared verbatim — no safety logic is duplicated. Handlers return the snapshot on success and a structured refusal dict (carrying the funnel's return code) on a blocked/refused path. `type` redacts text in `target` exactly like the CLI (`<N chars>`).

`tool_tap` exposes both spec §5.2 target forms (`i` and `x,y`); `tool_swipe` is coordinate-only (`x1,y1,x2,y2`), matching the current CLI `_cmd_swipe` — the named-direction `swipe(dir)` form from spec §5.2 stays deferred (see "Deferred / out of scope"). Both target forms of `tap` and all four mutating verbs (`tap`/`type`/`swipe`/`key`/`launch`) plus `reconnect` get a dedicated handler-level test in Step 1.

**Files:**
- Modify: `src/phonectl/mcp_server.py`
- Test: `tests/test_mcp_server.py` (extend)

**Interfaces:**
- Consumes: `runtime.run_action(verb, fn, target, *, yes, cfg, build) -> tuple[int, dict | None]`, `config.load() -> dict`, `actuator.tap/type_text/swipe/key/launch`, `connection.Connection.connect(addr)`.
- Produces (all accept `yes: bool = True` and an injectable `build=`):
  - `mcp_server.tool_tap(*, i: int | None = None, x: int | None = None, y: int | None = None, yes: bool = True, build=None) -> dict`
  - `mcp_server.tool_type(*, text: str, yes: bool = True, build=None) -> dict`
  - `mcp_server.tool_swipe(*, x1: int, y1: int, x2: int, y2: int, ms: int = 200, yes: bool = True, build=None) -> dict`
  - `mcp_server.tool_key(*, keycode: str, yes: bool = True, build=None) -> dict`
  - `mcp_server.tool_launch(*, package: str, yes: bool = True, build=None) -> dict`
  - `mcp_server.tool_reconnect(*, addr: str | None = None, build=None) -> dict` — re-runs `connect`/`ensure` and returns a `tool_doctor`-shaped status dict.
  - Internal: `_action_result(rc: int, snap: dict | None) -> dict` — returns `snap` when `rc == 0` and `snap` is not None, else `{"refused": True, "code": rc}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_mcp_server.py  (append)


def test_tool_tap_by_xy_acts_and_returns_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    fb = HandlerBackend()
    snap = mcp_server.tool_tap(x=100, y=200, build=_build(fb))
    assert ("tap", 100, 200) in fb.calls
    assert snap["elements"][0]["text"] == "Wi-Fi"
    assert "tap" in (tmp_path / "actions.jsonl").read_text()


def test_tool_tap_blocked_by_kill_switch(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    (tmp_path / "STOP").write_text("")
    fb = HandlerBackend()
    out = mcp_server.tool_tap(x=1, y=2, build=_build(fb))
    assert out == {"refused": True, "code": 2}
    assert fb.calls == []


def test_tool_tap_refused_in_confirm_mode_without_yes(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    config.save({"mode": "confirm"})
    fb = HandlerBackend()
    out = mcp_server.tool_tap(x=1, y=2, yes=False, build=_build(fb))
    assert out == {"refused": True, "code": 3}
    assert fb.calls == []


def test_tool_type_redacts_text_in_audit_log(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    fb = HandlerBackend()
    fb.input_text = lambda t: fb.calls.append(("text", t))
    snap = mcp_server.tool_type(text="hunter2", build=_build(fb))
    assert ("text", "hunter2") in fb.calls            # real text WAS typed
    log = (tmp_path / "actions.jsonl").read_text()
    assert "hunter2" not in log                        # but NOT in the audit log
    assert "<7 chars>" in log                          # redacted surrogate present


def test_tool_launch_routes_through_funnel(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    fb = HandlerBackend()
    fb.launch = lambda pkg: fb.calls.append(("launch", pkg))
    snap = mcp_server.tool_launch(package="com.android.settings", build=_build(fb))
    assert ("launch", "com.android.settings") in fb.calls
    log = (tmp_path / "actions.jsonl").read_text()
    assert "launch" in log


def test_tool_reconnect_returns_status(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    fb = HandlerBackend(state="device")
    out = mcp_server.tool_reconnect(addr="127.0.0.1:5555", build=_build(fb))
    assert out["connected"] is True
    assert ("adb", ("connect", "127.0.0.1:5555")) in fb.calls


def test_tool_tap_by_index_resolves_and_acts(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    fb = HandlerBackend()
    # tap-by-index needs the SAME session to already hold a snapshot
    # (actuator.tap calls session.resolve(i) before injecting). Pre-observe
    # into a shared session, then inject a build that hands that session back.
    shared = Session()
    from phonectl import observer
    observer.observe(fb, shared)        # populates shared.last with element 0

    def build(cfg, backend=None):
        return fb, shared, Connection(fb, cfg)

    snap = mcp_server.tool_tap(i=0, build=build)
    # element 0 bounds [44,380][1036,520] -> center (540, 450)
    assert ("tap", 540, 450) in fb.calls
    assert snap["elements"][0]["text"] == "Wi-Fi"
    log = (tmp_path / "actions.jsonl").read_text()
    assert '"i": 0' in log


def test_tool_swipe_routes_through_funnel(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    fb = HandlerBackend()
    fb.input_swipe = lambda x1, y1, x2, y2, ms: fb.calls.append(
        ("swipe", x1, y1, x2, y2, ms))
    snap = mcp_server.tool_swipe(x1=10, y1=20, x2=30, y2=40, ms=150,
                                 build=_build(fb))
    assert ("swipe", 10, 20, 30, 40, 150) in fb.calls
    assert snap["elements"][0]["text"] == "Wi-Fi"
    log = (tmp_path / "actions.jsonl").read_text()
    assert "swipe" in log


def test_tool_key_routes_through_funnel(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    fb = HandlerBackend()
    fb.input_key = lambda kc: fb.calls.append(("key", kc))
    snap = mcp_server.tool_key(keycode="back", build=_build(fb))
    assert ("key", "KEYCODE_BACK") in fb.calls    # KEYMAP maps "back" -> KEYCODE_BACK
    assert snap["elements"][0]["text"] == "Wi-Fi"
    log = (tmp_path / "actions.jsonl").read_text()
    assert "key" in log
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_mcp_server.py -v`
Expected: FAIL (`AttributeError: module 'phonectl.mcp_server' has no attribute 'tool_tap'`)

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/mcp_server.py  (append)
from phonectl import runtime


def _action_result(rc: int, snap) -> dict:
    if rc == 0 and snap is not None:
        return snap
    return {"refused": True, "code": rc}


def tool_tap(*, i=None, x=None, y=None, yes: bool = True, build=None) -> dict:
    build = _build(build)
    cfg = config.load()
    if i is not None:
        fn = lambda b, s: actuator.tap(b, s, i=i)
        target = {"i": i}
    else:
        fn = lambda b, s: actuator.tap(b, s, x=x, y=y)
        target = {"x": x, "y": y}
    rc, snap = runtime.run_action("tap", fn, target, yes=yes, cfg=cfg, build=build)
    return _action_result(rc, snap)


def tool_type(*, text: str, yes: bool = True, build=None) -> dict:
    build = _build(build)
    cfg = config.load()
    rc, snap = runtime.run_action(
        "type", lambda b, s: actuator.type_text(b, s, text),
        {"text": f"<{len(text)} chars>"}, yes=yes, cfg=cfg, build=build)
    return _action_result(rc, snap)


def tool_swipe(*, x1: int, y1: int, x2: int, y2: int, ms: int = 200,
               yes: bool = True, build=None) -> dict:
    build = _build(build)
    cfg = config.load()
    rc, snap = runtime.run_action(
        "swipe", lambda b, s: actuator.swipe(b, s, x1, y1, x2, y2, ms),
        {"coords": [x1, y1, x2, y2]}, yes=yes, cfg=cfg, build=build)
    return _action_result(rc, snap)


def tool_key(*, keycode: str, yes: bool = True, build=None) -> dict:
    build = _build(build)
    cfg = config.load()
    rc, snap = runtime.run_action(
        "key", lambda b, s: actuator.key(b, s, keycode),
        {"key": keycode}, yes=yes, cfg=cfg, build=build)
    return _action_result(rc, snap)


def tool_launch(*, package: str, yes: bool = True, build=None) -> dict:
    build = _build(build)
    cfg = config.load()
    rc, snap = runtime.run_action(
        "launch", lambda b, s: actuator.launch(b, s, package),
        {"package": package}, yes=yes, cfg=cfg, build=build)
    return _action_result(rc, snap)


def tool_reconnect(*, addr: str | None = None, build=None) -> dict:
    build = _build(build)
    cfg = config.load()
    backend, session, conn = build(cfg)
    if addr:
        conn.connect(addr)
    try:
        conn.ensure()
    except ConnectionError as e:
        return {"connected": False, "serial": backend.serial,
                "state": backend.get_state(), "guidance": str(e) or GUIDANCE}
    return {"connected": True, "serial": backend.serial,
            "state": backend.get_state(), "guidance": None}
```

Note: `type` passes the **real** `text` to `actuator.type_text` while handing `run_action` a redacted `target` (`<N chars>`), so `audit.log_action` only ever sees the surrogate — identical to `cli._cmd_type`. No cleartext secret reaches `actions.jsonl`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_mcp_server.py -v`
Expected: PASS (7 from Tasks 2-3 + 9 new = 16 tests; the 9 cover tap-by-xy, tap-by-index, kill-switch refusal, confirm-without-yes refusal, type redaction, launch, swipe, key, and reconnect — every mutating verb exercised at the handler level)

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/mcp_server.py tests/test_mcp_server.py
git commit -m "feat: mutating MCP tool handlers routed through shared run_action funnel"
```

---

### Task 5: Tool registry + gated FastMCP transport adapter + `phonectl mcp` entry point

Expose a stdlib-testable **registry** mapping tool names to handlers (unit-tested), then a thin FastMCP adapter that registers them and runs the stdio server (gated behind `require_mcp()`, imported lazily so the suite stays stdlib-only). Wire a `phonectl mcp` CLI subcommand that starts it. The transport `run()` call itself cannot be unit-tested without the SDK and a live stdio loop — it is isolated to one tiny function and marked accordingly.

**Files:**
- Modify: `src/phonectl/mcp_server.py`, `src/phonectl/cli.py` (add subparser near lines 164-165, before `return p`)
- Test: `tests/test_mcp_server.py`, `tests/test_cli.py` (extend)

**Interfaces:**
- Produces:
  - `mcp_server.TOOLS: dict[str, Callable]` — name → handler (`"observe"`, `"tap"`, `"type"`, `"swipe"`, `"key"`, `"launch"`, `"wait_for"`, `"doctor"`, `"reconnect"`).
  - `mcp_server.build_server()` — imports the SDK via `require_mcp()`, constructs a `FastMCP("phonectl")`, registers every `TOOLS` entry via `@server.tool()`, returns the server. (Not unit-tested without the SDK.)
  - `mcp_server.serve() -> None` — `build_server().run()` over stdio. (Not unit-tested; transport.)
  - `cli._cmd_mcp(args) -> int` — calls `mcp_server.serve()`; returns 0. Raises the `require_mcp()` `ImportError` (with the install hint) if the extra is missing.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_mcp_server.py  (append)


def test_registry_lists_all_verbs():
    assert set(mcp_server.TOOLS) == {
        "observe", "tap", "type", "swipe", "key", "launch",
        "wait_for", "doctor", "reconnect",
    }


def test_registry_handlers_are_callable_and_route(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    fb = HandlerBackend()
    snap = mcp_server.TOOLS["observe"](build=_build(fb))
    assert snap["elements"][0]["text"] == "Wi-Fi"


def test_build_server_raises_without_sdk(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "mcp" or name.startswith("mcp."):
            raise ImportError("no mcp")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(ImportError) as e:
        mcp_server.build_server()
    assert "phonectl[mcp]" in str(e.value)
```

```python
# tests/test_cli.py  (append)
def test_mcp_subcommand_invokes_serve(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    from phonectl import mcp_server
    called = []
    monkeypatch.setattr(mcp_server, "serve", lambda: called.append(True))
    rc = cli.main(["mcp"])
    assert rc == 0
    assert called == [True]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_mcp_server.py tests/test_cli.py -v`
Expected: FAIL (`AttributeError: module 'phonectl.mcp_server' has no attribute 'TOOLS'`; `cli.main(["mcp"])` -> `SystemExit 2` unknown command)

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/mcp_server.py  (append)
TOOLS = {
    "observe": tool_observe,
    "tap": tool_tap,
    "type": tool_type,
    "swipe": tool_swipe,
    "key": tool_key,
    "launch": tool_launch,
    "wait_for": tool_wait_for,
    "doctor": tool_doctor,
    "reconnect": tool_reconnect,
}


def build_server():  # pragma: no cover - requires the optional mcp SDK
    fastmcp = require_mcp()
    server = fastmcp.FastMCP("phonectl")
    for name, handler in TOOLS.items():
        server.tool(name=name)(handler)
    return server


def serve() -> None:  # pragma: no cover - live stdio transport
    build_server().run()
```

```python
# src/phonectl/cli.py  (add inside build_parser, before `return p`)
    m = sub.add_parser("mcp")
    m.set_defaults(func=_cmd_mcp)
```

```python
# src/phonectl/cli.py  (add a handler alongside the other _cmd_* functions)
def _cmd_mcp(args):
    from phonectl import mcp_server
    mcp_server.serve()
    return 0
```

Note: `build_server`/`serve` are marked `# pragma: no cover` because they require the live SDK + stdio loop; **all routing logic they touch is in the `TOOLS` handlers, which are fully unit-tested above.** `test_build_server_raises_without_sdk` covers the `require_mcp()` gate. The `@server.tool()` registration relies on FastMCP reading each handler's keyword-only signature and type hints to build the tool schema — verified manually in Task 6, not in CI.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_mcp_server.py tests/test_cli.py -v`
Expected: PASS (registry + gate tests, and the CLI `mcp` subcommand dispatches to `serve`)

- [ ] **Step 5: Run the full suite**

Run: `pytest -v`
Expected: PASS (all prior tests + the new runtime and mcp_server tests; suite remains stdlib-only — `mcp` need not be installed)

- [ ] **Step 6: Commit**

```bash
git add src/phonectl/mcp_server.py src/phonectl/cli.py tests/test_mcp_server.py tests/test_cli.py
git commit -m "feat: MCP tool registry, gated FastMCP adapter, and phonectl mcp entry point"
```

---

### Task 6: Manual MCP transport smoke + README/docs

Document installing the optional extra and running the server against a real MCP client; manually verify the FastMCP transport (the one piece CI cannot exercise). This is manual because it requires the third-party SDK plus a live stdio MCP client and a paired device.

**Files:**
- Create: `docs/mcp-server.md`
- Modify: `README.md` (add an "MCP server" section)

**Interfaces:** none (documentation + manual procedure).

- [ ] **Step 1: Install the optional extra**

```bash
# inside the PRoot distro, with phonectl already paired+connected (phonectl doctor == connected)
pip install -e '.[mcp]'
```

- [ ] **Step 2: Start the server and list tools from a client**

```bash
phonectl mcp   # serves MCP over stdio
```

Point an MCP-capable client (e.g. the MCP Inspector, or an agent's stdio MCP config running `phonectl mcp`) at it and list tools. Expected: nine tools — `observe`, `tap`, `type`, `swipe`, `key`, `launch`, `wait_for`, `doctor`, `reconnect` — each with the parameters from its handler signature.

- [ ] **Step 3: Drive the observe→act→observe loop through the client**

Call `observe` (expect the snapshot JSON), then `launch` with `package="com.android.settings"`, then `wait_for` with `text="Network & internet"`, then `tap` with the index of that element. Expect each tool to return a snapshot whose `app`/`hash` change after `tap`, confirming the action landed and the funnel ran. Then `touch "$PHONECTL_HOME/STOP"` and re-call `tap`: expect `{"refused": true, "code": 2}` — proving the kill-switch funnel is shared.

- [ ] **Step 4: Write the docs**

`docs/mcp-server.md`: the install (`pip install 'phonectl[mcp]'`), the `phonectl mcp` stdio invocation, an example agent MCP client config block, the tool list with parameters, and a note that tools obey the same mode/kill-switch/audit funnel as the CLI (a mutating tool returns `{"refused": true, "code": N}` when gated). `README.md`: a short "MCP server" section pointing to `docs/mcp-server.md` and noting `mcp` is an **optional** extra (core stays stdlib-only).

- [ ] **Step 5: Commit**

```bash
git add README.md docs/mcp-server.md
git commit -m "docs: MCP server install, stdio invocation, and manual transport smoke"
```

---

## Deferred / out of scope (not in this plan)

- MCP **resources/prompts** (only tools are exposed here).
- **Named-direction `swipe`** (spec §5.2's `swipe(dir | x1,y1->x2,y2)` form): `tool_swipe` ships coordinate-only (`x1,y1,x2,y2`), matching the current CLI `_cmd_swipe`. Named directions (`up`/`down`/`left`/`right`) remain a deferred carryover already flagged by the resilience spec §4; adding them here would mean adding them to the CLI first, which is out of this plan's scope.
- An HTTP/SSE MCP transport (stdio only; the design specifies a local, on-device server).
- Per-tool argument schema customization beyond what FastMCP infers from the handler signatures.
- The transport-layer (`build_server`/`serve`) FastMCP wiring has **no CI coverage** by design — it needs the optional SDK and a live stdio loop, so it is verified by the Task 6 manual smoke. All routing/safety logic lives in the unit-tested `TOOLS` handlers.
