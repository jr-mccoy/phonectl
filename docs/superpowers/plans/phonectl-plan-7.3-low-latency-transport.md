# phonectl Low-Latency Screen/Input Transport Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Plan 7.3 of the platform roadmap** (`docs/superpowers/phonectl-platform-roadmap.md`). Third plan of
Phase 7. Adds an **optional, runtime-discovered low-latency transport** for fast screen capture and input
injection, inspired by scrcpy / OpenSTF minicap+minitouch (strategy §11.3, §13.1). Depends on **Plan 1.1**
(`capabilities`/`results`/`errors`), **Plan 3.1** (`ProviderRegistry`/`build_runtime`), and **Plan 3.4**
(region-text / screenshot consumers); optional on **Plan 4.4** (the OCR provider, a downstream consumer of
fast screencap). It is **never a hard dependency**: when the fast transport is absent, observe/act fall back
to the ADB path exactly as today.

**Goal:** cut the per-frame cost of "screenshot → decide → tap" loops (the most latency-sensitive agent
pattern) by registering a provider that, when a low-latency capture/input bridge is present, serves
`screencap` and input injection through it instead of spawning an `adb exec-out screencap` per frame —
while preserving every invariant (capability discovery, structured results, backend isolation, re-observe,
one policy choke-point). This plan ships the **Python-side provider seam + a design spec** for the native
capture/input bridge; the bridge binary itself (a scrcpy-server-style or minicap/minitouch-style component)
is built/sourced separately and discovered at runtime.

**Architecture:** a new `src/phonectl/providers/fast_transport.py` (`FastTransportProvider`) that talks to a
capture/input bridge **only** through an injectable `session` seam — an object exposing
`capture(path) -> path` and `inject_tap(x, y)` / `inject_swipe(...)` (default: a thin adapter over a
discovered bridge socket/binary; tests inject a fake). It implements the `backend.Backend` capture/input
methods and reports `observe_screenshot_fast` / `act_input_fast` **only when the bridge is reachable**.
`cli.build_runtime` registers it **ahead of ADB for those two capabilities only** (so `ui_dump` still comes
from ADB/Accessibility — the fast path is screenshot + raw input, not the structured tree) and **only when
`enable_fast_transport` is true**. Input verbs still route through `runtime.run_action`.

**Tech Stack:** Python 3 (stdlib only: `socket`, `shutil`, `os`, `time`); `pytest`. No new runtime dep. The
native capture/input bridge is **design-spec only** here.

## Global Constraints

- **stdlib-only at runtime.** `socket`, `shutil`, `os`, `time` — all stdlib.
- **Backend isolation.** Only `fast_transport.py` knows about the bridge; it talks to it through the
  injected `session`. No other module references the bridge.
- **Capability-scoped registration.** The provider reports **only** `observe_screenshot_fast` +
  `act_input_fast`; it never claims the UI tree (`observe_ui_tree` stays with ADB/Accessibility). The
  registry's per-capability resolution means it accelerates exactly the screenshot/input path and nothing
  else.
- **Opt-in + runtime-discovered.** All-false `capabilities()` unless the bridge is reachable; registered
  only under `enable_fast_transport`. Default builds/tests unchanged.
- **Re-observe invariant preserved.** Fast input still routes through `runtime.run_action`, which
  re-observes via the registry's normal `observe` path; the fast transport changes *how a frame is
  captured*, not *that an act re-observes*.
- **One policy choke-point.** Input verbs go through `run_action` (mode/kill-switch/risk/rate-limit). The
  fast path adds no bypass.
- **Structured-result invariant.** Unreachable bridge ⇒ `errors.CapabilityUnavailableError`; the runtime
  falls back to ADB for that capability automatically (graceful degradation).
- **Injectable seams.** `FastTransportProvider(*, session=…, which=shutil.which)`; the `session` is a fake
  in tests. `PHONECTL_HOME` isolation.
- **One commit per task. TDD order is non-negotiable.**

## Shared conventions established by this plan

- **New capability keys** (added to `capabilities.CAPABILITY_KEYS`): `observe_screenshot_fast`,
  `act_input_fast`.
