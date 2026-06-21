# phonectl Scroll-Until & Gestures Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Plan 3.3 of the platform roadmap** (`docs/superpowers/phonectl-platform-roadmap.md`). Third plan of
Phase 3. Depends on **Plan 1.2** (selectors + `scrollable` metadata), **Plan 2.1** (`runtime.run_action`),
and **Plan 3.1** (ProviderRegistry). Folds the superseded polish plan's Task 9 (named swipe directions +
density-aware scaling).

**Goal:** `scroll-until --text/--selector`, container-aware scroll (`--within i=`, uses the `scrollable`
metadata from 1.2), long-press/double-tap/drag/fling, and **named swipe directions (up/down/left/right)
with density-aware scaling** (strategy §6.2, §6.3).

**Architecture:** All new gesture primitives live in `adb_backend.py` (the only place allowed to call
`adb`). All higher-level orchestration (target resolution, stale-check, re-observe, scroll loop) lives in
`actuator.py`. `cli.py` gains new verbs. `mcp_server.py` gains new tools. No new modules are needed.
`scroll_until` is the one function that holds state across multiple observe→scroll cycles; it injects
`sleep` for testability.

**Tech Stack:** Python 3 (stdlib only: `time`); `pytest` for tests; no new runtime deps.

## Global Constraints

- **stdlib-only at runtime** (Python ≥ 3.9; `pytest` dev-only).
- **ONLY `adb_backend.py` may touch adb/subprocess.** All new backend methods follow the
  `_adb(...)` list-based invocation pattern (no shell string interpolation).
- **`ui_parser.py` stays pure** (untouched by this plan).
- **Every `act()` re-observes.** All new actuator functions end with `observer.observe(backend,
  session)` so the caller always gets a fresh post-gesture snapshot.
- **Injectable seams:** `AdbBackend(runner=…)` for subprocess calls; `sleep` is passed as a
  parameter to `double_tap` and `scroll_until` so tests run with fake sleep.
- **Modes + kill-switch + risk policy gate every mutating action** through `runtime.run_action`.
  Gesture verbs are classified by the risk classifier (already in Phase 2.2); none currently hit
  `HIGH_RISK_VERBS` or `CRITICAL_VERBS`.
- **One commit per task. TDD order is non-negotiable.**

## Shared conventions established by this plan

- **`input_named_swipe(direction, distance_pct, ms)`** is the canonical ADB-level named swipe. The
  higher-level `actuator.named_swipe` resolves targets and calls it.
- **Container-aware scroll** reads `element["bounds"]` from `session.last` to confine the swipe to
  the container's bounding box. If `within_i` is `None`, the swipe covers the full screen.
- **`scroll_until` returns the snapshot in which the target was found**, or the last snapshot if
  `max_scrolls` is exhausted — never `None`. The caller checks `snapshot["elements"]` for the
  target element to distinguish "found" from "exhausted".
- **Direction strings** for all new functions: `"up"`, `"down"`, `"left"`, `"right"` (lowercase).
  Anything else raises `ValueError`.

---

### Task 1: Named swipe directions + density-aware scaling

Folds the superseded polish plan's Task 9 (named swipe directions + density scaling into `swipe up`
etc.). Both `AdbBackend.input_named_swipe` and `actuator.named_swipe` are new; the existing `swipe`
verb is extended to accept a direction string.

**Files:**
- Modify: `src/phonectl/adb_backend.py` (add `input_named_swipe`)
- Modify: `src/phonectl/actuator.py` (add `named_swipe`; extend CLI swipe verb)
- Test: `tests/test_adb_backend.py` (append), `tests/test_actuator.py` (append)

**Interfaces:**
- `AdbBackend.input_named_swipe(direction: str, distance_pct: float = 0.5, ms: int = 400) -> None`
  — calls `wm_size()` to get screen dimensions; computes swipe endpoints centered on-screen;
  dispatches `input_swipe`. `distance_pct` is the fraction of the relevant screen axis covered.
- `actuator.named_swipe(backend, session, direction, *, distance_pct=0.5, ms=400,
  within_i=None, expected_hash=None, stale_ok=False) -> dict`
  — validates direction, resolves container bounds if `within_i` is set (using `session.last`),
  calls `backend.input_named_swipe` (or computes within-container swipe coordinates directly),
  returns `observer.observe(backend, session)`.

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_adb_backend.py

