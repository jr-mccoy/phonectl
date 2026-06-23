# phonectl Macro Runtime Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Plan 6.1 of the platform roadmap** (`docs/superpowers/phonectl-platform-roadmap.md`). First plan of
Phase 6. Implements the Phase-6.0 macro-engine design
(`docs/superpowers/specs/2026-06-22-phonectl-macro-engine-design.md`), locked decisions **D1–D3, D6–D9**.
Depends on **Plan 2.1** (`runtime.run_action` funnel + audit v2 + `redact.py`), **Plan 2.2**
(`policy`/`risk`), **Plan 3.1** (`ProviderRegistry` + `cli.build_runtime`), and the action/verb mapping
shipped across **Plans 1.2/3.2–3.5/4.x**. Opportunistic on **Plan 5.1** (daemon: `runs.jsonl` records,
`_dispatch` frontend routing) — gated via `hasattr`/`discover` so a no-daemon build runs macros in-process.
**Plans 6.2** (triggers + scheduler) and **6.3** (autonomy + memory) land on top of this one.

**Goal:** make phonectl run a **declarative macro document** end-to-end: parse + validate it (pure),
resolve scoped variables with `${var}` interpolation (pure), and execute its action/control-flow steps
through a single executor where **every mutating action routes through `runtime.run_action`** (D3) — with a
cancellation token, bounded-backoff retries that re-check policy before replaying a high-risk action (D7),
and one durable macro-run record + per-action lineage in `runs.jsonl` (D9). This plan ships the **manual**
run path (`phonectl macro run <doc>`); triggers and the scheduler are Plan 6.2. The macro engine **MUST NOT**
be required for any existing primitive — it is purely additive and every existing test stays green.

**Architecture:** a new package `src/phonectl/macro/`. `macro/schema.py` is **pure**
(`parse(doc: dict) -> Macro`, `validate(doc) -> list[str]`); no I/O, no `subprocess`, no `eval` (D1).
`macro/variables.py` is **pure**: a `Scopes` view (runtime→macro→trigger→secret read order, scoped writes)
plus `interpolate(template, scopes)` `${var}` substitution, with secret values rendered through `redact`
(D6). `macro/engine.py` is the executor: it walks the typed step list, interprets control flow
(`if`/`switch`/`for_each`/`loop`/`retry`/`race`/`try`/`wait`/`set`/`confirm`/`stop`/`audit_note`), maps
phone-verb steps to the existing `(verb, fn, target)` triple, and calls `runtime.run_action` for each
(D3). `macro/records.py` writes the `MacroRun` summary + sets `parent_task_id` on action records (reusing
the Plan 5.1 `daemon/records.py` writer when present, else its own appender). `macro/loader.py` reads a
macro file (JSON via stdlib; YAML only behind the optional `[yaml]` extra, D2). The daemon gains
`macro_validate/run/cancel/status` RPC handlers; `cli.py` gains a `phonectl macro` command group; the MCP
registry gains `phone.macro.*` tools.

**Tech Stack:** Python 3 (stdlib only at runtime: `json`, `time`, `uuid`, `re`, `threading`); `pytest` for
tests; optional `[yaml]` extra (PyYAML) consumed **only** in `macro/loader.py`. No new core runtime dep.

## Global Constraints

- **stdlib-only at runtime.** The engine uses `json`, `time`, `uuid`, `re`, `threading` — all stdlib. YAML
  is an **optional extra**, imported only inside `macro/loader.py`, never in the pure parser (D2).
- **`schema.py` and `variables.py` are pure** (the `ui_parser` discipline): `dict`/`str` in, data out — no
  I/O, no `subprocess`, no sleep, no `eval`. All edge cases are fixture-tested.
- **No new action execution path.** Every mutating macro step calls `runtime.run_action`; the engine never
  calls `actuator`/providers directly for a mutating verb (D3). Mode/kill-switch/risk/rate-limit/audit are
  inherited, not re-implemented.
- **Backend isolation preserved.** The engine touches the phone only through the `(verb, fn, target)`
  triple `run_action` already accepts (built via `cli.build_runtime`); it never imports `adb_backend` or
  calls `subprocess`/`adb`.
- **Every `act()` re-observes.** Unchanged: action steps go through `run_action`, which re-observes and
  returns the post-action snapshot.
- **Structured-result invariant (Plan 1.1).** `macro_validate/run/cancel/status` return `results.ok`/
  `results.err` envelopes; a macro run yields a `results` envelope carrying `run_id` + outcome.
- **Secrets never leak (D6).** `secret`-scope variables pass through `redact.py` in every record/log.
- **Compatible evolution.** With no daemon running, `phonectl macro run` executes in-process against
  `build_runtime`; existing CLI/MCP/daemon behavior and tests are unchanged.
- **Injectable seams.** `Engine(*, build=cli.build_runtime, run_action=runtime.run_action, now=time.time,
  sleep=time.sleep, confirm=…, append_record=…)`; tests inject fakes and isolate state via
  `monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))`. No real wall-clock waits in unit tests.
- **One commit per task. TDD order is non-negotiable.**

## Shared conventions established by this plan

- **New package** `src/phonectl/macro/` (`__init__.py` + `schema.py`, `variables.py`, `engine.py`,
  `records.py`, `loader.py`).
- **Typed macro** (`schema.Macro`): `name`, `version`, `permissions`, `trigger` (opaque here; consumed in
  6.2), `conditions` (opaque here; consumed in 6.2), `variables`, `actions` (the typed step list),
  `policy`, `limits`. Unknown top-level keys are a validation error; unknown step `type` is a validation
  error.
- **Step kinds:** phone-verb steps (`tap`, `type`/`set_text`, `swipe`, `scroll_until`, `launch`, `key`,
  `intent`, `clipboard_read|write|clear`, `notification_reply|dismiss`, …) → mapped to `(verb, fn, target)`
  and run via `run_action`. Control-flow steps (`if`, `switch`, `for_each`, `loop`, `retry`, `race`, `try`,
  `wait`, `set`, `confirm`, `stop`, `audit_note`) → interpreted by the engine.
- **Variable scopes** (read order) `runtime` → `macro` → `trigger` → `secret`; writes target an explicit
  scope (default `runtime`). `${name}` interpolation is flat (no nested paths in v1).
- **Run lineage:** `run_id = "run_" + uuid4().hex`. Every action step's `runs.jsonl` record carries
  `parent_task_id = run_id`. One `MacroRun` summary record (`kind="macro_run"`) is appended per run.
- **New additive error codes (in `errors.py` this plan):** `MacroValidationError` (code
  `"macro_invalid"`, `requires_user=True`), `MacroCancelledError` (code `"macro_cancelled"`).
  `ConfirmationRequiredError` (code `"confirmation_required"`) is **reused** from Plan 2.1 for the
  `confirm` step in a no-human context.
- **Optional extra:** `pyproject.toml` gains `[project.optional-dependencies] yaml = ["PyYAML>=6"]`,
  imported only in `macro/loader.py`.

---

### Task 1: Additive errors + `macro` package + step-kind constants