- **Session contract:** `session.capture(path) -> path` (write a PNG/raw frame, return the path);
  `session.inject_tap(x, y) -> None`; `session.inject_swipe(x1, y1, x2, y2, ms) -> None`;
  `session.is_alive() -> bool`. The provider owns the session lifecycle (lazy connect, reconnect on drop).
- **Config keys added:** `enable_fast_transport` (bool, default `false`); `fast_transport_kind`
  (`"scrcpy"|"minicap"`, default `"scrcpy"`) selecting the bridge adapter when multiple are discovered.
- **`screencap` parity:** `FastTransportProvider.screencap(path)` returns the same `path` contract the ADB
  `screencap` returns, so downstream consumers (observer screenshot, OCR 4.4) are unchanged.

---

### Task 1: Capability keys + `FastTransportProvider` discovery + `capabilities()`

**Files:**
- Modify: `src/phonectl/capabilities.py`
- Create: `src/phonectl/providers/fast_transport.py`
- Test: `tests/test_fast_transport_provider.py`

**Interfaces:**
- New keys: `observe_screenshot_fast`, `act_input_fast`.
- `FastTransportProvider(*, session=None, which=shutil.which)`. `is_available() -> bool` —
  `session is not None and session.is_alive()`; the default session is built only if a bridge binary is
  discovered. `capabilities()` — all-false unless available; then the two fast keys = True.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_fast_transport_provider.py
from phonectl import capabilities as caps
from phonectl.providers.fast_transport import FastTransportProvider


class FakeSession:
    def __init__(self, alive=True):
        self.alive = alive
        self.taps = []
        self.captures = []

    def is_alive(self):
        return self.alive

    def capture(self, path):
        self.captures.append(path)
        return path

    def inject_tap(self, x, y):
        self.taps.append((x, y))

    def inject_swipe(self, x1, y1, x2, y2, ms):
        self.taps.append(("swipe", x1, y1, x2, y2, ms))


def test_capability_keys_exist():
    c = caps.make(observe_screenshot_fast=True, act_input_fast=True)
    assert c["observe_screenshot_fast"] and c["act_input_fast"]


def test_unavailable_without_session():
    p = FastTransportProvider(session=None, which=lambda n: None)
    assert p.is_available() is False
    assert p.capabilities()["observe_screenshot_fast"] is False


def test_available_reports_fast_caps():
    p = FastTransportProvider(session=FakeSession(alive=True))
    c = p.capabilities()
    assert c["observe_screenshot_fast"] and c["act_input_fast"]
    assert c["observe_ui_tree"] is False  # never claims the tree


def test_dead_session_is_unavailable():
    p = FastTransportProvider(session=FakeSession(alive=False))
    assert p.is_available() is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_fast_transport_provider.py -v`
Expected: FAIL (`ValueError: unknown capability keys` / `ModuleNotFoundError`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/capabilities.py — append to CAPABILITY_KEYS
    # Phase 7.3 addition (low-latency transport)
    "observe_screenshot_fast",
    "act_input_fast",
```

```python
# src/phonectl/providers/fast_transport.py
"""Optional low-latency screen/input transport — runtime-discovered, never required."""
from __future__ import annotations

import shutil

from phonectl import capabilities as caps_mod

_BRIDGE_BINS = ("scrcpy", "minicap")


class FastTransportProvider:
    def __init__(self, *, session=None, which=shutil.which) -> None:
        self._session = session
        self._which = which

    def is_available(self) -> bool:
        try:
            return self._session is not None and self._session.is_alive()
        except Exception:
            return False

    def capabilities(self) -> dict:
        if not self.is_available():
            return caps_mod.make()
        return caps_mod.make(observe_screenshot_fast=True, act_input_fast=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_fast_transport_provider.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/capabilities.py src/phonectl/providers/fast_transport.py tests/test_fast_transport_provider.py
git commit -m "feat: fast-transport provider discovery + screenshot/input capability keys"
```

---

### Task 2: `screencap` over the fast session (with reconnect)

**Files:**
- Modify: `src/phonectl/providers/fast_transport.py`
- Test: `tests/test_fast_transport_provider.py` (append)

