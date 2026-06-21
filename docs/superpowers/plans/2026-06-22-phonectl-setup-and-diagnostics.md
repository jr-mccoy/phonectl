# phonectl Setup Wizard + Diagnostics Bundle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Plan 1.4 of the platform roadmap** (`docs/superpowers/phonectl-platform-roadmap.md`). Depends on
**Plan 1.1** for `backend.capabilities()` and the `results` envelope; lands after **Plan 1.3** and is
*opportunistic* on it (`Connection.rediscover`, `last_port`, `backend.mdns_services` — all `hasattr`/
`getattr`-gated so this plan still runs correctly if 1.3 is absent). This is the **last Phase-1 plan** and
closes the onboarding + supportability gap.

This plan **re-homes the superseded `2026-06-21-phonectl-setup-wizard.md` Tasks 1–6 verbatim** and
**extends** them with (a) **modular setup** — `phonectl setup adb|accessibility|notifications|termux-api|
all`, each module reporting its required permission, current status, how to enable it, what capabilities
it unlocks, and its safety implications (strategy §9.1); and (b) a **redacted diagnostics bundle** —
`phonectl doctor --json`/`doctor --bundle <zip>` (strategy §9.3).

**Implementation status (2026-06-21):** ✅ Complete on this branch.

**Goal:** A fully-testable interactive `phonectl setup` that walks a new user through build-step-zero
(detect `adb`, guide Wireless Debugging, pair, connect, verify, persist), reports the status of every
provider module, and emits a support bundle for diagnosis — with injectable `prompt`/`out`/`which`/
`exists`/`Connection` so no real `input()` and no real device are needed in tests.

**Architecture:** A new pure-orchestration module `src/phonectl/setup.py` exposes
`run_setup(conn, prompt, out, which, exists) -> int` (the ADB flow), `module_report(module, *, caps,
which) -> dict` (pure per-module status), and `run_module(module, conn, ...) -> int` (dispatch). All device
contact stays behind `Connection`/`AdbBackend`. A new `src/phonectl/diagnostics.py` exposes
`redact_config(cfg) -> dict` (pure), `collect(backend, cfg) -> dict` (calls only backend methods +
`audit` tail — no direct adb), and `bundle(path, backend, cfg) -> str` (writes a redacted zip via stdlib
`zipfile`). `adb_backend` grows `adb_version()` and `devices()` (the only new device-touching code). `cli`
gains the modular `setup` verb and `doctor --bundle`.

**Tech Stack:** Python 3 (stdlib only: `shutil`, `os`, `json`, `zipfile`, `argparse`); `pytest` for tests;
existing `phonectl.connection.Connection` + `phonectl.config` for persistence; `adb` reached only through
`AdbBackend`/`Connection`.

## Global Constraints

- **stdlib-only at runtime** (Python ≥ 3.9; `pytest` dev-only). `zipfile`/`shutil` are stdlib.
- **ONLY `adb_backend.py` may touch adb/subprocess.** `setup.py` and `diagnostics.py` touch **no** adb —
  they orchestrate `Connection`/`AdbBackend` methods. The bundle's `adb version`/`adb devices -l` come from
  new `AdbBackend` methods.
- **`ui_parser.py` stays pure.** (Untouched by this plan.)
- **Element index `i` is the primary target.** (Untouched.)
- **Every actuator `act()` re-observes.** (Untouched.)
- **Injectable seams** — `prompt`, `out`, `which`, `exists` for setup; `backend`/`cfg` for diagnostics. No
  real `input()`, no real device, no real zip-to-`~` in unit tests.
- **Tests isolate via** `monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))`.
- **Structured-result invariant (Plan 1.1):** `doctor --json` returns a `results` envelope; the bundle
  embeds the `backend.capabilities()` capability doc.
- **Redaction:** the diagnostics bundle never writes secrets — `redact_config` masks sensitive keys and
  the audit tail is metadata-only (`ts`/`verb`/`app`/`hash`, never typed text).
- **One commit per task.**
- **TDD order is non-negotiable.**

## Shared conventions used by this plan

- **`config.json` keys:** existing `serial`, `mode`, plus `last_port` (from Plan 1.3, written here too).
  No new keys are introduced by this plan.
- **`setup.py` is a package submodule** imported as `phonectl.setup` — **not** a setuptools build script
  (packaging is `pyproject.toml`-only here), so the name is safe.
- **Provider modules:** `adb` (built — ADB backend), `accessibility` / `notifications` (companion-APK
  providers, **not built until Phase 4** — reported as unavailable with enable-guidance), `termux-api`
  (optional, **Phase 3.5** — detected at runtime via `which`). `module_report` is honest about what is and
  is not available today.
- **Optional 1.3 seams** (`conn.rediscover`, `backend.mdns_services`, `backend.host_shim_runner`,
  `cfg["last_port"]`) are always accessed via `hasattr`/`getattr` so this plan degrades gracefully.

---

### Task 1: `setup.run_setup` happy path — pair, connect, verify, persist

Re-homes the old setup-wizard plan's Task 1 verbatim.

