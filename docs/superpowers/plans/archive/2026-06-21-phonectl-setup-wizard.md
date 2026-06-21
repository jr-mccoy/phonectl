> **SUPERSEDED 2026-06-22** — folded into `docs/superpowers/phonectl-platform-roadmap.md` (Phase 1.4; extended with modular `setup <module>` + diagnostics bundle). Task-level re-homing is in `docs/superpowers/plans/2026-06-22-phonectl-remaining-plans-meta-plan.md`. Kept for traceability; do not execute as-is.

# phonectl Interactive `setup` Onboarding Wizard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a fully-testable interactive `phonectl setup` wizard that walks a new user through build-step-zero (detect `adb`, guide Wireless Debugging, pair, connect, verify, persist config) with injectable `prompt`/`out`/`Connection` so no real `input()` and no real device are needed in tests.

**Architecture:** A new pure-Python module `src/phonectl/setup.py` exposes `run_setup(conn, prompt=input, out=print, which=shutil.which, exists=os.path.exists) -> int` that orchestrates an existing `Connection` (its `pair`/`connect` already wrap `adb`). The wizard touches no `adb`/`subprocess` itself — all device contact stays behind `Connection`/`AdbBackend` — so it is driven in tests by scripted `prompt` answers, a recording fake `Connection`, and a fake `which`/`exists`. The `cli` gains a `setup` verb that constructs the real runtime and delegates to `run_setup`.

**Tech Stack:** Python 3 (stdlib only: `shutil`, `os`, `argparse`, `json`), `pytest` for tests, existing `phonectl.connection.Connection` + `phonectl.config` for persistence, `adb` (android-tools) reached only through `Connection`.

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

- Uses `Connection.pair(addr, code)` and `Connection.connect(addr)`, which **exist today** (`src/phonectl/connection.py`). This plan stands alone and does not require the resilience plan (Plan 1) to land first.
- **Optional, not a hard dependency:** if Plan 1 (resilience) has already landed and added a reconnect/port-probe helper (e.g. `Connection.reconnect()` or a persisted `last_port`), the wizard's "already connected" fast-path and an optional `reconnect` branch become richer. This plan gates that behavior with `hasattr(conn, "reconnect")` so it degrades gracefully when run first. Task 4 implements that gate; do not hard-import any resilience symbol.

## File Structure (delta from the core plan)

```
phonectl/
├── src/phonectl/
│   ├── setup.py        # NEW: run_setup(conn, prompt, out, which, exists) wizard
│   └── cli.py          # MODIFY: add `setup` subcommand + _cmd_setup
└── tests/
    └── test_setup.py   # NEW: scripted-prompt wizard tests (no device, no input())
```

Note on naming: the module is `src/phonectl/setup.py` — a package submodule imported as `phonectl.setup`. It is **not** a distutils/setuptools `setup.py` build script (packaging is `pyproject.toml`-only here), so the name is safe and does not collide with the build system.

---

### Task 1: `setup.run_setup` happy path — pair, connect, verify, persist

**Files:**
- Create: `src/phonectl/setup.py`
- Test: `tests/test_setup.py`

**Interfaces:**
- Produces:
  - `setup.run_setup(conn, prompt=input, out=print, which=shutil.which, exists=os.path.exists) -> int`
    - `conn` is a duck-typed `Connection` exposing `pair(addr: str, code: str) -> None`, `connect(addr: str) -> None`, `cfg: dict`, and `backend` (with `serial: str | None` and `get_state() -> str`).
    - `prompt(msg: str) -> str` returns the user's typed line (injected as a scripted list in tests).
    - `out(msg: str) -> None` sinks human-facing lines (injected as a recorder in tests).
    - `which(name: str) -> str | None` resolves a binary on PATH (default `shutil.which`).
    - `exists(path: str) -> bool` tests a filesystem path (default `os.path.exists`).
    - Returns `0` on success, non-zero on a handled abort (later tasks add the abort paths).
