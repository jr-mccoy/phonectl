# phonectl Tasker / MacroDroid Interop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Plan 7.4 of the platform roadmap** (`docs/superpowers/phonectl-platform-roadmap.md`). Final plan of
Phase 7. Adds **two-way interop with the Android automation ecosystem** — phonectl can **invoke** Tasker
tasks / MacroDroid macros via intents (outbound), and external automation apps can **trigger** phonectl
macros via an inbound intent surface (strategy §13.3, §6.4 intents, §12). Depends on **Plan 3.2**
(`IntentsProvider` — the existing intent send/broadcast surface it builds on), **Plan 2.2** (`risk` —
invoking external automations is high-risk and policy-gated), and **Plan 6.1/6.2** (the macro engine +
trigger surface the inbound path fires). It is **optional**: when no automation app is installed, the
outbound calls simply have no receiver, and the inbound surface is a no-op seam.

**Goal:** let phonectl and the user's existing Tasker/MacroDroid setup cooperate — a macro action can
"run Tasker task X with these vars," and a Tasker profile can "trigger phonectl macro Y" — **without**
phonectl embedding either app's engine, and **without** weakening the risk/policy model: outbound invokes
are high-risk and go through `runtime.run_action`; the inbound surface fires macros through the same
trigger/condition/autonomy gates as any other trigger. This plan ships the **Python-side interop seam +
the inbound trigger source + a design spec** for the companion intent receiver; the companion-side
`BroadcastReceiver`/intent wiring is built from the spec separately.

**Architecture:** a new `src/phonectl/providers/interop.py` (`InteropProvider`) that builds **outbound
intents** for known automation targets (Tasker `net.dinglisch.android.tasker`, MacroDroid
`com.arlosoft.macrodroid`) and dispatches them through the existing **`IntentsProvider`** (3.2) — so all
adb/intent plumbing is reused, not reinvented. It reports `interop_invoke` capability when an automation
package is installed (discovered via the 3.2 `PackagesProvider`). The **inbound** path adds a
`macro.external` trigger type and a daemon `interop_trigger` RPC: the companion intent receiver (design
spec) forwards an external intent → the daemon → a `macro.external` event on the Plan-5.2 bus → the
Plan-6.2 TriggerManager fires the matching macro. Outbound invoke verbs route through `runtime.run_action`.

**Tech Stack:** Python 3 (stdlib only: `json`); `pytest`. No new runtime dep. The companion intent receiver
is **design-spec only** here.

## Global Constraints

- **stdlib-only at runtime.** `json` — stdlib. All device I/O is delegated to the existing 3.2
  `IntentsProvider`/`PackagesProvider`.
- **Reuse, don't reinvent, the intent surface.** `InteropProvider` **builds** intent descriptors and hands
  them to `IntentsProvider.start`/`broadcast`; it never calls `adb`/`subprocess` itself (backend
  isolation).
- **Opt-in + runtime-discovered.** `interop_invoke` is reported only when a known automation package is
  installed (via `PackagesProvider`). Absent ⇒ all-false capabilities; the inbound surface is inert.
- **High-risk + policy-gated.** Invoking an external automation *leaves phonectl's control surface*, so the
  invoke verbs are classified **high** by `risk` (2.2) and gated by `run_action`. The inbound path fires
  macros through the **same** trigger/condition/autonomy gates as any trigger (6.2/6.3) — an external app
  cannot bypass the macro policy model.
- **Structured-result invariant.** Outbound invokes return `results.ok`/`err`; an absent target ⇒
  `errors.CapabilityUnavailableError` with an actionable `user_action`.
- **No new event source mechanism.** The inbound path emits a `macro.external` event onto the **existing**
  Plan-5.2 event bus; the TriggerManager (6.2) consumes it via the normal cursor poll.
- **Injectable seams.** `InteropProvider(intents=…, packages=…)`; the inbound RPC injects an event-bus
  publisher. `PHONECTL_HOME` isolation.
- **One commit per task. TDD order is non-negotiable.**

## Shared conventions established by this plan

- **New capability keys** (added to `capabilities.CAPABILITY_KEYS`): `interop_invoke`, `interop_trigger`.
- **Known targets:** `TARGETS = {"tasker": {...}, "macrodroid": {...}}` — package name + the intent
  action/extras shape each app expects to run a named task/macro (documented; values live in
  `interop.py`).