**Files:**
- Modify: `src/phonectl/errors.py`
- Create: `src/phonectl/macro/__init__.py`
- Test: `tests/test_macro_errors.py`

**Interfaces:**
- `errors.MacroValidationError` (code `"macro_invalid"`, `requires_user=True`).
- `errors.MacroCancelledError` (code `"macro_cancelled"`).
- `macro.PHONE_VERBS` (frozenset of step types that map to `run_action`) and
  `macro.CONTROL_STEPS` (frozenset of interpreted control-flow step types).

- [x] **Step 1: Write the failing test**

```python
# tests/test_macro_errors.py
from phonectl import errors
import phonectl.macro as macro


def test_macro_error_codes():
    assert errors.MacroValidationError("x").code == "macro_invalid"
    assert errors.MacroValidationError("x").requires_user is True
    assert errors.MacroCancelledError().code == "macro_cancelled"


def test_step_kind_sets_are_disjoint_and_populated():
    assert "tap" in macro.PHONE_VERBS
    assert "if" in macro.CONTROL_STEPS and "for_each" in macro.CONTROL_STEPS
    assert macro.PHONE_VERBS.isdisjoint(macro.CONTROL_STEPS)
```

- [x] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_macro_errors.py -v`
Expected: FAIL (`AttributeError: MacroValidationError` / `ModuleNotFoundError: phonectl.macro`).

- [x] **Step 3: Write minimal implementation**

```python
# src/phonectl/errors.py — append
class MacroValidationError(PhonectlError):
    code = "macro_invalid"
    requires_user = True


class MacroCancelledError(PhonectlError):
    code = "macro_cancelled"
```

```python
# src/phonectl/macro/__init__.py
"""phonectl macro engine: declarative, auditable automations (Phase 6)."""

PHONE_VERBS = frozenset({
    "tap", "type", "set_text", "swipe", "scroll_until", "launch", "key",
    "intent", "clipboard_read", "clipboard_write", "clipboard_clear",
    "notification_reply", "notification_dismiss",
})

CONTROL_STEPS = frozenset({
    "if", "switch", "for_each", "loop", "retry", "race", "try",
    "wait", "set", "confirm", "stop", "audit_note",
})
```

- [x] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_macro_errors.py -v`
Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add src/phonectl/errors.py src/phonectl/macro/__init__.py tests/test_macro_errors.py
git commit -m "feat: macro package skeleton + macro_invalid/macro_cancelled errors + step-kind sets"
```

---

### Task 2: `macro/schema.py` — pure parse + validate

**Files:**
- Create: `src/phonectl/macro/schema.py`
- Test: `tests/test_macro_schema.py`

**Interfaces:**
- `schema.validate(doc: dict) -> list[str]` — **pure**: returns a list of human-readable validation errors
  (empty ⇒ valid). Checks: `name` present + str; unknown top-level key; each step is a dict with a known
  `type` (in `PHONE_VERBS | CONTROL_STEPS`); control-flow steps have their required sub-keys (`if` →
  `condition`+`then`; `for_each` → `in`+`as`+`do`; `loop` → `do`; `retry` → `do`; `switch` → `on`+`cases`);
  nested step lists are validated recursively.
- `schema.parse(doc: dict) -> Macro` — **pure**: returns a normalized `Macro` (a small frozen dataclass or
  a normalized dict) with defaults applied (`version=1`, `permissions={}`, `variables={}`, `policy={}`,
  `limits={}`); raises `errors.MacroValidationError` (joined messages) when `validate` is non-empty.

- [x] **Step 1: Write the failing test**

```python
# tests/test_macro_schema.py
import pytest

from phonectl import errors
from phonectl.macro import schema


def test_validate_ok_minimal():
    assert schema.validate({"name": "m", "actions": [{"type": "tap", "target": {"i": 0}}]}) == []


def test_validate_missing_name():
    errs = schema.validate({"actions": []})
    assert any("name" in e for e in errs)


def test_validate_unknown_top_level_key():
    errs = schema.validate({"name": "m", "actions": [], "bogus": 1})
    assert any("bogus" in e for e in errs)


def test_validate_unknown_step_type():
    errs = schema.validate({"name": "m", "actions": [{"type": "frobnicate"}]})
    assert any("frobnicate" in e for e in errs)


def test_validate_if_requires_condition_and_then():
    errs = schema.validate({"name": "m", "actions": [{"type": "if"}]})
    assert any("condition" in e for e in errs) and any("then" in e for e in errs)


def test_validate_recurses_into_nested_steps():
    errs = schema.validate({"name": "m", "actions": [
        {"type": "if", "condition": {"type": "device_unlocked"},
         "then": [{"type": "nope"}]}]})
    assert any("nope" in e for e in errs)


def test_parse_applies_defaults():
    m = schema.parse({"name": "m", "actions": []})
    assert m.name == "m" and m.version == 1
    assert m.permissions == {} and m.policy == {} and m.limits == {}


def test_parse_raises_on_invalid():
    with pytest.raises(errors.MacroValidationError):
        schema.parse({"actions": []})
```

- [x] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_macro_schema.py -v`
Expected: FAIL (`ModuleNotFoundError: phonectl.macro.schema`).

- [x] **Step 3: Write minimal implementation**

```python
# src/phonectl/macro/schema.py
"""Pure macro-document parse + validation (no I/O, no eval)."""
from __future__ import annotations

from dataclasses import dataclass, field

from phonectl import errors
from phonectl.macro import CONTROL_STEPS, PHONE_VERBS

_TOP_KEYS = {"name", "version", "permissions", "trigger", "conditions",
             "variables", "actions", "policy", "limits"}
_REQUIRED_SUBKEYS = {
    "if": ("condition", "then"),
    "for_each": ("in", "as", "do"),
    "loop": ("do",),
    "retry": ("do",),
    "switch": ("on", "cases"),
    "try": ("do",),
}
_NESTED_LISTS = ("then", "else", "do")


@dataclass(frozen=True)
class Macro:
    name: str
    version: int = 1
    permissions: dict = field(default_factory=dict)
    trigger: dict | None = None
    conditions: list = field(default_factory=list)
    variables: dict = field(default_factory=dict)
    actions: list = field(default_factory=list)
    policy: dict = field(default_factory=dict)
    limits: dict = field(default_factory=dict)


def _validate_steps(steps, path, errs):
    if not isinstance(steps, list):
        errs.append(f"{path}: expected a list of steps")
        return
    for n, step in enumerate(steps):
        sp = f"{path}[{n}]"
        if not isinstance(step, dict) or "type" not in step:
            errs.append(f"{sp}: each step needs a 'type'")
            continue
        t = step["type"]
        if t not in PHONE_VERBS and t not in CONTROL_STEPS:
            errs.append(f"{sp}: unknown step type {t!r}")
            continue
        for req in _REQUIRED_SUBKEYS.get(t, ()):
            if req not in step:
                errs.append(f"{sp}: step {t!r} requires {req!r}")
        for key in _NESTED_LISTS:
            if key in step:
                _validate_steps(step[key], f"{sp}.{key}", errs)
        if t == "switch":
            for case, body in (step.get("cases") or {}).items():
                _validate_steps(body, f"{sp}.cases.{case}", errs)
            if "default" in step:
                _validate_steps(step["default"], f"{sp}.default", errs)


def validate(doc) -> list:
    errs: list = []
    if not isinstance(doc, dict):
        return ["macro must be an object"]
    if not isinstance(doc.get("name"), str) or not doc.get("name"):
        errs.append("macro requires a non-empty string 'name'")
    for key in doc:
        if key not in _TOP_KEYS:
            errs.append(f"unknown top-level key {key!r}")
    _validate_steps(doc.get("actions", []), "actions", errs)
    return errs


def parse(doc) -> Macro:
    errs = validate(doc)
    if errs:
        raise errors.MacroValidationError("; ".join(errs))
    return Macro(
        name=doc["name"],
        version=int(doc.get("version", 1)),
        permissions=doc.get("permissions", {}),
        trigger=doc.get("trigger"),
        conditions=doc.get("conditions", []),
        variables=doc.get("variables", {}),
        actions=doc.get("actions", []),
        policy=doc.get("policy", {}),
        limits=doc.get("limits", {}),
    )
```

