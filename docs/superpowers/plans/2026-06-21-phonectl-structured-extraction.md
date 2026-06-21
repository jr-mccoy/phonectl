# phonectl Structured Extraction APIs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Plan 3.4 of the platform roadmap** (`docs/superpowers/phonectl-platform-roadmap.md`). Fourth plan of
Phase 3. Depends on **Plan 1.2** (selectors + tree + relations + rich element metadata from `ui_parser`),
**Plan 3.1** (ProviderRegistry provides structured snapshots).

**Goal:** Read structured data from the UI, not just tap controls — `extract list`, `extract form`,
`get focused-field`, `find --text-regex`, and `get text-in-region` (strategy §5.4). Agents often need
to enumerate RecyclerView rows, find form labels and their associated fields, locate the currently-
active text field, filter elements by text pattern, or extract all visible text within a screen region.

**Architecture:** All extraction logic lives in `ui_parser.py` as pure functions (XML/dict → data, no
I/O, no subprocess, no sleep). This keeps them testable with raw fixture data and preserves the "pure
parser" invariant. CLI commands call `observer.observe()` for the current snapshot then pass elements
and relations to the pure extractor. MCP tools follow the same pattern. No new modules; the extractors
are added alongside the existing `parse_elements`, `build_tree`, `parse_relations`, `match_selector`.

**Tech Stack:** Python 3 (stdlib only: `re`); `pytest` for tests; no new runtime deps.

## Global Constraints

- **stdlib-only at runtime** (Python ≥ 3.9; `pytest` dev-only).
- **`ui_parser.py` stays pure.** All new functions are pure: `list[dict] → list[dict]` or
  `list[dict] → dict | None`. No I/O, no subprocess, no `observer` calls inside them.
- **Only `adb_backend.py` may touch adb/subprocess** (not affected by this plan).
- **Every actuator `act()` re-observes** (not affected; extraction functions do not mutate state).
- **Injectable seams:** extractors take parsed `elements`, optional `tree`, optional `relations` as
  plain Python dicts/lists — tests pass these directly without a real device.
- **Structured-result invariant (Plan 1.1):** CLI `--json` and MCP tools return `results.ok/err`
  envelopes wrapping the extracted data.
- **One commit per task. TDD order is non-negotiable.**

## Shared conventions established by this plan

- **All extractors accept `elements: list[dict]`** as their primary input (the flat list from
  `parse_elements`). Where structural information helps, they also accept an optional
  `relations: dict | None` (from `parse_relations`). Callers that have a full snapshot pass
  `snap["relations"]`; callers without it pass `None` and get best-effort results.
- **`extract_list` returns a list of element dicts**, not a nested structure — it is an agent-
  friendly flat format consistent with the rest of the snapshot's `elements` array.
- **`extract_form` returns `list[dict]`** where each entry is
  `{field_i, label, value, hint, class, is_password, is_focused}`. `label` is `None` when no
  adjacent label can be identified.
- **`find_by_text_regex` uses `re.search`** (substring match) to avoid requiring agents to anchor
  patterns, consistent with the selector's `text_regex` key.

---

### Task 1: `extract_list` — pure list/RecyclerView extractor

**Files:**
- Modify: `src/phonectl/ui_parser.py` (add `extract_list`)
- Test: `tests/test_ui_parser.py` (append)

**Interfaces:**
- `extract_list(elements: list[dict], *, container_i: int | None = None,
  relations: dict | None = None) -> list[dict]`
  — finds a scrollable list container and returns its direct children as row entries.
  If `container_i` is given, uses that specific element's bounds as the container. If omitted,
  uses the first element with `scrollable=True`. Returns elements spatially inside the container
  bounds, excluding the container element itself. Returns `[]` if no container is found.
  Row elements are returned as-is from `elements` (same fields); order is preserved.

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_ui_parser.py

from phonectl import ui_parser


