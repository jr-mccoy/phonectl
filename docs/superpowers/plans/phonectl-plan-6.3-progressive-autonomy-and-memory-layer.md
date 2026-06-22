# phonectl Progressive Autonomy & Memory Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Plan 6.3 of the platform roadmap** (`docs/superpowers/phonectl-platform-roadmap.md`). Final plan of
Phase 6. Implements the Phase-6.0 macro-engine design
(`docs/superpowers/specs/2026-06-22-phonectl-macro-engine-design.md`), locked decisions **D10, D11, D12**.
Depends on **Plan 6.1** (`macro/engine.py` per-action gate seam, `macro/records.py`), **Plan 6.2**
(`registry`, `TriggerManager` — the unattended path autonomy gates), **Plan 2.2** (`policy.explain`/`risk`
the gate consults), and **Plan 2.1** (`redact.py` — every stored value is redacted). This is the plan that
makes unattended automation **safe and reversible**.

**Goal:** let a user **graduate a recipe from confirm/dry-run to unattended** with full audit, inspect, and
revoke (strategy §18, §2), and give the platform a **narrow, user-controlled memory** that makes recipes
more robust over time (strategy §25). Concretely: (1) an append-only **autonomy grant ledger**
(`autonomy.jsonl`) + a **pure** `autonomy.decide(macro, action_risk, grants, *, now) -> allow|confirm|deny`
(D11); (2) wiring that decision into the **engine's per-action gate** *above* `run_action` (D10) — the gate
can only further restrict the funnel, never weaken it; (3) a redacted, `PHONECTL_HOME`-isolated **memory
layer** (`memory/` — device profile, app profiles, user prefs, selector library, failure memory) with
capture hooks from run records (D12); and (4) `autonomy_grant/revoke/list` + `memory_show/export/delete`
RPC + CLI. Everything is additive — default behavior is **confirm**, the safe state, and the full suite
stays green.

**Architecture:** new pure-ish modules `src/phonectl/macro/autonomy.py` (append-only ledger I/O +
`decide` pure decision) and `src/phonectl/macro/memory.py` (five typed JSON stores under
`$PHONECTL_HOME/memory/`, redacted on write, with capture hooks + export/delete). The Plan-6.1 `Engine`
gains an injectable **`gate`** seam called before each action step: it runs `policy.explain` (risk) →
`autonomy.decide` (grant) → `allow` (proceed via `run_action`), `confirm` (call the engine's `confirm`
callback; in an unattended/no-human context, return `confirmation_required`), or `deny`
(`GuardedActionError`). Capture hooks read the `runs.jsonl` records 6.1 writes and fold selector/failure
metadata into `memory/`. The daemon + CLI expose grant/revoke/list and memory show/export/delete.

**Tech Stack:** Python 3 (stdlib only: `json`, `time`); `pytest`. No new runtime dep. Reuses
`policy`/`risk` (2.2) and `redact` (2.1) verbatim.

## Global Constraints

- **stdlib-only at runtime.** `json`, `time` — all stdlib.
- **`autonomy.decide` is pure** (the `ui_parser` discipline): macro + risk + grants + clock in,
  `allow|confirm|deny` out. No I/O, no `subprocess`. Ledger read/write is a thin separate function.
- **The gate can only further restrict (D10).** `run_action` still independently enforces
  mode/kill-switch/policy/rate-limit (2.1/2.2). The autonomy gate is an **additional** guard above the
  funnel; it can downgrade `allow→confirm→deny`, never upgrade past what `run_action` permits.
- **Confirm is the safe default (D11).** A recipe with no live grant runs in **confirm**; an unattended
  context that hits a confirm boundary returns `confirmation_required` rather than proceeding. Critical
  risk always requires an explicit one-time approval.
- **Secrets/PII never leak (D12).** Every value written to `memory/` (and the grant ledger) passes through
  `redact.py`. Memory is operational metadata only — no message/contact content.
- **User-controlled.** Grants are revocable (`autonomy_revoke`) and inspectable (`autonomy_list`); memory
  is exportable (`memory_export`) and deletable (`memory_delete`).
- **Structured-result invariant.** All new RPC handlers return `results.ok`/`results.err` envelopes.
- **Compatible evolution.** With no grants and no memory, behavior is exactly Plan 6.1/6.2 (confirm
  default); existing tests are unchanged.