- [x] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_macro_schema.py -v`
Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add src/phonectl/macro/schema.py tests/test_macro_schema.py
git commit -m "feat: pure macro schema parse + recursive validation"
```

---

### Task 3: `macro/variables.py` — scoped variables + `${var}` interpolation

**Files:**
- Create: `src/phonectl/macro/variables.py`
- Test: `tests/test_macro_variables.py`

**Interfaces:**
- `variables.Scopes(*, runtime=None, macro=None, trigger=None, secret=None)` — holds four dicts.
  `.get(name, default=None)` reads in order runtime→macro→trigger→secret. `.set(name, value,
  scope="runtime")` writes to a named scope. `.is_secret(name)` ⇒ name resolves from the `secret` scope.
- `variables.interpolate(template: str, scopes) -> str` — replaces each `${name}` with `str(scopes.get
  (name, ""))`. **Pure.**
- `variables.redacted_view(scopes) -> dict` — a flat merged dict for logging where every secret-scope value
  is replaced by `redact.SECRET_MASK` (reuse Plan 2.1 `redact`).

- [x] **Step 1: Write the failing test**

```python
# tests/test_macro_variables.py
from phonectl.macro import variables as V


def test_read_order_runtime_beats_macro():
    s = V.Scopes(runtime={"x": "r"}, macro={"x": "m", "y": "ym"})
    assert s.get("x") == "r"
    assert s.get("y") == "ym"
    assert s.get("missing", "d") == "d"


def test_set_targets_named_scope():
    s = V.Scopes()
    s.set("a", "1")
    s.set("b", "2", scope="macro")
    assert s.get("a") == "1"
    assert s.runtime == {"a": "1"} and s.macro == {"b": "2"}


def test_interpolate_substitutes():
    s = V.Scopes(runtime={"name": "Sam"})
    assert V.interpolate("Hi ${name}!", s) == "Hi Sam!"
    assert V.interpolate("none ${gone}", s) == "none "


def test_redacted_view_masks_secrets():
    s = V.Scopes(runtime={"x": "1"}, secret={"otp": "123456"})
    view = V.redacted_view(s)
    assert view["x"] == "1"
    assert view["otp"] != "123456"
    assert s.is_secret("otp") is True
```

- [x] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_macro_variables.py -v`
Expected: FAIL (`ModuleNotFoundError: phonectl.macro.variables`).

- [x] **Step 3: Write minimal implementation**

```python
# src/phonectl/macro/variables.py
"""Pure scoped-variable resolution + ${var} interpolation for macros."""
from __future__ import annotations

import re

_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
try:
    from phonectl.redact import SECRET_MASK
except Exception:  # pragma: no cover - redact ships in 2.1
    SECRET_MASK = "***"


class Scopes:
    _ORDER = ("runtime", "macro", "trigger", "secret")

    def __init__(self, *, runtime=None, macro=None, trigger=None, secret=None):
        self.runtime = dict(runtime or {})
        self.macro = dict(macro or {})
        self.trigger = dict(trigger or {})
        self.secret = dict(secret or {})

    def _scope(self, name):
        return getattr(self, name)

    def get(self, name, default=None):
        for s in self._ORDER:
            d = self._scope(s)
            if name in d:
                return d[name]
        return default

    def set(self, name, value, scope="runtime"):
        if scope not in self._ORDER:
            raise ValueError(f"unknown scope {scope!r}")
        self._scope(scope)[name] = value

    def is_secret(self, name):
        return name in self.secret and not any(
            name in self._scope(s) for s in ("runtime", "macro", "trigger"))


def interpolate(template, scopes) -> str:
    return _VAR.sub(lambda m: str(scopes.get(m.group(1), "")), template)


def redacted_view(scopes) -> dict:
    merged = {}
    for s in ("secret", "trigger", "macro", "runtime"):
        merged.update(scopes._scope(s))
    for k in scopes.secret:
        if scopes.is_secret(k):
            merged[k] = SECRET_MASK
    return merged
```

- [x] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_macro_variables.py -v`
Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add src/phonectl/macro/variables.py tests/test_macro_variables.py
git commit -m "feat: pure scoped macro variables + interpolation + secret-redacted view"
```

---

### Task 4: `macro/engine.py` — sequential actions via `run_action` + `set`/`wait`/`stop`/`audit_note`

**Files:**
- Create: `src/phonectl/macro/engine.py`
- Test: `tests/test_macro_engine.py`

**Interfaces:**
- `engine.CancellationToken()` — `.cancel()` / `.cancelled` flag.
- `engine.Engine(*, build=cli.build_runtime, run_action=runtime.run_action, now=time.time,
  sleep=lambda s: None, confirm=lambda msg: False, fn_for=None, cfg=None)`. `fn_for(step, scopes)` maps a
  phone-verb step to the `fn` callable `run_action` expects (defaults to a `cli` helper; injectable in
  tests).
- `Engine.run(macro, *, scopes=None, token=None, trigger="manual", yes=False) -> dict` — executes
  `macro.actions` sequentially, returns a `results` envelope with `data={"run_id", "steps_run",
  "outcome", "variables": redacted_view}`. For each phone-verb step it calls
  `run_action(verb, fn, target, build=build, yes=yes, cfg=cfg, request_id=…, idempotency_key=…)`; a
  non-`ok` envelope ends the run with `outcome=<error code>` unless inside a `try`/`retry` (Task 5). `set`
  writes a variable; `wait` sleeps `seconds` (injected); `stop` ends successfully; `audit_note` logs.
- Cancellation is checked **before each step**; a cancelled run returns `results.err(MacroCancelledError)`.

- [x] **Step 1: Write the failing test**

```python
# tests/test_macro_engine.py
from phonectl.macro import schema
from phonectl.macro.engine import Engine, CancellationToken


def _engine(recorder, **kw):
    # run_action stand-in: records the verb/target, returns an ok envelope.
    def fake_run_action(verb, fn, target, **kwargs):
        recorder.append((verb, target))
        return {"ok": True, "data": {"hash": "h", "verb": verb}, "request_id": kwargs.get("request_id")}
    return Engine(build=lambda cfg: ("REG", "SESS", None),
                  run_action=fake_run_action,
                  fn_for=lambda step, scopes: (lambda b, s: None),
                  **kw)


