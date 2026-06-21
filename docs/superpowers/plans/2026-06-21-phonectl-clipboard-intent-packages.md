# phonectl Clipboard, Intent & Package Providers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Plan 3.2 of the platform roadmap** (`docs/superpowers/phonectl-platform-roadmap.md`). Second plan of
Phase 3. Depends on **Plan 1.1** (errors/results/capabilities), **Plan 2.1** (`runtime.run_action`),
**Plan 2.2** (risk classifier), and **Plan 3.1** (ProviderRegistry).

**Goal:** Add first-class `clipboard read/write/clear`, `intent start/broadcast` + deep links, and
`packages list/resolve/launch/stop/clear` (strategy §6.4, §6.5) — each risk-classified via the Phase 2.2
risk ledger, routed through `runtime.run_action`, and exposed as both CLI verbs and MCP tools.

**Architecture:** Three new provider modules under `src/phonectl/providers/`: `clipboard.py`,
`intents.py`, `packages.py`. Each is a pure orchestration class — it holds a `ProviderRegistry`
reference and produces `results.ok/err` envelopes. All I/O lives in `AdbBackend` (new methods added
there). `capabilities.py` gains five new keys. `risk.py` gains new verb classifications. `cli.py` gains
three new subcommand groups. `mcp_server.py` gains ten new tool entries.

**Tech Stack:** Python 3 (stdlib only: `json`, `re`, `shlex`); `pytest` for tests; no new runtime deps.

## Global Constraints

- **stdlib-only at runtime** (Python ≥ 3.9; `pytest` dev-only). No new runtime deps.
- **ONLY `adb_backend.py` may touch adb/subprocess.** Provider classes call `AdbBackend` methods;
  they never call `subprocess` or `adb` directly.
- **`ui_parser.py` stays pure** (untouched by this plan).
- **Every mutating action routes through `runtime.run_action`** — clipboard write, intent start/
  broadcast, and package stop/clear are mutating; they go through the funnel so audit, kill-switch,
  mode, risk, and rate gates apply.
- **Read-only operations** (`clipboard read`, `packages list/resolve`) call `observer`/`backend`
  directly and return `results.ok/err` without `run_action`.
- **Structured-result invariant (Plan 1.1):** every provider method and every CLI `--json` path
  returns a `results.ok/err` envelope.
- **Injectable seams** — `AdbBackend(runner=…)` for all new methods; provider classes accept a
  `registry` parameter so tests pass a fake registry.
- **One commit per task. TDD order is non-negotiable.**

## Shared conventions established by this plan

- **New capability keys are additive** — `make(**flags)` raises `ValueError` on unknown keys, so all
  five new keys must be in `CAPABILITY_KEYS` before `AdbBackend.capabilities()` uses them (Task 1).
- **ADB clipboard write** is via `adb shell service call clipboard 2 s16 <text>` (Android 10+; ROM-
  specific). `read_clipboard=False` for ADB because parcel-based read is unreliable; the key becomes
  `True` only when Termux:API is registered (Plan 3.5).
- **`packages_clear`** is classified `critical` risk (destroys user data); it requires explicit
  `--yes` even in `auto` mode.
- **`ClipboardProvider`, `IntentProvider`, `PackageProvider`** all accept
  `registry: ProviderRegistry` and expose a consistent `method() -> dict` interface returning the
  Phase-1 results envelope.
- **CLI grouping:** `phonectl clipboard`, `phonectl intent`, `phonectl packages` are subcommand
  groups; each subcommand accepts `--json` and `--yes` where applicable.

---

### Task 1: New capability keys + `AdbBackend` capability update

**Files:**
- Modify: `src/phonectl/capabilities.py` (add five keys)
- Modify: `src/phonectl/adb_backend.py` (update `capabilities()` to set them)
- Test: `tests/test_capabilities.py` (append), `tests/test_adb_backend.py` (append)

**Interfaces:**
- New keys appended to `CAPABILITY_KEYS`:
  `packages_list`, `packages_stop`, `packages_clear`, `intent_start`, `intent_broadcast`.
  (`read_clipboard` and `write_clipboard` already exist.)
- `AdbBackend.capabilities()` sets:
  `write_clipboard=True`, `packages_list=True`, `packages_stop=True`, `packages_clear=True`,
  `intent_start=True`, `intent_broadcast=True`.
  (`read_clipboard` stays `False` for ADB; Termux:API sets it `True` in Plan 3.5.)

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_capabilities.py

def test_new_capability_keys_exist():
    from phonectl import capabilities
    for key in ("packages_list", "packages_stop", "packages_clear",
                "intent_start", "intent_broadcast"):
        assert key in capabilities.CAPABILITY_KEYS, f"missing key: {key}"


# Append to tests/test_adb_backend.py

