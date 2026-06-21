# phonectl Resilience & Connection-Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Implementation status:** ✅ COMPLETE. Landed in `d5f2125 feat: resilience and connection recovery`. Key shipped files: retry/lock/rotation parser support in `src/phonectl/ui_parser.py`, backend wake/keyguard/mDNS seams in `src/phonectl/adb_backend.py`, observe retry + structured lock-state in `src/phonectl/observer.py`, connection recovery in `src/phonectl/connection.py`, `phonectl reconnect` wiring in `src/phonectl/cli.py`, and coverage in `tests/test_ui_parser.py`, `tests/test_adb_backend.py`, `tests/test_observer.py`, `tests/test_connection.py`, and `tests/test_cli.py`.

**Plan 1.3 of the platform roadmap** (`docs/superpowers/phonectl-platform-roadmap.md`). Depends on
**Plan 1.1** for the `errors` hierarchy (`ObserveError`/`DeviceLockedError`) and the `results` envelope,
and lands on top of **Plan 1.2**'s `observe()` (which already carries `tree`/`relations`/`observed_at`).
This finishes the foundation's robustness story. It **re-homes the superseded
`2026-06-21-phonectl-resilience.md` Tasks 2–9 verbatim** (its Task 1 — `errors.py` — was pulled forward
to Plan 1.1 and is **deleted here**), and **adds structured lock-state** (strategy §7.2) so an observation
reports *why* it is blocked instead of only raising.

**Goal:** Survive unattended use — auto-wake the device, retry the `uiautomator` "could not get idle
state" dump before raising a clean typed error, detect the lock screen and return **structured lock-state**
(`lock_state`/`can_act`/`recommended_user_action`), derive **rotation-aware orientation**, rediscover the
volatile Wireless-Debugging port with a layered strategy, and add a one-command `reconnect` verb — all
without leaking an `xml.etree` `ParseError` or a traceback to the agent.

