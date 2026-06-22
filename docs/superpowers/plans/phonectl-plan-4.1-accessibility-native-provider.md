# phonectl AccessibilityService Native Provider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Plan 4.1 of the platform roadmap** (`docs/superpowers/phonectl-platform-roadmap.md`). First plan of
Phase 4 (companion APK event providers). Depends on **Plan 1.1** (`errors`/`results`/`capabilities`/
`backend.Backend` Protocol) and **Plan 3.1** (`ProviderRegistry`). Supersedes the archived
`accessibility-backend` plan (its `Backend` Protocol seam already landed in 1.1; the native provider lands
here).

**Goal:** Add an `AccessibilityProvider` that talks to a companion Android AccessibilityService and exposes
its richer, lower-latency surface (strategy §11): a **native JSON UI tree** (windows → nodes with
actions/metadata), a **UI event stream**, **gesture dispatch**, **`ACTION_SET_TEXT`**, and **semantic node
actions** (click/long-click/scroll). The provider satisfies `backend.Backend` so it slots into the
`ProviderRegistry` and **wins over ADB** for `observe_ui_tree`/`act_*` when the companion is connected,
while ADB remains the bootstrap/shell provider and the fallback when the companion is absent. This plan
ships the **Python provider seam plus a shared transport seam and an Android design spec**; the Kotlin APK
is built separately from that spec.

**Architecture:** One new transport module `src/phonectl/providers/transport.py` defines a request/response
`Transport` Protocol (`request(method, params, *, request_id, timeout) -> dict`) plus a `LoopbackTransport`
in-process fake for tests; the low-latency `SocketTransport` is added in Plan 4.3. One new provider module
`src/phonectl/providers/accessibility.py` (`AccessibilityProvider`) is constructed with an injected
`Transport`; its `capabilities()` are gated on `is_available()` (a cheap `ping` over the transport). To
keep `ui_parser.py` pure and reuse the entire existing parse path, native JSON is converted to
**uiautomator-compatible XML** by a new **pure** module `src/phonectl/native_tree.py`
(`to_compat_xml(native) -> str`); the provider's `ui_dump()` returns that XML so `observer`/`ui_parser`
work unchanged (strategy §11.2 "compatibility mode"). `observe_native()` exposes the raw native shape for
callers that want actions/window metadata. `cli.build_runtime()` prepends `AccessibilityProvider` ahead of
ADB when the companion is reachable.

**Tech Stack:** Python 3 (stdlib only: `json`, `xml.sax.saxutils`, `typing`); `pytest` for tests; no new
runtime deps. The companion APK is a **runtime prerequisite** for the provider to activate — never
imported, never hard-required; the provider degrades gracefully when the transport cannot reach it.

## Global Constraints

- **stdlib-only at runtime.** Transport and provider use stdlib only. No third-party runtime deps.
- **Backend isolation.** `AccessibilityProvider` talks to the companion **only through the injected
  `Transport`**. It never calls `adb`/`subprocess`, never imports `adb_backend`. ADB-only helpers
  (`wake`, `get_state`, shell) continue to be served by `AdbBackend` via `ProviderRegistry.__getattr__`.
- **`ui_parser.py` stays pure** and **untouched**. The native→XML conversion lives in a new pure module
  `native_tree.py` (no I/O, no subprocess); it is fixture-tested like `ui_parser`.
- **Index/selector/`(x,y)` targeting is preserved.** Because `ui_dump()` returns uiautomator-compatible
  XML, element index `i` keeps working across providers. Native `node_id` is an additional escape hatch,
  not a replacement.
- **Every actuator `act()` re-observes** — unchanged; the registry/provider is transparent to `actuator`.
- **Injectable seams.** `AccessibilityProvider(transport=...)`; `LoopbackTransport(handlers=...)`. Tests
  pass a fake transport with scripted responses. Isolate config/audit via
  `monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))`.
- **Modes + kill-switch + risk policy gate every mutating action** — unchanged; native gestures/set-text
  still flow through `runtime.run_action` at the CLI/MCP layer (no new bypass).
- **Structured-result invariant (Plan 1.1):** CLI `--json` paths and MCP tools return `results.ok/err`
  envelopes.
- **One commit per task. TDD order is non-negotiable.**

## Shared conventions established by this plan

- **`src/phonectl/providers/transport.py` is created HERE.** Plans 4.2 (notifications), 4.3
  (`SocketTransport`), and 4.4 (ML-Kit OCR path) all reuse this `Transport` seam. Every request carries a
  `request_id`, a `timeout`, and a protocol `version`; every response is `{"ok": bool, "data"/"error":
  ..., "request_id": ..., "version": ...}`. **Stale-response protection:** the provider drops any response
  whose `request_id` does not match the request it sent.
