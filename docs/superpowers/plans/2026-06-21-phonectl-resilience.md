# phonectl Resilience & Port-Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `phonectl` survive unattended use — auto-wake/recover the connection, retry `uiautomator` "could not get idle state" dumps before raising a clear typed error, detect the lock screen, and rediscover the volatile Wireless-Debugging port with a layered strategy plus a one-command `reconnect` verb.

**Architecture:** A new pure parser layer in `ui_parser` (`is_error_dump`, `parse_rotation`, `parse_keyguard`) classifies raw device text with no I/O. `observer.observe` gains a bounded, injectable-`sleep` retry/settle loop that raises typed `errors.ObserveError`/`errors.DeviceLockedError` (from a new `errors.py`) instead of leaking `xml.etree` `ParseError`. `adb_backend` grows the only new device-touching methods — `wake()`, `mdns_services()`, and a `keyguard`/window probe used by the pure parsers — while `connection.ensure()` wakes the device and `Connection` performs layered port rediscovery (mDNS → bounded port probe → host-Termux shim seam), all driven through the duck-typed backend so a future AccessibilityService backend stays a drop-in. `cli` surfaces the typed errors as one-line messages with nonzero exit and adds the `reconnect` verb.

**Tech Stack:** Python 3 (stdlib only for runtime: `subprocess`, `xml.etree`, `re`, `json`, `argparse`, `time`), `pytest` for tests, `adb` (android-tools) as the only external runtime dependency.

## Global Constraints

- **stdlib-only at runtime** (Python ≥ 3.9; `pytest` dev-only). No third-party runtime deps.
- **ONLY `adb_backend.py` may touch adb/subprocess.** New device interactions = new `AdbBackend` methods.
- **`ui_parser.py` stays pure** (no I/O, no subprocess, no sleep). Pure helpers like `is_error_dump(text)`, `parse_rotation(xml)`, `parse_keyguard(window_dump)` belong there.
- **Element index `i` is the primary target.** Raw `(x,y)` is an escape hatch only.
- **Every actuator `act()` re-observes** — returns the post-action `observer.observe()` snapshot.
- **Tests isolate via** `monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))`.
- **One commit per task.**
- **TDD order is non-negotiable:** write the failing test, run it to confirm it fails for the right reason, then write the minimum code to pass.

## Shared conventions used by this plan

- **Typed errors live in `src/phonectl/errors.py`.** THIS plan CREATES that module with the base + observe hierarchy: `PhonectlError(Exception)`, `ObserveError(PhonectlError)`, `DeviceLockedError(ObserveError)`. Later plans (safety) ADD `GuardedActionError`/`RateLimitError` defensively. Task 1 creates the file; nothing else in this plan redefines it.
- **`config.json` keys:** existing `"serial"`, `"mode"`. This plan ADDS `"last_port"` (last-known-good `ip:port`) and `"probe_ports"` (an explicit list/range of candidate ports for the port-probe fallback). No collisions with the safety plan's `"rate_limit_per_min"`/`"guarded_packages"`.

---

### Task 1: `errors.py` — typed error hierarchy

**Files:**
- Create: `src/phonectl/errors.py`
- Test: `tests/test_errors.py`

**Interfaces:**
- Produces:
  - `class PhonectlError(Exception)` — base for all phonectl-raised errors.
  - `class ObserveError(PhonectlError)` — raised when `observe()` cannot get a usable screen.
  - `class DeviceLockedError(ObserveError)` — raised when the keyguard is up (cannot enter a PIN without root).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_errors.py
from phonectl import errors

def test_hierarchy_is_correct():
    assert issubclass(errors.ObserveError, errors.PhonectlError)
    assert issubclass(errors.DeviceLockedError, errors.ObserveError)
    assert issubclass(errors.DeviceLockedError, errors.PhonectlError)

def test_errors_are_raisable_with_message():
    import pytest
    with pytest.raises(errors.ObserveError) as e:
        raise errors.ObserveError("screen not idle")
    assert "screen not idle" in str(e.value)

def test_device_locked_is_an_observe_error_when_caught():
    import pytest
    with pytest.raises(errors.ObserveError):
        raise errors.DeviceLockedError("device is locked, unlock it")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_errors.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'phonectl.errors'`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/errors.py
"""Typed error hierarchy for phonectl.

Created here by the resilience plan. Other plans (safety) append their own
classes to this module defensively rather than recreating it.
"""


class PhonectlError(Exception):
    """Base class for all errors phonectl raises intentionally."""


class ObserveError(PhonectlError):
    """observe() could not obtain a usable screen (asleep, animating, locked)."""


class DeviceLockedError(ObserveError):
    """The keyguard is up; we cannot pass a PIN without root (spec non-goal)."""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_errors.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/errors.py tests/test_errors.py
git commit -m "feat: typed error hierarchy (PhonectlError/ObserveError/DeviceLockedError)"
```

---

### Task 2: `ui_parser` pure helpers — `is_error_dump`, `parse_rotation`, `parse_keyguard`

**Files:**
- Modify: `src/phonectl/ui_parser.py` (append after `screen_hash`, currently ends at line 56)
- Test: `tests/test_ui_parser.py` (append below existing tests)

