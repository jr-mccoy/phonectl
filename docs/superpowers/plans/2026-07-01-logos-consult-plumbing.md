# Logos-Consult Plumbing (Part B1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make phonectl able to open Logos directly at a reference via a named, argument-driven macro (`logos-lookup`), by closing the two gaps the spike found — the macro engine doesn't map the `intent` verb, and macros take no CLI arguments.

**Architecture:** Add an `intent` branch to `macro_fn_for` that returns a `lambda b, s: b.intent_start(...)` (the backend already implements `intent_start` → `am start -a VIEW -d <uri>`). Add a `--set K=V` flag to `macro run` that seeds the engine's `runtime` variable scope, so `${ref}` in a macro's `data` interpolates a caller-supplied reference. Ship a `logos-lookup.json` macro asset. All of this is testable with fakes — no device required.

**Tech Stack:** Python 3 stdlib, pytest, phonectl's existing `macro`/`providers`/`actuator` modules.

**Related plans (same initiative):** design doc `../../../../../sdcard/Projects/original-language-translation-lab/docs/superpowers/specs/2026-07-01-phase2-lexicon-and-logos-lookup-design.md` (§6); sibling lab plan `/sdcard/Projects/original-language-translation-lab/docs/superpowers/plans/2026-07-01-phase2-lexicon-framework.md`. **B2** (OCR capture loop) is a follow-on plan, gated on Wireless-Debugging pairing + an OCR feasibility check — not in scope here.

## Global Constraints

- **Backend isolation** — only `adb_backend.py` touches adb/subprocess. The macro fn calls `backend.intent_start(...)`; it must not shell out itself.
- **One choke-point** — every action runs through `runtime.run_action`; the macro engine already routes `intent` steps through it (verb key = `step["type"]`). Do not bypass.
- **Stdlib-only runtime**, Python ≥ 3.9; structured `results` envelopes; `PHONECTL_HOME` isolation in tests; **no device in tests** (use fakes, mirror `tests/test_providers_intents.py`).
- **TDD, one commit per task.**
- **Deep-link URIs** use Logos's registered schemes: `logosref:` (references), `logosres:<id>;ref=<headword>` (resource-at-entry, needs resource IDs — deferred to B2). B1 handles references only.

---

### Task 1: Map the `intent` verb in `macro_fn_for`

**Files:**
- Modify: `src/phonectl/cli.py:33-61` (`macro_fn_for`)
- Test: `tests/test_macro_intent.py`

**Interfaces:**
- Consumes: `variables.interpolate` via the existing `_interp` closure; `backend.intent_start(*, action, data, component, extras)` (`adb_backend.py:92-105`).
- Produces: a macro step `{"type":"intent","action":...,"data":...,"component":?,"extras":?}` maps to `lambda b, s: b.intent_start(action=..., data=..., component=..., extras=...)`, with `${var}` interpolation on `action`/`data`/`component`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_macro_intent.py
from phonectl.cli import macro_fn_for
from phonectl.macro.variables import Scopes

class _Backend:
    def __init__(self): self.kw = None
    def intent_start(self, **kwargs): self.kw = kwargs

