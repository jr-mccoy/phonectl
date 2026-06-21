> **SUPERSEDED 2026-06-22** — folded into `docs/superpowers/phonectl-platform-roadmap.md` (Phase 4.1; the `Backend` Protocol seam was pulled forward to Phase 1.1). Task-level re-homing is in `docs/superpowers/plans/2026-06-22-phonectl-remaining-plans-meta-plan.md`. Kept for traceability; do not execute as-is.

# phonectl AccessibilityService APK Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a second, event-driven `A11yBackend` (Python) that speaks the existing backend-agnostic interface by talking to an on-device AccessibilityService APK over `am broadcast`, so `observer`/`actuator`/`cli` can read the screen and act *without* a `uiautomator dump` — the robust path for continuous/unattended use.

**Architecture:** Today the backend is an *implicit* duck-typed object (`AdbBackend`). This plan makes the contract *explicit* as a documented `typing.Protocol` (`src/phonectl/backend.py`) with zero runtime cost and zero restructuring of the existing module, then implements `A11yBackend` (`src/phonectl/a11y_backend.py`) against that same Protocol. `A11yBackend` reuses the existing `runner`/serial seam and the *same* `am broadcast` → result-file transport for every method; it emits the **same uiautomator-format XML** the service captures so `ui_parser` is reused verbatim. The Android APK itself (the AccessibilityService that produces that XML and dispatches gestures) is a Kotlin/Java deliverable and is **not** TDD-able in Python — it is scoped at design altitude only.

**Tech Stack:** Python 3 (stdlib only: `typing.Protocol`, `subprocess`, `xml.etree`, `json`), `pytest` for tests, `adb` (android-tools) as the only external runtime dependency. The APK is a separate Android (Kotlin/Java) artifact built with the Android SDK/Gradle — out of scope for Python TDD and tracked as its own spec.

## Global Constraints

- stdlib-only at runtime (Python >=3.9; pytest dev-only).
- ONLY `adb_backend.py` may touch adb/subprocess. **This plan adds one more module that touches the device — `a11y_backend.py` — and that is a deliberate, documented extension of the backend-isolation rule:** the rule's intent is "device I/O is confined to *backend* modules implementing the `Backend` Protocol, never leaking into `observer`/`actuator`/`session`/`cli`." `a11y_backend.py` is such a backend. No new device I/O appears anywhere above the backend layer.
- `ui_parser.py` stays pure (no I/O). The a11y node-tree → element mapping reuses `ui_parser` by having the APK emit uiautomator-format XML; any new parsing helper added here is also pure.
- element index `i` is the primary target. `A11yBackend` produces the *same* element shape (via `ui_parser`) so indices resolve identically across backends.
- every actuator `act()` re-observes. Unchanged: `actuator` calls `observer.observe(backend, ...)` regardless of which backend is injected.
- tests isolate via `monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))`.
- one commit per task.
- TDD order is non-negotiable.

## Dependencies

- **Sequenced after plan 1 (resilience).** This plan's `Backend` Protocol enumerates the optional `wake()` and `mdns_services()` methods that the resilience plan adds. Tasks below are written **defensively**: the Protocol marks those two methods as optional (documented but not required by `observer`/`actuator`), and the `A11yBackend` tests do not assume resilience landed. If plan 1 has not landed, the Protocol still type-checks (the optional methods simply have no implementer in the a11y path yet) and every task here passes. No code in this plan imports resilience internals.
- **Independent of plans 2 (safety), 3 (setup), 4 (mcp).** `A11yBackend` is selected by a `config.json` `"backend"` key (default `"adb"`); it composes with the existing `cli`/safety/mode machinery unchanged because it satisfies the same Protocol.

## File Structure (added/changed by this plan)

```
phonectl/
├── pyproject.toml                      # MODIFY: add optional extra [a11y] is NOT needed (stdlib only) — see note
├── src/phonectl/
│   ├── backend.py                      # NEW: Backend Protocol (typing.Protocol) — the explicit seam
│   ├── a11y_backend.py                 # NEW: A11yBackend over am-broadcast transport
│   └── cli.py                          # MODIFY: _make_backend() selects backend by config["backend"]
├── tests/
│   ├── test_backend_protocol.py        # NEW: AdbBackend & A11yBackend both satisfy the Protocol
│   ├── test_a11y_backend.py            # NEW: fake runner asserts exact broadcast protocol
│   └── test_cli.py                     # MODIFY: backend selection test
└── android/                            # NEW (design docs only this plan): APK lives here
    └── README.md                       # NEW: points to the Android spec; no Kotlin code in THIS plan
```