**Interfaces:**
- `FastTransportProvider.screencap(path) -> path` — capture a frame via `session.capture(path)`; raise
  `errors.CapabilityUnavailableError` if unavailable. On a transient session drop, attempt **one**
  reconnect (`session.reconnect()` if present), then retry once; if still failing, raise so the registry
  degrades to ADB.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_fast_transport_provider.py — append
import pytest
from phonectl import errors


def test_screencap_uses_session(tmp_path):
    s = FakeSession(alive=True)
    p = FastTransportProvider(session=s)
    out = p.screencap(str(tmp_path / "f.png"))
    assert out.endswith("f.png") and s.captures == [out]


def test_screencap_unavailable_raises(tmp_path):
    p = FastTransportProvider(session=FakeSession(alive=False))
    with pytest.raises(errors.CapabilityUnavailableError):
        p.screencap(str(tmp_path / "f.png"))


def test_screencap_reconnects_once_on_drop(tmp_path):
    class Flaky(FakeSession):
        def __init__(self):
            super().__init__(alive=True)
            self.reconnected = False
            self._fail_once = True

        def capture(self, path):
            if self._fail_once:
                self._fail_once = False
                raise OSError("frame socket closed")
            return super().capture(path)

        def reconnect(self):
            self.reconnected = True

    s = Flaky()
    p = FastTransportProvider(session=s)
    out = p.screencap(str(tmp_path / "f.png"))
    assert s.reconnected is True and out.endswith("f.png")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_fast_transport_provider.py -v -k "screencap"`
Expected: FAIL (`AttributeError: screencap`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/providers/fast_transport.py — append
from phonectl import errors


class FastTransportProvider:  # method added to the class above
    def _require(self):
        if not self.is_available():
            raise errors.CapabilityUnavailableError(
                "fast transport unavailable; enable it (enable_fast_transport) and ensure the "
                "capture bridge is running, or rely on ADB screencap (see docs/fast-transport.md).")

    def screencap(self, path):
        self._require()
        try:
            return self._session.capture(path)
        except OSError:
            if hasattr(self._session, "reconnect"):
                self._session.reconnect()
                return self._session.capture(path)
            raise errors.CapabilityUnavailableError("fast capture failed and no reconnect available")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_fast_transport_provider.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/providers/fast_transport.py tests/test_fast_transport_provider.py
git commit -m "feat: fast-transport screencap with single reconnect-on-drop"
```

---

### Task 3: Fast input injection (`input_tap`/`input_swipe`)

**Files:**
- Modify: `src/phonectl/providers/fast_transport.py`
- Test: `tests/test_fast_transport_provider.py` (append)

**Interfaces:**
- `FastTransportProvider.input_tap(x, y) -> None` / `input_swipe(x1, y1, x2, y2, ms=200) -> None` — route to
  `session.inject_tap` / `session.inject_swipe`; guarded by `_require()`. These satisfy the `Backend`
  input methods so `actuator` can use them transparently when this provider wins `act_input_fast`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_fast_transport_provider.py — append
def test_input_tap_and_swipe_route_to_session():
    s = FakeSession(alive=True)
    p = FastTransportProvider(session=s)
    p.input_tap(10, 20)
    p.input_swipe(1, 2, 3, 4, ms=120)
    assert (10, 20) in s.taps
    assert ("swipe", 1, 2, 3, 4, 120) in s.taps
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_fast_transport_provider.py -v -k "input"`
Expected: FAIL (`AttributeError: input_tap`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/providers/fast_transport.py — append
    def input_tap(self, x, y):
        self._require()
        self._session.inject_tap(x, y)

    def input_swipe(self, x1, y1, x2, y2, ms=200):
        self._require()
        self._session.inject_swipe(x1, y1, x2, y2, ms)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_fast_transport_provider.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/providers/fast_transport.py tests/test_fast_transport_provider.py
git commit -m "feat: fast-transport input injection (tap/swipe) satisfying the Backend input seam"
```

---

### Task 4: `build_runtime` wiring (capability-scoped, opt-in) + design spec + docs

**Files:**
- Modify: `src/phonectl/cli.py`, `src/phonectl/config.py`, `README.md`,
  `docs/superpowers/phonectl-platform-roadmap.md`
