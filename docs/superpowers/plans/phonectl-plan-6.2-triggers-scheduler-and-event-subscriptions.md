# phonectl Macro Triggers, Scheduler & Event Subscriptions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Plan 6.2 of the platform roadmap** (`docs/superpowers/phonectl-platform-roadmap.md`). Second plan of
Phase 6. Implements the Phase-6.0 macro-engine design
(`docs/superpowers/specs/2026-06-22-phonectl-macro-engine-design.md`), locked decisions **D4, D5** and the
trigger/condition vocabulary of spec **§6**. Depends on **Plan 6.1** (`macro/schema.py`,
`macro/engine.py`, `macro/conditions.py` stub, `macro/records.py`), **Plan 5.2** (the daemon **event bus**
+ `events_poll(since, max)` cursor contract), and **Plan 5.1** (the `DaemonServer` + warm runtime). Reuses
**Plan 2.2**'s `ratelimit` sliding-window discipline for per-macro limits. **Plan 6.3** (autonomy + memory)
lands on top.

**Goal:** make macros run **without the agent polling** (roadmap §2). This plan ships: (1) a **pure
trigger matcher** (`triggers.matches(spec, event)`) over the Phase-5.2 event-bus envelopes and over
snapshots; (2) the **full pure condition vocabulary** (`conditions.evaluate(spec, ctx)`) the engine and
trigger gate use; (3) a **pure monotonic scheduler** (`scheduler.next_fire(spec, *, now)`) for
time/interval triggers — no cron dependency (D5); (4) a daemon **TriggerManager** that drains the event bus
cursor, matches each **registered** macro's trigger, enforces per-macro `max_runs_per_hour`/`cooldown`, and
enqueues a run through the Plan-6.1 `Engine`; (5) a daemon **scheduler thread** that fires time triggers;
and (6) `macro_enable/disable/list` RPC + CLI. Triggers add **no new event source** (D4) and the daemon
remains the only writer. Everything is additive — with no daemon, `phonectl macro run` (6.1) still works,
and the full suite stays green.

**Architecture:** new pure modules `src/phonectl/macro/triggers.py` (event/snapshot → bool) and
`src/phonectl/macro/scheduler.py` (`next_fire` over time specs), plus the completed
`src/phonectl/macro/conditions.py` (extends the 6.1 stub). A new `src/phonectl/macro/registry.py` persists
**enabled macros** to `$PHONECTL_HOME/macros/` (one JSON doc per enabled macro) and a pure
`macro/limits.py` (sliding-window `max_runs_per_hour` + `cooldown`, mirroring Plan 2.2 `ratelimit`). In the
daemon, `daemon/triggers.py` hosts a `TriggerManager` (drains `events_poll`, matches, gates on limits,
enqueues `Engine.run`) and a `Scheduler` thread (sleeps to `next_fire`, enqueues). Both are **readers** of
the event bus and take no writer lock; the macro action steps they enqueue take it via `run_action` (6.1
D3). `cli.py` gains `phonectl macro enable|disable|list`.

**Tech Stack:** Python 3 (stdlib only: `json`, `time`, `re`, `threading`, `datetime`); `pytest`. No new
runtime dep. The scheduler/manager threads are tested by driving their **step** methods synchronously (no
real threads, no real sockets, no wall-clock sleeps).

## Global Constraints

- **stdlib-only at runtime.** `json`, `time`, `re`, `threading`, `datetime` — all stdlib.
- **`triggers.py`, `conditions.py`, `scheduler.py`, `limits.py` are pure** (the `ui_parser` discipline):
  spec/event/context in, `bool`/timestamp out. No I/O, no `subprocess`, no sleep, no event-bus access
  inside the pure layer — the daemon supplies events/clock.
- **No new event source (D4).** Triggers consume the Plan-5.2 bus via `events_poll` only. The matcher never
  observes the device itself; it matches what the bus already carries.
- **No new writer.** The TriggerManager/Scheduler enqueue runs into the Plan-6.1 `Engine`, whose action
  steps go through `run_action` (single writer). Managers hold **no** write lock.
- **Monotonic scheduling (D5).** Sleeps use a monotonic clock (the Plan-1.3 `wait_for` discipline);
  `next_fire` is pure and tested with an injected `now`.
- **Structured-result invariant.** `macro_enable/disable/list` return `results.ok`/`results.err`
  envelopes; an auto-fired run yields the same `MacroRun`/`runs.jsonl` records 6.1 defined.
- **Per-macro limits + global rate ledger.** Auto-fires honor `max_runs_per_hour`/`cooldown` (this plan) and
  every action still passes the Plan-2.2 global rate ledger inside `run_action`.
- **Compatible evolution.** No daemon ⇒ no triggers/scheduler; the explicit `macro run` path is unchanged.
- **Injectable seams.** `TriggerManager(engine, *, poll=events_poll, registry=…, now=time.time,
  limits=…)`; `Scheduler(*, now=time.monotonic, sleep=…, enqueue=…)`. Tests inject fakes and isolate state
  via `monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))`.
- **One commit per task. TDD order is non-negotiable.**