def _make_el(i, text, bounds, scrollable=False, clickable=True):
    x1, y1, x2, y2 = bounds
    return {
        "i": i, "text": text, "id": "", "class": "android.view.View",
        "content_desc": "", "clickable": clickable, "enabled": True,
        "focused": False, "checkable": False, "checked": False,
        "scrollable": scrollable, "long_clickable": False, "password": False,
        "selected": False, "editable": False, "package": "",
        "bounds": list(bounds), "center": [(x1+x2)//2, (y1+y2)//2],
    }


def test_extract_list_finds_children_of_scrollable_container():
    container = _make_el(0, "", [0, 100, 1080, 900], scrollable=True, clickable=False)
    row1 = _make_el(1, "Row A", [10, 120, 1070, 180])
    row2 = _make_el(2, "Row B", [10, 190, 1070, 250])
    outside = _make_el(3, "Outside", [0, 0, 1080, 90])
    elements = [container, row1, row2, outside]
    rows = ui_parser.extract_list(elements)
    texts = [r["text"] for r in rows]
    assert "Row A" in texts
    assert "Row B" in texts
    assert "Outside" not in texts
    assert "" not in texts  # container itself excluded


def test_extract_list_with_explicit_container_i():
    container = _make_el(0, "", [0, 100, 1080, 900], scrollable=True, clickable=False)
    row1 = _make_el(1, "Item 1", [10, 120, 1070, 180])
    elements = [container, row1]
    rows = ui_parser.extract_list(elements, container_i=0)
    assert any(r["text"] == "Item 1" for r in rows)


def test_extract_list_returns_empty_when_no_scrollable():
    el = _make_el(0, "plain text", [0, 0, 100, 50])
    assert ui_parser.extract_list([el]) == []


def test_extract_list_returns_empty_for_empty_input():
    assert ui_parser.extract_list([]) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_ui_parser.py -v -k "extract_list"`
Expected: FAIL (`AttributeError: module 'phonectl.ui_parser' has no attribute 'extract_list'`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/ui_parser.py — add after parse_relations

def extract_list(elements: list, *, container_i=None, relations=None) -> list:
    """Return child elements of a scrollable list container.

    Uses spatial containment: elements whose center falls inside the container's
    bounds. For accurate parent–child extraction, pass relations from parse_relations.
    """
    if not elements:
        return []

    container = None
    if container_i is not None:
        container = next((e for e in elements if e["i"] == container_i), None)
    if container is None:
        container = next((e for e in elements if e.get("scrollable")), None)
    if container is None:
        return []

    x1, y1, x2, y2 = container["bounds"]
    return [
        e for e in elements
        if e["i"] != container["i"]
        and x1 <= e["center"][0] <= x2
        and y1 <= e["center"][1] <= y2
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_ui_parser.py -v -k "extract_list"`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/ui_parser.py tests/test_ui_parser.py
git commit -m "feat: ui_parser.extract_list — spatially-bounded child extraction from scrollable containers"
```

---

### Task 2: `extract_form` — label-field pair extraction

**Files:**
- Modify: `src/phonectl/ui_parser.py` (add `extract_form`)
- Test: `tests/test_ui_parser.py` (append)

**Interfaces:**
- `extract_form(elements: list[dict], *, relations: dict | None = None) -> list[dict]`
  — finds editable elements (`editable=True` or class contains `"EditText"`). For each field:
    - With `relations`: searches siblings for a non-editable text element (label candidate), then
      ancestors.
    - Without `relations`: searches for the nearest element by `bounds` proximity (same row by
      Y-coordinate overlap) that is not editable and has non-empty `text`.
  - Returns `[{field_i, label, value, hint, class, is_password, is_focused}]`.
  - Elements with `password=True` have their `value` replaced with `"[redacted]"`.

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_ui_parser.py

def _make_edittext(i, value, bounds, password=False, focused=False, hint=""):
    x1, y1, x2, y2 = bounds
    el = _make_el(i, value, bounds, clickable=True)
    el["editable"] = True
    el["class"] = "android.widget.EditText"
    el["password"] = password
    el["focused"] = focused
    if hint:
        el["hint_text"] = hint
    return el


def _make_label(i, text, bounds):
    el = _make_el(i, text, bounds, clickable=False)
    el["editable"] = False
    el["class"] = "android.widget.TextView"
    return el


def test_extract_form_finds_field_without_relations():
    label = _make_label(0, "Username", [10, 50, 200, 90])
    field = _make_edittext(1, "alice", [10, 100, 400, 150], hint="Enter username")
    rows = ui_parser.extract_form([label, field])
    assert len(rows) == 1
    assert rows[0]["field_i"] == 1
    assert rows[0]["value"] == "alice"


def test_extract_form_redacts_password_fields():
    field = _make_edittext(0, "secret", [0, 0, 100, 50], password=True)
    rows = ui_parser.extract_form([field])
    assert rows[0]["is_password"] is True
    assert rows[0]["value"] == "[redacted]"


def test_extract_form_finds_label_via_relations():
    label = _make_label(0, "Email", [10, 50, 200, 90])
    field = _make_edittext(1, "", [10, 100, 400, 150])
    relations = {
        "siblings": {0: [1], 1: [0]},
        "parent": {0: None, 1: None},
        "children": {0: [], 1: []},
        "ancestors": {0: [], 1: []},
    }
    rows = ui_parser.extract_form([label, field], relations=relations)
    assert rows[0]["label"] == "Email"


def test_extract_form_marks_focused_field():
    field = _make_edittext(0, "", [0, 0, 100, 50], focused=True)
    rows = ui_parser.extract_form([field])
    assert rows[0]["is_focused"] is True


def test_extract_form_returns_empty_when_no_fields():
    label = _make_label(0, "Title", [0, 0, 100, 30])
    assert ui_parser.extract_form([label]) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_ui_parser.py -v -k "extract_form"`
Expected: FAIL (`AttributeError`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/ui_parser.py

def extract_form(elements: list, *, relations: dict | None = None) -> list:
    """Return form fields with associated labels.

    Uses sibling relations when available, Y-proximity otherwise.
    Password fields have value replaced with '[redacted]'.
    """
    fields = [
        e for e in elements
        if e.get("editable") or "EditText" in e.get("class", "")
    ]
    by_i = {e["i"]: e for e in elements}
    result = []

    for field in fields:
        label = None

        if relations is not None:
            sibs = relations.get("siblings", {}).get(field["i"], [])
            for sib_i in sibs:
                sib = by_i.get(sib_i, {})
                if sib.get("text") and not sib.get("editable"):
                    label = sib["text"]
                    break
            if label is None:
                for anc_i in relations.get("ancestors", {}).get(field["i"], []):
                    anc = by_i.get(anc_i, {})
                    if anc.get("text") and not anc.get("editable"):
                        label = anc["text"]
                        break
        else:
            # Proximity heuristic: nearest non-editable text element in the same row
            fy1, fy2 = field["bounds"][1], field["bounds"][3]
            candidates = [
                e for e in elements
                if e["i"] != field["i"]
                and e.get("text")
                and not e.get("editable")
                and e["bounds"][1] <= fy2 and e["bounds"][3] >= fy1
            ]
            if candidates:
                fx = field["center"][0]
                label = min(candidates,
                            key=lambda e: abs(e["center"][0] - fx))["text"]

        value = field.get("text", "") or ""
        if field.get("password"):
            value = "[redacted]"

        result.append({
            "field_i": field["i"],
            "label": label,
            "value": value,
            "hint": field.get("hint_text", "") or "",
            "class": field.get("class", ""),
            "is_password": bool(field.get("password")),
            "is_focused": bool(field.get("focused")),
        })

    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_ui_parser.py -v -k "extract_form"`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/ui_parser.py tests/test_ui_parser.py
git commit -m "feat: ui_parser.extract_form — label-field pair extraction with relations or proximity"
```

---

### Task 3: `get_focused_field` + `find_by_text_regex`

**Files:**
- Modify: `src/phonectl/ui_parser.py` (add both functions)
- Test: `tests/test_ui_parser.py` (append)

**Interfaces:**
- `get_focused_field(elements: list[dict]) -> dict | None`
  — returns the first element with `focused=True` that is also editable; falls back to the first
  `focused=True` element regardless of editability; returns `None` if no focused element.
- `find_by_text_regex(elements: list[dict], pattern: str) -> list[dict]`
  — returns elements whose `text` matches `re.search(pattern, text)`. Searches `text` only
  (not `content_desc`); order preserved. Raises `re.error` on invalid pattern (caller handles).

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_ui_parser.py

def test_get_focused_field_returns_focused_editable():
    f = _make_edittext(0, "text", [0, 0, 100, 50], focused=True)
    other = _make_el(1, "label", [0, 60, 100, 90])
    assert ui_parser.get_focused_field([other, f])["i"] == 0


def test_get_focused_field_falls_back_to_any_focused():
    el = _make_el(0, "button", [0, 0, 100, 50])
    el["focused"] = True
    assert ui_parser.get_focused_field([el])["i"] == 0


def test_get_focused_field_returns_none_when_none_focused():
    el = _make_el(0, "button", [0, 0, 100, 50])
    assert ui_parser.get_focused_field([el]) is None


def test_find_by_text_regex_matches_substring():
    a = _make_el(0, "Total: $5.00", [0, 0, 100, 50])
    b = _make_el(1, "Balance: $10.00", [0, 60, 100, 110])
    c = _make_el(2, "No match here", [0, 120, 100, 170])
    results = ui_parser.find_by_text_regex([a, b, c], r"\$\d+\.\d+")
    assert len(results) == 2
    assert any(r["i"] == 0 for r in results)
    assert any(r["i"] == 1 for r in results)


def test_find_by_text_regex_empty_when_no_match():
    el = _make_el(0, "nothing", [0, 0, 100, 50])
    assert ui_parser.find_by_text_regex([el], r"\d{4}") == []


def test_find_by_text_regex_preserves_order():
    els = [_make_el(i, f"Item {i}", [0, i*50, 100, i*50+40]) for i in range(5)]
    results = ui_parser.find_by_text_regex(els, r"Item")
    assert [r["i"] for r in results] == [0, 1, 2, 3, 4]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_ui_parser.py -v -k "focused_field or text_regex"`
Expected: FAIL (`AttributeError`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/ui_parser.py

import re as _re


def get_focused_field(elements: list) -> dict | None:
    """Return the focused editable field, or the first focused element, or None."""
    focused_editable = next(
        (e for e in elements if e.get("focused") and e.get("editable")), None
    )
    if focused_editable:
        return focused_editable
    return next((e for e in elements if e.get("focused")), None)


def find_by_text_regex(elements: list, pattern: str) -> list:
    """Return elements whose text matches the regex pattern (re.search)."""
    compiled = _re.compile(pattern)
    return [e for e in elements if compiled.search(e.get("text", "") or "")]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_ui_parser.py -v -k "focused_field or text_regex"`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/ui_parser.py tests/test_ui_parser.py
git commit -m "feat: ui_parser.get_focused_field and find_by_text_regex extractors"
```

---

### Task 4: `get_visible_text_in_region` — region-bounded text extraction

**Files:**
- Modify: `src/phonectl/ui_parser.py` (add `get_visible_text_in_region`)
- Test: `tests/test_ui_parser.py` (append)

**Interfaces:**
- `get_visible_text_in_region(elements: list[dict], bounds: tuple[int,int,int,int]) -> list[dict]`
  — returns elements whose bounds **overlap** with the given region `(x1, y1, x2, y2)`. Two
  rectangles overlap when they are not disjoint: `ex1 <= rx2 and ex2 >= rx1 and ey1 <= ry2 and
  ey2 >= ry1`. Elements with empty text are included if they have `content_desc` (agents may want
  non-textual elements in a region for context). Returns elements in document order.

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_ui_parser.py

def test_get_visible_text_in_region_returns_overlapping():
    a = _make_el(0, "In region", [10, 100, 400, 200])
    b = _make_el(1, "Partially in", [350, 150, 600, 300])
    c = _make_el(2, "Outside", [500, 400, 900, 500])
    region = (0, 50, 450, 250)
    found = ui_parser.get_visible_text_in_region([a, b, c], region)
    ids = [e["i"] for e in found]
    assert 0 in ids
    assert 1 in ids    # overlaps: x2=400 >= rx1=0 and ex1=350 <= rx2=450; y overlap: 150<=250, 300>=50
    assert 2 not in ids


def test_get_visible_text_in_region_returns_empty_when_none_overlap():
    el = _make_el(0, "Far away", [800, 800, 1000, 900])
    assert ui_parser.get_visible_text_in_region([el], (0, 0, 100, 100)) == []


def test_get_visible_text_in_region_preserves_order():
    els = [_make_el(i, f"el{i}", [i*50, 0, i*50+40, 50]) for i in range(4)]
    found = ui_parser.get_visible_text_in_region(els, (0, 0, 1000, 100))
    assert [e["i"] for e in found] == [0, 1, 2, 3]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_ui_parser.py -v -k "visible_text_in_region"`
Expected: FAIL (`AttributeError`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/ui_parser.py

def get_visible_text_in_region(elements: list, bounds: tuple) -> list:
    """Return elements whose bounds overlap with the given region."""
    rx1, ry1, rx2, ry2 = bounds
    return [
        e for e in elements
        if e["bounds"][0] <= rx2
        and e["bounds"][2] >= rx1
        and e["bounds"][1] <= ry2
        and e["bounds"][3] >= ry1
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_ui_parser.py -v -k "visible_text_in_region"`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/ui_parser.py tests/test_ui_parser.py
git commit -m "feat: ui_parser.get_visible_text_in_region — rectangle-overlap spatial text filter"
```

---

### Task 5: CLI verbs — `extract`, `get focused-field`, `find --text-regex`, `get text-in-region`

**Files:**
- Modify: `src/phonectl/cli.py` (new subcommands)
- Test: `tests/test_cli.py` (append)

**Interfaces:**
- `phonectl extract list [--container-i N] [--json]` — observe, extract_list, print.
- `phonectl extract form [--json]` — observe with `--relations`, extract_form, print.
- `phonectl get focused-field [--json]` — observe, get_focused_field, print.
- `phonectl find --text-regex PATTERN [--json]` — observe, find_by_text_regex, print.
- `phonectl get text-in-region --bounds x1 y1 x2 y2 [--json]` — observe,
  get_visible_text_in_region, print.

All commands emit a `results.ok(capability="extraction.<verb>", provider=..., data={"rows": [...]})`
or `results.ok(..., data={"element": ...})` envelope with `--json`; plain output prints a
human-readable summary (one row per line for lists, field label=value pairs for forms).

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_cli.py

def test_extract_list_returns_ok_envelope(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: FakeBackend())
    rc = cli.main(["extract", "list", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["ok"] is True
    assert "rows" in out["data"]


def test_find_text_regex_returns_ok(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: FakeBackend())
    rc = cli.main(["find", "--text-regex", "Wi.*Fi", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["ok"] is True
    assert "matches" in out["data"]


def test_get_focused_field_returns_ok(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: FakeBackend())
    rc = cli.main(["get", "focused-field", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["ok"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli.py -v -k "extract_list or find_text or focused_field"`
Expected: FAIL (subparsers not yet added).

- [ ] **Step 3: Write minimal implementation**

In `cli.py`, add `extract`, `find`, and `get` subcommand groups:

```python
# _cmd_extract_list
def _cmd_extract_list(args):
    cfg = config.load()
    backend, session, conn = build_runtime(cfg)
    conn.ensure()
    snap = observer.observe(backend, session, tree=False, relations=False)
    container_i = getattr(args, "container_i", None)
    rows = ui_parser.extract_list(snap["elements"], container_i=container_i)
    env = results.ok(capability="extraction.list",
                     provider=getattr(backend, "last_used", None) or "adb",
                     data={"rows": rows})
    if getattr(args, "json", False):
        print(json.dumps(env, indent=2))
    else:
        for row in rows:
            print(row.get("text") or row.get("content_desc") or f"i={row['i']}")
    return 0


# _cmd_find_text_regex
def _cmd_find(args):
    cfg = config.load()
    backend, session, conn = build_runtime(cfg)
    conn.ensure()
    snap = observer.observe(backend, session)
    matches = ui_parser.find_by_text_regex(snap["elements"], args.text_regex)
    env = results.ok(capability="extraction.find",
                     provider=getattr(backend, "last_used", None) or "adb",
                     data={"matches": matches})
    if getattr(args, "json", False):
        print(json.dumps(env, indent=2))
    else:
        for m in matches:
            print(f"i={m['i']} text={m['text']!r}")
    return 0


# _cmd_get_focused_field
def _cmd_get_focused_field(args):
    cfg = config.load()
    backend, session, conn = build_runtime(cfg)
    conn.ensure()
    snap = observer.observe(backend, session)
    el = ui_parser.get_focused_field(snap["elements"])
    env = results.ok(capability="extraction.focused_field",
                     provider=getattr(backend, "last_used", None) or "adb",
                     data={"element": el})
    if getattr(args, "json", False):
        print(json.dumps(env, indent=2))
    elif el:
        print(f"i={el['i']} text={el.get('text','')!r} hint={el.get('hint_text','')!r}")
    else:
        print("phonectl: no focused field")
    return 0
```

Register subparsers in `build_parser()`:
- `extract` group with `list` and `form` subcommands
- `find` command with `--text-regex`
- `get` group with `focused-field` and `text-in-region` subcommands

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli.py -v`
Expected: PASS (new tests + all existing).

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/cli.py tests/test_cli.py
git commit -m "feat: CLI extract list/form, get focused-field/text-in-region, find --text-regex"
```

---

### Task 6: MCP tools — extraction tools

**Files:**
- Modify: `src/phonectl/mcp_server.py` (add four new tool entries)
- Test: `tests/test_mcp_server.py` (append)

**Interfaces:**
- `phone_extract_list` — args: `container_index` (int | null); calls `extract_list`.
- `phone_extract_form` — no required args; calls `extract_form` with `relations` from snapshot.
- `phone_get_focused_field` — no args; calls `get_focused_field`.
- `phone_find_text` — args: `pattern` (string regex); calls `find_by_text_regex`.

All four observe-only (no `run_action`); handlers return `results.ok(data=...)`.

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_mcp_server.py

def test_phone_extract_list_returns_rows(build):
    from phonectl.mcp_server import call_tool
    env = call_tool("phone_extract_list", {}, build)
    assert env["ok"] is True
    assert "rows" in env["data"]


def test_phone_extract_form_returns_fields(build):
    from phonectl.mcp_server import call_tool
    env = call_tool("phone_extract_form", {}, build)
    assert env["ok"] is True
    assert "fields" in env["data"]


def test_phone_get_focused_field_returns_element_or_none(build):
    from phonectl.mcp_server import call_tool
    env = call_tool("phone_get_focused_field", {}, build)
    assert env["ok"] is True
    assert "element" in env["data"]


def test_phone_find_text_returns_matches(build):
    from phonectl.mcp_server import call_tool
    env = call_tool("phone_find_text", {"pattern": "Wi.*Fi"}, build)
    assert env["ok"] is True
    assert "matches" in env["data"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_mcp_server.py -v -k "extract or focused or find_text"`
Expected: FAIL (tools not registered).

- [ ] **Step 3: Write minimal implementation**

Add handler functions and `TOOLS` entries in `mcp_server.py`:

```python
def _h_extract_list(build, *, container_index=None, **_):
    backend, session, conn = build(config.load())
    conn.ensure()
    snap = observer.observe(backend, session)
    rows = ui_parser.extract_list(snap["elements"], container_i=container_index)
    return results.ok(capability="extraction.list",
                      provider=getattr(backend, "last_used", None) or "adb",
                      data={"rows": rows})


def _h_extract_form(build, **_):
    backend, session, conn = build(config.load())
    conn.ensure()
    snap = observer.observe(backend, session, relations=True)
    fields = ui_parser.extract_form(
        snap["elements"], relations=snap.get("relations")
    )
    return results.ok(capability="extraction.form",
                      provider=getattr(backend, "last_used", None) or "adb",
                      data={"fields": fields})


def _h_get_focused_field(build, **_):
    backend, session, conn = build(config.load())
    conn.ensure()
    snap = observer.observe(backend, session)
    el = ui_parser.get_focused_field(snap["elements"])
    return results.ok(capability="extraction.focused_field",
                      provider=getattr(backend, "last_used", None) or "adb",
                      data={"element": el})


def _h_find_text(build, *, pattern, **_):
    backend, session, conn = build(config.load())
    conn.ensure()
    snap = observer.observe(backend, session)
    matches = ui_parser.find_by_text_regex(snap["elements"], pattern)
    return results.ok(capability="extraction.find",
                      provider=getattr(backend, "last_used", None) or "adb",
                      data={"matches": matches})
```

Register in `TOOLS`:
```python
"phone_extract_list": {
    "schema": {"container_index": {"type": ["integer", "null"]}},
    "handler": _h_extract_list,
},
"phone_extract_form": {"schema": {}, "handler": _h_extract_form},
"phone_get_focused_field": {"schema": {}, "handler": _h_get_focused_field},
"phone_find_text": {
    "schema": {"pattern": {"type": "string"}},
    "handler": _h_find_text,
},
```

- [ ] **Step 4: Run test to verify it passes + run full suite**

Run: `pytest tests/test_mcp_server.py -v && pytest -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/mcp_server.py tests/test_mcp_server.py
git commit -m "feat: MCP tools phone_extract_list/form, phone_get_focused_field, phone_find_text"
```

---

### Task 7: Docs

**Files:**
- Modify: `README.md` (add "Structured extraction" section)
- Modify: `docs/superpowers/specs/2026-06-20-phonectl-adb-bridge-design.md`

- [ ] **Step 1: Update docs**

In `README.md`, add a **Structured extraction** section with examples:

```
# Extract all rows from a list
phonectl extract list --json

# Extract form fields with labels
phonectl extract form --json

# Find elements matching a regex
phonectl find --text-regex "Total|Balance" --json

# Get the currently focused text field
phonectl get focused-field --json

# Get all text in a screen region (coordinates)
phonectl get text-in-region --bounds 0 0 1080 400 --json
```

Note that `extract form` requires a snapshot with `relations` (automatically requested). Note that
`find --text-regex` searches element text only; for content-desc matching, use `--selector`.

- [ ] **Step 2: Commit**

```bash
git add README.md docs/superpowers/specs/2026-06-20-phonectl-adb-bridge-design.md
git commit -m "docs: structured extraction API reference — extract list/form, find, get focused-field"
```

---

## Dependencies

**Requires:** 1.2 (selectors, tree, relations, rich metadata — `scrollable`, `editable`, `focused`,
`password`, `hint_text` fields used by all extractors); 3.1 (ProviderRegistry — CLI and MCP handlers
call `observer.observe` through the registry). This plan is otherwise self-contained: all extractors
are pure functions over already-shipped element/relations data.
**Enables:** Plan 3.5 (Termux:API adds device-level data extraction); Phase 4 (AccessibilityService
provides richer node info including `collection_info`, `range_info`, `actions` lists that improve
`extract_list` and `extract_form` accuracy). Phase 6 (macro `extract_list` / `extract_form` actions).

## Deferred / out of scope

- **XPath-style deep queries** (query inside `tree`) — deferred; `relations` + selector-based
  `match_selector` covers the common cases.
- **`extract_table`** (structured grid/table extraction) — deferred; tables on Android are often
  RecyclerViews with complex layout managers that require heuristics beyond flat bounds.
- **OCR-based extraction** (for WebViews or canvas UIs) — Phase 4.4 (optional OCR provider).
- **`content_desc` regex search** in `find_by_text_regex` — conservative omission to keep the
  function's contract clear; agents can use `match_selector(selector={"content_desc": ...})` for
  content-desc matching.

## Notes on testability

All extraction functions are pure (`list[dict] → list[dict]` or `dict | None`) and are tested
directly with hand-built element/relations fixtures. No device, no `adb`, no observer calls are
needed. CLI and MCP tests use the existing `FakeBackend` whose `ui_dump()` returns a small XML
fixture; the resulting `elements` list may be empty (no scrollable container) so the tests check
envelope shape, not specific extracted content.