- **New capability keys** added to `CAPABILITY_KEYS`: `observe_ui_native`, `observe_ui_events`,
  `act_set_text_native`, `act_gesture_native`, `act_semantic_action`. The provider **also** advertises the
  existing keys it satisfies (`observe_ui_tree`, `act_tap`, `act_type`, `act_key`, `launch_app`) so the
  registry prefers it over ADB for those.
- **Native tree shape** (the `observe_native()` return and the companion's `observe_native` response
  `data`): `{"windows": [{"id", "type", "package", "nodes": [{"node_id", "text", "class",
  "content_desc", "actions": [...], "bounds": [l, t, r, b], "checkable", "checked", "clickable",
  "enabled", "focused", "scrollable", "password"}]}]}`.
- **`AccessibilityProvider` does not implement ADB-only methods** (`wake`, `keyguard`, `get_state` shell
  helpers). `ProviderRegistry.__getattr__` falls through to `AdbBackend` for those, so the companion never
  has to reimplement shell/system access.

---

### Task 1: `Transport` Protocol + `LoopbackTransport` fake

**Files:**
- Create: `src/phonectl/providers/transport.py`
- Test: `tests/test_transport.py`

**Interfaces:**
- `Transport` — `typing.Protocol` with `request(self, method: str, params: dict, *, request_id: str,
  timeout: float) -> dict` and `ping(self, *, timeout: float = 1.0) -> bool`.
- `LoopbackTransport(handlers: dict[str, callable], *, version: int = 1)` — an in-process fake. Each
  handler is `params -> data`. `request()` wraps the handler's return in the response envelope and echoes
  the `request_id`. A handler that raises returns `{"ok": False, "error": {...}}`. `ping()` returns
  `True` unless constructed with `available=False`.