- **Outbound verbs:** `interop_invoke` (run a task/macro with variables). Mapped into the 2.2
  `HIGH_RISK_VERBS` set.
- **Inbound trigger type:** `macro.external` — a macro `trigger: {type: "macro.external", filters:
  {source: "tasker", name: "X"}}` fires when an external intent with that name/source arrives.
- **Inbound event envelope:** `{"seq", "type": "macro.external", "ts", "source": "interop",
  "data": {"source": "tasker"|"macrodroid", "name": str, "vars": dict}}` — published onto the 5.2 bus by
  the `interop_trigger` RPC.

---

### Task 1: Capability keys + `InteropProvider` discovery + outbound intent build

**Files:**
- Modify: `src/phonectl/capabilities.py`
- Create: `src/phonectl/providers/interop.py`
- Test: `tests/test_interop_provider.py`

**Interfaces:**
- New keys: `interop_invoke`, `interop_trigger`.
- `InteropProvider(*, intents, packages)` — `intents` is a 3.2 `IntentsProvider`, `packages` a 3.2
  `PackagesProvider` (both injectable).
- `is_available() -> bool` — at least one known automation package is installed (via
  `packages.is_installed`). `capabilities()` — `interop_invoke=True` when available, else all-false.
- `build_invoke_intent(target, name, variables) -> dict` — **pure**: build the intent descriptor (action,
  package, extras) for `target ∈ TARGETS`; raise `ValueError` for an unknown target.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_interop_provider.py
import pytest

from phonectl import capabilities as caps
from phonectl.providers.interop import InteropProvider, build_invoke_intent


class FakePackages:
    def __init__(self, installed):
        self._installed = set(installed)

    def is_installed(self, pkg):
        return pkg in self._installed


class FakeIntents:
    def __init__(self):
        self.sent = []

    def start(self, **kw):
        self.sent.append(("start", kw))
        return {"ok": True, "data": kw}

    def broadcast(self, **kw):
        self.sent.append(("broadcast", kw))
        return {"ok": True, "data": kw}


def test_capability_keys_exist():
    c = caps.make(interop_invoke=True, interop_trigger=True)
    assert c["interop_invoke"] and c["interop_trigger"]


def test_available_when_tasker_installed():
    p = InteropProvider(intents=FakeIntents(),
                        packages=FakePackages({"net.dinglisch.android.tasker"}))
    assert p.is_available() is True
    assert p.capabilities()["interop_invoke"] is True


def test_unavailable_when_no_automation_app():
    p = InteropProvider(intents=FakeIntents(), packages=FakePackages(set()))
    assert p.is_available() is False
    assert p.capabilities()["interop_invoke"] is False


def test_build_invoke_intent_for_tasker():
    desc = build_invoke_intent("tasker", "MorningRoutine", {"loc": "home"})
    assert desc["package"] == "net.dinglisch.android.tasker"
    assert desc["extras"]["task_name"] == "MorningRoutine"
    assert desc["extras"]["loc"] == "home"


def test_build_invoke_intent_unknown_target():
    with pytest.raises(ValueError):
        build_invoke_intent("ifttt", "X", {})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_interop_provider.py -v`
Expected: FAIL (`ValueError: unknown capability keys` / `ModuleNotFoundError`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/capabilities.py — append to CAPABILITY_KEYS
    # Phase 7.4 additions (automation-app interop)
    "interop_invoke",
    "interop_trigger",
```

```python
# src/phonectl/providers/interop.py
"""Optional Tasker/MacroDroid interop — outbound invoke via the 3.2 intent surface."""
from __future__ import annotations

from phonectl import capabilities as caps_mod

TARGETS = {
    "tasker": {
        "package": "net.dinglisch.android.tasker",
        "action": "net.dinglisch.android.tasker.ACTION_TASK",
        "name_extra": "task_name",
    },
    "macrodroid": {
        "package": "com.arlosoft.macrodroid",
        "action": "com.arlosoft.macrodroid.action.TRIGGER_MACRO",
        "name_extra": "macro_name",
    },
}


def build_invoke_intent(target, name, variables) -> dict:
    spec = TARGETS.get(target)
    if spec is None:
        raise ValueError(f"unknown interop target {target!r}")
    extras = {spec["name_extra"]: name}
    extras.update(variables or {})
    return {"action": spec["action"], "package": spec["package"], "extras": extras}


class InteropProvider:
    def __init__(self, *, intents, packages) -> None:
        self._intents = intents
        self._packages = packages

    def is_available(self) -> bool:
        return any(self._packages.is_installed(t["package"]) for t in TARGETS.values())

    def capabilities(self) -> dict:
        if not self.is_available():
            return caps_mod.make()
        return caps_mod.make(interop_invoke=True, interop_trigger=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_interop_provider.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/capabilities.py src/phonectl/providers/interop.py tests/test_interop_provider.py
git commit -m "feat: interop provider discovery + pure outbound invoke-intent builder"
```