**Interfaces:**
- Produces (all PURE — `str -> bool`/`str -> int`, no I/O):
  - `is_error_dump(text: str) -> bool` — `True` when `uiautomator dump` returned non-XML / an `ERROR:`-prefixed status line (e.g. the literal `ERROR: could not get idle state.`) instead of a hierarchy. Detection: the text (after stripping) does not contain `<hierarchy` OR it starts with `ERROR:`.
  - `parse_rotation(xml: str) -> int` — reads the `rotation` attribute on the `<hierarchy>` root (0/1/2/3); returns `0` if absent/unparseable. (Lets `observer` derive orientation from the tree, not just `w` vs `h`.)
  - `parse_keyguard(window_dump: str) -> bool` — `True` when `dumpsys window` reports the keyguard/lock screen is showing. Detection: a line containing `mDreamingLockscreen=true`, or `KeyguardServiceDelegate` with `showing=true`, or `mShowingLockscreen=true`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_ui_parser.py  (append below existing tests)

def test_is_error_dump_detects_idle_state_error():
    assert ui_parser.is_error_dump("ERROR: could not get idle state.") is True

def test_is_error_dump_detects_non_xml():
    assert ui_parser.is_error_dump("null root node returned by UiTestAutomationBridge.") is True
    assert ui_parser.is_error_dump("") is True

def test_is_error_dump_false_for_real_hierarchy():
    good = "<?xml version='1.0'?><hierarchy rotation='0'><node/></hierarchy>"
    assert ui_parser.is_error_dump(good) is False

def test_is_error_dump_false_for_hierarchy_with_trailing_status_line():
    noisy = ("<?xml version='1.0'?><hierarchy rotation='0'><node/></hierarchy>"
             "\nUI hierchary dumped to: /dev/tty")
    assert ui_parser.is_error_dump(noisy) is False

def test_parse_rotation_reads_attribute():
    assert ui_parser.parse_rotation("<hierarchy rotation='1'><node/></hierarchy>") == 1
    assert ui_parser.parse_rotation("<hierarchy rotation=\"3\"></hierarchy>") == 3

def test_parse_rotation_defaults_zero_when_absent_or_bad():
    assert ui_parser.parse_rotation("<hierarchy><node/></hierarchy>") == 0
    assert ui_parser.parse_rotation("garbage") == 0

def test_parse_keyguard_true_when_lockscreen_showing():
    dump = "  mDreamingLockscreen=true\n  mCurrentFocus=Window{a b NotificationShade}"
    assert ui_parser.parse_keyguard(dump) is True

def test_parse_keyguard_true_when_keyguard_delegate_showing():
    dump = "KeyguardServiceDelegate{showing=true secure=true}"
    assert ui_parser.parse_keyguard(dump) is True

def test_parse_keyguard_false_when_unlocked():
    dump = ("  mDreamingLockscreen=false\n"
            "  mCurrentFocus=Window{a b com.android.settings/.Settings}")
    assert ui_parser.parse_keyguard(dump) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_ui_parser.py -v`
Expected: FAIL (`AttributeError: module 'phonectl.ui_parser' has no attribute 'is_error_dump'`).

- [ ] **Step 3: Write minimal implementation**

Append to `src/phonectl/ui_parser.py`:

```python
import re

_ROTATION_RE = re.compile(r"<hierarchy[^>]*\brotation=[\"'](\d+)[\"']")

_KEYGUARD_PATTERNS = (
    "mDreamingLockscreen=true",
    "mShowingLockscreen=true",
)


def is_error_dump(text: str) -> bool:
    """True when uiautomator returned a status/error line instead of XML."""
    stripped = (text or "").strip()
    if not stripped:
        return True
    if stripped.startswith("ERROR:"):
        return True
    return "<hierarchy" not in stripped


def parse_rotation(xml: str) -> int:
    """Read the <hierarchy rotation="N"> attribute; 0 when absent/unparseable."""
    m = _ROTATION_RE.search(xml or "")
    if not m:
        return 0
    try:
        return int(m.group(1))
    except ValueError:
        return 0


def parse_keyguard(window_dump: str) -> bool:
    """True when `dumpsys window` reports the lock screen / keyguard is showing."""
    text = window_dump or ""
    for pat in _KEYGUARD_PATTERNS:
        if pat in text:
            return True
    for line in text.splitlines():
        if "KeyguardServiceDelegate" in line and "showing=true" in line:
            return True
    return False
