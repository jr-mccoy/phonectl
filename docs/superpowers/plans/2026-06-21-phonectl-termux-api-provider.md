# phonectl Termux:API Provider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Plan 3.5 of the platform roadmap** (`docs/superpowers/phonectl-platform-roadmap.md`). Fifth (final)
plan of Phase 3. Depends on **Plan 1.1** (capabilities, results, errors), **Plan 3.1** (ProviderRegistry),
and **Plan 3.2** (clipboard capability keys, `ClipboardProvider`).

**Goal:** Add an optional `TermuxApiProvider` that is discovered at runtime, never a hard dependency
(strategy §13.2, §19). When `termux-battery-status` is on `PATH`, the provider registers itself in the
`ProviderRegistry` with `read_clipboard=True`, `write_clipboard=True`, `device_battery=True`,
`device_wifi_info=True`, `tts_speak=True`. It **supersedes ADB for clipboard operations** (ADB write
works; ADB read is unreliable — Termux:API read is the upgrade). New device-state and TTS capabilities
have no ADB equivalent and unlock the Phase 3.2 `phonectl device battery|wifi` and a new
`phonectl tts speak TEXT` verb.

**Architecture:** One new module `src/phonectl/providers/termux.py` (`TermuxApiProvider`). No
changes to `adb_backend.py`. `capabilities.py` gains four new keys. `cli.build_runtime()` probes
for Termux:API and prepends `TermuxApiProvider` to the registry if found (so it takes priority over
ADB for capabilities it supports). CLI and MCP gain new verbs. `ClipboardProvider.read()` from Plan
3.2 automatically upgrades once Termux:API is in the registry (no code change needed — it calls
`registry.for_capability("read_clipboard")` which now returns `TermuxApiProvider`).

**Tech Stack:** Python 3 (stdlib only: `json`, `shutil`, `subprocess`); `pytest` for tests; no
new runtime deps. `termux-api` (the Termux app + companion APK) is a runtime prerequisite for the
provider to activate — **never imported or hard-required**; the provider degrades gracefully when
absent.

## Global Constraints

- **stdlib-only at runtime.** `TermuxApiProvider` uses `subprocess.run` and `shutil.which` —
  both stdlib. Never import the `termux-api` package; we invoke the `termux-*` CLI programs.
- **Backend isolation:** `TermuxApiProvider` calls `termux-*` shell programs via `runner` (injectable).
  It does not call `adb`. It does not call `ui_parser` or `observer`.
- **`ui_parser.py` stays pure** (untouched).
- **Discovery is non-blocking:** `is_available()` checks `shutil.which("termux-battery-status")`.
  It does not actually run `termux-battery-status`; a `which` check is fast, safe, and sufficient
  for unit tests with a fake `which`.
- **Injectable seams:** `TermuxApiProvider(runner=subprocess.run, which=shutil.which)`. Tests pass
  a fake `runner` that returns preset outputs and a fake `which` that returns a path or `None`.
- **Structured-result invariant (Plan 1.1):** CLI `--json` paths and MCP tools return
  `results.ok/err` envelopes.
- **One commit per task. TDD order is non-negotiable.**

## Shared conventions established by this plan

- **New capability keys** (`device_battery`, `device_wifi_info`, `tts_speak`) are added to
  `CAPABILITY_KEYS` here. `read_clipboard` and `write_clipboard` already exist (Plan 3.2).
- **`TermuxApiProvider` is prepended** to the registry in `cli.build_runtime()` via
  `_make_termux_provider()` — a pure helper that returns `TermuxApiProvider()` if discovered or
  `None`. The registry is constructed as:
  ```python
  providers = [p for p in [_make_termux_provider(), adb] if p is not None]
  registry = ProviderRegistry(providers)
  ```
  This ensures Termux:API is checked first for `read_clipboard` and `write_clipboard`.
- **`TermuxApiProvider` does not implement ADB-only methods** (`ui_dump`, `wm_size`, `input_tap`,
  etc.). `ProviderRegistry.__getattr__` falls through to ADB for those.
- **TTS is fire-and-forget:** `tts_speak` runs `termux-tts-speak` and does not wait for speech to
  finish (subprocess returns immediately on most Android versions; the TTS engine runs asynchronously).

---

### Task 1: New capability keys + `TermuxApiProvider` discovery + `capabilities()`

**Files:**
- Modify: `src/phonectl/capabilities.py` (add three new keys)
- Create: `src/phonectl/providers/termux.py`
- Test: `tests/test_capabilities.py` (append), `tests/test_providers_termux.py` (create)

