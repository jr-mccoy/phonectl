# phonectl Structured Results, Errors & Capability Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Plan 1.1 of the platform roadmap** (`docs/superpowers/phonectl-platform-roadmap.md`). This is the first
of the Phase-1 "seam" plans; it is a prerequisite for Plan 1.2 (selectors), 1.3 (resilience), and every
later structured-result surface.

**Goal:** Establish the platform seams so every later surface (CLI, MCP, providers, daemon) returns
**structured, actionable results** instead of CLI tuples or raw tracebacks. Add: a typed error hierarchy
with stable codes/flags, a `{ok, …, error{code,message,retryable,requires_user,user_action}}` result
envelope, per-backend **capability discovery**, and an explicit `Backend` Protocol seam.

**Architecture:** Four new, mostly-pure/structural modules. `errors.py` defines the canonical exception
hierarchy with `code`/`retryable`/`requires_user` class attributes. `results.py` provides `ok()`/`err()`
envelope builders (strategy §21 shape). `capabilities.py` defines the capability schema and a `describe`
helper. `backend.py` defines the `Backend` `typing.Protocol` seam (folding the old accessibility-backend
plan's Task 1 forward) and `AdbBackend` gains a `capabilities()` method. These modules add **data
structures only** — the *behaviors* that raise the errors (observe retry/lock → Plan 1.3; stale-snapshot
→ Plan 1.2; rate-limit/guarded → Plan 2.2) land in their own plans, importing these classes. Task 5 wires
a thin CLI `--json` path that demonstrates the envelope and maps typed errors → exit codes with no
traceback.

**Tech Stack:** Python 3 (stdlib only: `json`, `typing`, `argparse`); `pytest` for tests; `adb`
(android-tools) remains the only external runtime dependency.

## Global Constraints

- **stdlib-only at runtime** (Python ≥ 3.9; `pytest` dev-only). No third-party runtime deps.
- **ONLY `adb_backend.py` may touch adb/subprocess.** `capabilities()` is a pure dict return — no new I/O.
- **`ui_parser.py` stays pure.** (Untouched by this plan.)
- **Every actuator `act()` re-observes.** (Untouched by this plan.)
- **Tests isolate via** `monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))` where config/audit is touched.
- **One commit per task.**
- **TDD order is non-negotiable:** write the failing test, run it to confirm it fails for the right
  reason, then write the minimum code to pass.

## Shared conventions established by this plan

- **`src/phonectl/errors.py` is created HERE and is canonical.** Plans 1.2/1.3/2.2 IMPORT these classes;
  they never redefine the module. (This supersedes the old resilience plan's Task 1 and the old safety
  plan's Task 1, which each created/appended `errors.py` independently.)
- **Error codes are stable strings** (`device_locked`, `stale_snapshot`, `capability_unavailable`,
  `guarded_action`, `rate_limited`, `observe_failed`) — they appear in the result envelope and in the
  CLI/MCP contract, so do not rename them once shipped.
- **The result envelope** is the strategy §21 shape and is the new platform invariant (roadmap §4).

---

### Task 1: `errors.py` — typed hierarchy with stable codes/flags

**Files:**
- Create: `src/phonectl/errors.py`
- Test: `tests/test_errors.py`

**Interfaces:**
- Produces:
  - `class PhonectlError(Exception)` — base; class attrs `code = "error"`, `retryable = False`,
    `requires_user = False`.
  - `class ObserveError(PhonectlError)` — `code = "observe_failed"`, `retryable = True`.
  - `class DeviceLockedError(ObserveError)` — `code = "device_locked"`, `retryable = False`,
    `requires_user = True`.
  - `class StaleSnapshotError(PhonectlError)` — `code = "stale_snapshot"`, `retryable = True`.
  - `class CapabilityUnavailableError(PhonectlError)` — `code = "capability_unavailable"`,
    `requires_user = True`.
  - `class GuardedActionError(PhonectlError)` — `code = "guarded_action"`, `requires_user = True`.
  - `class RateLimitError(PhonectlError)` — `code = "rate_limited"`, `retryable = True`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_errors.py
import pytest
from phonectl import errors


def test_hierarchy_is_correct():
    assert issubclass(errors.ObserveError, errors.PhonectlError)
    assert issubclass(errors.DeviceLockedError, errors.ObserveError)
    assert issubclass(errors.StaleSnapshotError, errors.PhonectlError)
    assert issubclass(errors.CapabilityUnavailableError, errors.PhonectlError)
    assert issubclass(errors.GuardedActionError, errors.PhonectlError)
    assert issubclass(errors.RateLimitError, errors.PhonectlError)


def test_stable_codes():
    assert errors.DeviceLockedError.code == "device_locked"
    assert errors.StaleSnapshotError.code == "stale_snapshot"
    assert errors.CapabilityUnavailableError.code == "capability_unavailable"
    assert errors.GuardedActionError.code == "guarded_action"
    assert errors.RateLimitError.code == "rate_limited"
    assert errors.ObserveError.code == "observe_failed"


def test_actionable_flags():
    assert errors.DeviceLockedError.requires_user is True
    assert errors.DeviceLockedError.retryable is False
    assert errors.ObserveError.retryable is True
    assert errors.StaleSnapshotError.retryable is True


def test_raisable_with_message_and_caught_as_base():
    with pytest.raises(errors.PhonectlError) as e:
        raise errors.DeviceLockedError("device is locked, unlock it")
    assert "locked" in str(e.value)
    assert e.value.code == "device_locked"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_errors.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'phonectl.errors'`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/errors.py
"""Canonical typed-error hierarchy for phonectl.

Created by Plan 1.1. Every class carries a stable string `code` plus `retryable`
and `requires_user` flags so the result envelope (results.py) and the CLI/MCP
contract can distinguish "try a different UI route" from "ask the user to grant
a permission / unlock" from "this device cannot do that". Later plans IMPORT
these classes and never redefine the module.
"""


class PhonectlError(Exception):
    code = "error"
    retryable = False
    requires_user = False


class ObserveError(PhonectlError):
    code = "observe_failed"
    retryable = True


class DeviceLockedError(ObserveError):
    code = "device_locked"
    retryable = False
    requires_user = True


class StaleSnapshotError(PhonectlError):
    code = "stale_snapshot"
    retryable = True


class CapabilityUnavailableError(PhonectlError):
    code = "capability_unavailable"
    requires_user = True


class GuardedActionError(PhonectlError):
    code = "guarded_action"
    requires_user = True


class RateLimitError(PhonectlError):
    code = "rate_limited"
    retryable = True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_errors.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/errors.py tests/test_errors.py
git commit -m "feat: canonical typed-error hierarchy with stable codes and actionable flags"
```

---

### Task 2: `results.py` — structured envelope builders

**Files:**
- Create: `src/phonectl/results.py`
- Test: `tests/test_results.py`

**Interfaces:**
- Produces:
  - `ok(*, capability: str | None = None, provider: str | None = None, data=None, **extra) -> dict` —
    returns `{"ok": True}` plus any non-None of `capability`/`provider`/`data`, plus `extra`
    (e.g. `snapshot_before`, `snapshot_after`, `target_resolution`, `fallbacks_considered` from §21).
  - `err(error, *, capability: str | None = None, user_action: str | None = None, **extra) -> dict` —
    `error` may be a `PhonectlError` instance or a `(code, message)` pair. Returns
    `{"ok": False, "error": {"code", "message", "retryable", "requires_user", "user_action"}}` plus
    `capability`/`extra`. For a `PhonectlError`, `code`/`retryable`/`requires_user` come from the
    instance; for a `(code, message)` pair they default to `retryable=False, requires_user=False`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_results.py
from phonectl import results, errors


def test_ok_minimal():
    assert results.ok() == {"ok": True}


def test_ok_with_capability_provider_and_extra():
    out = results.ok(capability="ui.set_text", provider="adb",
                     snapshot_after="snap_def")
    assert out["ok"] is True
    assert out["capability"] == "ui.set_text"
    assert out["provider"] == "adb"
    assert out["snapshot_after"] == "snap_def"


def test_err_from_phonectl_error_maps_code_and_flags():
    out = results.err(errors.DeviceLockedError("device is locked, unlock it"),
                      user_action="Unlock the phone manually.")
    assert out["ok"] is False
    assert out["error"]["code"] == "device_locked"
    assert out["error"]["message"] == "device is locked, unlock it"
    assert out["error"]["retryable"] is False
    assert out["error"]["requires_user"] is True
    assert out["error"]["user_action"] == "Unlock the phone manually."


def test_err_from_code_message_pair_defaults():
    out = results.err(("capability_unavailable", "notifications.reply not available"),
                      capability="notifications.reply",
                      user_action="Enable Notification Access in Android Settings.")
    assert out["ok"] is False
    assert out["error"]["code"] == "capability_unavailable"
    assert out["error"]["retryable"] is False
    assert out["capability"] == "notifications.reply"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_results.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'phonectl.results'`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/results.py
"""Structured result envelope (strategy §21). Every runtime/provider/MCP call
returns one of these, never a bare tuple or a raw traceback."""
from __future__ import annotations

from phonectl import errors


def ok(*, capability=None, provider=None, data=None, **extra) -> dict:
    out = {"ok": True}
    if capability is not None:
        out["capability"] = capability
    if provider is not None:
        out["provider"] = provider
    if data is not None:
        out["data"] = data
    out.update(extra)
    return out


def err(error, *, capability=None, user_action=None, **extra) -> dict:
    if isinstance(error, errors.PhonectlError):
        code = error.code
        message = str(error)
        retryable = error.retryable
        requires_user = error.requires_user
    else:
        code, message = error  # (code, message) pair
        retryable = False
        requires_user = False
    body = {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
            "retryable": retryable,
            "requires_user": requires_user,
            "user_action": user_action,
        },
    }
    if capability is not None:
        body["capability"] = capability
    body.update(extra)
    return body
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_results.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/results.py tests/test_results.py
git commit -m "feat: structured result envelope builders (ok/err) per strategy §21"
```

---

### Task 3: `capabilities.py` — capability schema + describe

**Files:**
- Create: `src/phonectl/capabilities.py`
- Test: `tests/test_capabilities.py`

**Interfaces:**
- Produces:
  - `CAPABILITY_KEYS: tuple[str, ...]` — the full key set from strategy §4.2: `observe_ui_tree`,
    `observe_screenshot`, `act_tap`, `act_type`, `act_key`, `launch_app`, `send_intent`,
    `read_notifications`, `reply_notifications`, `read_clipboard`, `write_clipboard`,
    `write_secure_settings`, `persistent_events`, `requires_adb`, `requires_accessibility`,
    `requires_notification_listener`.
  - `make(**flags) -> dict` — returns a capability doc with every `CAPABILITY_KEYS` key present,
    defaulting missing keys to `False`; raises `ValueError` on an unknown key (guards typos).
  - `describe(caps: dict) -> str` — a short human/agent-readable summary listing which capabilities are
    available and which are not (used by `phonectl_capabilities` MCP tool later and by `--json` now).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_capabilities.py
import pytest
from phonectl import capabilities


def test_schema_has_strategy_keys():
    for key in ("observe_ui_tree", "act_tap", "act_type", "send_intent",
                "read_notifications", "read_clipboard", "persistent_events",
                "requires_adb"):
        assert key in capabilities.CAPABILITY_KEYS


def test_make_fills_all_keys_default_false():
    caps = capabilities.make(observe_ui_tree=True, act_tap=True, requires_adb=True)
    assert set(caps) == set(capabilities.CAPABILITY_KEYS)
    assert caps["observe_ui_tree"] is True
    assert caps["read_notifications"] is False        # defaulted


def test_make_rejects_unknown_key():
    with pytest.raises(ValueError):
        capabilities.make(teleport=True)


def test_describe_mentions_available_and_unavailable():
    caps = capabilities.make(observe_ui_tree=True, read_notifications=False)
    text = capabilities.describe(caps)
    assert "observe_ui_tree" in text
    assert "read_notifications" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_capabilities.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'phonectl.capabilities'`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/capabilities.py
"""Per-provider capability discovery (strategy §4.2). The runtime chooses the
best provider for an operation and explains why a feature is unavailable."""
from __future__ import annotations

CAPABILITY_KEYS = (
    "observe_ui_tree",
    "observe_screenshot",
    "act_tap",
    "act_type",
    "act_key",
    "launch_app",
    "send_intent",
    "read_notifications",
    "reply_notifications",
    "read_clipboard",
    "write_clipboard",
    "write_secure_settings",
    "persistent_events",
    "requires_adb",
    "requires_accessibility",
    "requires_notification_listener",
)


def make(**flags) -> dict:
    unknown = set(flags) - set(CAPABILITY_KEYS)
    if unknown:
        raise ValueError(f"unknown capability keys: {sorted(unknown)}")
    return {key: bool(flags.get(key, False)) for key in CAPABILITY_KEYS}


def describe(caps: dict) -> str:
    have = [k for k in CAPABILITY_KEYS if caps.get(k)]
    miss = [k for k in CAPABILITY_KEYS if not caps.get(k)]
    return f"available: {', '.join(have) or '(none)'}\nunavailable: {', '.join(miss) or '(none)'}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_capabilities.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/capabilities.py tests/test_capabilities.py
git commit -m "feat: capability schema (CAPABILITY_KEYS/make/describe) per strategy §4.2"
```

---

### Task 4: `backend.py` — `Backend` Protocol + `AdbBackend.capabilities()`

**Files:**
- Create: `src/phonectl/backend.py`
- Modify: `src/phonectl/adb_backend.py` (add `capabilities()` after `get_state`, currently ends line 61)
- Test: `tests/test_backend.py`; `tests/test_adb_backend.py` (append)

**Interfaces:**
- Produces:
  - `backend.Backend` — a `typing.Protocol` documenting the backend-agnostic seam: `ui_dump`,
    `window_dump`, `wm_size`, `screencap`, `input_tap`, `input_text`, `input_swipe`, `input_key`,
    `launch`, `get_state`, and `capabilities`. (Folds the old accessibility-backend plan's Task 1; a
    future `A11yBackend` is a drop-in that satisfies the same Protocol.)
  - `AdbBackend.capabilities(self) -> dict` — returns `capabilities.make(...)` with ADB's truth values:
    `observe_ui_tree`/`observe_screenshot`/`act_tap`/`act_type`/`act_key`/`launch_app`/`send_intent`
    = True, `requires_adb` = True, the event/notification/clipboard/secure-settings keys = False.
- Consumes: `phonectl.capabilities` (Task 3).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_backend.py
from phonectl import backend, capabilities
from phonectl.adb_backend import AdbBackend


def test_adb_backend_satisfies_protocol_runtime_checkable():
    b = AdbBackend(serial="d")
    # Backend is a runtime_checkable Protocol; AdbBackend implements every method.
    assert isinstance(b, backend.Backend)


def test_adb_capabilities_shape_and_values():
    caps = AdbBackend(serial="d").capabilities()
    assert set(caps) == set(capabilities.CAPABILITY_KEYS)
    assert caps["observe_ui_tree"] is True
    assert caps["act_tap"] is True
    assert caps["send_intent"] is True
    assert caps["requires_adb"] is True
    assert caps["read_notifications"] is False
    assert caps["persistent_events"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_backend.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'phonectl.backend'`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/backend.py
"""Explicit backend seam. Any backend (AdbBackend today; A11yBackend, Termux,
Shizuku later) that implements these methods is a drop-in. Keeping this a
Protocol — not an ABC — preserves the duck-typed, injectable-runner test style."""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Backend(Protocol):
    serial: str | None

    def ui_dump(self) -> str: ...
    def window_dump(self) -> str: ...
    def wm_size(self) -> tuple[int, int]: ...
    def screencap(self, path: str) -> str: ...
    def input_tap(self, x: int, y: int) -> None: ...
    def input_text(self, text: str) -> None: ...
    def input_swipe(self, x1: int, y1: int, x2: int, y2: int, ms: int = 200) -> None: ...
    def input_key(self, keycode: str) -> None: ...
    def launch(self, package: str) -> None: ...
    def get_state(self) -> str: ...
    def capabilities(self) -> dict: ...
```

Append to the `AdbBackend` class in `src/phonectl/adb_backend.py` (and add
`from phonectl import capabilities` near the top imports):

```python
    def capabilities(self) -> dict:
        # ADB is a strong shell/intent/UI provider but has no event/notification
        # /clipboard/secure-settings powers without a companion APK (strategy §4.1).
        return capabilities.make(
            observe_ui_tree=True, observe_screenshot=True,
            act_tap=True, act_type=True, act_key=True,
            launch_app=True, send_intent=True, requires_adb=True,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_backend.py tests/test_adb_backend.py -v`
Expected: PASS (2 new backend tests + existing adb_backend tests).

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/backend.py src/phonectl/adb_backend.py tests/test_backend.py
git commit -m "feat: Backend Protocol seam + AdbBackend.capabilities() discovery"
```

---

### Task 5: CLI `--json` envelope + clean typed-error surfacing

Wire the structured result through the CLI as a demonstration surface (the richer MCP results land in
Plan 2.3). `observe --json` and `doctor --json` emit a `results.ok(...)` envelope; a top-level catch in
`main` maps any `PhonectlError` to `results.err(...)` JSON and a nonzero exit — never a traceback.

**Files:**
- Modify: `src/phonectl/cli.py` (add `--json` to `observe`/`doctor`; wrap `main` dispatch)
- Test: `tests/test_cli.py` (append)

**Interfaces:**
- Produces:
  - `observe --json` → prints `results.ok(capability="ui.observe", provider="adb", data=<snapshot>)`.
  - `doctor --json` → prints `results.ok(...)`/`results.err(...)` describing connectivity + capabilities.
  - `main` catches `errors.PhonectlError`, prints `json.dumps(results.err(e))` to stdout, returns
    `1` (no traceback). Non-`--json` paths keep today's plain-text behavior.
- Consumes: `results` (Task 2), `errors` (Task 1), `backend.capabilities()` (Task 4).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli.py  (append below existing tests)
import json as _json
from phonectl import cli, errors


def test_observe_json_emits_ok_envelope(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: FakeBackend())
    rc = cli.main(["observe", "--json"])
    out = _json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["ok"] is True
    assert out["capability"] == "ui.observe"
    assert out["provider"] == "adb"
    assert out["data"]["elements"][0]["text"] == "Wi-Fi"


def test_main_maps_phonectl_error_to_err_envelope(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))

    def boom(args):
        raise errors.DeviceLockedError("device is locked, unlock it")

    monkeypatch.setattr(cli, "_cmd_observe", boom)
    rc = cli.main(["observe", "--json"])
    out = _json.loads(capsys.readouterr().out)
    assert rc == 1
    assert out["ok"] is False
    assert out["error"]["code"] == "device_locked"
    assert out["error"]["requires_user"] is True
    assert "Traceback" not in capsys.readouterr().out
```

Note: `FakeBackend` is the existing test double in `tests/test_cli.py` (has `get_state`/`ui_dump`/
`window_dump`/`wm_size`/`input_tap`). The error-mapping test patches `_cmd_observe` to raise, proving the
`main`-level catch produces the envelope for ANY verb, independent of where the error originates.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli.py -v`
Expected: FAIL (`observe` has no `--json`; `main` does not catch `PhonectlError` into an envelope).

- [ ] **Step 3: Write minimal implementation**

In `src/phonectl/cli.py`: add `from phonectl import results, errors` to the imports; add `--json` to the
`observe` and `doctor` subparsers; branch `_cmd_observe`/`_cmd_doctor` on `args.json`; wrap `main`.

```python
# _cmd_observe (replace the emit branch)
def _cmd_observe(args):
    cfg = config.load()
    backend, session, conn = build_runtime(cfg)
    conn.ensure()
    snap = observer.observe(backend, session, screenshot=args.screenshot,
                            snap_path=args.screenshot_path)
    if getattr(args, "json", False):
        print(json.dumps(results.ok(capability="ui.observe", provider="adb", data=snap),
                         indent=2))
    else:
        _emit(snap)
    return 0
```

```python
# build_parser: add to the observe and doctor subparsers
    o.add_argument("--json", action="store_true")
    ...
    d.add_argument("--json", action="store_true")
```

```python
# main: wrap dispatch so typed errors surface as an envelope (json) or one line (plain)
def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "func", None) is None:
        parser.print_help()
        return 0
    try:
        return args.func(args)
    except errors.PhonectlError as e:
        if getattr(args, "json", False):
            print(json.dumps(results.err(e), indent=2))
        else:
            print(f"phonectl: {e}")
        return 1
```

For `doctor --json`, return `results.ok(provider="adb", data={"connected": True, "serial": ...,
"capabilities": backend.capabilities()})` on success, or `results.err(("connection_failed", str(e)),
user_action=GUIDANCE)` when `conn.ensure()` raises `ConnectionError`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli.py -v`
Expected: PASS (existing tests + 2 new).

- [ ] **Step 5: Run the full suite**

Run: `pytest -v`
Expected: PASS (errors, results, capabilities, backend, adb_backend, cli, and all prior tests).

- [ ] **Step 6: Commit**

```bash
git add src/phonectl/cli.py tests/test_cli.py
git commit -m "feat: CLI --json result envelope and traceback-free typed-error surfacing"
```

---

### Task 6: Docs — capability schema, error-code table, result envelope

**Files:**
- Modify: `README.md` (add a "Structured results & capabilities" section)
- Modify: `docs/superpowers/specs/2026-06-20-phonectl-adb-bridge-design.md` (note the new invariant)

**Interfaces:** none (documentation).

- [ ] **Step 1: Document the contract**

In `README.md`: the result envelope shape (`ok`/`err`), the stable error-code table
(`device_locked`/`stale_snapshot`/`capability_unavailable`/`guarded_action`/`rate_limited`/
`observe_failed` with their `retryable`/`requires_user` flags), the capability keys, and example
`observe --json`/`doctor --json` output. In the design spec, add a short note under the safety/contract
section that **structured results are now an invariant** (roadmap §4) and that `errors.py`/`results.py`/
`capabilities.py` are the canonical homes.

- [ ] **Step 2: Commit**

```bash
git add README.md docs/superpowers/specs/2026-06-20-phonectl-adb-bridge-design.md
git commit -m "docs: structured-result envelope, error-code table, and capability schema"
```

---

## Dependencies

This is **Plan 1.1 of the platform roadmap** and builds only on the shipped observe→act core. It
**creates the canonical `errors.py`, `results.py`, `capabilities.py`, and `backend.py`**. Downstream:

- **Plan 1.2** (selectors) raises `errors.StaleSnapshotError` (defined here) and uses the `results`
  envelope in its CLI `--json` path.
- **Plan 1.3** (resilience) raises `errors.ObserveError`/`DeviceLockedError` from the observe retry/lock
  loop (the *behavior*; the *classes* are here).
- **Plan 2.2** (risk ledger) raises `errors.GuardedActionError`/`RateLimitError`.
- **Plan 2.3** (MCP) returns the `results` envelope from every tool and exposes a `phonectl_capabilities`
  tool over `backend.capabilities()`.

## Deferred / out of scope (not in this plan)

- The **behaviors** that raise the new errors (observe retry/settle/lock guard, stale-snapshot check,
  rate-limit/guarded enforcement) — each lands in its own plan importing these classes.
- The **provider graph** that picks a provider per capability (Plan 3.1) — this plan ships single-backend
  `capabilities()`; the composite runtime comes later.
- The richer MCP structured results and `policy.explain` tool (Plan 2.3 / 2.2).

## Notes on testability

Every module here is pure data/structure and is unit-tested with no device and no I/O. The only file with
device-shaped behavior, `AdbBackend.capabilities()`, returns a static dict (no subprocess) and is tested
directly. The CLI `--json` and error-mapping paths are tested with the existing `FakeBackend` and a
monkeypatched command, so no real `adb` or wall-clock is involved.
