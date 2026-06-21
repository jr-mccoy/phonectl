# phonectl Provider/Capability Graph Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Plan 3.1 of the platform roadmap** (`docs/superpowers/phonectl-platform-roadmap.md`). First plan of
Phase 3. Depends on **Plan 1.1** (`errors`/`results`/`capabilities`/`Backend` Protocol).

**Goal:** Move from one active backend to a **composite runtime** that selects the best provider per
capability with graceful degradation, reporting the **provider path** that satisfied each call (strategy
§4.3, §21). Introduce a `ProviderRegistry` that holds multiple `Backend`-conforming providers in priority
order, merges their capability sets, and satisfies the `Backend` Protocol itself via delegation — so all
existing `observer`, `actuator`, `runtime`, and `cli` call-sites work unchanged. `cli.build_runtime()`
returns a `ProviderRegistry([AdbBackend(...)])` as its single-provider initial state; additional providers
(Termux:API in 3.5, AccessibilityService in 4.1) slot in without touching any caller.

**Architecture:** One new package `src/phonectl/providers/` with `__init__.py` (empty) and `registry.py`
(`ProviderRegistry`). The registry is purely structural — no I/O, no subprocess — and satisfies
`backend.Backend` via explicit delegation methods, each calling `_require(cap_key)` which sets
`self._last_used` to the chosen provider's class name. `runtime.run_action` reads `backend._last_used`
(via `getattr`, so it degrades cleanly for tests that pass a plain `AdbBackend`) and includes `provider`
in the result envelope. `cli.build_runtime()` is updated to wrap `AdbBackend` in a `ProviderRegistry`.

**Tech Stack:** Python 3 (stdlib only: `typing`); `pytest` for tests; no new runtime deps.

## Global Constraints

- **stdlib-only at runtime** (Python ≥ 3.9; `pytest` dev-only). No third-party runtime deps.
- **ONLY `adb_backend.py` may touch adb/subprocess.** `ProviderRegistry` delegates to backends; it
  never calls `subprocess` or `adb` directly.
- **`ui_parser.py` stays pure** (untouched by this plan).
- **Every actuator `act()` re-observes** — unchanged; the registry is transparent to `actuator`.
- **Injectable seams** — tests pass fake backends with predetermined `capabilities()` dicts.
  Isolate config/audit via `monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))`.
- **Modes + kill-switch + risk policy gate every mutating action** — unchanged; all flow through
  `runtime.run_action`.
- **Structured-result invariant (Plan 1.1):** every result is a `results.ok/err` envelope.
- **One commit per task. TDD order is non-negotiable.**

## Shared conventions established by this plan

- **`src/phonectl/providers/` is created HERE.** Plans 3.2 and 3.5 add provider modules inside it.
- **`ProviderRegistry._last_used`** is the provider-path seam: a `str | None` class name, set by
  every `_require()` call. Callers reading `getattr(backend, "last_used", None)` get the name of the
  provider that actually ran; if the backend is a plain `AdbBackend` they get `None` and fall back to
  `"adb"`.
- **Priority order is positional:** first provider in the list wins for each capability. Adding a
  higher-priority provider means prepending it to the list in `build_runtime`.
- **`ProviderRegistry.__getattr__`** delegates unknown attribute lookups to the first ADB-capable
  provider, covering the `adb`-specific helper methods (`wake`, `keyguard`, `lock_state`,
  `mdns_services`, `adb_version`, `devices`) used by `Connection` and diagnostics without requiring
  every backend to implement them.

---

### Task 1: `ProviderRegistry` — capability resolution core

**Files:**
- Create: `src/phonectl/providers/__init__.py` (empty)
- Create: `src/phonectl/providers/registry.py`
- Test: `tests/test_providers_registry.py`

**Interfaces:**
- `ProviderRegistry(providers: list)` — stores providers in priority order.
- `ProviderRegistry.for_capability(cap: str) -> object | None` — first provider whose
  `capabilities()[cap]` is `True`, or `None`.