**Interfaces:**
- New keys in `CAPABILITY_KEYS`: `device_battery`, `device_wifi_info`, `tts_speak`.
- `TermuxApiProvider(runner=subprocess.run, which=shutil.which)`.
- `TermuxApiProvider.is_available() -> bool` — `which("termux-battery-status") is not None`.
- `TermuxApiProvider.capabilities() -> dict` — if `is_available()`: returns
  `capabilities.make(read_clipboard=True, write_clipboard=True, device_battery=True,
  device_wifi_info=True, tts_speak=True)`. Else: `capabilities.make()` (all False).

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_capabilities.py

def test_new_device_capability_keys_exist():
    from phonectl import capabilities
    for key in ("device_battery", "device_wifi_info", "tts_speak"):
        assert key in capabilities.CAPABILITY_KEYS, f"missing key: {key}"


# tests/test_providers_termux.py (new file)
import pytest
from phonectl.providers.termux import TermuxApiProvider
from phonectl import capabilities


def _fake_which_found(name):
    return "/data/data/com.termux/files/usr/bin/" + name


def _fake_which_missing(name):
    return None


class FakeRunner:
    def __init__(self, outputs):
        self.outputs = outputs  # list of stdout strings, consumed in order
        self.calls = []

    def __call__(self, cmd, *, capture_output=True, text=True, input=None, **kw):
        self.calls.append(cmd)
        stdout = self.outputs.pop(0) if self.outputs else ""
        return type("R", (), {"stdout": stdout, "returncode": 0})()


def test_is_available_true_when_on_path():
    p = TermuxApiProvider(which=_fake_which_found)
    assert p.is_available() is True


def test_is_available_false_when_not_on_path():
    p = TermuxApiProvider(which=_fake_which_missing)
    assert p.is_available() is False


def test_capabilities_all_true_when_available():
    p = TermuxApiProvider(which=_fake_which_found)
    caps = p.capabilities()
    assert caps["read_clipboard"] is True
    assert caps["write_clipboard"] is True
    assert caps["device_battery"] is True
    assert caps["device_wifi_info"] is True
    assert caps["tts_speak"] is True


def test_capabilities_all_false_when_not_available():
    p = TermuxApiProvider(which=_fake_which_missing)
    caps = p.capabilities()
    assert all(v is False for v in caps.values())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_capabilities.py tests/test_providers_termux.py -v`
Expected: FAIL (`ModuleNotFoundError` for `termux.py`; missing keys in `CAPABILITY_KEYS`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/capabilities.py — append to CAPABILITY_KEYS
CAPABILITY_KEYS = (
    # ... existing keys ...
    "packages_list", "packages_stop", "packages_clear",
    "intent_start", "intent_broadcast",
    # Phase 3.5 additions
    "device_battery",
    "device_wifi_info",
    "tts_speak",
)
```

```python
# src/phonectl/providers/termux.py
"""Optional Termux:API provider — discovered at runtime, never a hard dependency."""
from __future__ import annotations

import shutil
import subprocess

from phonectl import capabilities as caps_mod


class TermuxApiProvider:
    def __init__(self, runner=subprocess.run, which=shutil.which) -> None:
        self._runner = runner
        self._which = which

    def is_available(self) -> bool:
        return self._which("termux-battery-status") is not None

    def capabilities(self) -> dict:
        if not self.is_available():
            return caps_mod.make()
        return caps_mod.make(
            read_clipboard=True,
            write_clipboard=True,
            device_battery=True,
            device_wifi_info=True,
            tts_speak=True,
        )

    def _run(self, *cmd: str) -> str:
        res = self._runner(list(cmd), capture_output=True, text=True)
        return res.stdout
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_capabilities.py tests/test_providers_termux.py -v`
Expected: PASS (new capability key tests + 5 Termux provider tests).

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/capabilities.py src/phonectl/providers/termux.py \
        tests/test_capabilities.py tests/test_providers_termux.py