> **No third-party runtime dep is introduced.** `typing.Protocol` is stdlib on 3.9 (via `typing`, available since 3.8). The transport is plain `adb` text I/O through the existing `runner`. The APK is built with the Android toolchain entirely outside the Python package, so it is neither a `pyproject` dependency nor an optional extra.

---

### Task 1: `backend.py` — make the backend contract explicit as a Protocol

**Files:**
- Create: `src/phonectl/backend.py`
- Test: `tests/test_backend_protocol.py`

**Interfaces:**
- Consumes: nothing (pure typing module; no I/O, no imports of device code at module top beyond `typing`).
- Produces: `Backend` (a `typing.Protocol`, `@runtime_checkable`) documenting the methods `observer`/`actuator` require:
  - `ui_dump(self) -> str`
  - `window_dump(self) -> str`
  - `wm_size(self) -> tuple[int, int]`
  - `screencap(self, path: str) -> str`
  - `input_tap(self, x: int, y: int) -> None`
  - `input_text(self, text: str) -> None`
  - `input_swipe(self, x1: int, y1: int, x2: int, y2: int, ms: int = 200) -> None`
  - `input_key(self, keycode: str) -> None`
  - `launch(self, package: str) -> None`
  - `get_state(self) -> str`
  - Optional (documented in the docstring, NOT in the `@runtime_checkable` required set so older/partial backends still pass): `wake(self) -> None` and `mdns_services(self) -> list[str]` — added by the resilience plan; `A11yBackend` delegates these to adb.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_backend_protocol.py
from phonectl.backend import Backend, REQUIRED_METHODS
from phonectl.adb_backend import AdbBackend


def test_required_methods_list_is_the_documented_contract():
    # The explicit seam: these are exactly the methods observer/actuator call.
    assert REQUIRED_METHODS == (
        "ui_dump", "window_dump", "wm_size", "screencap",
        "input_tap", "input_text", "input_swipe", "input_key",
        "launch", "get_state",
    )


def test_adb_backend_satisfies_protocol_at_runtime():
    b = AdbBackend(serial="d", runner=lambda *a, **k: None)
    assert isinstance(b, Backend)