## Shared conventions established by this plan

- **Trigger spec** (in a macro doc under `trigger`): `{"type": <trigger type>, "filters": {...}}`. Types
  (spec §6): `notification.posted`, `notification.removed`, `ui.element_appears`, `ui.element_disappears`,
  `ui.text_appears`, `app.opened`, `app.closed`, `activity.changed`, `clipboard.changed`,
  `power.charging_changed`, `power.battery_level`, `connectivity.wifi`, `schedule.time`,
  `schedule.interval`, `manual`. Event-driven types match event-bus envelopes; `schedule.*` go to the
  Scheduler; `manual` never auto-fires.
- **Event envelope** (from Plan 5.2): `{"seq", "type", "ts", "source", "data"}`. The matcher keys off
  `event["type"]` + `event["data"]` (e.g. `data.package`, `data.text`, `data.selector`).
- **Condition spec:** `{"type": <condition>, ...}` (spec §6 list). `conditions.evaluate(spec, ctx)` where
  `ctx = {"scopes", "snapshot", "device", "now"}`.
- **Enabled-macro store:** `$PHONECTL_HOME/macros/<name>.json` — the macro doc + `{"enabled": bool}`.
- **Limits store:** per-macro fire timestamps in `$PHONECTL_HOME/macro_runs_history.json` (reused by
  `limits.allow(name, macro, *, now, history)`), mirroring Plan 2.2's persisted rate history.
- **New additive error code (in `errors.py`):** `TriggerError` (code `"trigger_invalid"`,
  `requires_user=True`) for an unknown/invalid trigger or schedule spec.

---

### Task 1: Additive error + `macro/triggers.py` — pure event/snapshot matcher

**Files:**
- Modify: `src/phonectl/errors.py`
- Create: `src/phonectl/macro/triggers.py`
- Test: `tests/test_macro_triggers.py`

**Interfaces:**
- `errors.TriggerError` (code `"trigger_invalid"`, `requires_user=True`).
- `triggers.is_event_driven(spec) -> bool` / `triggers.is_scheduled(spec) -> bool` / `triggers.is_manual
  (spec) -> bool`.
- `triggers.matches(spec, event) -> bool` — **pure**: does this event-bus envelope satisfy the trigger?
  Validates the `type` is known (else `TriggerError`); applies `filters` (`package`/`package_in`,
  `text_regex`, `selector`, `min_percent`, `ssid`) against `event["data"]`. A `schedule.*`/`manual` spec
  never matches an event.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_macro_triggers.py
import pytest

from phonectl import errors
from phonectl.macro import triggers as T


def _ev(type_, **data):
    return {"seq": 1, "type": type_, "ts": 0.0, "source": "x", "data": data}


def test_classification():
    assert T.is_event_driven({"type": "notification.posted"})
    assert T.is_scheduled({"type": "schedule.time"})
    assert T.is_manual({"type": "manual"})
    assert not T.is_event_driven({"type": "schedule.time"})


def test_notification_package_in_filter():
    spec = {"type": "notification.posted", "filters": {"package_in": ["com.whatsapp", "org.signal"]}}
    assert T.matches(spec, _ev("notification.posted", package="com.whatsapp")) is True
    assert T.matches(spec, _ev("notification.posted", package="com.other")) is False


def test_text_regex_filter():
    spec = {"type": "notification.posted", "filters": {"text_regex": "urgent|asap"}}
    assert T.matches(spec, _ev("notification.posted", text="this is URGENT")) is True
    assert T.matches(spec, _ev("notification.posted", text="hello")) is False


def test_type_mismatch_is_false():
    spec = {"type": "ui.text_appears", "filters": {}}
    assert T.matches(spec, _ev("notification.posted")) is False


def test_scheduled_spec_never_matches_event():
    assert T.matches({"type": "schedule.time", "at": "08:00"}, _ev("notification.posted")) is False