def test_runs_sequential_phone_verbs():
    rec = []
    m = schema.parse({"name": "m", "actions": [
        {"type": "tap", "target": {"i": 0}},
        {"type": "launch", "package": "com.example"}]})
    out = _engine(rec).run(m)
    assert out["ok"] is True
    assert [v for v, _ in rec] == ["tap", "launch"]
    assert out["data"]["steps_run"] == 2
    assert out["data"]["run_id"].startswith("run_")


def test_set_and_interpolation_into_target():
    rec = []
    m = schema.parse({"name": "m", "actions": [
        {"type": "set", "var": "msg", "value": "hello"},
        {"type": "type", "target": {"text": "${msg}"}}]})
    _engine(rec).run(m)
    assert rec[-1][1]["text"] == "hello"


def test_stop_ends_run_early():
    rec = []
    m = schema.parse({"name": "m", "actions": [
        {"type": "tap", "target": {"i": 0}}, {"type": "stop"},
        {"type": "tap", "target": {"i": 1}}]})
    out = _engine(rec).run(m)
    assert out["data"]["steps_run"] == 1 and out["data"]["outcome"] == "ok"


def test_failed_action_ends_run_with_error_code():
    def failing(verb, fn, target, **kw):
        return {"ok": False, "error": {"code": "guarded_action"}}
    eng = Engine(build=lambda cfg: (None, None, None), run_action=failing,
                 fn_for=lambda step, scopes: (lambda b, s: None))
    m = schema.parse({"name": "m", "actions": [{"type": "tap", "target": {"i": 0}}]})
    out = eng.run(m)
    assert out["ok"] is False and out["data"]["outcome"] == "guarded_action"


def test_cancellation_before_step():
    rec = []
    tok = CancellationToken()
    tok.cancel()
    m = schema.parse({"name": "m", "actions": [{"type": "tap", "target": {"i": 0}}]})
    out = _engine(rec).run(m, token=tok)
    assert out["ok"] is False and out["error"]["code"] == "macro_cancelled"
    assert rec == []
```

- [x] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_macro_engine.py -v`
Expected: FAIL (`ModuleNotFoundError: phonectl.macro.engine`).

- [x] **Step 3: Write minimal implementation**

```python
# src/phonectl/macro/engine.py
"""Macro executor: control flow + action steps routed through run_action."""
from __future__ import annotations

import time
import uuid

from phonectl import errors, results
from phonectl.macro import PHONE_VERBS
from phonectl.macro import variables as V


class CancellationToken:
    def __init__(self):
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


class _Stop(Exception):
    pass


class Engine:
    def __init__(self, *, build=None, run_action=None, now=time.time,
                 sleep=lambda s: None, confirm=lambda msg: False, fn_for=None, cfg=None):
        if build is None:
            from phonectl import cli
            build = cli.build_runtime
        if run_action is None:
            from phonectl import runtime
            run_action = runtime.run_action
        self._build = build
        self._run_action = run_action
        self._now = now
        self._sleep = sleep
        self._confirm = confirm
        self._fn_for = fn_for or self._default_fn_for
        self._cfg = cfg

    def _default_fn_for(self, step, scopes):
        from phonectl import cli
        return cli.macro_fn_for(step, scopes)  # shared mapping, added in Task 6

    def run(self, macro, *, scopes=None, token=None, trigger="manual", yes=False) -> dict:
        scopes = scopes or V.Scopes(macro=dict(macro.variables))
        token = token or CancellationToken()
        run_id = "run_" + uuid.uuid4().hex
        state = {"run_id": run_id, "steps_run": 0, "outcome": "ok", "ok": True}
        try:
            self._exec_steps(macro.actions, scopes, token, state, yes)
        except _Stop:
            pass
        except errors.MacroCancelledError:
            return results.err(errors.MacroCancelledError(),
                               data={**self._summary(state, scopes)})
        env = results.ok if state["ok"] else results.err
        if state["ok"]:
            return results.ok(capability="macro.run", data=self._summary(state, scopes))
        return results.err(("macro_failed", f"step failed: {state['outcome']}"),
                           data=self._summary(state, scopes))

    def _summary(self, state, scopes):
        return {"run_id": state["run_id"], "steps_run": state["steps_run"],
                "outcome": state["outcome"], "variables": V.redacted_view(scopes)}

    def _exec_steps(self, steps, scopes, token, state, yes):
        for step in steps:
            if token.cancelled:
                raise errors.MacroCancelledError()
            self._exec_step(step, scopes, token, state, yes)

    def _exec_step(self, step, scopes, token, state, yes):
        t = step["type"]
        if t == "stop":
            raise _Stop()
        if t == "set":
            val = step.get("value")
            if isinstance(val, str):
                val = V.interpolate(val, scopes)
            scopes.set(step["var"], val, step.get("scope", "runtime"))
            return
        if t == "wait":
            self._sleep(step.get("seconds", 0))
            return
        if t == "audit_note":
            self._audit_note(V.interpolate(step.get("text", ""), scopes), state)
            return
        if t in PHONE_VERBS:
            self._exec_action(step, scopes, state, yes)
            return
        # control-flow steps (if/switch/for_each/loop/retry/race/try/confirm)
        # are added in Task 5; reaching here for an unknown type is a guard.
        raise errors.MacroValidationError(f"unsupported step at runtime: {t!r}")

    def _exec_action(self, step, scopes, state, yes):
        verb = "set_text" if step["type"] == "set_text" else step["type"]
        target = self._resolve_target(step, scopes)
        fn = self._fn_for(step, scopes)
        env = self._run_action(verb, fn, target, build=self._build, yes=yes,
                               cfg=self._cfg, request_id=None,
                               idempotency_key=step.get("idempotency_key"))
        state["steps_run"] += 1
        if not env.get("ok"):
            state["ok"] = False
            state["outcome"] = env.get("error", {}).get("code", "error")
            raise _Stop()

    def _resolve_target(self, step, scopes):
        target = dict(step.get("target", {}))
        for k, v in list(target.items()):
            if isinstance(v, str):
                target[k] = V.interpolate(v, scopes)
        # carry verb-specific fields (text/package/keycode/...) into target
        for k in ("text", "package", "keycode", "selector", "direction"):
            if k in step and k not in target:
                v = step[k]
                target[k] = V.interpolate(v, scopes) if isinstance(v, str) else v
        return target

    def _audit_note(self, text, state):
        from phonectl import audit
        if hasattr(audit, "log_note"):
            audit.log_note(text)
```

(`run_action` already mints a `request_id` when passed `None`; the engine passes `idempotency_key` from the
step so a re-run is replay-safe per Plan 2.1.)

- [x] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_macro_engine.py -v`
Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add src/phonectl/macro/engine.py tests/test_macro_engine.py
git commit -m "feat: macro executor — sequential actions via run_action + set/wait/stop/audit_note + cancellation"
```

---