- Consumes: `phonectl.connection.Connection` (today) and `phonectl.config.save` (to persist the config dict). The wizard does **not** call `config.get_mode`; it seeds the persisted default with `conn.cfg.setdefault("mode", "auto")` (matching `get_mode`'s own `"auto"` default) so a fresh config lands with `mode="auto"` written to disk.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_setup.py
from phonectl import setup


class RecordingConn:
    """Duck-typed Connection double: records pair/connect, scripts get-state."""
    def __init__(self, states, cfg=None):
        self.cfg = cfg if cfg is not None else {}
        self.pairs = []
        self.connects = []
        self.backend = _RecordingBackend(states)

    def pair(self, addr, code):
        self.pairs.append((addr, code))

    def connect(self, addr):
        self.connects.append(addr)
        self.backend.serial = addr
        self.cfg["serial"] = addr


class _RecordingBackend:
    def __init__(self, states):
        self.serial = None
        self._states = list(states)

    def get_state(self):
        return self._states.pop(0) if len(self._states) > 1 else self._states[0]


def scripted(answers):
    it = iter(answers)
    return lambda _msg="": next(it)


def collector():
    lines = []
    return lines, lambda msg="": lines.append(str(msg))


def test_happy_path_pairs_connects_and_persists(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    conn = RecordingConn(states=["device"])
    out_lines, out = collector()
    rc = setup.run_setup(
        conn,
        prompt=scripted(["127.0.0.1:37000", "482913", "127.0.0.1:41000"]),
        out=out,
        which=lambda name: "/usr/bin/adb",
        exists=lambda path: True,  # adbkey present
    )
    assert rc == 0
    assert conn.pairs == [("127.0.0.1:37000", "482913")]
    assert conn.connects == ["127.0.0.1:41000"]
    assert conn.cfg["serial"] == "127.0.0.1:41000"
    assert conn.cfg["mode"] == "auto"  # default mode persisted
    assert conn.cfg["last_port"] == "41000"  # connect port persisted for fast reconnect
    # config was saved to disk under PHONECTL_HOME
    from phonectl import config
    saved = config.load()
    assert saved["serial"] == "127.0.0.1:41000"
    assert saved["last_port"] == "41000"
    # a success line was emitted
    assert any("connected" in line.lower() for line in out_lines)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_setup.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'phonectl.setup'`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/setup.py
from __future__ import annotations

import os
import shutil

from phonectl import config

ADBKEY_PATH = os.path.expanduser("~/.android/adbkey")

INSTALL_GUIDANCE = (
    "adb is not installed. In Termux run:\n"
    "    pkg install android-tools\n"
    "then re-run: phonectl setup"
)

WIRELESS_GUIDANCE = (
    "Wireless debugging requires Android 11 or newer.\n"
    "On the phone, enable:\n"
    "    Settings > Developer options > Wireless debugging\n"
    "Tap 'Pair device with pairing code' to read the pairing host:port and 6-digit code."
)


def run_setup(conn, prompt=input, out=print, which=shutil.which, exists=os.path.exists) -> int:
    out("phonectl setup — let's get your phone connected.")

    # 1) adb presence (guidance only; never silent-install).
    if which("adb") is None:
        out(INSTALL_GUIDANCE)
        return 1

    # 2) Guide Wireless Debugging, then pair.
    out(WIRELESS_GUIDANCE)
    pair_addr = prompt("Pairing host:port (e.g. 127.0.0.1:37000): ").strip()
    code = prompt("6-digit pairing code: ").strip()
    conn.pair(pair_addr, code)

    # 3) Connect.
    connect_addr = prompt("Connect host:port (e.g. 127.0.0.1:41000): ").strip()
    conn.connect(connect_addr)

    # 4) Verify get-state == 'device'.
    state = conn.backend.get_state()
    if state != "device":
        out(f"phonectl: device did not come online (get-state={state!r}). "
            "Re-check Wireless debugging and re-run: phonectl setup")
        return 2

    # 5) Persist config (serial already set by connect()); set a default mode and
    #    record the connect port so the resilience plan's reconnect can retry it first
    #    (spec §7 step 5: "persist ... last ports"). Storing only the port (not the whole
    #    addr) is intentional — the host is always loopback; the port is the volatile part.
    conn.cfg.setdefault("mode", "auto")
    if ":" in connect_addr:
        conn.cfg["last_port"] = connect_addr.rsplit(":", 1)[-1]
    config.save(conn.cfg)

    # adb generates ~/.android/adbkey on first server start; confirm, never fabricate.
    if exists(ADBKEY_PATH):
        out(f"adb identity key present: {ADBKEY_PATH}")
    else:
        out(f"note: {ADBKEY_PATH} not found yet; adb creates it on first server start.")

    out(f"phonectl: connected (serial={conn.backend.serial}, state={state}).")
    return 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_setup.py -v`
Expected: PASS (1 test).

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/setup.py tests/test_setup.py
git commit -m "feat: setup.run_setup wizard happy path (pair/connect/verify/persist)"
```

---

### Task 2: Missing-`adb` guidance branch (characterization test, no silent install)

> **Note on TDD framing:** the missing-`adb` branch already ships in Task 1's `run_setup` (the `which("adb") is None` guard). This task adds a **characterization test** that pins that branch's contract (exit code, guidance string, no pair/connect, no silent install). It is honestly a regression-lock, not a red→green cycle — there is no genuine failing-test step, and the plan does not fabricate one.

**Files:**
- Modify: `src/phonectl/setup.py` (no production change expected — the Task 1 branch already exists; this task pins its behavior with a test, and only tightens the message if an assertion exposes a real gap)
- Test: `tests/test_setup.py` (extend)

**Interfaces:**
- Consumes/Produces: same `run_setup` signature as Task 1. This task asserts that when `which("adb") is None`, the wizard prints `pkg install android-tools`, returns a non-zero code, and **never** calls `conn.pair`/`conn.connect`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_setup.py  (append)
def test_missing_adb_prints_termux_guidance_and_does_not_pair(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    conn = RecordingConn(states=["device"])
    out_lines, out = collector()
    rc = setup.run_setup(
        conn,
        prompt=scripted([]),  # must never be consumed
        out=out,
        which=lambda name: None,   # adb absent
        exists=lambda path: True,
    )
    assert rc == 1
    assert conn.pairs == [] and conn.connects == []
    joined = "\n".join(out_lines)
    assert "pkg install android-tools" in joined
    # never attempt a silent install: no install verb should be mentioned as performed
    assert "installing" not in joined.lower()
```

- [ ] **Step 2: Run the characterization test**

Run: `pytest tests/test_setup.py::test_missing_adb_prints_termux_guidance_and_does_not_pair -v`
Expected: PASS — the `which("adb") is None` branch from Task 1 already returns `1`, prints `pkg install android-tools`, and never calls `conn.pair`/`conn.connect`. Do **not** fabricate a red step (e.g. a contrived `assert rc == 7`); this test locks an already-correct contract.

If this test instead FAILS, that is a real signal of a Task 1 regression — debug it as a genuine red (most likely the guidance string drifted or the early `return 1` was lost), then proceed to Step 3.

- [ ] **Step 3: Production change (only if the test exposed a gap)**

If Step 2 passed, no production change is needed — the contract is pinned by a passing characterization test, so go straight to Step 4. The minimal fix, *only if Step 2 surfaced a real failure*, is to restore the `which("adb") is None` guard so it (a) returns `1` and (b) emits `INSTALL_GUIDANCE` containing `pkg install android-tools` **before** any `prompt`/`pair`/`connect` call.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_setup.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add tests/test_setup.py
git commit -m "test: pin missing-adb guidance branch (no silent install)"
```

---

### Task 3: Verify-failure and persistence-isolation branches (characterization tests)

> **Note on TDD framing:** both branches under test — verify-failure (`get_state()` never returns `"device"` → return `2`, no wizard-level `config.save`) and adbkey-absent (emit the "first server start" note, never fabricate the file) — already ship in Task 1's `run_setup`. These two tests are **characterization tests** that lock those contracts and the verify→return ordering invariant; they are not red→green cycles and the plan does not pretend otherwise. (The genuinely new red→green work in this plan is Tasks 1, 4, and 5.)

**Files:**
- Modify: `src/phonectl/setup.py` (no production change expected — the verify branch and adbkey note exist from Task 1; this task pins them with two regression tests)
- Test: `tests/test_setup.py` (extend)

**Interfaces:**
- Consumes/Produces: `run_setup` as above. Asserts: when `get_state()` never returns `"device"`, the wizard returns `2`, emits a re-enable message, and does **not** write `config.json` (so a half-finished setup never persists a dead serial). Also asserts the adbkey-absent path emits the "adb creates it on first server start" note rather than fabricating the file.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_setup.py  (append)
def test_verify_failure_returns_2_and_does_not_persist(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    conn = RecordingConn(states=["offline"])  # never reaches "device"
    out_lines, out = collector()
    rc = setup.run_setup(
        conn,
        prompt=scripted(["127.0.0.1:37000", "482913", "127.0.0.1:41000"]),
        out=out,
        which=lambda name: "/usr/bin/adb",
        exists=lambda path: True,
    )
    assert rc == 2
    # pair/connect were attempted, but no config.json was written on failure
    assert conn.connects == ["127.0.0.1:41000"]
    assert not (tmp_path / "config.json").exists()
    assert any("did not come online" in line for line in out_lines)


def test_adbkey_absent_emits_note_and_does_not_fabricate(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    conn = RecordingConn(states=["device"])
    out_lines, out = collector()
    checked = []

    def fake_exists(path):
        checked.append(path)
        return False  # adbkey missing

    rc = setup.run_setup(
        conn,
        prompt=scripted(["127.0.0.1:37000", "482913", "127.0.0.1:41000"]),
        out=out,
        which=lambda name: "/usr/bin/adb",
        exists=fake_exists,
    )
    assert rc == 0
    assert any("first server start" in line for line in out_lines)
    # we only *checked* for adbkey; we never tried to create it
    assert any(p.endswith("adbkey") for p in checked)
```

- [ ] **Step 2: Run the characterization tests**

Run: `pytest tests/test_setup.py -v`
Expected: BOTH new tests PASS against the unchanged Task 1 code:

- `test_verify_failure_returns_2_and_does_not_persist` passes because Task 1 already returns `2` on a non-`device` state *before* the wizard's `config.save`, emits the `did not come online` line, and — because the `RecordingConn.connect` double deliberately omits `config.save` (unlike the real `Connection.connect`) — no `config.json` is written, so `not (tmp_path / "config.json").exists()` holds.
- `test_adbkey_absent_emits_note_and_does_not_fabricate` passes because Task 1 already calls `exists(ADBKEY_PATH)` and, when it returns `False`, emits the "first server start" note without creating the file.

Do **not** fabricate a red here. If either test FAILS, treat it as a real regression in the Task 1 verify/adbkey ordering and debug it before continuing.

- [ ] **Step 3: Production change (none expected — ordering invariant only)**

No production change is needed: the verify→`return 2`→(then) `config.save` ordering and the adbkey note already exist in Task 1's `run_setup`. This task's contribution is the two regression tests that lock that ordering, plus this documented invariant for the implementer:

```python
    # 4) Verify get-state == 'device'.
    state = conn.backend.get_state()
    if state != "device":
        out(f"phonectl: device did not come online (get-state={state!r}). "
            "Re-check Wireless debugging and re-run: phonectl setup")
        return 2

    # 5) Persist config only after a verified connection.
    conn.cfg.setdefault("mode", "auto")
    config.save(conn.cfg)
```

Real-vs-double note for the implementer: the real `phonectl.connection.Connection.connect()` calls `config.save(self.cfg)` itself (`src/phonectl/connection.py:16-20`), so against the *real* Connection a failed-verify run will already have persisted the serial inside `connect()`. The wizard does not undo that (it is not a runtime bug — a stale serial is harmless and gets overwritten on the next successful setup), but the **test double** `RecordingConn.connect` intentionally omits `config.save`, which is what lets `test_verify_failure_..._does_not_persist` assert the no-file invariant deterministically at the *wizard* boundary. If a future change makes "never persist a dead serial" a hard requirement against the real Connection, that belongs in a follow-up that moves the serial write out of `connect()` and into a verify-gated wizard save — out of scope here.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_setup.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/setup.py tests/test_setup.py
git commit -m "feat: setup verify-failure aborts without persisting + adbkey note"
```

---

### Task 4: Idempotent re-run — `already connected` fast-path (+ optional reconnect)

**Files:**
- Modify: `src/phonectl/setup.py`
- Test: `tests/test_setup.py` (extend)

**Interfaces:**
- Produces (extends `run_setup`): when `conn.backend.get_state() == "device"` **at entry**, skip the whole pair/connect dance, emit an "already connected" line, ensure `cfg["mode"]` has a default, persist, and return `0` — without consuming any `prompt` answers. If the resilience plan has landed and `conn` exposes a `reconnect()` method, and entry state is not `"device"` but a configured `serial` exists, offer a one-keypress reconnect before falling back to the full pairing flow. The reconnect branch is gated by `hasattr(conn, "reconnect")` so this plan runs correctly when sequenced first.
- Consumes: same.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_setup.py  (append)
def test_already_connected_fast_path_skips_pairing(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    conn = RecordingConn(states=["device"], cfg={"serial": "127.0.0.1:41000"})
    conn.backend.serial = "127.0.0.1:41000"
    out_lines, out = collector()

    def no_prompt(_msg=""):
        raise AssertionError("fast-path must not prompt the user")

    rc = setup.run_setup(
        conn,
        prompt=no_prompt,
        out=out,
        which=lambda name: "/usr/bin/adb",
        exists=lambda path: True,
    )
    assert rc == 0
    assert conn.pairs == [] and conn.connects == []
    assert any("already connected" in line.lower() for line in out_lines)


def test_reconnect_branch_used_when_available(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))

    class ReconnectConn(RecordingConn):
        def __init__(self):
            # offline at entry, then 'device' after reconnect()
            super().__init__(states=["offline", "device"], cfg={"serial": "127.0.0.1:41000"})
            self.reconnects = 0

        def reconnect(self):
            self.reconnects += 1
            self.backend.serial = "127.0.0.1:41000"

    conn = ReconnectConn()
    out_lines, out = collector()
    rc = setup.run_setup(
        conn,
        prompt=scripted(["y"]),  # accept the reconnect offer
        out=out,
        which=lambda name: "/usr/bin/adb",
        exists=lambda path: True,
    )
    assert rc == 0
    assert conn.reconnects == 1
    assert conn.pairs == [] and conn.connects == []  # reconnect avoided a full re-pair
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_setup.py -v`
Expected: FAIL — `test_already_connected_fast_path_skips_pairing` fails because Task 1's `run_setup` always prompts (the `no_prompt` double raises `AssertionError`), and `test_reconnect_branch_used_when_available` fails because there is no reconnect gate yet.

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/setup.py  (insert the fast-path + reconnect gate after the adb-presence
#  check and before the WIRELESS_GUIDANCE / pairing block)

    # Idempotent fast-path: already online, nothing to pair.
    if conn.backend.get_state() == "device":
        conn.cfg.setdefault("mode", "auto")
        config.save(conn.cfg)
        out(f"phonectl: already connected (serial={conn.backend.serial}). Nothing to do.")
        return 0

    # Optional reconnect (only if a resilience-era reconnect() exists and we have a serial).
    if hasattr(conn, "reconnect") and conn.cfg.get("serial"):
        answer = prompt(
            f"Last serial {conn.cfg['serial']} is offline. Try reconnecting? [Y/n]: "
        ).strip().lower()
        if answer in ("", "y", "yes"):
            conn.reconnect()
            if conn.backend.get_state() == "device":
                conn.cfg.setdefault("mode", "auto")
                config.save(conn.cfg)
                out(f"phonectl: reconnected (serial={conn.backend.serial}).")
                return 0
            out("phonectl: reconnect failed; falling back to full pairing.")
```

Place this block immediately after the `if which("adb") is None:` guard. The existing WIRELESS_GUIDANCE / pair / connect / verify / persist sequence remains as the fall-through for the not-yet-paired case.

Important ordering note for the implementer: Task 1's verify step calls `conn.backend.get_state()` once. The fast-path above adds an **entry** `get_state()` call, so the `RecordingConn` state scripts now account for two reads in the non-fast-path flow (entry-state, then verify-state). Trace `_RecordingBackend.get_state` (pop while `len>1`, else return the last element):

- `states=["offline"]` (verify-failure test): entry read returns `"offline"` (len==1, no pop, no short-circuit), the pairing path runs, verify read returns `"offline"` again → rc 2. Unchanged, still correct.
- `states=["device"]` (already-connected fast-path test, reconnect test uses its own script): entry read returns `"device"` and short-circuits. Correct.
- `states=["device", "offline"]` would be wrong for any flow that must reach verify — not used.

Two fixtures from earlier tasks seed `states=["device"]` for flows that are expected to traverse the **full** pair/connect/verify path, and both would now wrongly take the entry fast-path. Update BOTH in this task to seed an offline-then-device script so they reach the pairing path (entry read pops `"offline"`, verify read returns `"device"`):

1. `test_happy_path_pairs_connects_and_persists` (Task 1) — change `states=["device"]` to:

```python
    conn = RecordingConn(states=["offline", "device"])  # offline at entry, device after connect
```

2. `test_adbkey_absent_emits_note_and_does_not_fabricate` (Task 3) — change `states=["device"]` to:

```python
    conn = RecordingConn(states=["offline", "device"])  # offline at entry so the fast-path
    #  does not short-circuit before the adbkey-presence note is emitted
```

This is the BLOCKER fix: without (2), the entry fast-path returns `0` before `exists(adbkey)` is ever called, so `test_adbkey_absent`'s `any("first server start" ...)` and `any(p.endswith("adbkey") ...)` assertions both fail. The remaining fixtures (`test_missing_adb` returns before any `get_state`; `test_verify_failure` `states=["offline"]`; the Task 4 fast-path/reconnect fixtures) need no change.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_setup.py -v`
Expected: PASS (6 tests). This count holds only with BOTH fixture updates above applied in this task: the two new Task 4 tests turn green from the new code, and `test_happy_path` + `test_adbkey_absent` stay green because their `states` fixtures now seed `["offline", "device"]` so they bypass the entry fast-path and traverse the full pair/connect/verify/adbkey path.

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/setup.py tests/test_setup.py
git commit -m "feat: idempotent setup fast-path + optional reconnect branch"
```

---

### Task 5: `cli setup` verb — wire wizard to the real runtime

**Files:**
- Modify: `src/phonectl/cli.py` (add `_cmd_setup` after `_cmd_doctor`, whose body ends at line 118; register the `setup` subparser inside `build_parser` after the `doctor` parser block at lines 164-165, i.e. immediately before the `return p` on line 166; import `setup`)
- Test: `tests/test_cli.py` (extend)

**Interfaces:**
- Produces: CLI subcommand `setup`. `_cmd_setup(args) -> int` loads config, builds the runtime via the existing `build_runtime(cfg)`, and calls `setup.run_setup(conn, ...)` with the default `prompt=input`/`out=print` (real I/O in production). Tests inject a fake backend through the existing `_make_backend` monkeypatch seam and a fake `run_setup` to assert wiring without touching `input()`.
- Consumes: `phonectl.setup.run_setup` (Tasks 1-4), existing `_make_backend` (`src/phonectl/cli.py:11-12`) and `build_runtime` (`src/phonectl/cli.py:15-19`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli.py  (append)
def test_setup_verb_wires_runtime_to_run_setup(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    fb = FakeBackend()
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: fb)

    captured = {}

    def fake_run_setup(conn, **kwargs):
        captured["conn"] = conn
        captured["kwargs"] = kwargs
        return 0

    from phonectl import setup as setup_mod
    monkeypatch.setattr(setup_mod, "run_setup", fake_run_setup)
    rc = cli.main(["setup"])
    assert rc == 0
    # the wired Connection wraps our fake backend
    assert captured["conn"].backend is fb
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli.py::test_setup_verb_wires_runtime_to_run_setup -v`
Expected: FAIL (`argparse` rejects the unknown `setup` subcommand / `cli` has no `_cmd_setup`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/cli.py — add to the imports at the top
from phonectl import setup as setup_mod
```

```python
# src/phonectl/cli.py — add after _cmd_doctor (its body ends at line 118)
def _cmd_setup(args):
    cfg = config.load()
    backend, session, conn = build_runtime(cfg)
    return setup_mod.run_setup(conn)
```

```python
# src/phonectl/cli.py — add inside build_parser, after the doctor parser block
#  (lines 164-165), immediately before `return p` (line 166)
    su = sub.add_parser("setup")
    su.set_defaults(func=_cmd_setup)
```

Note: `_cmd_setup` calls `setup_mod.run_setup(conn)` via the module attribute so `monkeypatch.setattr(setup_mod, "run_setup", ...)` in the test intercepts it. The production call uses `run_setup`'s defaults (`prompt=input`, `out=print`), giving real interactive I/O.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli.py -v`
Expected: PASS (existing CLI tests + the new `setup` wiring test).

- [ ] **Step 5: Run the full suite**

Run: `pytest -v`
Expected: PASS (all files: `test_setup.py` 6 + the new CLI test + every prior test green).

- [ ] **Step 6: Commit**

```bash
git add src/phonectl/cli.py tests/test_cli.py
git commit -m "feat: phonectl setup CLI verb wiring run_setup to the runtime"
```

---

### Task 6: README onboarding section + manual real-device walkthrough

**Files:**
- Modify: `README.md` (add a "Getting started: `phonectl setup`" section)
- Create: `docs/setup-walkthrough.md`

**Interfaces:** none (documentation + manual procedure).

This task documents the wizard for real users. It is manual because pairing requires a real phone and a human reading the 6-digit code; do not automate it in CI. The Python-side logic is already fully covered by tests in Tasks 1-5 (red→green for Tasks 1, 4, 5; characterization/regression tests for the already-shipping branches in Tasks 2-3) — this task only adds prose and a manual checklist.

- [ ] **Step 1: Write the README section**

Add to `README.md` under a new heading `## Getting started: \`phonectl setup\``:
- One sentence: "Run `phonectl setup` and answer the prompts; it detects `adb`, guides Wireless Debugging, pairs, connects, verifies, and saves your config."
- The `pkg install android-tools` note for when `adb` is absent.
- A line stating that re-running `setup` is safe (idempotent) and short-circuits when already connected.

- [ ] **Step 2: Write `docs/setup-walkthrough.md`**

Document the manual flow exactly as the wizard prompts:

```text
$ phonectl setup
phonectl setup — let's get your phone connected.
Wireless debugging requires Android 11 or newer.
On the phone, enable:
    Settings > Developer options > Wireless debugging
Tap 'Pair device with pairing code' to read the pairing host:port and 6-digit code.
Pairing host:port (e.g. 127.0.0.1:37000): 127.0.0.1:37000
6-digit pairing code: 482913
Connect host:port (e.g. 127.0.0.1:41000): 127.0.0.1:41000
adb identity key present: ~/.android/adbkey
phonectl: connected (serial=127.0.0.1:41000, state=device).
```

Include the "adb absent" branch (`pkg install android-tools`) and the "already connected" fast-path output. Add a note that the connect port is volatile across phone sleep/reboot (per the design spec §13 / follow-up §2.1); the wizard persists the last connect port as `last_port` in `config.json` so the resilience plan's reconnect can retry it first, but until that auto-reconnect lands, re-running `phonectl setup` is the recovery path. Also note that the Android 11+ requirement is currently surfaced as guidance only (active version gating is a deferred follow-up — see "Spec §7 coverage and explicit deferrals").

- [ ] **Step 3: Manual device verification (no CI)**

On a real paired device from inside the PRoot distro:

```bash
pkg install android-tools      # if `adb` is missing
phonectl setup                 # answer the three prompts off the Wireless Debugging screen
phonectl doctor                # expect: connected (serial=..., state=device)
phonectl setup                 # re-run: expect the "already connected" fast-path, no prompts
```

Expected: first run pairs + connects + persists `config.json`; `doctor` confirms `state=device`; the second `setup` short-circuits with "already connected".

- [ ] **Step 4: Commit**

```bash
git add README.md docs/setup-walkthrough.md
git commit -m "docs: phonectl setup onboarding section and manual walkthrough"
```

---

## Notes on testability boundaries

- **Fully test-covered at the Python level (Tasks 1-5):** every branch of `run_setup` — adb-present/absent, pair, connect, verify success/failure, persistence (serial/mode/last_port), adbkey present/absent, idempotent fast-path, optional reconnect — and the `cli setup` wiring. The new behavior (happy path, fast-path, reconnect gate, CLI wiring) is genuine red→green TDD; the already-shipping branches (missing-adb in Task 2, verify-failure + adbkey-absent in Task 3) are pinned by characterization tests rather than fabricated reds. No test calls real `input()` or touches a real device; `prompt`, `out`, `which`, `exists`, and the `Connection` double are all injected, and `PHONECTL_HOME` isolation keeps config writes inside `tmp_path`.
- **Not auto-testable (Task 6, manual):** the real `adb pair`/`adb connect` handshake against a physical phone, reading the volatile 6-digit code, and the first-server-start creation of `~/.android/adbkey`. These require a human and hardware; they are scoped to a manual walkthrough, not fabricated test steps.
- **No native code in this plan.** The wizard is pure Python orchestrating the existing `Connection`; there is no Kotlin/APK component here, so nothing is deferred to an Android spec.

## Spec §7 coverage and explicit deferrals

This plan implements spec §7 steps 1, 3, and (partially) 5; it explicitly defers steps 2 and 4. Recorded here so no in-scope bullet is silently dropped:

- **§7.1 Install `android-tools`** — covered: Task 2 detects missing `adb` and prints the `pkg install android-tools` guidance (we never silent-install).
- **§7.2 Android 11+ detection/gating** — **explicitly deferred (not silently omitted).** The wizard surfaces the requirement as *static guidance* (`WIRELESS_GUIDANCE` now opens with "Wireless debugging requires Android 11 or newer."), but it does **not** actively detect the OS version or hard-gate on it. Rationale: active detection needs a new device read (`adb shell getprop ro.build.version.release` → parse the major version), which by the architecture invariant must live behind `AdbBackend`, not in the wizard — the wizard touches no `adb`/`subprocess`. Adding a `backend.android_release() -> int | None` getter plus a `< 11` abort branch is a self-contained follow-up. It is deferred (not done here) because: (a) the no-root Wireless-Debugging pairing flow physically cannot complete on <11 anyway — pairing-code Wireless Debugging is an Android-11 feature — so a sub-11 device fails fast at the pair/verify step with the existing `did not come online` abort (rc 2); and (b) the spec's own §3 topology already assumes "unrooted Android 11+", making active gating a defense-in-depth nicety rather than a correctness gate. **Follow-up task (when picked up):** add `AdbBackend.android_release()`, call it in `run_setup` right after the adb-presence check, and abort with a `requires Android 11+` message + non-zero rc if the major version is `< 11`; test it with the injected backend double exactly as the other branches are tested.
- **§7.4 mDNS auto-discovery** (`adb mdns services`) — deferred, consistent with follow-up §2.1/§3: mDNS does not work reliably under PRoot, so the user hand-types the connect port. The optional reconnect fast-path (Task 4) is `hasattr`-gated so it lights up automatically once the resilience plan lands a discovery/reconnect helper.
- **§7.5 Verify + persist serial/last ports + adbkey** — covered: Task 1 verifies `get-state == "device"`, persists `serial` (via `connect()`) plus `mode` and now `last_port` (the volatile connect port, for the resilience plan's reconnect-first retry), and confirms (never fabricates) the `adbkey`. Full silent-reconnect-after-reboot still depends on Plan 1's reconnect helper, which this plan does not implement.