---

### Task 2: Outbound `invoke` through `IntentsProvider` (high-risk)

**Files:**
- Modify: `src/phonectl/providers/interop.py`, `src/phonectl/risk.py`
- Test: `tests/test_interop_provider.py` (append), `tests/test_risk.py` (append)

**Interfaces:**
- `InteropProvider.invoke(target, name, variables=None, *, broadcast=False) -> dict` — build the intent and
  dispatch it via `intents.broadcast(...)` (default for Tasker/MacroDroid receivers) or `intents.start`;
  raise `errors.CapabilityUnavailableError` if unavailable. Returns the `IntentsProvider` result.
- `risk`: add `interop_invoke` to `HIGH_RISK_VERBS` (invoking an external automation leaves phonectl's
  control surface).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_interop_provider.py — append
from phonectl import errors


def test_invoke_dispatches_broadcast():
    intents = FakeIntents()
    p = InteropProvider(intents=intents, packages=FakePackages({"com.arlosoft.macrodroid"}))
    out = p.invoke("macrodroid", "Cleanup", {"deep": "true"})
    assert out["ok"] is True
    kind, kw = intents.sent[-1]
    assert kind == "broadcast"
    assert kw["package"] == "com.arlosoft.macrodroid"
    assert kw["extras"]["macro_name"] == "Cleanup"


def test_invoke_unavailable_raises():
    p = InteropProvider(intents=FakeIntents(), packages=FakePackages(set()))
    with pytest.raises(errors.CapabilityUnavailableError):
        p.invoke("tasker", "X", {})
```

```python
# tests/test_risk.py — append
def test_interop_invoke_is_high_risk():
    from phonectl import risk
    assert "interop_invoke" in risk.HIGH_RISK_VERBS
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_interop_provider.py tests/test_risk.py -v -k "invoke or interop"`
Expected: FAIL (`AttributeError: invoke` / verb not in set).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/providers/interop.py — append
from phonectl import errors


class InteropProvider:  # method added to the class above
    def invoke(self, target, name, variables=None, *, broadcast=True) -> dict:
        if not self.is_available():
            raise errors.CapabilityUnavailableError(
                "no supported automation app (Tasker/MacroDroid) is installed; "
                "install one to use interop invoke (see docs/interop.md).")
        desc = build_invoke_intent(target, name, variables or {})
        if broadcast:
            return self._intents.broadcast(action=desc["action"], package=desc["package"],
                                           extras=desc["extras"])
        return self._intents.start(action=desc["action"], package=desc["package"],
                                   extras=desc["extras"])
```

```python
# src/phonectl/risk.py — append
HIGH_RISK_VERBS = HIGH_RISK_VERBS | {"interop_invoke"}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_interop_provider.py tests/test_risk.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/providers/interop.py src/phonectl/risk.py tests/test_interop_provider.py tests/test_risk.py
git commit -m "feat: interop outbound invoke via IntentsProvider (high-risk, policy-gated)"
```

---

### Task 3: Inbound `macro.external` trigger matching

**Files:**
- Modify: `src/phonectl/macro/triggers.py`
- Test: `tests/test_macro_triggers.py` (append)

**Interfaces:**
- `triggers`: add `macro.external` to `EVENT_TYPES`; `matches` applies `filters.source` and `filters.name`
  against the event `data`. So a macro can subscribe to "Tasker fired the task named X."

- [ ] **Step 1: Write the failing test**