### Task 5: Control flow — `if`/`switch`/`for_each`/`loop`/`retry`/`race`/`try`/`confirm`

**Files:**
- Modify: `src/phonectl/macro/engine.py`
- Create: `src/phonectl/macro/conditions.py` (minimal evaluator stub; the full vocabulary lands in 6.2)
- Test: `tests/test_macro_engine.py` (append), `tests/test_macro_control_flow.py`

**Interfaces:**
- `conditions.evaluate(spec, ctx) -> bool` — **pure**; this plan ships only the conditions the executor
  needs for control flow tests (`always`/`never`, `variable` comparison); Plan 6.2 extends the vocabulary.
  `ctx` carries `{"scopes": Scopes, "snapshot": last_snapshot}`.
- Engine additions: `if` (eval `condition`, run `then`/`else`), `switch` (interpolate `on`, pick `cases`
  key or `default`), `for_each` (iterate `${in}` list, bind `as` into runtime scope, run `do`), `loop`
  (run `do` while `while` holds, bounded by `max_iterations`, default 100), `retry` (run `do`; on a
  **retryable** error envelope, bounded backoff via injected `sleep`, up to `max_attempts`; **re-check
  policy before replaying a high/critical step** — D7), `race` (return when any `any` condition holds or
  `timeout`), `try` (run `do`; always run `finally`).

- [x] **Step 1: Write the failing test**

```python
# tests/test_macro_control_flow.py
from phonectl.macro import schema
from phonectl.macro.engine import Engine


def _eng(rec, run_action=None):
    def default_ra(verb, fn, target, **kw):
        rec.append((verb, target))
        return {"ok": True, "data": {"hash": "h"}}
    return Engine(build=lambda cfg: (None, None, None),
                  run_action=run_action or default_ra,
                  fn_for=lambda step, scopes: (lambda b, s: None),
                  sleep=lambda s: None)


def test_if_then_branch():
    rec = []
    m = schema.parse({"name": "m", "actions": [
        {"type": "if", "condition": {"type": "always"},
         "then": [{"type": "tap", "target": {"i": 1}}],
         "else": [{"type": "tap", "target": {"i": 2}}]}]})
    _eng(rec).run(m)
    assert rec == [("tap", {"i": 1})]


def test_for_each_iterates():
    rec = []
    m = schema.parse({"name": "m", "variables": {"rows": ["a", "b", "c"]}, "actions": [
        {"type": "for_each", "in": "${rows}", "as": "row",
         "do": [{"type": "type", "target": {"text": "${row}"}}]}]})
    _eng(rec).run(m)
    assert [t["text"] for _, t in rec] == ["a", "b", "c"]


def test_loop_bounded_by_max_iterations():
    rec = []
    m = schema.parse({"name": "m", "actions": [
        {"type": "loop", "while": {"type": "always"}, "max_iterations": 3,
         "do": [{"type": "tap", "target": {"i": 0}}]}]})
    _eng(rec).run(m)
    assert len(rec) == 3


def test_retry_succeeds_after_transient_busy():
    calls = {"n": 0}

    def flaky(verb, fn, target, **kw):
        calls["n"] += 1
        if calls["n"] < 2:
            return {"ok": False, "error": {"code": "busy", "retryable": True}}
        return {"ok": True, "data": {"hash": "h"}}

    rec = []
    m = schema.parse({"name": "m", "actions": [
        {"type": "retry", "max_attempts": 3, "backoff_seconds": 0,
         "do": [{"type": "tap", "target": {"i": 0}}]}]})
    out = _eng(rec, run_action=flaky).run(m)
    assert out["ok"] is True and calls["n"] == 2


def test_try_runs_finally_even_on_failure():
    rec = []

    def failing(verb, fn, target, **kw):
        rec.append((verb, target))
        if target.get("i") == 9:
            return {"ok": False, "error": {"code": "guarded_action"}}
        return {"ok": True, "data": {}}

    m = schema.parse({"name": "m", "actions": [
        {"type": "try", "do": [{"type": "tap", "target": {"i": 9}}],
         "finally": [{"type": "tap", "target": {"i": 0}}]}]})
    _eng(rec, run_action=failing).run(m)
    assert ("tap", {"i": 0}) in rec  # finally ran
```

- [x] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_macro_control_flow.py -v`
Expected: FAIL (`unsupported step at runtime: 'if'` etc.).

- [x] **Step 3: Write minimal implementation**

```python
# src/phonectl/macro/conditions.py
"""Minimal pure condition evaluator (extended in Plan 6.2)."""
from __future__ import annotations

from phonectl.macro import variables as V

_OPS = {"eq": lambda a, b: a == b, "ne": lambda a, b: a != b,
        "lt": lambda a, b: a < b, "gt": lambda a, b: a > b}


def evaluate(spec, ctx) -> bool:
    t = spec.get("type")
    if t == "always":
        return True
    if t == "never":
        return False
    if t == "variable":
        scopes = ctx["scopes"]
        left = scopes.get(spec["var"])
        return _OPS[spec.get("op", "eq")](left, spec.get("value"))
    raise NotImplementedError(f"condition {t!r} lands in Plan 6.2")
```

```python
# src/phonectl/macro/engine.py — extend _exec_step before the final raise
        from phonectl.macro import conditions

        if t == "if":
            ctx = {"scopes": scopes, "snapshot": None}
            branch = step["then"] if conditions.evaluate(step["condition"], ctx) else step.get("else", [])
            self._exec_steps(branch, scopes, token, state, yes)
            return
        if t == "switch":
            key = V.interpolate(str(step["on"]), scopes)
            body = (step.get("cases") or {}).get(key, step.get("default", []))
            self._exec_steps(body, scopes, token, state, yes)
            return
        if t == "for_each":
            items = scopes.get(step["in"].strip("${}")) if isinstance(step["in"], str) else step["in"]
            for item in (items or []):
                scopes.set(step["as"], item, "runtime")
                self._exec_steps(step["do"], scopes, token, state, yes)
            return
        if t == "loop":
            ctx = {"scopes": scopes, "snapshot": None}
            i = 0
            cap = step.get("max_iterations", 100)
            while i < cap and conditions.evaluate(step.get("while", {"type": "always"}), ctx):
                self._exec_steps(step["do"], scopes, token, state, yes)
                i += 1
            return
        if t == "retry":
            self._exec_retry(step, scopes, token, state, yes)
            return
        if t == "try":
            try:
                self._exec_steps(step["do"], scopes, token, state, yes)
            except _Stop:
                state["ok"] = state["ok"]  # preserve outcome; finally still runs
            self._exec_steps(step.get("finally", []), scopes, token, state, yes)
            return
        if t == "confirm":
            msg = V.interpolate(step.get("message", "Proceed?"), scopes)
            if not self._confirm(msg):
                state["ok"] = False
                state["outcome"] = "confirmation_required"
                raise _Stop()
            return
        if t == "race":
            return  # condition-race lands with the full vocabulary in Plan 6.2