def test_input_named_swipe_up_calls_swipe_with_correct_direction(calls, wm_size_runner):
    from phonectl.adb_backend import AdbBackend
    AdbBackend(serial=None, runner=wm_size_runner(1080, 2400, calls)).input_named_swipe("up")
    cmd = " ".join(str(a) for a in calls.recorded[-1])
    # For "up", x is constant (center), y2 < y1
    assert "input" in cmd and "swipe" in cmd


def test_input_named_swipe_unknown_direction_raises():
    import pytest
    from phonectl.adb_backend import AdbBackend
    with pytest.raises(ValueError, match="unknown swipe direction"):
        AdbBackend(serial=None).input_named_swipe("diagonal")


# Append to tests/test_actuator.py

def test_named_swipe_returns_snapshot(fake_backend, fake_session):
    from phonectl import actuator
    snap = actuator.named_swipe(fake_backend, fake_session, "down")
    assert "elements" in snap
    assert fake_backend.named_swipe_called


def test_named_swipe_unknown_direction_raises(fake_backend, fake_session):
    import pytest
    from phonectl import actuator
    with pytest.raises(ValueError):
        actuator.named_swipe(fake_backend, fake_session, "diagonal")
```

Note: `wm_size_runner` is a test helper that returns a runner whose first call returns `wm_size`
output and passes subsequent calls through to `calls`.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_adb_backend.py tests/test_actuator.py -v -k "named_swipe"`
Expected: FAIL (`AttributeError: 'AdbBackend' object has no attribute 'input_named_swipe'`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/adb_backend.py — add after input_swipe

def input_named_swipe(self, direction: str,
                      distance_pct: float = 0.5, ms: int = 400) -> None:
    w, h = self.wm_size()
    cx, cy = w // 2, h // 2
    half_x = int(w * distance_pct / 2)
    half_y = int(h * distance_pct / 2)
    if direction == "up":
        self.input_swipe(cx, cy + half_y, cx, cy - half_y, ms)
    elif direction == "down":
        self.input_swipe(cx, cy - half_y, cx, cy + half_y, ms)
    elif direction == "left":
        self.input_swipe(cx + half_x, cy, cx - half_x, cy, ms)
    elif direction == "right":
        self.input_swipe(cx - half_x, cy, cx + half_x, cy, ms)
    else:
        raise ValueError(f"unknown swipe direction: {direction!r}")
```

```python
# src/phonectl/actuator.py — add named_swipe

def named_swipe(backend, session, direction: str, *,
                distance_pct: float = 0.5, ms: int = 400,
                within_i=None, expected_hash=None, stale_ok=False) -> dict:
    _check_stale(backend, session, expected_hash, stale_ok)
    if within_i is not None:
        elements = (session.last or {}).get("elements", [])
        el = next((e for e in elements if e["i"] == within_i), None)
        if el is not None:
            x1b, y1b, x2b, y2b = el["bounds"]
            cx, cy = (x1b + x2b) // 2, (y1b + y2b) // 2
            half_x = int((x2b - x1b) * distance_pct / 2)
            half_y = int((y2b - y1b) * distance_pct / 2)
            if direction == "up":
                backend.input_swipe(cx, cy + half_y, cx, cy - half_y, ms)
            elif direction == "down":
                backend.input_swipe(cx, cy - half_y, cx, cy + half_y, ms)
            elif direction == "left":
                backend.input_swipe(cx + half_x, cy, cx - half_x, cy, ms)
            elif direction == "right":
                backend.input_swipe(cx - half_x, cy, cx + half_x, cy, ms)
            else:
                raise ValueError(f"unknown swipe direction: {direction!r}")
            return observer.observe(backend, session)
    backend.input_named_swipe(direction, distance_pct, ms)
    return observer.observe(backend, session)
```

Also extend the CLI `swipe` verb to accept a direction string: if the first positional arg is a
direction word, route to `named_swipe`; otherwise keep the existing `x1 y1 x2 y2` coordinate form.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_adb_backend.py tests/test_actuator.py -v -k "named_swipe or swipe"`
Expected: PASS (new tests + existing swipe tests).

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/adb_backend.py src/phonectl/actuator.py \
        tests/test_adb_backend.py tests/test_actuator.py
git commit -m "feat: named swipe directions (up/down/left/right) with density-aware scaling"
```

---

### Task 2: Long-press + double-tap

**Files:**
- Modify: `src/phonectl/adb_backend.py` (add `input_long_press`)
- Modify: `src/phonectl/actuator.py` (add `long_press`, `double_tap`)
- Test: `tests/test_adb_backend.py` (append), `tests/test_actuator.py` (append)

**Interfaces:**
- `AdbBackend.input_long_press(x, y, duration_ms=1000) -> None` — `adb shell input swipe x y x y
  duration_ms` (ADB long-press via zero-distance swipe held for `duration_ms` milliseconds).
- `actuator.long_press(backend, session, *, i=None, x=None, y=None, selector=None,
  duration_ms=1000, expected_hash=None, stale_ok=False) -> dict`
  — resolves target, calls `input_long_press`, re-observes.
- `actuator.double_tap(backend, session, *, i=None, x=None, y=None, selector=None,
  interval_ms=100, sleep=time.sleep, expected_hash=None, stale_ok=False) -> dict`
  — resolves target, calls `input_tap` twice with `sleep(interval_ms / 1000)` between, re-observes.
  `sleep` is injectable so tests pass `sleep=lambda _: None`.

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_adb_backend.py

def test_input_long_press_issues_zero_distance_swipe(calls):
    from phonectl.adb_backend import AdbBackend
    AdbBackend(serial=None, runner=calls).input_long_press(300, 500, 1500)
    cmd = " ".join(str(a) for a in calls.recorded[-1])
    # Expect: adb shell input swipe 300 500 300 500 1500
    assert "swipe" in cmd
    assert cmd.count("300") >= 2 and cmd.count("500") >= 2
    assert "1500" in cmd


# Append to tests/test_actuator.py

def test_long_press_by_index_returns_snapshot(fake_backend, fake_session):
    from phonectl import actuator
    snap = actuator.long_press(fake_backend, fake_session, i=0)
    assert "elements" in snap
    assert fake_backend.long_press_called


def test_double_tap_calls_input_tap_twice(fake_backend, fake_session):
    from phonectl import actuator
    slept = []
    snap = actuator.double_tap(fake_backend, fake_session, i=0,
                                sleep=slept.append)
    assert fake_backend.tap_count == 2
    assert len(slept) == 1   # one sleep between taps


def test_double_tap_requires_target():
    from phonectl import actuator
    import pytest
    with pytest.raises(ValueError):
        actuator.double_tap(None, None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_adb_backend.py tests/test_actuator.py -v -k "long_press or double_tap"`
Expected: FAIL (`AttributeError`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/adb_backend.py

def input_long_press(self, x: int, y: int, duration_ms: int = 1000) -> None:
    self.input_swipe(x, y, x, y, duration_ms)
```

```python
# src/phonectl/actuator.py

def long_press(backend, session, *, i=None, x=None, y=None, selector=None,
               duration_ms=1000, expected_hash=None, stale_ok=False) -> dict:
    _check_stale(backend, session, expected_hash, stale_ok)
    if x is None or y is None:
        if i is not None:
            x, y = session.resolve(i)
        elif selector is not None:
            x, y = session.resolve_selector(selector)
        else:
            raise ValueError("long_press requires x/y, i, or selector")
    backend.input_long_press(x, y, duration_ms)
    return observer.observe(backend, session)


def double_tap(backend, session, *, i=None, x=None, y=None, selector=None,
               interval_ms=100, sleep=time.sleep,
               expected_hash=None, stale_ok=False) -> dict:
    _check_stale(backend, session, expected_hash, stale_ok)
    if x is None or y is None:
        if i is not None:
            x, y = session.resolve(i)
        elif selector is not None:
            x, y = session.resolve_selector(selector)
        else:
            raise ValueError("double_tap requires x/y, i, or selector")
    backend.input_tap(x, y)
    sleep(interval_ms / 1000)
    backend.input_tap(x, y)
    return observer.observe(backend, session)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_adb_backend.py tests/test_actuator.py -v -k "long_press or double_tap"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/adb_backend.py src/phonectl/actuator.py \
        tests/test_adb_backend.py tests/test_actuator.py
git commit -m "feat: long_press and double_tap gestures in AdbBackend and actuator"
```

---

### Task 3: Drag + fling

**Files:**
- Modify: `src/phonectl/adb_backend.py` (add `input_fling`)
- Modify: `src/phonectl/actuator.py` (add `drag`, `fling`)
- Test: `tests/test_adb_backend.py` (append), `tests/test_actuator.py` (append)

**Interfaces:**
- `AdbBackend.input_fling(direction: str, velocity: int = 2000) -> None` — a fast named swipe:
  velocity maps to `ms = max(50, min(400, 2_000_000 // velocity))`. Uses the same coordinate
  computation as `input_named_swipe` but with distance_pct fixed at 0.6 for a longer throw.
- `actuator.drag(backend, session, x1, y1, x2, y2, duration_ms=500, expected_hash=None,
  stale_ok=False) -> dict` — calls `backend.input_swipe(x1, y1, x2, y2, duration_ms)` with
  a long duration, which triggers drag behaviour on Android 7+ via the long-duration swipe path.
- `actuator.fling(backend, session, direction, expected_hash=None, stale_ok=False) -> dict`
  — calls `backend.input_fling(direction)`, re-observes.

Note: ADB does not have a native drag command distinct from long-duration swipe. `adb shell input
draganddrop` exists on some Android versions but is not universally available. Using a long-duration
swipe (≥ 500 ms) is the portable ADB drag primitive.

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_adb_backend.py

def test_input_fling_issues_fast_swipe(calls, wm_size_runner):
    from phonectl.adb_backend import AdbBackend
    AdbBackend(serial=None, runner=wm_size_runner(1080, 2400, calls)).input_fling("up")
    cmd = " ".join(str(a) for a in calls.recorded[-1])
    assert "swipe" in cmd


def test_input_fling_unknown_direction_raises():
    import pytest
    from phonectl.adb_backend import AdbBackend
    with pytest.raises(ValueError):
        AdbBackend(serial=None).input_fling("sideways")


# Append to tests/test_actuator.py

def test_drag_calls_swipe_with_long_duration(fake_backend, fake_session):
    from phonectl import actuator
    actuator.drag(fake_backend, fake_session, 100, 200, 300, 400)
    # drag is a long-duration swipe
    assert fake_backend.last_swipe_ms >= 500


def test_fling_returns_snapshot(fake_backend, fake_session):
    from phonectl import actuator
    snap = actuator.fling(fake_backend, fake_session, "down")
    assert "elements" in snap
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_adb_backend.py tests/test_actuator.py -v -k "drag or fling"`
Expected: FAIL (`AttributeError`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/adb_backend.py

def input_fling(self, direction: str, velocity: int = 2000) -> None:
    ms = max(50, min(400, 2_000_000 // velocity))
    self.input_named_swipe(direction, distance_pct=0.6, ms=ms)
```

```python
# src/phonectl/actuator.py

def drag(backend, session, x1, y1, x2, y2, duration_ms=500,
         expected_hash=None, stale_ok=False) -> dict:
    _check_stale(backend, session, expected_hash, stale_ok)
    backend.input_swipe(x1, y1, x2, y2, duration_ms)
    return observer.observe(backend, session)


def fling(backend, session, direction: str,
          expected_hash=None, stale_ok=False) -> dict:
    _check_stale(backend, session, expected_hash, stale_ok)
    backend.input_fling(direction)
    return observer.observe(backend, session)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_adb_backend.py tests/test_actuator.py -v -k "drag or fling"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/adb_backend.py src/phonectl/actuator.py \
        tests/test_adb_backend.py tests/test_actuator.py
git commit -m "feat: drag (long-duration swipe) and fling (velocity-scaled fast swipe) gestures"
```

---

### Task 4: Container-aware `scroll`

**Files:**
- Modify: `src/phonectl/actuator.py` (add `scroll`)
- Test: `tests/test_actuator.py` (append)

**Interfaces:**
- `actuator.scroll(backend, session, direction: str, *, within_i=None,
  distance_pct=0.5, ms=400, expected_hash=None, stale_ok=False) -> dict`
  — if `within_i` is given: read `session.last["elements"]` for the element with that index;
  raise `ValueError` if not found; warn (but proceed) if `element["scrollable"]` is False;
  compute swipe coordinates within the element's `bounds`. If `within_i` is `None`: call
  `backend.input_named_swipe(direction, distance_pct, ms)`. Always re-observes.

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_actuator.py

def test_scroll_full_screen_delegates_to_named_swipe(fake_backend, fake_session):
    from phonectl import actuator
    snap = actuator.scroll(fake_backend, fake_session, "up")
    assert "elements" in snap
    assert fake_backend.named_swipe_called


def test_scroll_within_container_uses_element_bounds(fake_backend, fake_session):
    from phonectl import actuator
    # fake_session.last should have an element at i=0 with scrollable=True
    snap = actuator.scroll(fake_backend, fake_session, "down", within_i=0)
    assert "elements" in snap
    # swipe should be within bounds of element 0, not full-screen
    assert fake_backend.last_swipe_within_bounds


def test_scroll_within_missing_index_raises(fake_backend, fake_session):
    import pytest
    from phonectl import actuator
    with pytest.raises(ValueError, match="no element"):
        actuator.scroll(fake_backend, fake_session, "up", within_i=999)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_actuator.py -v -k "scroll"`
Expected: FAIL (`AttributeError: module 'phonectl.actuator' has no attribute 'scroll'`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/actuator.py

def scroll(backend, session, direction: str, *,
           within_i=None, distance_pct: float = 0.5, ms: int = 400,
           expected_hash=None, stale_ok=False) -> dict:
    _check_stale(backend, session, expected_hash, stale_ok)
    if within_i is not None:
        elements = (session.last or {}).get("elements", [])
        el = next((e for e in elements if e["i"] == within_i), None)
        if el is None:
            raise ValueError(f"no element with index {within_i} in current snapshot")
        x1b, y1b, x2b, y2b = el["bounds"]
        cx, cy = (x1b + x2b) // 2, (y1b + y2b) // 2
        half_x = int((x2b - x1b) * distance_pct / 2)
        half_y = int((y2b - y1b) * distance_pct / 2)
        if direction == "up":
            backend.input_swipe(cx, cy + half_y, cx, cy - half_y, ms)
        elif direction == "down":
            backend.input_swipe(cx, cy - half_y, cx, cy + half_y, ms)
        elif direction == "left":
            backend.input_swipe(cx + half_x, cy, cx - half_x, cy, ms)
        elif direction == "right":
            backend.input_swipe(cx - half_x, cy, cx + half_x, cy, ms)
        else:
            raise ValueError(f"unknown scroll direction: {direction!r}")
    else:
        backend.input_named_swipe(direction, distance_pct, ms)
    return observer.observe(backend, session)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_actuator.py -v -k "scroll"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/actuator.py tests/test_actuator.py
git commit -m "feat: container-aware scroll using scrollable element bounds from snapshot"
```

---

### Task 5: `scroll_until` — loop scroll + observe + condition

**Files:**
- Modify: `src/phonectl/actuator.py` (add `scroll_until`)
- Test: `tests/test_actuator.py` (append)

**Interfaces:**
- `actuator.scroll_until(backend, session, direction: str, *, text=None, selector=None,
  max_scrolls=10, within_i=None, sleep=time.sleep) -> dict`
  — `text` and `selector` are mutually compatible (either or both); raises `ValueError` if neither
  is given. Loop: observe → check → scroll → sleep(0.3) → repeat up to `max_scrolls` times.
  Returns the snapshot in which the target was found, or the last snapshot if exhausted.
  The return envelope carries `found: bool` so the caller knows whether the target was located.

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_actuator.py

def test_scroll_until_finds_text_on_second_scroll(fake_backend, fake_session):
    from phonectl import actuator

    call_count = [0]
    found_elements = [
        [],
        [{"i": 0, "text": "Target", "id": "", "class": "", "content_desc": "",
          "clickable": False, "enabled": True, "focused": False, "checkable": False,
          "checked": False, "scrollable": False, "long_clickable": False,
          "password": False, "selected": False, "editable": False,
          "package": "", "bounds": [0,0,100,50], "center": [50, 25]}],
    ]

    def fake_observe(b, s):
        snap = {"elements": found_elements[min(call_count[0], 1)],
                "app": {}, "hash": "h"}
        call_count[0] += 1
        s.last = snap
        return snap

    import phonectl.actuator as act_mod
    import phonectl.observer as obs
    original_observe = obs.observe
    obs.observe = fake_observe
    try:
        snap = actuator.scroll_until(
            fake_backend, fake_session, "down", text="Target",
            sleep=lambda _: None,
        )
        assert any(e["text"] == "Target" for e in snap.get("elements", []))
    finally:
        obs.observe = original_observe


def test_scroll_until_returns_last_snapshot_when_not_found(fake_backend, fake_session):
    from phonectl import actuator
    snap = actuator.scroll_until(
        fake_backend, fake_session, "up", text="NotPresent",
        max_scrolls=2, sleep=lambda _: None,
    )
    assert isinstance(snap, dict)


def test_scroll_until_requires_text_or_selector(fake_backend, fake_session):
    import pytest
    from phonectl import actuator
    with pytest.raises(ValueError):
        actuator.scroll_until(fake_backend, fake_session, "down")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_actuator.py -v -k "scroll_until"`
Expected: FAIL (`AttributeError: module 'phonectl.actuator' has no attribute 'scroll_until'`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/actuator.py

def scroll_until(backend, session, direction: str, *,
                 text=None, selector=None, max_scrolls=10,
                 within_i=None, sleep=time.sleep) -> dict:
    if text is None and selector is None:
        raise ValueError("scroll_until requires text or selector")
    from phonectl import ui_parser
    for _ in range(max_scrolls):
        snap = observer.observe(backend, session)
        elements = snap.get("elements", [])
        if text is not None and any(e.get("text") == text for e in elements):
            return snap
        if selector is not None and ui_parser.match_selector(elements, selector):
            return snap
        scroll(backend, session, direction, within_i=within_i)
        sleep(0.3)
    return observer.observe(backend, session)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_actuator.py -v -k "scroll_until"`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/actuator.py tests/test_actuator.py
git commit -m "feat: scroll_until loops scroll+observe until text/selector found or max_scrolls exhausted"
```

---

### Task 6: CLI verbs + MCP tools for new gestures

**Files:**
- Modify: `src/phonectl/cli.py` (new verbs + swipe extension)
- Modify: `src/phonectl/mcp_server.py` (new tools)
- Test: `tests/test_cli.py` (append), `tests/test_mcp_server.py` (append)

**Interfaces:**

CLI new verbs (all accept `--json`, `--request-id`; mutating ones route through `_do_action`):
- `phonectl swipe up|down|left|right [--within i=N] [--distance-pct F]` — named swipe; coordinate
  form `phonectl swipe x1 y1 x2 y2` is preserved for backward compat.
- `phonectl long-press [--i N | --selector S | --x X --y Y] [--duration-ms D]`
- `phonectl double-tap [--i N | --selector S | --x X --y Y] [--interval-ms D]`
- `phonectl drag --x1 X --y1 Y --x2 X --y2 Y [--duration-ms D]`
- `phonectl fling up|down|left|right`
- `phonectl scroll up|down|left|right [--within i=N]`
- `phonectl scroll-until --text T | --selector S [--direction down] [--within i=N] [--max N]`

MCP new tools (stable names):
- `phone_long_press` — args: `index`/`selector`/`x`+`y`, `duration_ms`, `expected_hash`, `stale_ok`
- `phone_double_tap` — args: `index`/`selector`/`x`+`y`, `interval_ms`
- `phone_drag` — args: `x1`, `y1`, `x2`, `y2`, `duration_ms`
- `phone_fling` — args: `direction`
- `phone_scroll` — args: `direction`, `within_index`, `distance_pct`
- `phone_scroll_until` — args: `direction`, `text`, `selector`, `max_scrolls`, `within_index`

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_cli.py

def test_swipe_named_direction(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: FakeBackend())
    rc = cli.main(["swipe", "up", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["ok"] is True


def test_scroll_until_cli(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: FakeBackend())
    rc = cli.main(["scroll-until", "--text", "NotHere", "--max", "1", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0  # returns last snapshot, not an error


# Append to tests/test_mcp_server.py

def test_phone_long_press_returns_ok(build):
    from phonectl.mcp_server import call_tool
    env = call_tool("phone_long_press", {"x": 100, "y": 200}, build)
    assert env["ok"] is True


def test_phone_scroll_until_returns_ok(build):
    from phonectl.mcp_server import call_tool
    env = call_tool("phone_scroll_until", {"direction": "down", "text": "x", "max_scrolls": 1}, build)
    assert env["ok"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli.py tests/test_mcp_server.py -v -k "swipe_named or scroll_until_cli or long_press or scroll_until"`
Expected: FAIL (verbs/tools not yet wired).

- [ ] **Step 3: Write minimal implementation**

**In `cli.py`:** Extend the existing `swipe` subparser to detect direction strings; add `long-press`,
`double-tap`, `drag`, `fling`, `scroll`, `scroll-until` subcommands. Each handler calls the
corresponding `actuator` function via `_do_action`.

```python
# Example: _cmd_long_press
def _cmd_long_press(args):
    def fn(backend, session):
        return actuator.long_press(backend, session,
                                   i=getattr(args, "i", None),
                                   x=getattr(args, "x", None),
                                   y=getattr(args, "y", None),
                                   selector=_selector_from_args(args),
                                   duration_ms=args.duration_ms)
    return _do_action(args, "long_press",
                      fn, f"i={args.i}" if args.i is not None else f"({args.x},{args.y})")
```

**In `mcp_server.py`:** Add handler functions and `TOOLS` entries for the six new tools.

- [ ] **Step 4: Run test to verify it passes + run full suite**

Run: `pytest tests/test_cli.py tests/test_mcp_server.py -v && pytest -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/cli.py src/phonectl/mcp_server.py \
        tests/test_cli.py tests/test_mcp_server.py
git commit -m "feat: CLI and MCP tools for long-press, double-tap, drag, fling, scroll, scroll-until"
```

---

### Task 7: Docs

**Files:**
- Modify: `README.md` (gesture command reference)
- Modify: `docs/superpowers/specs/2026-06-20-phonectl-adb-bridge-design.md`

- [ ] **Step 1: Update docs**

In `README.md`, add a **Gestures** section listing:
- Named swipe: `phonectl swipe up|down|left|right [--within i=N] [--distance-pct 0.5]`
- Long-press: `phonectl long-press --i N [--duration-ms 1000]`
- Double-tap: `phonectl double-tap --i N`
- Drag: `phonectl drag --x1 X --y1 Y --x2 X --y2 Y`
- Fling: `phonectl fling down`
- Scroll: `phonectl scroll down [--within i=N]`
- Scroll-until: `phonectl scroll-until --text "Advanced" [--direction down] [--within i=N] [--max 10]`

Note the ADB drag-vs-swipe distinction (long-duration swipe is the portable primitive).

- [ ] **Step 2: Commit**

```bash
git add README.md docs/superpowers/specs/2026-06-20-phonectl-adb-bridge-design.md
git commit -m "docs: gesture command reference including scroll-until and named swipe directions"
```

---

## Dependencies

**Requires:** 1.2 (selectors + `scrollable` metadata for container-aware scroll), 2.1 (`run_action`),
3.1 (ProviderRegistry — `scroll_until` calls `observer.observe` which goes through the registry).
**Enables:** AccessibilityService (Plan 4.1) will provide a higher-fidelity `scroll` that uses
Android's `AccessibilityNodeInfo.ACTION_SCROLL_FORWARD`; when registered, the registry will prefer
it for `act_tap`-class actions; `scroll` and `scroll_until` will transparently upgrade.

## Deferred / out of scope

- **Multi-pointer gestures** (pinch/zoom) — `adb shell input` does not support multi-touch in the
  standard way; these are deferred to Plan 4.1 (AccessibilityService gesture dispatch).
- **`scroll_until` with a timeout** instead of `max_scrolls` — straightforward to add; deferred to
  keep this plan focused.
- **Press-and-hold then move** (drag from a held element) — closer to accessibility-level gesture
  dispatch; deferred to Plan 4.1.
- **Accessibility `ACTION_SCROLL_FORWARD`** (semantic scroll) — Plan 4.1.

## Notes on testability

All ADB gesture methods are tested via the fake-runner fixture (record-and-inspect pattern). All
actuator functions are tested with a `FakeBackend` that records which methods were called and with
what coordinates. `double_tap` and `scroll_until` inject `sleep` to avoid wall-clock in tests.
`scroll_until`'s observe loop is tested by temporarily replacing `observer.observe` with a stub
that cycles through prepared snapshot lists.