```python
# tests/test_macro_triggers.py — append
def test_macro_external_trigger_matches_source_and_name():
    spec = {"type": "macro.external", "filters": {"source": "tasker", "name": "Sync"}}
    ok_ev = _ev("macro.external", source="tasker", name="Sync")
    assert T.matches(spec, ok_ev) is True
    assert T.matches(spec, _ev("macro.external", source="tasker", name="Other")) is False
    assert T.matches(spec, _ev("macro.external", source="macrodroid", name="Sync")) is False


def test_macro_external_is_event_driven():
    assert T.is_event_driven({"type": "macro.external"})
```

(Note: the event envelope's `data` carries `source`/`name`; `_ev` here passes them as `data` kwargs.)

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_macro_triggers.py -v -k "external"`
Expected: FAIL (`TriggerError: unknown trigger type 'macro.external'`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/macro/triggers.py — add to EVENT_TYPES and the filter handling
EVENT_TYPES = EVENT_TYPES | {"macro.external"}
# in matches(), after the generic filters, add source/name handling:
    if "source" in f and data.get("source") != f["source"]:
        return False
    if "name" in f and data.get("name") != f["name"]:
        return False
```

(`source`/`name` are generic `data` filters; if the 6.2 `matches` already iterates a generic filter map,
extend that map instead of adding a special case — keep one filter path.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_macro_triggers.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/macro/triggers.py tests/test_macro_triggers.py
git commit -m "feat: macro.external inbound trigger type + source/name filters"
```

---

### Task 4: Daemon `interop_trigger` RPC — publish a `macro.external` event

**Files:**
- Modify: `src/phonectl/daemon/server.py`
- Test: `tests/test_daemon_server.py` (append)

**Interfaces:**
- `interop_trigger` RPC — params `{"source", "name", "vars"?}`; publishes a `macro.external` event onto the
  Plan-5.2 event bus (`{"type": "macro.external", "source": "interop", "data": {...}}`) and returns
  `results.ok(data={"published": True, "seq": <bus seq>})`. This is the endpoint the companion intent
  receiver (Task 5 spec) forwards external intents to; the 6.2 TriggerManager then fires matching macros.
- Gated so a daemon without the macro engine/event bus still returns a structured error rather than
  crashing.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_daemon_server.py — append
def test_interop_trigger_publishes_external_event(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    srv = _srv(tmp_path)
    published = []
    # inject the event-bus publisher seam the server uses
    srv._publish_event = lambda ev: published.append(ev) or 7
    resp = json.loads(srv.handle_line(_req("interop_trigger",
        {"source": "tasker", "name": "Sync", "vars": {"k": "v"}})))
    assert resp["ok"] is True and resp["data"]["published"] is True
    assert published[-1]["type"] == "macro.external"
    assert published[-1]["data"]["name"] == "Sync"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_daemon_server.py -v -k "interop"`
Expected: FAIL (`unknown_method`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/daemon/server.py — register handler; _publish_event delegates to the 5.2 EventBus
        @self.registry.register("interop_trigger")
        def _interop_trigger(params, ctx):
            ev = {"type": "macro.external", "source": "interop",
                  "data": {"source": params["source"], "name": params["name"],
                           "vars": params.get("vars", {})}}
            seq = self._publish_event(ev)
            return results.ok(capability="interop.trigger",
                              data={"published": True, "seq": seq})
```

(`self._publish_event` is the 5.2 `EventBus.publish` bound on the server; keep it a method/attribute so a
test can inject it. `interop_trigger` is a read/control method — it only publishes an event; the actual
macro run still goes through the TriggerManager + the macro policy/autonomy gates.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_daemon_server.py -v -k "interop"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/daemon/server.py tests/test_daemon_server.py
git commit -m "feat: daemon interop_trigger RPC — publish macro.external event for inbound automation"
```

---

### Task 5: `build_runtime` wiring + CLI + companion design spec + docs

**Files:**
- Modify: `src/phonectl/cli.py`, `src/phonectl/mcp_server.py`, `README.md`,
  `docs/superpowers/phonectl-platform-roadmap.md`,
  `docs/superpowers/plans/2026-06-22-phonectl-remaining-plans-meta-plan.md` (mark Phase 7 written)
- Create: `docs/superpowers/specs/2026-06-22-phonectl-automation-interop-design.md`, `docs/interop.md`
- Test: `tests/test_cli.py` (append), `tests/test_mcp_server.py` (append)

**Interfaces:**
- `cli._make_interop_provider(cfg)` builds an `InteropProvider` over the runtime's `IntentsProvider`/
  `PackagesProvider`; `build_runtime` registers it (it only adds `interop_invoke`/`interop_trigger`, so it
  never shadows core capabilities). No config flag needed — it self-discovers via installed packages.
- CLI: `phonectl interop invoke <target> <name> [--var K=V ...]` (routes through `run_action`),
  `phonectl interop trigger <source> <name>` (local helper that calls `interop_trigger`, useful for
  testing the inbound path). MCP: `phone.interop.invoke` on the 2.3 registry.
- Design spec: the companion `BroadcastReceiver` that forwards external intents → `interop_trigger`, the
  intent/extras contract for Tasker/MacroDroid in both directions, the trust model (which external sources
  may trigger which macros — enforced by the macro `filters` + autonomy gate), and the handoff to the
  Python seam (Tasks 1–4).
- Run the **full suite** on this final task.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli.py — append
def test_interop_provider_registered_when_automation_app_present(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: FakeBackend())
    # FakeBackend's PackagesProvider reports a Tasker install for this test
    reg = cli.build_runtime(config.load())
    caps = reg.capabilities()
    # interop_invoke present iff an automation package is discovered; assert the key exists
    assert "interop_invoke" in caps
```

```python
# tests/test_mcp_server.py — append
def test_interop_tool_registered():
    from phonectl import mcp_server
    assert "phone.interop.invoke" in mcp_server.TOOLS
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli.py tests/test_mcp_server.py -v -k "interop"`
Expected: FAIL (`AttributeError: _make_interop_provider` / tool missing).

- [ ] **Step 3: Write minimal implementation**

Add `cli._make_interop_provider(cfg)` + register it in `build_runtime`; add the `interop` CLI subparser
(`invoke` via `run_action`, `trigger` via `interop_trigger`); register `phone.interop.invoke` in the MCP
`TOOLS`. Write the companion design spec + `docs/interop.md` (both directions, the intent contracts, the
trust model: external sources can only fire macros that explicitly subscribe via `macro.external` filters,
still subject to the autonomy gate). Mark 7.4 / Phase 7 in the roadmap and the meta-plan tracker.

- [ ] **Step 4: Run the full suite, then commit**

Run: `pytest -v`
Expected: PASS (interop is additive + self-discovered; default builds are unchanged).

```bash
git add src/phonectl/cli.py src/phonectl/mcp_server.py docs/superpowers/specs/2026-06-22-phonectl-automation-interop-design.md docs/interop.md README.md docs/superpowers/phonectl-platform-roadmap.md docs/superpowers/plans/2026-06-22-phonectl-remaining-plans-meta-plan.md tests/test_cli.py tests/test_mcp_server.py
git commit -m "feat: interop build_runtime wiring + CLI + MCP + companion design spec; complete Phase 7"
```

---

## Dependencies

- **Plan 3.2** (`IntentsProvider`/`PackagesProvider` — outbound dispatch + discovery), **Plan 2.2** (`risk`
  high-risk set), **Plan 6.1/6.2** (macro engine + TriggerManager the inbound path fires through), **Plan
  5.2** (the event bus the `interop_trigger` RPC publishes onto). Opportunistic on **Plan 2.3** (MCP).

## Deferred / out of scope

- **The companion `BroadcastReceiver`** (Kotlin) that forwards external intents into `interop_trigger` —
  built from the Task-5 design spec separately; this plan ships the Python endpoint + the trigger type.
- **Tasker plugin (`.tasker` plugin protocol) / MacroDroid native plugin** — deeper plugin integration is a
  follow-up; this plan uses the documented intent/broadcast contracts only.
- **Bidirectional variable return** (a Tasker task returning values back into a phonectl macro variable) —
  needs a result-intent round-trip; deferred.

## Notes on testability

- `build_invoke_intent` and the trigger matcher are **pure** — fixture-tested.
- `InteropProvider` injects fake `intents`/`packages`; the daemon `interop_trigger` injects a fake event
  publisher — **no real intents, device, or socket** run in CI.
- **No device behavior is claimed.** Real Tasker/MacroDroid round-trips are a manual on-device smoke (note
  them in `docs/interop.md`; do not run them in CI).