```

Note: the existing module imports `hashlib` and `xml.etree.ElementTree`; adding `import re` at the top is fine (place it with the other imports to keep the file tidy). Keep these functions side-effect-free — no `print`, no file access, no `time`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_ui_parser.py -v`
Expected: PASS (existing tests + 9 new).

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/ui_parser.py tests/test_ui_parser.py
git commit -m "feat: pure ui_parser helpers is_error_dump/parse_rotation/parse_keyguard"
```

---

### Task 3: `AdbBackend.wake()` + `keyguard()` window probe

**Files:**
- Modify: `src/phonectl/adb_backend.py` (add methods after `get_state`, currently the last method ending at line 61)
- Test: `tests/test_adb_backend.py` (append below existing tests)

**Interfaces:**
- Produces (new `AdbBackend` methods — the only new device-touching code):
  - `wake(self) -> None` — runs `adb [-s serial] shell input keyevent WAKEUP` (spec §8: "Device asleep → `input keyevent WAKEUP`").
  - `keyguard(self) -> bool` — calls `self.window_dump()` and returns `ui_parser.parse_keyguard(...)`. (Thin composition; the decision logic stays pure in `ui_parser`.)
- Consumes: existing `window_dump()`; `ui_parser.parse_keyguard` (Task 2).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_adb_backend.py  (append below existing tests)

def test_wake_sends_wakeup_keyevent():
    calls = []
    b = AdbBackend(serial="d", runner=make_runner(calls))
    b.wake()
    assert calls[0][0] == ["adb", "-s", "d", "shell", "input", "keyevent", "WAKEUP"]

def test_keyguard_true_when_window_dump_shows_lockscreen():
    calls = []
    b = AdbBackend(serial="d",
                   runner=make_runner(calls, stdout="  mDreamingLockscreen=true\n"))
    assert b.keyguard() is True
    assert calls[0][0] == ["adb", "-s", "d", "shell", "dumpsys", "window"]

def test_keyguard_false_when_unlocked():
    calls = []
    b = AdbBackend(serial="d",
                   runner=make_runner(calls, stdout="  mDreamingLockscreen=false\n"))
    assert b.keyguard() is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_adb_backend.py -v`
Expected: FAIL (`AttributeError: 'AdbBackend' object has no attribute 'wake'`).

- [ ] **Step 3: Write minimal implementation**

Add the import at the top of `src/phonectl/adb_backend.py` (it currently imports only `shlex`, `subprocess`):

```python
from phonectl import ui_parser
```

Append these methods to the `AdbBackend` class (after `get_state`):

```python
    def wake(self) -> None:
        # spec §8: "Device asleep -> input keyevent WAKEUP"
        self._adb("shell", "input", "keyevent", "WAKEUP")

    def keyguard(self) -> bool:
        # Lock-screen check. Decision logic is pure in ui_parser; this only does I/O.
        return ui_parser.parse_keyguard(self.window_dump())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_adb_backend.py -v`
Expected: PASS (existing tests + 3 new).

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/adb_backend.py tests/test_adb_backend.py
git commit -m "feat: AdbBackend.wake() (input keyevent WAKEUP) and keyguard() probe"
```

---

### Task 4: `observer.observe` retry/settle loop + lock-screen guard

**Files:**
- Modify: `src/phonectl/observer.py` (rewrite `observe`, currently lines 16–31; keep `parse_focused_app` lines 8–14)
- Test: `tests/test_observer.py` (append below existing tests)

**Interfaces:**
- Produces:
  - `observe(backend, session, screenshot=False, snap_path=None, attempts: int = 3, settle: float = 0.5, sleep=time.sleep) -> dict` — same shape as today, plus:
    - Before parsing, if `backend.keyguard()` returns `True`, raise `errors.DeviceLockedError("device is locked, unlock it")`.
    - Calls `backend.ui_dump()`; if `ui_parser.is_error_dump(xml)`, `sleep(settle)` and retry up to `attempts` times. If still an error dump after the last attempt, raise `errors.ObserveError("screen not idle — is it asleep or locked?")` instead of letting `xml.etree` raise a raw `ParseError`.
    - `orientation` derived from `ui_parser.parse_rotation(xml)` (1/3 = landscape) with a `w`-vs-`h` fallback when rotation is 0.
- Consumes: `errors` (Task 1), `ui_parser.is_error_dump`/`parse_rotation` (Task 2), `backend.keyguard` (Task 3).

**Where the checks fire (documented decision):** the keyguard check fires **inside `observe()`** (every snapshot is gated) because every action re-observes, so guarding `observe` guards the whole act loop with one check. `ensure()` (Task 5) additionally wakes the device first, so the common "asleep" case is fixed before `observe` ever runs; lock is reported by `observe` because we cannot unlock without root.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_observer.py  (append below existing tests)
from phonectl import errors

ERR_DUMP = "ERROR: could not get idle state."

class FlakyBackend:
    """Returns an error dump for the first `fail` ui_dump calls, then good XML."""
    def __init__(self, fail, good_xml, window=WINDOW, locked=False):
        self._fail = fail
        self._good = good_xml
        self._window = window
        self._locked = locked
        self.dumps = 0
    def ui_dump(self):
        self.dumps += 1
        if self.dumps <= self._fail:
            return ERR_DUMP
        return self._good
    def window_dump(self): return self._window
    def wm_size(self): return (1080, 2400)
    def keyguard(self): return self._locked
    def screencap(self, path): return path

def test_observe_retries_then_succeeds():
    s = Session()
    b = FlakyBackend(fail=2, good_xml=XML)
    slept = []
    snap = observer.observe(b, s, attempts=3, settle=0.5,
                            sleep=lambda d: slept.append(d))
    assert snap["elements"][0]["text"] == "Wi-Fi"
    assert b.dumps == 3            # two failures + one success
    assert slept == [0.5, 0.5]     # settled between the two retries

def test_observe_raises_observe_error_after_exhausting_retries():
    s = Session()
    b = FlakyBackend(fail=5, good_xml=XML)
    with pytest.raises(errors.ObserveError) as e:
        observer.observe(b, s, attempts=3, settle=0, sleep=lambda d: None)
    assert "not idle" in str(e.value)
    assert b.dumps == 3            # bounded by attempts, not infinite

def test_observe_raises_device_locked_before_dumping():
    s = Session()
    b = FlakyBackend(fail=0, good_xml=XML, locked=True)
    with pytest.raises(errors.DeviceLockedError) as e:
        observer.observe(b, s, sleep=lambda d: None)
    assert "locked" in str(e.value)
    assert b.dumps == 0            # never reached ui_dump

def test_observe_landscape_from_rotation():
    s = Session()
    land_xml = XML.replace("rotation=\"0\"", "rotation=\"1\"")
    b = FlakyBackend(fail=0, good_xml=land_xml)
    snap = observer.observe(b, s, sleep=lambda d: None)
    assert snap["screen"]["orientation"] == "landscape"
```