def test_partial_object_missing_a_method_is_not_a_backend():
    class Missing:  # lacks get_state
        def ui_dump(self): ...
        def window_dump(self): ...
        def wm_size(self): ...
        def screencap(self, path): ...
        def input_tap(self, x, y): ...
        def input_text(self, text): ...
        def input_swipe(self, x1, y1, x2, y2, ms=200): ...
        def input_key(self, keycode): ...
        def launch(self, package): ...
    assert not isinstance(Missing(), Backend)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_backend_protocol.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'phonectl.backend'`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/backend.py
from __future__ import annotations

from typing import Protocol, runtime_checkable

# The explicit contract that observer/actuator/session/cli depend on.
# Any backend (AdbBackend today, A11yBackend next, a future cloud backend)
# MUST implement every name here. Optional resilience methods (wake,
# mdns_services) are documented in the Protocol body but intentionally
# excluded from REQUIRED_METHODS / runtime isinstance so a backend that
# predates the resilience plan still type-checks.
REQUIRED_METHODS = (
    "ui_dump", "window_dump", "wm_size", "screencap",
    "input_tap", "input_text", "input_swipe", "input_key",
    "launch", "get_state",
)


@runtime_checkable
class Backend(Protocol):
    """Backend-agnostic device interface.

    observer.observe() calls: ui_dump, window_dump, wm_size, screencap.
    actuator verbs call: input_tap, input_text, input_swipe, input_key, launch.
    connection.ensure() calls: get_state.

    Optional (added by the resilience plan; not required for isinstance):
        wake(self) -> None
        mdns_services(self) -> list[str]
    """

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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_backend_protocol.py -v`
Expected: PASS (3 tests). Also run `pytest -v` to confirm no regression in the existing suite.

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/backend.py tests/test_backend_protocol.py
git commit -m "feat: explicit Backend Protocol documenting the backend-agnostic seam"
```

---

### Task 2: `a11y_backend` transport core — `am broadcast` request + result-file read

**Files:**
- Create: `src/phonectl/a11y_backend.py`
- Test: `tests/test_a11y_backend.py`

**Interfaces:**
- Consumes: an injectable `runner` (defaults to `subprocess.run`), identical to `AdbBackend`, so unit tests never touch a real device or APK.
- Produces: class `A11yBackend`
  - `__init__(self, serial: str | None = None, runner=subprocess.run, result_path: str = "/sdcard/phonectl-a11y.json")`
  - `_adb(self, *args: str) -> str` — runs `adb [-s serial] *args`, returns `res.stdout` (same shape as `AdbBackend._adb`).
  - `_call(self, action: str, extras: dict[str, str] | None = None) -> dict` — the transport primitive:
    1. fire `adb shell am broadcast -a com.phonectl.a11y.ACTION_<ACTION> [--es <k> <v> ...]` (each extra via `--es key shlex.quote(value)`),
    2. read the service's JSON reply back with `adb exec-out cat <result_path>`,
    3. `json.loads` it and return the dict. Raises `A11yTransportError` if the broadcast stdout lacks `result=0` (the service ACK) or the result file is not valid JSON.

**Transport choice & justification (read before coding):** Two transports were on the table — (A) `am broadcast` to the service plus a result file read back over `adb exec-out cat`, or (B) a localhost TCP socket the service exposes. **We choose (A).** It (1) reuses the *exact* `runner`/serial seam already proven in `AdbBackend`, so the fake-runner test style ports directly and no new I/O primitive (sockets) enters the codebase; (2) needs no extra listening port and no assumption about loopback reachability from inside PRoot beyond what `adb` already provides; (3) keeps every device byte flowing through `adb` stdout, so the backend-isolation story is unchanged. The cost — a round trip through a result file — is acceptable because the win of this backend is *event-driven reads without a uiautomator dump*, not raw latency. (B) remains a documented future option behind the same `A11yBackend` facade if latency ever dominates.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_a11y_backend.py
import json
import pytest
from phonectl.a11y_backend import A11yBackend, A11yTransportError


class FakeCompleted:
    def __init__(self, stdout="", stdout_bytes=b"", returncode=0):
        self.stdout = stdout
        self._bytes = stdout_bytes
        self.returncode = returncode


def make_runner(record, broadcast_stdout="Broadcast completed: result=0\n",
                result_json="{}"):
    """Records (cmd, kwargs). Returns the broadcast ACK for `am broadcast`
    calls and the JSON reply for `exec-out cat` calls."""
    def runner(cmd, **kwargs):
        record.append((cmd, kwargs))
        if "cat" in cmd:
            return FakeCompleted(stdout=result_json)
        return FakeCompleted(stdout=broadcast_stdout)
    return runner


def test_call_broadcasts_action_and_reads_result_file():
    calls = []
    b = A11yBackend(serial="d",
                    runner=make_runner(calls, result_json='{"ok": true}'))
    out = b._call("PING")
    assert out == {"ok": True}
    broadcast_cmd = calls[0][0]
    assert broadcast_cmd == ["adb", "-s", "d", "shell", "am", "broadcast",
                             "-a", "com.phonectl.a11y.ACTION_PING"]
    cat_cmd = calls[1][0]
    assert cat_cmd == ["adb", "-s", "d", "exec-out", "cat",
                       "/sdcard/phonectl-a11y.json"]


def test_call_passes_extras_as_es_pairs():
    import shlex
    calls = []
    b = A11yBackend(serial="d", runner=make_runner(calls))
    b._call("SET_TEXT", {"text": "a b$c"})
    broadcast_cmd = calls[0][0]
    assert broadcast_cmd == ["adb", "-s", "d", "shell", "am", "broadcast",
                             "-a", "com.phonectl.a11y.ACTION_SET_TEXT",
                             "--es", "text", shlex.quote("a b$c")]


def test_call_raises_when_service_does_not_ack():
    calls = []
    b = A11yBackend(serial="d",
                    runner=make_runner(calls, broadcast_stdout="Broadcast completed: result=-1\n"))
    with pytest.raises(A11yTransportError):
        b._call("PING")


def test_call_raises_on_non_json_result():
    calls = []
    b = A11yBackend(serial="d", runner=make_runner(calls, result_json="not json"))
    with pytest.raises(A11yTransportError):
        b._call("PING")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_a11y_backend.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'phonectl.a11y_backend'`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/a11y_backend.py
import json
import shlex
import subprocess

ACTION_PREFIX = "com.phonectl.a11y.ACTION_"