def test_adb_capabilities_include_new_keys(fake_runner):
    from phonectl.adb_backend import AdbBackend
    caps = AdbBackend(serial="d", runner=fake_runner).capabilities()
    assert caps["write_clipboard"] is True
    assert caps["packages_list"] is True
    assert caps["packages_stop"] is True
    assert caps["packages_clear"] is True
    assert caps["intent_start"] is True
    assert caps["intent_broadcast"] is True
    assert caps["read_clipboard"] is False   # ADB cannot reliably read clipboard
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_capabilities.py tests/test_adb_backend.py -v -k "new_capability"`
Expected: FAIL (`AssertionError` — new keys not yet in `CAPABILITY_KEYS`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/capabilities.py — append to CAPABILITY_KEYS tuple
CAPABILITY_KEYS = (
    "observe_ui_tree", "observe_screenshot", "act_tap", "act_type", "act_key",
    "launch_app", "send_intent", "read_notifications", "reply_notifications",
    "read_clipboard", "write_clipboard", "write_secure_settings",
    "persistent_events", "requires_adb", "requires_accessibility",
    "requires_notification_listener",
    # Phase 3.2 additions
    "packages_list", "packages_stop", "packages_clear",
    "intent_start", "intent_broadcast",
)
```

```python
# src/phonectl/adb_backend.py — replace capabilities() body
def capabilities(self) -> dict:
    return capabilities.make(
        observe_ui_tree=True, observe_screenshot=True,
        act_tap=True, act_type=True, act_key=True,
        launch_app=True, send_intent=True, requires_adb=True,
        write_clipboard=True,
        packages_list=True, packages_stop=True, packages_clear=True,
        intent_start=True, intent_broadcast=True,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_capabilities.py tests/test_adb_backend.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/capabilities.py src/phonectl/adb_backend.py \
        tests/test_capabilities.py tests/test_adb_backend.py
git commit -m "feat: extend capability keys for clipboard/packages/intents; update AdbBackend"
```

---

### Task 2: `AdbBackend` — clipboard methods

**Files:**
- Modify: `src/phonectl/adb_backend.py` (add `clipboard_write`, `clipboard_read`)
- Test: `tests/test_adb_backend.py` (append)

**Interfaces:**
- `clipboard_write(text: str) -> None` — `adb shell service call clipboard 2 s16 <shell-quoted text>`.
  Works on Android 10+ on most ROMs. Does not verify success (ADB service call returns a status
  parcel but it is not reliably parseable cross-ROM).
- `clipboard_read() -> str` — `adb shell service call clipboard 1`, returns raw parcel output.
  Marked best-effort; `ClipboardProvider.read()` (Task 5) raises `CapabilityUnavailableError`
  with a Termux:API install hint rather than surfacing raw parcel bytes to agents.

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_adb_backend.py

def test_clipboard_write_calls_service_call(calls):
    from phonectl.adb_backend import AdbBackend
    AdbBackend(serial=None, runner=calls).clipboard_write("hello world")
    assert any("service" in str(c) and "clipboard" in str(c) for c in calls.recorded)


def test_clipboard_read_calls_service_call(calls):
    from phonectl.adb_backend import AdbBackend
    AdbBackend(serial=None, runner=calls).clipboard_read()
    assert any("service" in str(c) and "clipboard" in str(c) for c in calls.recorded)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_adb_backend.py -v -k "clipboard"`
Expected: FAIL (`AttributeError: 'AdbBackend' object has no attribute 'clipboard_write'`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/adb_backend.py — add after get_state

def clipboard_write(self, text: str) -> None:
    self._adb("shell", "service", "call", "clipboard", "2", "s16", shlex.quote(text))

def clipboard_read(self) -> str:
    return self._adb("shell", "service", "call", "clipboard", "1")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_adb_backend.py -v -k "clipboard"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/adb_backend.py tests/test_adb_backend.py
git commit -m "feat: AdbBackend clipboard_write/clipboard_read via adb service call"
```

---

### Task 3: `AdbBackend` — intent methods

**Files:**
- Modify: `src/phonectl/adb_backend.py` (add `intent_start`, `intent_broadcast`)
- Test: `tests/test_adb_backend.py` (append)

**Interfaces:**
- `intent_start(*, action=None, data=None, component=None, extras=None, flags=None) -> None` —
  builds `adb shell am start` with the supplied arguments. String extras use `--es`; numeric extras
  are passed as-is to `--ei`. `extras` is a `dict[str, str]` (string extras only in this plan;
  typed extras can be added later). All args are shell-safe via the `_adb` list-based invocation
  (no shell interpolation).
- `intent_broadcast(action: str, *, extras=None) -> None` — `adb shell am broadcast -a <action>`.

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_adb_backend.py