```

```python
# src/phonectl/macro/engine.py — retry with high-risk re-check (D7)
    _RETRYABLE = {"busy", "rate_limited", "observe_failed", "stale_snapshot"}

    def _exec_retry(self, step, scopes, token, state, yes):
        attempts = step.get("max_attempts", 3)
        backoff = step.get("backoff_seconds", 1.0)
        for attempt in range(attempts):
            saved_ok, saved_outcome = state["ok"], state["outcome"]
            try:
                self._exec_steps(step["do"], scopes, token, state, yes)
            except _Stop:
                pass
            if state["ok"]:
                return
            if state["outcome"] not in self._RETRYABLE or attempt == attempts - 1:
                raise _Stop()
            # bounded backoff, then retry; high-risk re-check happens in _exec_action's
            # run_action policy gate, which re-classifies on each replay (D7).
            state["ok"], state["outcome"] = saved_ok, saved_outcome
            self._sleep(backoff * (2 ** attempt))
```

(Re-checking policy on replay is automatic: every `run_action` call re-evaluates the risk policy against the
*current* snapshot, so a high-risk replay cannot bypass the gate even if the first attempt was granted.)

- [x] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_macro_engine.py tests/test_macro_control_flow.py -v`
Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add src/phonectl/macro/engine.py src/phonectl/macro/conditions.py tests/test_macro_control_flow.py tests/test_macro_engine.py
git commit -m "feat: macro control flow — if/switch/for_each/loop/retry(backoff+re-check)/try/confirm"
```

---

### Task 6: Phone-verb → `fn` mapping (`cli.macro_fn_for`) + the `loader`

**Files:**
- Modify: `src/phonectl/cli.py` (add `macro_fn_for`), reuse the existing selector/index resolution
- Create: `src/phonectl/macro/loader.py`
- Test: `tests/test_macro_fn_for.py`, `tests/test_macro_loader.py`

**Interfaces:**
- `cli.macro_fn_for(step, scopes) -> fn` — maps a phone-verb step to the `fn(backend, session)` callable
  `run_action` expects, **reusing** the same actuator/selector helpers the CLI verbs already use (index,
  selector, `(x,y)`, text, package, keycode). Unknown verb → `errors.MacroValidationError`.
- `loader.load(path) -> dict` — read a macro file. `.json` via stdlib `json`. `.yaml`/`.yml` only if the
  optional `[yaml]` extra is importable, else `errors.MacroValidationError` with an actionable message
  ("install phonectl[yaml] to load YAML macros"). The pure parser never sees YAML.

- [x] **Step 1: Write the failing test**

```python
# tests/test_macro_fn_for.py
from phonectl import cli
from phonectl.macro import variables as V


def test_fn_for_tap_index_resolves_via_actuator(monkeypatch):
    calls = {}
    import phonectl.actuator as actuator
    monkeypatch.setattr(actuator, "tap", lambda b, s, **kw: calls.setdefault("tap", kw) or {"hash": "h"})
    fn = cli.macro_fn_for({"type": "tap", "target": {"i": 4}}, V.Scopes())
    fn("BACKEND", "SESSION")
    assert calls["tap"]["i"] == 4


def test_fn_for_unknown_verb_raises():
    import pytest
    from phonectl import errors
    with pytest.raises(errors.MacroValidationError):
        cli.macro_fn_for({"type": "frobnicate"}, V.Scopes())
```

```python
# tests/test_macro_loader.py
import json
import pytest

from phonectl import errors
from phonectl.macro import loader


def test_load_json(tmp_path):
    p = tmp_path / "m.json"
    p.write_text(json.dumps({"name": "m", "actions": []}))
    assert loader.load(str(p))["name"] == "m"


def test_load_yaml_without_extra_raises(tmp_path, monkeypatch):
    p = tmp_path / "m.yaml"
    p.write_text("name: m\nactions: []\n")
    monkeypatch.setattr(loader, "_import_yaml", lambda: None)
    with pytest.raises(errors.MacroValidationError):
        loader.load(str(p))
```

- [x] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_macro_fn_for.py tests/test_macro_loader.py -v`
Expected: FAIL (`AttributeError: macro_fn_for` / `ModuleNotFoundError: phonectl.macro.loader`).

- [x] **Step 3: Write minimal implementation**

```python
# src/phonectl/cli.py — add (reuse the existing actuator/selector helpers the CLI verbs use)
from phonectl.macro import variables as _mvars


def macro_fn_for(step, scopes):
    from phonectl import actuator, errors
    verb = step["type"]
    target = dict(step.get("target", {}))

    def _interp(v):
        return _mvars.interpolate(v, scopes) if isinstance(v, str) else v

    if verb == "tap":
        if "i" in target:
            return lambda b, s: actuator.tap(b, s, i=target["i"])
        if "selector" in target:
            return lambda b, s: actuator.tap(b, s, selector=target["selector"])
        return lambda b, s: actuator.tap(b, s, x=target["x"], y=target["y"])
    if verb in ("type", "set_text"):
        text = _interp(step.get("text", target.get("text", "")))
        return lambda b, s: actuator.type_text(b, s, text)
    if verb == "launch":
        pkg = _interp(step["package"])
        return lambda b, s: actuator.launch(b, s, pkg)
    if verb == "key":
        kc = step["keycode"]
        return lambda b, s: actuator.key(b, s, kc)
    if verb == "swipe":
        return lambda b, s: actuator.swipe(b, s, **target)
    # clipboard_*/intent/notification_*/scroll_until route through their providers
    # via the same helpers the CLI verbs call; add each here as the verb is wired.
    raise errors.MacroValidationError(f"no macro fn mapping for verb {verb!r}")
```

```python
# src/phonectl/macro/loader.py
"""Macro file loader: JSON (stdlib) + optional YAML extra at the edge only."""
from __future__ import annotations

import json

from phonectl import errors


def _import_yaml():
    try:
        import yaml  # optional extra: phonectl[yaml]
        return yaml
    except Exception:
        return None


def load(path) -> dict:
    text = open(path).read()
    if path.endswith((".yaml", ".yml")):
        yaml = _import_yaml()
        if yaml is None:
            raise errors.MacroValidationError(
                "YAML macros require the optional extra: pip install 'phonectl[yaml]'")
        return yaml.safe_load(text)
    return json.loads(text)
```

```toml
# pyproject.toml — add (alongside any existing optional-dependencies, e.g. mcp)
[project.optional-dependencies]
yaml = ["PyYAML>=6"]
```

- [x] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_macro_fn_for.py tests/test_macro_loader.py -v`
Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add src/phonectl/cli.py src/phonectl/macro/loader.py pyproject.toml tests/test_macro_fn_for.py tests/test_macro_loader.py
git commit -m "feat: phone-verb->fn mapping (cli.macro_fn_for) + JSON loader with optional [yaml] extra"
```

---

### Task 7: Macro-run records → `runs.jsonl` lineage + `MacroRun` summary

**Files:**
- Create: `src/phonectl/macro/records.py`
- Modify: `src/phonectl/macro/engine.py` (set `parent_task_id` on action records; append the summary)
- Test: `tests/test_macro_records.py`, `tests/test_macro_engine.py` (append)