class A11yTransportError(RuntimeError):
    """The on-device AccessibilityService did not ACK or replied with junk."""


class A11yBackend:
    def __init__(self, serial=None, runner=subprocess.run,
                 result_path="/sdcard/phonectl-a11y.json"):
        self.serial = serial
        self._runner = runner
        self._result_path = result_path

    def _base(self) -> list[str]:
        return ["adb", "-s", self.serial] if self.serial else ["adb"]

    def _adb(self, *args: str) -> str:
        cmd = self._base() + list(args)
        res = self._runner(cmd, capture_output=True, text=True)
        return res.stdout

    def _call(self, action: str, extras=None) -> dict:
        cmd = ["shell", "am", "broadcast", "-a", ACTION_PREFIX + action]
        for k, v in (extras or {}).items():
            cmd += ["--es", k, shlex.quote(str(v))]
        ack = self._adb(*cmd)
        if "result=0" not in ack:
            raise A11yTransportError(
                f"a11y service did not ACK action {action}: {ack!r}")
        raw = self._adb("exec-out", "cat", self._result_path)
        try:
            return json.loads(raw)
        except (ValueError, TypeError) as e:
            raise A11yTransportError(
                f"a11y result for {action} was not JSON: {raw!r}") from e
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_a11y_backend.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/a11y_backend.py tests/test_a11y_backend.py
git commit -m "feat: A11yBackend transport core (am broadcast + result-file read)"
```

---

### Task 3: `A11yBackend` observe-side methods — `ui_dump`/`window_dump`/`wm_size`/`screencap`

**Files:**
- Modify: `src/phonectl/a11y_backend.py` (add observe-side methods after `_call`, around line 30)
- Test: `tests/test_a11y_backend.py` (extend)

**Interfaces:**
- Consumes: `A11yBackend._call` (Task 2).
- Produces (each maps the service reply onto the existing observer contract; the service emits **uiautomator-format XML** so `ui_parser` is reused unchanged):
  - `ui_dump(self) -> str` — `_call("DUMP")["xml"]`. The service serializes its `AccessibilityNodeInfo` tree into the same `<hierarchy><node .../></hierarchy>` XML `uiautomator dump` produces, so `ui_parser.parse_elements` consumes it verbatim.
  - `window_dump(self) -> str` — synthesizes a `mCurrentFocus=Window{... pkg/activity}` line from `_call("DUMP")` keys `package`/`activity`, so the existing `observer.parse_focused_app` regex matches without change.
  - `wm_size(self) -> tuple[int, int]` — `(_call("SIZE")["w"], _call("SIZE")["h"])`.
  - `screencap(self, path: str) -> str` — the a11y service cannot capture pixels without MediaProjection consent; delegate to adb (`exec-out screencap -p`) via a private `_adb_bytes`, identical to `AdbBackend`. Documented: screenshots remain the adb path; the a11y win is the *element tree*, not pixels.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_a11y_backend.py  (append)
from phonectl import ui_parser, observer

A11Y_XML = ("<?xml version='1.0'?><hierarchy rotation=\"0\">"
            "<node index=\"0\" text=\"Wi-Fi\" resource-id=\"android:id/title\" "
            "class=\"TextView\" content-desc=\"\" clickable=\"true\" "
            "bounds=\"[44,380][1036,520]\"/></hierarchy>")


def make_dump_runner(record, dump_reply):
    def runner(cmd, **kwargs):
        record.append((cmd, kwargs))
        if "cat" in cmd:
            return FakeCompleted(stdout=json.dumps(dump_reply))
        return FakeCompleted(stdout="Broadcast completed: result=0\n")
    return runner


def test_ui_dump_returns_service_xml_consumable_by_ui_parser():
    calls = []
    reply = {"xml": A11Y_XML, "package": "com.android.settings",
             "activity": ".Settings"}
    b = A11yBackend(serial="d", runner=make_dump_runner(calls, reply))
    xml = b.ui_dump()
    els = ui_parser.parse_elements(xml)   # reuse the PURE parser unchanged
    assert els[0]["text"] == "Wi-Fi"
    assert els[0]["center"] == [540, 450]


def test_window_dump_synthesizes_focus_line_parseable_by_observer():
    calls = []
    reply = {"xml": A11Y_XML, "package": "com.android.settings",
             "activity": ".Settings"}
    b = A11yBackend(serial="d", runner=make_dump_runner(calls, reply))
    app = observer.parse_focused_app(b.window_dump())
    assert app == {"package": "com.android.settings", "activity": ".Settings"}


def test_wm_size_reads_size_action():
    calls = []
    def runner(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if "cat" in cmd:
            return FakeCompleted(stdout=json.dumps({"w": 1080, "h": 2400}))
        return FakeCompleted(stdout="Broadcast completed: result=0\n")
    b = A11yBackend(serial="d", runner=runner)
    assert b.wm_size() == (1080, 2400)


def test_screencap_delegates_to_adb_bytes(tmp_path):
    calls = []
    png = b"\x89PNG\r\n\x1a\nFAKE"
    def runner(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return FakeCompleted(stdout_bytes=png)
    b = A11yBackend(serial="d", runner=runner)
    dest = str(tmp_path / "snap.png")
    assert b.screencap(dest) == dest
    assert (tmp_path / "snap.png").read_bytes() == png
    assert calls[0][0] == ["adb", "-s", "d", "exec-out", "screencap", "-p"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_a11y_backend.py -v`