**Files:**
- Create: `src/phonectl/setup.py`
- Test: `tests/test_setup.py`

**Interfaces:**
- `setup.run_setup(conn, prompt=input, out=print, which=shutil.which, exists=os.path.exists) -> int`
  - `conn` is a duck-typed `Connection` exposing `pair(addr, code)`, `connect(addr)`, `cfg: dict`, and
    `backend` (with `serial` and `get_state()`).
  - Returns `0` on success; non-zero on a handled abort (later tasks add the abort paths).
- Consumes: `phonectl.config.save`. Seeds `conn.cfg.setdefault("mode", "auto")` and records the connect
  port as `cfg["last_port"]` for the resilience reconnect-first retry.

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
        exists=lambda path: True,
    )
    assert rc == 0
    assert conn.pairs == [("127.0.0.1:37000", "482913")]
    assert conn.connects == ["127.0.0.1:41000"]
    assert conn.cfg["serial"] == "127.0.0.1:41000"
    assert conn.cfg["mode"] == "auto"
    assert conn.cfg["last_port"] == "41000"
    from phonectl import config
    saved = config.load()
    assert saved["serial"] == "127.0.0.1:41000"
    assert saved["last_port"] == "41000"
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

    if which("adb") is None:
        out(INSTALL_GUIDANCE)
        return 1

    out(WIRELESS_GUIDANCE)
    pair_addr = prompt("Pairing host:port (e.g. 127.0.0.1:37000): ").strip()
    code = prompt("6-digit pairing code: ").strip()
    conn.pair(pair_addr, code)

    connect_addr = prompt("Connect host:port (e.g. 127.0.0.1:41000): ").strip()
    conn.connect(connect_addr)

    state = conn.backend.get_state()
    if state != "device":
        out(f"phonectl: device did not come online (get-state={state!r}). "
            "Re-check Wireless debugging and re-run: phonectl setup")
        return 2

    conn.cfg.setdefault("mode", "auto")
    if ":" in connect_addr:
        conn.cfg["last_port"] = connect_addr.rsplit(":", 1)[-1]
    config.save(conn.cfg)

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

Re-homes the old setup-wizard plan's Task 2 verbatim.