Note: the existing `CannedBackend` in this file has no `keyguard` method; the new tests use `FlakyBackend` which does. To keep the existing `test_observe_*` tests passing, `observe` must treat a missing `keyguard` attribute as "not locked" (see implementation `getattr` guard).

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_observer.py -v`
Expected: FAIL (`observe()` has no `attempts`/`sleep` retry behavior; `errors.ObserveError`/`DeviceLockedError` not raised).

- [ ] **Step 3: Write minimal implementation**

Rewrite `observe` in `src/phonectl/observer.py` (add `import time` and `from phonectl import errors` at the top, keep `parse_focused_app`):

```python
from __future__ import annotations

import re
import time
from phonectl import errors, ui_parser

_FOCUS_RE = re.compile(r"([A-Za-z0-9_.]+)/([A-Za-z0-9_.]+)")


def parse_focused_app(window_dump: str) -> dict:
    for line in window_dump.splitlines():
        if "mCurrentFocus" in line or "mFocusedApp" in line:
            m = _FOCUS_RE.search(line)
            if m:
                return {"package": m.group(1), "activity": m.group(2)}
    return {"package": "", "activity": ""}


def _orientation(xml: str, w: int, h: int) -> str:
    rot = ui_parser.parse_rotation(xml)
    if rot in (1, 3):
        return "landscape"
    if rot in (0, 2):
        return "portrait" if h >= w else "landscape"
    return "portrait" if h >= w else "landscape"


def observe(backend, session, screenshot: bool = False, snap_path: str | None = None,
            attempts: int = 3, settle: float = 0.5, sleep=time.sleep) -> dict:
    # Lock-screen guard: we cannot pass a PIN without root (spec non-goal).
    if getattr(backend, "keyguard", None) is not None and backend.keyguard():
        raise errors.DeviceLockedError("device is locked, unlock it")

    xml = ""
    for attempt in range(attempts):
        xml = backend.ui_dump()
        if not ui_parser.is_error_dump(xml):
            break
        if attempt < attempts - 1:
            sleep(settle)
    else:
        raise errors.ObserveError("screen not idle — is it asleep or locked?")
    if ui_parser.is_error_dump(xml):
        raise errors.ObserveError("screen not idle — is it asleep or locked?")

    elements = ui_parser.parse_elements(xml)
    w, h = backend.wm_size()
    app = parse_focused_app(backend.window_dump())
    snap = {
        "app": app,
        "screen": {"w": w, "h": h, "orientation": _orientation(xml, w, h)},
        "hash": ui_parser.screen_hash(elements),
        "elements": elements,
        "screenshot": None,
    }
    if screenshot and snap_path:
        snap["screenshot"] = backend.screencap(snap_path)
    session.set_snapshot(snap)
    return snap
```

Note: the `for/else` raises when every attempt was an error dump; the extra `is_error_dump` check after the loop is belt-and-suspenders for the `attempts == 0` edge and keeps `xml.etree` from ever seeing non-XML. The `getattr(backend, "keyguard", ...)` guard keeps the existing `CannedBackend` tests (no `keyguard`) green.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_observer.py -v`
Expected: PASS (existing tests + 4 new).

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/observer.py tests/test_observer.py
git commit -m "feat: observe() retry/settle loop + lock-screen guard with typed errors"
```

---

### Task 5: `Connection.ensure()` auto-WAKEUP + persisted `last_port`

**Files:**
- Modify: `src/phonectl/connection.py` (rewrite `connect` lines 16–20 and `ensure` lines 22–30; keep `GUIDANCE` and `pair`)
- Test: `tests/test_connection.py` (append below existing tests)

**Interfaces:**
- Produces (on `class Connection`):
  - `connect(self, addr: str) -> None` — unchanged behavior plus persists `cfg["last_port"] = addr` alongside `cfg["serial"] = addr`, then `config.save`.
  - `ensure(self) -> None` — new order: (1) if `get_state() != "device"`, call `backend.wake()` (guarded by `getattr`, so duck-typed test doubles without `wake` still work); (2) re-check `get_state()`; (3) if still not "device", try `connect(serial)` where `serial = cfg.get("serial") or cfg.get("last_port")`; (4) if still not "device" (or no serial/last_port), raise `ConnectionError(GUIDANCE)`. (`ensure` does NOT itself run layered rediscovery; that is the explicit `reconnect` verb's job in Task 7 — keeping `ensure` cheap on the per-op hot path.)
- Consumes: `backend.wake` (Task 3), `config` (existing).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_connection.py  (append below existing tests)

class WakeStateBackend(StateBackend):
    """StateBackend plus a recordable wake()."""
    def __init__(self, states):
        super().__init__(states)
        self.woke = 0
    def wake(self):
        self.woke += 1

def test_ensure_wakes_then_proceeds_when_wakeup_recovers(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    # asleep -> offline; after WAKEUP the state reads "device"
    b = WakeStateBackend(["offline", "device"])
    Connection(b, {"serial": "127.0.0.1:5555"}).ensure()
    assert b.woke == 1
    assert b.adb_calls == []   # waking alone fixed it; no reconnect needed

def test_ensure_falls_back_to_last_port_when_no_serial(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    b = WakeStateBackend(["offline", "offline", "device"])
    Connection(b, {"last_port": "127.0.0.1:43210"}).ensure()
    assert ("connect", "127.0.0.1:43210") in b.adb_calls

def test_connect_persists_last_port(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    import json
    cfg = {}
    b = StateBackend(["device"])
    Connection(b, cfg).connect("127.0.0.1:5555")
    assert cfg["serial"] == "127.0.0.1:5555"
    assert cfg["last_port"] == "127.0.0.1:5555"
    saved = json.loads((tmp_path / "config.json").read_text())
    assert saved["last_port"] == "127.0.0.1:5555"
```