- `ProviderRegistry.capabilities() -> dict` — union: a key is `True` if any provider has it `True`.
- `ProviderRegistry.capabilities_by_provider() -> list[dict]` — per-provider view for diagnostics;
  each entry is `{"provider": ClassName, "caps": dict}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_providers_registry.py
import pytest
from phonectl.providers.registry import ProviderRegistry


class FakeProv:
    def __init__(self, name, caps):
        self._name = name
        self._caps = caps

    def capabilities(self):
        return self._caps


def _caps(**kw):
    from phonectl import capabilities
    return capabilities.make(**kw)


def test_capabilities_merged():
    a = FakeProv("A", _caps(act_tap=True, requires_adb=True))
    b = FakeProv("B", _caps(read_clipboard=True))
    r = ProviderRegistry([a, b])
    merged = r.capabilities()
    assert merged["act_tap"] is True
    assert merged["read_clipboard"] is True
    assert merged["observe_ui_tree"] is False


def test_for_capability_returns_first_matching():
    a = FakeProv("A", _caps(act_tap=True))
    b = FakeProv("B", _caps(act_tap=True))
    r = ProviderRegistry([a, b])
    assert r.for_capability("act_tap") is a


def test_for_capability_returns_none_when_no_match():
    a = FakeProv("A", _caps(act_tap=True))
    r = ProviderRegistry([a])
    assert r.for_capability("read_clipboard") is None


def test_capabilities_by_provider_shape():
    a = FakeProv("A", _caps(act_tap=True))
    r = ProviderRegistry([a])
    items = r.capabilities_by_provider()
    assert len(items) == 1
    assert items[0]["provider"] == "FakeProv"
    assert items[0]["caps"]["act_tap"] is True


def test_empty_registry_has_all_false_capabilities():
    r = ProviderRegistry([])
    assert all(v is False for v in r.capabilities().values())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_providers_registry.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'phonectl.providers'`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/providers/__init__.py
# (empty — registry and provider modules live as siblings)
```

```python
# src/phonectl/providers/registry.py
"""Composite provider registry — selects the best provider per capability."""
from __future__ import annotations

from phonectl import capabilities as caps_mod