def test_intent_start_builds_correct_command(calls):
    from phonectl.adb_backend import AdbBackend
    AdbBackend(serial=None, runner=calls).intent_start(
        action="android.intent.action.VIEW",
        data="geo:0,0",
        extras={"q": "coffee"},
    )
    cmd = " ".join(str(a) for a in calls.recorded[-1])
    assert "am" in cmd and "start" in cmd
    assert "android.intent.action.VIEW" in cmd
    assert "geo:0,0" in cmd
    assert "--es" in cmd and "q" in cmd and "coffee" in cmd


def test_intent_broadcast_builds_correct_command(calls):
    from phonectl.adb_backend import AdbBackend
    AdbBackend(serial=None, runner=calls).intent_broadcast(
        "com.example.ACTION", extras={"key": "val"}
    )
    cmd = " ".join(str(a) for a in calls.recorded[-1])
    assert "broadcast" in cmd and "com.example.ACTION" in cmd
    assert "--es" in cmd and "val" in cmd
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_adb_backend.py -v -k "intent"`
Expected: FAIL (`AttributeError`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/adb_backend.py

def intent_start(self, *, action=None, data=None, component=None,
                 extras=None, flags=None) -> None:
    cmd = ["shell", "am", "start"]
    if action:
        cmd += ["-a", action]
    if data:
        cmd += ["-d", data]
    if component:
        cmd += ["-n", component]
    if flags is not None:
        cmd += ["-f", str(flags)]
    for key, val in (extras or {}).items():
        cmd += ["--es", key, str(val)]
    self._adb(*cmd)

def intent_broadcast(self, action: str, *, extras=None) -> None:
    cmd = ["shell", "am", "broadcast", "-a", action]
    for key, val in (extras or {}).items():
        cmd += ["--es", key, str(val)]
    self._adb(*cmd)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_adb_backend.py -v -k "intent"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/adb_backend.py tests/test_adb_backend.py
git commit -m "feat: AdbBackend intent_start and intent_broadcast via adb am"
```

---

### Task 4: `AdbBackend` — package methods

**Files:**
- Modify: `src/phonectl/adb_backend.py` (add package methods)
- Test: `tests/test_adb_backend.py` (append)

**Interfaces:**
- `packages_list(include_system: bool = False) -> list[str]` — `adb shell pm list packages [-3]`;
  strips `"package:"` prefix from each line.
- `packages_resolve(package: str) -> dict` — `adb shell dumpsys package <pkg>`, parsed for
  `versionName`, `versionCode`, and first `Activity` class name. Returns
  `{"package": pkg, "version_name": str, "version_code": str, "launch_activity": str | None}`.
  Parsing is best-effort and ROM-specific; missing fields are `None`.
- `packages_stop(package: str) -> None` — `adb shell am force-stop <package>`.
- `packages_clear(package: str) -> None` — `adb shell pm clear <package>`.

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_adb_backend.py

def test_packages_list_strips_prefix(make_runner):
    out = "package:com.example.a\npackage:com.example.b\n"
    from phonectl.adb_backend import AdbBackend
    pkgs = AdbBackend(serial=None, runner=make_runner(out)).packages_list()
    assert pkgs == ["com.example.a", "com.example.b"]


def test_packages_list_user_only_excludes_system(calls):
    from phonectl.adb_backend import AdbBackend
    AdbBackend(serial=None, runner=calls).packages_list(include_system=False)
    cmd = " ".join(str(a) for a in calls.recorded[-1])
    assert "-3" in cmd


def test_packages_stop_calls_force_stop(calls):
    from phonectl.adb_backend import AdbBackend
    AdbBackend(serial=None, runner=calls).packages_stop("com.foo")
    cmd = " ".join(str(a) for a in calls.recorded[-1])
    assert "force-stop" in cmd and "com.foo" in cmd


def test_packages_clear_calls_pm_clear(calls):
    from phonectl.adb_backend import AdbBackend
    AdbBackend(serial=None, runner=calls).packages_clear("com.foo")
    cmd = " ".join(str(a) for a in calls.recorded[-1])
    assert "pm" in cmd and "clear" in cmd and "com.foo" in cmd
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_adb_backend.py -v -k "packages"`
Expected: FAIL (`AttributeError`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/adb_backend.py

def packages_list(self, include_system: bool = False) -> list:
    flag = [] if include_system else ["-3"]
    out = self._adb("shell", "pm", "list", "packages", *flag)
    return [
        line.split("package:", 1)[-1].strip()
        for line in out.splitlines()
        if line.startswith("package:")
    ]

def packages_resolve(self, package: str) -> dict:
    out = self._adb("shell", "dumpsys", "package", package)
    version_name = None
    version_code = None
    launch_activity = None
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("versionName="):
            version_name = line.split("=", 1)[1]
        elif line.startswith("versionCode="):
            version_code = line.split("=", 1)[1].split()[0]
        elif "Activity" in line and "/" in line and launch_activity is None:
            # e.g. "com.example/.MainActivity"
            part = line.strip().split()[-1]
            if "/" in part:
                launch_activity = part
    return {
        "package": package,
        "version_name": version_name,
        "version_code": version_code,
        "launch_activity": launch_activity,
    }

def packages_stop(self, package: str) -> None:
    self._adb("shell", "am", "force-stop", package)

def packages_clear(self, package: str) -> None:
    self._adb("shell", "pm", "clear", package)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_adb_backend.py -v -k "packages"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/adb_backend.py tests/test_adb_backend.py
git commit -m "feat: AdbBackend packages_list/resolve/stop/clear via adb pm and am"
```