def test_unknown_trigger_type_raises():
    with pytest.raises(errors.TriggerError):
        T.matches({"type": "telepathy"}, _ev("notification.posted"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_macro_triggers.py -v`
Expected: FAIL (`ModuleNotFoundError: phonectl.macro.triggers`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/errors.py — append
class TriggerError(PhonectlError):
    code = "trigger_invalid"
    requires_user = True
```

```python
# src/phonectl/macro/triggers.py
"""Pure trigger matching: event-bus envelope / snapshot -> bool."""
from __future__ import annotations

import re

from phonectl import errors

EVENT_TYPES = {
    "notification.posted", "notification.removed", "ui.element_appears",
    "ui.element_disappears", "ui.text_appears", "app.opened", "app.closed",
    "activity.changed", "clipboard.changed", "power.charging_changed",
    "power.battery_level", "connectivity.wifi",
}
SCHEDULE_TYPES = {"schedule.time", "schedule.interval"}
ALL_TYPES = EVENT_TYPES | SCHEDULE_TYPES | {"manual"}


def _check(spec):
    t = spec.get("type")
    if t not in ALL_TYPES:
        raise errors.TriggerError(f"unknown trigger type {t!r}")
    return t


def is_event_driven(spec):
    return _check(spec) in EVENT_TYPES


def is_scheduled(spec):
    return _check(spec) in SCHEDULE_TYPES


def is_manual(spec):
    return _check(spec) == "manual"


def matches(spec, event) -> bool:
    t = _check(spec)
    if t not in EVENT_TYPES or event.get("type") != t:
        return False
    data = event.get("data", {}) or {}
    f = spec.get("filters", {}) or {}
    if "package" in f and data.get("package") != f["package"]:
        return False
    if "package_in" in f and data.get("package") not in f["package_in"]:
        return False
    if "text_regex" in f and not re.search(f["text_regex"], data.get("text", "") or "", re.I):
        return False
    if "selector" in f and data.get("selector") != f["selector"]:
        return False
    if "min_percent" in f and not (data.get("percent", 0) <= f["min_percent"]):
        return False
    if "ssid" in f and data.get("ssid") != f["ssid"]:
        return False
    return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_macro_triggers.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/errors.py src/phonectl/macro/triggers.py tests/test_macro_triggers.py
git commit -m "feat: pure macro trigger matcher + trigger_invalid error"
```

---

### Task 2: `macro/conditions.py` — full pure condition vocabulary

**Files:**
- Modify: `src/phonectl/macro/conditions.py` (extend the 6.1 stub)
- Test: `tests/test_macro_conditions.py`

**Interfaces:**
- `conditions.evaluate(spec, ctx) -> bool` — **pure**. `ctx = {"scopes", "snapshot", "device", "now"}`.
  Adds (to the 6.1 `always`/`never`/`variable`): `foreground_package` (snapshot `app`), `screen_contains`
  (regex over snapshot element texts), `selector_exists` (reuse the Plan-1.2 selector resolver over the
  snapshot), `device_unlocked` (snapshot `lock_state`), `battery_min`/`charging` (device map),
  `wifi_ssid`, `time_window` (`after`/`before` vs `ctx["now"]`), `risk_below` (reuse `risk.classify`),
  `last_action_ok` (scopes flag), `network_available`. Unknown type → `TriggerError`.
- `conditions.all_hold(spec_list, ctx) -> bool` — every condition holds (the macro `conditions` gate).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_macro_conditions.py
import pytest

from phonectl import errors
from phonectl.macro import conditions as C
from phonectl.macro import variables as V


def _ctx(**kw):
    base = {"scopes": V.Scopes(), "snapshot": {}, "device": {}, "now": None}
    base.update(kw)
    return base


def test_foreground_package():
    ctx = _ctx(snapshot={"app": "com.example"})
    assert C.evaluate({"type": "foreground_package", "equals": "com.example"}, ctx) is True
    assert C.evaluate({"type": "foreground_package", "equals": "com.other"}, ctx) is False


def test_battery_min_and_charging():
    ctx = _ctx(device={"battery": 40, "charging": True})
    assert C.evaluate({"type": "battery_min", "percent": 15}, ctx) is True
    assert C.evaluate({"type": "battery_min", "percent": 80}, ctx) is False
    assert C.evaluate({"type": "charging"}, ctx) is True


def test_screen_contains():
    ctx = _ctx(snapshot={"elements": [{"text": "Send code"}, {"text": "Cancel"}]})
    assert C.evaluate({"type": "screen_contains", "text_regex": "send"}, ctx) is True
    assert C.evaluate({"type": "screen_contains", "text_regex": "delete"}, ctx) is False


def test_all_hold():
    ctx = _ctx(snapshot={"app": "com.example"}, device={"battery": 50})
    specs = [{"type": "foreground_package", "equals": "com.example"},
             {"type": "battery_min", "percent": 15}]
    assert C.all_hold(specs, ctx) is True


def test_unknown_condition_raises():
    with pytest.raises(errors.TriggerError):
        C.evaluate({"type": "vibes"}, _ctx())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_macro_conditions.py -v`
Expected: FAIL (`NotImplementedError`/`TriggerError` from the 6.1 stub for the new types).

- [ ] **Step 3: Write minimal implementation**

Extend `conditions.evaluate` with the full vocabulary (reusing `selectors` from 1.2 and `risk` from 2.2
where noted), raising `errors.TriggerError` for an unknown type, and add `all_hold`. Keep it pure:

```python
# src/phonectl/macro/conditions.py — replace the stub body
from __future__ import annotations

import re

from phonectl import errors
from phonectl.macro import variables as V

_OPS = {"eq": lambda a, b: a == b, "ne": lambda a, b: a != b,
        "lt": lambda a, b: a < b, "gt": lambda a, b: a > b}


def evaluate(spec, ctx) -> bool:
    t = spec.get("type")
    snap = ctx.get("snapshot") or {}
    dev = ctx.get("device") or {}
    scopes = ctx.get("scopes")
    if t == "always":
        return True
    if t == "never":
        return False
    if t == "variable":
        return _OPS[spec.get("op", "eq")](scopes.get(spec["var"]), spec.get("value"))
    if t == "foreground_package":
        return snap.get("app") == spec.get("equals")
    if t == "screen_contains":
        texts = " ".join(e.get("text", "") or "" for e in snap.get("elements", []))
        return bool(re.search(spec["text_regex"], texts, re.I))
    if t == "selector_exists":
        from phonectl import selectors
        return bool(selectors.find(snap, spec["selector"]))
    if t == "device_unlocked":
        return snap.get("lock_state", "unlocked") == "unlocked"
    if t == "battery_min":
        return dev.get("battery", 0) >= spec["percent"]
    if t == "charging":
        return bool(dev.get("charging"))
    if t == "wifi_ssid":
        return dev.get("ssid") == spec.get("equals")
    if t == "network_available":
        return bool(dev.get("network", True))
    if t == "time_window":
        return _in_window(ctx.get("now"), spec.get("after"), spec.get("before"))
    if t == "risk_below":
        from phonectl import risk
        order = ["low", "medium", "high", "critical"]
        level = risk.classify(snap, spec.get("action", {})).get("risk_level", "low")
        return order.index(level) < order.index(spec["level"])
    if t == "last_action_ok":
        return bool(scopes.get("__last_action_ok__", True))
    raise errors.TriggerError(f"unknown condition type {t!r}")


def all_hold(spec_list, ctx) -> bool:
    return all(evaluate(s, ctx) for s in (spec_list or []))


def _in_window(now, after, before):
    if now is None or after is None or before is None:
        return True
    hm = now.strftime("%H:%M") if hasattr(now, "strftime") else str(now)
    return after <= hm <= before
```

(If `selectors.find`/`risk.classify` have different shipped names, call those — do not introduce a second
resolver/classifier.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_macro_conditions.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/macro/conditions.py tests/test_macro_conditions.py
git commit -m "feat: full pure macro condition vocabulary + all_hold gate"
```

---

### Task 3: `macro/scheduler.py` — pure monotonic `next_fire`

**Files:**
- Create: `src/phonectl/macro/scheduler.py`
- Test: `tests/test_macro_scheduler.py`

**Interfaces:**
- `scheduler.next_fire(spec, *, now) -> float | None` — **pure**: seconds-from-`now` until the next fire for
  a `schedule.time` (daily `at: "HH:MM"`, optional `weekdays: [0..6]`) or `schedule.interval`
  (`every_seconds`). `now` is a `datetime`. Returns the delay (≥ 0) or `None` for a non-schedule spec.
- `scheduler.validate(spec) -> list[str]` — schedule-spec validation errors (bad time, empty interval).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_macro_scheduler.py
from datetime import datetime

from phonectl.macro import scheduler as S


def test_interval_next_fire():
    assert S.next_fire({"type": "schedule.interval", "every_seconds": 300},
                       now=datetime(2026, 6, 22, 12, 0, 0)) == 300


def test_time_today_later():
    # 12:00 now, fire at 18:30 → 6h30m = 23400s
    d = S.next_fire({"type": "schedule.time", "at": "18:30"}, now=datetime(2026, 6, 22, 12, 0, 0))
    assert d == 23400


def test_time_already_passed_rolls_to_tomorrow():
    d = S.next_fire({"type": "schedule.time", "at": "08:00"}, now=datetime(2026, 6, 22, 12, 0, 0))
    assert d == (24 - 4) * 3600  # 20h


def test_non_schedule_is_none():
    assert S.next_fire({"type": "notification.posted"}, now=datetime(2026, 6, 22, 12, 0, 0)) is None


def test_validate_bad_time():
    assert S.validate({"type": "schedule.time", "at": "99:99"})
    assert S.validate({"type": "schedule.interval", "every_seconds": 0})
    assert S.validate({"type": "schedule.time", "at": "08:00"}) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_macro_scheduler.py -v`
Expected: FAIL (`ModuleNotFoundError: phonectl.macro.scheduler`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/macro/scheduler.py
"""Pure schedule math: seconds until the next fire (no real clock, no sleep)."""
from __future__ import annotations

from datetime import timedelta


def _parse_hm(at):
    h, m = at.split(":")
    h, m = int(h), int(m)
    if not (0 <= h < 24 and 0 <= m < 60):
        raise ValueError(at)
    return h, m


def validate(spec) -> list:
    t = spec.get("type")
    if t == "schedule.time":
        try:
            _parse_hm(spec.get("at", ""))
        except (ValueError, AttributeError):
            return [f"invalid schedule.time 'at': {spec.get('at')!r}"]
        return []
    if t == "schedule.interval":
        if not isinstance(spec.get("every_seconds"), (int, float)) or spec["every_seconds"] <= 0:
            return ["schedule.interval requires a positive 'every_seconds'"]
        return []
    return []


def next_fire(spec, *, now):
    t = spec.get("type")
    if t == "schedule.interval":
        return float(spec["every_seconds"])
    if t != "schedule.time":
        return None
    h, m = _parse_hm(spec["at"])
    target = now.replace(hour=h, minute=m, second=0, microsecond=0)
    weekdays = spec.get("weekdays")
    if target <= now:
        target = target + timedelta(days=1)
    if weekdays:
        for _ in range(8):
            if target.weekday() in weekdays and target > now:
                break
            target = target + timedelta(days=1)
    return (target - now).total_seconds()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_macro_scheduler.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/macro/scheduler.py tests/test_macro_scheduler.py
git commit -m "feat: pure monotonic schedule next_fire + schedule-spec validation"
```

---

### Task 4: `macro/limits.py` — per-macro `max_runs_per_hour` + `cooldown`

**Files:**
- Create: `src/phonectl/macro/limits.py`
- Test: `tests/test_macro_limits.py`

**Interfaces:**
- `limits.allow(name, macro_limits, *, now, history) -> (bool, reason)` — **pure** sliding-window
  (mirrors Plan 2.2 `ratelimit`): denies if the last fire was within `cooldown_seconds`, or if fires in the
  trailing hour ≥ `max_runs_per_hour`. `history` is the list of prior fire timestamps for `name`.
- `limits.record(name, *, now, store_path) -> None` / `limits.load(store_path) -> dict` — persist fire
  timestamps to `$PHONECTL_HOME/macro_runs_history.json` (pruned to the trailing hour).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_macro_limits.py
from phonectl.macro import limits as L


def test_cooldown_blocks():
    ok, reason = L.allow("m", {"cooldown_seconds": 300}, now=1000.0, history=[900.0])
    assert ok is False and "cooldown" in reason


def test_cooldown_passes_after_window():
    ok, _ = L.allow("m", {"cooldown_seconds": 300}, now=1300.0, history=[900.0])
    assert ok is True


def test_max_runs_per_hour():
    hist = [3600.0 + i for i in range(5)]  # 5 fires in the last hour
    ok, reason = L.allow("m", {"max_runs_per_hour": 5}, now=3700.0, history=hist)
    assert ok is False and "per_hour" in reason


def test_no_limits_allows():
    ok, _ = L.allow("m", {}, now=1.0, history=[])
    assert ok is True


def test_record_and_load_roundtrip(tmp_path):
    p = tmp_path / "h.json"
    L.record("m", now=10.0, store_path=p)
    L.record("m", now=20.0, store_path=p)
    assert L.load(p)["m"] == [10.0, 20.0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_macro_limits.py -v`
Expected: FAIL (`ModuleNotFoundError: phonectl.macro.limits`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/macro/limits.py
"""Pure per-macro fire limits (cooldown + max_runs_per_hour), persisted history."""
from __future__ import annotations

import json


def allow(name, macro_limits, *, now, history):
    macro_limits = macro_limits or {}
    history = history or []
    cooldown = macro_limits.get("cooldown_seconds")
    if cooldown and history and (now - max(history)) < cooldown:
        return False, "cooldown"
    per_hour = macro_limits.get("max_runs_per_hour")
    if per_hour is not None:
        recent = [t for t in history if now - t < 3600]
        if len(recent) >= per_hour:
            return False, "per_hour"
    return True, ""


def load(store_path) -> dict:
    try:
        return json.loads(store_path.read_text())
    except (FileNotFoundError, ValueError):
        return {}


def record(name, *, now, store_path) -> None:
    data = load(store_path)
    hist = [t for t in data.get(name, []) if now - t < 3600]
    hist.append(now)
    data[name] = hist
    store_path.write_text(json.dumps(data))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_macro_limits.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/macro/limits.py tests/test_macro_limits.py
git commit -m "feat: pure per-macro fire limits (cooldown + max_runs_per_hour) + persisted history"
```

---

### Task 5: `macro/registry.py` — enabled-macro persistence

**Files:**
- Create: `src/phonectl/macro/registry.py`
- Test: `tests/test_macro_registry.py`

**Interfaces:**
- `registry.enable(doc) -> None` — validate (`schema.validate`) + persist to
  `$PHONECTL_HOME/macros/<name>.json` with `enabled=True`; raise `MacroValidationError` on bad docs and
  `TriggerError`/scheduler `validate` on a bad trigger/schedule.
- `registry.disable(name) -> None` — set `enabled=False` (keep the doc for re-enable).
- `registry.list_enabled() -> list[Macro]` — parsed enabled macros (skips manual-only/disabled).
- `registry.all() -> list[dict]` — every stored macro + state (for `macro_list`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_macro_registry.py
import pytest

from phonectl import errors
from phonectl.macro import registry


def _doc(name="m"):
    return {"name": name, "trigger": {"type": "notification.posted",
            "filters": {"package_in": ["com.x"]}},
            "actions": [{"type": "tap", "target": {"i": 0}}]}


def test_enable_then_list(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    registry.enable(_doc("a"))
    names = [m.name for m in registry.list_enabled()]
    assert names == ["a"]


def test_disable_removes_from_enabled(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    registry.enable(_doc("a"))
    registry.disable("a")
    assert registry.list_enabled() == []
    assert any(m["name"] == "a" and not m["enabled"] for m in registry.all())


def test_enable_rejects_bad_trigger(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    bad = {"name": "b", "trigger": {"type": "telepathy"}, "actions": []}
    with pytest.raises(errors.TriggerError):
        registry.enable(bad)


def test_enable_rejects_invalid_schedule(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    bad = {"name": "s", "trigger": {"type": "schedule.time", "at": "99:99"}, "actions": []}
    with pytest.raises(errors.MacroValidationError):
        registry.enable(bad)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_macro_registry.py -v`
Expected: FAIL (`ModuleNotFoundError: phonectl.macro.registry`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/macro/registry.py
"""Persistence for enabled macros: $PHONECTL_HOME/macros/<name>.json."""
from __future__ import annotations

import json

from phonectl import errors
from phonectl.config import config_dir
from phonectl.macro import schema, scheduler, triggers


def _dir():
    d = config_dir() / "macros"
    d.mkdir(parents=True, exist_ok=True)
    return d


def enable(doc) -> None:
    macro = schema.parse(doc)  # raises MacroValidationError
    if macro.trigger is not None:
        triggers._check(macro.trigger)  # raises TriggerError on unknown type
        if triggers.is_scheduled(macro.trigger):
            errs = scheduler.validate(macro.trigger)
            if errs:
                raise errors.MacroValidationError("; ".join(errs))
    (_dir() / f"{macro.name}.json").write_text(json.dumps({**doc, "enabled": True}))


def disable(name) -> None:
    p = _dir() / f"{name}.json"
    if p.exists():
        doc = json.loads(p.read_text())
        doc["enabled"] = False
        p.write_text(json.dumps(doc))


def all() -> list:
    out = []
    for p in sorted(_dir().glob("*.json")):
        out.append(json.loads(p.read_text()))
    return out


def list_enabled() -> list:
    macros = []
    for doc in all():
        if doc.get("enabled") and doc.get("trigger") and not triggers.is_manual(doc["trigger"]):
            macros.append(schema.parse(doc))
    return macros
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_macro_registry.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/macro/registry.py tests/test_macro_registry.py
git commit -m "feat: enabled-macro registry (validate trigger/schedule on enable)"
```

---

### Task 6: `daemon/triggers.py` — `TriggerManager.step()` (drain bus → match → gate → enqueue)

**Files:**
- Create: `src/phonectl/daemon/triggers.py`
- Test: `tests/test_daemon_trigger_manager.py`

**Interfaces:**
- `TriggerManager(engine, *, poll, registry=registry_mod, limits=limits_mod, now=time.time,
  history_path=…)` — `poll(since, max)` is the Plan-5.2 `events_poll` (injected as a fake in tests);
  `engine` is the Plan-6.1 `Engine`.
- `TriggerManager.step() -> list[str]` — drain new events since the cursor; for each event, for each
  enabled macro whose `trigger` matches and whose `conditions.all_hold` pass and whose `limits.allow`
  permits, **enqueue** `engine.run(macro, trigger=<event type>, ...)`; record the fire; advance the cursor.
  Returns the list of macro names fired.
- The manager is a **reader**: it never takes the write lock; the enqueued `engine.run` takes it per action
  via `run_action`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_daemon_trigger_manager.py
from phonectl.daemon.triggers import TriggerManager
from phonectl.macro import registry


class FakeEngine:
    def __init__(self):
        self.runs = []

    def run(self, macro, **kw):
        self.runs.append((macro.name, kw.get("trigger")))
        return {"ok": True, "data": {"run_id": "run_x"}}


def _poll_factory(events):
    def poll(since, max_):
        batch = [e for e in events if e["seq"] > since]
        cursor = max([e["seq"] for e in batch], default=since)
        return {"events": batch, "cursor": cursor}
    return poll


def _ev(seq, type_, **data):
    return {"seq": seq, "type": type_, "ts": 0.0, "source": "x", "data": data}


def test_matching_event_fires_macro(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    registry.enable({"name": "reply", "trigger": {"type": "notification.posted",
                     "filters": {"package_in": ["com.x"]}},
                     "actions": [{"type": "tap", "target": {"i": 0}}]})
    eng = FakeEngine()
    mgr = TriggerManager(eng, poll=_poll_factory([_ev(1, "notification.posted", package="com.x")]),
                         now=lambda: 100.0)
    fired = mgr.step()
    assert fired == ["reply"] and eng.runs == [("reply", "notification.posted")]


def test_non_matching_event_does_not_fire(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    registry.enable({"name": "reply", "trigger": {"type": "notification.posted",
                     "filters": {"package_in": ["com.x"]}}, "actions": []})
    eng = FakeEngine()
    mgr = TriggerManager(eng, poll=_poll_factory([_ev(1, "notification.posted", package="com.other")]),
                         now=lambda: 100.0)
    assert mgr.step() == [] and eng.runs == []


def test_cooldown_suppresses_second_fire(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    registry.enable({"name": "reply", "trigger": {"type": "clipboard.changed"},
                     "limits": {"cooldown_seconds": 300},
                     "actions": [{"type": "tap", "target": {"i": 0}}]})
    eng = FakeEngine()
    events = [_ev(1, "clipboard.changed"), _ev(2, "clipboard.changed")]
    mgr = TriggerManager(eng, poll=_poll_factory(events), now=lambda: 100.0)
    fired = mgr.step()
    assert fired == ["reply"]  # second event within cooldown is suppressed
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_daemon_trigger_manager.py -v`
Expected: FAIL (`ModuleNotFoundError: phonectl.daemon.triggers`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/daemon/triggers.py
"""Daemon TriggerManager: drain the event bus, match macros, gate, enqueue runs."""
from __future__ import annotations

import time

from phonectl.config import config_dir
from phonectl.macro import conditions as conditions_mod
from phonectl.macro import limits as limits_mod
from phonectl.macro import registry as registry_mod
from phonectl.macro import triggers as triggers_mod
from phonectl.macro import variables as V


class TriggerManager:
    def __init__(self, engine, *, poll, registry=registry_mod, limits=limits_mod,
                 conditions=conditions_mod, now=time.time, history_path=None):
        self._engine = engine
        self._poll = poll
        self._registry = registry
        self._limits = limits
        self._conditions = conditions
        self._now = now
        self._cursor = 0
        self._history_path = history_path or (config_dir() / "macro_runs_history.json")

    def step(self) -> list:
        batch = self._poll(self._cursor, 100)
        self._cursor = batch.get("cursor", self._cursor)
        fired = []
        macros = self._registry.list_enabled()
        for event in batch.get("events", []):
            for macro in macros:
                if not triggers_mod.is_event_driven(macro.trigger):
                    continue
                if not triggers_mod.matches(macro.trigger, event):
                    continue
                ctx = {"scopes": V.Scopes(macro=dict(macro.variables)),
                       "snapshot": event.get("data", {}).get("snapshot", {}),
                       "device": event.get("data", {}).get("device", {}), "now": None}
                if not self._conditions.all_hold(macro.conditions, ctx):
                    continue
                now = self._now()
                hist = self._limits.load(self._history_path).get(macro.name, [])
                ok, _ = self._limits.allow(macro.name, macro.limits, now=now, history=hist)
                if not ok:
                    continue
                self._limits.record(macro.name, now=now, store_path=self._history_path)
                self._engine.run(macro, trigger=event["type"],
                                 scopes=V.Scopes(macro=dict(macro.variables),
                                                 trigger=event.get("data", {})))
                fired.append(macro.name)
        return fired
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_daemon_trigger_manager.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/daemon/triggers.py tests/test_daemon_trigger_manager.py
git commit -m "feat: daemon TriggerManager.step — drain bus, match macros, gate on conditions+limits, enqueue"
```

---

### Task 7: `daemon` Scheduler step + `macro_enable/disable/list` RPC + CLI + docs

**Files:**
- Modify: `src/phonectl/daemon/triggers.py` (add `Scheduler`), `src/phonectl/daemon/server.py`
  (RPC + wire the manager/scheduler into the poller loop as a reader), `src/phonectl/cli.py`,
  `docs/macros.md`, `docs/superpowers/phonectl-platform-roadmap.md`
- Test: `tests/test_daemon_scheduler.py`, `tests/test_daemon_server.py` (append), `tests/test_cli.py`
  (append)

**Interfaces:**
- `Scheduler(engine, *, registry=registry_mod, next_fire=scheduler.next_fire, now=datetime.now,
  enqueue=None)` — `due(now_dt) -> list[str]` returns the scheduled macro names whose `next_fire` ≤ 0 at
  `now_dt` and enqueues them (pure decision + injected `now`; no real sleep in tests).
- Daemon RPC: `macro_enable` (`registry.enable(params["macro"])`), `macro_disable`
  (`registry.disable(params["name"])`), `macro_list` (`registry.all()` + recent runs). `macro_enable`/
  `macro_disable` are control methods.
- Daemon wiring: the existing Plan-5.2 poller thread, after draining provider events into the bus, calls
  `TriggerManager.step()` and `Scheduler.due(...)` — both **readers** (no writer lock). Gated so the daemon
  runs fine if 6.x isn't installed.
- CLI: `phonectl macro enable <file>`, `phonectl macro disable <name>`, `phonectl macro list` (route via
  `_dispatch`; in-process for enable/disable/list since they only touch the registry store).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_daemon_scheduler.py
from datetime import datetime

from phonectl.daemon.triggers import Scheduler
from phonectl.macro import registry


class FakeEngine:
    def __init__(self):
        self.runs = []

    def run(self, macro, **kw):
        self.runs.append(macro.name)
        return {"ok": True, "data": {"run_id": "r"}}


def test_due_fires_interval_macro(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    registry.enable({"name": "tick", "trigger": {"type": "schedule.interval", "every_seconds": 0.0001},
                     "actions": [{"type": "tap", "target": {"i": 0}}]})
    eng = FakeEngine()
    sch = Scheduler(eng)
    # First due() arms; after the tiny interval the macro is due.
    sch.due(datetime(2026, 6, 22, 12, 0, 0))
    fired = sch.due(datetime(2026, 6, 22, 12, 0, 1))
    assert "tick" in fired and "tick" in eng.runs
```

```python
# tests/test_daemon_server.py — append
def test_macro_enable_disable_list(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    srv = _srv(tmp_path)
    doc = {"name": "m", "trigger": {"type": "clipboard.changed"},
           "actions": [{"type": "tap", "target": {"i": 0}}]}
    assert json.loads(srv.handle_line(_req("macro_enable", {"macro": doc})))["ok"] is True
    listed = json.loads(srv.handle_line(_req("macro_list")))
    assert any(m["name"] == "m" and m["enabled"] for m in listed["data"]["macros"])
    assert json.loads(srv.handle_line(_req("macro_disable", {"name": "m"})))["ok"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_daemon_scheduler.py tests/test_daemon_server.py -v -k "due or enable_disable"`
Expected: FAIL (`ImportError: Scheduler` / `unknown_method`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/daemon/triggers.py — add
from datetime import datetime

from phonectl.macro import scheduler as scheduler_mod


class Scheduler:
    def __init__(self, engine, *, registry=registry_mod, next_fire=scheduler_mod.next_fire,
                 now=datetime.now):
        self._engine = engine
        self._registry = registry
        self._next_fire = next_fire
        self._now = now
        self._armed = {}  # name -> absolute fire time (seconds, via timestamp)

    def due(self, now_dt=None) -> list:
        now_dt = now_dt or self._now()
        fired = []
        for macro in self._registry.list_enabled():
            if not (macro.trigger and macro.trigger.get("type", "").startswith("schedule")):
                continue
            delay = self._next_fire(macro.trigger, now=now_dt)
            if delay is None:
                continue
            armed = self._armed.get(macro.name)
            target = now_dt.timestamp() + delay
            if armed is None:
                self._armed[macro.name] = target
                continue
            if now_dt.timestamp() >= armed:
                self._engine.run(macro, trigger=macro.trigger["type"])
                self._armed[macro.name] = now_dt.timestamp() + (delay or 0)
                fired.append(macro.name)
        return fired
```

```python
# src/phonectl/daemon/server.py — register control/read methods
        from phonectl.macro import registry as _mreg

        @self.registry.register("macro_enable")
        def _macro_enable(params, ctx):
            _mreg.enable(params["macro"])
            return results.ok(capability="macro.enable", data={"enabled": True})

        @self.registry.register("macro_disable")
        def _macro_disable(params, ctx):
            _mreg.disable(params["name"])
            return results.ok(capability="macro.disable", data={"enabled": False})

        @self.registry.register("macro_list")
        def _macro_list(params, ctx):
            return results.ok(capability="macro.list", data={"macros": _mreg.all()})
```

Wire `TriggerManager.step()` + `Scheduler.due()` into the Plan-5.2 poller loop (after the bus drain), each
inside a `try/except` so a macro-engine error never wedges the daemon's event loop. Document the trigger
vocabulary, the scheduler, limits, and `enable/disable/list` in `docs/macros.md`; mark 6.2 in the roadmap.

- [ ] **Step 4: Run test to verify it passes, then the full suite**

Run: `pytest tests/test_daemon_scheduler.py tests/test_daemon_server.py tests/test_cli.py -v`
Then: `pytest -v`
Expected: PASS (no regression; triggers/scheduler are additive and the no-daemon path is untouched).

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/daemon/triggers.py src/phonectl/daemon/server.py src/phonectl/cli.py docs/macros.md docs/superpowers/phonectl-platform-roadmap.md tests/test_daemon_scheduler.py tests/test_daemon_server.py tests/test_cli.py
git commit -m "feat: daemon Scheduler + macro_enable/disable/list RPC + CLI; triggers/scheduler in poller loop"
```

---

## Dependencies

- **Plan 6.1** — `schema`, `engine.Engine`, `conditions` (stub extended here), `records`, `variables`.
- **Plan 5.2** — the event bus + `events_poll(since, max)` cursor contract the TriggerManager drains, and
  the poller thread the manager/scheduler hook into.
- **Plan 5.1** — `DaemonServer` + warm runtime (the engine acts through it).
- **Plan 2.2** — `ratelimit` discipline mirrored by `macro/limits.py`; `risk.classify` reused by the
  `risk_below` condition. **Plan 1.2** — `selectors.find` reused by `selector_exists`.

## Deferred (out of scope for 6.2)

- **Progressive autonomy gate + memory layer** → Plan **6.3**.
- **`race`/`wait until` against live event conditions** inside the engine — the engine's `race` step (6.1)
  resolves immediately; a live-event race needs the bus and lands opportunistically in 6.3 or a follow-up.
- **Geofence/NFC/SMS/call triggers** (strategy §12.1) — require companion/permissioned sources; deferred to
  Phase 7 providers + a follow-up trigger source.

## Notes on testability

- `triggers`, `conditions`, `scheduler`, `limits` are **pure** — fixture-tested, no I/O, no real clock.
- `TriggerManager`/`Scheduler` are tested via their synchronous `step()`/`due()` with an **injected** `poll`
  (a fake `events_poll`) and an **injected** `now` — **no real threads, sockets, or sleeps**.
- `PHONECTL_HOME` isolation keeps the macro registry + fire-history per-test.
- **No device behavior is claimed.** A real trigger firing against the phone is a manual on-device smoke
  (note it in `docs/macros.md`; do not run it in CI).