> **TDD framing:** the missing-`adb` branch already ships in Task 1's `run_setup` (the `which("adb") is
> None` guard). This task adds a **characterization test** pinning that contract; it is a regression-lock,
> not a red→green cycle, and the plan does not fabricate a red step.

**Files:**
- Modify: `src/phonectl/setup.py` (no production change expected — only tighten the message if an assertion
  exposes a real gap)
- Test: `tests/test_setup.py` (extend)

- [ ] **Step 1: Write the characterization test**

```python
# tests/test_setup.py  (append)
def test_missing_adb_prints_termux_guidance_and_does_not_pair(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    conn = RecordingConn(states=["device"])
    out_lines, out = collector()
    rc = setup.run_setup(
        conn,
        prompt=scripted([]),       # must never be consumed
        out=out,
        which=lambda name: None,   # adb absent
        exists=lambda path: True,
    )
    assert rc == 1
    assert conn.pairs == [] and conn.connects == []
    joined = "\n".join(out_lines)
    assert "pkg install android-tools" in joined
    assert "installing" not in joined.lower()
```

- [ ] **Step 2: Run the characterization test**

Run: `pytest tests/test_setup.py::test_missing_adb_prints_termux_guidance_and_does_not_pair -v`
Expected: PASS — Task 1's guard already returns `1`, prints `pkg install android-tools`, and never pairs.
Do **not** fabricate a red. A FAIL here is a real Task-1 regression (guidance string drift or a lost early
`return 1`) — debug it as a genuine red, then proceed to Step 3.

- [ ] **Step 3: Production change (only if Step 2 exposed a gap)**

If Step 2 passed, no production change is needed. The minimal fix, *only if* Step 2 failed, is to restore
the `which("adb") is None` guard so it returns `1` and emits `INSTALL_GUIDANCE` (containing `pkg install
android-tools`) **before** any `prompt`/`pair`/`connect`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_setup.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add tests/test_setup.py
git commit -m "test: pin missing-adb guidance branch (no silent install)"
```

---

### Task 3: Verify-failure + adbkey-absent branches (characterization tests)

Re-homes the old setup-wizard plan's Task 3 verbatim.

> **TDD framing:** both branches — verify-failure (`get_state()` never `"device"` → return `2`, no
> wizard-level `config.save`) and adbkey-absent (emit the "first server start" note, never fabricate the
> file) — already ship in Task 1. These are **characterization tests** locking those contracts and the
> verify→return ordering invariant.

**Files:**
- Modify: `src/phonectl/setup.py` (no production change expected)
- Test: `tests/test_setup.py` (extend)

- [ ] **Step 1: Write the characterization tests**

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
    assert any(p.endswith("adbkey") for p in checked)
```

- [ ] **Step 2: Run the characterization tests**

Run: `pytest tests/test_setup.py -v`
Expected: BOTH new tests PASS against unchanged Task 1 code: `test_verify_failure_...` passes because Task
1 returns `2` *before* the wizard's `config.save` and `RecordingConn.connect` (unlike the real
`Connection.connect`) deliberately omits `config.save`, so no file is written;
`test_adbkey_absent_...` passes because Task 1 calls `exists(ADBKEY_PATH)` and emits the note without
creating the file. Do **not** fabricate a red.

- [ ] **Step 3: Production change (none expected — ordering invariant only)**

No production change. This task contributes the two regression tests plus the documented invariant:
verify (`get_state != "device"` → `return 2`) happens **before** the wizard's `config.save`. (Real-Connection
note: `Connection.connect()` itself calls `config.save`, so against the *real* Connection a failed-verify
run persists a stale serial inside `connect()` — harmless, overwritten on next setup; the `RecordingConn`
double omits that save so the no-file invariant is deterministic at the *wizard* boundary.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_setup.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/setup.py tests/test_setup.py
git commit -m "feat: setup verify-failure aborts without persisting + adbkey note"
```

---

### Task 4: Idempotent re-run — `already connected` fast-path (+ optional rediscover)

Re-homes the old setup-wizard plan's Task 4, **gated on Plan 1.3's `Connection.rediscover`** (the old plan
named it `reconnect`; in this roadmap 1.3 ships `rediscover`).

**Files:**
- Modify: `src/phonectl/setup.py`
- Test: `tests/test_setup.py` (extend)

**Interfaces (extends `run_setup`):** when `conn.backend.get_state() == "device"` **at entry**, skip the
pair/connect dance, emit an "already connected" line, default `cfg["mode"]`, persist, return `0` — without
consuming any `prompt`. If `conn` exposes `rediscover()` (Plan 1.3) and entry state is not `"device"` but a
configured `serial` exists, offer a one-keypress rediscover before the full pairing flow. The branch is
gated by `hasattr(conn, "rediscover")` so this plan runs correctly if sequenced before 1.3.

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

    rc = setup.run_setup(conn, prompt=no_prompt, out=out,
                         which=lambda name: "/usr/bin/adb", exists=lambda path: True)
    assert rc == 0
    assert conn.pairs == [] and conn.connects == []
    assert any("already connected" in line.lower() for line in out_lines)


def test_rediscover_branch_used_when_available(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))

    class RediscoverConn(RecordingConn):
        def __init__(self):
            super().__init__(states=["offline", "device"], cfg={"serial": "127.0.0.1:41000"})
            self.rediscovers = 0
        def rediscover(self):
            self.rediscovers += 1
            self.backend.serial = "127.0.0.1:41000"
            return "127.0.0.1:41000"

    conn = RediscoverConn()
    out_lines, out = collector()
    rc = setup.run_setup(conn, prompt=scripted(["y"]), out=out,
                         which=lambda name: "/usr/bin/adb", exists=lambda path: True)
    assert rc == 0
    assert conn.rediscovers == 1
    assert conn.pairs == [] and conn.connects == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_setup.py -v`
Expected: FAIL — `test_already_connected_fast_path_skips_pairing` fails because Task 1 always prompts; the
rediscover test fails because there is no gate yet.

- [ ] **Step 3: Write minimal implementation**

Insert after the `which("adb") is None` guard and before `WIRELESS_GUIDANCE`:

```python
    # Idempotent fast-path: already online, nothing to pair.
    if conn.backend.get_state() == "device":
        conn.cfg.setdefault("mode", "auto")
        config.save(conn.cfg)
        out(f"phonectl: already connected (serial={conn.backend.serial}). Nothing to do.")
        return 0

    # Optional rediscover (only if Plan 1.3's rediscover() exists and we have a serial).
    if hasattr(conn, "rediscover") and conn.cfg.get("serial"):
        answer = prompt(
            f"Last serial {conn.cfg['serial']} is offline. Try reconnecting? [Y/n]: "
        ).strip().lower()
        if answer in ("", "y", "yes"):
            conn.rediscover()
            if conn.backend.get_state() == "device":
                conn.cfg.setdefault("mode", "auto")
                config.save(conn.cfg)
                out(f"phonectl: reconnected (serial={conn.backend.serial}).")
                return 0
            out("phonectl: reconnect failed; falling back to full pairing.")
