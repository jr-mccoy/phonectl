> **SUPERSEDED 2026-06-22** — distributed across `docs/superpowers/phonectl-platform-roadmap.md` (named swipe → Phase 3.3; monotonic `wait_for` + rotation-aware orientation → Phase 1.2/1.3; remaining cleanups → opportunistic). Item-level disposition is in `docs/superpowers/plans/2026-06-22-phonectl-remaining-plans-meta-plan.md` §4. Kept for traceability; do not execute as-is.

# phonectl Polish & Minor Findings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the non-blocking polish findings from the reviews (spec §4) plus named swipe directions with density-aware scaling (spec §4 first bullet; design §5.2, §13), each as its own TDD cycle and commit.

**Architecture:** Every change stays inside the existing module boundaries: pure helpers (`parse_rotation`, the `screen_hash` encoding) live in `ui_parser.py` with no I/O; the swipe-direction coordinate math is computed in `actuator.swipe` from the last snapshot / `wm_size` and still goes through the duck-typed backend's `input_swipe`; `cli.py` only changes presentation and guard plumbing. No new device interactions and no new runtime dependencies are introduced.

**Tech Stack:** Python 3 (stdlib only: `json`, `hashlib`, `xml.etree`, `time`), `pytest` for tests, `adb` (android-tools) unchanged as the only external runtime dependency.

## Global Constraints

- stdlib-only at runtime (Python >=3.9; pytest dev-only).
- ONLY `adb_backend.py` may touch adb/subprocess.
- `ui_parser.py` stays pure (no I/O).
- element index `i` is the primary target.
- every actuator `act()` re-observes.
- tests isolate via `monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))`.
- one commit per task.
- TDD order is non-negotiable.

## Batching guidance (these findings are non-blocking)

These items are independent and each is small. The plan keeps **one task = one commit** so a reviewer can bisect cleanly, but the following may be **safely batched into a single commit if a worker prefers fewer commits**, because they touch disjoint code with no shared behavior:

- Task 2 (`json.dumps(target)` in confirm/dry-run messages) and Task 3 (drop `_guard_action`'s `cfg`) and Task 4 (dry-run emits the snapshot) all touch only `cli.py` and its test file; they can be one commit if desired.
- Task 6 (`screen_hash` bounds encoding) and Task 5 (`parse_rotation`) both touch `ui_parser.py`; they can be one commit.

These **must stay separate** from each other and from the batches above, because they change behavior other tasks/tests assert independently:

- Task 1 (drop the `_adb_bytes` sentinel) — changes the test-double contract in `test_adb_backend.py`; isolate it so a regression is unambiguous.
- Task 7 (`wait_for` monotonic deadline) — changes timing semantics; keep its test isolated.
- Task 9 (named swipe directions + density scaling) — the only new feature; must be its own commit.

Task 8 (the `id` kwarg decision) and Task 10 (docs) are documentation-only.

None of these tasks block another. They have **no `## Dependencies`** on the other five follow-up plans. They can be implemented in any order; the numbering is only a suggested sequence.

---

### Task 1: Drop the `_adb_bytes` test-only sentinel

**Files:**
- Modify: `src/phonectl/adb_backend.py` (lines 17-20: `_adb_bytes`)
- Test: `tests/test_adb_backend.py` (lines 3-15: `FakeCompleted`/`make_runner`; lines 49-57: `test_screencap_writes_bytes_and_returns_path`)

**Interfaces:**
- Consumes: `runner(cmd, **kwargs) -> res` where `res.stdout` carries bytes when `capture_output=True` and `text` is falsey (matching `subprocess.run(..., capture_output=True)` with no `text=True`, whose `stdout` is `bytes`).
- Produces: `AdbBackend._adb_bytes(self, *args: str) -> bytes` returning exactly `res.stdout` (no `_bytes` sentinel). `screencap(self, path: str) -> str` unchanged in signature.

The production line `res._bytes if hasattr(res, "_bytes") else res.stdout` lets the real code probe a test-only attribute, so the test never exercises the production bytes path (`res.stdout`). The fix is to make the fake expose the real bytes on `.stdout` (as real `subprocess.run` does) and simplify production to `return res.stdout`. To get a genuine red, the fake keeps a leftover `._bytes` set to a **wrong** value, and the test asserts `.stdout` wins — so the unsimplified production (which prefers `res._bytes`) returns the wrong bytes and fails, while `return res.stdout` returns the right bytes and passes.

- [ ] **Step 1: Update the test double to pin the value path to `.stdout`**

Replace `FakeCompleted`/`make_runner` so the bytes runner puts the real bytes on `.stdout` while leaving a deliberately-wrong `._bytes` value that production must NOT read:

```python
# tests/test_adb_backend.py  (replace lines 3-15)
class FakeCompleted:
    def __init__(self, stdout="", returncode=0, _bytes=None):
        self.stdout = stdout
        self.returncode = returncode
        if _bytes is not None:
            # Deliberately-wrong leftover sentinel: production must read .stdout, not this.
            self._bytes = _bytes

def make_runner(record, stdout="", stdout_bytes=b""):
    def runner(cmd, **kwargs):
        record.append((cmd, kwargs))
        if kwargs.get("capture_output") and not kwargs.get("text", False):
            # Real subprocess.run(capture_output=True) (no text=True) returns bytes on .stdout.
            # Leave a WRONG _bytes so an implementation that prefers it is caught red-handed.
            return FakeCompleted(stdout=stdout_bytes, _bytes=b"WRONG-SENTINEL")
        return FakeCompleted(stdout=stdout)
    return runner
```

The existing `test_screencap_writes_bytes_and_returns_path` (lines 49-57) already passes `stdout_bytes=png` and asserts the file bytes equal `png`; with the wrong `._bytes` in place it now distinguishes `res.stdout` from `res._bytes`. Add one assertion that the bytes call did **not** request text mode (so `.stdout` is bytes):

```python
# tests/test_adb_backend.py  (append inside test_screencap_writes_bytes_and_returns_path)
    assert calls[0][1].get("text", False) is False   # bytes path: not text mode
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_adb_backend.py::test_screencap_writes_bytes_and_returns_path -v`
Expected: FAIL — production still reads `res._bytes` first; `hasattr(res, "_bytes")` is now `True` (the fake sets it to `b"WRONG-SENTINEL"`), so `screencap` writes `b"WRONG-SENTINEL"` and the assertion `read_bytes() == png` fails. This is a real red: the test pins the value path to `res.stdout`, and only Step 3's simplification makes it green.

- [ ] **Step 3: Simplify production**

```python
# src/phonectl/adb_backend.py  (replace lines 17-20)
    def _adb_bytes(self, *args: str) -> bytes:
        cmd = self._base() + list(args)
        res = self._runner(cmd, capture_output=True)
        return res.stdout
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_adb_backend.py -v`
Expected: PASS (all adb_backend tests, including `test_screencap_writes_bytes_and_returns_path`)

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/adb_backend.py tests/test_adb_backend.py
git commit -m "refactor: drop _adb_bytes test-only sentinel; exercise real bytes path"
```

---

### Task 2: `cli` confirm/dry-run messages print `json.dumps(target)`

**Files:**
- Modify: `src/phonectl/cli.py` (line 49: confirm message; line 55: dry-run message)
- Test: `tests/test_cli.py` (extend)

**Interfaces:**
- Consumes: `_do_action(args, verb: str, fn, target: dict) -> int` (unchanged signature).
- Produces: confirm/dry-run stdout now contains `json.dumps(target)` instead of the Python dict repr `{target}`.

The raw dict repr renders single-quoted Python (`{'i': 7}`); `json.dumps` renders valid JSON (`{"i": 7}`), which is consistent with `_emit`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli.py  (append)
def test_confirm_message_prints_json_target(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    config.save({"mode": "confirm"})
    fb = FakeBackend()
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: fb)
    rc = cli.main(["tap", "--xy", "1", "2"])
    out = capsys.readouterr().out
    assert rc == 3
    assert '{"x": 1, "y": 2}' in out            # JSON, not Python dict repr
    assert "{'x': 1" not in out                  # no single-quoted Python repr

def test_dry_run_message_prints_json_target(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    config.save({"mode": "dry-run"})
    fb = FakeBackend()
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: fb)
    rc = cli.main(["tap", "--xy", "1", "2"])
    out = capsys.readouterr().out
    assert rc == 0
    assert '{"x": 1, "y": 2}' in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli.py::test_confirm_message_prints_json_target tests/test_cli.py::test_dry_run_message_prints_json_target -v`
Expected: FAIL — current code prints `f"... {target} ..."` (Python repr `{'x': 1, 'y': 2}`), so the `'{"x": 1, "y": 2}'` substring is absent.

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/cli.py  (replace line 49)
        print(f"phonectl: {verb} {json.dumps(target)} requires --yes in confirm mode")
```

```python
# src/phonectl/cli.py  (replace line 55)
        print(f"phonectl: dry-run {verb} {json.dumps(target)} (not executed)")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli.py -v`
Expected: PASS (the two new tests plus all existing cli tests — the existing confirm/dry-run tests assert only return codes and `fb.calls`, which are unaffected)

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/cli.py tests/test_cli.py
git commit -m "refactor: print json.dumps(target) in confirm/dry-run messages"
```

---

### Task 3: Drop the unused `cfg` param from `cli._guard_action`

**Files:**
- Modify: `src/phonectl/cli.py` (lines 26-30: `_guard_action`; lines 43-45: the only caller in `_do_action`)
- Test: `tests/test_cli.py` (extend)

**Interfaces:**
- Produces: `_guard_action() -> int | None` (no parameters). Returns `2` and prints the kill-switch message when `audit.kill_switch_active()`, else `None`.
- Consumes: caller `_do_action` calls `_guard_action()` with no argument.

`_guard_action(cfg)` ignores `cfg`. The finding offers two options; the kill switch is keyed off `PHONECTL_HOME` (sentinel file), not config, so dropping the param is correct — there is nothing in `cfg` it needs.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli.py  (append)
import inspect

def test_guard_action_takes_no_args():
    assert list(inspect.signature(cli._guard_action).parameters) == []

def test_guard_action_returns_two_when_kill_switch(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    (tmp_path / "STOP").write_text("")
    assert cli._guard_action() == 2

def test_guard_action_returns_none_when_clear(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    assert cli._guard_action() is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli.py::test_guard_action_takes_no_args tests/test_cli.py::test_guard_action_returns_two_when_kill_switch -v`
Expected: FAIL — current signature is `_guard_action(cfg)`, so `parameters` is `['cfg']` and `cli._guard_action()` raises `TypeError: missing 1 required positional argument: 'cfg'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/cli.py  (replace lines 26-30)
def _guard_action() -> int | None:
    if audit.kill_switch_active():
        print("phonectl: action refused (kill switch STOP present)")
        return 2
    return None
```

```python
# src/phonectl/cli.py  (replace line 44 — the caller inside _do_action)
    blocked = _guard_action()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli.py -v`
Expected: PASS (the three new tests plus all existing cli tests, including `test_tap_blocked_by_kill_switch`)

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/cli.py tests/test_cli.py
git commit -m "refactor: drop unused cfg param from cli._guard_action"
```

---

### Task 4: Dry-run emits the observed snapshot as a richer preview

**Files:**
- Modify: `src/phonectl/cli.py` (lines 53-56: the `dry-run` branch in `_do_action`)
- Test: `tests/test_cli.py` (extend; note interaction with existing `test_tap_dry_run_observes_but_does_not_inject`)

**Interfaces:**
- Consumes: `observer.observe(backend, session) -> dict`.
- Produces: in `dry-run` mode `_do_action` calls `_emit(snap)` for the observed snapshot (still no input injection, still no audit log), then prints the dry-run notice. Return code stays `0`.

Today dry-run observes and discards the snapshot. Emitting it makes dry-run a true preview of what the agent would see, while preserving the no-inject / no-log contract the existing test asserts.

- [ ] **Step 1: Write the failing test**

Parse note: stdout contains two blocks (the `_emit` JSON, then the dry-run notice line). The test asserts on substrings rather than parsing the whole stream, since `_emit` uses `indent=2` and the notice follows it on the same stream. Write the body exactly as below:

```python
# tests/test_cli.py  (append)
def test_dry_run_emits_observed_snapshot(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    config.save({"mode": "dry-run"})
    fb = FakeBackend()
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: fb)
    rc = cli.main(["tap", "--xy", "1", "2"])
    out = capsys.readouterr().out
    assert rc == 0
    assert '"elements"' in out
    assert "Wi-Fi" in out
    assert "dry-run" in out
    assert fb.calls == []
    assert not (tmp_path / "actions.jsonl").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli.py::test_dry_run_emits_observed_snapshot -v`
Expected: FAIL — current dry-run branch calls `observer.observe(...)` but discards it, so `"elements"`/`"Wi-Fi"` never reach stdout.

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/cli.py  (replace lines 53-56 — the dry-run branch)
    if mode == "dry-run":
        snap = observer.observe(backend, session)
        _emit(snap)
        print(f"phonectl: dry-run {verb} {json.dumps(target)} (not executed)")
        return 0
```

(If Task 2 has not landed, the message line stays `f"... {target} ..."`; if Task 2 has landed it is already `json.dumps(target)`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli.py -v`
Expected: PASS — the new test, plus the existing `test_tap_dry_run_observes_but_does_not_inject` (it asserts `fb.calls == []` and no `actions.jsonl`, both still true)

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/cli.py tests/test_cli.py
git commit -m "feat: dry-run emits the observed snapshot as a richer preview"
```

---

### Task 5: `ui_parser.parse_rotation` + observer rotation-aware orientation

**Files:**
- Modify: `src/phonectl/ui_parser.py` (add `parse_rotation`); `src/phonectl/observer.py` (lines 16-31: `observe`, orientation derivation)
- Test: `tests/test_ui_parser.py` (extend); `tests/test_observer.py` (extend)

**Interfaces:**
- Produces (pure, in `ui_parser.py`): `parse_rotation(xml: str) -> int | None` — returns the integer `rotation` attribute of the `<hierarchy>` root (0/1/2/3), or `None` when absent/unparsable. No I/O.
- Consumes (in `observer.observe`): `ui_parser.parse_rotation(xml)`; maps `0`/`2` -> `"portrait"`, `1`/`3` -> `"landscape"`; when `None`, falls back to the existing `"portrait" if h >= w else "landscape"` heuristic.

Rotation lives on the `<hierarchy rotation="...">` root (every fixture in the repo has it). Reading it is more reliable than the w-vs-h heuristic (which mis-labels square-ish or letterboxed screens), and the heuristic remains the documented fallback.

- [ ] **Step 1: Write the failing tests (pure parser)**

```python
# tests/test_ui_parser.py  (append)
def test_parse_rotation_reads_root_attribute():
    assert ui_parser.parse_rotation(FIXTURE) == 0

def test_parse_rotation_landscape_value():
    xml = "<?xml version='1.0'?><hierarchy rotation=\"1\"></hierarchy>"
    assert ui_parser.parse_rotation(xml) == 1

def test_parse_rotation_absent_returns_none():
    xml = "<?xml version='1.0'?><hierarchy></hierarchy>"
    assert ui_parser.parse_rotation(xml) is None

def test_parse_rotation_tolerates_trailing_device_line():
    noisy = FIXTURE + "\nUI hierchary dumped to: /dev/tty\n"
    assert ui_parser.parse_rotation(noisy) == 0
```

- [ ] **Step 2: Run parser tests to verify they fail**

Run: `pytest tests/test_ui_parser.py -k parse_rotation -v`
Expected: FAIL (`AttributeError: module 'phonectl.ui_parser' has no attribute 'parse_rotation'`)

- [ ] **Step 3: Implement `parse_rotation` (pure)**

```python
# src/phonectl/ui_parser.py  (add after parse_bounds; reuse _extract_hierarchy)
def parse_rotation(xml: str):
    # <hierarchy rotation="0"> ... </hierarchy>  -> 0/1/2/3, or None if absent.
    try:
        root = ET.fromstring(_extract_hierarchy(xml))
    except ET.ParseError:
        return None
    raw = root.get("rotation")
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None
```

Note: `parse_rotation` is defined after `_extract_hierarchy` in source order (which is defined at module level), so no forward-reference issue. If placing it before `_extract_hierarchy` textually, move it below.

- [ ] **Step 4: Run parser tests to verify they pass**

Run: `pytest tests/test_ui_parser.py -v`
Expected: PASS (the four new tests plus all existing parser tests)

- [ ] **Step 5: Write the failing observer test**

```python
# tests/test_observer.py  (append)
XML_LANDSCAPE = """<?xml version='1.0'?><hierarchy rotation="1">
<node index="0" text="Wi-Fi" resource-id="android:id/title" class="TextView"
 content-desc="" clickable="true" bounds="[44,380][1036,520]"/></hierarchy>"""

XML_NO_ROTATION = """<?xml version='1.0'?><hierarchy>
<node index="0" text="Wi-Fi" resource-id="android:id/title" class="TextView"
 content-desc="" clickable="true" bounds="[44,380][1036,520]"/></hierarchy>"""

def test_observe_orientation_from_rotation_landscape():
    s = Session()
    # rotation=1 -> landscape even though wm_size reports a tall screen
    snap = observer.observe(CannedBackend(XML_LANDSCAPE, WINDOW, size=(1080, 2400)), s)
    assert snap["screen"]["orientation"] == "landscape"

def test_observe_orientation_falls_back_to_heuristic_when_absent():
    s = Session()
    # no rotation attr -> fall back to w-vs-h; wide screen => landscape
    snap = observer.observe(CannedBackend(XML_NO_ROTATION, WINDOW, size=(2400, 1080)), s)
    assert snap["screen"]["orientation"] == "landscape"
    s2 = Session()
    snap2 = observer.observe(CannedBackend(XML_NO_ROTATION, WINDOW, size=(1080, 2400)), s2)
    assert snap2["screen"]["orientation"] == "portrait"
```

- [ ] **Step 6: Run observer test to verify it fails**

Run: `pytest tests/test_observer.py -k orientation -v`
Expected: FAIL — current `observe` ignores rotation; with `size=(1080, 2400)` and `rotation=1` it returns `"portrait"` (h >= w), so `test_observe_orientation_from_rotation_landscape` fails.

- [ ] **Step 7: Implement rotation-aware orientation**

```python
# src/phonectl/observer.py  (replace lines 16-27 — through the snap dict's screen field)
def observe(backend, session, screenshot: bool = False, snap_path: str | None = None) -> dict:
    xml = backend.ui_dump()
    elements = ui_parser.parse_elements(xml)
    w, h = backend.wm_size()
    rotation = ui_parser.parse_rotation(xml)
    if rotation in (0, 2):
        orientation = "portrait"
    elif rotation in (1, 3):
        orientation = "landscape"
    else:
        orientation = "portrait" if h >= w else "landscape"
    app = parse_focused_app(backend.window_dump())
    snap = {
        "app": app,
        "screen": {"w": w, "h": h, "orientation": orientation},
        "hash": ui_parser.screen_hash(elements),
        "elements": elements,
        "screenshot": None,
    }
```

(Lines 28-31 — the `screenshot`/`set_snapshot`/`return` tail — are unchanged.)

- [ ] **Step 8: Run observer tests to verify they pass**

Run: `pytest tests/test_observer.py -v`
Expected: PASS — the new orientation tests plus existing tests (`test_observe_builds_snapshot_and_updates_session` uses `XML` with `rotation="0"`, so orientation stays `"portrait"`)

- [ ] **Step 9: Commit**

```bash
git add src/phonectl/ui_parser.py src/phonectl/observer.py tests/test_ui_parser.py tests/test_observer.py
git commit -m "feat: pure parse_rotation; observer derives orientation from hierarchy rotation"
```

---

### Task 6: `screen_hash` encodes bounds via `','.join(map(str, bounds))`

**Files:**
- Modify: `src/phonectl/ui_parser.py` (lines 52-56: `screen_hash`)
- Test: `tests/test_ui_parser.py` (extend; existing `test_screen_hash_stable_and_sensitive` must still pass)

**Interfaces:**
- Produces: `screen_hash(elements: list[dict]) -> str` — per element hashes `text|id|<bounds-csv>` where `<bounds-csv> = ','.join(map(str, e['bounds']))`.

Encoding `bounds` via the Python list-repr (`f"{e['bounds']}"` -> `"[44, 380, 1036, 520]"`) is brittle if a future caller passes a tuple instead of a list (the repr would differ). A CSV join is type-agnostic. No hash is persisted, so this is not a current bug — but the hash value changes, so the test must not pin a literal digest (it does not; it only asserts stability and sensitivity).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ui_parser.py  (append)
def test_screen_hash_bounds_encoding_is_csv_and_type_agnostic():
    els = ui_parser.parse_elements(FIXTURE)
    h_list = ui_parser.screen_hash(els)
    # same numbers as a tuple must hash identically (CSV join, not list-repr)
    as_tuple = [dict(e) for e in els]
    for e in as_tuple:
        e["bounds"] = tuple(e["bounds"])
    assert ui_parser.screen_hash(as_tuple) == h_list
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_ui_parser.py::test_screen_hash_bounds_encoding_is_csv_and_type_agnostic -v`
Expected: FAIL — current code interpolates `e['bounds']`, so a list hashes as `[44, 380, ...]` and a tuple as `(44, 380, ...)`, yielding different digests.

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/ui_parser.py  (replace lines 52-56)
def screen_hash(elements: list[dict]) -> str:
    h = hashlib.sha1()
    for e in elements:
        bounds_csv = ",".join(map(str, e["bounds"]))
        h.update(f"{e['text']}|{e['id']}|{bounds_csv}".encode())
    return h.hexdigest()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_ui_parser.py -v`
Expected: PASS — the new test plus `test_screen_hash_stable_and_sensitive` (stable across re-parse; still sensitive to text/id/bounds changes, since the CSV of changed bounds differs)

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/ui_parser.py tests/test_ui_parser.py
git commit -m "refactor: screen_hash encodes bounds as csv (type-agnostic)"
```

---

### Task 7: `actuator.wait_for` uses a `time.monotonic()` deadline

**Files:**
- Modify: `src/phonectl/actuator.py` (lines 42-54: `wait_for`)
- Test: `tests/test_actuator.py` (extend; existing `test_wait_for_finds_text_after_polling` and `test_wait_for_requires_text_or_id` must still pass)

**Interfaces:**
- Produces: `wait_for(backend, session, text=None, id=None, timeout: float = 5.0, interval: float = 0.5, sleep=time.sleep, clock=time.monotonic) -> dict | None`. Adds an injectable `clock` (defaults to `time.monotonic`). Loops until `clock() - start >= timeout`. Supports `interval=0` without exhausting an iteration counter.
- Consumes: `clock() -> float` and `sleep(seconds: float)` (both injectable for determinism).

The current iteration-count decrement (`deadline -= max(interval, 0.0001)`) only approximates wall-clock and behaves oddly at `interval=0`. A monotonic deadline is exact and makes `interval=0` (busy-poll) well-defined: it polls until a real `timeout` elapses on the injected clock.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_actuator.py  (append)
def test_wait_for_uses_injected_monotonic_clock_for_timeout():
    s = Session()

    class NeverMatchBackend:
        def ui_dump(self):
            return ("<?xml version='1.0'?><hierarchy rotation=\"0\">"
                    "<node index=\"0\" text=\"Other\" resource-id=\"\" class=\"T\" "
                    "content-desc=\"\" clickable=\"true\" bounds=\"[0,0][1,1]\"/></hierarchy>")
        def window_dump(self): return WINDOW
        def wm_size(self): return (1080, 2400)

    ticks = iter([0.0, 0.0, 0.5, 1.0, 1.5])  # start, then per-loop reads past timeout=1.0
    snap = actuator.wait_for(NeverMatchBackend(), s, text="Nope", timeout=1.0,
                             interval=0, sleep=lambda *_: None,
                             clock=lambda: next(ticks))
    assert snap is None   # times out via the monotonic deadline, not an iteration count

def test_wait_for_interval_zero_still_finds_match():
    s = Session()
    b = ScriptBackend()  # XML_A then XML_B
    snap = actuator.wait_for(b, s, text="Bluetooth", timeout=5.0, interval=0,
                             sleep=lambda *_: None, clock=lambda: 0.0)
    assert snap is not None
    assert any(e["text"] == "Bluetooth" for e in snap["elements"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_actuator.py::test_wait_for_uses_injected_monotonic_clock_for_timeout tests/test_actuator.py::test_wait_for_interval_zero_still_finds_match -v`
Expected: FAIL — `wait_for` has no `clock` parameter (`TypeError: unexpected keyword argument 'clock'`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/actuator.py  (replace lines 42-54)
def wait_for(backend, session, text=None, id=None, timeout: float = 5.0,
             interval: float = 0.5, sleep=time.sleep, clock=time.monotonic):
    if text is None and id is None:
        raise ValueError("wait_for requires text or id")
    start = clock()
    while True:
        snap = observer.observe(backend, session)
        if any(_matches(e, text, id) for e in snap["elements"]):
            return snap
        if clock() - start >= timeout:
            return None
        sleep(interval)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_actuator.py -v`
Expected: PASS — the two new tests, plus the existing `test_wait_for_finds_text_after_polling` (default `clock=time.monotonic`; `XML_A` then `XML_B` matches "Bluetooth" before timeout) and `test_wait_for_requires_text_or_id`

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/actuator.py tests/test_actuator.py
git commit -m "refactor: wait_for uses monotonic deadline; supports interval=0"
```

---

### Task 8: Document the `wait_for(..., id=...)` builtin-shadow decision (no code change)

**Files:**
- Modify: `CLAUDE.md` (append a short note to "Architecture invariants")
- Test: none (documentation/decision task — there is no behavior to test)

**Interfaces:** none. `wait_for`'s `id` keyword is unchanged.

This is a deliberate decision item, not a fix. `actuator.wait_for(..., id=None, ...)` shadows the builtin `id`. It is **kept intentionally**: `id` is the public kwarg the CLI passes (`actuator.wait_for(backend, session, text=args.text, id=args.id, ...)` in `cli._cmd_wait_for`) and is the user-facing `--id` flag's natural name. Renaming it would ripple into the CLI and the documented contract for no behavioral benefit. There is nothing to TDD here.

- [ ] **Step 1: Record the decision in CLAUDE.md**

Append under "## Architecture invariants (must hold across changes)":

```markdown
- **`wait_for(..., id=...)` shadows the builtin `id` on purpose.** It is the public keyword the CLI passes (mirroring the `--id` flag); keep it. Rename only if a linter policy mandates it, and then thread the new name through `cli._cmd_wait_for` too.
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: record intentional id-kwarg shadow in wait_for"
```

---

### Task 9: Named swipe directions (up/down/left/right) with density-aware scaling

**Files:**
- Modify: `src/phonectl/actuator.py` (lines 23-25: `swipe`)
- Test: `tests/test_actuator.py` (extend)

**Interfaces:**
- Produces: `swipe(backend, session, x1=None, y1=None, x2=None, y2=None, direction: str | None = None, ms: int = 200, fraction: float = 0.6) -> dict`. Backward-compatible: the existing positional `swipe(b, s, 100, 200, 100, 800)` call still works. When `direction` is given (`"up"`/`"down"`/`"left"`/`"right"`), endpoints are computed as fractions of the screen `w`/`h`, then passed to `backend.input_swipe`; the call still re-observes.
- Consumes: screen size from the last snapshot (`session.last["screen"]["w"|"h"]`) when present, else `backend.wm_size()`.

**Direction semantics (content-scroll convention, matches the README "scroll" verbs):** a `direction` names the way the *content* should move. To scroll content **up** (reveal what's below), the finger drags upward: start low, end high. Concretely, with screen `w,h` and `f = fraction`, midpoints `cx = w//2`, `cy = h//2`, and span `dy = int(h * f / 2)`, `dx = int(w * f / 2)`:

- `up`    -> `(cx, cy + dy) -> (cx, cy - dy)`  (finger moves up; content scrolls up)
- `down`  -> `(cx, cy - dy) -> (cx, cy + dy)`
- `left`  -> `(cx + dx, cy) -> (cx - dx, cy)`  (finger moves left; content scrolls left)
- `right` -> `(cx - dx, cy) -> (cx + dx, cy)`

The fraction of the screen (default `0.6`) **is** the density-aware scaling (design §13): the swipe distance is a proportion of the physical pixel dimensions reported by `wm_size`, so it scales correctly across devices of different resolution/density rather than using a fixed pixel distance.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_actuator.py  (append)
def test_swipe_direction_up_computes_density_aware_coords():
    s = Session()
    b = ScriptBackend()  # wm_size() == (1080, 2400)
    from phonectl import observer
    observer.observe(b, s)  # seed snapshot so screen dims are known
    snap = actuator.swipe(b, s, direction="up")
    # cx=540, cy=1200, dy=int(2400*0.6/2)=720 -> (540,1920)->(540,480)
    assert ("swipe", 540, 1920, 540, 480, 200) in b.calls
    assert snap["elements"][0]["text"] == "Bluetooth"  # re-observed XML_B

def test_swipe_direction_down_reverses_endpoints():
    s = Session()
    b = ScriptBackend()
    from phonectl import observer
    observer.observe(b, s)
    actuator.swipe(b, s, direction="down")
    assert ("swipe", 540, 480, 540, 1920, 200) in b.calls

def test_swipe_direction_left_uses_width():
    s = Session()
    b = ScriptBackend()
    from phonectl import observer
    observer.observe(b, s)
    actuator.swipe(b, s, direction="left")
    # cx=540, cy=1200, dx=int(1080*0.6/2)=324 -> (864,1200)->(216,1200)
    assert ("swipe", 864, 1200, 216, 1200, 200) in b.calls

def test_swipe_direction_falls_back_to_wm_size_without_snapshot():
    s = Session()
    b = ScriptBackend()
    actuator.swipe(b, s, direction="up")  # no prior observe(); uses backend.wm_size()
    assert ("swipe", 540, 1920, 540, 480, 200) in b.calls

def test_swipe_coordinate_form_still_works():
    s = Session()
    b = ScriptBackend()
    from phonectl import observer
    observer.observe(b, s)
    actuator.swipe(b, s, 100, 200, 100, 800)
    assert ("swipe", 100, 200, 100, 800, 200) in b.calls

def test_swipe_requires_direction_or_coords():
    s = Session()
    b = ScriptBackend()
    with pytest.raises(ValueError):
        actuator.swipe(b, s)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_actuator.py -k swipe -v`
Expected: FAIL — `swipe` has no `direction` parameter (`TypeError: unexpected keyword argument 'direction'`); `test_swipe_records_and_reobserves` (existing) still passes.

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/actuator.py  (replace lines 23-25 — the swipe function)
def _screen_dims(backend, session):
    last = getattr(session, "last", None)
    if last and "screen" in last:
        return last["screen"]["w"], last["screen"]["h"]
    return backend.wm_size()

def _direction_coords(w, h, direction, fraction):
    cx, cy = w // 2, h // 2
    dx, dy = int(w * fraction / 2), int(h * fraction / 2)
    if direction == "up":
        return cx, cy + dy, cx, cy - dy
    if direction == "down":
        return cx, cy - dy, cx, cy + dy
    if direction == "left":
        return cx + dx, cy, cx - dx, cy
    if direction == "right":
        return cx - dx, cy, cx + dx, cy
    raise ValueError(f"unknown swipe direction: {direction!r}")

def swipe(backend, session, x1=None, y1=None, x2=None, y2=None,
          direction: str | None = None, ms: int = 200, fraction: float = 0.6) -> dict:
    if direction is not None:
        w, h = _screen_dims(backend, session)
        x1, y1, x2, y2 = _direction_coords(w, h, direction, fraction)
    if None in (x1, y1, x2, y2):
        raise ValueError("swipe requires a direction or all of x1,y1,x2,y2")
    backend.input_swipe(x1, y1, x2, y2, ms)
    return observer.observe(backend, session)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_actuator.py -v`
Expected: PASS — the six new swipe tests, plus the existing `test_swipe_records_and_reobserves`

- [ ] **Step 5: Wire the CLI direction form (optional within this task — same commit)**

Add a `--dir` choice to the `swipe` subparser and make `coords` optional, so the contract spec §5.2 advertises (`swipe(dir | x1,y1->x2,y2)`) is reachable from the CLI:

```python
# tests/test_cli.py  (append)
def test_cli_swipe_direction_form(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))

    class SwipeBackend(FakeBackend):
        def __init__(self):
            super().__init__()
        def input_swipe(self, x1, y1, x2, y2, ms=200):
            self.calls.append(("swipe", x1, y1, x2, y2, ms))
        def wm_size(self): return (1080, 2400)

    sb = SwipeBackend()
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: sb)
    rc = cli.main(["swipe", "--dir", "up"])
    assert rc == 0
    assert ("swipe", 540, 1920, 540, 480, 200) in sb.calls

def test_cli_swipe_without_dir_or_coords_is_friendly_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    fb = FakeBackend()
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: fb)
    rc = cli.main(["swipe"])            # no --dir, no coords -> no ValueError from unpacking
    assert rc == 2
    assert "swipe requires" in capsys.readouterr().out
    assert fb.calls == []
```

```python
# src/phonectl/cli.py  (replace _cmd_swipe, lines 77-80)
def _cmd_swipe(args):
    if args.dir is not None:
        return _do_action(args, "swipe",
                          lambda b, s: actuator.swipe(b, s, direction=args.dir),
                          {"dir": args.dir})
    if len(args.coords) != 4:
        print("phonectl: swipe requires --dir or exactly 4 coords (X1 Y1 X2 Y2)")
        return 2
    x1, y1, x2, y2 = args.coords
    return _do_action(args, "swipe", lambda b, s: actuator.swipe(b, s, x1, y1, x2, y2),
                      {"coords": args.coords})
```

```python
# src/phonectl/cli.py  (replace the swipe subparser block, lines 143-146)
    sw = sub.add_parser("swipe")
    sw.add_argument("coords", nargs="*", type=int, metavar=("X1", "Y1", "X2", "Y2"))
    sw.add_argument("--dir", choices=["up", "down", "left", "right"], default=None)
    sw.add_argument("--yes", action="store_true")
    sw.set_defaults(func=_cmd_swipe)
```

Note on the metavar: `argparse` accepts a tuple metavar with `nargs="*"`; keep the existing four-element `("X1", "Y1", "X2", "Y2")` so `--help` still reads `swipe [X1 [Y1 [X2 [Y2]]]]` rather than regressing to a single blob. `nargs="*"` lets `coords` be empty when `--dir` is given; the explicit `len(args.coords) != 4` guard turns a bare `phonectl swipe` (no `--dir`, no coords) into a friendly message + exit 2 instead of an unpacking `ValueError`.

Run: `pytest tests/test_cli.py -k swipe -v` ; Expected: PASS (both new tests — the `--dir` form and the friendly `len != 4` error; the existing positional-coords path is unaffected because `nargs="*"` still accepts four ints).

- [ ] **Step 6: Run the full suite**

Run: `pytest -v`
Expected: PASS (all files)

- [ ] **Step 7: Commit**

```bash
git add src/phonectl/actuator.py src/phonectl/cli.py tests/test_actuator.py tests/test_cli.py
git commit -m "feat: named swipe directions with density-aware screen-fraction scaling"
```

---

### Task 10: Docs — design §5.1 element example gains `content_desc`; fix confirmed README drift

**Files:**
- Modify: `docs/superpowers/specs/2026-06-20-phonectl-adb-bridge-design.md` (lines 76-82: the `observe()` JSON example)
- Modify: `README.md` (only the lines confirmed to mismatch the code — see Step 2)
- Test: none (documentation task)

**Interfaces:** none.

The shipped element dict (`ui_parser.parse_elements`) includes a `content_desc` key, but the design §5.1 example omits it. Update the example to match the real shape.

- [ ] **Step 1: Add `content_desc` to the design §5.1 element example**

In `docs/superpowers/specs/2026-06-20-phonectl-adb-bridge-design.md`, the element object currently reads:

Before (lines 77-78):
```json
    {"i": 7, "text": "Wi-Fi", "id": "android:id/title", "class": "TextView",
     "clickable": true, "bounds": [44, 380, 1036, 520], "center": [540, 450]}
```

After:
```json
    {"i": 7, "text": "Wi-Fi", "id": "android:id/title", "class": "TextView",
     "content_desc": "", "clickable": true, "bounds": [44, 380, 1036, 520],
     "center": [540, 450]}
```

This matches the keys produced in `src/phonectl/ui_parser.py` `parse_elements` (`i, text, id, class, content_desc, clickable, bounds, center`).

- [ ] **Step 2: Fix confirmed README drift only**

Verify each candidate against the code before editing; change only what is provably wrong. Run the grep/inspection first:

```bash
grep -n "scroll up\|dry-run\|orientation\|swipe" /root/phonectl/README.md
```

Confirmed-against-code edits to make:

1. **Swipe section gains the direction form** (now real after Task 9). README lines 85-91 currently show only the coordinate form. Add the direction usage and correct the scroll-direction comment to the content-scroll convention implemented in Task 9.

Before (README lines 85-91):
```markdown
### `swipe`

Swipe from (X1, Y1) to (X2, Y2).

```bash
phonectl swipe 540 1600 540 400    # scroll up
```
```

After:
```markdown
### `swipe`

Swipe by named direction (preferred — distance scales with screen size) or from (X1, Y1) to (X2, Y2).

```bash
phonectl swipe --dir up            # finger drags up; content scrolls up
phonectl swipe --dir down
phonectl swipe 540 1600 540 400    # explicit coordinates
```
```

2. **Dry-run row** (README line 151) currently says dry-run "prints what would have been done, but does **not** inject any input and does **not** write to the audit log." After Task 4 it also emits the observed snapshot. Update only if Task 4 has landed:

Before (README line 151):
```markdown
| `dry-run` | Observes the screen, prints what would have been done, but does **not** inject any input and does **not** write to the audit log. |
```

After:
```markdown
| `dry-run` | Observes the screen and emits the snapshot as a preview, prints what would have been done, but does **not** inject any input and does **not** write to the audit log. |
```

Do **not** invent other README changes; if a candidate line cannot be confirmed wrong against the code, leave it.

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-06-20-phonectl-adb-bridge-design.md README.md
git commit -m "docs: add content_desc to design element example; fix README swipe/dry-run drift"
```

---

## Dependencies

None on the other five follow-up plans. This plan stands alone and can be implemented in any session order. Internal note: Task 10's README/dry-run and swipe edits assume Tasks 4 and 9 have landed earlier *in this same plan* — each Task 10 step is gated with an "only if … has landed" qualifier so it is safe regardless of intra-plan ordering.

## Not in this plan (other follow-up plans own these)

- Resilience: `errors.py` hierarchy, `ensure()` auto-WAKEUP, observe retry/settle, `last_port` probe (plan 1).
- Safety: `rate_limit_per_min`, `guarded_packages` (plan 2).
- Setup wizard (plan 3), MCP server (plan 4), AccessibilityService APK backend (plan 5).