**Architecture:** New **pure** parser helpers in `ui_parser` (`is_error_dump`, `parse_rotation`,
`parse_keyguard`, `parse_lock_state`, `parse_mdns_services`) classify raw device text with no I/O.
`adb_backend` grows the only new device-touching methods — `wake()`, `keyguard()`, `lock_state()`,
`mdns_services()`. `observer.observe` gains a bounded, injectable-`sleep` retry/settle loop that raises
`errors.ObserveError`/`errors.DeviceLockedError` (from Plan 1.1, the locked error carrying the structured
lock-state) and a rotation-aware orientation, while **preserving** the `tree`/`relations`/`observed_at`
payload added in Plan 1.2. `connection.ensure()` wakes the device and persists `last_port`;
`Connection.rediscover()` performs layered port recovery (last-port → mDNS → bounded port-probe →
host-Termux shim seam). `actuator.wait_for` switches to a **monotonic** deadline (polish #7). `cli` adds
the `reconnect` verb and surfaces structured lock-state in `observe --json`. Everything routes through the
duck-typed backend so a future AccessibilityService backend stays a drop-in.

**Tech Stack:** Python 3 (stdlib only at runtime: `subprocess`, `xml.etree`, `re`, `json`, `time`,
`argparse`); `pytest` for tests; `adb` (android-tools) remains the only external runtime dependency.

## Global Constraints

- **stdlib-only at runtime** (Python ≥ 3.9; `pytest` dev-only). No third-party runtime deps.
- **ONLY `adb_backend.py` may touch adb/subprocess.** New device interactions = new `AdbBackend` methods.
- **`ui_parser.py` stays pure** (no I/O, no subprocess, no `sleep`, no `print`). The new helpers
  (`is_error_dump`, `parse_rotation`, `parse_keyguard`, `parse_lock_state`, `parse_mdns_services`) are all
  `str → data`.
- **Element index `i` is a primary target; selectors are the durable target; raw `(x,y)` is the escape
  hatch.** Unchanged by this plan.
- **Every actuator `act()` re-observes** — returns the post-action `observer.observe()` snapshot.
- **Injectable seams** (`sleep`, `runner`) — no real I/O or wall-clock waits in unit tests.
- **Tests isolate via** `monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))` where config/audit is touched.
- **Structured-result invariant (Plan 1.1):** the locked/observe-failed outcomes are distinguishable,
  actionable typed errors, and `observe --json` surfaces structured lock-state in the envelope.
- **One commit per task.**
- **TDD order is non-negotiable:** write the failing test, run it to confirm it fails for the right
  reason, then write the minimum code to pass.

## Shared conventions used by this plan

- **Typed errors come from Plan 1.1's `src/phonectl/errors.py`** — `PhonectlError`, `ObserveError`
  (`retryable=True`), `DeviceLockedError` (`retryable=False`, `requires_user=True`). This plan is the first
  *producer* of `ObserveError`/`DeviceLockedError`; it **imports** them and never redefines the module.
- **Structured lock-state (strategy §7.2)** is the dict
  `{"lock_state": <enum>, "can_act": bool, "recommended_user_action": str | None}`. The recognized
  `lock_state` enum is `unlocked | locked_swipe_only | locked_secure | biometric_prompt |
  work_profile_locked | unknown`; this plan classifies the three we can detect from `dumpsys window`
  (`unlocked`, `locked_secure`, `locked_swipe_only`) and leaves the richer states as future refinement.
- **`config.json` keys:** existing `serial`, `mode`. This plan ADDS `last_port` (last-known-good `ip:port`)
  and `probe_ports` (an explicit list of candidate Wireless-Debugging ports for the port-probe fallback).
  No collision with Plan 2.x keys (`rate_limits`, `guarded_packages`, `risk_policy`, `audit_level`).
- **`observe()` keeps the Plan 1.2 payload** — `elements` (rich metadata), `hash`, `observed_at`, and the
  opt-in `tree`/`relations`. This plan only *adds* the retry loop, lock-state, and rotation-aware
  orientation; it must not drop those fields (the 1.2 tests stay green).

---

### Task 1: `ui_parser` pure helpers — `is_error_dump`, `parse_rotation`, `parse_keyguard`, `parse_lock_state`

Re-homes the old resilience plan's Task 2 and **adds `parse_lock_state`** for structured lock-state.

**Files:**
- Modify: `src/phonectl/ui_parser.py` (append after `match_selector`)
- Test: `tests/test_ui_parser.py` (append below existing tests)

**Interfaces (all PURE — no I/O):**
- `is_error_dump(text: str) -> bool` — `True` when `uiautomator dump` returned a status/error line instead
  of a hierarchy. Detection: stripped text is empty, OR starts with `ERROR:`, OR does not contain
  `<hierarchy`.
- `parse_rotation(xml: str) -> int` — reads the `rotation` attribute on `<hierarchy>` (0/1/2/3); returns
  `0` if absent/unparseable.
- `parse_keyguard(window_dump: str) -> bool` — `True` when `dumpsys window` reports the keyguard/lock
  screen is showing (`mDreamingLockscreen=true`, `mShowingLockscreen=true`, or a
  `KeyguardServiceDelegate{... showing=true ...}` line).
- `parse_lock_state(window_dump: str) -> dict` — the strategy §7.2 structured state. `unlocked` when the
  keyguard is not showing; otherwise `locked_secure` when the dump reports `secure=true`/
  `KeyguardSecure=true`, else `locked_swipe_only`. Returns `{"lock_state", "can_act",
  "recommended_user_action"}`; `can_act` is `True` only for `unlocked`.

- [x] **Step 1: Write the failing tests**

```python
# tests/test_ui_parser.py  (append below existing tests)

def test_is_error_dump_detects_idle_state_error():
    assert ui_parser.is_error_dump("ERROR: could not get idle state.") is True

def test_is_error_dump_detects_non_xml_and_empty():
    assert ui_parser.is_error_dump("null root node returned by UiTestAutomationBridge.") is True
    assert ui_parser.is_error_dump("") is True

def test_is_error_dump_false_for_real_hierarchy_even_with_trailing_status():
    good = "<?xml version='1.0'?><hierarchy rotation='0'><node/></hierarchy>"
    assert ui_parser.is_error_dump(good) is False
    noisy = good + "\nUI hierchary dumped to: /dev/tty"
    assert ui_parser.is_error_dump(noisy) is False

def test_parse_rotation_reads_attribute_and_defaults_zero():
    assert ui_parser.parse_rotation("<hierarchy rotation='1'><node/></hierarchy>") == 1
    assert ui_parser.parse_rotation("<hierarchy rotation=\"3\"></hierarchy>") == 3
    assert ui_parser.parse_rotation("<hierarchy><node/></hierarchy>") == 0
    assert ui_parser.parse_rotation("garbage") == 0

def test_parse_keyguard_detects_showing_and_unlocked():
    assert ui_parser.parse_keyguard("  mDreamingLockscreen=true\n") is True
    assert ui_parser.parse_keyguard("KeyguardServiceDelegate{showing=true secure=true}") is True
    assert ui_parser.parse_keyguard("  mDreamingLockscreen=false\n  mCurrentFocus=...") is False

def test_parse_lock_state_unlocked():
    ls = ui_parser.parse_lock_state("  mDreamingLockscreen=false\n")
    assert ls == {"lock_state": "unlocked", "can_act": True, "recommended_user_action": None}

def test_parse_lock_state_locked_secure():
    ls = ui_parser.parse_lock_state("KeyguardServiceDelegate{showing=true secure=true}")
    assert ls["lock_state"] == "locked_secure"
    assert ls["can_act"] is False
    assert "nlock" in ls["recommended_user_action"]

def test_parse_lock_state_locked_swipe_only():
    ls = ui_parser.parse_lock_state("  mDreamingLockscreen=true\n  KeyguardServiceDelegate{showing=true secure=false}")
    assert ls["lock_state"] == "locked_swipe_only"
    assert ls["can_act"] is False
```

- [x] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_ui_parser.py -v`
Expected: FAIL (`AttributeError: module 'phonectl.ui_parser' has no attribute 'is_error_dump'`).

- [x] **Step 3: Write minimal implementation**

`ui_parser` already imports `re`. Append:

```python
_ROTATION_RE = re.compile(r"<hierarchy[^>]*\brotation=[\"'](\d+)[\"']")

_KEYGUARD_PATTERNS = ("mDreamingLockscreen=true", "mShowingLockscreen=true")


def is_error_dump(text: str) -> bool:
    """True when uiautomator returned a status/error line instead of XML."""
    stripped = (text or "").strip()
    if not stripped:
        return True
    if stripped.startswith("ERROR:"):
        return True
    return "<hierarchy" not in stripped


def parse_rotation(xml: str) -> int:
    """Read <hierarchy rotation="N">; 0 when absent/unparseable."""
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


def parse_lock_state(window_dump: str) -> dict:
    """Structured lock state (strategy §7.2). can_act is True only when unlocked."""
    text = window_dump or ""
    if not parse_keyguard(text):
        return {"lock_state": "unlocked", "can_act": True, "recommended_user_action": None}
    secure = ("secure=true" in text) or ("KeyguardSecure=true" in text)
    if secure:
        return {"lock_state": "locked_secure", "can_act": False,
                "recommended_user_action": "Unlock the phone manually."}
    return {"lock_state": "locked_swipe_only", "can_act": False,
            "recommended_user_action": "Swipe up to dismiss the lock screen, then retry."}
```

Keep these side-effect-free — no `print`, no file access, no `time`.

- [x] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_ui_parser.py -v`
Expected: PASS (existing tests + new ones).

- [x] **Step 5: Commit**

```bash
git add src/phonectl/ui_parser.py tests/test_ui_parser.py
git commit -m "feat: pure ui_parser helpers is_error_dump/parse_rotation/parse_keyguard/parse_lock_state"
```

---

### Task 2: `AdbBackend.wake()` + `keyguard()` + `lock_state()` window probe

Re-homes the old resilience plan's Task 3 and adds the `lock_state()` composition.

**Files:**
- Modify: `src/phonectl/adb_backend.py` (add methods after `capabilities`)
- Test: `tests/test_adb_backend.py` (append below existing tests)

**Interfaces (new `AdbBackend` methods — the only new device-touching code):**
- `wake(self) -> None` — runs `adb [-s serial] shell input keyevent WAKEUP` (design §8: "Device asleep →
  `input keyevent WAKEUP`").
- `keyguard(self) -> bool` — `ui_parser.parse_keyguard(self.window_dump())`.
- `lock_state(self) -> dict` — `ui_parser.parse_lock_state(self.window_dump())`. (Decision logic stays
  pure in `ui_parser`; the backend only does the I/O.)

- [x] **Step 1: Write the failing tests**

```python
# tests/test_adb_backend.py  (append below existing tests)

def test_wake_sends_wakeup_keyevent():
    calls = []
    b = AdbBackend(serial="d", runner=make_runner(calls))
    b.wake()
    assert calls[0][0] == ["adb", "-s", "d", "shell", "input", "keyevent", "WAKEUP"]

def test_keyguard_true_when_window_dump_shows_lockscreen():
    calls = []
    b = AdbBackend(serial="d", runner=make_runner(calls, stdout="  mDreamingLockscreen=true\n"))
    assert b.keyguard() is True
    assert calls[0][0] == ["adb", "-s", "d", "shell", "dumpsys", "window"]

def test_lock_state_reports_structured_state():
    b = AdbBackend(serial="d",
                   runner=make_runner([], stdout="KeyguardServiceDelegate{showing=true secure=true}"))
    ls = b.lock_state()
    assert ls["lock_state"] == "locked_secure"
    assert ls["can_act"] is False

def test_lock_state_unlocked():
    b = AdbBackend(serial="d", runner=make_runner([], stdout="  mDreamingLockscreen=false\n"))
    assert b.lock_state()["can_act"] is True
```

- [x] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_adb_backend.py -v`
Expected: FAIL (`AttributeError: 'AdbBackend' object has no attribute 'wake'`).

- [x] **Step 3: Write minimal implementation**

`adb_backend.py` already imports `from phonectl import capabilities`; add `ui_parser` to that import.
Append to the `AdbBackend` class:

```python
    def wake(self) -> None:
        # design §8: device asleep -> input keyevent WAKEUP
        self._adb("shell", "input", "keyevent", "WAKEUP")

    def keyguard(self) -> bool:
        return ui_parser.parse_keyguard(self.window_dump())

    def lock_state(self) -> dict:
        return ui_parser.parse_lock_state(self.window_dump())
```

- [x] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_adb_backend.py -v`
Expected: PASS (existing tests + 4 new).

- [x] **Step 5: Commit**

```bash
git add src/phonectl/adb_backend.py tests/test_adb_backend.py
git commit -m "feat: AdbBackend.wake()/keyguard()/lock_state() device probes"
```

---

### Task 3: `observer.observe` retry/settle loop + structured lock-state guard + rotation-aware orientation

Re-homes the old resilience plan's Task 4, **layered onto Plan 1.2's `observe`** (keep
`tree`/`relations`/`observed_at`), and **emits structured lock-state**: an unlocked snapshot carries the
three §7.2 fields; a non-actionable lock raises `DeviceLockedError` carrying the same structured dict.

**Files:**
- Modify: `src/phonectl/observer.py` (`observe`, currently lines 17–37; keep `parse_focused_app`)
- Test: `tests/test_observer.py` (append below existing tests)

**Interfaces:**
- `observe(backend, session, screenshot=False, snap_path=None, tree=False, relations=False, attempts=3,
  settle=0.5, sleep=time.sleep) -> dict` — same shape as Plan 1.2 plus:
  - **Lock-state guard (first):** if `backend.lock_state()` reports `can_act` is `False`, raise
    `errors.DeviceLockedError(recommended_user_action)` with the structured dict attached as
    `exc.lock_state`. When unlocked, the three fields (`lock_state`, `can_act`, `recommended_user_action`)
    are attached to the returned snapshot. (Guarded by `getattr` so a duck-typed backend without
    `lock_state`/`keyguard` is treated as unlocked — keeps existing observer tests green.)
  - **Retry/settle:** call `backend.ui_dump()`; while `ui_parser.is_error_dump(xml)`, `sleep(settle)` and
    retry up to `attempts` times; if still an error dump, raise
    `errors.ObserveError("screen not idle — is it asleep or locked?")` instead of letting `xml.etree`
    raise a raw `ParseError`.
  - **Rotation-aware orientation (polish #5):** `screen.orientation` derives from
    `ui_parser.parse_rotation(xml)` (1/3 → landscape), with the `w`-vs-`h` fallback when rotation is 0/2.
  - **Unchanged:** `elements` (rich metadata), `hash`, `observed_at`, opt-in `tree`/`relations`, screenshot.

**Where the checks fire (documented decision):** the lock guard fires **inside `observe()`** so the whole
act loop is gated by one check (every action re-observes). `ensure()` (Task 5) additionally wakes the
device first, so the common "asleep" case is fixed before `observe` ever runs; the lock is *reported*
(not bypassed) because we cannot unlock without root (design non-goal).

- [x] **Step 1: Write the failing tests**

```python
# tests/test_observer.py  (append below existing tests)
import pytest
from phonectl import errors

ERR_DUMP = "ERROR: could not get idle state."

class FlakyBackend:
    """Error-dumps the first `fail` ui_dump calls, then returns good XML."""
    def __init__(self, fail, good_xml, window=WINDOW, lock=None):
        self._fail = fail
        self._good = good_xml
        self._window = window
        self._lock = lock or {"lock_state": "unlocked", "can_act": True,
                              "recommended_user_action": None}
        self.dumps = 0
    def ui_dump(self):
        self.dumps += 1
        return ERR_DUMP if self.dumps <= self._fail else self._good
    def window_dump(self): return self._window
    def wm_size(self): return (1080, 2400)
    def lock_state(self): return self._lock
    def screencap(self, path): return path

def test_observe_retries_then_succeeds_and_keeps_observed_at():
    s = Session()
    b = FlakyBackend(fail=2, good_xml=XML)
    slept = []
    snap = observer.observe(b, s, attempts=3, settle=0.5, sleep=lambda d: slept.append(d))
    assert snap["elements"][0]["text"] == "Wi-Fi"
    assert b.dumps == 3            # two failures + one success
    assert slept == [0.5, 0.5]
    assert "observed_at" in snap   # Plan 1.2 field preserved

def test_observe_raises_observe_error_after_exhausting_retries():
    s = Session()
    b = FlakyBackend(fail=5, good_xml=XML)
    with pytest.raises(errors.ObserveError) as e:
        observer.observe(b, s, attempts=3, settle=0, sleep=lambda d: None)
    assert "not idle" in str(e.value)
    assert b.dumps == 3

def test_observe_unlocked_snapshot_carries_lock_state():
    s = Session()
    snap = observer.observe(FlakyBackend(fail=0, good_xml=XML), s, sleep=lambda d: None)
    assert snap["lock_state"] == "unlocked"
    assert snap["can_act"] is True

def test_observe_raises_device_locked_with_structured_state():
    s = Session()
    locked = {"lock_state": "locked_secure", "can_act": False,
              "recommended_user_action": "Unlock the phone manually."}
    b = FlakyBackend(fail=0, good_xml=XML, lock=locked)
    with pytest.raises(errors.DeviceLockedError) as e:
        observer.observe(b, s, sleep=lambda d: None)
    assert e.value.lock_state == locked      # structured state attached to the error
    assert b.dumps == 0                       # never reached ui_dump

def test_observe_landscape_from_rotation():
    s = Session()
    land_xml = XML.replace('rotation="0"', 'rotation="1"')
    snap = observer.observe(FlakyBackend(fail=0, good_xml=land_xml), s, sleep=lambda d: None)
    assert snap["screen"]["orientation"] == "landscape"

def test_observe_opt_in_tree_and_relations_still_work():
    s = Session()
    snap = observer.observe(FlakyBackend(fail=0, good_xml=XML), s, tree=True, relations=True,
                            sleep=lambda d: None)
    assert snap["tree"]["class"]
    assert "siblings" in snap["relations"]
```

Note: the existing `CannedBackend` in this file has no `lock_state`/`keyguard`; the `getattr` guard treats
it as unlocked so the existing `test_observe_*` tests stay green.

- [x] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_observer.py -v`
Expected: FAIL (`observe()` has no `attempts`/`sleep` retry; no lock guard; no `lock_state` on snapshot).

- [x] **Step 3: Write minimal implementation**

Rewrite `observe` in `src/phonectl/observer.py` (keep the `import re`, `import time`, `from phonectl import
ui_parser` already present; add `errors`):

```python
from phonectl import errors, ui_parser


def _orientation(xml: str, w: int, h: int) -> str:
    rot = ui_parser.parse_rotation(xml)
    if rot in (1, 3):
        return "landscape"
    return "portrait" if h >= w else "landscape"


def _lock_state(backend) -> dict:
    fn = getattr(backend, "lock_state", None)
    if fn is not None:
        return fn()
    kg = getattr(backend, "keyguard", None)
    if kg is not None and kg():
        return {"lock_state": "locked_secure", "can_act": False,
                "recommended_user_action": "Unlock the phone manually."}
    return {"lock_state": "unlocked", "can_act": True, "recommended_user_action": None}


def observe(backend, session, screenshot: bool = False, snap_path: str | None = None,
            tree: bool = False, relations: bool = False,
            attempts: int = 3, settle: float = 0.5, sleep=time.sleep) -> dict:
    # Lock-state guard: report structured state; cannot pass a PIN without root.
    ls = _lock_state(backend)
    if not ls["can_act"]:
        exc = errors.DeviceLockedError(ls["recommended_user_action"] or "device is locked, unlock it")
        exc.lock_state = ls
        raise exc

    xml = ""
    for attempt in range(attempts):
        xml = backend.ui_dump()
        if not ui_parser.is_error_dump(xml):
            break
        if attempt < attempts - 1:
            sleep(settle)
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
        "observed_at": time.time(),
        "lock_state": ls["lock_state"],
        "can_act": ls["can_act"],
        "recommended_user_action": ls["recommended_user_action"],
        "screenshot": None,
    }
    if tree:
        snap["tree"] = ui_parser.build_tree(xml)
    if relations:
        snap["relations"] = ui_parser.parse_relations(xml)
    if screenshot and snap_path:
        snap["screenshot"] = backend.screencap(snap_path)
    session.set_snapshot(snap)
    return snap
```

Note: the `getattr` guards in `_lock_state` keep `CannedBackend` (no `lock_state`/`keyguard`) green; the
extra `is_error_dump` check after the loop covers the `attempts == 0` edge and keeps `xml.etree` from ever
seeing non-XML.

- [x] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_observer.py -v`
Expected: PASS (existing tests + new ones).

- [x] **Step 5: Commit**

```bash
git add src/phonectl/observer.py tests/test_observer.py
git commit -m "feat: observe() retry/settle loop + structured lock-state guard + rotation-aware orientation"
```

---

### Task 4: `actuator.wait_for` monotonic deadline (polish #7 / #8)

Folds the old polish plan's monotonic-deadline item: replace the decrement countdown with a
`time.monotonic()`-based deadline so it tracks real elapsed time across slow observes, and document the
`id`-kwarg shadow (polish #8).

**Files:**
- Modify: `src/phonectl/actuator.py` (`wait_for`, currently lines 59–71)
- Test: `tests/test_actuator.py` (append below existing tests)

**Interfaces:**
- `wait_for(backend, session, text=None, id=None, timeout=5.0, interval=0.5, sleep=time.sleep,
  monotonic=time.monotonic)` — same return contract (`snap` on match, `None` on timeout) but the deadline
  is `monotonic() + timeout` and the loop stops once `monotonic() >= deadline`. `monotonic` is injectable
  so tests drive elapsed time without wall-clock sleeps.

- [x] **Step 1: Write the failing test**

```python
# tests/test_actuator.py  (append below existing tests)
def test_wait_for_times_out_on_monotonic_deadline():
    b = SelBackend()                 # never serves a "Nope" element
    s = Session()
    ticks = iter([100.0, 100.0, 100.4, 100.8, 101.2])  # crosses deadline=100.0+1.0
    snap = actuator.wait_for(b, s, text="Nope", timeout=1.0, interval=0.4,
                             sleep=lambda d: None, monotonic=lambda: next(ticks))
    assert snap is None              # timed out by the monotonic clock, not a fixed count

def test_wait_for_returns_snapshot_on_match():
    b = SelBackend()                 # serves a "Wi-Fi" element
    s = Session()
    snap = actuator.wait_for(b, s, text="Wi-Fi", timeout=1.0,
                             sleep=lambda d: None, monotonic=lambda: 0.0)
    assert snap["elements"][0]["text"] == "Wi-Fi"
```

Note: `SelBackend` is the existing actuator-test double serving a single Wi-Fi node; `"Nope"` never
matches, so the loop runs until the injected monotonic clock crosses the deadline.

- [x] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_actuator.py -v`
Expected: FAIL (`wait_for()` has no `monotonic` parameter).

- [x] **Step 3: Write minimal implementation**

```python
def wait_for(backend, session, text=None, id=None, timeout: float = 5.0,
             interval: float = 0.5, sleep=time.sleep, monotonic=time.monotonic):
    # `id` intentionally shadows the builtin to mirror the element field name `id`;
    # it is only ever compared, never used as the builtin (polish #8).
    if text is None and id is None:
        raise ValueError("wait_for requires text or id")
    deadline = monotonic() + timeout
    while True:
        snap = observer.observe(backend, session)
        if any(_matches(e, text, id) for e in snap["elements"]):
            return snap
        if monotonic() >= deadline:
            return None
        sleep(interval)
```

- [x] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_actuator.py -v`
Expected: PASS (existing tests + 2 new).

- [x] **Step 5: Commit**

```bash
git add src/phonectl/actuator.py tests/test_actuator.py
git commit -m "feat: monotonic wait_for deadline (injectable clock) + id-shadow note"
```

---

### Task 5: `Connection.ensure()` auto-WAKEUP + persisted `last_port`

Re-homes the old resilience plan's Task 5 verbatim.

**Files:**
- Modify: `src/phonectl/connection.py` (`connect` lines 16–20 and `ensure` lines 22–30; keep `GUIDANCE`
  and `pair`)
- Test: `tests/test_connection.py` (append below existing tests)

**Interfaces (on `class Connection`):**
- `connect(self, addr) -> None` — unchanged behavior plus persists `cfg["last_port"] = addr` alongside
  `cfg["serial"] = addr`, then `config.save`.
- `ensure(self) -> None` — new order: (1) if `get_state() != "device"`, call `backend.wake()` (guarded by
  `getattr` so duck-typed doubles without `wake` still work); (2) re-check `get_state()`; (3) if still not
  `"device"`, try `connect(serial)` where `serial = cfg.get("serial") or cfg.get("last_port")`; (4) if
  still not `"device"` (or no serial/last_port), raise `ConnectionError(GUIDANCE)`. `ensure` does **not**
  itself run layered rediscovery — that is the explicit `reconnect` verb's job (Task 8 → Task 7),
  keeping `ensure` cheap on the per-op hot path.

- [x] **Step 1: Write the failing tests**

```python
# tests/test_connection.py  (append below existing tests)
import json

class WakeStateBackend(StateBackend):
    def __init__(self, states):
        super().__init__(states)
        self.woke = 0
    def wake(self):
        self.woke += 1

def test_ensure_wakes_then_proceeds_when_wakeup_recovers(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    b = WakeStateBackend(["offline", "device"])      # asleep -> device after WAKEUP
    Connection(b, {"serial": "127.0.0.1:5555"}).ensure()
    assert b.woke == 1
    assert b.adb_calls == []                          # waking alone fixed it

def test_ensure_falls_back_to_last_port_when_no_serial(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    b = WakeStateBackend(["offline", "offline", "device"])
    Connection(b, {"last_port": "127.0.0.1:43210"}).ensure()
    assert ("connect", "127.0.0.1:43210") in b.adb_calls

def test_connect_persists_last_port(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    cfg = {}
    Connection(StateBackend(["device"]), cfg).connect("127.0.0.1:5555")
    assert cfg["serial"] == "127.0.0.1:5555"
    assert cfg["last_port"] == "127.0.0.1:5555"
    saved = json.loads((tmp_path / "config.json").read_text())
    assert saved["last_port"] == "127.0.0.1:5555"
```

Note: the existing `StateBackend` has no `wake()`, so the four existing `ensure` tests stay green only if
`ensure` calls `wake` through a `getattr` guard. The new tests use `WakeStateBackend`, which adds `wake()`.

- [x] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_connection.py -v`
Expected: FAIL (`ensure` does not call `wake`; `connect` does not set `last_port`).

- [x] **Step 3: Write minimal implementation**

Rewrite `connect` and `ensure` in `src/phonectl/connection.py` (keep `GUIDANCE` and `pair`):

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
        wake = getattr(self.backend, "wake", None)  # design §8: wake before giving up
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

- [x] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_connection.py -v`
Expected: PASS (existing 4 tests + 3 new).

- [x] **Step 5: Commit**

```bash
git add src/phonectl/connection.py tests/test_connection.py
git commit -m "feat: ensure() auto-WAKEUP and persisted last_port retry-first recovery"
```

---

### Task 6: `AdbBackend.mdns_services()` + pure `parse_mdns_services`

Re-homes the old resilience plan's Task 6 verbatim.

**Files:**
- Modify: `src/phonectl/ui_parser.py` (append `parse_mdns_services`)
- Modify: `src/phonectl/adb_backend.py` (add `mdns_services` method)
- Test: `tests/test_ui_parser.py` and `tests/test_adb_backend.py` (append)

**Interfaces:**
- `ui_parser.parse_mdns_services(text: str) -> list[str]` — PURE: parse `adb mdns services` output into
  `"ip:port"` candidate strings; lines without a `host:port` are skipped; `[]` for empty/headerless input.
- `AdbBackend.mdns_services(self) -> list[str]` — runs `adb mdns services` and returns
  `ui_parser.parse_mdns_services(out)`.

- [x] **Step 1: Write the failing tests**

```python
# tests/test_ui_parser.py  (append)
MDNS_OUT = """List of discovered mdns services
adb-39FA-coo1\t_adb-tls-connect._tcp\t192.168.1.42:43210
adb-7C2B-zz9q\t_adb-tls-pairing._tcp\t192.168.1.42:37115
"""

def test_parse_mdns_services_extracts_host_ports():
    assert ui_parser.parse_mdns_services(MDNS_OUT) == ["192.168.1.42:43210", "192.168.1.42:37115"]

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

- [x] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_ui_parser.py tests/test_adb_backend.py -v`
Expected: FAIL (`parse_mdns_services` / `mdns_services` undefined).

- [x] **Step 3: Write minimal implementation**

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

Append to the `AdbBackend` class:

```python
    def mdns_services(self) -> list[str]:
        return ui_parser.parse_mdns_services(self._adb("mdns", "services"))
```

- [x] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_ui_parser.py tests/test_adb_backend.py -v`
Expected: PASS (existing + 3 new).

- [x] **Step 5: Commit**

```bash
git add src/phonectl/ui_parser.py src/phonectl/adb_backend.py tests/test_ui_parser.py tests/test_adb_backend.py
git commit -m "feat: mdns_services() backend method + pure parse_mdns_services"
```

---

### Task 7: `Connection.rediscover()` — layered port recovery

Re-homes the old resilience plan's Task 7 verbatim.

**Files:**
- Modify: `src/phonectl/connection.py` (add `rediscover`, `_try_connect`, `_device_ip`)
- Test: `tests/test_connection.py` (append)

**Interfaces (on `class Connection`):**
- `_try_connect(self, addr) -> bool` — `connect(addr)` then `backend.get_state() == "device"`.
- `_device_ip(self) -> str` — IP portion of `last_port`/`serial`; default `127.0.0.1` (PRoot shares
  Android loopback, design §4.1).
- `rediscover(self, sleep=time.sleep) -> str` — layered strategy returning the `ip:port` that reached
  `"device"`, else `ConnectionError(GUIDANCE)`. Layers, each short-circuiting on first success:
  1. **Retry last-known-good first:** `cfg.get("last_port")`, then `cfg.get("serial")`.
  2. **mDNS:** each candidate from `backend.mdns_services()`.
  3. **Bounded port probe (PRoot/Termux fallback):** for each port in `cfg.get("probe_ports", [])`, try
     `f"{ip}:{port}"`, `sleep(0)`-able between attempts.
  4. **Host-Termux shim seam:** if the backend exposes `host_shim_runner()`, build a sibling backend with
     that alternate runner and retry last-port/serial through it. Absent the seam, skip. (Structural +
     unit-tested via a fake seam; a real host-Termux round-trip needs a device — Task 9.)

- [x] **Step 1: Write the failing tests**

```python
# tests/test_connection.py  (append)
class RediscoverBackend:
    """Connects only to a designated 'good' addr; tracks attempts."""
    def __init__(self, good_addr, mdns=None):
        self.serial = None
        self.good = good_addr
        self._mdns = mdns or []
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
    addr = Connection(b, {"last_port": "127.0.0.1:43210"}).rediscover(sleep=lambda d: None)
    assert addr == "127.0.0.1:43210"

def test_rediscover_uses_mdns_when_last_port_dead(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    b = RediscoverBackend(good_addr="192.168.1.9:55001", mdns=["192.168.1.9:55001"])
    addr = Connection(b, {"last_port": "127.0.0.1:1"}).rediscover(sleep=lambda d: None)
    assert addr == "192.168.1.9:55001"

def test_rediscover_port_probe_finds_live_port(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    b = RediscoverBackend(good_addr="127.0.0.1:40003")
    cfg = {"last_port": "127.0.0.1:1", "probe_ports": [40001, 40002, 40003]}
    addr = Connection(b, cfg).rediscover(sleep=lambda d: None)
    assert addr == "127.0.0.1:40003"

def test_rediscover_raises_guidance_when_all_layers_fail(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    b = RediscoverBackend(good_addr="never", mdns=["127.0.0.1:1"])
    cfg = {"last_port": "127.0.0.1:2", "probe_ports": [40001, 40002]}
    with pytest.raises(ConnectionError) as e:
        Connection(b, cfg).rediscover(sleep=lambda d: None)
    assert GUIDANCE in str(e.value)
```

- [x] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_connection.py -v`
Expected: FAIL (`Connection` has no `rediscover`).

- [x] **Step 3: Write minimal implementation**

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
        # Layer 4: host-Termux shim seam (alternate runner). Structural only here.
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

Note on the shim seam: `host_shim_runner()` is the documented seam — a future implementation returns a
`runner` callable that invokes host Termux's `adb` instead of the in-PRoot one, keeping the `AdbBackend`
interface unchanged (design §4.1). It is exercised in tests only through a fake backend exposing
`host_shim_runner`; the default `AdbBackend` does **not** define it yet, so this layer is skipped in
production until that seam is implemented under its own follow-up. A real host-Termux round-trip is part
of Task 9's manual smoke.

- [x] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_connection.py -v`
Expected: PASS (existing + 4 new).

- [x] **Step 5: Commit**

```bash
git add src/phonectl/connection.py tests/test_connection.py
git commit -m "feat: layered Connection.rediscover (last-port/mDNS/port-probe/shim seam)"
```

---

### Task 8: `cli` — `reconnect` verb + structured lock-state in `observe --json`

Re-homes the old resilience plan's Task 8 and **adds lock-state surfacing**. The Plan 1.1 `main`-level
`except errors.PhonectlError` catch already exists; this task adds the `reconnect` verb, enriches the
`--json` error path so a `DeviceLockedError` envelope carries the structured `lock_state`/`can_act`/
`recommended_user_action`, and keeps a one-line plain-text path for the non-`--json` case.

**Files:**
- Modify: `src/phonectl/cli.py` (add `reconnect`; enrich `main`'s error catch; import `Connection`'s
  `GUIDANCE`)
- Test: `tests/test_cli.py` (append below existing tests)

**Interfaces:**
- `phonectl reconnect [port]` → `_cmd_reconnect(args)`: with a `port` arg, `Connection.connect(port)` and
  report; without, `Connection.rediscover()` and report the discovered `ip:port`. Returns `0` on success,
  `1` with `GUIDANCE` on failure.
- `main`'s `except errors.PhonectlError` catch: when `--json`, spread any `getattr(e, "lock_state", {})`
  into `results.err(e, **lock_state)` so the envelope carries the §7.2 fields; otherwise print one line.

- [x] **Step 1: Write the failing tests**

```python
# tests/test_cli.py  (append below existing tests)
import json as _json
from phonectl import errors

class LockedBackend(FakeBackend):
    def get_state(self): return "device"
    def lock_state(self):
        return {"lock_state": "locked_secure", "can_act": False,
                "recommended_user_action": "Unlock the phone manually."}
    def ui_dump(self):
        raise AssertionError("ui_dump called despite lock")

def test_observe_locked_plain_one_line_nonzero(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: LockedBackend())
    rc = cli.main(["observe"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "nlock" in out and "Traceback" not in out

def test_observe_locked_json_carries_structured_lock_state(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: LockedBackend())
    rc = cli.main(["observe", "--json"])
    out = _json.loads(capsys.readouterr().out)
    assert rc == 1
    assert out["ok"] is False
    assert out["error"]["code"] == "device_locked"
    assert out["lock_state"] == "locked_secure"
    assert out["can_act"] is False

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

Note: `FakeBackend` (defined earlier in `tests/test_cli.py`) lacks `lock_state`; the `observe` `getattr`
guard treats it as unlocked, so the existing CLI tests stay green.

- [x] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cli.py -v`
Expected: FAIL (no `reconnect` subcommand; `--json` lock error lacks `lock_state` fields).

- [x] **Step 3: Write minimal implementation**

In `src/phonectl/cli.py`, import `GUIDANCE` (`from phonectl.connection import Connection, GUIDANCE`), add
the handler, register the subparser, and enrich the `main` catch:

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

```python
    # build_parser: register reconnect
    rc = sub.add_parser("reconnect")
    rc.add_argument("port", nargs="?", default=None)
    rc.set_defaults(func=_cmd_reconnect)
```

```python
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
            print(json.dumps(results.err(e, **getattr(e, "lock_state", {})), indent=2))
        else:
            print(f"phonectl: {e}")
        return 1
```

Note: `results.err`'s `**extra` spreads the §7.2 fields (`lock_state`/`can_act`/`recommended_user_action`)
flat into the envelope; non-lock errors have no `lock_state` attribute, so `getattr(..., {})` is a no-op
for them. `ConnectionError` is handled inside `_cmd_reconnect`; the `main` catch is the backstop for
observe/action typed errors. No verb prints a traceback.

- [x] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cli.py -v`
Expected: PASS (existing tests + 3 new).

- [x] **Step 5: Run the full suite**

Run: `pytest -v`
Expected: PASS (ui_parser, adb_backend, observer, actuator, connection, cli, and all prior tests).

- [x] **Step 6: Commit**

```bash
git add src/phonectl/cli.py tests/test_cli.py
git commit -m "feat: phonectl reconnect verb + structured lock-state in observe --json"
```

---

### Task 9: Docs + real-device resilience smoke (manual)

Re-homes the old resilience plan's Task 9.

**Files:**
- Modify: `README.md` (document `last_port`/`probe_ports`, the `reconnect` verb, lock-state fields, and
  the lock/asleep messages)
- Modify: `docs/integration-smoke.md` (add the resilience scenario; create if absent)

**Interfaces:** none (documentation + manual procedure). Steps 2–3 require a real paired device; do not
automate them in CI.

- [x] **Step 1: Document the new config keys, verb, and lock-state contract**

In `README.md`: `last_port` (last-known-good `ip:port`, retried first by `ensure`/`rediscover`) and
`probe_ports` (candidate Wireless-Debugging ports for the PRoot port-probe fallback). Document `phonectl
reconnect [port]` (with port = connect it; without = layered rediscovery), the structured lock-state
fields (`lock_state`/`can_act`/`recommended_user_action`) now present on snapshots and the
`device_locked` error envelope, and the one-line messages `Unlock the phone manually.` and
`screen not idle — is it asleep or locked?`.

- [x] **Step 2: Verify auto-wake + observe robustness on-device**

```bash
# put the screen to sleep (power button), then:
phonectl observe        # expect a JSON snapshot, NOT a traceback (ensure() WAKEUPs; observe retries)
# lock the device with a PIN, then:
phonectl observe        # expect one line: "phonectl: Unlock the phone manually." (exit 1)
phonectl observe --json # expect an envelope with error.code=device_locked + lock_state=locked_secure
```

- [x] **Step 3: Verify port recovery after a sleep/disconnect cycle**

```bash
# let the phone sleep long enough for the connect port to rotate, then:
phonectl doctor         # may report the dropped link
phonectl reconnect      # expect: "phonectl: reconnected to 127.0.0.1:<newPort>"
phonectl observe        # expect a fresh snapshot
```

If the in-PRoot `adb mdns services` returns empty (the known PRoot caveat), confirm the port-probe layer
recovered the link and record the working `probe_ports` range in config.

- [x] **Step 4: Write the docs**

Capture the above in `docs/integration-smoke.md` under a "Resilience" heading, including the host-Termux
shim seam status (structural, needs a real device round-trip).

- [x] **Step 5: Commit**

```bash
git add README.md docs/integration-smoke.md
git commit -m "docs: resilience config keys, reconnect verb, lock-state, and on-device smoke procedure"
```

---

## Dependencies

**Plan 1.3 of the platform roadmap.** Requires **Plan 1.1** for `errors.ObserveError`/`DeviceLockedError`
and the `results` envelope, and lands on **Plan 1.2**'s `observe` (preserving `tree`/`relations`/
`observed_at`). It depends on no later plan. Downstream: **Plan 1.4** (setup/diagnostics) opportunistically
uses `reconnect`/`last_port` (gated via `hasattr`), and its diagnostics bundle reports recent connection
errors + mDNS result; **Plan 2.1** (single-writer `run_action`) wraps the act path whose `observe` now
raises structured lock-state.

## Deferred / out of scope (not in this plan)

- **Richer lock-state classification** (`biometric_prompt`, `work_profile_locked`, secure-screen black
  screenshots) — the enum is reserved; only `unlocked`/`locked_secure`/`locked_swipe_only` are detected
  here, refined with real-device data later.
- **Entering a PIN/unlocking** — an explicit design non-goal; the default policy never stores or enters
  PINs.
- **Observation-freshness beyond `observed_at` + retry** (strategy §7.3: focus/rotation before-vs-after
  re-checks, N-identical-hash settle windows, `observe --settle fast|stable|none`) — a later refinement.
- **The real host-Termux `adb` shim** (rediscover Layer 4's actual round-trip) — the Python *seam* is
  TDD'd against a fake; the host round-trip is a manual smoke item, not native code (no Android spec
  needed).
- **Active Android-version/environment gating** (strategy §9.2) — folded into Plan 1.4's diagnostics.

## Notes on testability

Every layer is unit-tested with a duck-typed fake backend and injected `sleep`/`monotonic`/`runner` — no
real device and no wall-clock waits. The new pure helpers (`is_error_dump`, `parse_rotation`,
`parse_keyguard`, `parse_lock_state`, `parse_mdns_services`) are fixture-tested with no I/O. The lock-state
guard is exercised with a fake backend returning a scripted `lock_state` dict; the retry loop with a
`FlakyBackend` that error-dumps a fixed number of times; the rediscover layers with a backend that only
connects to a designated good address. The host-Termux shim (rediscover Layer 4) is the one part that
needs a real device + host Termux to exercise end-to-end — its Python seam is fully TDD'd against a fake,
and the device round-trip is a flagged manual smoke item (Task 9), never run in CI.