- **Injectable seams.** `autonomy.decide(..., now=…)`; the engine `gate=` is injectable; capture hooks take
  an injectable record source. `PHONECTL_HOME` isolation for all stores.
- **One commit per task. TDD order is non-negotiable.**

## Shared conventions established by this plan

- **Grant ledger:** `$PHONECTL_HOME/autonomy.jsonl`, append-only. Grant record: `{"kind": "grant",
  "id", "macro", "max_risk", "scope", "granted_at", "expires_at"|null}`. Revoke record: `{"kind":
  "revoke", "id"|"macro", "revoked_at"}`. The **live** grants are derived by replaying the ledger
  (grant minus later revoke, minus expired).
- **Decision values:** `"allow"` (unattended OK), `"confirm"` (needs foreground approval),
  `"deny"` (blocked). Risk order: `low < medium < high < critical`.
- **Memory stores** (under `$PHONECTL_HOME/memory/`, one JSON each): `device.json`, `apps.json`,
  `prefs.json`, `selectors.json`, `failures.json` (spec §8). All redacted on write.
- **Selector-library key:** `f"{package}|{app_version}|{locale}"` → the selector that resolved (so a stale
  entry self-invalidates across app updates, D12 trade-off).
- **No new error code** — the gate reuses `errors.GuardedActionError` (deny) and
  `errors.ConfirmationRequiredError` (confirm-in-no-human), both shipped in 2.1/2.2.

---

### Task 1: `macro/autonomy.py` — pure `decide`

**Files:**
- Create: `src/phonectl/macro/autonomy.py`
- Test: `tests/test_macro_autonomy.py`

**Interfaces:**
- `autonomy.decide(macro, action_risk, grants, *, now) -> str` — **pure**. Returns:
  - `"deny"` if `action_risk == "critical"` and no grant explicitly covers `critical` for this macro;
  - `"allow"` if a **live** grant for `macro.name` covers `action_risk` (grant `max_risk` ≥ action risk)
    and the macro's `policy.require_confirm` is not forcing confirm;
  - `"confirm"` otherwise (the default: medium/high without a covering grant, or `require_confirm: true`).