def test_intent_verb_maps_and_interpolates():
    step = {"type": "intent",
            "action": "android.intent.action.VIEW",
            "data": "logosref:${ref}"}
    scopes = Scopes(runtime={"ref": "Bible.Mk1.9"})
    fn = macro_fn_for(step, scopes)
    b = _Backend()
    fn(b, None)
    assert b.kw["action"] == "android.intent.action.VIEW"
    assert b.kw["data"] == "logosref:Bible.Mk1.9"   # ${ref} interpolated
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_macro_intent.py -v`
Expected: FAIL — `MacroValidationError: no macro fn mapping for verb 'intent'`

- [ ] **Step 3: Write minimal implementation**

In `src/phonectl/cli.py`, inside `macro_fn_for`, add this branch immediately before the final `raise` (after the `swipe` branch at line 60):

```python
    if verb == "intent":
        action = _interp(step.get("action", "android.intent.action.VIEW"))
        data = _interp(step.get("data", target.get("data", "")))
        component = _interp(step["component"]) if step.get("component") else None
        extras = step.get("extras") or None
        return lambda b, s: b.intent_start(
            action=action, data=data, component=component, extras=extras
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_macro_intent.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/cli.py tests/test_macro_intent.py
git commit -m "feat(macro): map intent verb to backend.intent_start with interpolation"
```

---

### Task 2: `--set K=V` argument passing into macros

**Files:**
- Modify: `src/phonectl/cli.py:1659-1663` (argparse), `:1047-1058` (`_cmd_macro_run`)
- Test: `tests/test_macro_set_args.py`

**Interfaces:**
- Produces: `parse_set_overrides(pairs:list[str])->dict` (splits `"k=v"`, raises `ValueError` on a missing `=`); `_cmd_macro_run` seeds `V.Scopes(runtime=<overrides>, macro=dict(macro.variables))` and passes it to `eng.run(macro, scopes=..., yes=...)` (`engine.py:67` accepts `scopes=`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_macro_set_args.py
import pytest
from phonectl.cli import parse_set_overrides

def test_parse_pairs():
    assert parse_set_overrides(["ref=Bible.Mk1.9", "x=y"]) == {"ref": "Bible.Mk1.9", "x": "y"}

def test_parse_allows_equals_in_value():
    assert parse_set_overrides(["u=a=b"]) == {"u": "a=b"}

def test_parse_rejects_missing_equals():
    with pytest.raises(ValueError):
        parse_set_overrides(["nope"])

def test_empty_is_empty():
    assert parse_set_overrides([]) == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_macro_set_args.py -v`
Expected: FAIL — `ImportError: cannot import name 'parse_set_overrides'`

- [ ] **Step 3: Write minimal implementation**

Add the helper near `macro_fn_for` in `src/phonectl/cli.py`:

```python
def parse_set_overrides(pairs) -> dict:
    out = {}
    for item in pairs or []:
        if "=" not in item:
            raise ValueError(f"--set expects K=V, got {item!r}")
        k, v = item.split("=", 1)
        out[k] = v
    return out
```

Add the argparse flag after `mcr.add_argument("--json", ...)` (line 1662):

```python
    mcr.add_argument("--set", action="append", metavar="K=V", default=[],
                     help="seed a runtime macro variable (repeatable)")
```

Wire it into `_cmd_macro_run` — replace the local-engine branch (lines 1055-1058) with:

```python
        from phonectl.macro import variables as V
        macro = schema.parse(doc)
        overrides = parse_set_overrides(getattr(args, "set", []))
        scopes = V.Scopes(runtime=overrides, macro=dict(macro.variables))
        eng = Engine(build=build_runtime, cfg=cfg, fn_for=macro_fn_for)
        env = eng.run(macro, scopes=scopes, yes=bool(getattr(args, "yes", False)))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_macro_set_args.py tests/test_macro_intent.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/cli.py tests/test_macro_set_args.py
git commit -m "feat(macro): --set K=V seeds runtime variable scope for macro run"
```

---

### Task 3: `logos-lookup` macro asset + validation

**Files:**
- Create: `macros/logos-lookup.json`
- Test: `tests/test_logos_lookup_macro.py`

**Interfaces:**
- Consumes: `macro.loader.load`, `macro.schema.validate`/`parse`, `macro_fn_for` (Task 1).
- Produces: a shippable macro that opens a Logos reference. `variables.ref` default plus one `intent` action step with `data: "logosref:${ref}"`.

- [ ] **Step 1: Write the failing test + asset**

```json
// macros/logos-lookup.json
{
  "name": "logos-lookup",
  "version": 1,
  "variables": { "ref": "Bible.Mk1.9" },
  "actions": [
    { "type": "intent",
      "action": "android.intent.action.VIEW",
      "data": "logosref:${ref}" }
  ]
}
```

```python
# tests/test_logos_lookup_macro.py
from pathlib import Path
from phonectl.macro import loader, schema
from phonectl.cli import macro_fn_for
from phonectl.macro.variables import Scopes

MACRO = Path(__file__).resolve().parents[1] / "macros" / "logos-lookup.json"

class _Backend:
    def __init__(self): self.kw = None
    def intent_start(self, **kwargs): self.kw = kwargs

def test_macro_is_valid():
    doc = loader.load(str(MACRO))
    assert schema.validate(doc) == []          # no validation errors
    assert doc["name"] == "logos-lookup"

def test_macro_intent_step_builds_reference_uri():
    doc = loader.load(str(MACRO))
    step = doc["actions"][0]
    fn = macro_fn_for(step, Scopes(runtime={"ref": "Bible.Jn1.1"}))
    b = _Backend(); fn(b, None)
    assert b.kw["data"] == "logosref:Bible.Jn1.1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_logos_lookup_macro.py -v`
Expected: FAIL — `FileNotFoundError` (asset absent) until you create `macros/logos-lookup.json`; if `schema.validate` returns errors, adjust the asset to the real top-level key whitelist in `macro/schema.py:9-10`.

- [ ] **Step 3: Create the asset (above) and align to the real schema**

Read `src/phonectl/macro/schema.py:9-32` and confirm the macro's top-level keys (`name`, `version`, `variables`, `actions`) are all in the allowed set and that an `intent` step passes `PHONE_VERBS` (`macro/__init__.py:3-12`). If `validate` reports an unknown key, remove/rename it to match.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_logos_lookup_macro.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add macros/logos-lookup.json tests/test_logos_lookup_macro.py
git commit -m "feat(macro): ship logos-lookup reference-jump macro"
```

---

### Task 4: Screen-safe runbook (dry-run) + full-suite guard

**Files:**
- Modify: `docs/macros.md` (append a `logos-lookup` section)
- Test: run the full suite to prove no regressions

**Interfaces:** none (documentation + regression gate).

- [ ] **Step 1: Document the commands**

Append to `docs/macros.md`:

```markdown
## logos-lookup

Open Logos directly at a reference (no UI navigation). Requires the adb channel
(Wireless Debugging) since `intent`/`am start` is adb-backed.

    # validate structure (no device):
    phonectl macro validate macros/logos-lookup.json

    # dry-run — all gates run, am start is NOT executed (screen not foregrounded):
    PHONECTL_MODE=dry-run phonectl macro run macros/logos-lookup.json --set ref=Bible.Mk1.9

    # real run (foregrounds Logos at the reference):
    phonectl macro run macros/logos-lookup.json --set ref=Bible.Mk1.9 --yes

Zero-code equivalent (no macro):

    phonectl intent start --action android.intent.action.VIEW --data 'logosref:Bible.Mk1.9' --yes

Capturing the entry *text* (OCR) is Part B2 — a separate plan.
```

- [ ] **Step 2: Run the full test suite**

Run: `pytest -q`
Expected: PASS (existing suite + the 3 new test files; no regressions). If the macro engine has a dedicated dry-run gate test, confirm it still passes.

- [ ] **Step 3: Commit**

```bash
git add docs/macros.md
git commit -m "docs(macro): logos-lookup runbook (validate/dry-run/real)"
```

---

## Self-Review

**Spec coverage (design §6):** §6.3 phonectl changes → Task 1 (intent mapping) + Task 2 (`--set`). §6.1 `logos-lookup` navigate sub-capability → Task 3 asset. §6.6 screen-safe dry-run → Task 4 runbook. **Deferred to B2 (by design, §6.2/§6.3a/§6.5):** the OCR capture loop, a11y-reference reading, `private_only` filing, and `logosres:` lexicon-at-lemma URIs — all gated on the Wireless-Debugging pairing + OCR feasibility check.

**Placeholder scan:** Task 3 Step 3 intentionally reconciles the asset against the real `schema.py` key whitelist (the one on-the-ground check); all other steps carry complete code/content. No TBD/TODO.

**Type consistency:** `parse_set_overrides`, `macro_fn_for`'s `intent` branch (`action`/`data`/`component`/`extras`), and `V.Scopes(runtime=...,macro=...)` → `eng.run(macro, scopes=...)` match the real signatures in `cli.py:33-61`, `engine.py:67`, and `variables.py:16`. The fake-backend test pattern mirrors `tests/test_providers_intents.py`.
