# phonectl companion setup — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One idempotent, safe-by-default `phonectl companion setup` command that brings the companion from "APK on disk" to "phonectl verified it end-to-end."

**Architecture:** A new stdlib module `src/phonectl/companion_setup.py` holds six small, independently-testable step functions plus an orchestrator, all driven through an injected `adb` seam (never `subprocess` directly). CLI adds a `companion` subparser group (`setup`, `status`) and a real `config` group (`get`, `set`). The token is read from the companion's own SharedPrefs via `run-as` on debug builds, falling back to a prompt.

**Tech Stack:** Python ≥3.9 stdlib only (`hashlib`, `xml.etree.ElementTree`, `time`), pytest, existing `phonectl` modules (`config`, `results`, `trust`, `providers.transport`, `adb_backend`).

Spec: `docs/superpowers/specs/2026-07-02-phonectl-companion-startup-design.md`

## Global Constraints

- **Stdlib-only runtime**, Python ≥ 3.9. No new third-party dependencies.
- **Backend isolation:** only `adb_backend.py` touches `adb`/`subprocess`. `companion_setup.py` receives an `adb` callable seam; it never imports `subprocess`.
- **No bypassing `runtime.run_action`:** setup issues device *administration* commands (install / settings / grant / broadcast / observe), never gated *action* verbs.
- **TDD, non-negotiable:** write the failing test first, one commit per task, execute tasks in order.
- **Tests are `PHONECTL_HOME`-isolated** (`monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))`) and device-free (fake `adb` seam).
- **Safe-by-default:** the two powerful steps (`ensure_accessibility`, `start_server`) print what they will grant/start and require `assume_yes` **or** an interactive confirmation.
- Companion identifiers (verbatim, from the APK source):
  - package `com.phonectl.companion`
  - accessibility component `com.phonectl.companion/com.phonectl.companion.service.CompanionAccessibilityService`
  - lifecycle receiver `com.phonectl.companion/.service.LifecycleReceiver`, action `com.phonectl.companion.action.START_SERVICE`, token extra `token`
  - SharedPrefs `shared_prefs/phonectl_companion.xml`, token key `companion_token`
  - default socket port `8765`

---

### Task 1: Device spike — confirm `run-as` token read (manual, no code)

De-risks approach B before any parser is built on it. **No commit; this is a checkpoint.**