**Interfaces:**
- `records.macro_run_record(state, macro, *, trigger, now) -> dict` — **pure**: build the `kind="macro_run"`
  summary (`run_id`, `macro_name`, `trigger`, `outcome`, `steps_run`, `started_at`, `ended_at`,
  `cancelled`).
- `records.append(record) -> None` / `records.read(kind=None, limit=None) -> list[dict]` — append/read
  `$PHONECTL_HOME/runs.jsonl`; reuse the Plan 5.1 `daemon/records.py` writer when present (import-guard) so
  there is one `runs.jsonl` writer.
- Engine: pass `parent_task_id=run_id` into each `run_action` call (so action records join the run); append
  exactly one `MacroRun` summary per `run`.

- [x] **Step 1: Write the failing test**

```python
# tests/test_macro_records.py
from phonectl.macro import records


def test_macro_run_record_shape():
    state = {"run_id": "run_1", "steps_run": 2, "outcome": "ok"}
    rec = records.macro_run_record(state, _M("reply"), trigger="manual", now=lambda: 5.0)
    assert rec["kind"] == "macro_run"
    assert rec["run_id"] == "run_1" and rec["macro_name"] == "reply"
    assert rec["steps_run"] == 2 and rec["outcome"] == "ok"
    assert rec["cancelled"] is False


def test_append_and_read_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    records.append({"kind": "macro_run", "run_id": "r1"})
    records.append({"kind": "action", "action_id": "a1"})
    assert [r["run_id"] for r in records.read(kind="macro_run")] == ["r1"]


class _M:
    def __init__(self, name):
        self.name = name
```

```python
# tests/test_macro_engine.py — append
def test_run_sets_parent_task_id_on_actions():
    seen = {}

    def ra(verb, fn, target, **kw):
        seen["parent"] = kw.get("parent_task_id")
        return {"ok": True, "data": {}}

    from phonectl.macro import schema
    from phonectl.macro.engine import Engine
    eng = Engine(build=lambda cfg: (None, None, None), run_action=ra,
                 fn_for=lambda step, scopes: (lambda b, s: None))
    out = eng.run(schema.parse({"name": "m", "actions": [{"type": "tap", "target": {"i": 0}}]}))
    assert seen["parent"] == out["data"]["run_id"]
```

- [x] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_macro_records.py tests/test_macro_engine.py -v -k "record or parent_task"`
Expected: FAIL (`ModuleNotFoundError` / `parent_task_id` not threaded).

- [x] **Step 3: Write minimal implementation**

```python
# src/phonectl/macro/records.py
"""Macro-run records: lineage + MacroRun summary, layered on runs.jsonl."""
from __future__ import annotations

import json
import time

from phonectl.config import config_dir


def _path():
    return config_dir() / "runs.jsonl"


def macro_run_record(state, macro, *, trigger="manual", now=time.time) -> dict:
    return {
        "kind": "macro_run",
        "run_id": state["run_id"],
        "macro_name": macro.name,
        "trigger": trigger,
        "outcome": state.get("outcome", "ok"),
        "steps_run": state.get("steps_run", 0),
        "started_at": state.get("started_at"),
        "ended_at": now(),
        "cancelled": state.get("cancelled", False),
    }


def append(record) -> None:
    try:
        from phonectl.daemon import records as drec  # one writer when the daemon ships
        drec.append(record)
        return
    except Exception:
        pass
    with open(_path(), "a") as f:
        f.write(json.dumps(record) + "\n")


def read(kind=None, limit=None) -> list:
    p = _path()
    if not p.exists():
        return []
    rows = [json.loads(x) for x in p.read_text().splitlines() if x.strip()]
    if kind is not None:
        rows = [r for r in rows if r.get("kind") == kind]
    return rows[-limit:] if limit else rows
```

```python
# src/phonectl/macro/engine.py — thread parent_task_id + append summary
# in run(): record state["started_at"] = self._now() before _exec_steps; after, append summary
        from phonectl.macro import records as _records
        env = ... # existing envelope build
        state["cancelled"] = ... # True on MacroCancelledError path
        _records.append(_records.macro_run_record(state, macro, trigger=trigger, now=self._now))
        return env
