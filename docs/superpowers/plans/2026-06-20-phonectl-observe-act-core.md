# phonectl Observe→Act Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Python CLI (`phonectl`) that lets an agent observe the host Android phone as structured JSON and act on it (tap/type/swipe/key/launch), over ADB with no root.

**Architecture:** All adb knowledge is confined to `adb_backend` (subprocess wrappers). A pure `ui_parser` turns `uiautomator` XML into indexed elements. `observer` composes backend + parser into an `observe()` snapshot with a screen-hash; `actuator` implements `act()` verbs, resolving element indices to coordinates via `session`, and re-observes after each action. `cli` wires verbs to argparse with an audit log, mode flag, and kill-switch.

**Tech Stack:** Python 3 (stdlib only for core: `subprocess`, `xml.etree`, `hashlib`, `json`, `argparse`), `pytest` for tests, `adb` (android-tools) as the only external runtime dependency.

## Global Constraints

- Python 3.9+ (Termux/PRoot default); **no third-party runtime deps** — stdlib only. `pytest` is dev-only.
- **No root.** Nothing in this plan may assume root. ADB runs as the `shell` user.
- Backend is reached over **Wireless Debugging (Android 11+)** at `127.0.0.1:<port>` on loopback.
- The agent targets elements by **stable index `i`**; raw `(x,y)` is an escape hatch only.
- Action modes: `auto` (default for dev), `confirm`, `dry-run`. Default is `auto` here; the released build overrides to `confirm` via config.
- `adb` is invoked as `adb -s <serial> …` once a device serial is configured; the injectable `runner` (defaults to `subprocess.run`) makes every backend call testable without a device.
- Frequent commits: one per task minimum.

## File Structure

```
phonectl/
├── pyproject.toml                      # packaging + console entry + pytest config
├── src/phonectl/
│   ├── __init__.py                     # version
│   ├── ui_parser.py                    # PURE: XML → elements, screen_hash, parse_bounds
│   ├── adb_backend.py                  # AdbBackend: subprocess adb wrappers
│   ├── session.py                      # Session: last snapshot + index→coord resolution
│   ├── observer.py                     # observe(); parse_focused_app()
│   ├── actuator.py                     # tap/type_text/swipe/key/launch/wait_for
│   ├── connection.py                   # pair/connect/ensure + config-backed serial
│   ├── config.py                       # load/save config, get_mode
│   ├── audit.py                        # JSONL action log + kill-switch sentinel
│   └── cli.py                          # argparse entry point
└── tests/
    ├── fixtures/settings_dump.xml      # captured uiautomator hierarchy
    ├── test_ui_parser.py
    ├── test_adb_backend.py
    ├── test_observer.py
    ├── test_actuator.py
    ├── test_connection.py
    └── test_cli.py
```

---

### Task 1: Project skeleton & installable CLI shell

**Files:**
- Create: `pyproject.toml`, `src/phonectl/__init__.py`, `src/phonectl/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Produces: `phonectl.__version__: str`; `phonectl.cli.main(argv: list[str] | None = None) -> int`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli.py
from phonectl import cli

def test_version_flag_prints_and_exits_zero(capsys):
    rc = cli.main(["--version"])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.strip()  # non-empty version string
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli.py -v`
Expected: FAIL (ModuleNotFoundError: phonectl)

- [ ] **Step 3: Write minimal implementation**

```toml
# pyproject.toml
[build-system]
requires = ["setuptools>=61"]
build-backend = "setuptools.build_meta"

[project]
name = "phonectl"
version = "0.1.0"
description = "Android computer-use bridge over ADB (no root)"
requires-python = ">=3.9"

[project.scripts]
phonectl = "phonectl.cli:main"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
```

```python
# src/phonectl/__init__.py
__version__ = "0.1.0"
```

```python
# src/phonectl/cli.py
import argparse
from phonectl import __version__

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="phonectl")
    p.add_argument("--version", action="version", version=__version__)
    p.set_defaults(func=None)
    return p

def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "func", None) is None:
        parser.print_help()
        return 0
    return args.func(args)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli.py -v`
Expected: PASS

Note: `argparse` `--version` raises `SystemExit(0)`. Adjust the test if needed:

```python
def test_version_flag_prints_and_exits_zero(capsys):
    import pytest
    with pytest.raises(SystemExit) as e:
        cli.main(["--version"])
    assert e.value.code == 0
    assert capsys.readouterr().out.strip()
```

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml src/phonectl/__init__.py src/phonectl/cli.py tests/test_cli.py
git commit -m "feat: project skeleton and installable phonectl CLI shell"
```

---

### Task 2: `ui_parser` — pure XML → indexed elements

**Files:**
- Create: `src/phonectl/ui_parser.py`, `tests/fixtures/settings_dump.xml`
- Test: `tests/test_ui_parser.py`

**Interfaces:**
- Produces:
  - `parse_bounds(s: str) -> tuple[int,int,int,int]` — `"[44,380][1036,520]"` → `(44,380,1036,520)`
  - `parse_elements(xml: str) -> list[dict]` — each dict: `{"i":int,"text":str,"id":str,"class":str,"content_desc":str,"clickable":bool,"bounds":[x1,y1,x2,y2],"center":[cx,cy]}`. Includes only nodes with non-empty text OR content-desc OR `clickable="true"`. `i` is sequential from 0 in document order.
  - `screen_hash(elements: list[dict]) -> str` — stable sha1 over each element's `(text,id,bounds)`.

- [ ] **Step 1: Write the fixture**

```xml
<!-- tests/fixtures/settings_dump.xml -->
<?xml version='1.0' encoding='UTF-8'?>
<hierarchy rotation="0">
  <node index="0" text="" resource-id="" class="android.widget.FrameLayout" content-desc="" clickable="false" bounds="[0,0][1080,2400]">
    <node index="0" text="Settings" resource-id="com.android.settings:id/title" class="android.widget.TextView" content-desc="" clickable="false" bounds="[44,120][400,200]"/>
    <node index="1" text="Wi-Fi" resource-id="android:id/title" class="android.widget.TextView" content-desc="" clickable="true" bounds="[44,380][1036,520]"/>
    <node index="2" text="Bluetooth" resource-id="android:id/title" class="android.widget.TextView" content-desc="" clickable="true" bounds="[44,540][1036,680]"/>
    <node index="3" text="" resource-id="" class="android.widget.ImageView" content-desc="Search" clickable="true" bounds="[960,120][1040,200]"/>
  </node>
</hierarchy>
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_ui_parser.py
from pathlib import Path
from phonectl import ui_parser

FIXTURE = (Path(__file__).parent / "fixtures" / "settings_dump.xml").read_text()

def test_parse_bounds():
    assert ui_parser.parse_bounds("[44,380][1036,520]") == (44, 380, 1036, 520)

def test_parse_elements_filters_and_indexes():
    els = ui_parser.parse_elements(FIXTURE)
    # FrameLayout (no text/desc, not clickable) excluded; 4 meaningful nodes kept
    assert [e["text"] for e in els] == ["Settings", "Wi-Fi", "Bluetooth", ""]
    assert [e["i"] for e in els] == [0, 1, 2, 3]

def test_parse_elements_center_and_flags():
    els = ui_parser.parse_elements(FIXTURE)
    wifi = els[1]
    assert wifi["bounds"] == [44, 380, 1036, 520]
    assert wifi["center"] == [540, 450]
    assert wifi["clickable"] is True
    search = els[3]
    assert search["content_desc"] == "Search"
    assert search["clickable"] is True

def test_screen_hash_stable_and_sensitive():
    els = ui_parser.parse_elements(FIXTURE)
    h1 = ui_parser.screen_hash(els)
    assert h1 == ui_parser.screen_hash(ui_parser.parse_elements(FIXTURE))
    changed = [dict(e) for e in els]
    changed[1] = {**changed[1], "text": "Wi-Fi (off)"}
    assert ui_parser.screen_hash(changed) != h1

def test_parse_elements_tolerates_device_trailing_line():
    # `uiautomator dump /dev/tty` appends a status line after </hierarchy>
    noisy = FIXTURE + "\nUI hierchary dumped to: /dev/tty\n"
    els = ui_parser.parse_elements(noisy)
    assert [e["text"] for e in els] == ["Settings", "Wi-Fi", "Bluetooth", ""]
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_ui_parser.py -v`
Expected: FAIL (module/function not defined)

- [ ] **Step 4: Write minimal implementation**

```python
# src/phonectl/ui_parser.py
import hashlib
import xml.etree.ElementTree as ET

def parse_bounds(s: str) -> tuple[int, int, int, int]:
    # "[44,380][1036,520]" -> (44, 380, 1036, 520)
    nums = s.replace("[", " ").replace("]", " ").replace(",", " ").split()
    x1, y1, x2, y2 = (int(n) for n in nums)
    return (x1, y1, x2, y2)

def _is_meaningful(text: str, desc: str, clickable: bool) -> bool:
    return bool(text) or bool(desc) or clickable

def _extract_hierarchy(xml: str) -> str:
    # Devices append a trailing status line after </hierarchy>; slice to the root element.
    start = xml.find("<hierarchy")
    end = xml.rfind("</hierarchy>")
    if start != -1 and end != -1:
        return xml[start:end + len("</hierarchy>")]
    return xml