- Create: `src/phonectl/providers/fast_transport.py` session adapter stub (default `session` builder),
  `docs/superpowers/specs/2026-06-22-phonectl-fast-transport-design.md`, `docs/fast-transport.md`
- Test: `tests/test_cli.py` (append)

**Interfaces:**
- `config.DEFAULTS["enable_fast_transport"] = False`, `config.DEFAULTS["fast_transport_kind"] = "scrcpy"`.
- `cli._make_fast_transport_provider(cfg)` returns a `FastTransportProvider` **only** when
  `cfg["enable_fast_transport"]` and a bridge is discovered/alive; else `None`. `build_runtime` registers
  it **ahead of ADB** (so it wins `observe_screenshot_fast`/`act_input_fast`) but **below** the structured
  providers for the tree; absent ⇒ ordering unchanged.
- Design spec: the native capture/input bridge (scrcpy-server-style framing vs minicap+minitouch),
  connection lifecycle, the `session` contract, the latency/throughput rationale, the security model
  (loopback, no remote), and the handoff to the Python seam (Tasks 1–3).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli.py — append
def test_fast_transport_off_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: FakeBackend())
    reg = cli.build_runtime(config.load())
    assert reg.capabilities().get("observe_screenshot_fast", False) is False


def test_fast_transport_wins_screencap_when_enabled(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: FakeBackend())
    from phonectl.providers.fast_transport import FastTransportProvider
    from tests.test_fast_transport_provider import FakeSession
    monkeypatch.setattr(cli, "_make_fast_transport_provider",
                        lambda cfg: FastTransportProvider(session=FakeSession(alive=True)))
    reg = cli.build_runtime({**config.load(), "enable_fast_transport": True})
    reg._require("observe_screenshot_fast")
    assert reg.last_used == "FastTransportProvider"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli.py -v -k "fast_transport"`
Expected: FAIL (`AttributeError: _make_fast_transport_provider`).

- [ ] **Step 3: Write minimal implementation**

Add the config defaults; add `cli._make_fast_transport_provider(cfg)` + a default `session` builder that
adapts a discovered bridge (stubbed, never required); prepend the provider for its two capabilities in
`build_runtime`. Write the design spec + `docs/fast-transport.md` (when to enable, the latency rationale,
the bridge options, the "optional, falls back to ADB" framing); mark 7.3 in the roadmap.

- [ ] **Step 4: Run test to verify it passes, then the full suite**

Run: `pytest tests/test_cli.py -v -k "fast_transport"`
Then: `pytest -v`
Expected: PASS (the fast transport is opt-in + capability-scoped; default builds are unchanged).

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/cli.py src/phonectl/config.py src/phonectl/providers/fast_transport.py docs/superpowers/specs/2026-06-22-phonectl-fast-transport-design.md docs/fast-transport.md README.md docs/superpowers/phonectl-platform-roadmap.md tests/test_cli.py
git commit -m "feat: opt-in fast-transport wiring (capability-scoped) + bridge design spec + docs"
```

---

## Dependencies

- **Plan 1.1** (`capabilities`/`results`/`errors`), **Plan 3.1** (`ProviderRegistry`/`build_runtime` +
  per-capability resolution + graceful degradation). **Plan 3.4**/**4.4** are downstream consumers of fast
  `screencap` (no change required — they call the same `screencap` contract).

## Deferred / out of scope

- **The native capture/input bridge** (scrcpy-server / minicap+minitouch) — built/sourced separately from
  the Task-4 design spec; this plan ships only the Python seam + the `session` contract.
- **Continuous video streaming / frame diffing** — this plan accelerates discrete `screencap`; a streaming
  observe loop + screenshot-diff trigger source is a follow-up.
- **H.264 decode in-process** — out of scope (would need a non-stdlib dep); the bridge delivers decoded
  frames/PNG to the `session.capture` contract.

## Notes on testability

- The `session` seam is fully injectable — capture, input, reconnect, and degradation are asserted against
  a **fake session**; no real bridge, socket, or device runs in CI.
- Capability-scoped registration and the ADB-fallback ordering are unit-tested with fakes.
- **No device behavior is claimed.** Real latency wins are a manual on-device smoke (note it in
  `docs/fast-transport.md`; do not run it in CI).