# in _exec_action(): pass parent_task_id=state["run_id"] into self._run_action(...)
```

- [x] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_macro_records.py tests/test_macro_engine.py -v`
Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add src/phonectl/macro/records.py src/phonectl/macro/engine.py tests/test_macro_records.py tests/test_macro_engine.py
git commit -m "feat: macro-run lineage (parent_task_id) + MacroRun summary in runs.jsonl"
```

---

### Task 8: Daemon RPC handlers — `macro_validate/run/cancel/status`

**Files:**
- Modify: `src/phonectl/daemon/server.py`
- Test: `tests/test_daemon_server.py` (append)

**Interfaces (each returns a `results` envelope; gated so a no-daemon build is unaffected):**
- `macro_validate` — `schema.validate(params["macro"])`; `results.ok(data={"valid": bool, "errors": [...]})`.
- `macro_run` — build an `Engine` over the warm triple (`build=lambda cfg: self._warm_triple()`); run
  `schema.parse(params["macro"])`; track the live run by `run_id` so `macro_cancel` can reach its token.
  Mutating ⇒ added to `rpc.MUTATING` (it drives action steps under the single writer).
- `macro_cancel` — flip the token for `params["run_id"]`; `results.ok`.
- `macro_status` — return the last/live state for a `run_id` (or recent `MacroRun` records).

- [x] **Step 1: Write the failing test**

```python
# tests/test_daemon_server.py — append
def test_macro_validate_method(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    srv = _srv(tmp_path)
    resp = json.loads(srv.handle_line(_req("macro_validate",
        {"macro": {"name": "m", "actions": [{"type": "tap", "target": {"i": 0}}]}})))
    assert resp["ok"] is True and resp["data"]["valid"] is True


def test_macro_validate_reports_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    srv = _srv(tmp_path)
    resp = json.loads(srv.handle_line(_req("macro_validate", {"macro": {"actions": []}})))
    assert resp["ok"] is True and resp["data"]["valid"] is False and resp["data"]["errors"]


def test_macro_run_executes_actions(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    srv = _srv(tmp_path)
    resp = json.loads(srv.handle_line(_req("macro_run",
        {"macro": {"name": "m", "actions": [{"type": "tap", "target": {"i": 0}, "i": 0}]}})))
    assert resp["ok"] is True and resp["data"]["run_id"].startswith("run_")


def test_macro_in_mutating_set():
    from phonectl.daemon import rpc
    assert {"macro_run", "macro_cancel"} <= rpc.MUTATING
```

- [x] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_daemon_server.py -v -k "macro"`
Expected: FAIL (`unknown_method` / `macro_run` not in MUTATING).

- [x] **Step 3: Write minimal implementation**

```python
# src/phonectl/daemon/rpc.py — extend the MUTATING set
MUTATING = {"act", "stop", "resume", "macro_run", "macro_cancel"}
```

```python
# src/phonectl/daemon/server.py — in _register_builtins
        from phonectl.macro import schema as _mschema
        from phonectl.macro.engine import Engine as _Engine, CancellationToken as _Token

        @self.registry.register("macro_validate")
        def _macro_validate(params, ctx):
            errs = _mschema.validate(params["macro"])
            return results.ok(capability="macro.validate",
                              data={"valid": not errs, "errors": errs})

        @self.registry.register("macro_run")
        def _macro_run(params, ctx):
            macro = _mschema.parse(params["macro"])
            token = _Token()
            eng = _Engine(build=lambda cfg: self._warm_triple(), cfg=self._cfg)
            env = eng.run(macro, token=token, yes=bool(params.get("yes", False)))
            rid = env.get("data", {}).get("run_id")
            if rid:
                self._macro_tokens.pop(rid, None)
            return env

        @self.registry.register("macro_cancel")
        def _macro_cancel(params, ctx):
            tok = self._macro_tokens.get(params["run_id"])
            if tok is not None:
                tok.cancel()
            return results.ok(capability="macro.cancel", data={"cancelled": tok is not None})

        @self.registry.register("macro_status")
        def _macro_status(params, ctx):
            from phonectl.macro import records as _records
            return results.ok(capability="macro.status",
                              data={"runs": _records.read(kind="macro_run", limit=params.get("limit", 10))})
```

(Add `self._macro_tokens = {}` in `__init__`; register the live token keyed by `run_id` before `eng.run`
returns so an in-flight macro is cancellable — the engine exposes the `run_id` via a `token`-keyed callback
or by minting the id in the handler and passing it in. Keep the token registry on the server so
`macro_cancel` reaches it.)

- [x] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_daemon_server.py -v -k "macro"`
Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add src/phonectl/daemon/rpc.py src/phonectl/daemon/server.py tests/test_daemon_server.py
git commit -m "feat: daemon RPC — macro_validate/run/cancel/status (run_/cancel under single writer)"
```

---

### Task 9: CLI `phonectl macro` group + MCP `phone.macro.*` tools + docs

**Files:**
- Modify: `src/phonectl/cli.py`, `src/phonectl/mcp_server.py`, `README.md`,
  `docs/superpowers/phonectl-platform-roadmap.md` (mark 6.1 implemented in the Phase index note)
- Create: `docs/macros.md`
- Test: `tests/test_cli.py` (append), `tests/test_mcp_server.py` (append)

**Interfaces:**
- CLI: `phonectl macro validate <file>`, `phonectl macro run <file> [--yes]`, `phonectl macro status
  [--limit N]`, `phonectl macro cancel <run_id>`. Each routes through `_dispatch` (Plan 5.1) when a daemon
  is reachable, else runs in-process (`loader.load` → `schema.parse` → `Engine().run`). `--json` prints the
  envelope.
- MCP: `phone.macro.validate`, `phone.macro.run`, `phone.macro.cancel`, `phone.macro.status` added to the
  Plan-2.3 `TOOLS` registry, each returning the `results` envelope.
- Run the **full suite** on this final task.

- [x] **Step 1: Write the failing test**

```python
# tests/test_cli.py — append
def test_macro_run_in_process(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_daemon_client", lambda cfg: None)
    monkeypatch.setattr(cli, "build_runtime", lambda cfg: (FakeBackend(), Session(), _FakeConn()))
    p = tmp_path / "m.json"
    p.write_text(json.dumps({"name": "m", "actions": [{"type": "tap", "target": {"i": 0}, "i": 0}]}))
    rc = cli.main(["macro", "run", str(p), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["ok"] is True and out["data"]["run_id"].startswith("run_")


def test_macro_validate_reports_errors_cli(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    p = tmp_path / "bad.json"
    p.write_text(json.dumps({"actions": []}))
    rc = cli.main(["macro", "validate", str(p), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert out["data"]["valid"] is False
```

```python
# tests/test_mcp_server.py — append
def test_macro_tools_registered():
    from phonectl import mcp_server
    assert "phone.macro.run" in mcp_server.TOOLS
    assert "phone.macro.validate" in mcp_server.TOOLS
```

(`_FakeConn` mirrors the existing test conn with an `ensure()` no-op; reuse the one in `tests/test_cli.py`.)

- [x] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli.py tests/test_mcp_server.py -v -k "macro"`
Expected: FAIL (no `macro` subcommand / tool).

- [x] **Step 3: Write minimal implementation**

Wire a `macro` subparser in `cli.main` whose handlers call `loader.load` → `schema.parse`/`validate` and
route action through `_dispatch("macro_run", {"macro": doc, "yes": args.yes}, in_process, cfg=cfg)` where
`in_process` builds an `Engine` over `build_runtime`. Register the four `phone.macro.*` handlers in
`mcp_server.TOOLS`, each calling the same engine/schema path and returning the envelope. Write `docs/macros.md`
(document the schema from spec §5, the `[yaml]` extra, the run/validate/cancel/status verbs, and the
progressive-autonomy note pointing forward to 6.3). Add a one-line README "Macros" section.

- [x] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli.py tests/test_mcp_server.py -v -k "macro"`
Expected: PASS.

- [x] **Step 5: Run the full suite, then commit**

Run: `pytest -v`
Expected: PASS (no existing test regressed; the macro engine is purely additive).

```bash
git add src/phonectl/cli.py src/phonectl/mcp_server.py README.md docs/macros.md docs/superpowers/phonectl-platform-roadmap.md tests/test_cli.py tests/test_mcp_server.py
git commit -m "feat: phonectl macro CLI group + phone.macro.* MCP tools + macro docs"
```

---

## Dependencies

- **Plan 2.1** (`runtime.run_action`, `redact.py`, audit v2) — the action funnel + redaction every macro
  step reuses; **Plan 2.2** (`policy`/`risk`) — the policy gate `run_action` re-checks on each replay (D7).
- **Plan 3.1** (`ProviderRegistry`, `cli.build_runtime`) — the warm/one-shot runtime macros act through.
- **Plans 1.2/3.2–3.5/4.x** — the actuator/selector/verb surface `cli.macro_fn_for` maps onto.
- **Plan 5.1** (daemon: `runs.jsonl` writer, `_dispatch` routing) — used opportunistically; gated so a
  no-daemon build runs macros in-process.

## Deferred (out of scope for 6.1)

- **Triggers + scheduler + condition vocabulary** → Plan **6.2** (this plan ships only `always`/`never`/
  `variable` conditions for control-flow tests and the **manual** run path).
- **Progressive autonomy gate + memory layer** → Plan **6.3** (the `confirm` step here is a literal
  author-placed gate; standing grants come in 6.3).
- **`http`/`webhook`/`media`/`settings` action steps, macro signing, parallelism** → spec §11 open
  questions.

## Notes on testability

- `schema.py`, `variables.py`, `conditions.py` are **pure** — fixture-tested with no I/O.
- The `Engine` injects `run_action`, `build`, `sleep`, `confirm`, `fn_for`, and `now`, so control flow,
  retry/backoff, cancellation, and lineage are tested **without** a device, a real clock, or a socket.
- `PHONECTL_HOME` isolation keeps `runs.jsonl` per-test.
- **No device behavior is claimed.** Every test drives injected fakes; a real macro against the phone is a
  manual on-device smoke (note it in `docs/macros.md`, do not run it in CI).