```

**BLOCKER fixture fix.** The new entry `get_state()` call means flows that must traverse the full
pair/connect/verify path now wrongly short-circuit when seeded `states=["device"]`. Update BOTH earlier
fixtures in this task to seed an offline-then-device script so they reach the pairing path:

1. `test_happy_path_pairs_connects_and_persists` (Task 1): `RecordingConn(states=["offline", "device"])`.
2. `test_adbkey_absent_emits_note_and_does_not_fabricate` (Task 3): `RecordingConn(states=["offline",
   "device"])` — without this, the entry fast-path returns `0` before `exists(adbkey)` is called and the
   note assertions fail.

`test_missing_adb` returns before any `get_state`; `test_verify_failure` (`states=["offline"]`) and the two
Task-4 fixtures need no change.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_setup.py -v`
Expected: PASS (6 tests) — with BOTH fixture updates applied.

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/setup.py tests/test_setup.py
git commit -m "feat: idempotent setup fast-path + optional rediscover branch"
```

---

### Task 5: Modular setup — `setup.module_report` + `run_module` dispatch

**New** (strategy §9.1). Each module reports its required permission, current status, how to enable it, what
capabilities it unlocks, and its safety implications.

**Files:**
- Modify: `src/phonectl/setup.py` (add `MODULES`, `module_report`, `run_module`)
- Test: `tests/test_setup.py` (extend)

**Interfaces:**
- `MODULES = ("adb", "accessibility", "notifications", "termux-api")`.
- `module_report(module, *, caps, which=shutil.which) -> dict` — PURE w.r.t. its inputs: returns
  `{"module", "required_permission", "available", "status", "how_to_enable", "capabilities_unlocked",
  "safety"}`. `available` derives from `caps` (the `backend.capabilities()` doc) for `adb`/`accessibility`/
  `notifications`, and from `which("termux-battery")` for `termux-api`. Raises `ValueError` on an unknown
  module.
- `run_module(module, conn, *, prompt=input, out=print, which=shutil.which, exists=os.path.exists) -> int`
  — `"adb"` runs `run_setup`; `"all"` runs `run_setup` then prints each other module's report and returns
  `run_setup`'s code; any other known module prints its report and returns `0`. Reports are read from
  `conn.backend.capabilities()`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_setup.py  (append)
import pytest
from phonectl import capabilities


def test_module_report_adb_available():
    caps = capabilities.make(requires_adb=True, act_tap=True, observe_ui_tree=True)
    rep = setup.module_report("adb", caps=caps, which=lambda n: "/usr/bin/adb")
    assert rep["available"] is True
    assert "capabilities_unlocked" in rep and rep["how_to_enable"]


def test_module_report_accessibility_unavailable_with_guidance():
    caps = capabilities.make(requires_adb=True)        # no accessibility yet
    rep = setup.module_report("accessibility", caps=caps)
    assert rep["available"] is False
    assert "Accessibility" in rep["how_to_enable"]
    assert rep["safety"]


def test_module_report_termux_api_uses_which():
    caps = capabilities.make(requires_adb=True)
    yes = setup.module_report("termux-api", caps=caps, which=lambda n: "/data/.../termux-battery")
    no = setup.module_report("termux-api", caps=caps, which=lambda n: None)
    assert yes["available"] is True and no["available"] is False


def test_module_report_unknown_raises():
    with pytest.raises(ValueError):
        setup.module_report("teleport", caps=capabilities.make())


def test_run_module_reports_without_pairing(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))

    class CapConn(RecordingConn):
        def __init__(self):
            super().__init__(states=["device"], cfg={})
            self.backend.capabilities = lambda: capabilities.make(requires_adb=True)

    conn = CapConn()
    out_lines, out = collector()
    rc = setup.run_module("notifications", conn,
                          prompt=lambda _m="": (_ for _ in ()).throw(AssertionError("no prompt")),
                          out=out, which=lambda n: None, exists=lambda p: True)
    assert rc == 0
    assert conn.pairs == [] and conn.connects == []
    assert any("notification" in line.lower() for line in out_lines)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_setup.py -v`