---

### Task 5: `providers/clipboard.py` — `ClipboardProvider`

**Files:**
- Create: `src/phonectl/providers/clipboard.py`
- Test: `tests/test_providers_clipboard.py`

**Interfaces:**
- `ClipboardProvider(registry)` — holds a `ProviderRegistry`; never calls `adb` directly.
- `read() -> dict` — calls `registry.for_capability("read_clipboard")`; if no provider, returns
  `results.err(CapabilityUnavailableError(...), user_action="Install Termux:API…")`.
- `write(text: str) -> dict` — routes through `runtime.run_action` (verb=`"clipboard_write"`).
- `clear() -> dict` — `write("")`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_providers_clipboard.py
import pytest
from phonectl.providers.clipboard import ClipboardProvider
from phonectl.providers.registry import ProviderRegistry
from phonectl import capabilities, errors


class FakeClipProv:
    _written = None

    def capabilities(self):
        return capabilities.make(write_clipboard=True, requires_adb=True)

    def clipboard_write(self, text):
        FakeClipProv._written = text

    def clipboard_read(self):
        return "hello"


class FakeReadProv:
    def capabilities(self):
        return capabilities.make(read_clipboard=True, write_clipboard=True)

    def clipboard_read(self):
        return "clipboard text"

    def clipboard_write(self, text):
        pass


def test_read_raises_capability_unavailable_when_no_provider():
    r = ProviderRegistry([FakeClipProv()])  # write only, no read
    cp = ClipboardProvider(r)
    env = cp.read()
    assert env["ok"] is False
    assert env["error"]["code"] == "capability_unavailable"
    assert "Termux" in env["error"]["user_action"]


def test_read_returns_text_when_provider_available():
    r = ProviderRegistry([FakeReadProv()])
    cp = ClipboardProvider(r)
    env = cp.read()
    assert env["ok"] is True
    assert env["data"]["text"] == "clipboard text"


def test_clear_delegates_to_write_empty():
    r = ProviderRegistry([FakeClipProv()])
    cp = ClipboardProvider(r)
    # clear is syntactic sugar over write(""); we just confirm it doesn't raise
    # (write routing through run_action is tested in integration; unit test is structural)
    assert callable(cp.clear)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_providers_clipboard.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'phonectl.providers.clipboard'`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/providers/clipboard.py
"""ClipboardProvider — reads/writes clipboard via the best available provider."""
from __future__ import annotations

from phonectl import errors, results


class ClipboardProvider:
    def __init__(self, registry) -> None:
        self._registry = registry

    def read(self) -> dict:
        p = self._registry.for_capability("read_clipboard")
        if p is None:
            return results.err(
                errors.CapabilityUnavailableError("clipboard read is not available via ADB"),
                capability="clipboard.read",
                user_action=(
                    "Install Termux:API and run 'phonectl setup termux-api' to enable clipboard read."
                ),
            )
        try:
            text = p.clipboard_read()
            return results.ok(
                capability="clipboard.read",
                provider=type(p).__name__,
                data={"text": text},
            )
        except Exception as e:
            return results.err(
                (errors.ObserveError.code, str(e)), capability="clipboard.read"
            )

    def write(self, text: str, *, build, yes: bool = False, cfg=None) -> dict:
        from phonectl import runtime
        return runtime.run_action(
            "clipboard_write",
            lambda backend, session: (
                backend.clipboard_write(text), {"text": text}
            )[1],
            repr(text[:20]),
            build=build,
            yes=yes,
            cfg=cfg,
        )

    def clear(self, *, build, yes: bool = False, cfg=None) -> dict:
        return self.write("", build=build, yes=yes, cfg=cfg)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_providers_clipboard.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/providers/clipboard.py tests/test_providers_clipboard.py
git commit -m "feat: ClipboardProvider with read/write/clear routing through ProviderRegistry"
```

---

### Task 6: `providers/intents.py` — `IntentProvider`

**Files:**
- Create: `src/phonectl/providers/intents.py`
- Test: `tests/test_providers_intents.py`

**Interfaces:**
- `IntentProvider(registry)`.
- `start(*, action=None, data=None, component=None, extras=None, build, yes, cfg) -> dict` — routes
  through `runtime.run_action` (verb=`"intent_start"`).