class ProviderRegistry:
    def __init__(self, providers) -> None:
        self._providers = list(providers)
        self._last_used: str | None = None

    @property
    def last_used(self) -> str | None:
        return self._last_used

    def for_capability(self, cap: str):
        for p in self._providers:
            if p.capabilities().get(cap):
                return p
        return None

    def capabilities(self) -> dict:
        merged = caps_mod.make()
        for p in self._providers:
            for k, v in p.capabilities().items():
                if v:
                    merged[k] = True
        return merged

    def capabilities_by_provider(self) -> list:
        return [
            {"provider": type(p).__name__, "caps": p.capabilities()}
            for p in self._providers
        ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_providers_registry.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/providers/__init__.py src/phonectl/providers/registry.py \
        tests/test_providers_registry.py
git commit -m "feat: ProviderRegistry — composite capability resolution with priority ordering"
```

---

### Task 2: `ProviderRegistry` — `Backend` Protocol delegation

**Files:**
- Modify: `src/phonectl/providers/registry.py` (add delegation methods + `_require` + `__getattr__`)
- Modify: `src/phonectl/backend.py` (add `serial` property to Protocol; document ADB-specific helpers
  as optional via `hasattr`)
- Test: `tests/test_providers_registry.py` (append)

**Interfaces:**
- `_require(cap) -> provider` — returns the first matching provider, sets `_last_used` to its class
  name, raises `CapabilityUnavailableError` when none available.
- Explicit delegation for all `Backend` Protocol methods: `ui_dump`, `window_dump`, `wm_size`,
  `screencap`, `input_tap`, `input_text`, `input_swipe`, `input_key`, `launch`, `get_state`.
- `serial` property — first provider's `serial` attribute, or `None`.
- `__getattr__` — delegates unknown attributes to the first provider with `requires_adb=True`, covering
  `wake`, `keyguard`, `lock_state`, `mdns_services`, `adb_version`, `devices` and any future ADB helper
  added to `AdbBackend` without a Protocol entry.

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_providers_registry.py

from phonectl import errors
from phonectl.adb_backend import AdbBackend


class FakeAdbProv:
    serial = "fake:5555"
    _tapped = None

    def capabilities(self):
        return _caps(act_tap=True, observe_ui_tree=True, requires_adb=True,
                     launch_app=True, act_type=True, act_key=True,
                     observe_screenshot=True)

    def ui_dump(self):
        return "<hierarchy></hierarchy>"

    def window_dump(self):
        return ""

    def wm_size(self):
        return (1080, 2400)

    def screencap(self, path):
        return path

    def input_tap(self, x, y):
        FakeAdbProv._tapped = (x, y)

    def input_text(self, text):
        pass

    def input_swipe(self, x1, y1, x2, y2, ms=200):
        pass

    def input_key(self, keycode):
        pass

    def launch(self, package):
        pass

    def get_state(self):
        return "device"

    def wake(self):
        pass


def test_delegation_tap_sets_last_used():
    prov = FakeAdbProv()
    r = ProviderRegistry([prov])
    r.input_tap(100, 200)
    assert r.last_used == "FakeAdbProv"
    assert FakeAdbProv._tapped == (100, 200)


def test_delegation_serial_property():
    prov = FakeAdbProv()
    r = ProviderRegistry([prov])
    assert r.serial == "fake:5555"


def test_require_raises_capability_unavailable_when_no_provider():
    r = ProviderRegistry([])
    with pytest.raises(errors.CapabilityUnavailableError) as exc:
        r.input_tap(0, 0)
    assert "act_tap" in str(exc.value)


def test_getattr_delegates_adb_specific_helpers():
    prov = FakeAdbProv()
    r = ProviderRegistry([prov])
    r.wake()   # delegates via __getattr__ to FakeAdbProv.wake


def test_getattr_raises_when_no_adb_provider():
    r = ProviderRegistry([])
    with pytest.raises(AttributeError):
        r.wake()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_providers_registry.py -v`
Expected: FAIL (new tests fail — delegation methods not yet on `ProviderRegistry`).

- [ ] **Step 3: Write minimal implementation**

Append to `src/phonectl/providers/registry.py`:

```python
from phonectl import errors


class ProviderRegistry:
    # ... (existing __init__, for_capability, capabilities, capabilities_by_provider) ...

    @property
    def serial(self):
        p = self.for_capability("requires_adb")
        return getattr(p, "serial", None) if p else None

    def _require(self, cap: str):
        p = self.for_capability(cap)
        if p is None:
            raise errors.CapabilityUnavailableError(
                f"no provider registered for capability {cap!r}"
            )
        self._last_used = type(p).__name__
        return p

    # Backend Protocol delegation
    def ui_dump(self) -> str:
        return self._require("observe_ui_tree").ui_dump()

    def window_dump(self) -> str:
        return self._require("observe_ui_tree").window_dump()

    def wm_size(self):
        return self._require("observe_ui_tree").wm_size()

    def screencap(self, path: str) -> str:
        return self._require("observe_screenshot").screencap(path)

    def input_tap(self, x: int, y: int) -> None:
        self._require("act_tap").input_tap(x, y)

    def input_text(self, text: str) -> None:
        self._require("act_type").input_text(text)

    def input_swipe(self, x1, y1, x2, y2, ms: int = 200) -> None:
        self._require("act_tap").input_swipe(x1, y1, x2, y2, ms)

    def input_key(self, keycode: str) -> None:
        self._require("act_key").input_key(keycode)

    def launch(self, package: str) -> None:
        self._require("launch_app").launch(package)

    def get_state(self) -> str:
        return self._require("requires_adb").get_state()

    def __getattr__(self, name: str):
        # Delegate ADB-specific helpers (wake, keyguard, lock_state, mdns_services, …)
        # to the first ADB-capable provider without requiring Protocol entries for each.
        p = self.for_capability("requires_adb")
        if p is None:
            raise AttributeError(
                f"ProviderRegistry has no attribute {name!r} and no ADB provider is registered"
            )
        return getattr(p, name)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_providers_registry.py -v`
Expected: PASS (all tests, including the 5 new delegation tests).

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/providers/registry.py tests/test_providers_registry.py
git commit -m "feat: ProviderRegistry Backend delegation with _require, serial, and __getattr__ fallback"
```

---

### Task 3: Wire `cli.build_runtime()` to return a `ProviderRegistry`

**Files:**
- Modify: `src/phonectl/cli.py` (`build_runtime` wraps `AdbBackend` in `ProviderRegistry`)
- Test: `tests/test_cli.py` (append)

**Interfaces:**
- `build_runtime(cfg, backend=None)` returns `(ProviderRegistry, Session, Connection)` instead of
  `(AdbBackend, Session, Connection)`. All callers receive the same tuple shape; `ProviderRegistry`
  satisfies `Backend` so they work unchanged.
- When `backend` is passed explicitly (e.g. by tests), it is wrapped in `ProviderRegistry([backend])`
  unless it is already a `ProviderRegistry`.

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_cli.py

from phonectl.providers.registry import ProviderRegistry


def test_build_runtime_returns_registry(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    from phonectl import config, cli
    cfg = config.load()
    backend, session, conn = cli.build_runtime(cfg)
    assert isinstance(backend, ProviderRegistry)


def test_build_runtime_wraps_explicit_backend(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    from phonectl import config, cli
    cfg = config.load()
    fake = FakeBackend()  # existing test double in test_cli.py
    backend, session, conn = cli.build_runtime(cfg, backend=fake)
    assert isinstance(backend, ProviderRegistry)
    assert backend.for_capability("act_tap") is fake
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli.py::test_build_runtime_returns_registry -v`
Expected: FAIL (`isinstance(backend, ProviderRegistry)` is False — still returns `AdbBackend`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/cli.py — update build_runtime
from phonectl.providers.registry import ProviderRegistry


def build_runtime(cfg, backend=None):
    raw = backend or _make_backend(cfg)
    registry = raw if isinstance(raw, ProviderRegistry) else ProviderRegistry([raw])
    session = Session()
    conn = Connection(registry, cfg)
    return registry, session, conn
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli.py -v`
Expected: PASS (new tests + all existing CLI tests — `ProviderRegistry` is transparent).

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/cli.py tests/test_cli.py
git commit -m "feat: build_runtime returns ProviderRegistry wrapping AdbBackend"
```

---

### Task 4: Provider-path reporting in result envelopes

**Files:**
- Modify: `src/phonectl/runtime.py` (read `backend.last_used` for `provider` field)
- Modify: `src/phonectl/cli.py` (`_cmd_observe` reads `backend.last_used`)
- Test: `tests/test_runtime.py` (append), `tests/test_cli.py` (append)

**Interfaces:**
- `runtime.run_action` replaces the hardcoded `provider="adb"` with
  `provider = getattr(backend, "last_used", None) or "adb"` in both the `dry-run` and the success
  path. The `fn(backend, session)` call triggers delegation inside the registry, which sets
  `backend._last_used`; we read it after the call.
- `_cmd_observe` in `cli.py` similarly reads `getattr(backend, "last_used", None) or "adb"` after
  `observer.observe(backend, session)` completes.
- `fallbacks_considered` is omitted in Phase 3.1 (single-provider registry); it will be populated
  in later phases when multiple providers compete for the same capability.

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_runtime.py

def test_run_action_reports_provider_from_registry(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    from phonectl import config, runtime, results, errors
    from phonectl.providers.registry import ProviderRegistry
    from tests.test_cli import FakeBackend  # reuse the existing test double

    fake = FakeBackend()
    registry = ProviderRegistry([fake])

    def build(cfg):
        from phonectl.session import Session
        from phonectl.connection import Connection
        sess = Session()
        conn = Connection(registry, cfg)
        conn.ensure = lambda: None
        return registry, sess, conn

    cfg = config.load()
    env = runtime.run_action(
        "tap", lambda b, s: s.last or {}, "i=0",
        build=build, yes=True,
        cfg=cfg,
    )
    assert env["ok"] is True
    assert env["provider"] in ("FakeBackend", "adb")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_runtime.py -v -k test_run_action_reports_provider_from_registry`
Expected: FAIL or partial pass — the test will show that `env["provider"]` equals `"adb"` even when
a registry is used (because the provider field is still hardcoded).

- [ ] **Step 3: Write minimal implementation**

In `src/phonectl/runtime.py`, in `_run_action_body`, replace the two occurrences of
`provider="adb"` with dynamic lookup:

```python
# dry-run path:
provider = getattr(backend, "last_used", None) or "adb"
return results.ok(
    capability=f"ui.{verb}", provider=provider,
    data=session.last, dry_run=True, **risk, **base
)

# success path (after fn call):
snap = fn(backend, session)
provider = getattr(backend, "last_used", None) or "adb"
...
return results.ok(
    capability=f"ui.{verb}", provider=provider, data=snap, **risk, **base
)
```

In `src/phonectl/cli.py`, in `_cmd_observe`, after `observer.observe(...)`:

```python
if getattr(args, "json", False):
    provider = getattr(backend, "last_used", None) or "adb"
    print(json.dumps(results.ok(capability="ui.observe", provider=provider, data=snap), indent=2))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_runtime.py tests/test_cli.py -v`
Expected: PASS (new test + all existing runtime and CLI tests).

- [ ] **Step 5: Run full suite**

Run: `pytest -v`
Expected: PASS (all tests).

- [ ] **Step 6: Commit**

```bash
git add src/phonectl/runtime.py src/phonectl/cli.py \
        tests/test_runtime.py tests/test_cli.py
git commit -m "feat: result envelopes report provider name from ProviderRegistry.last_used"
```

---

### Task 5: Docs — provider graph section

**Files:**
- Modify: `README.md` (add "Provider graph" section)
- Modify: `docs/superpowers/specs/2026-06-20-phonectl-adb-bridge-design.md` (note registry seam)

**Interfaces:** none (documentation only).

- [ ] **Step 1: Document the contract**

In `README.md`, under "Architecture", add a **Provider graph** section:
- Explain that `build_runtime()` now returns a `ProviderRegistry` wrapping `AdbBackend`.
- Show the `capabilities_by_provider()` shape and how to query it.
- List the capability keys and which provider currently satisfies each.
- Note that adding a provider (Termux:API, AccessibilityService) means prepending it to the registry
  list in `build_runtime()` with no other changes required.
- Show the `provider` field in the result envelope and what it means.

In the design spec, add a note in the backend-isolation invariant: "the `Backend` seam is now a
`ProviderRegistry` that selects the best-available provider per capability; `AdbBackend` is the sole
provider in Phase 3.1 but the registry is extensible."

- [ ] **Step 2: Commit**

```bash
git add README.md docs/superpowers/specs/2026-06-20-phonectl-adb-bridge-design.md
git commit -m "docs: provider graph architecture, capability registry, and provider-path envelope field"
```

---

## Dependencies

**Requires:** Plan 1.1 (`errors`/`results`/`capabilities`/`backend.Backend`).
**Enables:** Plans 3.2 and 3.5 (add providers to the registry); Plan 4.1 (AccessibilityService slots
in as a higher-priority `observe_ui_tree` provider).

## Deferred / out of scope

- The **provider selection algorithm** remains first-match in Phase 3.1. A scored fallback (e.g.
  prefer Accessibility over ADB for `observe_ui_tree` but fall through to ADB on connect failure) is
  deferred to when multiple providers coexist (Phase 4+).
- **`fallbacks_considered`** in the result envelope is left empty until multiple providers compete
  (Phase 4+).
- **Provider lifecycle management** (connect/disconnect, health checks) is a daemon concern (Phase 5).
- The **Termux:API provider** and its capability extensions are Plan 3.5.
- The **AccessibilityService provider** is Plan 4.1.

## Notes on testability

`ProviderRegistry` is pure structural delegation — no I/O, no subprocess — so all tests use fake
providers with predetermined `capabilities()` dicts. The `cli.build_runtime()` change is tested by
asserting the return type; the existing `FakeBackend` test double satisfies the delegation contract
because `ProviderRegistry.__getattr__` wraps it. The `runtime.run_action` provider-path test reuses
the existing `FakeBackend` through a `ProviderRegistry` wrapper.
