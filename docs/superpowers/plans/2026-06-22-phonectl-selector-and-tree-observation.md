# phonectl Selector + Tree Observation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Plan 1.2 of the platform roadmap** (`docs/superpowers/phonectl-platform-roadmap.md`). Depends on
**Plan 1.1** for `errors.StaleSnapshotError` and the `results` envelope.

**Goal:** Add **selector-based targeting** and **hierarchy-preserving observation** alongside element
indices, plus **stale-snapshot protection**, so an agent survives list scrolls, keyboard opens, layout
updates, and OEM node reordering, and never acts on a screen that moved under it (strategy §5; backlog
§16 #1, #2, #5).

**Architecture:** `ui_parser` grows pure functions for **richer element metadata**, a **tree** +
**relations** view, and **selector matching** — all str→data, no I/O, fixture-tested. `observer.observe`
optionally embeds `tree`/`relations` and an `observed_at` stamp while keeping the flat default cheap.
`session` resolves selectors against the cached snapshot. `actuator` accepts `selector=` plus
`expected_hash=`/`stale_ok=` and raises `errors.StaleSnapshotError` on drift. `cli` gains
`--selector/--text/--id/--nth` and `--expected-hash/--stale-ok`. Element index `i` remains a valid
target; selectors are the durable target; raw `(x,y)` stays the escape hatch.

**Tech Stack:** Python 3 (stdlib only: `re`, `json`, `time`, `argparse`); `pytest` for tests; `adb`
remains the only external runtime dependency.

## Global Constraints

- **stdlib-only at runtime** (Python ≥ 3.9; `pytest` dev-only).
- **ONLY `adb_backend.py` may touch adb/subprocess.** This plan adds no backend I/O.
- **`ui_parser.py` stays pure** — `build_tree`, `parse_relations`, `match_selector`, and the extended
  `parse_elements` are all side-effect-free (no I/O, no `time`, no `print`).
- **Element index `i` is a primary target; selectors are the durable target; raw `(x,y)` is the escape
  hatch.**
- **Every actuator `act()` re-observes** — returns the post-action `observer.observe()` snapshot.
- **Tests isolate via** `monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))` where config/audit is touched.
- **One commit per task.**
- **TDD order is non-negotiable.**

## Shared conventions used by this plan

- **Selector grammar (strategy §5.1)** — a JSON object; any subset of: `text`, `text_regex`,
  `content_desc`, `resource_id`, `class`, `clickable`, `enabled`, `checked` (and other boolean flags),
  `ancestor_text`, `sibling_text`, `bounds_near` (`[x1,y1,x2,y2]`), `nth_match` (int, default 0). All
  present keys must match (AND); `nth_match` selects among ranked candidates.
- **`errors.StaleSnapshotError`** comes from Plan 1.1; this plan is the first *producer* of it.
- **`screen_hash` stays the change-detector** — extending element metadata must NOT change the existing
  `text|id|bounds` hash recipe (so Plan 1.1/core tests stay green); new fields are additive only.

---

### Task 1: `ui_parser` richer element metadata

Extend `parse_elements` to preserve more node state (strategy §5.3) while keeping every existing field
and the `screen_hash` recipe unchanged.

**Files:**
- Modify: `src/phonectl/ui_parser.py` (`parse_elements`, currently lines 25–49)
- Test: `tests/test_ui_parser.py` (append)

**Interfaces:**
- Produces: each element dict gains `enabled`, `focused`, `checkable`, `checked`, `scrollable`,
  `long_clickable`, `password`, `selected`, `editable`, `package`, and (when present) `hint_text` /
  `error_text`. Booleans parse the `"true"`/`"false"` attrs; `editable` derives from
  `class` containing `EditText` OR an `editable="true"` attr when the ROM provides it; `package` reads
  the `package` attr. Existing keys (`i`, `text`, `id`, `class`, `content_desc`, `clickable`, `bounds`,
  `center`) are unchanged. `screen_hash` is untouched.

- [x] **Step 1: Write the failing test**

```python
# tests/test_ui_parser.py  (append below existing tests)

RICH_NODE = (
    "<?xml version='1.0'?><hierarchy rotation=\"0\">"
    "<node index=\"0\" text=\"Wi-Fi\" resource-id=\"android:id/title\" "
    "class=\"android.widget.Switch\" content-desc=\"\" package=\"com.android.settings\" "
    "clickable=\"true\" enabled=\"true\" focused=\"false\" checkable=\"true\" "
    "checked=\"true\" scrollable=\"false\" long-clickable=\"true\" password=\"false\" "
    "selected=\"false\" bounds=\"[44,380][1036,520]\"/>"
    "<node index=\"1\" text=\"\" resource-id=\"x:id/field\" class=\"android.widget.EditText\" "
    "content-desc=\"Search\" package=\"com.android.settings\" clickable=\"true\" "
    "enabled=\"false\" password=\"true\" bounds=\"[0,600][1080,700]\"/>"
    "</hierarchy>")


def test_parse_elements_captures_richer_metadata():
    els = ui_parser.parse_elements(RICH_NODE)
    sw = els[0]
    assert sw["enabled"] is True
    assert sw["checkable"] is True
    assert sw["checked"] is True
    assert sw["long_clickable"] is True
    assert sw["package"] == "com.android.settings"
    field = els[1]
    assert field["editable"] is True          # EditText
    assert field["password"] is True
    assert field["enabled"] is False


def test_existing_fields_and_hash_unchanged():
    els = ui_parser.parse_elements(RICH_NODE)
    assert els[0]["text"] == "Wi-Fi"
    assert els[0]["center"] == [540, 450]
    # hash recipe is still text|id|bounds — adding metadata must not perturb it
    h = ui_parser.screen_hash(els)
    assert isinstance(h, str) and len(h) == 40
```

- [x] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_ui_parser.py -v`
Expected: FAIL (`KeyError: 'enabled'` — metadata not yet captured).

- [x] **Step 3: Write minimal implementation**

In `parse_elements`, read the extra attrs and add them to the element dict. Suggested helper inside the
loop:

```python
        def _b(attr, default="false"):
            return node.get(attr, default) == "true"

        cls = node.get("class", "") or ""
        elements.append({
            "i": i,
            "text": text,
            "id": node.get("resource-id", "") or "",
            "class": cls,
            "content_desc": desc,
            "clickable": clickable,
            "enabled": _b("enabled", "true"),
            "focused": _b("focused"),
            "checkable": _b("checkable"),
            "checked": _b("checked"),
            "scrollable": _b("scrollable"),
            "long_clickable": _b("long-clickable"),
            "password": _b("password"),
            "selected": _b("selected"),
            "editable": _b("editable") or "EditText" in cls,
            "package": node.get("package", "") or "",
            "bounds": [x1, y1, x2, y2],
            "center": [(x1 + x2) // 2, (y1 + y2) // 2],
        })
```

Keep `screen_hash` exactly as-is (`text|id|bounds`). Only append optional `hint_text`/`error_text` keys
when the corresponding attrs are present (avoid bloating every element).

- [x] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_ui_parser.py -v`
Expected: PASS (existing tests + 2 new).

- [x] **Step 5: Commit**

```bash
git add src/phonectl/ui_parser.py tests/test_ui_parser.py
git commit -m "feat: richer element metadata in parse_elements (enabled/checked/editable/...)"
```

---

### Task 2: `ui_parser.build_tree` + `parse_relations`

Add hierarchy views keyed to the same indices `parse_elements` produces (strategy §5.2).

**Files:**
- Modify: `src/phonectl/ui_parser.py` (append)
- Test: `tests/test_ui_parser.py` (append)

**Interfaces (both PURE):**
- `build_tree(xml: str) -> dict` — a nested `{"i": int|None, "class": str, "children": [...]}` tree over
  the SAME meaningful nodes `parse_elements` keeps; non-meaningful container nodes carry `"i": None` but
  preserve structure so relations are computable.
- `parse_relations(xml: str) -> dict` — `{"parent": {i: pi}, "children": {i: [..]}, "siblings":
  {i: [..]}, "ancestors": {i: [..]}}`, keyed by the meaningful-element index `i` (ints as dict keys).

- [x] **Step 1: Write the failing test**

```python
# tests/test_ui_parser.py  (append)

NESTED = (
    "<?xml version='1.0'?><hierarchy rotation=\"0\">"
    "<node class=\"android.widget.FrameLayout\" bounds=\"[0,0][1080,2400]\">"
    "  <node text=\"Network\" class=\"T\" clickable=\"true\" bounds=\"[0,0][500,100]\"/>"
    "  <node class=\"android.widget.LinearLayout\" bounds=\"[0,100][1080,300]\">"
    "    <node text=\"Wi-Fi\" class=\"T\" clickable=\"true\" bounds=\"[0,100][500,200]\"/>"
    "    <node text=\"Bluetooth\" class=\"T\" clickable=\"true\" bounds=\"[0,200][500,300]\"/>"
    "  </node>"
    "</node></hierarchy>")


def test_build_tree_preserves_structure():
    tree = ui_parser.build_tree(NESTED)
    assert tree["class"].endswith("FrameLayout")
    # the LinearLayout holds the two leaf rows
    classes = [c["class"] for c in tree["children"]]
    assert any(c.endswith("LinearLayout") for c in classes)


def test_parse_relations_parent_children_siblings():
    rel = ui_parser.parse_relations(NESTED)
    els = ui_parser.parse_elements(NESTED)   # Network=0, Wi-Fi=1, Bluetooth=2
    assert [e["text"] for e in els] == ["Network", "Wi-Fi", "Bluetooth"]
    # Wi-Fi(1) and Bluetooth(2) share a parent -> siblings of each other
    assert 2 in rel["siblings"][1]
    assert 1 in rel["siblings"][2]
    # both have the same parent index
    assert rel["parent"][1] == rel["parent"][2]
```

- [x] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_ui_parser.py -v`
Expected: FAIL (`AttributeError: module 'phonectl.ui_parser' has no attribute 'build_tree'`).

- [x] **Step 3: Write minimal implementation**

Walk the parsed XML tree recursively, assigning each meaningful node the same sequential `i` that
`parse_elements` assigns (reuse `_is_meaningful`). Build `build_tree` during the walk; derive
`parse_relations` from the parent links. Keep both pure (operate on `ET.fromstring(_extract_hierarchy(
xml))`). Siblings = co-children of the same parent excluding self; ancestors = parent chain to root.

- [x] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_ui_parser.py -v`
Expected: PASS (existing tests + 2 new).

- [x] **Step 5: Commit**

```bash
git add src/phonectl/ui_parser.py tests/test_ui_parser.py
git commit -m "feat: pure build_tree + parse_relations (parent/children/siblings/ancestors)"
```

---

### Task 3: `ui_parser.match_selector`

Pure, ranked selector matching (strategy §5.1).

**Files:**
- Modify: `src/phonectl/ui_parser.py` (append; add `import re` if not already present)
- Test: `tests/test_ui_parser.py` (append)

**Interfaces:**
- `match_selector(elements: list[dict], selector: dict, relations: dict | None = None) -> list[int]` —
  returns the indices `i` of matching elements, **ranked** (exact text > regex > content_desc/id match;
  clickable preferred), with `nth_match` applied LAST as a positional pick into the ranked list. Supported
  keys: `text`, `text_regex`, `content_desc`, `resource_id`, `class`, boolean flags (`clickable`,
  `enabled`, `checked`, `editable`, …), `ancestor_text`/`sibling_text` (need `relations` + elements),
  `bounds_near` (center within/near the given box). Unknown keys raise `ValueError` (typo guard). No match
  → `[]`.

- [x] **Step 1: Write the failing test**

```python
# tests/test_ui_parser.py  (append)

def _els():
    return ui_parser.parse_elements(NESTED)   # Network=0, Wi-Fi=1, Bluetooth=2


def test_match_exact_text():
    assert ui_parser.match_selector(_els(), {"text": "Wi-Fi"}) == [1]


def test_match_text_regex():
    got = ui_parser.match_selector(_els(), {"text_regex": "^(Wi-?Fi|Bluetooth)$"})
    assert set(got) == {1, 2}


def test_match_flag_clickable_and_class():
    got = ui_parser.match_selector(_els(), {"clickable": True, "class": "T"})
    assert set(got) == {0, 1, 2}


def test_match_nth_picks_positionally():
    got = ui_parser.match_selector(_els(), {"text_regex": ".+", "nth_match": 1})
    assert got == [1]            # second ranked candidate


def test_match_sibling_text_uses_relations():
    rel = ui_parser.parse_relations(NESTED)
    got = ui_parser.match_selector(_els(), {"text": "Wi-Fi", "sibling_text": "Bluetooth"},
                                   relations=rel)
    assert got == [1]


def test_no_match_returns_empty():
    assert ui_parser.match_selector(_els(), {"text": "Nope"}) == []


def test_unknown_selector_key_raises():
    import pytest
    with pytest.raises(ValueError):
        ui_parser.match_selector(_els(), {"txt": "Wi-Fi"})
```

- [x] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_ui_parser.py -v`
Expected: FAIL (`AttributeError: ... has no attribute 'match_selector'`).

- [x] **Step 3: Write minimal implementation**

Implement predicate-by-predicate filtering with a small ranking key, then apply `nth_match`. Keep it
pure. Validate selector keys against a known set and raise `ValueError` on unknowns. Relation-dependent
predicates (`ancestor_text`/`sibling_text`) are skipped (treated as non-matching) when `relations` is
`None`, documented in the docstring.

- [x] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_ui_parser.py -v`
Expected: PASS (existing tests + 7 new).

- [x] **Step 5: Commit**

```bash
git add src/phonectl/ui_parser.py tests/test_ui_parser.py
git commit -m "feat: pure match_selector (text/regex/flags/relations/nth) returning ranked indices"
```

---

### Task 4: `observer.observe` — optional tree/relations + observed_at

**Files:**
- Modify: `src/phonectl/observer.py` (`observe`, currently lines 16–31)
- Test: `tests/test_observer.py` (append)

**Interfaces:**
- `observe(backend, session, screenshot=False, snap_path=None, tree=False, relations=False) -> dict` —
  same shape as today plus an `observed_at` float timestamp (`time.time()`); when `tree=True` the snapshot
  gains `"tree": ui_parser.build_tree(xml)`; when `relations=True` it gains
  `"relations": ui_parser.parse_relations(xml)`. The flat `elements` (now with richer metadata) and the
  `hash` are unchanged and remain the default payload (keeps it cheap, strategy §5.2 closing note).

- [x] **Step 1: Write the failing test**

```python
# tests/test_observer.py  (append below existing tests)
from phonectl import ui_parser


def test_observe_default_omits_tree_and_relations(tmp_path):
    s = Session()
    snap = observer.observe(CannedBackend(), s)
    assert "tree" not in snap and "relations" not in snap
    assert "observed_at" in snap


def test_observe_opt_in_tree_and_relations():
    s = Session()
    snap = observer.observe(CannedBackend(), s, tree=True, relations=True)
    assert snap["tree"]["class"]            # has a root class
    assert "siblings" in snap["relations"]
```

Note: `CannedBackend` / `XML` are the existing observer-test doubles. If `CannedBackend`'s XML has only a
single flat node, add a small nested fixture XML in the test module for the tree/relations assertions
(mirroring `NESTED` from `test_ui_parser.py`).

- [x] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_observer.py -v`
Expected: FAIL (`observe()` has no `tree`/`relations` params; no `observed_at`).

- [x] **Step 3: Write minimal implementation**

Add `import time` (observer already imports `re`, `ui_parser`). Capture `xml = backend.ui_dump()` once,
reuse it for `parse_elements`, `build_tree`, `parse_relations`. Add `snap["observed_at"] = time.time()`;
conditionally add `tree`/`relations`.

- [x] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_observer.py -v`
Expected: PASS (existing tests + 2 new).

- [x] **Step 5: Commit**

```bash
git add src/phonectl/observer.py tests/test_observer.py
git commit -m "feat: observe() optional tree/relations payload + observed_at timestamp"
```

---

### Task 5: `session.find` / `session.resolve_selector`

**Files:**
- Modify: `src/phonectl/session.py`
- Test: `tests/test_session.py` (create if absent, else append)

**Interfaces (on `class Session`):**
- `find(self, selector: dict) -> list[int]` — `ui_parser.match_selector(self.last["elements"], selector,
  relations=self.last.get("relations"))`; raises `KeyError("no snapshot; call observe() first")` if
  `self.last is None` (mirrors `resolve`).
- `resolve_selector(self, selector: dict) -> tuple[int, int]` — first match's center `(x, y)`; raises
  `errors.StaleSnapshotError` (Plan 1.1) with a clear message when there are **zero** matches (the screen
  no longer contains the target), and uses `nth_match`/ranking via `find`.

- [x] **Step 1: Write the failing test**

```python
# tests/test_session.py
import pytest
from phonectl import ui_parser, errors
from phonectl.session import Session

NESTED = (
    "<?xml version='1.0'?><hierarchy rotation=\"0\">"
    "<node class=\"F\" bounds=\"[0,0][1080,2400]\">"
    "<node text=\"Wi-Fi\" class=\"T\" clickable=\"true\" bounds=\"[0,100][500,200]\"/>"
    "<node text=\"Bluetooth\" class=\"T\" clickable=\"true\" bounds=\"[0,200][500,300]\"/>"
    "</node></hierarchy>")


def _snap():
    els = ui_parser.parse_elements(NESTED)
    return {"elements": els, "relations": ui_parser.parse_relations(NESTED),
            "hash": ui_parser.screen_hash(els)}


def test_find_returns_candidate_indices():
    s = Session(); s.set_snapshot(_snap())
    assert s.find({"text": "Bluetooth"}) == [1]


def test_resolve_selector_returns_center():
    s = Session(); s.set_snapshot(_snap())
    assert s.resolve_selector({"text": "Wi-Fi"}) == (250, 150)


def test_resolve_selector_raises_stale_when_absent():
    s = Session(); s.set_snapshot(_snap())
    with pytest.raises(errors.StaleSnapshotError):
        s.resolve_selector({"text": "Nonexistent"})


def test_find_without_snapshot_raises():
    with pytest.raises(KeyError):
        Session().find({"text": "x"})
```

- [x] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_session.py -v`
Expected: FAIL (`AttributeError: 'Session' object has no attribute 'find'`).

- [x] **Step 3: Write minimal implementation**

Add `from phonectl import ui_parser, errors` to `session.py` and the two methods. `resolve_selector`
calls `find`; on empty result raises `errors.StaleSnapshotError(f"selector {selector} matched nothing in
the current snapshot")`; otherwise returns the center of the first matched index by reusing the existing
`resolve(i)`.

- [x] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_session.py -v`
Expected: PASS (4 tests).

- [x] **Step 5: Commit**

```bash
git add src/phonectl/session.py tests/test_session.py
git commit -m "feat: Session.find/resolve_selector with stale-snapshot typed error"
```

---

### Task 6: `actuator` selector targeting + stale-snapshot protection

**Files:**
- Modify: `src/phonectl/actuator.py` (`tap`, and thread `expected_hash`/`stale_ok`)
- Test: `tests/test_actuator.py` (append)

**Interfaces:**
- `tap(backend, session, i=None, x=None, y=None, selector=None, expected_hash=None, stale_ok=False)` —
  precedence: explicit `x,y` → `i` (via `session.resolve`) → `selector` (via `session.resolve_selector`).
  **Stale-snapshot protection:** when `expected_hash` is provided and `session.last["hash"] !=
  expected_hash`, re-observe once; if the hash still differs and not `stale_ok`, raise
  `errors.StaleSnapshotError`; otherwise resolve against the fresh snapshot and act (strategy §5.5).
  Re-observes after acting, as today.
- The same `selector`/`expected_hash`/`stale_ok` plumbing is documented for `type_text`/`swipe`/`key` but
  only `tap` needs target resolution; for the others `expected_hash`/`stale_ok` gate execution.

- [x] **Step 1: Write the failing test**

```python
# tests/test_actuator.py  (append below existing tests)
import pytest
from phonectl import actuator, observer, errors
from phonectl.session import Session

SEL_XML = (
    "<?xml version='1.0'?><hierarchy rotation=\"0\">"
    "<node text=\"Wi-Fi\" class=\"T\" clickable=\"true\" bounds=\"[0,100][500,200]\"/>"
    "</hierarchy>")


class SelBackend:
    def __init__(self): self.taps = []
    def ui_dump(self): return SEL_XML
    def window_dump(self): return "mCurrentFocus=Window{a b com.x/.A}"
    def wm_size(self): return (1080, 2400)
    def input_tap(self, x, y): self.taps.append((x, y))


def test_tap_by_selector_resolves_and_acts():
    b = SelBackend(); s = Session()
    observer.observe(b, s)                       # populate snapshot
    snap = actuator.tap(b, s, selector={"text": "Wi-Fi"})
    assert (250, 150) in b.taps
    assert snap["elements"][0]["text"] == "Wi-Fi"


def test_tap_stale_hash_raises_when_screen_changed():
    b = SelBackend(); s = Session()
    observer.observe(b, s)
    with pytest.raises(errors.StaleSnapshotError):
        actuator.tap(b, s, selector={"text": "Wi-Fi"}, expected_hash="not-the-current-hash")
    assert b.taps == []                          # never tapped a moved screen


def test_tap_stale_ok_proceeds_against_fresh_snapshot():
    b = SelBackend(); s = Session()
    observer.observe(b, s)
    snap = actuator.tap(b, s, selector={"text": "Wi-Fi"},
                        expected_hash="stale", stale_ok=True)
    assert (250, 150) in b.taps
    assert snap["hash"]                           # acted against the re-observed screen
```

- [x] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_actuator.py -v`
Expected: FAIL (`tap()` has no `selector`/`expected_hash`/`stale_ok`).

- [x] **Step 3: Write minimal implementation**

Add the params to `tap`; implement the stale check (re-`observer.observe` once on mismatch, then compare
again); resolve target by precedence; raise `errors.StaleSnapshotError` on unresolved stale; act + return
`observer.observe(...)`. Import `errors`.

- [x] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_actuator.py -v`
Expected: PASS (existing tests + 3 new).

- [x] **Step 5: Commit**

```bash
git add src/phonectl/actuator.py tests/test_actuator.py
git commit -m "feat: actuator selector targeting + stale-snapshot protection (expected_hash/stale_ok)"
```

---

### Task 7: `cli` selector flags

**Files:**
- Modify: `src/phonectl/cli.py` (`tap`/`wait-for` subparsers + `_cmd_tap`/`_cmd_wait_for`)
- Test: `tests/test_cli.py` (append)

**Interfaces:**
- `phonectl tap` accepts `--selector JSON` | `--text STR` | `--id STR` (mutually exclusive with
  `--index`/`--xy`), plus `--nth N`, `--expected-hash HASH`, `--stale-ok`. `--text`/`--id`/`--nth` build a
  selector dict (`{"text": ...}` / `{"resource_id": ...}` + `nth_match`). The resolved selector flows to
  `actuator.tap(..., selector=sel, expected_hash=args.expected_hash, stale_ok=args.stale_ok)`.
- `phonectl wait-for --selector JSON` matches via `ui_parser.match_selector` on each polled snapshot.
- Audit `target` records the selector (e.g. `{"selector": {...}}`) so the action log shows how the target
  was chosen (strategy §20.2 "always records how the target resolved").

- [x] **Step 1: Write the failing test**

```python
# tests/test_cli.py  (append)
def test_tap_by_text_selector_resolves_and_logs(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    b = FakeBackend()                              # existing double; serves Wi-Fi node
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: b)
    # observe once so the session/snapshot exists for resolution path used by the cmd
    rc = cli.main(["tap", "--text", "Wi-Fi", "--yes"])
    assert rc == 0
    log = (tmp_path / "actions.jsonl").read_text()
    assert "selector" in log and "Wi-Fi" in log
```

Note: `FakeBackend` in `tests/test_cli.py` must serve a Wi-Fi element (it already does for the existing
tap-by-index tests). `_cmd_tap` builds the runtime, observes to populate the session, then resolves the
selector — mirror the existing `_cmd_tap` structure and route through `_do_action` so mode/kill-switch
gating is unchanged.

- [x] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli.py -v`
Expected: FAIL (`tap` has no `--text`/`--selector`).

- [x] **Step 3: Write minimal implementation**

Add the args to the `tap` subparser group and `--nth`/`--expected-hash`/`--stale-ok`. In `_cmd_tap`, when
a selector form is given, build the selector dict, and pass it through `_do_action` with a `fn` that calls
`actuator.tap(b, s, selector=sel, ...)` and a `target` of `{"selector": sel}`. Add `--selector` to
`wait-for` and branch `_cmd_wait_for` to selector matching.

- [x] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli.py -v`
Expected: PASS (existing tests + 1 new).

- [x] **Step 5: Run the full suite**

Run: `pytest -v`
Expected: PASS (ui_parser, observer, session, actuator, cli, and all prior tests).

- [x] **Step 6: Commit**

```bash
git add src/phonectl/cli.py tests/test_cli.py
git commit -m "feat: CLI selector flags (--selector/--text/--id/--nth, --expected-hash/--stale-ok)"
```

---

### Task 8: Docs — selector grammar, tree/relations, stale semantics

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-06-20-phonectl-adb-bridge-design.md` (§5 element/contract section)

**Interfaces:** none (documentation).

- [x] **Step 1: Document**

`README.md`: the selector grammar (all keys + `nth_match`), `tap --selector/--text/--id`,
`observe --tree/--relations`, and stale-snapshot semantics (`--expected-hash`/`--stale-ok`, the
`stale_snapshot` error code from Plan 1.1). Design spec §5: add the selector object and the
`tree`/`relations` snapshot fields to the element/contract example; note selectors are the durable target
and index `i` remains valid within one snapshot.

- [x] **Step 2: Commit**

```bash
git add README.md docs/superpowers/specs/2026-06-20-phonectl-adb-bridge-design.md
git commit -m "docs: selector grammar, tree/relations observation, and stale-snapshot semantics"
```

---

## Dependencies

**Plan 1.2 of the platform roadmap.** Requires **Plan 1.1** for `errors.StaleSnapshotError` (Tasks 5–6)
and the `results` envelope (Task 7's `--json` reuse, optional). Pure-parser Tasks 1–3 are independent of
1.1 and may be built first. Downstream, Plan 1.3 (resilience) layers retry/lock onto the same `observe`;
Plan 2.3 (MCP) exposes selector-aware tools over `session.resolve_selector`/`match_selector`; Plan 3.3
adds container-aware scroll using the `scrollable` metadata added in Task 1.

## Deferred / out of scope (not in this plan)

- **Semantic AccessibilityService scroll / `ACTION_SET_TEXT` target selection** (Phase 4.1) — this plan is
  coordinate/ADB-based resolution only.
- **OCR / visual-anchor selectors** (Phase 4.4).
- **Matching-confidence scoring surfaced to the agent** — `match_selector` ranks candidates but returns
  plain indices; a confidence field is a Plan 2.3 (MCP `phone.find`) concern (strategy §20.1).
- **`bounds_near` fuzzy tuning** beyond a simple center-in-box test — refine with real-device data later.

## Notes on testability

All parsing, tree, relations, and selector matching are pure and fixture-tested with no device. Session
resolution and actuator stale-protection use duck-typed fake backends and the existing `Session`; the
stale path is exercised by passing a deliberately wrong `expected_hash`, so no real screen change or
wall-clock is needed. The CLI selector path reuses the existing `FakeBackend` and the established
mode/kill-switch funnel, so safety behavior is proven unchanged.