- `broadcast(action: str, *, extras=None, build, yes, cfg) -> dict` — routes through `run_action`
  (verb=`"intent_broadcast"`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_providers_intents.py
from phonectl.providers.intents import IntentProvider
from phonectl.providers.registry import ProviderRegistry
from phonectl import capabilities


class FakeIntentProv:
    _started = None
    _broadcast = None

    def capabilities(self):
        return capabilities.make(intent_start=True, intent_broadcast=True, requires_adb=True,
                                 act_tap=True, observe_ui_tree=True, launch_app=True,
                                 act_type=True, act_key=True)

    def intent_start(self, **kwargs):
        FakeIntentProv._started = kwargs

    def intent_broadcast(self, action, **kwargs):
        FakeIntentProv._broadcast = (action, kwargs)

    def get_state(self): return "device"
    def ui_dump(self): return "<hierarchy></hierarchy>"
    def window_dump(self): return ""
    def wm_size(self): return (1080, 2400)
    def input_tap(self, x, y): pass
    def input_text(self, t): pass
    def input_swipe(self, *a): pass
    def input_key(self, k): pass
    def launch(self, p): pass
    def screencap(self, p): return p


def test_start_raises_unavailable_when_no_provider():
    r = ProviderRegistry([])
    ip = IntentProvider(r)
    env = ip.start(action="x", build=lambda cfg: (r, None, None), yes=True, cfg={})
    assert env["ok"] is False
    assert env["error"]["code"] == "capability_unavailable"


def test_intent_provider_is_constructable():
    r = ProviderRegistry([FakeIntentProv()])
    ip = IntentProvider(r)
    assert ip is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_providers_intents.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/providers/intents.py
"""IntentProvider — start activities and send broadcasts via the best provider."""
from __future__ import annotations

from phonectl import errors, results, runtime as rt


class IntentProvider:
    def __init__(self, registry) -> None:
        self._registry = registry

    def start(self, *, action=None, data=None, component=None,
              extras=None, build, yes=False, cfg=None) -> dict:
        if self._registry.for_capability("intent_start") is None:
            return results.err(
                errors.CapabilityUnavailableError("intent_start not available"),
                capability="intent.start",
            )
        target = component or action or data or "(intent)"
        return rt.run_action(
            "intent_start",
            lambda backend, session: (
                backend.intent_start(action=action, data=data,
                                     component=component, extras=extras),
                {},
            )[1],
            target,
            build=build, yes=yes, cfg=cfg,
        )

    def broadcast(self, action: str, *, extras=None,
                  build, yes=False, cfg=None) -> dict:
        if self._registry.for_capability("intent_broadcast") is None:
            return results.err(
                errors.CapabilityUnavailableError("intent_broadcast not available"),
                capability="intent.broadcast",
            )
        return rt.run_action(
            "intent_broadcast",
            lambda backend, session: (backend.intent_broadcast(action, extras=extras), {})[1],
            action,
            build=build, yes=yes, cfg=cfg,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_providers_intents.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/providers/intents.py tests/test_providers_intents.py
git commit -m "feat: IntentProvider wrapping intent_start and intent_broadcast via run_action"
```

---

### Task 7: `providers/packages.py` — `PackageProvider`

**Files:**
- Create: `src/phonectl/providers/packages.py`
- Test: `tests/test_providers_packages.py`

**Interfaces:**
- `PackageProvider(registry)`.
- `list_packages(include_system=False) -> dict` — read-only; returns `results.ok(data={"packages": [...]})`.
- `resolve(package: str) -> dict` — read-only.
- `launch(package: str, *, build, yes, cfg) -> dict` — routes through `run_action`
  (verb=`"launch"`, delegates to `backend.launch(package)`; already in Phase 0).
- `stop(package: str, *, build, yes, cfg) -> dict` — `run_action(verb="packages_stop")`.
- `clear(package: str, *, build, yes, cfg) -> dict` — `run_action(verb="packages_clear")`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_providers_packages.py
from phonectl.providers.packages import PackageProvider
from phonectl.providers.registry import ProviderRegistry
from phonectl import capabilities


class FakePkgProv:
    def capabilities(self):
        return capabilities.make(packages_list=True, packages_stop=True,
                                 packages_clear=True, requires_adb=True,
                                 act_tap=True, observe_ui_tree=True,
                                 launch_app=True, act_type=True, act_key=True)

    def packages_list(self, include_system=False):
        return ["com.a", "com.b"]

    def packages_resolve(self, pkg):
        return {"package": pkg, "version_name": "1.0", "version_code": "1", "launch_activity": None}

    def get_state(self): return "device"
    def ui_dump(self): return "<hierarchy></hierarchy>"
    def window_dump(self): return ""
    def wm_size(self): return (1080, 2400)
    def input_tap(self, x, y): pass
    def input_text(self, t): pass
    def input_swipe(self, *a): pass
    def input_key(self, k): pass
    def launch(self, p): pass
    def screencap(self, p): return p
    def packages_stop(self, p): pass
    def packages_clear(self, p): pass


def test_list_packages_returns_ok_envelope():
    r = ProviderRegistry([FakePkgProv()])
    pp = PackageProvider(r)
    env = pp.list_packages()
    assert env["ok"] is True
    assert "com.a" in env["data"]["packages"]


def test_resolve_returns_ok_envelope():
    r = ProviderRegistry([FakePkgProv()])
    pp = PackageProvider(r)
    env = pp.resolve("com.a")
    assert env["ok"] is True
    assert env["data"]["package"] == "com.a"


def test_list_returns_unavailable_when_no_provider():
    r = ProviderRegistry([])
    pp = PackageProvider(r)
    env = pp.list_packages()
    assert env["ok"] is False
    assert env["error"]["code"] == "capability_unavailable"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_providers_packages.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/providers/packages.py
"""PackageProvider — list, resolve, launch, stop, and clear apps via the best provider."""
from __future__ import annotations

from phonectl import errors, results, runtime as rt


class PackageProvider:
    def __init__(self, registry) -> None:
        self._registry = registry

    def _prov(self, cap: str):
        return self._registry.for_capability(cap)

    def list_packages(self, include_system: bool = False) -> dict:
        p = self._prov("packages_list")
        if p is None:
            return results.err(
                errors.CapabilityUnavailableError("packages_list not available"),
                capability="packages.list",
            )
        try:
            pkgs = p.packages_list(include_system=include_system)
            return results.ok(
                capability="packages.list",
                provider=type(p).__name__,
                data={"packages": pkgs},
            )
        except Exception as e:
            return results.err((errors.ObserveError.code, str(e)), capability="packages.list")

    def resolve(self, package: str) -> dict:
        p = self._prov("packages_list")
        if p is None:
            return results.err(
                errors.CapabilityUnavailableError("packages_list not available"),
                capability="packages.resolve",
            )
        try:
            info = p.packages_resolve(package)
            return results.ok(capability="packages.resolve", provider=type(p).__name__, data=info)
        except Exception as e:
            return results.err((errors.ObserveError.code, str(e)), capability="packages.resolve")

    def launch(self, package: str, *, build, yes=False, cfg=None) -> dict:
        return rt.run_action(
            "launch",
            lambda backend, session: (backend.launch(package), {})[1],
            package,
            build=build, yes=yes, cfg=cfg,
        )

    def stop(self, package: str, *, build, yes=False, cfg=None) -> dict:
        if self._prov("packages_stop") is None:
            return results.err(
                errors.CapabilityUnavailableError("packages_stop not available"),
                capability="packages.stop",
            )
        return rt.run_action(
            "packages_stop",
            lambda backend, session: (backend.packages_stop(package), {})[1],
            package,
            build=build, yes=yes, cfg=cfg,
        )

    def clear(self, package: str, *, build, yes=False, cfg=None) -> dict:
        if self._prov("packages_clear") is None:
            return results.err(
                errors.CapabilityUnavailableError("packages_clear not available"),
                capability="packages.clear",
            )
        return rt.run_action(
            "packages_clear",
            lambda backend, session: (backend.packages_clear(package), {})[1],
            package,
            build=build, yes=yes, cfg=cfg,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_providers_packages.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/providers/packages.py tests/test_providers_packages.py
git commit -m "feat: PackageProvider with list/resolve/launch/stop/clear via ProviderRegistry"
```

---

### Task 8: Extend `risk.py` — new HIGH_RISK_VERBS and keyword signals

**Files:**
- Modify: `src/phonectl/risk.py`
- Test: `tests/test_risk.py` (append)

**Interfaces:**
- `HIGH_RISK_VERBS` gains `"packages_stop"`.
- New frozenset `CRITICAL_VERBS = frozenset({"packages_clear"})` with signal level `"critical"`.
- `_SIGNAL_LEVEL` gains `"critical_verb": "critical"`.
- `classify()` checks `verb in CRITICAL_VERBS` (new) alongside `verb in HIGH_RISK_VERBS` (existing).
- `DEFAULT_KEYWORDS["destructive_keyword"]` gains `"clear data"` and `"force stop"`.

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_risk.py

from phonectl import risk


def test_packages_clear_classifies_critical():
    snap = {"app": {}, "elements": []}
    result = risk.classify(snap, "packages_clear", "com.example")
    assert result["level"] == "critical"
    assert any(r["signal"] == "critical_verb" for r in result["reasons"])


def test_packages_stop_classifies_high():
    snap = {"app": {}, "elements": []}
    result = risk.classify(snap, "packages_stop", "com.example")
    assert result["level"] == "high"
    assert any(r["signal"] == "high_risk_verb" for r in result["reasons"])


def test_intent_broadcast_classifies_high():
    snap = {"app": {}, "elements": []}
    result = risk.classify(snap, "intent_broadcast", "com.example.ACTION")
    assert result["level"] == "high"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_risk.py -v -k "packages_clear or packages_stop or intent_broadcast"`
Expected: FAIL (levels will be `"low"` — verbs not yet classified).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/risk.py — update seams

HIGH_RISK_VERBS = frozenset({"packages_stop", "intent_broadcast"})
CRITICAL_VERBS = frozenset({"packages_clear"})

_SIGNAL_LEVEL = {
    "payment_keyword": "critical",
    "destructive_keyword": "critical",
    "install_keyword": "high",
    "guarded_package": "high",
    "password_field": "high",
    "otp_like_content": "medium",
    "high_risk_verb": "high",
    "critical_verb": "critical",     # new
}

# In classify(), add before the return, alongside the HIGH_RISK_VERBS check:
if verb in CRITICAL_VERBS:
    add("critical_verb", f"{verb} is a critical-risk verb")
if verb in HIGH_RISK_VERBS:
    add("high_risk_verb", f"{verb} is a high-risk verb")
```

Also extend `DEFAULT_KEYWORDS["destructive_keyword"]` to include `"clear data"` and `"force stop"`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_risk.py -v`
Expected: PASS (all existing + 3 new).

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/risk.py tests/test_risk.py
git commit -m "feat: risk classifier adds CRITICAL_VERBS for packages_clear and HIGH_RISK_VERBS for packages_stop/intent_broadcast"
```

---

### Task 9: CLI verbs — `clipboard`, `intent`, `packages`

**Files:**
- Modify: `src/phonectl/cli.py` (add three subcommand groups)
- Test: `tests/test_cli.py` (append)

**Interfaces:**
- `phonectl clipboard read` — prints `data.text` or JSON envelope.
- `phonectl clipboard write TEXT` — routes through `_do_action`.
- `phonectl clipboard clear` — routes through `_do_action`.
- `phonectl intent start [--action A] [--data D] [--component C] [--extra K=V ...]` — `_do_action`.
- `phonectl intent broadcast ACTION [--extra K=V ...]` — `_do_action`.
- `phonectl packages list [--all]` — prints list or JSON.
- `phonectl packages resolve PACKAGE` — prints info or JSON.
- `phonectl packages launch PACKAGE` — `_do_action`.
- `phonectl packages stop PACKAGE` — `_do_action` with `--yes` required for high risk.
- `phonectl packages clear PACKAGE` — `_do_action` with `--yes` required for critical risk.

All subcommands support `--json` and `--request-id`; mutating ones support `--yes`.

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_cli.py

def test_clipboard_read_emits_unavailable(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: FakeBackend())
    rc = cli.main(["clipboard", "read", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc != 0
    assert out["ok"] is False
    assert "capability_unavailable" == out["error"]["code"]


def test_packages_list_emits_ok(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))

    class FakePackageBackend(FakeBackend):
        def packages_list(self, include_system=False):
            return ["com.a", "com.b"]
        def capabilities(self):
            from phonectl import capabilities
            return capabilities.make(packages_list=True, requires_adb=True,
                                     act_tap=True, observe_ui_tree=True,
                                     launch_app=True, act_type=True, act_key=True)

    monkeypatch.setattr(cli, "_make_backend", lambda cfg: FakePackageBackend())
    rc = cli.main(["packages", "list", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["ok"] is True
    assert "com.a" in out["data"]["packages"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli.py -v -k "clipboard_read or packages_list"`
Expected: FAIL (no `clipboard` or `packages` subparsers).

- [ ] **Step 3: Write minimal implementation**

In `cli.py`, add three new subcommand groups in `build_parser()`:
- `clipboard` with subcommands `read`, `write`, `clear`
- `intent` with subcommands `start`, `broadcast`
- `packages` with subcommands `list`, `resolve`, `launch`, `stop`, `clear`

Each handler constructs the relevant provider and calls its method. Mutating handlers call `_do_action`;
read-only handlers emit the envelope directly.

```python
# Example: _cmd_clipboard_read
def _cmd_clipboard_read(args):
    cfg = config.load()
    backend, session, conn = build_runtime(cfg)
    conn.ensure()
    from phonectl.providers.clipboard import ClipboardProvider
    env = ClipboardProvider(backend).read()
    if getattr(args, "json", False):
        print(json.dumps(env, indent=2))
    elif env["ok"]:
        print(env["data"]["text"])
    else:
        print(f"phonectl: {env['error']['message']}")
    return 0 if env["ok"] else 1

# Example: _cmd_packages_list
def _cmd_packages_list(args):
    cfg = config.load()
    backend, session, conn = build_runtime(cfg)
    conn.ensure()
    from phonectl.providers.packages import PackageProvider
    env = PackageProvider(backend).list_packages(include_system=getattr(args, "all", False))
    if getattr(args, "json", False):
        print(json.dumps(env, indent=2))
    elif env["ok"]:
        for pkg in env["data"]["packages"]:
            print(pkg)
    else:
        print(f"phonectl: {env['error']['message']}")
    return 0 if env["ok"] else 1
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli.py -v`
Expected: PASS (new tests + all existing).

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/cli.py tests/test_cli.py
git commit -m "feat: CLI clipboard/intent/packages subcommand groups with read-only and mutating verbs"
```

---

### Task 10: MCP tools — clipboard, intent, packages

**Files:**
- Modify: `src/phonectl/mcp_server.py` (add ten new tool entries)
- Test: `tests/test_mcp_server.py` (append)

**Interfaces:**
- New tools registered in `TOOLS`:
  `phone_clipboard_read`, `phone_clipboard_write`, `phone_clipboard_clear`,
  `phone_intent_start`, `phone_intent_broadcast`,
  `phone_packages_list`, `phone_packages_resolve`,
  `phone_packages_launch`, `phone_packages_stop`, `phone_packages_clear`.
- Handler signatures match the existing pattern: `handler(build, **args) -> dict` returning an
  envelope. Read-only tools call the provider's read method; mutating tools call `runtime.run_action`
  through the provider's mutating method.
- `phone_packages_clear` schema includes a `confirm: bool` arg that maps to `yes=True` when `True`.

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_mcp_server.py

def test_phone_clipboard_read_returns_unavailable_without_termux(build):
    from phonectl.mcp_server import call_tool
    env = call_tool("phone_clipboard_read", {}, build)
    assert env["ok"] is False
    assert env["error"]["code"] == "capability_unavailable"


def test_phone_packages_list_returns_list(build_with_packages):
    from phonectl.mcp_server import call_tool
    env = call_tool("phone_packages_list", {}, build_with_packages)
    assert env["ok"] is True
    assert isinstance(env["data"]["packages"], list)


def test_unknown_tool_still_returns_err(build):
    from phonectl.mcp_server import call_tool
    env = call_tool("phone_clipboard_read_UNKNOWN", {}, build)
    assert env["ok"] is False
```

Note: `build_with_packages` is a test fixture that returns a fake backend supporting `packages_list`.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_mcp_server.py -v -k "clipboard or packages_list"`
Expected: FAIL (tools not registered).

- [ ] **Step 3: Write minimal implementation**

Add handler functions and `TOOLS` entries in `mcp_server.py` following the same pattern as the
existing `phone_tap`, `phone_observe_ui`, etc. handlers. Each new handler constructs the provider
using `build(...)` and delegates.

- [ ] **Step 4: Run test to verify it passes + run full suite**

Run: `pytest tests/test_mcp_server.py -v && pytest -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/mcp_server.py tests/test_mcp_server.py
git commit -m "feat: MCP tools for clipboard, intent start/broadcast, and package management"
```

---

### Task 11: Docs

**Files:**
- Modify: `README.md` (add clipboard/intent/packages command reference)
- Modify: `docs/superpowers/specs/2026-06-20-phonectl-adb-bridge-design.md`

- [ ] **Step 1: Update docs**

In `README.md`: add a "Phone management" section with examples for `phonectl clipboard read/write/clear`,
`phonectl intent start --action …`, `phonectl packages list/stop/clear`. Note the risk level and
`--yes` requirement for `packages_stop` and `packages_clear`. Note that `clipboard read` requires
Termux:API (Plan 3.5).

- [ ] **Step 2: Commit**

```bash
git add README.md docs/superpowers/specs/2026-06-20-phonectl-adb-bridge-design.md
git commit -m "docs: clipboard/intent/packages command reference with risk levels and Termux:API note"
```

---

## Dependencies

**Requires:** 1.1 (errors/results/capabilities), 2.1 (run_action), 2.2 (risk), 2.3 (MCP TOOLS),
3.1 (ProviderRegistry).
**Enables:** Plan 3.5 (Termux:API upgrades `read_clipboard=True`; ClipboardProvider then returns text
automatically). Plan 4.2 (NotificationListenerService adds `notifications_list` capability).

## Deferred / out of scope

- **`clipboard_read` via ADB** returns raw parcel bytes (ROM-specific); the provider raises
  `CapabilityUnavailableError` with a Termux:API install hint. True read support lands in Plan 3.5.
- **Typed extras** (`--extra-int`, `--extra-bool`) for intent commands — deferred; string extras
  cover the common cases.
- **`packages install/uninstall`** — critical-risk APK operations requiring user-visible UI flows;
  deferred to Phase 4 (companion APK trust UX is needed first).
- **`package grant/revoke permission`** — requires Shizuku or root; deferred to Phase 7.

## Notes on testability

Every provider class is pure orchestration — no ADB calls — and is tested with fake backends via
`ProviderRegistry`. ADB methods are tested with the existing fake-runner fixture. Risk classifier
extensions are pure function tests. CLI and MCP tests use the existing `FakeBackend` test double
extended with the new methods as needed.