git commit -m "feat: TermuxApiProvider discovery and capability advertisement (device_battery/wifi/tts)"
```

---

### Task 2: Clipboard read + write via Termux:API

**Files:**
- Modify: `src/phonectl/providers/termux.py` (add `clipboard_read`, `clipboard_write`)
- Test: `tests/test_providers_termux.py` (append)

**Interfaces:**
- `clipboard_read() -> str` — runs `termux-clipboard-get`; returns stdout stripped.
- `clipboard_write(text: str) -> None` — runs `termux-clipboard-set` with `text` passed on stdin
  (avoids shell quoting issues; `termux-clipboard-set` reads stdin when no argument is given).

These methods make `ClipboardProvider.read()` from Plan 3.2 work automatically when Termux:API is
in the registry — no changes to `ClipboardProvider` are needed.

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_providers_termux.py

def test_clipboard_read_calls_termux_clipboard_get():
    runner = FakeRunner(["hello world\n"])
    p = TermuxApiProvider(runner=runner, which=_fake_which_found)
    result = p.clipboard_read()
    assert result == "hello world"
    assert any("termux-clipboard-get" in str(c) for c in runner.calls)


def test_clipboard_write_passes_text_via_stdin():
    calls = []

    def fake_runner(cmd, *, capture_output=True, text=True, input=None, **kw):
        calls.append((cmd, input))
        return type("R", (), {"stdout": "", "returncode": 0})()

    p = TermuxApiProvider(runner=fake_runner, which=_fake_which_found)
    p.clipboard_write("test text")
    assert any("termux-clipboard-set" in str(c[0]) for c in calls)
    assert any(c[1] == "test text" for c in calls)


def test_clipboard_read_strips_trailing_newline():
    runner = FakeRunner(["clipboard content\n"])
    p = TermuxApiProvider(runner=runner, which=_fake_which_found)
    assert p.clipboard_read() == "clipboard content"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_providers_termux.py -v -k "clipboard"`