Note: the existing `StateBackend` has no `wake()`, so the three existing `ensure` tests will still pass only if `ensure` calls `wake` through a `getattr` guard. The new tests use `WakeStateBackend`, which adds `wake()`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_connection.py -v`
Expected: FAIL (`ensure` does not call `wake`; `connect` does not set `last_port`).

- [ ] **Step 3: Write minimal implementation**

Rewrite `connect` and `ensure` in `src/phonectl/connection.py` (keep `GUIDANCE` and `pair` as-is):

```python
    def connect(self, addr: str) -> None:
        self.backend._adb("connect", addr)
        self.backend.serial = addr
        self.cfg["serial"] = addr
        self.cfg["last_port"] = addr  # last-known-good for retry-first recovery
        config.save(self.cfg)

    def ensure(self) -> None:
        if self.backend.get_state() == "device":
            return
        # spec §8: device asleep -> wake before giving up.
        wake = getattr(self.backend, "wake", None)
        if wake is not None:
            wake()
            if self.backend.get_state() == "device":
                return
        serial = self.cfg.get("serial") or self.cfg.get("last_port")
        if serial:
            self.connect(serial)
            if self.backend.get_state() == "device":
                return
        raise ConnectionError(GUIDANCE)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_connection.py -v`
Expected: PASS (existing 4 tests + 3 new).

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/connection.py tests/test_connection.py
git commit -m "feat: ensure() auto-WAKEUP and persisted last_port retry-first recovery"
```

---

### Task 6: `AdbBackend.mdns_services()` + pure `parse_mdns_services`

**Files:**
- Modify: `src/phonectl/ui_parser.py` (append `parse_mdns_services`)
- Modify: `src/phonectl/adb_backend.py` (add `mdns_services` method)
- Test: `tests/test_ui_parser.py` and `tests/test_adb_backend.py` (append)

**Interfaces:**
- Produces:
  - `ui_parser.parse_mdns_services(text: str) -> list[str]` — PURE: parse `adb mdns services` output into a list of `"ip:port"` candidate strings. Each service line looks like `adb-XXXX-YYYY	_adb-tls-connect._tcp	192.168.1.5:43210`; extract the trailing `host:port` token. Lines without a `host:port` (e.g. the `List of discovered mdns services` header) are skipped. Returns `[]` for empty/headerless input.
  - `AdbBackend.mdns_services(self) -> list[str]` — runs `adb mdns services` and returns `ui_parser.parse_mdns_services(out)`. (Only the I/O lives in the backend; the parse is pure and fixture-tested.)
- Consumes: nothing new.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_ui_parser.py  (append)

MDNS_OUT = """List of discovered mdns services
adb-39FA-coo1\t_adb-tls-connect._tcp\t192.168.1.42:43210
adb-7C2B-zz9q\t_adb-tls-pairing._tcp\t192.168.1.42:37115
"""

def test_parse_mdns_services_extracts_host_ports():
    assert ui_parser.parse_mdns_services(MDNS_OUT) == [
        "192.168.1.42:43210", "192.168.1.42:37115"]

def test_parse_mdns_services_empty_when_none_found():
    assert ui_parser.parse_mdns_services("List of discovered mdns services\n") == []
    assert ui_parser.parse_mdns_services("") == []
```

```python
# tests/test_adb_backend.py  (append)