- `next_request_id() -> str` — module helper returning a unique id (`uuid4().hex`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_transport.py
import pytest
from phonectl.providers.transport import LoopbackTransport, next_request_id


def test_request_echoes_request_id_and_wraps_data():
    t = LoopbackTransport({"echo": lambda p: {"said": p["msg"]}})
    rid = next_request_id()
    resp = t.request("echo", {"msg": "hi"}, request_id=rid, timeout=1.0)
    assert resp["ok"] is True
    assert resp["request_id"] == rid
    assert resp["data"]["said"] == "hi"


def test_request_unknown_method_returns_error_envelope():
    t = LoopbackTransport({})
    resp = t.request("nope", {}, request_id=next_request_id(), timeout=1.0)
    assert resp["ok"] is False
    assert "error" in resp


def test_handler_exception_becomes_error_envelope():
    def boom(p):
        raise RuntimeError("kaboom")
    t = LoopbackTransport({"boom": boom})
    resp = t.request("boom", {}, request_id=next_request_id(), timeout=1.0)
    assert resp["ok"] is False
    assert "kaboom" in resp["error"]["message"]


def test_ping_reflects_availability():
    assert LoopbackTransport({}).ping() is True
    assert LoopbackTransport({}, available=False).ping() is False


def test_next_request_id_is_unique():
    assert next_request_id() != next_request_id()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_transport.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'phonectl.providers.transport'`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/providers/transport.py
"""Companion-APK transport seam — request/response with request-id + stale-response protection."""
from __future__ import annotations

import uuid
from typing import Protocol, runtime_checkable


def next_request_id() -> str:
    return uuid.uuid4().hex


@runtime_checkable
class Transport(Protocol):
    def request(self, method: str, params: dict, *, request_id: str, timeout: float) -> dict: ...
    def ping(self, *, timeout: float = 1.0) -> bool: ...


class LoopbackTransport:
    """In-process fake companion. Tests register `method -> (params -> data)` handlers."""

    def __init__(self, handlers: dict, *, version: int = 1, available: bool = True) -> None:
        self._handlers = dict(handlers)
        self._version = version
        self._available = available

    def ping(self, *, timeout: float = 1.0) -> bool:
        return self._available

    def request(self, method: str, params: dict, *, request_id: str, timeout: float) -> dict:
        handler = self._handlers.get(method)
        if handler is None:
            return {"ok": False, "request_id": request_id, "version": self._version,
                    "error": {"code": "unknown_method", "message": f"no handler for {method!r}"}}
        try:
            data = handler(params or {})
        except Exception as exc:  # noqa: BLE001 — surface companion errors as envelopes
            return {"ok": False, "request_id": request_id, "version": self._version,
                    "error": {"code": "handler_error", "message": str(exc)}}
        return {"ok": True, "request_id": request_id, "version": self._version, "data": data}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_transport.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/providers/transport.py tests/test_transport.py
git commit -m "feat: Transport Protocol + LoopbackTransport in-process fake with request-id envelopes"
```

---

### Task 2: New capability keys + `AccessibilityProvider` discovery + `capabilities()`

**Files:**
- Modify: `src/phonectl/capabilities.py` (add five keys)
- Create: `src/phonectl/providers/accessibility.py`
- Test: `tests/test_capabilities.py` (append), `tests/test_providers_accessibility.py` (create)

**Interfaces:**
- New keys in `CAPABILITY_KEYS`: `observe_ui_native`, `observe_ui_events`, `act_set_text_native`,
  `act_gesture_native`, `act_semantic_action`.
- `AccessibilityProvider(transport, *, timeout=2.0)`.
- `AccessibilityProvider.is_available() -> bool` — `transport.ping()`.
- `AccessibilityProvider.capabilities() -> dict` — when available, `capabilities.make(...)` with the five
  new keys **plus** `observe_ui_tree`, `act_tap`, `act_type`, `act_key`, `launch_app`, `observe_screenshot`
  all `True`; when unavailable, all `False`.
- `AccessibilityProvider._call(method, params) -> dict` — internal helper: mints a `request_id`, calls
  `transport.request`, **drops responses whose `request_id` mismatches** (raises
  `errors.ObserveError`/`errors.ActionError` per call site), raises on `ok == False`, returns `data`.

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_capabilities.py
def test_accessibility_capability_keys_exist():
    from phonectl import capabilities
    for key in ("observe_ui_native", "observe_ui_events", "act_set_text_native",
                "act_gesture_native", "act_semantic_action"):
        assert key in capabilities.CAPABILITY_KEYS, f"missing key: {key}"


# tests/test_providers_accessibility.py (new file)
import pytest
from phonectl.providers.accessibility import AccessibilityProvider
from phonectl.providers.transport import LoopbackTransport


def test_capabilities_all_relevant_true_when_available():
    p = AccessibilityProvider(LoopbackTransport({}))
    caps = p.capabilities()
    for key in ("observe_ui_native", "observe_ui_events", "act_set_text_native",
                "act_gesture_native", "act_semantic_action",
                "observe_ui_tree", "act_tap", "act_type", "act_key", "launch_app"):
        assert caps[key] is True


def test_capabilities_all_false_when_unavailable():
    p = AccessibilityProvider(LoopbackTransport({}, available=False))
    assert all(v is False for v in p.capabilities().values())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_capabilities.py tests/test_providers_accessibility.py -v`
Expected: FAIL (`ModuleNotFoundError` for `accessibility.py`; missing capability keys).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/capabilities.py — append to CAPABILITY_KEYS
    # Phase 4.1 additions (AccessibilityService companion)
    "observe_ui_native",
    "observe_ui_events",
    "act_set_text_native",
    "act_gesture_native",
    "act_semantic_action",
```

```python
# src/phonectl/providers/accessibility.py
"""AccessibilityService companion provider — native tree, events, gestures, set-text."""
from __future__ import annotations

from phonectl import capabilities as caps_mod
from phonectl import errors
from phonectl.providers.transport import next_request_id


class AccessibilityProvider:
    def __init__(self, transport, *, timeout: float = 2.0) -> None:
        self._t = transport
        self._timeout = timeout

    def is_available(self) -> bool:
        try:
            return bool(self._t.ping())
        except Exception:  # noqa: BLE001
            return False

    def capabilities(self) -> dict:
        if not self.is_available():
            return caps_mod.make()
        return caps_mod.make(
            observe_ui_native=True, observe_ui_events=True,
            act_set_text_native=True, act_gesture_native=True, act_semantic_action=True,
            observe_ui_tree=True, observe_screenshot=True,
            act_tap=True, act_type=True, act_key=True, launch_app=True,
        )

    def _call(self, method: str, params: dict | None = None) -> dict:
        rid = next_request_id()
        resp = self._t.request(method, params or {}, request_id=rid, timeout=self._timeout)
        if resp.get("request_id") != rid:
            raise errors.ObserveError(
                f"stale companion response: expected {rid}, got {resp.get('request_id')}"
            )
        if not resp.get("ok"):
            err = resp.get("error", {})
            raise errors.ActionError(err.get("message", "companion error"))
        return resp.get("data", {})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_capabilities.py tests/test_providers_accessibility.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/capabilities.py src/phonectl/providers/accessibility.py \
        tests/test_capabilities.py tests/test_providers_accessibility.py
git commit -m "feat: AccessibilityProvider discovery + native capability keys (observe/gesture/set-text/events)"
```

---

### Task 3: Native tree + pure `native_tree.to_compat_xml` for `ui_dump()`

**Files:**
- Create: `src/phonectl/native_tree.py` (pure)
- Modify: `src/phonectl/providers/accessibility.py` (`observe_native`, `ui_dump`, `window_dump`, `wm_size`)
- Test: `tests/test_native_tree.py` (create), `tests/test_providers_accessibility.py` (append)

**Interfaces:**
- `native_tree.to_compat_xml(native: dict) -> str` — **pure**. Emits a uiautomator-style
  `<hierarchy rotation="0">…<node …/>…</hierarchy>` document so the existing `ui_parser.parse()` consumes
  it unchanged. Maps native node fields to uiautomator attributes: `text`→`text`, `class`→`class`,
  `content_desc`→`content-desc`, `bounds [l,t,r,b]`→`[l,t][r,b]`, and the boolean flags to
  `clickable`/`checkable`/`checked`/`enabled`/`focused`/`scrollable`/`password`. Escapes attribute values
  with `xml.sax.saxutils.quoteattr`.
- `AccessibilityProvider.observe_native() -> dict` — `self._call("observe_native")`; returns the native
  shape.
- `AccessibilityProvider.ui_dump() -> str` — `native_tree.to_compat_xml(self.observe_native())`.
- `AccessibilityProvider.window_dump() -> str` — returns `""` (foreground app is read from the native
  tree's first `application` window; `observer` already tolerates an empty window dump).
- `AccessibilityProvider.wm_size() -> tuple[int, int]` — from `observe_native()["screen"]` when present,
  else delegate is unavailable → the registry falls back to ADB for `wm_size` (companion may omit it).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_native_tree.py
from phonectl import native_tree, ui_parser

NATIVE = {
    "windows": [
        {"id": 1, "type": "application", "package": "com.example",
         "nodes": [
             {"node_id": "n1", "text": "Wi-Fi", "class": "android.widget.TextView",
              "content_desc": "", "bounds": [44, 380, 1036, 520],
              "clickable": True, "enabled": True, "scrollable": False, "password": False},
             {"node_id": "n2", "text": "", "class": "android.widget.EditText",
              "content_desc": "Search", "bounds": [0, 100, 1080, 200],
              "clickable": True, "enabled": True, "scrollable": False, "password": True},
         ]},
    ]
}


def test_to_compat_xml_is_parseable_by_ui_parser():
    xml = native_tree.to_compat_xml(NATIVE)
    elements, screen_hash = ui_parser.parse(xml)
    texts = [e.get("text") for e in elements]
    assert "Wi-Fi" in texts
    assert screen_hash  # non-empty stable hash


def test_to_compat_xml_maps_bounds_and_flags():
    xml = native_tree.to_compat_xml(NATIVE)
    assert 'bounds="[44,380][1036,520]"' in xml
    assert 'password="true"' in xml
    assert 'content-desc="Search"' in xml


def test_to_compat_xml_escapes_special_chars():
    native = {"windows": [{"id": 1, "type": "application", "package": "x",
              "nodes": [{"node_id": "n", "text": 'a & b "q"', "class": "T",
                         "content_desc": "", "bounds": [0, 0, 1, 1]}]}]}
    xml = native_tree.to_compat_xml(native)
    assert "&amp;" in xml  # raw '&' never leaks into the document
```

```python
# Append to tests/test_providers_accessibility.py
from phonectl import ui_parser


def _native_handler(_params):
    return {
        "windows": [{"id": 1, "type": "application", "package": "com.android.settings",
                     "nodes": [{"node_id": "n1", "text": "Network & internet",
                                "class": "android.widget.TextView", "content_desc": "",
                                "bounds": [0, 200, 1080, 320], "clickable": True,
                                "enabled": True}]}]
    }


def test_ui_dump_returns_parseable_compat_xml():
    p = AccessibilityProvider(LoopbackTransport({"observe_native": _native_handler}))
    elements, _ = ui_parser.parse(p.ui_dump())
    assert any(e.get("text") == "Network & internet" for e in elements)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_native_tree.py tests/test_providers_accessibility.py -v`
Expected: FAIL (`ModuleNotFoundError` for `native_tree`; `AttributeError: ... 'observe_native'`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/native_tree.py
"""Pure native-JSON -> uiautomator-compatible XML conversion. No I/O, no subprocess."""
from __future__ import annotations

from xml.sax.saxutils import quoteattr

_FLAGS = ("checkable", "checked", "clickable", "enabled", "focused", "scrollable", "password")


def _attr(name: str, value: str) -> str:
    return f"{name}={quoteattr(value)}"


def _node_xml(node: dict) -> str:
    l, t, r, b = node.get("bounds", [0, 0, 0, 0])
    parts = [
        _attr("text", str(node.get("text", "") or "")),
        _attr("class", str(node.get("class", "") or "")),
        _attr("content-desc", str(node.get("content_desc", "") or "")),
        _attr("bounds", f"[{l},{t}][{r},{b}]"),
    ]
    for flag in _FLAGS:
        parts.append(_attr(flag, "true" if node.get(flag) else "false"))
    return "<node " + " ".join(parts) + " />"


def to_compat_xml(native: dict) -> str:
    nodes = []
    for window in native.get("windows", []):
        for node in window.get("nodes", []):
            nodes.append(_node_xml(node))
    return '<?xml version="1.0" encoding="UTF-8"?>' \
           '<hierarchy rotation="0">' + "".join(nodes) + "</hierarchy>"
```

```python
# src/phonectl/providers/accessibility.py — add to AccessibilityProvider
from phonectl import native_tree


def observe_native(self) -> dict:
    return self._call("observe_native")

def ui_dump(self) -> str:
    return native_tree.to_compat_xml(self.observe_native())

def window_dump(self) -> str:
    return ""

def wm_size(self):
    data = self.observe_native()
    screen = data.get("screen")
    if screen and "width" in screen and "height" in screen:
        return (int(screen["width"]), int(screen["height"]))
    raise errors.CapabilityUnavailableError("companion did not report screen size")
```

> Note: if the companion omits `screen`, `wm_size()` raising `CapabilityUnavailableError` lets the
> registry fall through to ADB for screen metrics. Keep that behavior; do not hardcode a size.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_native_tree.py tests/test_providers_accessibility.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/native_tree.py src/phonectl/providers/accessibility.py \
        tests/test_native_tree.py tests/test_providers_accessibility.py
git commit -m "feat: native_tree.to_compat_xml + AccessibilityProvider.observe_native/ui_dump (compat mode)"
```

---

### Task 4: Gesture dispatch + `ACTION_SET_TEXT` (`input_*`, `set_text_native`)

**Files:**
- Modify: `src/phonectl/providers/accessibility.py`
- Test: `tests/test_providers_accessibility.py` (append)

**Interfaces:**
- `AccessibilityProvider.input_tap(x, y)` — `self._call("gesture", {"type": "tap", "x": x, "y": y})`.
- `input_swipe(x1, y1, x2, y2, ms=200)` — `gesture` with `{"type": "swipe", ...}`.
- `input_key(keycode)` — `self._call("key", {"keycode": keycode})`.
- `input_text(text)` — focus+type fallback path: `self._call("set_text", {"text": text, "mode": "type"})`.
- `set_text_native(node_id, text)` — `ACTION_SET_TEXT` on a specific node:
  `self._call("set_text", {"node_id": node_id, "text": text, "mode": "set"})`. This is the precise,
  IME-independent text strategy (strategy §6.1).
- `launch(package)` — `self._call("launch", {"package": package})`.
- `screencap(path)` — `self._call("screencap", {"path": path})` then returns `path` (companion writes the
  PNG to a shared path; absent companion support, the registry falls back to ADB `screencap`).

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_providers_accessibility.py

class RecordingTransport(LoopbackTransport):
    def __init__(self):
        self.sent = []
        super().__init__({
            "gesture": self._ok, "key": self._ok, "set_text": self._ok, "launch": self._ok,
        })

    def _ok(self, params):
        return {"applied": True}

    def request(self, method, params, *, request_id, timeout):
        self.sent.append((method, params))
        return super().request(method, params, request_id=request_id, timeout=timeout)


def test_input_tap_sends_tap_gesture():
    t = RecordingTransport()
    AccessibilityProvider(t).input_tap(100, 220)
    assert ("gesture", {"type": "tap", "x": 100, "y": 220}) in t.sent


def test_set_text_native_uses_action_set_text_mode():
    t = RecordingTransport()
    AccessibilityProvider(t).set_text_native("n2", "hello")
    method, params = t.sent[-1]
    assert method == "set_text"
    assert params == {"node_id": "n2", "text": "hello", "mode": "set"}


def test_input_text_uses_type_mode():
    t = RecordingTransport()
    AccessibilityProvider(t).input_text("hi")
    assert t.sent[-1] == ("set_text", {"text": "hi", "mode": "type"})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_providers_accessibility.py -v -k "tap or set_text or input_text"`
Expected: FAIL (`AttributeError`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/providers/accessibility.py — add to AccessibilityProvider

def input_tap(self, x, y):
    self._call("gesture", {"type": "tap", "x": x, "y": y})

def input_swipe(self, x1, y1, x2, y2, ms: int = 200):
    self._call("gesture", {"type": "swipe", "x1": x1, "y1": y1, "x2": x2, "y2": y2, "ms": ms})

def input_key(self, keycode):
    self._call("key", {"keycode": keycode})

def input_text(self, text):
    self._call("set_text", {"text": text, "mode": "type"})

def set_text_native(self, node_id, text):
    self._call("set_text", {"node_id": node_id, "text": text, "mode": "set"})

def launch(self, package):
    self._call("launch", {"package": package})

def screencap(self, path):
    self._call("screencap", {"path": path})
    return path

def get_state(self):
    return "device" if self.is_available() else "unknown"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_providers_accessibility.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/providers/accessibility.py tests/test_providers_accessibility.py
git commit -m "feat: AccessibilityProvider gesture dispatch, input_key, and ACTION_SET_TEXT (set_text_native)"
```

---

### Task 5: Semantic node actions

**Files:**
- Modify: `src/phonectl/providers/accessibility.py`
- Test: `tests/test_providers_accessibility.py` (append)

**Interfaces:**
- `semantic_action(node_id, action) -> dict` — performs an accessibility action on a node by id
  (`click`, `long_click`, `scroll_forward`, `scroll_backward`, `expand`, `collapse`, `dismiss`). Calls
  `self._call("semantic", {"node_id": node_id, "action": action})`. Raises
  `errors.GuardedActionError`-free `ActionError` when the companion reports the action unsupported on that
  node (the `actions` list in the native tree tells callers what is available before they try).
- `SUPPORTED_SEMANTIC_ACTIONS` — a frozenset used to validate the `action` argument locally before a round
  trip; an unknown action raises `ValueError` without contacting the companion.

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_providers_accessibility.py

def test_semantic_action_click_sends_request():
    t = RecordingTransport()
    t._handlers["semantic"] = lambda p: {"performed": p["action"]}
    out = AccessibilityProvider(t).semantic_action("n1", "click")
    assert out["performed"] == "click"
    assert t.sent[-1][0] == "semantic"


def test_semantic_action_rejects_unknown_action_locally():
    t = RecordingTransport()
    with pytest.raises(ValueError):
        AccessibilityProvider(t).semantic_action("n1", "teleport")
    assert all(m != "semantic" for m, _ in t.sent)  # never contacted companion
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_providers_accessibility.py -v -k "semantic"`
Expected: FAIL (`AttributeError`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/providers/accessibility.py

SUPPORTED_SEMANTIC_ACTIONS = frozenset({
    "click", "long_click", "scroll_forward", "scroll_backward",
    "expand", "collapse", "dismiss",
})


def semantic_action(self, node_id, action) -> dict:
    if action not in SUPPORTED_SEMANTIC_ACTIONS:
        raise ValueError(f"unsupported semantic action {action!r}")
    return self._call("semantic", {"node_id": node_id, "action": action})
```

(Place `SUPPORTED_SEMANTIC_ACTIONS` at module scope; reference it as the module constant.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_providers_accessibility.py -v -k "semantic"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/providers/accessibility.py tests/test_providers_accessibility.py
git commit -m "feat: AccessibilityProvider.semantic_action with local action allow-list"
```

---

### Task 6: UI event polling

**Files:**
- Modify: `src/phonectl/providers/accessibility.py`
- Test: `tests/test_providers_accessibility.py` (append)

**Interfaces:**
- `poll_events(since: int = 0, *, max_events: int = 50) -> dict` — `self._call("events", {"since": since,
  "max": max_events})`; returns `{"events": [{"seq", "type", "package", "ts", ...}], "cursor": int}`.
  Event `type` ∈ `window_state_changed | content_changed | view_focused | view_clicked | notification`.
  The `cursor` is a monotonic sequence the caller passes back as `since` to get only newer events
  (no busy polling on the wire; the companion blocks up to its own timeout).
- This is the Python seam for `phone.watch_ui`/event subscriptions; the daemon (Phase 5) drives the loop.
  Plan 4.1 ships only the single-call primitive; it does not start a background thread.

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_providers_accessibility.py

def test_poll_events_returns_events_and_cursor():
    def events(p):
        assert p["since"] == 0
        return {"events": [{"seq": 1, "type": "window_state_changed", "package": "com.x"}],
                "cursor": 1}
    t = LoopbackTransport({"events": events})
    out = AccessibilityProvider(t).poll_events(since=0)
    assert out["cursor"] == 1
    assert out["events"][0]["type"] == "window_state_changed"


def test_poll_events_passes_since_cursor():
    seen = {}
    def events(p):
        seen.update(p)
        return {"events": [], "cursor": 7}
    t = LoopbackTransport({"events": events})
    AccessibilityProvider(t).poll_events(since=7)
    assert seen["since"] == 7
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_providers_accessibility.py -v -k "poll_events"`
Expected: FAIL (`AttributeError`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/providers/accessibility.py

def poll_events(self, since: int = 0, *, max_events: int = 50) -> dict:
    return self._call("events", {"since": since, "max": max_events})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_providers_accessibility.py -v -k "poll_events"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/providers/accessibility.py tests/test_providers_accessibility.py
git commit -m "feat: AccessibilityProvider.poll_events cursor-based UI event polling primitive"
```

---

### Task 7: Wire `AccessibilityProvider` into `cli.build_runtime()`

**Files:**
- Modify: `src/phonectl/cli.py`
- Test: `tests/test_cli.py` (append)

**Interfaces:**
- `_make_accessibility_provider() -> AccessibilityProvider | None` — constructs the provider over the
  default transport (`LoopbackTransport`-absent in real builds is replaced by `SocketTransport` in Plan
  4.3; **Plan 4.1 returns `None` unless an explicit transport is configured**, so default builds are
  unchanged). Separated so tests can patch it.
- `build_runtime(cfg, backend=None)` prepends the accessibility provider **first** (it wins for
  `observe_ui_tree`/`act_*` when present), then Termux:API (Plan 3.5), then ADB:
  ```python
  providers = [p for p in [_make_accessibility_provider(), _make_termux_provider(), adb]
               if p is not None]
  ```

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_cli.py

def test_build_runtime_prepends_accessibility_when_present(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    from phonectl import cli, config
    from phonectl.providers.accessibility import AccessibilityProvider
    from phonectl.providers.transport import LoopbackTransport

    acc = AccessibilityProvider(LoopbackTransport({}))  # available
    monkeypatch.setattr(cli, "_make_accessibility_provider", lambda: acc)
    monkeypatch.setattr(cli, "_make_termux_provider", lambda: None)
    cfg = config.load()
    registry, _, _ = cli.build_runtime(cfg)
    assert registry.for_capability("observe_ui_tree") is acc
    assert registry.for_capability("observe_ui_native") is acc


def test_build_runtime_without_accessibility_uses_adb(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    from phonectl import cli, config
    monkeypatch.setattr(cli, "_make_accessibility_provider", lambda: None)
    monkeypatch.setattr(cli, "_make_termux_provider", lambda: None)
    cfg = config.load()
    registry, _, _ = cli.build_runtime(cfg)
    assert registry.for_capability("observe_ui_native") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli.py -v -k "accessibility"`
Expected: FAIL (`AttributeError: ... '_make_accessibility_provider'`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/cli.py
from phonectl.providers.accessibility import AccessibilityProvider  # noqa: F401 (used by helper)


def _make_accessibility_provider():
    # Plan 4.1: no default transport yet — Plan 4.3 supplies SocketTransport.
    # Returns None so default builds are ADB-first; tests patch this to inject a provider.
    return None


def build_runtime(cfg, backend=None):
    adb = backend or _make_backend(cfg)
    providers = [p for p in [_make_accessibility_provider(), _make_termux_provider(), adb]
                 if p is not None]
    registry = ProviderRegistry(providers)
    session = Session()
    conn = Connection(registry, cfg)
    return registry, session, conn
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli.py -v`
Expected: PASS (new tests + all existing — default `_make_accessibility_provider()` returns `None`, so
the registry is ADB-first exactly as before).

- [ ] **Step 5: Run full suite**

Run: `pytest -v`
Expected: PASS (all tests).

- [ ] **Step 6: Commit**

```bash
git add src/phonectl/cli.py tests/test_cli.py
git commit -m "feat: build_runtime prepends AccessibilityProvider ahead of Termux:API and ADB"
```

---

### Task 8: Android APK design spec + docs

**Files:**
- Create: `android/accessibility-companion/SPEC.md`
- Modify: `README.md` (Accessibility provider section)
- Modify: `docs/superpowers/specs/2026-06-20-phonectl-adb-bridge-design.md`

**Interfaces:** none (documentation only).

- [ ] **Step 1: Write the Android companion design spec**

Create `android/accessibility-companion/SPEC.md` describing the **Kotlin AccessibilityService companion**
(no implementation here — design only, the input to the dedicated Android build):
- **Service surface:** `AccessibilityService` with `canRetrieveWindowContent`,
  `canPerformGestures`, `flagReportViewIds`. Methods mapped to transport `method`s:
  `observe_native`, `gesture`, `key`, `set_text` (`mode=set` → `ACTION_SET_TEXT`, `mode=type` →
  focus + `ACTION_SET_TEXT` on focused node), `semantic`, `launch`, `screencap`, `events`, `ping`.
- **Native tree shape** — restate the windows/nodes JSON from §"Shared conventions"; document the
  node `actions` array (`AccessibilityNodeInfo.AccessibilityAction`).
- **Compatibility mode** — the companion MAY also emit uiautomator XML directly; the Python side already
  has `native_tree.to_compat_xml`, so JSON is the canonical wire format.
- **Transport** — request/response envelope with `request_id`, `timeout`, `version`,
  capability negotiation, stale-response protection. The concrete socket transport is specified in Plan
  4.3 (`android/foreground-service/SPEC.md`); 4.1's spec defines the **message contract**, transport-agnostic.
- **Permissions & trust** — Accessibility is highly sensitive; cross-reference the trust UX in Plan 4.3.
- **Non-goals** — no network, no analytics, local-only; explicitly out of scope for 4.1's Python seam.

- [ ] **Step 2: Update README + design spec**

In `README.md`, add an **AccessibilityService provider (companion APK)** section:
- What it unlocks: native JSON tree, UI events, precise `ACTION_SET_TEXT`, semantic actions, gesture
  dispatch — and that it transparently **wins over ADB** for `observe_ui_tree`/`act_*` when connected.
- That `ui_dump()` still returns uiautomator-compatible XML, so element index `i` and all selectors work
  identically across providers.
- That it is **optional**: absent the companion, phonectl is ADB-first and unchanged.
- Add the provider's capability row to the capability table.

In the design spec, extend the backend-isolation note: "AccessibilityService is an additional `Backend`
provider reached over an injectable `Transport`; it never bypasses the provider graph, and ADB remains the
shell/system provider via `ProviderRegistry.__getattr__`."

Run the full suite before committing:

```bash
pytest -v
```

- [ ] **Step 3: Commit**

```bash
git add android/accessibility-companion/SPEC.md README.md \
        docs/superpowers/specs/2026-06-20-phonectl-adb-bridge-design.md
git commit -m "docs: AccessibilityService companion APK design spec + provider docs (native tree/events/gestures)"
```

---

## Dependencies

**Requires:** Plan 1.1 (`errors`/`results`/`capabilities`/`backend.Backend`), Plan 3.1
(`ProviderRegistry` — `for_capability`, `__getattr__` fallthrough to ADB).
**Enables:** Plan 4.2 (notifications reuse the `Transport` seam + the `events` stream), Plan 4.3
(`SocketTransport` plugs into `_make_accessibility_provider`; trust toggles intersect this provider's
`capabilities()`), Plan 4.4 (ML-Kit OCR over the same transport), Phase 5 (the daemon owns the
`poll_events` loop and snapshot cache).

## Deferred / out of scope

- **The Kotlin APK implementation** — this plan ships the Python seam + design spec only.
- **`SocketTransport`** (low-latency localhost) — Plan 4.3. Plan 4.1's `_make_accessibility_provider()`
  returns `None` by default, so production builds stay ADB-first until 4.3 supplies the transport.
- **Background event loop / subscriptions** — `poll_events` is a single-call primitive; the daemon
  (Phase 5) drives continuous fanout.
- **Provider-scored fallback** (prefer Accessibility but fall back to ADB on a per-call failure) — the
  registry remains first-match; per-call health-based fallback is a Phase 5 daemon concern.
- **Multi-touch / pinch gestures** — the `gesture` method is extensible; pinch lands with the gesture
  work in Phase 7 if needed (3.3 already covers the ADB gesture set).

## Notes on testability

Everything is tested without a device or APK. `LoopbackTransport` plays the companion in-process with
scripted handlers; `RecordingTransport` captures outgoing `(method, params)` to assert the wire contract.
`native_tree.to_compat_xml` is **pure** and fixture-tested by round-tripping through the real
`ui_parser.parse` — proving the compatibility-mode contract end to end. The `cli.build_runtime` wiring is
tested by patching `_make_accessibility_provider` to inject an available provider and asserting it wins the
relevant capabilities; the default (`None`) path proves production builds remain ADB-first. The stale-
response guard is covered by a transport whose `request_id` echo is mismatched (add a focused test if the
companion's real transport is found to reorder responses on device).