Expected: FAIL (`AttributeError: 'TermuxApiProvider' object has no attribute 'clipboard_read'`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/providers/termux.py — add to TermuxApiProvider

def clipboard_read(self) -> str:
    return self._run("termux-clipboard-get").strip()

def clipboard_write(self, text: str) -> None:
    self._runner(
        ["termux-clipboard-set"],
        input=text, capture_output=True, text=True
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_providers_termux.py -v -k "clipboard"`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/providers/termux.py tests/test_providers_termux.py
git commit -m "feat: TermuxApiProvider clipboard_read (termux-clipboard-get) and clipboard_write (stdin)"
```

---

### Task 3: Battery + wifi device state

**Files:**
- Modify: `src/phonectl/providers/termux.py` (add `battery_status`, `wifi_info`)
- Test: `tests/test_providers_termux.py` (append)

**Interfaces:**
- `battery_status() -> dict` — runs `termux-battery-status`; parses JSON output. Returns the parsed
  dict (keys: `health`, `percentage`, `plugged`, `status`, `temperature`). Raises `ValueError` on
  unparseable output.
- `wifi_info() -> dict` — runs `termux-wifi-connectioninfo`; parses JSON output. Returns the parsed
  dict (keys: `bssid`, `frequency_mhz`, `ip`, `link_speed_mbps`, `mac_address`, `rssi`, `ssid`).
  Returns `{"ssid": null, "connected": false}` when the output is empty or unparseable.

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_providers_termux.py

import json as _json

_BATTERY_JSON = _json.dumps({
    "health": "GOOD", "percentage": 87,
    "plugged": "UNPLUGGED", "status": "DISCHARGING", "temperature": 28.5
})

_WIFI_JSON = _json.dumps({
    "bssid": "aa:bb:cc:dd:ee:ff", "frequency_mhz": 5180, "ip": "192.168.1.5",
    "link_speed_mbps": 433, "mac_address": "11:22:33:44:55:66",
    "rssi": -45, "ssid": "HomeNet"
})


def test_battery_status_parses_json():
    runner = FakeRunner([_BATTERY_JSON])
    p = TermuxApiProvider(runner=runner, which=_fake_which_found)
    bat = p.battery_status()
    assert bat["percentage"] == 87
    assert bat["status"] == "DISCHARGING"


def test_battery_status_calls_termux_battery_status():
    runner = FakeRunner([_BATTERY_JSON])
    p = TermuxApiProvider(runner=runner, which=_fake_which_found)
    p.battery_status()
    assert any("termux-battery-status" in str(c) for c in runner.calls)


def test_wifi_info_parses_json():
    runner = FakeRunner([_WIFI_JSON])
    p = TermuxApiProvider(runner=runner, which=_fake_which_found)
    info = p.wifi_info()
    assert info["ssid"] == "HomeNet"
    assert info["ip"] == "192.168.1.5"


def test_wifi_info_returns_disconnected_on_empty_output():
    runner = FakeRunner([""])
    p = TermuxApiProvider(runner=runner, which=_fake_which_found)
    info = p.wifi_info()
    assert info.get("connected") is False or info.get("ssid") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_providers_termux.py -v -k "battery or wifi"`
Expected: FAIL (`AttributeError`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/providers/termux.py

import json as _json


def battery_status(self) -> dict:
    raw = self._run("termux-battery-status")
    return _json.loads(raw)


def wifi_info(self) -> dict:
    raw = self._run("termux-wifi-connectioninfo").strip()
    if not raw:
        return {"ssid": None, "connected": False}
    try:
        return _json.loads(raw)
    except _json.JSONDecodeError:
        return {"ssid": None, "connected": False}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_providers_termux.py -v -k "battery or wifi"`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/providers/termux.py tests/test_providers_termux.py
git commit -m "feat: TermuxApiProvider battery_status and wifi_info via termux-battery-status/termux-wifi-connectioninfo"
```

---

### Task 4: TTS via Termux:API

**Files:**
- Modify: `src/phonectl/providers/termux.py` (add `tts_speak`)
- Test: `tests/test_providers_termux.py` (append)

**Interfaces:**
- `tts_speak(text: str, *, language: str | None = None, rate: float | None = None) -> None`
  — runs `termux-tts-speak [-l LANG] [-r RATE] TEXT`. Text is passed as a positional argument
  (last); `language` maps to `-l`; `rate` (speech rate multiplier, e.g. `1.0` = normal) maps to
  `-r`. Fire-and-forget: the subprocess call returns as soon as Android's TTS engine accepts the
  request (TTS runs asynchronously).

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_providers_termux.py

def test_tts_speak_calls_termux_tts_speak():
    runner = FakeRunner([""])
    p = TermuxApiProvider(runner=runner, which=_fake_which_found)
    p.tts_speak("hello world")
    cmd = " ".join(str(a) for a in runner.calls[-1])
    assert "termux-tts-speak" in cmd
    assert "hello world" in cmd


def test_tts_speak_includes_language_flag():
    runner = FakeRunner([""])
    p = TermuxApiProvider(runner=runner, which=_fake_which_found)
    p.tts_speak("bonjour", language="fr")
    cmd = " ".join(str(a) for a in runner.calls[-1])
    assert "-l" in cmd and "fr" in cmd


def test_tts_speak_includes_rate_flag():
    runner = FakeRunner([""])
    p = TermuxApiProvider(runner=runner, which=_fake_which_found)
    p.tts_speak("fast speech", rate=1.5)
    cmd = " ".join(str(a) for a in runner.calls[-1])
    assert "-r" in cmd and "1.5" in cmd
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_providers_termux.py -v -k "tts"`
Expected: FAIL (`AttributeError`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/providers/termux.py

def tts_speak(self, text: str, *,
              language: str | None = None,
              rate: float | None = None) -> None:
    cmd = ["termux-tts-speak"]
    if language is not None:
        cmd += ["-l", language]
    if rate is not None:
        cmd += ["-r", str(rate)]
    cmd.append(text)
    self._run(*cmd)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_providers_termux.py -v -k "tts"`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/providers/termux.py tests/test_providers_termux.py
git commit -m "feat: TermuxApiProvider.tts_speak via termux-tts-speak with optional language and rate"
```

---

### Task 5: Wire `TermuxApiProvider` into `cli.build_runtime()`

**Files:**
- Modify: `src/phonectl/cli.py` (`build_runtime` probes for Termux:API and prepends it)
- Test: `tests/test_cli.py` (append)

**Interfaces:**
- `_make_termux_provider() -> TermuxApiProvider | None` — constructs `TermuxApiProvider()` and
  returns it if `is_available()`, else `None`. Separated from `build_runtime` so tests can patch it.
- `build_runtime(cfg, backend=None)`:
  ```python
  adb = backend or _make_backend(cfg)
  termux = _make_termux_provider()
  providers = [p for p in [termux, adb] if p is not None]
  registry = ProviderRegistry(providers)
  ```
  Termux:API is first so it wins for `read_clipboard` and `write_clipboard`.

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_cli.py

def test_build_runtime_includes_termux_when_available(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    from phonectl import cli, config
    from phonectl.providers.termux import TermuxApiProvider

    fake_termux = TermuxApiProvider(
        which=lambda name: "/usr/bin/" + name  # always found
    )
    monkeypatch.setattr(cli, "_make_termux_provider", lambda: fake_termux)

    cfg = config.load()
    registry, session, conn = cli.build_runtime(cfg)
    assert registry.for_capability("read_clipboard") is fake_termux


def test_build_runtime_excludes_termux_when_not_available(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    from phonectl import cli, config

    monkeypatch.setattr(cli, "_make_termux_provider", lambda: None)
    cfg = config.load()
    registry, session, conn = cli.build_runtime(cfg)
    assert registry.for_capability("read_clipboard") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli.py -v -k "termux"`
Expected: FAIL (`AttributeError: module 'phonectl.cli' has no attribute '_make_termux_provider'`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/cli.py — add helper + update build_runtime

from phonectl.providers.termux import TermuxApiProvider


def _make_termux_provider():
    p = TermuxApiProvider()
    return p if p.is_available() else None


def build_runtime(cfg, backend=None):
    adb = backend or _make_backend(cfg)
    termux = _make_termux_provider()
    providers = [p for p in [termux, adb] if p is not None]
    registry = ProviderRegistry(providers)
    session = Session()
    conn = Connection(registry, cfg)
    return registry, session, conn
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli.py -v`
Expected: PASS (new tests + all existing — existing tests patch `_make_backend` and don't call
real `termux-*` programs; the registry degrades gracefully when Termux:API is absent).

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/cli.py tests/test_cli.py
git commit -m "feat: wire TermuxApiProvider into build_runtime; prepended for clipboard priority"
```

---

### Task 6: CLI verbs — `phonectl device battery|wifi` and `phonectl tts speak TEXT`

**Files:**
- Modify: `src/phonectl/cli.py` (add new verbs)
- Test: `tests/test_cli.py` (append)

**Interfaces:**
- `phonectl device battery [--json]` — calls `TermuxApiProvider.battery_status()` via the registry;
  returns `results.ok(capability="device.battery", data={...})` or `results.err(
  CapabilityUnavailableError(...), user_action="Install Termux:API…")` if unavailable.
- `phonectl device wifi [--json]` — analogous for `wifi_info()`.
- `phonectl tts speak TEXT [--language LANG] [--rate RATE] [--json]` — fire-and-forget TTS;
  returns `results.ok(capability="tts.speak")`.

All three read-only (battery, wifi) or fire-and-forget (TTS); they do not route through
`run_action` since they are not mutating UI operations. Risk classification is not needed.

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_cli.py

def test_device_battery_unavailable_without_termux(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: FakeBackend())
    monkeypatch.setattr(cli, "_make_termux_provider", lambda: None)
    rc = cli.main(["device", "battery", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc != 0
    assert out["ok"] is False
    assert out["error"]["code"] == "capability_unavailable"


def test_tts_speak_unavailable_without_termux(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: FakeBackend())
    monkeypatch.setattr(cli, "_make_termux_provider", lambda: None)
    rc = cli.main(["tts", "speak", "hello", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc != 0
    assert out["ok"] is False


def test_device_battery_ok_with_termux(tmp_path, monkeypatch, capsys):
    import json as _json
    from phonectl.providers.termux import TermuxApiProvider

    battery_data = {"percentage": 42, "status": "DISCHARGING", "health": "GOOD",
                    "plugged": "UNPLUGGED", "temperature": 27.0}

    class FakeTermux(TermuxApiProvider):
        def is_available(self): return True
        def capabilities(self):
            from phonectl import capabilities
            return capabilities.make(device_battery=True, device_wifi_info=True,
                                     tts_speak=True, read_clipboard=True,
                                     write_clipboard=True)
        def battery_status(self): return battery_data

    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: FakeBackend())
    monkeypatch.setattr(cli, "_make_termux_provider", lambda: FakeTermux())
    rc = cli.main(["device", "battery", "--json"])
    out = _json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["ok"] is True
    assert out["data"]["percentage"] == 42
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli.py -v -k "device_battery or tts_speak"`
Expected: FAIL (subparsers not yet added).

- [ ] **Step 3: Write minimal implementation**

In `cli.py`, add `device` and `tts` subcommand groups:

```python
def _cmd_device_battery(args):
    cfg = config.load()
    backend, session, conn = build_runtime(cfg)
    p = backend.for_capability("device_battery")
    if p is None:
        env = results.err(
            errors.CapabilityUnavailableError("device_battery not available"),
            capability="device.battery",
            user_action="Install Termux:API and run 'phonectl setup termux-api'.",
        )
        if getattr(args, "json", False):
            print(json.dumps(env, indent=2))
        else:
            print(f"phonectl: {env['error']['message']}")
        return 1
    data = p.battery_status()
    env = results.ok(capability="device.battery",
                     provider=type(p).__name__,
                     data=data)
    if getattr(args, "json", False):
        print(json.dumps(env, indent=2))
    else:
        print(f"Battery: {data.get('percentage')}% ({data.get('status')})")
    return 0


def _cmd_tts_speak(args):
    cfg = config.load()
    backend, session, conn = build_runtime(cfg)
    p = backend.for_capability("tts_speak")
    if p is None:
        env = results.err(
            errors.CapabilityUnavailableError("tts_speak not available"),
            capability="tts.speak",
            user_action="Install Termux:API and run 'phonectl setup termux-api'.",
        )
        if getattr(args, "json", False):
            print(json.dumps(env, indent=2))
        else:
            print(f"phonectl: {env['error']['message']}")
        return 1
    p.tts_speak(args.text,
                language=getattr(args, "language", None),
                rate=getattr(args, "rate", None))
    env = results.ok(capability="tts.speak", provider=type(p).__name__)
    if getattr(args, "json", False):
        print(json.dumps(env, indent=2))
    return 0
```

Register in `build_parser()`: `device` with `battery` and `wifi` subcommands; `tts` with `speak`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli.py -v`
Expected: PASS (new tests + all existing).

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/cli.py tests/test_cli.py
git commit -m "feat: CLI phonectl device battery|wifi and phonectl tts speak via TermuxApiProvider"
```

---

### Task 7: Docs

**Files:**
- Modify: `README.md` (Termux:API section)
- Modify: `docs/superpowers/specs/2026-06-20-phonectl-adb-bridge-design.md`

- [ ] **Step 1: Update docs**

In `README.md`, add a **Termux:API provider (optional)** section:
- How it is discovered (`termux-battery-status` on PATH).
- How to install (`pkg install termux-api` + enable Termux:API app permissions on Android).
- What it enables: `clipboard read` (ADB-only cannot read clipboard), `phonectl device battery`,
  `phonectl device wifi`, `phonectl tts speak TEXT`.
- Note that the provider is automatically preferred over ADB for clipboard operations once installed.
- Add a row to the capability table showing Termux:API's values.

In `docs/setup-walkthrough.md` (shipped in Plan 1.4), add a Termux:API setup module description
explaining the `phonectl setup termux-api` flow.

Run full suite before committing to confirm all 83+ tests pass:

```bash
pytest -v
```

- [ ] **Step 2: Commit**

```bash
git add README.md docs/superpowers/specs/2026-06-20-phonectl-adb-bridge-design.md
git commit -m "docs: Termux:API provider — installation, capabilities unlocked, clipboard upgrade"
```

---

## Dependencies

**Requires:** 1.1 (errors/results/capabilities), 3.1 (ProviderRegistry — `for_capability`,
`__getattr__` delegation), 3.2 (new capability keys `read_clipboard`/`write_clipboard`/`packages_*`
etc.; `ClipboardProvider.read()` auto-upgrades when Termux:API joins the registry).
**Enables:** Phase 4.2 (NotificationListenerService) will extend `TermuxApiProvider` with
`termux-notification-list` coverage as an intermediate step before the full companion APK.
Phase 5 (daemon) will manage `TermuxApiProvider` lifecycle as a persistent connected provider.

## Deferred / out of scope

- **Termux:API notifications** (`termux-notification`, `termux-notification-list`) — deferred to
  Phase 4.2 where notification semantics are defined formally alongside `NotificationListenerService`.
- **Termux:API sensors** (`termux-sensor`) — deferred; sensor data is not needed by current use
  cases. A `device_sensors` capability key and corresponding CLI/MCP verb can be added in Phase 7.
- **Termux:API contacts/SMS** — privacy-sensitive; requires careful permission UX beyond this plan.
- **Termux:API camera/media capture** — high-risk; deferred to Phase 7.
- **`termux-notification-send` as a TTS fallback** — Termux notification is a separate capability;
  TTS is strictly `termux-tts-speak`.

## Notes on testability

`TermuxApiProvider` is tested purely with injectable `runner` and `which`. Tests never call real
`termux-*` programs. The `which` function controls whether the provider reports itself as available.
The `runner` function returns preset stdout strings for JSON-parsing tests. CLI tests patch
`_make_termux_provider` to inject a fake provider (or `None`) so no Termux installation is needed
on the test machine. The `ClipboardProvider` upgrade path (read works once Termux:API is in the
registry) is covered by the registry tests in Plan 3.1 and the ClipboardProvider tests in Plan 3.2
— those pass a registry containing a fake provider with `read_clipboard=True`.