def test_mdns_services_runs_adb_and_parses():
    calls = []
    out = ("List of discovered mdns services\n"
           "adb-1\t_adb-tls-connect._tcp\t10.0.0.5:43210\n")
    b = AdbBackend(serial="d", runner=make_runner(calls, stdout=out))
    assert b.mdns_services() == ["10.0.0.5:43210"]
    assert calls[0][0] == ["adb", "-s", "d", "mdns", "services"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_ui_parser.py tests/test_adb_backend.py -v`
Expected: FAIL (`parse_mdns_services` / `mdns_services` undefined).

- [ ] **Step 3: Write minimal implementation**

Append to `src/phonectl/ui_parser.py`:

```python
_HOSTPORT_RE = re.compile(r"(\d{1,3}(?:\.\d{1,3}){3}:\d+)")


def parse_mdns_services(text: str) -> list[str]:
    """Parse `adb mdns services` output into ip:port candidate strings."""
    out: list[str] = []
    for line in (text or "").splitlines():
        m = _HOSTPORT_RE.search(line)
        if m:
            out.append(m.group(1))
    return out
```

Append to the `AdbBackend` class in `src/phonectl/adb_backend.py`:

```python
    def mdns_services(self) -> list[str]:
        return ui_parser.parse_mdns_services(self._adb("mdns", "services"))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_ui_parser.py tests/test_adb_backend.py -v`
Expected: PASS (existing + 3 new).

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/ui_parser.py src/phonectl/adb_backend.py tests/test_ui_parser.py tests/test_adb_backend.py
git commit -m "feat: mdns_services() backend method + pure parse_mdns_services"
```

---

### Task 7: `Connection.rediscover()` — layered port recovery

**Files:**
- Modify: `src/phonectl/connection.py` (add `rediscover` and a `_try_connect` helper)
- Test: `tests/test_connection.py` (append)

**Interfaces:**
- Produces (on `class Connection`):
  - `_try_connect(self, addr: str) -> bool` — `connect(addr)` then return `backend.get_state() == "device"`. (One probe attempt; persists on success via `connect`.)
  - `rediscover(self, sleep=time.sleep) -> str` — run the layered strategy, return the `ip:port` that reached state `"device"`, or raise `ConnectionError(GUIDANCE)` if every layer fails. Layers, each short-circuiting on first success:
    1. **Retry last-known-good first:** `cfg.get("last_port")` (then `cfg.get("serial")`) via `_try_connect`.
    2. **mDNS:** for each candidate from `backend.mdns_services()`, `_try_connect`.
    3. **Bounded port probe (PRoot/Termux fallback):** derive the device IP from `last_port`/`serial` (the `ip` before `:`) — default `127.0.0.1` for the loopback topology — and for each port in `cfg.get("probe_ports", [])`, `_try_connect(f"{ip}:{port}")`, `sleep(0)`-able between attempts (injected for tests). Stop on the first `"device"`.
    4. **Host-Termux shim seam:** if the backend exposes `host_shim_runner()` (a duck-typed seam returning an alternate `runner` that shells out to host Termux's `adb`), build a sibling backend with that runner and retry mDNS+last_port through it. Absent the seam, skip. (See note: this layer is *structurally* present and unit-tested via a fake seam; it needs a real device to exercise end-to-end.)
- Consumes: `backend.mdns_services` (Task 6), `backend._adb`/`get_state`, `config`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_connection.py  (append)

class RediscoverBackend:
    """Connects only to a designated 'good' addr; tracks attempts."""
    def __init__(self, good_addr, mdns=None):
        self.serial = None
        self.good = good_addr
        self._mdns = mdns or []
        self.attempts = []
        self._connected = None
    def get_state(self):
        return "device" if self._connected == self.good else "offline"
    def _adb(self, *args):
        if args and args[0] == "connect":
            self._connected = args[1]
        return ""
    def mdns_services(self):
        return list(self._mdns)

def test_rediscover_retries_last_port_first(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    b = RediscoverBackend(good_addr="127.0.0.1:43210")
    cfg = {"last_port": "127.0.0.1:43210"}
    addr = Connection(b, cfg).rediscover(sleep=lambda d: None)
    assert addr == "127.0.0.1:43210"

def test_rediscover_uses_mdns_when_last_port_dead(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    b = RediscoverBackend(good_addr="192.168.1.9:55001",
                          mdns=["192.168.1.9:55001"])
    cfg = {"last_port": "127.0.0.1:1"}   # dead
    addr = Connection(b, cfg).rediscover(sleep=lambda d: None)
    assert addr == "192.168.1.9:55001"

def test_rediscover_port_probe_finds_live_port(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    b = RediscoverBackend(good_addr="127.0.0.1:40003")
    cfg = {"last_port": "127.0.0.1:1", "probe_ports": [40001, 40002, 40003]}
    slept = []
    addr = Connection(b, cfg).rediscover(sleep=lambda d: slept.append(d))
    assert addr == "127.0.0.1:40003"

def test_rediscover_raises_guidance_when_all_layers_fail(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    b = RediscoverBackend(good_addr="never", mdns=["127.0.0.1:1"])
    cfg = {"last_port": "127.0.0.1:2", "probe_ports": [40001, 40002]}
    with pytest.raises(ConnectionError) as e:
        Connection(b, cfg).rediscover(sleep=lambda d: None)
    assert GUIDANCE in str(e.value)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_connection.py -v`
Expected: FAIL (`Connection` has no `rediscover`).

- [ ] **Step 3: Write minimal implementation**

Add `import time` at the top of `src/phonectl/connection.py`, then add to `class Connection`:

```python
    def _try_connect(self, addr: str) -> bool:
        self.connect(addr)
        return self.backend.get_state() == "device"

    def _device_ip(self) -> str:
        addr = self.cfg.get("last_port") or self.cfg.get("serial") or ""
        if ":" in addr:
            return addr.rsplit(":", 1)[0]
        return "127.0.0.1"   # PRoot shares Android loopback (design §4.1)

    def rediscover(self, sleep=time.sleep) -> str:
        # Layer 1: retry last-known-good first.
        for addr in (self.cfg.get("last_port"), self.cfg.get("serial")):
            if addr and self._try_connect(addr):
                return addr
        # Layer 2: mDNS (works where an mDNS responder exists).
        mdns = getattr(self.backend, "mdns_services", None)
        if mdns is not None:
            for addr in mdns():
                if self._try_connect(addr):
                    return addr
        # Layer 3: bounded port probe (PRoot/Termux fallback; no mDNS daemon).
        ip = self._device_ip()
        ports = self.cfg.get("probe_ports", [])
        for n, port in enumerate(ports):
            if self._try_connect(f"{ip}:{port}"):
                return f"{ip}:{port}"
            if n < len(ports) - 1:
                sleep(0)
        # Layer 4: host-Termux shim seam (alternate runner). Structural only here;
        # needs a real device to exercise end-to-end. See plan note + openQuestions.
        shim = getattr(self.backend, "host_shim_runner", None)
        if shim is not None:
            alt = type(self.backend)(serial=self.backend.serial, runner=shim())
            for addr in (self.cfg.get("last_port"), self.cfg.get("serial")):
                if addr:
                    alt._adb("connect", addr)
                    if alt.get_state() == "device":
                        self.backend = alt
                        self.cfg["serial"] = self.cfg["last_port"] = addr
                        config.save(self.cfg)
                        return addr
        raise ConnectionError(GUIDANCE)
```

Note on the shim seam: the `host_shim_runner()` accessor is the documented seam — a future implementation returns a `runner` callable that invokes host Termux's `adb` binary (e.g. via `run-as`/`termux-exec`) instead of the in-PRoot one, keeping the `AdbBackend` interface unchanged (design §4.1). It is exercised in tests only through a fake backend exposing `host_shim_runner`; a real host-Termux round-trip needs a device and is part of Task 9's manual smoke. The default `AdbBackend` does **not** define `host_shim_runner` yet, so this layer is skipped in production until that seam is implemented under its own follow-up.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_connection.py -v`
Expected: PASS (existing + 4 new).

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/connection.py tests/test_connection.py
git commit -m "feat: layered Connection.rediscover (last-port/mDNS/port-probe/shim seam)"
```

---

### Task 8: `cli` — `reconnect` verb + clean typed-error surfacing

**Files:**
- Modify: `src/phonectl/cli.py` (wrap `observe`/action error handling; add `reconnect` subcommand)
- Test: `tests/test_cli.py` (append below existing tests)

**Interfaces:**
- Produces:
  - `phonectl reconnect [port]` subcommand → `_cmd_reconnect(args)`: with an explicit `port` arg, `Connection.connect(port)` and report; without, `Connection.rediscover()` and report the discovered `ip:port`. Returns `0` on success, `1` with `GUIDANCE` on failure.
  - A single error-surfacing seam `_run(func, args) -> int` (or inline try/except in `main`) that catches `errors.ObserveError`/`errors.DeviceLockedError`/`ConnectionError`, prints a one-line `phonectl: <message>`, and returns nonzero (1) — never a traceback.
- Consumes: `Connection.rediscover`/`connect` (Tasks 5, 7), `errors` (Task 1).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cli.py  (append below existing tests)
from phonectl import errors

class LockedBackend(FakeBackend):
    def get_state(self): return "device"
    def keyguard(self): return True
    def ui_dump(self):  # should never be called once locked is detected
        raise AssertionError("ui_dump called despite lock")

def test_observe_locked_prints_one_line_and_nonzero(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: LockedBackend())
    rc = cli.main(["observe"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "locked" in out
    assert "Traceback" not in out
    assert out.count("\n") <= 1   # single clean line

class ErrDumpBackend(FakeBackend):
    def get_state(self): return "device"
    def keyguard(self): return False
    def ui_dump(self): return "ERROR: could not get idle state."

def test_observe_error_dump_surfaces_observe_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: ErrDumpBackend())
    rc = cli.main(["observe"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "not idle" in out
    assert "Traceback" not in out

class ReconnectBackend(FakeBackend):
    def __init__(self):
        super().__init__()
        self.connected = None
    def get_state(self): return "device" if self.connected else "offline"
    def _adb(self, *args):
        if args and args[0] == "connect":
            self.connected = args[1]
        return ""

def test_reconnect_with_explicit_port(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    b = ReconnectBackend()
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: b)
    rc = cli.main(["reconnect", "127.0.0.1:43210"])
    out = capsys.readouterr().out
    assert rc == 0
    assert b.connected == "127.0.0.1:43210"
    assert "127.0.0.1:43210" in out
```

Note: `FakeBackend` (defined earlier in `tests/test_cli.py`) has `get_state`, `ui_dump`, `window_dump`, `wm_size`, `input_tap`. The subclasses above add `keyguard`/`_adb` as needed; `observe()`'s `getattr` guard handles the base `FakeBackend` (no `keyguard`) used by the existing tests.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cli.py -v`
Expected: FAIL (no `reconnect` subcommand; `observe` lets `DeviceLockedError`/`ObserveError` propagate as tracebacks → `SystemExit`/raised exception, not rc 1).

- [ ] **Step 3: Write minimal implementation**

In `src/phonectl/cli.py`, add `from phonectl import errors, ...` to the existing import line, add the `reconnect` command + handler, and wrap dispatch so the typed errors surface cleanly:

```python
from phonectl import __version__, config, audit, observer, actuator, errors
from phonectl.connection import Connection, GUIDANCE
```

Add the handler (near the other `_cmd_*` functions):

```python
def _cmd_reconnect(args):
    cfg = config.load()
    backend, session, conn = build_runtime(cfg)
    try:
        if args.port:
            conn.connect(args.port)
            if backend.get_state() != "device":
                print(f"phonectl: {GUIDANCE}")
                return 1
            print(f"phonectl: reconnected to {args.port}")
            return 0
        addr = conn.rediscover()
        print(f"phonectl: reconnected to {addr}")
        return 0
    except ConnectionError as e:
        print(f"phonectl: {e}")
        return 1
```

Register it in `build_parser` (alongside the other subparsers):

```python
    rc = sub.add_parser("reconnect")
    rc.add_argument("port", nargs="?", default=None)
    rc.set_defaults(func=_cmd_reconnect)
```

Wrap dispatch in `main` so observe/action typed errors print one line and exit nonzero:

```python
def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "func", None) is None:
        parser.print_help()
        return 0
    try:
        return args.func(args)
    except (errors.ObserveError, ConnectionError) as e:
        print(f"phonectl: {e}")
        return 1
```

Note: `DeviceLockedError` subclasses `ObserveError`, so the single `except` catches both. `_cmd_reconnect` handles `ConnectionError` itself for a tailored message; the `main`-level catch is the backstop for `observe`/action verbs. No verb may print a traceback.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cli.py -v`
Expected: PASS (existing tests + 4 new).

- [ ] **Step 5: Run the full suite**

Run: `pytest -v`
Expected: PASS (all files — errors, ui_parser, adb_backend, observer, connection, cli).

- [ ] **Step 6: Commit**

```bash
git add src/phonectl/cli.py tests/test_cli.py
git commit -m "feat: phonectl reconnect verb + clean typed-error surfacing in CLI"
```

---

### Task 9: Config schema doc + real-device resilience smoke (manual)

**Files:**
- Modify: `README.md` (document `last_port`/`probe_ports` config keys, the `reconnect` verb, and the lock/asleep messages)
- Modify: `docs/integration-smoke.md` (add the resilience scenario)

**Interfaces:** none (documentation + manual procedure). This task requires a real paired device for steps 2–3; do not automate them in CI.

- [ ] **Step 1: Document the new config keys and verb**

In `README.md`, add to the config section: `last_port` (last-known-good `ip:port`, retried first by `ensure`/`rediscover`) and `probe_ports` (a list of candidate Wireless-Debugging ports for the PRoot port-probe fallback, e.g. `[37000, 37001, …]`). Document `phonectl reconnect [port]` (with port = connect that port; without = layered rediscovery) and the one-line messages `device is locked, unlock it` and `screen not idle — is it asleep or locked?`.

- [ ] **Step 2: Verify auto-wake + observe robustness on-device**

```bash
# put the screen to sleep (power button), then:
phonectl observe        # expect: a JSON snapshot, NOT a traceback —
                        # ensure() WAKEUPs first; observe retries the dump
# lock the device with a PIN, then:
phonectl observe        # expect one line: "phonectl: device is locked, unlock it" (exit 1)
```

- [ ] **Step 3: Verify port recovery after a sleep/disconnect cycle**

```bash
# let the phone sleep long enough for the connect port to rotate, then:
phonectl doctor         # may report the dropped link
phonectl reconnect      # expect: "phonectl: reconnected to 127.0.0.1:<newPort>"
phonectl observe        # expect: a fresh snapshot
```

If the in-PRoot `adb mdns services` returns empty (the known PRoot caveat), confirm the port-probe layer is what recovered the link by inspecting `actions`/output, and record the working `probe_ports` range in config.

- [ ] **Step 4: Write the docs**

Capture the above in `docs/integration-smoke.md` under a new "Resilience" heading, including the host-Termux shim seam status (structural, needs a real device round-trip — see openQuestions).

- [ ] **Step 5: Commit**

```bash
git add README.md docs/integration-smoke.md
git commit -m "docs: resilience config keys, reconnect verb, and on-device smoke procedure"
```

---

## Dependencies

This is **plan 1 of 6** (resilience first) and stands alone — it depends on no other follow-up plan. It builds on the already-landed observe→act core (`adb_backend`, `observer`, `ui_parser`, `connection`, `cli`, `config`). It CREATES `src/phonectl/errors.py`; later plans (safety, etc.) ADD their exception classes to that module defensively ("create `errors.py` if absent, else append").

## Notes on testability boundaries

- Every layer is unit-tested with a duck-typed fake backend and injected `sleep`/runner — no real device or wall-clock waits.
- The **host-Termux shim** (rediscover Layer 4) is the one part that cannot be fully validated at the Python level without a real device + host Termux: the Python *seam* (`host_shim_runner()` accessor + alternate-runner backend construction) is fully TDD'd against a fake, but the actual host-Termux `adb` round-trip is a manual smoke item. It is **not** native code (no Kotlin/APK here), so it does not need a separate Android spec — only a device to exercise end-to-end.