- `autonomy.live_grants(records, *, now) -> list[dict]` — **pure**: replay the ledger records to the set of
  active grants (grant minus later revoke, minus `expires_at <= now`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_macro_autonomy.py
from phonectl.macro import autonomy
from phonectl.macro.schema import parse


def _m(name="m", require_confirm=False):
    return parse({"name": name, "actions": [], "policy": {"require_confirm": require_confirm}})


def test_no_grant_defaults_to_confirm():
    assert autonomy.decide(_m(), "high", grants=[], now=10.0) == "confirm"
    assert autonomy.decide(_m(), "low", grants=[], now=10.0) == "confirm"


def test_live_grant_allows_up_to_max_risk():
    grants = [{"macro": "m", "max_risk": "high", "expires_at": None}]
    assert autonomy.decide(_m(), "high", grants, now=10.0) == "allow"
    assert autonomy.decide(_m(), "medium", grants, now=10.0) == "allow"


def test_grant_below_action_risk_still_confirms():
    grants = [{"macro": "m", "max_risk": "medium", "expires_at": None}]
    assert autonomy.decide(_m(), "high", grants, now=10.0) == "confirm"


def test_critical_denied_without_explicit_critical_grant():
    grants = [{"macro": "m", "max_risk": "high", "expires_at": None}]
    assert autonomy.decide(_m(), "critical", grants, now=10.0) == "deny"
    grants_crit = [{"macro": "m", "max_risk": "critical", "expires_at": None}]
    assert autonomy.decide(_m(), "critical", grants_crit, now=10.0) == "confirm"


def test_require_confirm_forces_confirm_even_with_grant():
    grants = [{"macro": "m", "max_risk": "high", "expires_at": None}]
    assert autonomy.decide(_m(require_confirm=True), "high", grants, now=10.0) == "confirm"


def test_live_grants_drops_expired_and_revoked():
    records = [
        {"kind": "grant", "id": "g1", "macro": "m", "max_risk": "high", "expires_at": 5.0},
        {"kind": "grant", "id": "g2", "macro": "n", "max_risk": "high", "expires_at": None},
        {"kind": "revoke", "macro": "n", "revoked_at": 8.0},
    ]
    live = autonomy.live_grants(records, now=10.0)
    assert live == []  # g1 expired, g2 revoked
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_macro_autonomy.py -v`
Expected: FAIL (`ModuleNotFoundError: phonectl.macro.autonomy`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/macro/autonomy.py
"""Progressive-autonomy grant ledger + pure decide (confirm by default)."""
from __future__ import annotations

import json

from phonectl.config import config_dir

_ORDER = ["low", "medium", "high", "critical"]


def _rank(level):
    return _ORDER.index(level)


def live_grants(records, *, now) -> list:
    grants = {}
    revoked_macros = set()
    revoked_ids = set()
    for r in records:
        if r.get("kind") == "grant":
            grants[r["id"]] = r
        elif r.get("kind") == "revoke":
            if r.get("id"):
                revoked_ids.add(r["id"])
            if r.get("macro"):
                revoked_macros.add(r["macro"])
    out = []
    for gid, g in grants.items():
        if gid in revoked_ids or g.get("macro") in revoked_macros:
            continue
        exp = g.get("expires_at")
        if exp is not None and exp <= now:
            continue
        out.append(g)
    return out


def decide(macro, action_risk, grants, *, now) -> str:
    covering = [g for g in grants
                if g.get("macro") == macro.name and _rank(g["max_risk"]) >= _rank(action_risk)]
    if action_risk == "critical":
        # critical always needs an explicit one-time human approval; allow only as far as confirm
        return "confirm" if any(g["max_risk"] == "critical" for g in covering) else "deny"
    if macro.policy.get("require_confirm"):
        return "confirm"
    return "allow" if covering else "confirm"


def _path():
    return config_dir() / "autonomy.jsonl"


def read_ledger() -> list:
    p = _path()
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]


def append(record) -> None:
    with open(_path(), "a") as f:
        f.write(json.dumps(record) + "\n")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_macro_autonomy.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/macro/autonomy.py tests/test_macro_autonomy.py
git commit -m "feat: pure autonomy decide (confirm-default) + grant ledger replay"
```

---

### Task 2: grant/revoke/list ledger ops

**Files:**
- Modify: `src/phonectl/macro/autonomy.py`
- Test: `tests/test_macro_autonomy.py` (append)

**Interfaces:**
- `autonomy.grant(macro_name, *, max_risk, scope="all", expires_at=None, now, gen_id=…) -> dict` — append a
  grant record; return it.
- `autonomy.revoke(*, macro=None, grant_id=None, now) -> None` — append a revoke record.
- `autonomy.list_live(*, now) -> list[dict]` — `live_grants(read_ledger(), now=now)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_macro_autonomy.py — append
def test_grant_then_list_then_revoke(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    g = autonomy.grant("reply", max_risk="high", now=10.0)
    assert g["macro"] == "reply" and g["max_risk"] == "high"
    assert [x["macro"] for x in autonomy.list_live(now=11.0)] == ["reply"]
    autonomy.revoke(macro="reply", now=12.0)
    assert autonomy.list_live(now=13.0) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_macro_autonomy.py -v -k "grant_then"`
Expected: FAIL (`AttributeError: grant`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/macro/autonomy.py — append
import uuid


def grant(macro_name, *, max_risk, scope="all", expires_at=None, now, gen_id=None) -> dict:
    if max_risk not in _ORDER:
        raise ValueError(f"bad max_risk {max_risk!r}")
    rec = {"kind": "grant", "id": (gen_id or (lambda: "g_" + uuid.uuid4().hex))(),
           "macro": macro_name, "max_risk": max_risk, "scope": scope,
           "granted_at": now, "expires_at": expires_at}
    append(rec)
    return rec


def revoke(*, macro=None, grant_id=None, now) -> None:
    append({"kind": "revoke", "id": grant_id, "macro": macro, "revoked_at": now})


def list_live(*, now) -> list:
    return live_grants(read_ledger(), now=now)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_macro_autonomy.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/macro/autonomy.py tests/test_macro_autonomy.py
git commit -m "feat: autonomy grant/revoke/list_live ledger operations"
```

---

### Task 3: Engine per-action autonomy gate (above `run_action`)

**Files:**
- Modify: `src/phonectl/macro/engine.py`
- Test: `tests/test_macro_engine_gate.py`

**Interfaces:**
- `Engine(..., gate=None)` — `gate(step, scopes, *, unattended) -> str` returns `allow|confirm|deny`.
  Default gate: `policy.explain(snapshot, verb, target, cfg)` → `risk_level` → `autonomy.decide(macro,
  risk_level, list_live(now), now)`. Injectable for tests.
- In `_exec_action`, **before** calling `run_action`: evaluate the gate.
  - `allow` → proceed.
  - `confirm` → if `unattended` (no human; the default for trigger/scheduler runs), end the step with
    `confirmation_required` (`ConfirmationRequiredError`); else call `self._confirm(message)` and proceed
    only on `True`.
  - `deny` → end the step with `GuardedActionError` (`outcome="guarded_action"`).
- `Engine.run(..., unattended=False)` — trigger/scheduler-fired runs pass `unattended=True`; explicit
  `macro run` passes `unattended=False`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_macro_engine_gate.py
from phonectl.macro import schema
from phonectl.macro.engine import Engine


def _eng(decisions, recorder, unattended_confirm=False):
    def ra(verb, fn, target, **kw):
        recorder.append(verb)
        return {"ok": True, "data": {}}
    # gate returns scripted decisions per call
    seq = iter(decisions)
    return Engine(build=lambda cfg: (None, None, None), run_action=ra,
                  fn_for=lambda step, scopes: (lambda b, s: None),
                  gate=lambda step, scopes, unattended: next(seq),
                  confirm=lambda msg: unattended_confirm)


def test_allow_proceeds():
    rec = []
    m = schema.parse({"name": "m", "actions": [{"type": "tap", "target": {"i": 0}}]})
    out = _eng(["allow"], rec).run(m)
    assert out["ok"] is True and rec == ["tap"]


def test_deny_blocks_with_guarded_action():
    rec = []
    m = schema.parse({"name": "m", "actions": [{"type": "tap", "target": {"i": 0}}]})
    out = _eng(["deny"], rec).run(m)
    assert out["ok"] is False and out["data"]["outcome"] == "guarded_action" and rec == []


def test_confirm_in_unattended_blocks():
    rec = []
    m = schema.parse({"name": "m", "actions": [{"type": "tap", "target": {"i": 0}}]})
    out = _eng(["confirm"], rec).run(m, unattended=True)
    assert out["ok"] is False and out["data"]["outcome"] == "confirmation_required" and rec == []


def test_confirm_interactive_proceeds_on_yes():
    rec = []
    m = schema.parse({"name": "m", "actions": [{"type": "tap", "target": {"i": 0}}]})
    out = _eng(["confirm"], rec, unattended_confirm=True).run(m, unattended=False)
    assert out["ok"] is True and rec == ["tap"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_macro_engine_gate.py -v`
Expected: FAIL (`TypeError: gate` / decisions not consulted).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/macro/engine.py — __init__ gains gate=None; store self._gate (default below)
# default gate:
    def _default_gate(self, step, scopes, *, unattended):
        from phonectl import policy
        from phonectl.macro import autonomy
        import time as _time
        verb = "set_text" if step["type"] == "set_text" else step["type"]
        snap = scopes.get("__snapshot__") or {}
        decision = policy.explain(snap, verb, dict(step.get("target", {})), self._cfg or {})
        risk = decision.get("risk_level", "low")
        return autonomy.decide(self._macro, risk, autonomy.list_live(now=_time.time()), now=_time.time())
```

```python
# src/phonectl/macro/engine.py — in _exec_action, before run_action
        from phonectl import errors
        decision = self._gate(step, scopes, unattended=self._unattended)
        if decision == "deny":
            state["ok"] = False
            state["outcome"] = "guarded_action"
            raise _Stop()
        if decision == "confirm":
            if self._unattended:
                state["ok"] = False
                state["outcome"] = "confirmation_required"
                raise _Stop()
            if not self._confirm(self._confirm_message(step, scopes)):
                state["ok"] = False
                state["outcome"] = "confirmation_required"
                raise _Stop()
        # decision == "allow" → fall through to run_action
```

(`run(..., unattended=False)` stores `self._unattended` + `self._macro` for the run; `self._gate` defaults
to `self._default_gate`. `_confirm_message` interpolates a default `"Run {macro}: {verb}?"`. The gate runs
**above** `run_action`, which still independently enforces mode/policy — defense in depth, D10.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_macro_engine_gate.py tests/test_macro_engine.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/macro/engine.py tests/test_macro_engine_gate.py
git commit -m "feat: engine per-action autonomy gate (allow/confirm/deny) above run_action"
```

---

### Task 4: `macro/memory.py` — five redacted stores + read/write

**Files:**
- Create: `src/phonectl/macro/memory.py`
- Test: `tests/test_macro_memory.py`

**Interfaces:**
- `memory.STORES = ("device", "apps", "prefs", "selectors", "failures")`.
- `memory.read(store) -> dict` / `memory.write(store, data) -> None` — JSON under
  `$PHONECTL_HOME/memory/<store>.json`; **every string value passes through `redact.redact_text`** on
  write (D12). Unknown store → `ValueError`.
- `memory.update(store, key, value) -> None` — merge one key (redacted).
- `memory.export() -> dict` / `memory.delete(store=None) -> None` — dump all stores / clear one or all.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_macro_memory.py
import pytest

from phonectl.macro import memory


def test_write_read_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    memory.write("device", {"android": "14", "oem": "samsung"})
    assert memory.read("device")["oem"] == "samsung"


def test_unknown_store_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    with pytest.raises(ValueError):
        memory.read("contacts")


def test_values_are_redacted(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    memory.update("failures", "note", "OTP is 123456 call 555-123-4567")
    stored = memory.read("failures")["note"]
    assert "123456" not in stored or "555-123-4567" not in stored  # redacted


def test_export_and_delete(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    memory.write("prefs", {"quiet_hours": "22:00-08:00"})
    assert "prefs" in memory.export()
    memory.delete("prefs")
    assert memory.read("prefs") == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_macro_memory.py -v`
Expected: FAIL (`ModuleNotFoundError: phonectl.macro.memory`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/macro/memory.py
"""Narrow, redacted, user-controlled memory stores (strategy §25, D12)."""
from __future__ import annotations

import json

from phonectl.config import config_dir

try:
    from phonectl.redact import redact_text
except Exception:  # pragma: no cover - redact ships in 2.1
    def redact_text(s):
        return s

STORES = ("device", "apps", "prefs", "selectors", "failures")


def _dir():
    d = config_dir() / "memory"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _check(store):
    if store not in STORES:
        raise ValueError(f"unknown memory store {store!r}")


def _redact(value):
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {k: _redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v) for v in value]
    return value


def read(store) -> dict:
    _check(store)
    p = _dir() / f"{store}.json"
    if not p.exists():
        return {}
    return json.loads(p.read_text())


def write(store, data) -> None:
    _check(store)
    (_dir() / f"{store}.json").write_text(json.dumps(_redact(data)))


def update(store, key, value) -> None:
    data = read(store)
    data[key] = _redact(value)
    write(store, data)


def export() -> dict:
    return {s: read(s) for s in STORES if (_dir() / f"{s}.json").exists()}


def delete(store=None) -> None:
    targets = [store] if store else list(STORES)
    for s in targets:
        _check(s)
        p = _dir() / f"{s}.json"
        if p.exists():
            p.unlink()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_macro_memory.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/macro/memory.py tests/test_macro_memory.py
git commit -m "feat: redacted, user-controlled memory stores (device/apps/prefs/selectors/failures)"
```

---

### Task 5: Memory capture hooks from run records

**Files:**
- Modify: `src/phonectl/macro/memory.py` (add capture functions)
- Test: `tests/test_macro_memory_capture.py`

**Interfaces:**
- `memory.capture_selector(record) -> None` — when an action record carries a resolved selector
  (`target.selector` + `target.matched_i`) and `outcome == "ok"`, store it in `selectors.json` keyed by
  `package|app_version|locale` (from the record/snapshot). Redacted.
- `memory.capture_failure(record) -> None` — when `outcome` is a retryable failure code, increment a
  per-`(verb, code)` counter in `failures.json` (failure memory; no payloads).
- `memory.capture_from_runs(records) -> None` — fold a batch of `runs.jsonl` records through both hooks
  (the daemon/engine calls this opportunistically; pure-ish, only touches `memory/`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_macro_memory_capture.py
from phonectl.macro import memory


def test_capture_selector_on_ok(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    rec = {"kind": "action", "outcome": "ok", "verb": "tap",
           "target": {"selector": {"resource_id": "com.x:id/send"}, "matched_i": 14},
           "context": {"package": "com.x", "app_version": "2.1", "locale": "en"}}
    memory.capture_selector(rec)
    sels = memory.read("selectors")
    assert "com.x|2.1|en" in sels


def test_capture_selector_skips_failures(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    rec = {"kind": "action", "outcome": "guarded_action",
           "target": {"selector": {"x": 1}}, "context": {"package": "com.x"}}
    memory.capture_selector(rec)
    assert memory.read("selectors") == {}


def test_capture_failure_counts(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    rec = {"kind": "action", "outcome": "stale_snapshot", "verb": "tap"}
    memory.capture_failure(rec)
    memory.capture_failure(rec)
    assert memory.read("failures")["tap|stale_snapshot"] == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_macro_memory_capture.py -v`
Expected: FAIL (`AttributeError: capture_selector`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/macro/memory.py — append
_RETRYABLE = {"busy", "rate_limited", "observe_failed", "stale_snapshot"}


def capture_selector(record) -> None:
    if record.get("outcome") != "ok":
        return
    target = record.get("target") or {}
    sel = target.get("selector")
    if not sel or "matched_i" not in target:
        return
    ctx = record.get("context") or {}
    key = f"{ctx.get('package', '?')}|{ctx.get('app_version', '?')}|{ctx.get('locale', '?')}"
    update("selectors", key, {"selector": sel, "matched_i": target["matched_i"]})


def capture_failure(record) -> None:
    if record.get("outcome") not in _RETRYABLE:
        return
    key = f"{record.get('verb', '?')}|{record.get('outcome')}"
    failures = read("failures")
    failures[key] = failures.get(key, 0) + 1
    write("failures", failures)


def capture_from_runs(records) -> None:
    for r in records:
        if r.get("kind") == "action":
            capture_selector(r)
            capture_failure(r)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_macro_memory_capture.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/macro/memory.py tests/test_macro_memory_capture.py
git commit -m "feat: memory capture hooks — learn selectors from successes, count retryable failures"
```

---

### Task 6: RPC + CLI for autonomy + memory, and docs

**Files:**
- Modify: `src/phonectl/daemon/server.py`, `src/phonectl/daemon/rpc.py` (control methods),
  `src/phonectl/cli.py`, `docs/macros.md`, `README.md`,
  `docs/superpowers/phonectl-platform-roadmap.md` (mark Phase 6 complete)
- Test: `tests/test_daemon_server.py` (append), `tests/test_cli.py` (append)

**Interfaces:**
- Daemon RPC: `autonomy_grant` (`autonomy.grant`), `autonomy_revoke` (`autonomy.revoke`), `autonomy_list`
  (`autonomy.list_live`), `memory_show` (`memory.read`/`export`), `memory_export` (`memory.export`),
  `memory_delete` (`memory.delete`). Grant/revoke/delete are **control** methods.
- CLI: `phonectl autonomy grant <macro> --max-risk high [--expires …]`, `phonectl autonomy revoke
  <macro>`, `phonectl autonomy list`; `phonectl memory show [<store>]`, `phonectl memory export [<file>]`,
  `phonectl memory delete [<store>]`. Route via `_dispatch`; in-process for the ledger/store ops.
- Run the **full suite** on this final task.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_daemon_server.py — append
def test_autonomy_grant_list_revoke(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    srv = _srv(tmp_path)
    g = json.loads(srv.handle_line(_req("autonomy_grant", {"macro": "reply", "max_risk": "high"})))
    assert g["ok"] is True
    listed = json.loads(srv.handle_line(_req("autonomy_list")))
    assert any(x["macro"] == "reply" for x in listed["data"]["grants"])
    assert json.loads(srv.handle_line(_req("autonomy_revoke", {"macro": "reply"})))["ok"] is True


def test_memory_show_export_delete(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    from phonectl.macro import memory
    memory.write("prefs", {"quiet_hours": "22:00-08:00"})
    srv = _srv(tmp_path)
    shown = json.loads(srv.handle_line(_req("memory_show", {"store": "prefs"})))
    assert shown["data"]["quiet_hours"] == "22:00-08:00"
    assert json.loads(srv.handle_line(_req("memory_delete", {"store": "prefs"})))["ok"] is True
```

```python
# tests/test_cli.py — append
def test_autonomy_grant_cli(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_daemon_client", lambda cfg: None)
    rc = cli.main(["autonomy", "grant", "reply", "--max-risk", "high", "--json"])
    assert rc == 0
    rc = cli.main(["autonomy", "list", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert any(g["macro"] == "reply" for g in out["data"]["grants"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_daemon_server.py tests/test_cli.py -v -k "autonomy or memory"`
Expected: FAIL (`unknown_method` / no `autonomy` subcommand).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/daemon/rpc.py — extend MUTATING with the control ops
MUTATING = {"act", "stop", "resume", "macro_run", "macro_cancel",
            "macro_enable", "macro_disable", "autonomy_grant", "autonomy_revoke", "memory_delete"}
```

```python
# src/phonectl/daemon/server.py — register handlers
        from phonectl.macro import autonomy as _aut
        from phonectl.macro import memory as _mem
        import time as _t

        @self.registry.register("autonomy_grant")
        def _autonomy_grant(params, ctx):
            g = _aut.grant(params["macro"], max_risk=params["max_risk"],
                           scope=params.get("scope", "all"),
                           expires_at=params.get("expires_at"), now=_t.time())
            return results.ok(capability="autonomy.grant", data=g)

        @self.registry.register("autonomy_revoke")
        def _autonomy_revoke(params, ctx):
            _aut.revoke(macro=params.get("macro"), grant_id=params.get("grant_id"), now=_t.time())
            return results.ok(capability="autonomy.revoke", data={"revoked": True})

        @self.registry.register("autonomy_list")
        def _autonomy_list(params, ctx):
            return results.ok(capability="autonomy.list", data={"grants": _aut.list_live(now=_t.time())})

        @self.registry.register("memory_show")
        def _memory_show(params, ctx):
            store = params.get("store")
            return results.ok(capability="memory.show",
                              data=_mem.read(store) if store else _mem.export())

        @self.registry.register("memory_export")
        def _memory_export(params, ctx):
            return results.ok(capability="memory.export", data=_mem.export())

        @self.registry.register("memory_delete")
        def _memory_delete(params, ctx):
            _mem.delete(params.get("store"))
            return results.ok(capability="memory.delete", data={"deleted": True})
```

Wire the `autonomy` and `memory` CLI subparsers (route via `_dispatch`, in-process fallback calls the
`autonomy`/`memory` modules directly). Document progressive autonomy + the memory layer in `docs/macros.md`
(grant/revoke/list, the confirm-default, the critical one-time-approval rule, memory export/delete and the
"operational metadata only" promise). Mark **Phase 6 complete** in the roadmap Phase index.

- [ ] **Step 4: Run test to verify it passes, then the full suite**

Run: `pytest tests/test_daemon_server.py tests/test_cli.py -v -k "autonomy or memory"`
Then: `pytest -v`
Expected: PASS (no regression; autonomy/memory are additive and confirm-default preserves prior behavior).

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/daemon/rpc.py src/phonectl/daemon/server.py src/phonectl/cli.py docs/macros.md README.md docs/superpowers/phonectl-platform-roadmap.md tests/test_daemon_server.py tests/test_cli.py
git commit -m "feat: autonomy grant/revoke/list + memory show/export/delete RPC + CLI; complete Phase 6"
```

---

## Dependencies

- **Plan 6.1** — the `Engine` per-action seam (`gate=`), `records`.
- **Plan 6.2** — `registry`/`TriggerManager` (the unattended path the gate guards), `conditions`.
- **Plan 2.2** — `policy.explain`/`risk` the gate consults; **Plan 2.1** — `redact` every store/ledger
  value passes through, and `GuardedActionError`/`ConfirmationRequiredError` the gate reuses.

## Deferred (out of scope for 6.3)

- **Macro signing / cryptographic grant binding** (spec §11 open question 2) — v1 uses a content hash +
  named grant; real signing + a key story is a follow-up before any cross-device macro sharing.
- **Selector-library override policy** (spec §11 open question 5) — this plan *captures* learned selectors;
  *using* a learned selector to override an author's failing selector is a deliberate follow-up.
- **`prefs`-driven gate defaults** (quiet hours, per-contact allowlists feeding `autonomy.decide`) — the
  stores exist; wiring prefs into the decision is a small follow-up once real prefs are populated.

## Notes on testability

- `autonomy.decide`/`live_grants` are **pure** — fixture-tested with injected `now`, no I/O.
- The engine **gate** is injectable, so allow/confirm/deny paths are tested without `policy`/`risk` or a
  device.
- `memory` + capture hooks are tested via `PHONECTL_HOME` isolation; redaction is asserted directly.
- **No device behavior is claimed.** Graduating a real recipe to unattended and watching it run is a manual
  on-device smoke (note it in `docs/macros.md`; do not run it in CI).