def parse_elements(xml: str) -> list[dict]:
    root = ET.fromstring(_extract_hierarchy(xml))
    elements: list[dict] = []
    i = 0
    for node in root.iter("node"):
        text = node.get("text", "") or ""
        desc = node.get("content-desc", "") or ""
        clickable = node.get("clickable", "false") == "true"
        if not _is_meaningful(text, desc, clickable):
            continue
        x1, y1, x2, y2 = parse_bounds(node.get("bounds", "[0,0][0,0]"))
        elements.append({
            "i": i,
            "text": text,
            "id": node.get("resource-id", "") or "",
            "class": node.get("class", "") or "",
            "content_desc": desc,
            "clickable": clickable,
            "bounds": [x1, y1, x2, y2],
            "center": [(x1 + x2) // 2, (y1 + y2) // 2],
        })
        i += 1
    return elements

def screen_hash(elements: list[dict]) -> str:
    h = hashlib.sha1()
    for e in elements:
        h.update(f"{e['text']}|{e['id']}|{e['bounds']}".encode())
    return h.hexdigest()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_ui_parser.py -v`
Expected: PASS (5 tests)

- [ ] **Step 6: Commit**

```bash
git add src/phonectl/ui_parser.py tests/test_ui_parser.py tests/fixtures/settings_dump.xml
git commit -m "feat: pure ui_parser for uiautomator XML to indexed elements"
```

---

### Task 3: `adb_backend` — testable subprocess wrappers

**Files:**
- Create: `src/phonectl/adb_backend.py`
- Test: `tests/test_adb_backend.py`

**Interfaces:**
- Produces: class `AdbBackend`
  - `__init__(self, serial: str | None = None, runner=subprocess.run)`
  - `_adb(self, *args: str) -> str` — runs `adb [-s serial] *args`, returns stdout (text)
  - `ui_dump(self) -> str` — returns uiautomator XML (`exec-out uiautomator dump /dev/tty`)
  - `screencap(self, path: str) -> str` — writes PNG bytes to `path`, returns `path`
  - `window_dump(self) -> str` — `shell dumpsys window windows`
  - `wm_size(self) -> tuple[int,int]` — parses `shell wm size` → `(w,h)`
  - `input_tap(self, x: int, y: int) -> None`
  - `input_text(self, text: str) -> None`
  - `input_swipe(self, x1,y1,x2,y2, ms: int = 200) -> None`
  - `input_key(self, keycode: str) -> None`
  - `launch(self, package: str) -> None` — monkey launcher intent
  - `get_state(self) -> str` — `adb get-state` (returns "device" when ready)

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_adb_backend.py
from phonectl.adb_backend import AdbBackend

class FakeCompleted:
    def __init__(self, stdout="", stdout_bytes=b"", returncode=0):
        self.stdout = stdout
        self._bytes = stdout_bytes
        self.returncode = returncode

def make_runner(record, stdout="", stdout_bytes=b""):
    def runner(cmd, **kwargs):
        record.append((cmd, kwargs))
        if kwargs.get("capture_output") and not kwargs.get("text", False):
            return FakeCompleted(stdout_bytes=stdout_bytes)
        return FakeCompleted(stdout=stdout)
    return runner

def test_adb_prepends_serial():
    calls = []
    b = AdbBackend(serial="127.0.0.1:5555", runner=make_runner(calls, stdout="ok"))
    out = b._adb("shell", "echo", "ok")
    assert out == "ok"
    assert calls[0][0] == ["adb", "-s", "127.0.0.1:5555", "shell", "echo", "ok"]

def test_wm_size_parses():
    calls = []
    b = AdbBackend(serial="d", runner=make_runner(calls, stdout="Physical size: 1080x2400\n"))
    assert b.wm_size() == (1080, 2400)

def test_input_tap_builds_command():
    calls = []
    b = AdbBackend(serial="d", runner=make_runner(calls))
    b.input_tap(540, 450)
    assert calls[0][0] == ["adb", "-s", "d", "shell", "input", "tap", "540", "450"]

def test_launch_uses_monkey():
    calls = []
    b = AdbBackend(serial="d", runner=make_runner(calls))
    b.launch("com.android.settings")
    cmd = calls[0][0]
    assert "monkey" in cmd and "com.android.settings" in cmd
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_adb_backend.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/adb_backend.py
import subprocess

class AdbBackend:
    def __init__(self, serial=None, runner=subprocess.run):
        self.serial = serial
        self._runner = runner

    def _base(self) -> list[str]:
        return ["adb", "-s", self.serial] if self.serial else ["adb"]

    def _adb(self, *args: str) -> str:
        cmd = self._base() + list(args)
        res = self._runner(cmd, capture_output=True, text=True)
        return res.stdout

    def _adb_bytes(self, *args: str) -> bytes:
        cmd = self._base() + list(args)
        res = self._runner(cmd, capture_output=True)
        return res._bytes if hasattr(res, "_bytes") else res.stdout

    def ui_dump(self) -> str:
        return self._adb("exec-out", "uiautomator", "dump", "/dev/tty")

    def screencap(self, path: str) -> str:
        data = self._adb_bytes("exec-out", "screencap", "-p")
        with open(path, "wb") as f:
            f.write(data)
        return path

    def window_dump(self) -> str:
        return self._adb("shell", "dumpsys", "window", "windows")

    def wm_size(self) -> tuple[int, int]:
        out = self._adb("shell", "wm", "size")
        # "Physical size: 1080x2400"
        wh = out.strip().split(":")[-1].strip()
        w, h = wh.split("x")
        return (int(w), int(h))

    def input_tap(self, x: int, y: int) -> None:
        self._adb("shell", "input", "tap", str(x), str(y))

    def input_text(self, text: str) -> None:
        self._adb("shell", "input", "text", text.replace(" ", "%s"))

    def input_swipe(self, x1, y1, x2, y2, ms: int = 200) -> None:
        self._adb("shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(ms))

    def input_key(self, keycode: str) -> None:
        self._adb("shell", "input", "keyevent", keycode)

    def launch(self, package: str) -> None:
        self._adb("shell", "monkey", "-p", package,
                  "-c", "android.intent.category.LAUNCHER", "1")

    def get_state(self) -> str:
        return self._adb("get-state").strip()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_adb_backend.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/adb_backend.py tests/test_adb_backend.py
git commit -m "feat: AdbBackend subprocess wrappers with injectable runner"
```

---

### Task 4: `session` + `observer` — produce `observe()` snapshots

**Files:**
- Create: `src/phonectl/session.py`, `src/phonectl/observer.py`
- Test: `tests/test_observer.py`

**Interfaces:**
- Produces:
  - class `Session`: `__init__(self)`; attr `last: dict | None`; `set_snapshot(self, snap: dict) -> None`; `resolve(self, i: int) -> tuple[int,int]` (returns the element's `center`, raises `KeyError` if unknown index or no snapshot)
  - `parse_focused_app(window_dump: str) -> dict` — `{"package": str, "activity": str}` from `dumpsys window` (`mCurrentFocus`/`mFocusedApp` line `pkg/.Activity`)
  - `observe(backend, session, screenshot: bool = False, snap_path: str | None = None) -> dict` — returns the snapshot in the spec's shape and calls `session.set_snapshot`.
- Consumes: `AdbBackend` (Task 3), `ui_parser` (Task 2).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_observer.py
from phonectl.session import Session
from phonectl import observer

class CannedBackend:
    def __init__(self, xml, window, size=(1080, 2400)):
        self._xml, self._window, self._size = xml, window, size
    def ui_dump(self): return self._xml
    def window_dump(self): return self._window
    def wm_size(self): return self._size
    def screencap(self, path): return path

XML = """<?xml version='1.0'?><hierarchy rotation="0">
<node index="0" text="Wi-Fi" resource-id="android:id/title" class="TextView"
 content-desc="" clickable="true" bounds="[44,380][1036,520]"/></hierarchy>"""

WINDOW = "  mCurrentFocus=Window{a b com.android.settings/com.android.settings.Settings}"

def test_parse_focused_app():
    app = observer.parse_focused_app(WINDOW)
    assert app == {"package": "com.android.settings", "activity": "com.android.settings.Settings"}

def test_observe_builds_snapshot_and_updates_session():
    s = Session()
    snap = observer.observe(CannedBackend(XML, WINDOW), s)
    assert snap["app"]["package"] == "com.android.settings"
    assert snap["screen"] == {"w": 1080, "h": 2400, "orientation": "portrait"}
    assert snap["elements"][0]["text"] == "Wi-Fi"
    assert "hash" in snap and snap["hash"]
    assert s.last is snap

def test_session_resolve_returns_center():
    s = Session()
    observer.observe(CannedBackend(XML, WINDOW), s)
    assert s.resolve(0) == (540, 450)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_observer.py -v`
Expected: FAIL (modules not found)

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/session.py
class Session:
    def __init__(self):
        self.last = None

    def set_snapshot(self, snap: dict) -> None:
        self.last = snap

    def resolve(self, i: int) -> tuple[int, int]:
        if not self.last:
            raise KeyError("no snapshot; call observe() first")
        for e in self.last["elements"]:
            if e["i"] == i:
                return (e["center"][0], e["center"][1])
        raise KeyError(f"no element with index {i}")
```

```python
# src/phonectl/observer.py
import re
from phonectl import ui_parser

_FOCUS_RE = re.compile(r"([A-Za-z0-9_.]+)/([A-Za-z0-9_.]+)")

def parse_focused_app(window_dump: str) -> dict:
    for line in window_dump.splitlines():
        if "mCurrentFocus" in line or "mFocusedApp" in line:
            m = _FOCUS_RE.search(line)
            if m:
                return {"package": m.group(1), "activity": m.group(2)}
    return {"package": "", "activity": ""}

def observe(backend, session, screenshot: bool = False, snap_path: str | None = None) -> dict:
    xml = backend.ui_dump()
    elements = ui_parser.parse_elements(xml)
    w, h = backend.wm_size()
    app = parse_focused_app(backend.window_dump())
    snap = {
        "app": app,
        "screen": {"w": w, "h": h, "orientation": "portrait" if h >= w else "landscape"},
        "hash": ui_parser.screen_hash(elements),
        "elements": elements,
        "screenshot": None,
    }
    if screenshot and snap_path:
        snap["screenshot"] = backend.screencap(snap_path)
    session.set_snapshot(snap)
    return snap
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_observer.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/session.py src/phonectl/observer.py tests/test_observer.py
git commit -m "feat: Session and observe() snapshot composition"
```

---

### Task 5: `actuator` — act() verbs with re-observe

**Files:**
- Create: `src/phonectl/actuator.py`
- Test: `tests/test_actuator.py`

**Interfaces:**
- Produces (each returns the post-action snapshot via `observer.observe`):
  - `tap(backend, session, i: int | None = None, x: int | None = None, y: int | None = None) -> dict`
  - `type_text(backend, session, text: str) -> dict`
  - `swipe(backend, session, x1, y1, x2, y2, ms: int = 200) -> dict`
  - `key(backend, session, keycode: str) -> dict` — accepts friendly names via `KEYMAP` (`back`,`home`,`recents`,`enter`)
  - `launch(backend, session, package: str) -> dict`
  - `wait_for(backend, session, text: str | None = None, id: str | None = None, timeout: float = 5.0, interval: float = 0.5, sleep=time.sleep) -> dict | None` — re-observe until a matching element appears; returns snapshot or `None` on timeout
- Consumes: `AdbBackend` (Task 3), `Session`/`observe` (Task 4).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_actuator.py
from phonectl.session import Session
from phonectl import actuator

XML_A = """<?xml version='1.0'?><hierarchy rotation="0">
<node index="0" text="Wi-Fi" resource-id="android:id/title" class="TextView"
 content-desc="" clickable="true" bounds="[44,380][1036,520]"/></hierarchy>"""
XML_B = """<?xml version='1.0'?><hierarchy rotation="0">
<node index="0" text="Bluetooth" resource-id="android:id/title" class="TextView"
 content-desc="" clickable="true" bounds="[44,540][1036,680]"/></hierarchy>"""
WINDOW = "mCurrentFocus=Window{a b com.android.settings/.Settings}"

class ScriptBackend:
    """Returns XML_A first, then XML_B on subsequent dumps; records actions."""
    def __init__(self):
        self.calls = []
        self._dumps = [XML_A, XML_B, XML_B, XML_B]
    def ui_dump(self):
        return self._dumps.pop(0) if len(self._dumps) > 1 else self._dumps[0]
    def window_dump(self): return WINDOW
    def wm_size(self): return (1080, 2400)
    def input_tap(self, x, y): self.calls.append(("tap", x, y))
    def input_text(self, t): self.calls.append(("text", t))
    def input_key(self, k): self.calls.append(("key", k))

def test_tap_by_index_resolves_center_then_reobserves():
    s = Session()
    from phonectl import observer
    b = ScriptBackend()
    observer.observe(b, s)                 # seed snapshot (XML_A)
    snap = actuator.tap(b, s, i=0)
    assert ("tap", 540, 450) in b.calls
    assert snap["elements"][0]["text"] == "Bluetooth"   # re-observed XML_B

def test_tap_by_xy_does_not_require_snapshot():
    s = Session()
    b = ScriptBackend()
    actuator.tap(b, s, x=100, y=200)
    assert ("tap", 100, 200) in b.calls

def test_key_maps_friendly_names():
    s = Session()
    b = ScriptBackend()
    actuator.key(b, s, "back")
    assert ("key", "KEYCODE_BACK") in b.calls

def test_wait_for_finds_text_after_polling():
    s = Session()
    b = ScriptBackend()
    calls = []
    snap = actuator.wait_for(b, s, text="Bluetooth", timeout=2, interval=0,
                             sleep=lambda *_: calls.append(1))
    assert snap is not None
    assert any(e["text"] == "Bluetooth" for e in snap["elements"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_actuator.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/actuator.py
import time
from phonectl import observer

KEYMAP = {
    "back": "KEYCODE_BACK",
    "home": "KEYCODE_HOME",
    "recents": "KEYCODE_APP_SWITCH",
    "enter": "KEYCODE_ENTER",
}

def tap(backend, session, i=None, x=None, y=None) -> dict:
    if i is not None:
        x, y = session.resolve(i)
    if x is None or y is None:
        raise ValueError("tap requires either i or both x and y")
    backend.input_tap(x, y)
    return observer.observe(backend, session)

def type_text(backend, session, text: str) -> dict:
    backend.input_text(text)
    return observer.observe(backend, session)

def swipe(backend, session, x1, y1, x2, y2, ms: int = 200) -> dict:
    backend.input_swipe(x1, y1, x2, y2, ms)
    return observer.observe(backend, session)

def key(backend, session, keycode: str) -> dict:
    backend.input_key(KEYMAP.get(keycode, keycode))
    return observer.observe(backend, session)

def launch(backend, session, package: str) -> dict:
    backend.launch(package)
    return observer.observe(backend, session)

def _matches(el, text, id):
    if text is not None and el["text"] == text:
        return True
    if id is not None and el["id"] == id:
        return True
    return False

def wait_for(backend, session, text=None, id=None, timeout: float = 5.0,
             interval: float = 0.5, sleep=time.sleep):
    deadline = timeout
    while True:
        snap = observer.observe(backend, session)
        if any(_matches(e, text, id) for e in snap["elements"]):
            return snap
        deadline -= max(interval, 0.0001)
        if deadline <= 0:
            return None
        sleep(interval)
```

Note: `swipe` calls `backend.input_swipe`; `ScriptBackend` in the test doesn't exercise it, so no recording needed there. If you add a swipe test, record it like the others.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_actuator.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/actuator.py tests/test_actuator.py
git commit -m "feat: actuator act() verbs with observe-after-act loop"
```

---

### Task 6: `config` + `audit` — mode, persisted serial, action log, kill-switch

**Files:**
- Create: `src/phonectl/config.py`, `src/phonectl/audit.py`
- Test: extend `tests/test_cli.py` (add config/audit tests in a new `tests/test_config_audit.py`)

**Interfaces:**
- Produces:
  - `config.config_dir() -> Path` (honors `PHONECTL_HOME` env override; default `~/.config/phonectl`)
  - `config.load() -> dict` (returns `{}` if absent); `config.save(cfg: dict) -> None`
  - `config.get_mode(cfg: dict) -> str` (returns `cfg.get("mode","auto")`)
  - `audit.log_action(verb: str, target: dict, result: dict) -> None` (appends one JSON line to `config_dir()/actions.jsonl` with `ts`, `verb`, `target`, `app`, `hash`)
  - `audit.kill_switch_active() -> bool` (True if `config_dir()/STOP` exists)

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_config_audit.py
import json
from phonectl import config, audit

def test_config_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    assert config.load() == {}
    config.save({"serial": "127.0.0.1:5555", "mode": "confirm"})
    cfg = config.load()
    assert cfg["serial"] == "127.0.0.1:5555"
    assert config.get_mode(cfg) == "confirm"
    assert config.get_mode({}) == "auto"

def test_kill_switch(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    assert audit.kill_switch_active() is False
    (tmp_path / "STOP").write_text("")
    assert audit.kill_switch_active() is True

def test_log_action_appends_jsonl(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    audit.log_action("tap", {"i": 7}, {"app": {"package": "com.x"}, "hash": "abc"})
    lines = (tmp_path / "actions.jsonl").read_text().strip().splitlines()
    rec = json.loads(lines[0])
    assert rec["verb"] == "tap" and rec["target"] == {"i": 7}
    assert rec["app"] == "com.x" and rec["hash"] == "abc"
    assert "ts" in rec
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_config_audit.py -v`
Expected: FAIL (modules not found)

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/config.py
import json
import os
from pathlib import Path

def config_dir() -> Path:
    base = os.environ.get("PHONECTL_HOME")
    d = Path(base) if base else Path.home() / ".config" / "phonectl"
    d.mkdir(parents=True, exist_ok=True)
    return d

def _path() -> Path:
    return config_dir() / "config.json"

def load() -> dict:
    p = _path()
    if not p.exists():
        return {}
    return json.loads(p.read_text())

def save(cfg: dict) -> None:
    _path().write_text(json.dumps(cfg, indent=2))

def get_mode(cfg: dict) -> str:
    return cfg.get("mode", "auto")
```

```python
# src/phonectl/audit.py
import json
import time
from phonectl.config import config_dir

def kill_switch_active() -> bool:
    return (config_dir() / "STOP").exists()

def log_action(verb: str, target: dict, result: dict) -> None:
    rec = {
        "ts": time.time(),
        "verb": verb,
        "target": target,
        "app": (result.get("app", {}) or {}).get("package", ""),
        "hash": result.get("hash", ""),
    }
    with open(config_dir() / "actions.jsonl", "a") as f:
        f.write(json.dumps(rec) + "\n")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_config_audit.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/config.py src/phonectl/audit.py tests/test_config_audit.py
git commit -m "feat: config (mode/serial) and audit log + kill-switch"
```

---

### Task 7: `connection` — pair/connect/ensure with config-backed serial

**Files:**
- Create: `src/phonectl/connection.py`
- Test: `tests/test_connection.py`

**Interfaces:**
- Produces: class `Connection`
  - `__init__(self, backend, cfg: dict)`
  - `pair(self, addr: str, code: str) -> None` — runs `adb pair addr code` via `backend._adb`
  - `connect(self, addr: str) -> None` — runs `adb connect addr`, sets `backend.serial = addr`, persists serial to cfg via `config.save`
  - `ensure(self) -> None` — if `backend.get_state() != "device"`, attempt `connect(cfg["serial"])`; if still not "device" (or no serial), raise `ConnectionError` with the exact re-enable guidance string
- Consumes: `AdbBackend` (Task 3), `config` (Task 6)

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_connection.py
import pytest
from phonectl.connection import Connection, GUIDANCE

class StateBackend:
    def __init__(self, states):
        self.serial = None
        self._states = list(states)
        self.adb_calls = []
    def get_state(self):
        return self._states.pop(0) if len(self._states) > 1 else self._states[0]
    def _adb(self, *args):
        self.adb_calls.append(args)
        return ""

def test_ensure_noop_when_already_device(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    b = StateBackend(["device"])
    Connection(b, {"serial": "127.0.0.1:5555"}).ensure()
    assert b.adb_calls == []  # no reconnect attempted

def test_ensure_reconnects_when_offline(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    b = StateBackend(["offline", "device"])
    Connection(b, {"serial": "127.0.0.1:5555"}).ensure()
    assert ("connect", "127.0.0.1:5555") in b.adb_calls
    assert b.serial == "127.0.0.1:5555"

def test_ensure_raises_guidance_when_no_serial(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    b = StateBackend(["offline", "offline"])
    with pytest.raises(ConnectionError) as e:
        Connection(b, {}).ensure()
    assert GUIDANCE in str(e.value)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_connection.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/connection.py
from phonectl import config

GUIDANCE = (
    "Cannot reach the device. Enable Settings > Developer options > "
    "Wireless debugging, then run: phonectl setup"
)

class Connection:
    def __init__(self, backend, cfg: dict):
        self.backend = backend
        self.cfg = cfg

    def pair(self, addr: str, code: str) -> None:
        self.backend._adb("pair", addr, code)

    def connect(self, addr: str) -> None:
        self.backend._adb("connect", addr)
        self.backend.serial = addr
        self.cfg["serial"] = addr
        config.save(self.cfg)

    def ensure(self) -> None:
        if self.backend.get_state() == "device":
            return
        serial = self.cfg.get("serial")
        if serial:
            self.connect(serial)
            if self.backend.get_state() == "device":
                return
        raise ConnectionError(GUIDANCE)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_connection.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/connection.py tests/test_connection.py
git commit -m "feat: Connection pair/connect/ensure with guidance on failure"
```

---

### Task 8: `cli` wiring — verbs, mode gating, audit, kill-switch

**Files:**
- Modify: `src/phonectl/cli.py`
- Test: `tests/test_cli.py` (extend)

**Interfaces:**
- Consumes: all prior modules.
- Produces: CLI subcommands `observe`, `tap`, `type`, `swipe`, `key`, `launch`, `wait-for`, `doctor`. A factory `build_runtime(cfg, backend=None) -> tuple[backend, session, connection]` so tests can inject a fake backend. Action verbs honor mode (`auto` acts; `dry-run` logs only; `confirm` requires `--yes`) and abort if `audit.kill_switch_active()`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cli.py  (append below existing test)
import json
from phonectl import cli

class FakeBackend:
    def __init__(self):
        self.calls = []
        self.serial = "d"
        self._xml = ("""<?xml version='1.0'?><hierarchy rotation="0">"""
            """<node index="0" text="Wi-Fi" resource-id="android:id/title" class="T" """
            """content-desc="" clickable="true" bounds="[44,380][1036,520]"/></hierarchy>""")
    def get_state(self): return "device"
    def ui_dump(self): return self._xml
    def window_dump(self): return "mCurrentFocus=Window{a b com.x/.A}"
    def wm_size(self): return (1080, 2400)
    def input_tap(self, x, y): self.calls.append(("tap", x, y))

def test_observe_prints_json(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: FakeBackend())
    rc = cli.main(["observe"])
    out = capsys.readouterr().out
    data = json.loads(out)
    assert rc == 0
    assert data["elements"][0]["text"] == "Wi-Fi"

def test_tap_auto_mode_acts_and_logs(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    fb = FakeBackend()
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: fb)
    rc = cli.main(["tap", "--xy", "100", "200"])
    assert rc == 0
    assert ("tap", 100, 200) in fb.calls
    log = (tmp_path / "actions.jsonl").read_text()
    assert "tap" in log

def test_tap_blocked_by_kill_switch(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    (tmp_path / "STOP").write_text("")
    fb = FakeBackend()
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: fb)
    rc = cli.main(["tap", "--xy", "100", "200"])
    assert rc == 2
    assert fb.calls == []  # action refused
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cli.py -v`
Expected: FAIL (no `observe`/`tap` subcommands, `_make_backend` missing)

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/cli.py
import argparse
import json
from phonectl import __version__, config, audit, observer, actuator
from phonectl.adb_backend import AdbBackend
from phonectl.session import Session
from phonectl.connection import Connection

def _make_backend(cfg) -> AdbBackend:
    return AdbBackend(serial=cfg.get("serial"))

def build_runtime(cfg, backend=None):
    backend = backend or _make_backend(cfg)
    session = Session()
    conn = Connection(backend, cfg)
    return backend, session, conn

def _emit(snap) -> None:
    print(json.dumps(snap, indent=2))

def _guard_action(cfg) -> int | None:
    if audit.kill_switch_active():
        print("phonectl: action refused (kill switch STOP present)")
        return 2
    return None

def _cmd_observe(args):
    cfg = config.load()
    backend, session, conn = build_runtime(cfg)
    conn.ensure()
    _emit(observer.observe(backend, session, screenshot=args.screenshot,
                           snap_path=args.screenshot_path))
    return 0

def _do_action(args, verb, fn, target):
    cfg = config.load()
    blocked = _guard_action(cfg)
    if blocked is not None:
        return blocked
    mode = config.get_mode(cfg)
    if mode == "confirm" and not args.yes:
        print(f"phonectl: {verb} {target} requires --yes in confirm mode")
        return 3
    backend, session, conn = build_runtime(cfg)
    conn.ensure()
    if mode == "dry-run":
        observer.observe(backend, session)
        print(f"phonectl: dry-run {verb} {target} (not executed)")
        return 0
    snap = fn(backend, session)
    audit.log_action(verb, target, snap)
    _emit(snap)
    return 0

def _cmd_tap(args):
    if args.index is not None:
        return _do_action(args, "tap", lambda b, s: actuator.tap(b, s, i=args.index),
                          {"i": args.index})
    x, y = args.xy
    return _do_action(args, "tap", lambda b, s: actuator.tap(b, s, x=x, y=y),
                      {"x": x, "y": y})

def _cmd_type(args):
    return _do_action(args, "type", lambda b, s: actuator.type_text(b, s, args.text),
                      {"text": args.text})

def _cmd_swipe(args):
    x1, y1, x2, y2 = args.coords
    return _do_action(args, "swipe", lambda b, s: actuator.swipe(b, s, x1, y1, x2, y2),
                      {"coords": args.coords})

def _cmd_key(args):
    return _do_action(args, "key", lambda b, s: actuator.key(b, s, args.keycode),
                      {"key": args.keycode})

def _cmd_launch(args):
    return _do_action(args, "launch", lambda b, s: actuator.launch(b, s, args.package),
                      {"package": args.package})

def _cmd_wait_for(args):
    cfg = config.load()
    backend, session, conn = build_runtime(cfg)
    conn.ensure()
    snap = actuator.wait_for(backend, session, text=args.text, id=args.id,
                             timeout=args.timeout)
    if snap is None:
        print("phonectl: wait-for timed out")
        return 1
    _emit(snap)
    return 0

def _cmd_doctor(args):
    cfg = config.load()
    backend, session, conn = build_runtime(cfg)
    try:
        conn.ensure()
    except ConnectionError as e:
        print(str(e))
        return 1
    print(f"phonectl: connected (serial={backend.serial}, state={backend.get_state()})")
    return 0

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="phonectl")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd")

    o = sub.add_parser("observe")
    o.add_argument("--screenshot", action="store_true")
    o.add_argument("--screenshot-path", default=None)
    o.set_defaults(func=_cmd_observe)

    t = sub.add_parser("tap")
    g = t.add_mutually_exclusive_group(required=True)
    g.add_argument("--index", type=int)
    g.add_argument("--xy", nargs=2, type=int, metavar=("X", "Y"))
    t.add_argument("--yes", action="store_true")
    t.set_defaults(func=_cmd_tap)

    ty = sub.add_parser("type")
    ty.add_argument("text")
    ty.add_argument("--yes", action="store_true")
    ty.set_defaults(func=_cmd_type)

    sw = sub.add_parser("swipe")
    sw.add_argument("coords", nargs=4, type=int, metavar=("X1", "Y1", "X2", "Y2"))
    sw.add_argument("--yes", action="store_true")
    sw.set_defaults(func=_cmd_swipe)

    k = sub.add_parser("key")
    k.add_argument("keycode")
    k.add_argument("--yes", action="store_true")
    k.set_defaults(func=_cmd_key)

    la = sub.add_parser("launch")
    la.add_argument("package")
    la.add_argument("--yes", action="store_true")
    la.set_defaults(func=_cmd_launch)

    w = sub.add_parser("wait-for")
    w.add_argument("--text", default=None)
    w.add_argument("--id", default=None)
    w.add_argument("--timeout", type=float, default=5.0)
    w.set_defaults(func=_cmd_wait_for)

    d = sub.add_parser("doctor")
    d.set_defaults(func=_cmd_doctor)
    return p

def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "func", None) is None:
        parser.print_help()
        return 0
    return args.func(args)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cli.py -v`
Expected: PASS (original version test + 3 new)

- [ ] **Step 5: Run the full suite**

Run: `pytest -v`
Expected: PASS (all tests, all files)

- [ ] **Step 6: Commit**

```bash
git add src/phonectl/cli.py tests/test_cli.py
git commit -m "feat: CLI verbs with mode gating, audit logging, kill-switch"
```

---

### Task 9: Real-device integration smoke test (manual) + README

**Files:**
- Create: `README.md`, `docs/integration-smoke.md`

**Interfaces:** none (documentation + manual procedure).

This task is the build-step-zero connectivity proof plus a documented end-to-end run. It is manual because it requires a real paired device; do not automate it in CI.

- [ ] **Step 1: Install adb and the package**

```bash
# inside the PRoot distro
apt-get update && apt-get install -y android-tools-adb || pkg install -y android-tools
pip install -e .
```

- [ ] **Step 2: Pair and connect (build-step-zero)**

On the phone: Settings → Developer options → Wireless debugging → "Pair device with pairing code". Note the `IP:PORT` and 6-digit code, then:

```bash
adb pair 127.0.0.1:<pairPort> <code>
adb connect 127.0.0.1:<connPort>
phonectl doctor   # expect: "connected (serial=127.0.0.1:<connPort>, state=device)"
```

If `phonectl doctor` prints the guidance string instead, the topology fallback applies: install adb in **host Termux** and re-run there (see docs/integration-smoke.md).

- [ ] **Step 3: Run the observe→act smoke scenario**

```bash
phonectl launch com.android.settings
phonectl wait-for --text "Network & internet" --timeout 8
phonectl observe | python -c "import sys,json; d=json.load(sys.stdin); print(len(d['elements']),'elements,',d['app'])"
# pick the index of an element, e.g. the one whose text is "Network & internet", then:
phonectl tap --index <i>
```

Expected: each command prints a JSON snapshot; the `app`/`hash` change between the pre- and post-tap `observe`, confirming the action landed.

- [ ] **Step 4: Write the docs**

`README.md`: one-paragraph project summary, the install + pair steps above, the verb list, and the mode/kill-switch notes. `docs/integration-smoke.md`: the full manual scenario and the host-Termux fallback.

- [ ] **Step 5: Commit**

```bash
git add README.md docs/integration-smoke.md
git commit -m "docs: README and real-device integration smoke procedure"
```

---

## Deferred to follow-on plans (not in this plan)

- mDNS auto-discovery (`adb mdns services`) for silent reconnect after reboot.
- Full `phonectl setup` interactive wizard.
- Guarded-package denylist enforcement in `_do_action`.
- MCP server wrapper exposing verbs as native agent tools.
- AccessibilityService APK backend behind the same interface.
- Density-aware swipe scaling and `uiautomator` retry/settle on animated screens.