Expected: FAIL (`AttributeError: module 'phonectl.setup' has no attribute 'module_report'`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/setup.py  (append; add `from phonectl import capabilities` if you reference keys directly)

MODULES = ("adb", "accessibility", "notifications", "termux-api")

_MODULE_META = {
    "adb": {
        "required_permission": "Wireless debugging (Developer options)",
        "cap_key": "requires_adb",
        "how_to_enable": "Run: phonectl setup   (pairs over Wireless Debugging).",
        "capabilities_unlocked": "observe UI, tap/type/swipe/key, launch apps, send intents.",
        "safety": "Full input control of the device; gated by phonectl modes + kill switch.",
    },
    "accessibility": {
        "required_permission": "AccessibilityService enabled for the phonectl companion app",
        "cap_key": "requires_accessibility",
        "how_to_enable": "Settings > Accessibility > phonectl > On (companion APK, Phase 4).",
        "capabilities_unlocked": "native UI tree + UI event stream + reliable set-text/gestures.",
        "safety": "Reads on-screen content and dispatches gestures; per-capability toggles in the app.",
    },
    "notifications": {
        "required_permission": "Notification access for the phonectl companion app",
        "cap_key": "read_notifications",
        "how_to_enable": "Settings > Notifications > Notification access > phonectl (companion APK, Phase 4).",
        "capabilities_unlocked": "read/wait/reply/dismiss notifications.",
        "safety": "Exposes notification contents; redaction policies apply to logs.",
    },
    "termux-api": {
        "required_permission": "Termux:API app + termux-api package",
        "cap_key": None,   # detected via `which`, not the ADB capability doc
        "how_to_enable": "Install Termux:API app + `pkg install termux-api` (optional, Phase 3.5).",
        "capabilities_unlocked": "battery/clipboard/sensors/notifications/TTS bridges (optional).",
        "safety": "Optional, never a hard dependency; discovered at runtime.",
    },
}


def module_report(module, *, caps, which=shutil.which) -> dict:
    if module not in _MODULE_META:
        raise ValueError(f"unknown setup module: {module!r} (known: {', '.join(MODULES)})")
    meta = _MODULE_META[module]
    if module == "termux-api":
        available = which("termux-battery") is not None
    else:
        available = bool(caps.get(meta["cap_key"]))
    return {
        "module": module,
        "required_permission": meta["required_permission"],
        "available": available,
        "status": "available" if available else "not available",
        "how_to_enable": meta["how_to_enable"],
        "capabilities_unlocked": meta["capabilities_unlocked"],
        "safety": meta["safety"],
    }


def _print_report(rep, out) -> None:
    out(f"[{rep['module']}] {rep['status']} — {rep['required_permission']}")
    out(f"    enable: {rep['how_to_enable']}")
    out(f"    unlocks: {rep['capabilities_unlocked']}")
    out(f"    safety: {rep['safety']}")


def run_module(module, conn, *, prompt=input, out=print, which=shutil.which,
               exists=os.path.exists) -> int:
    caps = conn.backend.capabilities() if hasattr(conn.backend, "capabilities") else {}
    if module == "adb":
        return run_setup(conn, prompt=prompt, out=out, which=which, exists=exists)
    if module == "all":
        rc = run_setup(conn, prompt=prompt, out=out, which=which, exists=exists)
        for name in MODULES:
            if name != "adb":
                _print_report(module_report(name, caps=caps, which=which), out)
        return rc
    _print_report(module_report(module, caps=caps, which=which), out)
    return 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_setup.py -v`
Expected: PASS (existing + 5 new).

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/setup.py tests/test_setup.py
git commit -m "feat: modular setup module_report + run_module dispatch (adb/a11y/notif/termux-api/all)"
```

---

### Task 6: `diagnostics.redact_config` — pure secret redaction

**New** (strategy §9.3, "Config with secrets redacted").

**Files:**
- Create: `src/phonectl/diagnostics.py`
- Test: `tests/test_diagnostics.py`

**Interfaces:**
- `redact_config(cfg: dict) -> dict` — PURE: returns a deep copy with any key whose lowercased name
  contains `code`, `token`, `secret`, `password`, `key`, or `pair` masked to `"***"`. Non-sensitive keys
  (`serial`, `mode`, `last_port`, `probe_ports`) pass through unchanged. Nested dicts are redacted
  recursively.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_diagnostics.py
from phonectl import diagnostics


def test_redact_masks_sensitive_keys_only():
    cfg = {"serial": "127.0.0.1:41000", "mode": "auto", "last_port": "41000",
           "pairing_code": "482913", "api_token": "abc", "nested": {"secret": "s", "ok": 1}}
    red = diagnostics.redact_config(cfg)
    assert red["serial"] == "127.0.0.1:41000"
    assert red["mode"] == "auto"
    assert red["last_port"] == "41000"
    assert red["pairing_code"] == "***"
    assert red["api_token"] == "***"
    assert red["nested"]["secret"] == "***"
    assert red["nested"]["ok"] == 1
    # original is not mutated
    assert cfg["pairing_code"] == "482913"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_diagnostics.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'phonectl.diagnostics'`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/diagnostics.py
"""Redacted diagnostics bundle (strategy §9.3). Touches no adb directly — it
calls backend methods + reads the (already metadata-only) audit tail."""
from __future__ import annotations

_SECRET_SUBSTRINGS = ("code", "token", "secret", "password", "key", "pair")


def _is_secret(name: str) -> bool:
    low = name.lower()
    return any(s in low for s in _SECRET_SUBSTRINGS)


def redact_config(cfg: dict) -> dict:
    out = {}
    for k, v in cfg.items():
        if isinstance(v, dict):
            out[k] = redact_config(v)
        elif _is_secret(str(k)):
            out[k] = "***"
        else:
            out[k] = v
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_diagnostics.py -v`
Expected: PASS (1 test).

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/diagnostics.py tests/test_diagnostics.py
git commit -m "feat: diagnostics.redact_config — pure secret masking for bundles"
```

---

### Task 7: `AdbBackend.adb_version()` + `devices()` — bundle device facts

**New** device-touching methods for the diagnostics bundle (`adb version`, `adb devices -l`).

**Files:**
- Modify: `src/phonectl/adb_backend.py`
- Test: `tests/test_adb_backend.py` (append)

**Interfaces:**
- `adb_version(self) -> str` — `self._adb("version")` output (stripped). Run **without** the `-s serial`
  prefix is fine; reuse `_adb`. Returns the raw version banner.
- `devices(self) -> str` — `self._adb("devices", "-l")` output. (Raw text; parsing is not needed for the
  bundle — it is captured as-is for human diagnosis.)

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_adb_backend.py  (append)
def test_adb_version_runs_version():
    calls = []
    b = AdbBackend(serial="d", runner=make_runner(calls, stdout="Android Debug Bridge version 1.0.41\n"))
    assert "1.0.41" in b.adb_version()
    assert calls[0][0] == ["adb", "-s", "d", "version"]

def test_devices_runs_devices_l():
    calls = []
    b = AdbBackend(serial="d", runner=make_runner(calls, stdout="List of devices attached\n127.0.0.1:41000 device\n"))
    out = b.devices()
    assert "127.0.0.1:41000" in out
    assert calls[0][0] == ["adb", "-s", "d", "devices", "-l"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_adb_backend.py -v`
Expected: FAIL (`AttributeError: 'AdbBackend' object has no attribute 'adb_version'`).

- [ ] **Step 3: Write minimal implementation**

Append to the `AdbBackend` class:

```python
    def adb_version(self) -> str:
        return self._adb("version").strip()

    def devices(self) -> str:
        return self._adb("devices", "-l")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_adb_backend.py -v`
Expected: PASS (existing + 2 new).

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/adb_backend.py tests/test_adb_backend.py
git commit -m "feat: AdbBackend.adb_version()/devices() for diagnostics"
```

---

### Task 8: `diagnostics.collect` + `diagnostics.bundle` — assemble the redacted bundle

**New** (strategy §9.3). `collect` builds the diagnostic dict from backend methods + audit tail; `bundle`
writes it (plus the raw text blobs) to a zip.

**Files:**
- Modify: `src/phonectl/diagnostics.py` (add `collect`, `bundle`)
- Test: `tests/test_diagnostics.py` (extend)

**Interfaces:**
- `collect(backend, cfg) -> dict` — returns `{"config": redact_config(cfg), "capabilities":
  backend.capabilities(), "state": backend.get_state(), "adb_version": ..., "devices": ...,
  "mdns": [...], "host_shim": bool, "audit_tail": [...]}`. Optional 1.3 facts (`mdns` via
  `backend.mdns_services()`, `host_shim` via `hasattr(backend, "host_shim_runner")`) are `getattr`-gated:
  `mdns` is `[]` when unavailable. `audit_tail` reads the last N (default 20) lines of
  `config_dir()/actions.jsonl` (already metadata-only: `ts`/`verb`/`app`/`hash`), `[]` if the file is
  absent.
- `bundle(path, backend, cfg) -> str` — writes a zip at `path` containing `manifest.json`
  (`json.dumps(collect(...))`), plus `adb-version.txt` and `adb-devices.txt` raw blobs. Returns `path`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_diagnostics.py  (append)
import json, zipfile
from phonectl import capabilities


class DiagBackend:
    serial = "127.0.0.1:41000"
    def capabilities(self): return capabilities.make(requires_adb=True, act_tap=True)
    def get_state(self): return "device"
    def adb_version(self): return "Android Debug Bridge version 1.0.41"
    def devices(self): return "List of devices attached\n127.0.0.1:41000 device\n"
    def mdns_services(self): return ["127.0.0.1:41000"]


def test_collect_includes_redacted_config_and_capabilities(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    (tmp_path / "actions.jsonl").write_text(
        json.dumps({"ts": 1, "verb": "tap", "app": "com.x", "hash": "h"}) + "\n")
    data = diagnostics.collect(DiagBackend(), {"serial": "127.0.0.1:41000", "pairing_code": "482913"})
    assert data["config"]["pairing_code"] == "***"
    assert data["capabilities"]["requires_adb"] is True
    assert data["state"] == "device"
    assert "1.0.41" in data["adb_version"]
    assert data["mdns"] == ["127.0.0.1:41000"]
    assert data["host_shim"] is False
    assert data["audit_tail"][-1]["verb"] == "tap"


def test_bundle_writes_zip_with_manifest_and_blobs(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    out = str(tmp_path / "diag.zip")
    ret = diagnostics.bundle(out, DiagBackend(), {"serial": "127.0.0.1:41000", "pairing_code": "482913"})
    assert ret == out
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
        assert "manifest.json" in names
        assert "adb-version.txt" in names and "adb-devices.txt" in names
        manifest = json.loads(z.read("manifest.json"))
        assert manifest["config"]["pairing_code"] == "***"   # never leak secrets into the bundle
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_diagnostics.py -v`
Expected: FAIL (`AttributeError: module 'phonectl.diagnostics' has no attribute 'collect'`).

- [ ] **Step 3: Write minimal implementation**

Append to `src/phonectl/diagnostics.py` (add the imports `json`, `zipfile`, and `from phonectl.config
import config_dir`):

```python
import json
import zipfile

from phonectl.config import config_dir


def _audit_tail(n: int = 20) -> list:
    path = config_dir() / "actions.jsonl"
    if not path.exists():
        return []
    lines = path.read_text().splitlines()[-n:]
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def collect(backend, cfg) -> dict:
    mdns_fn = getattr(backend, "mdns_services", None)
    return {
        "config": redact_config(cfg),
        "capabilities": backend.capabilities() if hasattr(backend, "capabilities") else {},
        "state": backend.get_state(),
        "adb_version": backend.adb_version() if hasattr(backend, "adb_version") else "",
        "devices": backend.devices() if hasattr(backend, "devices") else "",
        "mdns": (mdns_fn() if mdns_fn is not None else []),
        "host_shim": hasattr(backend, "host_shim_runner"),
        "audit_tail": _audit_tail(),
    }


def bundle(path, backend, cfg) -> str:
    data = collect(backend, cfg)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(data, indent=2))
        z.writestr("adb-version.txt", data["adb_version"])
        z.writestr("adb-devices.txt", data["devices"])
    return path
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_diagnostics.py -v`
Expected: PASS (redact + collect + bundle = 3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/diagnostics.py tests/test_diagnostics.py
git commit -m "feat: diagnostics.collect + bundle — redacted support zip (strategy §9.3)"
```

---

### Task 9: `cli` — modular `setup` verb + `doctor --bundle`

Re-homes the old setup-wizard plan's Task 5 (the `setup` verb) and **adds the modular argument +
`doctor --bundle`**.

**Files:**
- Modify: `src/phonectl/cli.py` (add `_cmd_setup`; register `setup [module]`; add `--bundle` to `doctor`;
  import `setup` + `diagnostics`)
- Test: `tests/test_cli.py` (append)

**Interfaces:**
- `phonectl setup [module]` → `_cmd_setup(args)`: loads config, builds the runtime, and calls
  `setup_mod.run_module(args.module, conn)` with default `prompt=input`/`out=print`. `module` defaults to
  `"adb"`; choices are `MODULES + ("all",)`.
- `phonectl doctor --bundle <zip>` → on a successful `conn.ensure()`, calls
  `diagnostics.bundle(args.bundle, backend, cfg)` and prints the written path; returns `0`. `doctor --json`
  is unchanged (Plan 1.1).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cli.py  (append)
def test_setup_verb_wires_runtime_to_run_module(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    fb = FakeBackend()
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: fb)
    captured = {}
    from phonectl import setup as setup_mod
    monkeypatch.setattr(setup_mod, "run_module",
                        lambda module, conn, **kw: captured.update(module=module, conn=conn) or 0)
    rc = cli.main(["setup", "notifications"])
    assert rc == 0
    assert captured["module"] == "notifications"
    assert captured["conn"].backend is fb


def test_setup_verb_defaults_to_adb(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: FakeBackend())
    seen = {}
    from phonectl import setup as setup_mod
    monkeypatch.setattr(setup_mod, "run_module", lambda module, conn, **kw: seen.update(m=module) or 0)
    assert cli.main(["setup"]) == 0
    assert seen["m"] == "adb"


def test_doctor_bundle_writes_zip(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: FakeBackend())
    out_zip = str(tmp_path / "diag.zip")
    from phonectl import diagnostics
    monkeypatch.setattr(diagnostics, "bundle", lambda path, backend, cfg: path)
    rc = cli.main(["doctor", "--bundle", out_zip])
    assert rc == 0
    assert out_zip in capsys.readouterr().out
```

Note: `FakeBackend` must answer `get_state()=="device"` and `capabilities()` for the doctor path (it gains
`capabilities` from Plan 1.1's CLI tests). The `run_module`/`bundle` patches assert wiring without touching
`input()` or writing a real zip.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cli.py -v`
Expected: FAIL (`argparse` rejects the `setup` subcommand; `doctor` has no `--bundle`).

- [ ] **Step 3: Write minimal implementation**

In `src/phonectl/cli.py`, add imports `from phonectl import setup as setup_mod, diagnostics` and:

```python
def _cmd_setup(args):
    cfg = config.load()
    backend, session, conn = build_runtime(cfg)
    return setup_mod.run_module(args.module, conn)
```

In `_cmd_doctor`, after a successful `conn.ensure()` and before/around the existing json/plain branches:

```python
    if getattr(args, "bundle", None):
        path = diagnostics.bundle(args.bundle, backend, cfg)
        print(f"phonectl: diagnostics bundle written to {path}")
        return 0
```

Register the parsers in `build_parser`:

```python
    su = sub.add_parser("setup")
    su.add_argument("module", nargs="?", default="adb",
                    choices=list(setup_mod.MODULES) + ["all"])
    su.set_defaults(func=_cmd_setup)
    ...
    d.add_argument("--bundle", default=None, metavar="ZIP")
```

Note: `_cmd_setup` calls `setup_mod.run_module(...)` via the module attribute so the test's
`monkeypatch.setattr(setup_mod, "run_module", ...)` intercepts it; production uses `run_module`'s defaults
(`prompt=input`, `out=print`) for real interactive I/O.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cli.py -v`
Expected: PASS (existing CLI tests + 3 new).

- [ ] **Step 5: Run the full suite**

Run: `pytest -v`
Expected: PASS (setup, diagnostics, adb_backend, cli, and every prior test green).

- [ ] **Step 6: Commit**

```bash
git add src/phonectl/cli.py tests/test_cli.py
git commit -m "feat: modular phonectl setup verb + doctor --bundle wiring"
```

---

### Task 10: README onboarding + walkthrough + manual real-device verification

Re-homes the old setup-wizard plan's Task 6 and extends the docs with modules + diagnostics.

**Files:**
- Modify: `README.md` (add "Getting started: `phonectl setup`" + "Diagnostics" sections)
- Create: `docs/setup-walkthrough.md`

**Interfaces:** none (documentation + manual procedure). Pairing requires a real phone + a human reading
the 6-digit code; do not automate in CI.

- [ ] **Step 1: Write the README sections**

Under `## Getting started: \`phonectl setup\``: one sentence on the wizard (detect `adb`, guide Wireless
Debugging, pair, connect, verify, persist); the `pkg install android-tools` note; that re-running `setup`
is idempotent (short-circuits when already connected); and the modular form `phonectl setup
adb|accessibility|notifications|termux-api|all` with what each reports. Under `## Diagnostics`: `phonectl
doctor --json` (connectivity + capability envelope) and `phonectl doctor --bundle <zip>` (a redacted
support zip — config with secrets masked, capabilities, state, `adb version`/`adb devices -l`, mDNS,
host-shim status, and the metadata-only audit tail).

- [ ] **Step 2: Write `docs/setup-walkthrough.md`**

Document the prompt-by-prompt flow (matching `run_setup`), the "adb absent" branch, the "already connected"
fast-path, and a sample `setup all` module-report listing. Note that the connect port is volatile across
sleep/reboot; the wizard persists `last_port` so Plan 1.3's `reconnect`/`rediscover` retries it first, and
re-running `phonectl setup` is the manual recovery path. Note the Android 11+ requirement is surfaced as
guidance only (active version gating is deferred — see §9.2 below).

- [ ] **Step 3: Manual device verification (no CI)**

```bash
pkg install android-tools          # if `adb` is missing
phonectl setup                     # answer the three prompts off the Wireless Debugging screen
phonectl doctor                    # expect: connected (serial=..., state=device)
phonectl setup                     # re-run: expect the "already connected" fast-path, no prompts
phonectl setup all                 # expect adb pairing + status reports for the other modules
phonectl doctor --bundle /tmp/phonectl-diag.zip   # expect a redacted zip; inspect manifest.json
```

Confirm the bundle's `manifest.json` masks any sensitive config and that `audit_tail` carries only
`ts`/`verb`/`app`/`hash`.

- [ ] **Step 4: Commit**

```bash
git add README.md docs/setup-walkthrough.md
git commit -m "docs: phonectl setup onboarding, module reports, and diagnostics bundle walkthrough"
```

---

## Dependencies

**Plan 1.4 of the platform roadmap** — the final Phase-1 plan. Requires **Plan 1.1** for
`backend.capabilities()` (module reports + bundle) and the `results` envelope (`doctor --json`). It is
*opportunistic* on **Plan 1.3**: `conn.rediscover` (Task 4 fast-path), `backend.mdns_services`/
`host_shim_runner` (Task 8 bundle), and `cfg["last_port"]` are all `hasattr`/`getattr`-gated, so this plan
runs correctly even if 1.3 has not landed. It builds on the shipped `Connection.pair`/`connect` and
`config.save`. Downstream, Plan 2.1's audit v2 will enrich the bundle's `audit_tail` (levels/redaction) and
add a persisted connection-error log for the bundle's "recent errors" field.

## Deferred / out of scope (not in this plan)

- **Active Android-version/environment gating** (strategy §9.2) — the wizard surfaces the Android 11+
  requirement as *static guidance* only; active detection (`adb shell getprop ro.build.version.release`)
  needs a new `AdbBackend.android_release()` getter and a `< 11` abort branch, a self-contained follow-up.
  Sub-11 devices fail fast at pair/verify anyway (pairing-code Wireless Debugging is an Android-11 feature),
  and the design topology already assumes "unrooted Android 11+".
- **mDNS auto-discovery in the wizard** — deferred (PRoot caveat); the user hand-types the connect port.
  The optional rediscover fast-path (Task 4) lights up automatically via the 1.3 seam.
- **A dedicated persisted connection-error log** for the bundle's "recent errors" — the bundle ships the
  metadata-only `audit_tail` today; a structured error log is folded into Plan 2.1's audit v2.
- **Real-APK status detection for `accessibility`/`notifications`** — until Phase 4 ships the companion APK,
  those modules report from `capabilities()` (always unavailable over ADB-only) with enable-guidance.
- **The real `adb pair`/`adb connect` handshake** against hardware and first-server-start `~/.android/
  adbkey` creation — a manual walkthrough (Task 10), never fabricated test steps.

## Notes on testability

Every branch of `run_setup`/`run_module`/`module_report` and all of `diagnostics` is unit-tested with
injected `prompt`/`out`/`which`/`exists`, a `RecordingConn` double, and a `DiagBackend` fake — no real
`input()`, no real device, no real adb. `PHONECTL_HOME` isolation keeps config + the bundle's audit-tail
read inside `tmp_path`. `redact_config` and `module_report` are pure given their inputs. The CLI wiring is
proven with patched `run_module`/`bundle` seams, so no test pairs a phone or writes outside `tmp_path`. The
only non-auto-testable surface (Task 10) is the physical pairing handshake + adbkey creation, scoped to a
manual walkthrough.