- [ ] **Step 1: Reconnect adb** (port rotates — read the current `IP:port` from the phone's Wireless Debugging screen)

Run: `adb connect <ip>:<port>` then `adb devices` → expect `device`.

- [ ] **Step 2: Attempt the run-as read on the installed debug build**

Run: `adb -s <serial> shell run-as com.phonectl.companion cat shared_prefs/phonectl_companion.xml`
Expected: XML containing `<string name="companion_token">…</string>`.

- [ ] **Step 3: Record the outcome**

If it prints the token → approach B is viable; proceed. If it prints `run-as: package not debuggable` or is denied → note it in the plan and implement **C only** (prompt) in Task 7, skipping the run-as branch. Either way the plan proceeds; only Task 7's default path changes.

---

### Task 2: `AdbBackend.run_adb` full-result seam

`_adb` returns stdout only; setup needs `returncode`/`stderr` to detect install failure and run-as denial. Add a sibling that returns the full result. Backend isolation preserved (this is the adb boundary).

**Files:**
- Modify: `src/phonectl/adb_backend.py` (add method after `_adb`, ~line 17)
- Test: `tests/test_adb_backend.py`

**Interfaces:**
- Produces: `AdbBackend.run_adb(*args: str) -> subprocess.CompletedProcess` — `.returncode`, `.stdout`, `.stderr` (text). Uses the same `self._runner` + `self._base()` seam as `_adb`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_adb_backend.py
def test_run_adb_returns_full_result_with_serial():
    calls = []
    def fake_runner(cmd, **kw):
        calls.append(cmd)
        import subprocess
        return subprocess.CompletedProcess(cmd, 7, stdout="OUT", stderr="ERR")
    from phonectl.adb_backend import AdbBackend
    b = AdbBackend(serial="1.2.3.4:5", runner=fake_runner)
    res = b.run_adb("shell", "true")
    assert calls == [["adb", "-s", "1.2.3.4:5", "shell", "true"]]
    assert (res.returncode, res.stdout, res.stderr) == (7, "OUT", "ERR")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_adb_backend.py::test_run_adb_returns_full_result_with_serial -v`
Expected: FAIL with `AttributeError: 'AdbBackend' object has no attribute 'run_adb'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/adb_backend.py  (insert after _adb)
    def run_adb(self, *args: str):
        cmd = self._base() + list(args)
        return self._runner(cmd, capture_output=True, text=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_adb_backend.py::test_run_adb_returns_full_result_with_serial -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/adb_backend.py tests/test_adb_backend.py
git commit -m "feat(adb): add run_adb full-result seam for setup"
```

---

### Task 3: `companion_setup` constants + `parse_token` + step-result helper

**Files:**
- Create: `src/phonectl/companion_setup.py`
- Test: `tests/test_companion_setup.py`

**Interfaces:**
- Produces:
  - constants `PACKAGE`, `ACCESSIBILITY_COMPONENT`, `LIFECYCLE_COMPONENT`, `START_ACTION`, `TOKEN_EXTRA`, `PREFS_REL`, `TOKEN_KEY`, `DEFAULT_PORT`
  - `parse_token(xml_text: str) -> str | None` — extracts `companion_token`; `None` if absent/blank/malformed
  - `step(name: str, status: str, message: str = "", ok: bool = True) -> dict` — result shape `{"name","ok","status","message"}`, `status ∈ {"done","skipped","manual","failed"}`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_companion_setup.py
from phonectl import companion_setup as cs

XML_OK = ('<?xml version="1.0"?><map>'
          '<string name="companion_token">abc123</string>'
          '<boolean name="cap_observe_ui_native" value="true"/></map>')

def test_parse_token_extracts_value():
    assert cs.parse_token(XML_OK) == "abc123"

def test_parse_token_missing_returns_none():
    assert cs.parse_token('<map><string name="stopped">x</string></map>') is None

def test_parse_token_blank_returns_none():
    assert cs.parse_token('<map><string name="companion_token"></string></map>') is None

def test_parse_token_garbage_returns_none():
    assert cs.parse_token("run-as: package not debuggable") is None

def test_step_shape():
    assert cs.step("verify", "done", "ok") == {
        "name": "verify", "ok": True, "status": "done", "message": "ok"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_companion_setup.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'phonectl.companion_setup'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/companion_setup.py
"""Guided, idempotent bring-up of the phonectl companion (spec 2026-07-02).

Device contact goes through an injected ``adb(*args) -> CompletedProcess`` seam
(``AdbBackend.run_adb`` in production); this module never imports subprocess.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

PACKAGE = "com.phonectl.companion"
ACCESSIBILITY_COMPONENT = f"{PACKAGE}/{PACKAGE}.service.CompanionAccessibilityService"
LIFECYCLE_COMPONENT = f"{PACKAGE}/.service.LifecycleReceiver"
START_ACTION = "com.phonectl.companion.action.START_SERVICE"
TOKEN_EXTRA = "token"
PREFS_REL = "shared_prefs/phonectl_companion.xml"
TOKEN_KEY = "companion_token"
DEFAULT_PORT = 8765


def step(name: str, status: str, message: str = "", ok: bool = True) -> dict:
    return {"name": name, "ok": ok, "status": status, "message": message}


def parse_token(xml_text: str) -> "str | None":
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None
    el = root.find(f".//string[@name='{TOKEN_KEY}']")
    if el is None or not (el.text or "").strip():
        return None
    return el.text.strip()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_companion_setup.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/companion_setup.py tests/test_companion_setup.py
git commit -m "feat(companion-setup): module constants + token parser"
```

---

### Task 4: `ensure_installed`

**Files:**
- Modify: `src/phonectl/companion_setup.py`
- Test: `tests/test_companion_setup.py`

**Interfaces:**
- Consumes: `step`, `PACKAGE`; `adb(*args) -> CompletedProcess`; `cfg: dict`
- Produces: `ensure_installed(adb, apk_path, cfg, out) -> dict` — installs if package absent or `cfg["companion_apk_sha"]` differs from the apk's sha256; records the sha; `status="skipped"` when already current.

Test helper (add near top of test file):
```python
import subprocess
class FakeAdb:
    """Maps a matcher predicate over args -> CompletedProcess; records calls."""
    def __init__(self, rules):  # rules: list[(predicate, CompletedProcess)]
        self.rules = rules; self.calls = []
    def __call__(self, *args):
        self.calls.append(args)
        for pred, res in self.rules:
            if pred(args):
                return res
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
def cp(rc=0, out="", err=""):
    return subprocess.CompletedProcess([], rc, stdout=out, stderr=err)
```

- [ ] **Step 1: Write the failing test**

```python
def test_ensure_installed_installs_when_absent(tmp_path):
    apk = tmp_path / "app-debug.apk"; apk.write_bytes(b"APKBYTES")
    adb = FakeAdb([
        (lambda a: a[:3] == ("shell", "pm", "list"), cp(out="")),        # not listed
        (lambda a: a[0] == "install", cp(out="Success")),
    ])
    cfg = {}; out = []
    r = cs.ensure_installed(adb, str(apk), cfg, out.append)
    assert r["status"] == "done" and r["ok"]
    assert any(a[0] == "install" for a in adb.calls)
    import hashlib
    assert cfg["companion_apk_sha"] == hashlib.sha256(b"APKBYTES").hexdigest()

def test_ensure_installed_skips_when_sha_matches(tmp_path):
    apk = tmp_path / "app-debug.apk"; apk.write_bytes(b"APKBYTES")
    import hashlib
    cfg = {"companion_apk_sha": hashlib.sha256(b"APKBYTES").hexdigest()}
    adb = FakeAdb([(lambda a: a[:3] == ("shell", "pm", "list"),
                    cp(out="package:com.phonectl.companion"))])
    r = cs.ensure_installed(adb, str(apk), cfg, (lambda m: None))
    assert r["status"] == "skipped"
    assert not any(a[0] == "install" for a in adb.calls)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_companion_setup.py -k ensure_installed -v`
Expected: FAIL with `AttributeError: module 'phonectl.companion_setup' has no attribute 'ensure_installed'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to companion_setup.py
import hashlib


def _sha256(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _installed(adb) -> bool:
    return PACKAGE in adb("shell", "pm", "list", "packages", PACKAGE).stdout


def ensure_installed(adb, apk_path, cfg, out) -> dict:
    sha = _sha256(apk_path)
    if _installed(adb) and cfg.get("companion_apk_sha") == sha:
        return step("install", "skipped", f"{PACKAGE} already current")
    res = adb("install", "-r", apk_path)
    if res.returncode != 0 and "signatures do not match" in (res.stdout + res.stderr).lower():
        out("signature mismatch — uninstalling old build (resets token + grants)")
        adb("uninstall", PACKAGE)
        res = adb("install", apk_path)
    if res.returncode != 0 or "Success" not in res.stdout:
        return step("install", "failed", res.stderr.strip() or res.stdout.strip(), ok=False)
    cfg["companion_apk_sha"] = sha
    return step("install", "done", f"installed {apk_path}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_companion_setup.py -k ensure_installed -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/companion_setup.py tests/test_companion_setup.py
git commit -m "feat(companion-setup): ensure_installed step (idempotent by apk sha)"
```

---

### Task 5: `ensure_accessibility` (safe-by-default, `--yes` gated)

**Files:**
- Modify: `src/phonectl/companion_setup.py`
- Test: `tests/test_companion_setup.py`

**Interfaces:**
- Consumes: `step`, `ACCESSIBILITY_COMPONENT`; `adb`; `out`; `assume_yes: bool`; `prompt`
- Produces: `ensure_accessibility(adb, out, *, assume_yes, prompt) -> dict` — skips if the component is already in `enabled_accessibility_services`; otherwise (append, not clobber) writes the secure settings, gated by `assume_yes` or a `y` confirmation.

- [ ] **Step 1: Write the failing test**

```python
def test_ensure_accessibility_skips_when_already_enabled():
    adb = FakeAdb([(lambda a: a[:4] == ("shell", "settings", "get", "secure"),
                    cp(out=cs.ACCESSIBILITY_COMPONENT))])
    r = cs.ensure_accessibility(adb, (lambda m: None), assume_yes=True, prompt=(lambda m="": "y"))
    assert r["status"] == "skipped"
    assert not any(a[2] == "put" for a in adb.calls if len(a) > 2)

def test_ensure_accessibility_appends_when_yes():
    adb = FakeAdb([(lambda a: a[:4] == ("shell", "settings", "get", "secure"), cp(out="null"))])
    r = cs.ensure_accessibility(adb, (lambda m: None), assume_yes=True, prompt=(lambda m="": "n"))
    assert r["status"] == "done"
    puts = [a for a in adb.calls if len(a) > 2 and a[2] == "put"]
    assert any(cs.ACCESSIBILITY_COMPONENT in a for a in puts)
    assert any(a[-2:] == ("accessibility_enabled", "1") for a in puts)

def test_ensure_accessibility_declined_without_yes():
    adb = FakeAdb([(lambda a: a[:4] == ("shell", "settings", "get", "secure"), cp(out="null"))])
    r = cs.ensure_accessibility(adb, (lambda m: None), assume_yes=False, prompt=(lambda m="": "n"))
    assert r["status"] == "failed" and not r["ok"]
    assert not any(len(a) > 2 and a[2] == "put" for a in adb.calls)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_companion_setup.py -k accessibility -v`
Expected: FAIL — `has no attribute 'ensure_accessibility'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to companion_setup.py
def _confirm(assume_yes, prompt, what) -> bool:
    if assume_yes:
        return True
    return prompt(f"Grant/start: {what}? [y/N]: ").strip().lower() in ("y", "yes")


def ensure_accessibility(adb, out, *, assume_yes, prompt) -> dict:
    current = adb("shell", "settings", "get", "secure",
                  "enabled_accessibility_services").stdout.strip()
    if ACCESSIBILITY_COMPONENT in current:
        return step("accessibility", "skipped", "service already enabled")
    out("This enables an AccessibilityService that can read the screen and inject gestures.")
    if not _confirm(assume_yes, prompt, "enable companion AccessibilityService"):
        return step("accessibility", "failed", "declined (re-run with --yes)", ok=False)
    value = (ACCESSIBILITY_COMPONENT if current in ("", "null")
             else current + ":" + ACCESSIBILITY_COMPONENT)
    adb("shell", "settings", "put", "secure", "enabled_accessibility_services", value)
    adb("shell", "settings", "put", "secure", "accessibility_enabled", "1")
    return step("accessibility", "done", "AccessibilityService enabled")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_companion_setup.py -k accessibility -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/companion_setup.py tests/test_companion_setup.py
git commit -m "feat(companion-setup): ensure_accessibility (append + --yes gate)"
```

---

### Task 6: `ensure_notifications`

**Files:**
- Modify: `src/phonectl/companion_setup.py`
- Test: `tests/test_companion_setup.py`

**Interfaces:**
- Consumes: `step`, `PACKAGE`; `adb`; `out`
- Produces: `ensure_notifications(adb, out) -> dict` — grants `POST_NOTIFICATIONS` if not already granted; always prints the one manual step adb cannot do (notification-listener toggle).

- [ ] **Step 1: Write the failing test**

```python
def test_ensure_notifications_grants_when_missing():
    adb = FakeAdb([(lambda a: a[:3] == ("shell", "dumpsys", "package"),
                    cp(out="android.permission.POST_NOTIFICATIONS: granted=false"))])
    out = []
    r = cs.ensure_notifications(adb, out.append)
    assert r["status"] == "done"
    assert any(a[:3] == ("shell", "pm", "grant") for a in adb.calls)
    assert any("notification access" in m.lower() for m in out)

def test_ensure_notifications_skips_when_granted():
    adb = FakeAdb([(lambda a: a[:3] == ("shell", "dumpsys", "package"),
                    cp(out="android.permission.POST_NOTIFICATIONS: granted=true"))])
    r = cs.ensure_notifications(adb, (lambda m: None))
    assert r["status"] == "skipped"
    assert not any(a[:3] == ("shell", "pm", "grant") for a in adb.calls)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_companion_setup.py -k notifications -v`
Expected: FAIL — `has no attribute 'ensure_notifications'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to companion_setup.py
_NOTIF_LISTENER_HINT = (
    "Manual step (adb cannot grant this): open the companion app and tap "
    "'Notification access' to enable inline-reply/dismiss.")


def ensure_notifications(adb, out) -> dict:
    dump = adb("shell", "dumpsys", "package", PACKAGE).stdout
    granted = "POST_NOTIFICATIONS: granted=true" in dump
    result = step("notifications", "skipped", "POST_NOTIFICATIONS already granted")
    if not granted:
        adb("shell", "pm", "grant", PACKAGE, "android.permission.POST_NOTIFICATIONS")
        result = step("notifications", "done", "granted POST_NOTIFICATIONS")
    out(_NOTIF_LISTENER_HINT)
    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_companion_setup.py -k notifications -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/companion_setup.py tests/test_companion_setup.py
git commit -m "feat(companion-setup): ensure_notifications (grant + listener hint)"
```

---

### Task 7: `acquire_token` (run-as read → prompt fallback)

**Files:**
- Modify: `src/phonectl/companion_setup.py`
- Test: `tests/test_companion_setup.py`

**Interfaces:**
- Consumes: `step`, `PACKAGE`, `PREFS_REL`, `parse_token`; `adb`; `cfg`; `out`; `prompt`
- Produces:
  - `read_token_via_runas(adb) -> str | None` — `run-as … cat <prefs>`; `None` on nonzero return or unparseable output
  - `acquire_token(adb, cfg, out, *, prompt) -> dict` — skips if `cfg["companion_token"]` set; else run-as (B), else launch app + prompt (C); persists `companion_token` via `config.save`

- [ ] **Step 1: Write the failing test**

```python
def test_read_token_via_runas_success():
    adb = FakeAdb([(lambda a: a[:3] == ("shell", "run-as", "cat") or "run-as" in a, cp(out=XML_OK))])
    assert cs.read_token_via_runas(adb) == "abc123"

def test_read_token_via_runas_denied_returns_none():
    adb = FakeAdb([(lambda a: "run-as" in a, cp(rc=1, err="run-as: not debuggable"))])
    assert cs.read_token_via_runas(adb) is None

def test_acquire_token_runas(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    adb = FakeAdb([(lambda a: "run-as" in a, cp(out=XML_OK))])
    cfg = {}
    r = cs.acquire_token(adb, cfg, (lambda m: None), prompt=(lambda m="": "SHOULD_NOT_BE_USED"))
    assert r["status"] == "done" and cfg["companion_token"] == "abc123"
    from phonectl import config
    assert config.load()["companion_token"] == "abc123"

def test_acquire_token_prompt_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    adb = FakeAdb([(lambda a: "run-as" in a, cp(rc=1, err="not debuggable"))])
    cfg = {}
    r = cs.acquire_token(adb, cfg, (lambda m: None), prompt=(lambda m="": "  pasted-tok  "))
    assert r["status"] == "done" and cfg["companion_token"] == "pasted-tok"
    assert any(a[:3] == ("shell", "am", "start") for a in adb.calls)  # app launched for the user
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_companion_setup.py -k token -v`
Expected: FAIL — `has no attribute 'read_token_via_runas'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to companion_setup.py
from phonectl import config as _config


def read_token_via_runas(adb) -> "str | None":
    res = adb("shell", "run-as", PACKAGE, "cat", PREFS_REL)
    if res.returncode != 0:
        return None
    return parse_token(res.stdout)


def acquire_token(adb, cfg, out, *, prompt) -> dict:
    if cfg.get("companion_token"):
        return step("token", "skipped", "companion_token already set")
    token = read_token_via_runas(adb)
    source = "run-as"
    if not token:
        adb("shell", "am", "start", "-n", f"{PACKAGE}/.ui.SettingsActivity")
        out("Copy the token from the companion app's Pairing section.")
        token = prompt("Paste companion token: ").strip()
        source = "prompt"
    if not token:
        return step("token", "failed", "no token acquired", ok=False)
    cfg["companion_token"] = token
    _config.save(cfg)
    return step("token", "done", f"paired via {source}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_companion_setup.py -k token -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/companion_setup.py tests/test_companion_setup.py
git commit -m "feat(companion-setup): acquire_token via run-as with prompt fallback"
```

---

### Task 8: `start_server` (token'd broadcast, poll socket, `--yes` gated)

**Files:**
- Modify: `src/phonectl/companion_setup.py`
- Test: `tests/test_companion_setup.py`

**Interfaces:**
- Consumes: `step`, `START_ACTION`, `TOKEN_EXTRA`, `LIFECYCLE_COMPONENT`, `DEFAULT_PORT`, `_confirm`; `adb`; `cfg`; `out`; `assume_yes`; `prompt`; `sleep`
- Produces: `start_server(adb, token, cfg, out, *, assume_yes, prompt, sleep=time.sleep, attempts=10) -> dict` — skips if `:8765` already listening; else fires the authenticated `START_SERVICE` broadcast and polls `ss -tln`; on success sets `cfg["companion_host"]="127.0.0.1"`, `cfg["companion_port"]=DEFAULT_PORT` and saves.

- [ ] **Step 1: Write the failing test**

```python
def _listening(port=8765):
    return cp(out=f"LISTEN 0 0 [::ffff:127.0.0.1]:{port} *:*")

def test_start_server_skips_when_already_up(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    adb = FakeAdb([(lambda a: a[:2] == ("shell", "ss"), _listening())])
    r = cs.start_server(adb, "tok", {}, (lambda m: None), assume_yes=True,
                        prompt=(lambda m="": "n"), sleep=(lambda s: None))
    assert r["status"] == "skipped"
    assert not any(a[:3] == ("shell", "am", "broadcast") for a in adb.calls)

def test_start_server_broadcasts_then_up(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    seq = [cp(out=""), _listening()]  # down, then up after broadcast
    adb = FakeAdb([(lambda a: a[:2] == ("shell", "ss"), None)])
    def ss_dispatch(*a):
        adb.calls.append(a)
        if a[:2] == ("shell", "ss"):
            return seq.pop(0) if len(seq) > 1 else seq[0]
        return cp(out="")
    cfg = {}
    r = cs.start_server(ss_dispatch, "tok", cfg, (lambda m: None), assume_yes=True,
                        prompt=(lambda m="": "n"), sleep=(lambda s: None))
    assert r["status"] == "done"
    assert any(a[:3] == ("shell", "am", "broadcast") and cs.TOKEN_EXTRA in a for a in adb.calls)
    assert cfg["companion_port"] == cs.DEFAULT_PORT

def test_start_server_timeout(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    adb = FakeAdb([(lambda a: a[:2] == ("shell", "ss"), cp(out=""))])
    r = cs.start_server(adb, "tok", {}, (lambda m: None), assume_yes=True,
                        prompt=(lambda m="": "n"), sleep=(lambda s: None), attempts=3)
    assert r["status"] == "failed" and not r["ok"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_companion_setup.py -k start_server -v`
Expected: FAIL — `has no attribute 'start_server'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to companion_setup.py
import time


def _socket_up(adb, port=DEFAULT_PORT) -> bool:
    return f":{port}" in adb("shell", "ss", "-tln").stdout


def start_server(adb, token, cfg, out, *, assume_yes, prompt,
                 sleep=time.sleep, attempts=10) -> dict:
    if _socket_up(adb):
        return step("server", "skipped", f"socket :{DEFAULT_PORT} already listening")
    out(f"This starts the companion's remote-control socket on 127.0.0.1:{DEFAULT_PORT}.")
    if not _confirm(assume_yes, prompt, "start companion server"):
        return step("server", "failed", "declined (re-run with --yes)", ok=False)
    adb("shell", "am", "broadcast", "-a", START_ACTION,
        "--es", TOKEN_EXTRA, token, "-n", LIFECYCLE_COMPONENT)
    for _ in range(attempts):
        if _socket_up(adb):
            cfg["companion_host"] = "127.0.0.1"
            cfg["companion_port"] = DEFAULT_PORT
            _config.save(cfg)
            return step("server", "done", f"socket :{DEFAULT_PORT} up")
        sleep(1)
    return step("server", "failed", f"socket :{DEFAULT_PORT} never came up", ok=False)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_companion_setup.py -k start_server -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/companion_setup.py tests/test_companion_setup.py
git commit -m "feat(companion-setup): start_server via token'd broadcast + socket poll"
```

---

### Task 9: `verify` (authenticated handshake)

**Files:**
- Modify: `src/phonectl/companion_setup.py`
- Test: `tests/test_companion_setup.py`

**Interfaces:**
- Consumes: `step`, `DEFAULT_PORT`; `cfg`; injectable `negotiate` (default `trust.negotiate`) and `transport_factory` (default `providers.transport.SocketTransport`)
- Produces: `verify(cfg, *, negotiate=..., transport_factory=...) -> dict` — negotiates with the paired token; `status="done"` and `message` listing enabled caps when reachable, else `failed`. Result carries `data={"reachable","stopped","capabilities"}` for `--json`.

- [ ] **Step 1: Write the failing test**

```python
class _HS:
    def __init__(self, reachable, stopped, caps):
        self.reachable, self.stopped, self.capabilities = reachable, stopped, caps

def test_verify_reachable_reports_caps():
    seen = {}
    def fake_factory(host, port, *, token=None):
        seen.update(host=host, port=port, token=token); return object()
    r = cs.verify({"companion_token": "t", "companion_port": 8765},
                  negotiate=lambda t, **k: _HS(True, False, {"observe_ui_native": True}),
                  transport_factory=fake_factory)
    assert r["status"] == "done" and r["data"]["reachable"] is True
    assert seen["token"] == "t" and seen["port"] == 8765

def test_verify_unreachable_fails():
    r = cs.verify({"companion_token": "t"},
                  negotiate=lambda t, **k: _HS(False, False, {}),
                  transport_factory=lambda *a, **k: object())
    assert r["status"] == "failed" and not r["ok"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_companion_setup.py -k verify -v`
Expected: FAIL — `has no attribute 'verify'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to companion_setup.py
def verify(cfg, *, negotiate=None, transport_factory=None) -> dict:
    if negotiate is None:
        from phonectl import trust
        negotiate = trust.negotiate
    if transport_factory is None:
        from phonectl.providers.transport import SocketTransport
        transport_factory = SocketTransport
    t = transport_factory(cfg.get("companion_host", "127.0.0.1"),
                          int(cfg.get("companion_port", DEFAULT_PORT)),
                          token=cfg.get("companion_token"))
    hs = negotiate(t, timeout=3.0)
    caps = hs.capabilities or {}
    data = {"reachable": hs.reachable, "stopped": hs.stopped, "capabilities": caps}
    if not hs.reachable:
        return {**step("verify", "failed", "companion unreachable (token/socket?)", ok=False),
                "data": data}
    on = sorted(k for k, v in caps.items() if v)
    return {**step("verify", "done", f"reachable; {len(on)} caps: {', '.join(on)}"),
            "data": data}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_companion_setup.py -k verify -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/companion_setup.py tests/test_companion_setup.py
git commit -m "feat(companion-setup): verify step (authenticated handshake)"
```

---

### Task 10: `run_companion_setup` orchestrator

**Files:**
- Modify: `src/phonectl/companion_setup.py`
- Test: `tests/test_companion_setup.py`

**Interfaces:**
- Consumes: all six steps
- Produces: `run_companion_setup(adb, cfg, *, apk_path, assume_yes=False, prompt=input, out=print, sleep=time.sleep) -> dict` — runs steps 1→6 in order, **stops at the first `ok=False`** (fail-closed), returns `{"ok": bool, "steps": [dict,...]}`. `apk_path` defaults resolved by caller (Task 12); orchestrator requires it non-None.

- [ ] **Step 1: Write the failing test**

```python
def test_orchestrator_runs_all_steps_happy(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    apk = tmp_path / "app-debug.apk"; apk.write_bytes(b"X")
    def adb(*a):
        if a[:3] == ("shell", "pm", "list"): return cp(out="")
        if a[0] == "install": return cp(out="Success")
        if a[:4] == ("shell", "settings", "get", "secure"): return cp(out="null")
        if a[:3] == ("shell", "dumpsys", "package"): return cp(out="POST_NOTIFICATIONS: granted=true")
        if "run-as" in a: return cp(out=XML_OK)
        if a[:2] == ("shell", "ss"): return _listening()
        return cp(out="")
    res = cs.run_companion_setup(
        adb, {}, apk_path=str(apk), assume_yes=True,
        prompt=(lambda m="": "n"), out=(lambda m: None), sleep=(lambda s: None),
        # verify seams injected so no real socket is opened:
    )
    assert res["ok"] is True
    assert [s["name"] for s in res["steps"]] == \
        ["install", "accessibility", "notifications", "token", "server", "verify"]

def test_orchestrator_stops_on_failed_step(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    apk = tmp_path / "app-debug.apk"; apk.write_bytes(b"X")
    def adb(*a):
        if a[:3] == ("shell", "pm", "list"): return cp(out="")
        if a[0] == "install": return cp(rc=1, err="INSTALL_FAILED")  # fail at step 1
        return cp(out="")
    res = cs.run_companion_setup(adb, {}, apk_path=str(apk), assume_yes=True,
                                 prompt=(lambda m="": "n"), out=(lambda m: None), sleep=(lambda s: None))
    assert res["ok"] is False and res["steps"][-1]["name"] == "install"
    assert len(res["steps"]) == 1  # stopped, did not proceed
```

Note: `verify` opens a real socket unless its seams are injected. For the happy-path test, make `run_companion_setup` accept optional `verify_kwargs` (dict passed to `verify`) so tests inject a fake `negotiate`/`transport_factory`. Add to the happy test:
```python
        # replace the run_companion_setup(...) call above with:
    res = cs.run_companion_setup(
        adb, {}, apk_path=str(apk), assume_yes=True, prompt=(lambda m="": "n"),
        out=(lambda m: None), sleep=(lambda s: None),
        verify_kwargs={"negotiate": lambda t, **k: _HS(True, False, {"observe_ui_native": True}),
                       "transport_factory": lambda *a, **k: object()})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_companion_setup.py -k orchestrator -v`
Expected: FAIL — `has no attribute 'run_companion_setup'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to companion_setup.py
def run_companion_setup(adb, cfg, *, apk_path, assume_yes=False,
                        prompt=input, out=print, sleep=time.sleep, verify_kwargs=None) -> dict:
    steps: list = []

    def record(result):
        steps.append(result)
        return result["ok"]

    if not record(ensure_installed(adb, apk_path, cfg, out)):
        return {"ok": False, "steps": steps}
    if not record(ensure_accessibility(adb, out, assume_yes=assume_yes, prompt=prompt)):
        return {"ok": False, "steps": steps}
    record(ensure_notifications(adb, out))  # never fatal
    if not record(acquire_token(adb, cfg, out, prompt=prompt)):
        return {"ok": False, "steps": steps}
    token = cfg.get("companion_token")
    if not record(start_server(adb, token, cfg, out, assume_yes=assume_yes,
                               prompt=prompt, sleep=sleep)):
        return {"ok": False, "steps": steps}
    record(verify(cfg, **(verify_kwargs or {})))
    return {"ok": all(s["ok"] for s in steps), "steps": steps}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_companion_setup.py -k orchestrator -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/companion_setup.py tests/test_companion_setup.py
git commit -m "feat(companion-setup): orchestrator (ordered, fail-closed)"
```

---

### Task 11: `config.coerce_and_set` + `phonectl config get/set` CLI

**Files:**
- Modify: `src/phonectl/config.py` (add helper after `get_mode`)
- Modify: `src/phonectl/cli.py` (add `_cmd_config_get`/`_cmd_config_set`; wire a `config` subparser group near the other groups, ~line 1608)
- Test: `tests/test_config_audit.py` (config helper) and `tests/test_cli.py` (CLI)

**Interfaces:**
- Produces:
  - `config.coerce_and_set(cfg: dict, key: str, raw: str) -> dict` — rejects keys absent from `config.DEFAULTS` (raise `KeyError`); coerces `raw` to the type of the default (`int`/`float`/`bool`/`None→str`); returns cfg
  - CLI `config get <key>` and `config set <key> <value>`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_config_audit.py
import pytest
from phonectl import config

def test_coerce_and_set_typed():
    cfg = {}
    config.coerce_and_set(cfg, "companion_port", "8765")
    assert cfg["companion_port"] == 8765 and isinstance(cfg["companion_port"], int)
    config.coerce_and_set(cfg, "companion_timeout", "1.5")
    assert cfg["companion_timeout"] == 1.5
    config.coerce_and_set(cfg, "companion_token", "abc")
    assert cfg["companion_token"] == "abc"

def test_coerce_and_set_rejects_unknown_key():
    with pytest.raises(KeyError):
        config.coerce_and_set({}, "not_a_real_key", "x")
```

```python
# tests/test_cli.py
def test_cli_config_set_and_get(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    from phonectl import cli
    assert cli.main(["config", "set", "companion_port", "8765"]) == 0
    assert cli.main(["config", "get", "companion_port"]) == 0
    assert "8765" in capsys.readouterr().out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_config_audit.py -k coerce -v tests/test_cli.py -k config_set_and_get -v`
Expected: FAIL — `has no attribute 'coerce_and_set'` / unknown command `config`

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/config.py  (after get_mode)
def coerce_and_set(cfg: dict, key: str, raw: str) -> dict:
    if key not in DEFAULTS:
        raise KeyError(f"unknown config key: {key!r}")
    default = DEFAULTS[key]
    if isinstance(default, bool):
        value = str(raw).strip().lower() in ("1", "true", "yes", "on")
    elif isinstance(default, int):
        value = int(raw)
    elif isinstance(default, float):
        value = float(raw)
    else:  # None or str
        value = raw
    cfg[key] = value
    return cfg
```

```python
# src/phonectl/cli.py  (handlers near _cmd_setup)
def _cmd_config_get(args):
    cfg = config.load()
    print(json.dumps(cfg.get(args.key)) if getattr(args, "json", False) else cfg.get(args.key))
    return 0

def _cmd_config_set(args):
    cfg = config.load()
    try:
        config.coerce_and_set(cfg, args.key, args.value)
    except KeyError as e:
        print(f"phonectl: {e}")
        return 2
    config.save(cfg)
    print(f"phonectl: {args.key} = {cfg[args.key]}")
    return 0
```

```python
# src/phonectl/cli.py  (in build_parser, near the other subparser groups ~line 1608)
    cfgp = sub.add_parser("config")
    cfgsub = cfgp.add_subparsers(dest="config_cmd")
    cg = cfgsub.add_parser("get"); cg.add_argument("key"); cg.add_argument("--json", action="store_true")
    cg.set_defaults(func=_cmd_config_get)
    cset = cfgsub.add_parser("set"); cset.add_argument("key"); cset.add_argument("value")
    cset.set_defaults(func=_cmd_config_set)
    cfgp.set_defaults(func=lambda args: (cfgp.print_help(), 2)[1])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_config_audit.py -k coerce tests/test_cli.py -k config_set_and_get -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/config.py src/phonectl/cli.py tests/test_config_audit.py tests/test_cli.py
git commit -m "feat(cli): config get/set with typed coercion"
```

---

### Task 12: `phonectl companion setup` + `companion status` CLI wiring

**Files:**
- Modify: `src/phonectl/cli.py` (handlers + `companion` subparser group; imports `companion_setup`)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `companion_setup.run_companion_setup`, `build_runtime`, `config`
- Produces: `_cmd_companion_setup(args)`, `_cmd_companion_status(args)`; a `companion` subparser group. `setup` resolves the APK (from `--apk` or newest `app-debug.apk` under `~/Download`, `/sdcard/Download`), connects (`conn.ensure()`), and calls the orchestrator with `adb=backend.run_adb`.

- [ ] **Step 1: Write the failing test** (dispatch-level, seams patched so no device is touched)

```python
# tests/test_cli.py
def test_cli_companion_setup_dispatches(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    apk = tmp_path / "app-debug.apk"; apk.write_bytes(b"X")
    from phonectl import cli, companion_setup

    class _Backend:  # stands in for AdbBackend
        serial = "1.2.3.4:5"
        def run_adb(self, *a): 
            import subprocess; return subprocess.CompletedProcess(a, 0, stdout="", stderr="")
    class _Conn:
        def __init__(self): self.backend = _Backend()
        def ensure(self): pass
    monkeypatch.setattr(cli, "build_runtime", lambda cfg: (_Conn().backend, None, _Conn()))
    monkeypatch.setattr(companion_setup, "run_companion_setup",
                        lambda adb, cfg, **k: {"ok": True, "steps": [
                            companion_setup.step("install", "done", "ok")]})
    rc = cli.main(["companion", "setup", "--apk", str(apk), "--yes"])
    assert rc == 0
    assert "install" in capsys.readouterr().out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli.py -k companion_setup_dispatches -v`
Expected: FAIL — invalid choice: `companion`

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/cli.py  (top-level import)
from phonectl import companion_setup as _companion_setup
```

```python
# src/phonectl/cli.py  (handlers)
import glob as _glob

def _resolve_apk(explicit):
    if explicit:
        return explicit
    candidates = []
    for base in (os.path.expanduser("~/Download"), "/sdcard/Download",
                 "/storage/emulated/0/Download"):
        candidates += _glob.glob(os.path.join(base, "**", "app-debug.apk"), recursive=True)
    return max(candidates, key=os.path.getmtime) if candidates else None

def _cmd_companion_setup(args):
    cfg = config.load()
    apk = _resolve_apk(getattr(args, "apk", None))
    if apk is None:
        print("phonectl: no app-debug.apk found; pass --apk PATH")
        return 2
    backend, _session, conn = build_runtime(cfg)
    conn.ensure()
    res = _companion_setup.run_companion_setup(
        backend.run_adb, cfg, apk_path=apk, assume_yes=getattr(args, "yes", False))
    if getattr(args, "json", False):
        print(json.dumps(res, indent=2))
    else:
        for s in res["steps"]:
            print(f"  [{s['status']}] {s['name']}: {s['message']}")
        print("phonectl: companion setup " + ("OK" if res["ok"] else "FAILED"))
    return 0 if res["ok"] else 1

def _cmd_companion_status(args):
    cfg = config.load()
    backend, _session, conn = build_runtime(cfg)
    adb = backend.run_adb
    installed = _companion_setup.PACKAGE in adb("shell", "pm", "list", "packages",
                                                _companion_setup.PACKAGE).stdout
    acc = _companion_setup.ACCESSIBILITY_COMPONENT in adb(
        "shell", "settings", "get", "secure", "enabled_accessibility_services").stdout
    up = _companion_setup._socket_up(adb)
    report = {"installed": installed, "accessibility": acc, "socket": up,
              "token_paired": bool(cfg.get("companion_token"))}
    print(json.dumps(report, indent=2) if getattr(args, "json", False)
          else "  " + "  ".join(f"{k}={v}" for k, v in report.items()))
    return 0
```

```python
# src/phonectl/cli.py  (build_parser, near the other groups)
    cp2 = sub.add_parser("companion")
    cp2sub = cp2.add_subparsers(dest="companion_cmd")
    cst = cp2sub.add_parser("setup")
    cst.add_argument("--apk", default=None)
    cst.add_argument("--yes", action="store_true")
    cst.add_argument("--json", action="store_true")
    cst.set_defaults(func=_cmd_companion_setup)
    cstat = cp2sub.add_parser("status"); cstat.add_argument("--json", action="store_true")
    cstat.set_defaults(func=_cmd_companion_status)
    cp2.set_defaults(func=lambda args: (cp2.print_help(), 2)[1])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli.py -k companion_setup_dispatches -v`
Expected: PASS

- [ ] **Step 5: Run the full suite + commit**

Run: `pytest -q` → Expected: all pass (existing 660 + new).

```bash
git add src/phonectl/cli.py tests/test_cli.py
git commit -m "feat(cli): phonectl companion setup + status commands"
```

---

### Task 13: Docs — README + roadmap

**Files:**
- Modify: `README.md` (add a "Companion setup" section documenting `phonectl companion setup [--apk] [--yes]`, `companion status`, `config set/get`)
- Modify: `docs/superpowers/phonectl-platform-roadmap.md` (mark companion-startup done; link this plan)

- [ ] **Step 1: Write the README section** (command table + the one manual step: notification-listener toggle; note run-as needs a debug build, else prompt)
- [ ] **Step 2: Update the roadmap status line + follow-up pointers** (ADB port-rotation plan; approach A; Kotlin Finding-5 gap)
- [ ] **Step 3: Commit**

```bash
git add README.md docs/superpowers/phonectl-platform-roadmap.md
git commit -m "docs: companion setup command + roadmap status"
```

---

## Self-Review

**1. Spec coverage** — every spec element maps to a task:
- One idempotent command → Tasks 10 + 12. Six steps → Tasks 4–9. Token B+C → Task 7. Safe-by-default `--yes` gate → Tasks 5, 8. `config set` → Task 11. `companion status` → Task 12. Verify handshake → Task 9. run_adb result seam (needed for failure detection) → Task 2. Device spike de-risk → Task 1. Docs → Task 13.
- Out-of-scope items (port-rotation, approach A, Kotlin Finding-5) are referenced in Task 13 pointers, not implemented. ✔

**2. Placeholder scan** — no TBD/TODO; every code step shows complete code and exact run commands. ✔

**3. Type consistency** — the step dict `{"name","ok","status","message"}` (+ optional `data`) is defined in Task 3 and used unchanged in Tasks 4–10. The `adb(*args) -> CompletedProcess` seam (Task 2) is consumed identically everywhere. `run_companion_setup` returns `{"ok","steps"}` (Task 10), consumed by the CLI (Task 12). `verify` seams `negotiate`/`transport_factory` (Task 9) are threaded via `verify_kwargs` (Task 10). ✔

**Known test seam note:** Task 10's happy-path test must pass `verify_kwargs` so `verify` doesn't open a real socket (called out inline in Task 10).