Expected: FAIL (`AttributeError: 'A11yBackend' object has no attribute 'ui_dump'`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/a11y_backend.py  (add after _call)

    def _adb_bytes(self, *args: str) -> bytes:
        cmd = self._base() + list(args)
        res = self._runner(cmd, capture_output=True)
        return res._bytes if hasattr(res, "_bytes") else res.stdout

    def ui_dump(self) -> str:
        return self._call("DUMP")["xml"]

    def window_dump(self) -> str:
        reply = self._call("DUMP")
        pkg = reply.get("package", "")
        act = reply.get("activity", "")
        # Synthesize the line observer.parse_focused_app expects.
        return f"mCurrentFocus=Window{{0 u0 {pkg}/{act}}}"

    def wm_size(self) -> tuple[int, int]:
        reply = self._call("SIZE")
        return (int(reply["w"]), int(reply["h"]))

    def screencap(self, path: str) -> str:
        # Pixels still come from adb; the a11y service has no FLAG_SECURE /
        # MediaProjection-free capture. The element tree is the a11y win.
        data = self._adb_bytes("exec-out", "screencap", "-p")
        with open(path, "wb") as f:
            f.write(data)
        return path
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_a11y_backend.py -v`
Expected: PASS (8 tests total in this file).

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/a11y_backend.py tests/test_a11y_backend.py
git commit -m "feat: A11yBackend observe-side methods (ui_dump/window_dump/wm_size/screencap)"
```

---

### Task 4: `A11yBackend` act-side methods — `input_tap`/`input_text`/`input_swipe`/`input_key`/`launch`/`get_state`

**Files:**
- Modify: `src/phonectl/a11y_backend.py` (add act-side methods after `screencap`)
- Test: `tests/test_a11y_backend.py` (extend)

**Interfaces:**
- Consumes: `A11yBackend._call` and `A11yBackend._adb` (Tasks 2-3).
- Produces (the service performs gestures via `dispatchGesture` and text via `ACTION_SET_TEXT`):
  - `input_tap(self, x: int, y: int) -> None` — `_call("TAP", {"x": x, "y": y})`.
  - `input_text(self, text: str) -> None` — `_call("SET_TEXT", {"text": text})`. (The service sets text on the focused node; the actuator's redaction/audit story is unchanged.)
  - `input_swipe(self, x1, y1, x2, y2, ms: int = 200) -> None` — `_call("SWIPE", {"x1":…, "y1":…, "x2":…, "y2":…, "ms":…})`.
  - `input_key(self, keycode: str) -> None` — global actions (`KEYCODE_BACK`/`HOME`/`APP_SWITCH`) map to `performGlobalAction`; delegate to `_call("KEY", {"keycode": keycode})`.
  - `launch(self, package: str) -> None` — **delegates to adb** (`shell monkey -p … LAUNCHER 1`), matching `AdbBackend.launch`, because launching by package is a system capability the a11y service lacks (design spec §4.2 explicitly notes `am start`/`launch` is "unavailable to the a11y path"). Documented as the one verb that always uses adb.
  - `get_state(self) -> str` — `self._adb("get-state").strip()`; connection health is an adb-transport concern, not the service's.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_a11y_backend.py  (append)

def test_input_tap_broadcasts_tap_action():
    calls = []
    b = A11yBackend(serial="d", runner=make_runner(calls))
    b.input_tap(540, 450)
    assert calls[0][0] == ["adb", "-s", "d", "shell", "am", "broadcast",
                           "-a", "com.phonectl.a11y.ACTION_TAP",
                           "--es", "x", "540", "--es", "y", "450"]


def test_input_text_broadcasts_set_text():
    import shlex
    calls = []
    b = A11yBackend(serial="d", runner=make_runner(calls))
    b.input_text("hello world")
    assert calls[0][0] == ["adb", "-s", "d", "shell", "am", "broadcast",
                           "-a", "com.phonectl.a11y.ACTION_SET_TEXT",
                           "--es", "text", shlex.quote("hello world")]


def test_input_key_broadcasts_keycode():
    calls = []
    b = A11yBackend(serial="d", runner=make_runner(calls))
    b.input_key("KEYCODE_BACK")
    assert calls[0][0] == ["adb", "-s", "d", "shell", "am", "broadcast",
                           "-a", "com.phonectl.a11y.ACTION_KEY",
                           "--es", "keycode", "KEYCODE_BACK"]


def test_launch_delegates_to_adb_monkey():
    calls = []
    b = A11yBackend(serial="d", runner=make_runner(calls))
    b.launch("com.android.settings")
    # launch is the system-level escape hatch the a11y service cannot do;
    # it goes straight to adb, NOT through am broadcast.
    assert calls[0][0] == ["adb", "-s", "d", "shell", "monkey", "-p",
                           "com.android.settings", "-c",
                           "android.intent.category.LAUNCHER", "1"]


def test_get_state_delegates_to_adb():
    calls = []
    b = A11yBackend(serial="d", runner=make_runner(calls, broadcast_stdout="device\n"))
    # get-state is captured-text adb; make_runner returns broadcast_stdout for
    # non-cat commands, which here stands in for the get-state output.
    assert b.get_state() == "device"
    assert calls[0][0] == ["adb", "-s", "d", "get-state"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_a11y_backend.py -v`
Expected: FAIL (`AttributeError: 'A11yBackend' object has no attribute 'input_tap'`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/a11y_backend.py  (add after screencap)

    def input_tap(self, x: int, y: int) -> None:
        self._call("TAP", {"x": x, "y": y})

    def input_text(self, text: str) -> None:
        self._call("SET_TEXT", {"text": text})

    def input_swipe(self, x1, y1, x2, y2, ms: int = 200) -> None:
        self._call("SWIPE", {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "ms": ms})

    def input_key(self, keycode: str) -> None:
        self._call("KEY", {"keycode": keycode})

    def launch(self, package: str) -> None:
        # System-level launch is unavailable to the a11y service (design §4.2);
        # use adb directly, matching AdbBackend.launch.
        self._adb("shell", "monkey", "-p", package,
                  "-c", "android.intent.category.LAUNCHER", "1")

    def get_state(self) -> str:
        return self._adb("get-state").strip()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_a11y_backend.py -v`
Expected: PASS. Then confirm the Protocol test from Task 1 now also accepts `A11yBackend`:

```python
# tests/test_backend_protocol.py  (append)
from phonectl.a11y_backend import A11yBackend

def test_a11y_backend_satisfies_protocol_at_runtime():
    b = A11yBackend(serial="d", runner=lambda *a, **k: None)
    assert isinstance(b, Backend)
```

Run: `pytest tests/test_backend_protocol.py tests/test_a11y_backend.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/a11y_backend.py tests/test_a11y_backend.py tests/test_backend_protocol.py
git commit -m "feat: A11yBackend act-side methods; prove it satisfies the Backend Protocol"
```

---

### Task 5: `cli` backend selection — choose AdbBackend or A11yBackend from config

**Files:**
- Modify: `src/phonectl/cli.py` (the `_make_backend` factory, currently at lines 22-23: `def _make_backend(cfg) -> AdbBackend: return AdbBackend(serial=cfg.get("serial"))`)
- Test: `tests/test_cli.py` (extend)

**Interfaces:**
- Consumes: `config.load()` shape; adds an optional `"backend"` key (`"adb"` default, or `"a11y"`). No collision with existing `"serial"`/`"mode"` or the resilience `"last_port"` / safety `"rate_limit_per_min"`/`"guarded_packages"` keys.
- Produces: `_make_backend(cfg) -> Backend` returns `A11yBackend(serial=cfg.get("serial"))` when `cfg.get("backend") == "a11y"`, else `AdbBackend(serial=cfg.get("serial"))`. All other CLI machinery (modes, audit, kill-switch, `build_runtime`) is untouched because both satisfy the Protocol.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli.py  (append below existing tests)
from phonectl.adb_backend import AdbBackend
from phonectl.a11y_backend import A11yBackend


def test_make_backend_defaults_to_adb():
    b = cli._make_backend({"serial": "127.0.0.1:5555"})
    assert isinstance(b, AdbBackend)
    assert b.serial == "127.0.0.1:5555"


def test_make_backend_selects_a11y_when_configured():
    b = cli._make_backend({"serial": "127.0.0.1:5555", "backend": "a11y"})
    assert isinstance(b, A11yBackend)
    assert b.serial == "127.0.0.1:5555"


def test_observe_uses_a11y_backend_end_to_end(tmp_path, monkeypatch, capsys):
    import json as _json
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))

    # A fake runner driving a REAL A11yBackend through cli.observe, proving the
    # backend swap is transparent to observer/cli.
    a11y_xml = ("<?xml version='1.0'?><hierarchy rotation=\"0\">"
                "<node index=\"0\" text=\"Wi-Fi\" resource-id=\"android:id/title\" "
                "class=\"TextView\" content-desc=\"\" clickable=\"true\" "
                "bounds=\"[44,380][1036,520]\"/></hierarchy>")

    class _Completed:
        def __init__(self, stdout): self.stdout = stdout

    def runner(cmd, **kwargs):
        if "get-state" in cmd:
            return _Completed("device\n")
        if "cat" in cmd:
            if cmd[-1].endswith("phonectl-a11y.json"):
                # DUMP/SIZE share the same result file in this fake; serve a
                # superset payload that satisfies both ui_dump and wm_size.
                return _Completed(_json.dumps(
                    {"xml": a11y_xml, "package": "com.x", "activity": ".A",
                     "w": 1080, "h": 2400}))
        return _Completed("Broadcast completed: result=0\n")

    real = A11yBackend(serial="d", runner=runner)
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: real)
    rc = cli.main(["observe"])
    data = _json.loads(capsys.readouterr().out)
    assert rc == 0
    assert data["elements"][0]["text"] == "Wi-Fi"
    assert data["app"]["package"] == "com.x"
    assert data["screen"] == {"w": 1080, "h": 2400, "orientation": "portrait"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli.py -v`
Expected: FAIL (`test_make_backend_selects_a11y_when_configured` returns an `AdbBackend`; `_make_backend` does not branch on `"backend"`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/cli.py  — replace the existing _make_backend
from phonectl.a11y_backend import A11yBackend   # add to the import block

def _make_backend(cfg):
    if cfg.get("backend") == "a11y":
        return A11yBackend(serial=cfg.get("serial"))
    return AdbBackend(serial=cfg.get("serial"))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli.py -v`
Expected: PASS. Then run the full suite: `pytest -v` — Expected: PASS (all files, no regression).

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/cli.py tests/test_cli.py
git commit -m "feat: select AdbBackend or A11yBackend from config['backend']"
```

---

### Task 6: Android APK — design-altitude spec only (NO Python TDD applies)

**Files:**
- Create: `android/README.md` (a design-altitude pointer; **no Kotlin code is written or tested in this plan**)

**Interfaces:** none at the Python level. This task documents the native artifact the Python `A11yBackend` talks to and records that it **requires its own Android (Kotlin/Java) spec + brainstorm before any code is written.**

> **HONESTY NOTE — this task is intentionally NOT test-driven.** The APK is an Android `AccessibilityService` in Kotlin/Java, built with the Android SDK/Gradle and exercised on a device with instrumentation tests (Espresso/UiAutomator), JUnit on the JVM, or manual device runs — **none of which is Python-level TDD.** Fabricating "write a failing Kotlin test" steps here would be dishonest. The Python side (Tasks 1-5) is the concrete, fully-TDD'd, mergeable deliverable; this task only fixes the contract the APK must honor and defers its implementation to a dedicated Android spec.

**What the APK must implement (the contract the fully-tested `A11yBackend` already assumes):**

1. **AccessibilityService** declared in the manifest with `android.accessibilityservice.AccessibilityService`, `canRetrieveWindowContent="true"`, and (for gestures) `android:canPerformGestures="true"`. The user enables it once in *Settings → Accessibility* (the a11y analogue of the one-time Wireless-Debugging pairing).

2. **A `BroadcastReceiver`** registered for the `com.phonectl.a11y.ACTION_*` actions the Python transport sends:
   - `ACTION_DUMP` → walk `getRootInActiveWindow()` and serialize the `AccessibilityNodeInfo` tree into **uiautomator-format XML** (`<hierarchy rotation=…><node text=… resource-id=… class=… content-desc=… clickable=… bounds="[l,t][r,b]"/>…</hierarchy>`). **Emitting that exact format is what lets the Python side reuse `ui_parser` verbatim** — the single most important constraint on the APK. Also include the focused `package`/`activity` in the reply JSON.
   - `ACTION_SIZE` → reply `{"w":…, "h":…}` from the display metrics.
   - `ACTION_TAP {x,y}` / `ACTION_SWIPE {x1,y1,x2,y2,ms}` → `dispatchGesture(...)` with a built `GestureDescription`.
   - `ACTION_SET_TEXT {text}` → `AccessibilityNodeInfo.performAction(ACTION_SET_TEXT, bundle)` on the focused/editable node.
   - `ACTION_KEY {keycode}` → map `KEYCODE_BACK`/`KEYCODE_HOME`/`KEYCODE_APP_SWITCH` to `performGlobalAction(GLOBAL_ACTION_BACK/HOME/RECENTS)`.
   - Every handler writes its JSON reply to the agreed result file (`/sdcard/phonectl-a11y.json`, app-writable scoped path in the real build) and the broadcast returns `result=0` as the ACK the Python `_call` checks.

3. **Why this backend is the robust path (state this in the README):** it reads the screen from live accessibility events **without invoking `uiautomator dump`** — so it never hits the `ERROR: could not get idle state` failure that bites the adb path on animated/asleep screens (resilience spec §2.2), and it is **event-driven** (the service already holds the node tree), making it the better backend for continuous/unattended operation. `launch` and `get_state` still defer to adb because those are system-level capabilities outside the service's reach (design §4.2).

**Android-side task breakdown (for the future Android spec — listed, not executed here):**
- A1. Gradle module + manifest + `accessibility_service_config.xml`; enable-flow UX.
- A2. Node-tree → uiautomator-XML serializer (the format-fidelity crux; will need device-captured fixtures compared byte-for-shape against `uiautomator dump`).
- A3. `dispatchGesture` tap/swipe + `ACTION_SET_TEXT` + global-action key mapping.
- A4. `BroadcastReceiver` wiring, result-file writer, ACK semantics.
- A5. Instrumented device tests (Espresso/UiAutomator) — **Android test tooling, not Python pytest.**

Each of A1-A5 **requires its own Android (Kotlin/Java) spec + brainstorm before coding; no Python-level TDD applies.**

- [ ] **Step 1: Write `android/README.md`** capturing the contract above (the `ACTION_*` protocol table, the result-file path, the uiautomator-XML fidelity requirement, the `launch`/`get_state`-defer-to-adb note, and the "needs its own Android spec" banner). This is documentation; there is no test step because there is no Python code in this task.

- [ ] **Step 2: Commit**

```bash
git add android/README.md
git commit -m "docs: AccessibilityService APK contract for A11yBackend (Android spec deferred)"
```

---

## Notes on invariants preserved

- **Backend isolation holds.** Device I/O lives only in `adb_backend.py` and `a11y_backend.py` — both `Backend`-Protocol backends. `observer`/`actuator`/`session`/`cli` gained zero device calls; `cli` only learned to *choose* a backend.
- **`ui_parser` stays pure and reused.** The APK emits uiautomator-format XML precisely so `parse_elements` is consumed unchanged; no new I/O entered `ui_parser`.
- **Every `act()` still re-observes.** `actuator` is untouched; with `A11yBackend` injected, `observe()` reads the service's node tree instead of a `uiautomator dump`, but the observe→act→observe shape is identical.
- **Index `i` stays primary.** Same element shape via `ui_parser`, so `session.resolve(i)` works identically across backends.
- **Stdlib only.** `typing.Protocol`, `subprocess`, `json`, `shlex`, `xml.etree`. No runtime dep added; the APK is built entirely outside the Python package.

## Deferred / out of scope for this plan

- The full Android APK implementation (Tasks A1-A5 above) — its own Android spec + brainstorm.
- MediaProjection-based a11y screenshot capture (today `screencap` defers to adb).
- A localhost-TCP transport variant of `A11yBackend` (kept as a documented future option behind the same facade if latency dominates).
