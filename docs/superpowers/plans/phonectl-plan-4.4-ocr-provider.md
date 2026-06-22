# phonectl Optional OCR Provider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Plan 4.4 of the platform roadmap** (`docs/superpowers/phonectl-platform-roadmap.md`). Fourth (final)
plan of Phase 4. Depends on **Plan 1.1** (`errors`/`results`/`capabilities`), **Plan 3.1**
(`ProviderRegistry` — `screencap` delegation + capability resolution), and **Plan 3.4** (region/text
consumers that the OCR output feeds). Optional consumer of **Plan 4.1** (`Transport`, for the companion
ML-Kit OCR path).

**Goal:** Add an **optional** `OcrProvider` that reads text from screenshots when the structured UI tree is
empty or unavailable — custom-drawn surfaces, canvas/game UIs, WebViews that don't expose nodes, image
content (strategy §13.4). It is **discovered at runtime, never a hard dependency**: it activates when a
local `tesseract` binary is on `PATH`, **or** when the companion APK advertises an ML-Kit OCR capability
over the Plan 4.1 transport. Absent both, `observe_ocr` is simply unavailable and the CLI/MCP return a
`CapabilityUnavailableError` envelope with an install hint. OCR output is normalized to the same
`{text, bounds, confidence}` shape so it composes with the Plan 3.4 region/regex text consumers.

**Architecture:** One pure module `src/phonectl/ocr.py` with `parse_tsv(tsv: str) -> list[dict]` (Tesseract
TSV → regions) — no I/O, fixture-tested like `ui_parser`. One provider module
`src/phonectl/providers/ocr.py` (`OcrProvider`) constructed with an injectable `runner`/`which` (local
Tesseract path) and an optional `transport` (companion ML-Kit path). `ocr_image(path)` runs OCR on an
existing PNG; `ocr_screen()` asks the registry for a `screencap` then OCRs it. `cli.build_runtime()`
appends `OcrProvider` to the registry (low priority — it never competes with the structured tree). CLI
gains `phonectl ocr screen` and a `find --ocr-text` flag; MCP gains `phone_ocr_screen`.

**Tech Stack:** Python 3 (stdlib only: `csv`/`io`, `shutil`, `subprocess`, `tempfile`); `pytest` for
tests; no new runtime deps. `tesseract` is a **runtime-optional external program** — never imported,
never required; the provider degrades gracefully when absent.

## Global Constraints

- **stdlib-only at runtime.** The Tesseract path shells out via `runner` (injectable); the TSV parse uses
  stdlib `csv`. The ML-Kit path uses the Plan 4.1 transport. No third-party Python deps.
- **Backend isolation.** `OcrProvider` shells out to `tesseract` via the injected `runner` only — it does
  not call `adb`. Screenshots come from the **registry's** `screencap` (which resolves to Accessibility or
  ADB), so OCR never knows how the PNG was captured.
- **`ui_parser.py` stays pure** and untouched. The TSV→regions parser lives in the new pure module
  `ocr.py`.
- **OCR is a fallback, never the default observe path.** It is **appended last** in the registry so it
  never shadows the structured `observe_ui_tree`. Indices/selectors remain the primary targeting; OCR
  regions are an escape hatch for surfaces the tree can't see.
- **Discovery is non-blocking.** `is_available()` checks `which("tesseract") is not None` (local) or the
  companion's `observe_ocr` toggle — it does not actually run OCR.
- **Injectable seams.** `OcrProvider(runner=subprocess.run, which=shutil.which, transport=None)`. Tests
  pass a fake `runner` returning canned TSV and a fake `which`. Isolate state via
  `monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))`.
- **Structured-result invariant (Plan 1.1):** CLI `--json` and MCP return `results.ok/err`.
- **One commit per task. TDD order is non-negotiable.**

## Shared conventions established by this plan

- **New capability key:** `observe_ocr`.
- **OCR region shape:** `{"text": str, "bounds": [l, t, r, b], "confidence": float}` — matches the
  `bounds` convention used elsewhere (left, top, right, bottom in pixels) so Plan 3.4's
  `find_by_text_regex` / `get_visible_text_in_region` can consume OCR regions identically to UI elements.
- **Source precedence:** local `tesseract` first (no companion round trip), else companion ML-Kit over the
  transport. `capabilities()` reflects which source is active.
- **`OcrProvider` is appended last** in `build_runtime` so it is the lowest-priority provider; it satisfies
  only `observe_ocr` and never `observe_ui_tree`.

---

### Task 1: Capability key + pure `ocr.parse_tsv` + provider discovery

**Files:**
- Modify: `src/phonectl/capabilities.py` (add `observe_ocr`)
- Create: `src/phonectl/ocr.py` (pure parser)
- Create: `src/phonectl/providers/ocr.py`
- Test: `tests/test_capabilities.py` (append), `tests/test_ocr.py` (create),
  `tests/test_providers_ocr.py` (create)

**Interfaces:**
- New key `observe_ocr` in `CAPABILITY_KEYS`.
- `ocr.parse_tsv(tsv: str, *, min_confidence: float = 0.0) -> list[dict]` — **pure**. Parses Tesseract
  `--psm 6 tsv` output (tab-separated columns `level …, left, top, width, height, conf, text`). Keeps rows
  with non-empty `text` and `conf >= min_confidence`; converts `(left, top, width, height)` to
  `bounds=[left, top, left+width, top+height]` and `conf` (0–100) to a `0.0–1.0` `confidence`.
- `OcrProvider(runner=subprocess.run, which=shutil.which, transport=None)`.
- `OcrProvider.is_available() -> bool` — local `which("tesseract")` or companion `observe_ocr` toggle.
- `OcrProvider.capabilities() -> dict` — `observe_ocr=True` when available, else all `False`.

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_capabilities.py
def test_observe_ocr_capability_key_exists():
    from phonectl import capabilities
    assert "observe_ocr" in capabilities.CAPABILITY_KEYS


# tests/test_ocr.py (new file)
from phonectl import ocr

# header row + two data rows (Tesseract TSV)
TSV = "\n".join([
    "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext",
    "5\t1\t1\t1\t1\t1\t44\t380\t120\t40\t96.5\tWi-Fi",
    "5\t1\t1\t1\t1\t2\t170\t380\t90\t40\t12.0\t???",
    "5\t1\t1\t1\t1\t3\t44\t440\t200\t40\t88.0\tConnected",
])


def test_parse_tsv_extracts_text_and_bounds():
    regions = ocr.parse_tsv(TSV)
    texts = [r["text"] for r in regions]
    assert "Wi-Fi" in texts and "Connected" in texts
    wifi = next(r for r in regions if r["text"] == "Wi-Fi")
    assert wifi["bounds"] == [44, 380, 164, 420]
    assert 0.0 <= wifi["confidence"] <= 1.0


def test_parse_tsv_filters_low_confidence():
    regions = ocr.parse_tsv(TSV, min_confidence=0.5)
    assert all(r["confidence"] >= 0.5 for r in regions)
    assert "???" not in [r["text"] for r in regions]


def test_parse_tsv_skips_empty_text_and_header():
    regions = ocr.parse_tsv(TSV)
    assert all(r["text"].strip() for r in regions)


# tests/test_providers_ocr.py (new file)
import pytest
from phonectl.providers.ocr import OcrProvider


def _which_found(name):
    return "/usr/bin/" + name


def _which_missing(name):
    return None


def test_is_available_with_local_tesseract():
    assert OcrProvider(which=_which_found).is_available() is True


def test_capabilities_false_without_tesseract_or_companion():
    p = OcrProvider(which=_which_missing, transport=None)
    assert p.is_available() is False
    assert all(v is False for v in p.capabilities().values())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_capabilities.py tests/test_ocr.py tests/test_providers_ocr.py -v`
Expected: FAIL (`ModuleNotFoundError` for `ocr`; missing key).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/capabilities.py — append to CAPABILITY_KEYS
    # Phase 4.4 addition (optional OCR)
    "observe_ocr",
```

```python
# src/phonectl/ocr.py
"""Pure Tesseract-TSV -> region parsing. No I/O, no subprocess."""
from __future__ import annotations

import csv
import io


def parse_tsv(tsv: str, *, min_confidence: float = 0.0) -> list:
    regions = []
    reader = csv.DictReader(io.StringIO(tsv), delimiter="\t")
    for row in reader:
        text = (row.get("text") or "").strip()
        if not text:
            continue
        try:
            conf = float(row.get("conf", "-1"))
        except ValueError:
            continue
        if conf < 0:
            continue
        confidence = conf / 100.0
        if confidence < min_confidence:
            continue
        left = int(row["left"]); top = int(row["top"])
        width = int(row["width"]); height = int(row["height"])
        regions.append({
            "text": text,
            "bounds": [left, top, left + width, top + height],
            "confidence": confidence,
        })
    return regions
```

```python
# src/phonectl/providers/ocr.py
"""Optional OCR provider — local Tesseract or companion ML-Kit; discovered at runtime."""
from __future__ import annotations

import shutil
import subprocess

from phonectl import capabilities as caps_mod


class OcrProvider:
    def __init__(self, runner=subprocess.run, which=shutil.which, transport=None) -> None:
        self._runner = runner
        self._which = which
        self._t = transport

    def _local_ok(self) -> bool:
        return self._which("tesseract") is not None

    def _companion_ok(self) -> bool:
        try:
            return self._t is not None and bool(self._t.ping())
        except Exception:  # noqa: BLE001
            return False

    def is_available(self) -> bool:
        return self._local_ok() or self._companion_ok()

    def capabilities(self) -> dict:
        return caps_mod.make(observe_ocr=True) if self.is_available() else caps_mod.make()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_capabilities.py tests/test_ocr.py tests/test_providers_ocr.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/capabilities.py src/phonectl/ocr.py src/phonectl/providers/ocr.py \
        tests/test_capabilities.py tests/test_ocr.py tests/test_providers_ocr.py
git commit -m "feat: OcrProvider discovery + pure ocr.parse_tsv (Tesseract TSV -> regions) + observe_ocr key"
```

---

### Task 2: `ocr_image(path)` — run OCR on an existing PNG

**Files:**
- Modify: `src/phonectl/providers/ocr.py`
- Test: `tests/test_providers_ocr.py` (append)

**Interfaces:**
- `ocr_image(path: str, *, min_confidence: float = 0.0) -> list[dict]` — local path: runs
  `tesseract <path> stdout --psm 6 tsv`, feeds stdout to `ocr.parse_tsv`. Companion path: calls
  `transport.request("ocr_image", {"path": path})` and normalizes the returned regions to the shared shape
  (ML-Kit already returns text + bounding boxes; map them to `{text, bounds, confidence}`). Raises
  `errors.CapabilityUnavailableError` when neither source is available.

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_providers_ocr.py
from phonectl.providers.transport import LoopbackTransport

TSV = "\n".join([
    "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext",
    "5\t1\t1\t1\t1\t1\t10\t20\t100\t30\t95.0\tHello",
])


class TsvRunner:
    def __init__(self, tsv): self.tsv = tsv; self.calls = []
    def __call__(self, cmd, *, capture_output=True, text=True, **kw):
        self.calls.append(cmd)
        return type("R", (), {"stdout": self.tsv, "returncode": 0})()


def test_ocr_image_local_tesseract_parses_regions():
    r = TsvRunner(TSV)
    p = OcrProvider(runner=r, which=_which_found)
    regions = p.ocr_image("/tmp/shot.png")
    assert regions[0]["text"] == "Hello"
    assert regions[0]["bounds"] == [10, 20, 110, 50]
    assert any("tesseract" in str(c) for c in r.calls)


def test_ocr_image_companion_path():
    def handler(params):
        assert params["path"] == "/tmp/shot.png"
        return {"regions": [{"text": "World", "bounds": [1, 2, 3, 4], "confidence": 0.9}]}
    p = OcrProvider(which=_which_missing,
                    transport=LoopbackTransport({"ocr_image": handler}))
    regions = p.ocr_image("/tmp/shot.png")
    assert regions[0]["text"] == "World"


def test_ocr_image_unavailable_raises():
    p = OcrProvider(which=_which_missing, transport=None)
    with pytest.raises(Exception):
        p.ocr_image("/tmp/shot.png")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_providers_ocr.py -v -k "ocr_image"`
Expected: FAIL (`AttributeError`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/providers/ocr.py
from phonectl import errors, ocr as ocr_mod
from phonectl.providers.transport import next_request_id


def ocr_image(self, path: str, *, min_confidence: float = 0.0) -> list:
    if self._local_ok():
        res = self._runner(["tesseract", path, "stdout", "--psm", "6", "tsv"],
                           capture_output=True, text=True)
        return ocr_mod.parse_tsv(res.stdout, min_confidence=min_confidence)
    if self._companion_ok():
        rid = next_request_id()
        resp = self._t.request("ocr_image", {"path": path}, request_id=rid, timeout=10.0)
        if resp.get("request_id") != rid or not resp.get("ok"):
            raise errors.ObserveError("companion OCR failed or returned a stale response")
        regions = resp.get("data", {}).get("regions", [])
        return [r for r in regions if r.get("confidence", 1.0) >= min_confidence]
    raise errors.CapabilityUnavailableError(
        "OCR unavailable: install tesseract or the companion ML-Kit OCR provider")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_providers_ocr.py -v -k "ocr_image"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/providers/ocr.py tests/test_providers_ocr.py
git commit -m "feat: OcrProvider.ocr_image — local Tesseract TSV + companion ML-Kit paths"
```

---

### Task 3: `ocr_screen()` — capture via the registry, then OCR

**Files:**
- Modify: `src/phonectl/providers/ocr.py`
- Test: `tests/test_providers_ocr.py` (append)

**Interfaces:**
- `ocr_screen(registry, *, min_confidence=0.0, _tmp=tempfile.mkstemp) -> dict` — asks `registry` for a
  `screencap(path)` (resolving to Accessibility or ADB), then `ocr_image(path)`. Returns
  `{"regions": [...], "source": "tesseract"|"mlkit"}`. The registry is passed in (the provider does not
  hold a back-reference to its own registry); the CLI/MCP layer supplies it.
- The provider does not delete the temp PNG implicitly when a caller passes an explicit path; for the
  internal temp it cleans up in a `finally`.

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_providers_ocr.py

class FakeRegistry:
    def __init__(self): self.captured = None
    def screencap(self, path):
        self.captured = path
        return path


def test_ocr_screen_captures_then_ocrs(tmp_path):
    reg = FakeRegistry()
    r = TsvRunner(TSV)
    p = OcrProvider(runner=r, which=_which_found)
    out = p.ocr_screen(reg, _tmp=lambda suffix=".png": (0, str(tmp_path / "s.png")))
    assert reg.captured == str(tmp_path / "s.png")
    assert out["regions"][0]["text"] == "Hello"
    assert out["source"] == "tesseract"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_providers_ocr.py -v -k "ocr_screen"`
Expected: FAIL (`AttributeError`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/providers/ocr.py
import os
import tempfile


def ocr_screen(self, registry, *, min_confidence: float = 0.0, _tmp=None) -> dict:
    make_tmp = _tmp or (lambda suffix=".png": tempfile.mkstemp(suffix=suffix))
    _fd, path = make_tmp()
    try:
        registry.screencap(path)
        regions = self.ocr_image(path, min_confidence=min_confidence)
    finally:
        try:
            if _tmp is None and os.path.exists(path):
                os.remove(path)
        except OSError:
            pass
    source = "tesseract" if self._local_ok() else "mlkit"
    return {"regions": regions, "source": source}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_providers_ocr.py -v -k "ocr_screen"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/providers/ocr.py tests/test_providers_ocr.py
git commit -m "feat: OcrProvider.ocr_screen — registry screencap then OCR with temp cleanup"
```

---

### Task 4: `build_runtime` wiring + CLI `phonectl ocr screen` / `find --ocr-text`

**Files:**
- Modify: `src/phonectl/cli.py`
- Test: `tests/test_cli.py` (append)

**Interfaces:**
- `_make_ocr_provider() -> OcrProvider | None` — constructs `OcrProvider(transport=
  _make_companion_transport(cfg))` and returns it if `is_available()`, else `None`.
- `build_runtime` **appends** the OCR provider (lowest priority): `providers = [... , adb, ocr]` filtered
  for `None`. It only satisfies `observe_ocr`, so it never shadows the structured tree.
- `phonectl ocr screen [--min-confidence F] [--json]` — resolves `backend.for_capability("observe_ocr")`,
  calls `ocr_screen(backend)`, returns `results.ok(capability="ocr.screen", data={...})` or a
  `CapabilityUnavailableError` envelope with an install hint.
- `phonectl find --ocr-text REGEX [--json]` — when `--ocr-text` is passed, OCR the screen and apply the
  regex to the region texts (reusing Plan 3.4's regex matching against the `{text, bounds}` shape);
  returns matching regions. This is the agent's escape hatch when `find --text-regex` over the UI tree
  returns nothing.

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_cli.py
def test_ocr_screen_unavailable(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: FakeBackend())
    monkeypatch.setattr(cli, "_make_ocr_provider", lambda: None)
    rc = cli.main(["ocr", "screen", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc != 0 and out["ok"] is False
    assert out["error"]["code"] == "capability_unavailable"


def test_ocr_screen_ok(tmp_path, monkeypatch, capsys):
    from phonectl.providers.ocr import OcrProvider

    class FakeOcr(OcrProvider):
        def __init__(self): pass
        def is_available(self): return True
        def capabilities(self):
            from phonectl import capabilities
            return capabilities.make(observe_ocr=True)
        def ocr_screen(self, registry, **kw):
            return {"regions": [{"text": "Balance", "bounds": [0, 0, 10, 10],
                                 "confidence": 0.9}], "source": "tesseract"}

    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: FakeBackend())
    monkeypatch.setattr(cli, "_make_ocr_provider", lambda: FakeOcr())
    rc = cli.main(["ocr", "screen", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["ok"] is True
    assert out["data"]["regions"][0]["text"] == "Balance"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli.py -v -k "ocr_screen"`
Expected: FAIL (`_make_ocr_provider` missing; `ocr` subcommand absent).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/cli.py
from phonectl.providers.ocr import OcrProvider


def _make_ocr_provider():
    cfg = config.load()
    p = OcrProvider(transport=_make_companion_transport(cfg))
    return p if p.is_available() else None


def build_runtime(cfg, backend=None):
    adb = backend or _make_backend(cfg)
    providers = [p for p in [
        _make_accessibility_provider(),
        _make_notifications_provider(),
        _make_termux_provider(),
        adb,
        _make_ocr_provider(),   # appended last — lowest priority, observe_ocr only
    ] if p is not None]
    registry = ProviderRegistry(providers)
    session = Session()
    conn = Connection(registry, cfg)
    return registry, session, conn


def _cmd_ocr_screen(args):
    cfg = config.load()
    backend, session, conn = build_runtime(cfg)
    p = backend.for_capability("observe_ocr")
    if p is None:
        env = results.err(
            errors.CapabilityUnavailableError("OCR not available"),
            capability="ocr.screen",
            user_action="Install 'tesseract' (pkg install tesseract) or the companion ML-Kit OCR provider.",
        )
        if getattr(args, "json", False):
            print(json.dumps(env, indent=2))
        else:
            print(f"phonectl: {env['error']['message']}")
        return 1
    data = p.ocr_screen(backend, min_confidence=getattr(args, "min_confidence", 0.0))
    env = results.ok(capability="ocr.screen", provider=type(p).__name__, data=data)
    if getattr(args, "json", False):
        print(json.dumps(env, indent=2))
    else:
        for r in data["regions"]:
            print(f"{r['text']}  {r['bounds']}")
    return 0
```

Register `ocr` with a `screen` subcommand and add the `--ocr-text` flag to the existing `find` parser
(when set, route `find` to OCR the screen and apply the regex to region texts via the Plan 3.4 matcher).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli.py -v`
Expected: PASS (new tests + all existing — `_make_ocr_provider()` returns `None` when `tesseract` is
absent and no companion is configured, so default builds are unchanged).

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/cli.py tests/test_cli.py
git commit -m "feat: OCR provider wiring + phonectl ocr screen + find --ocr-text fallback"
```

---

### Task 5: MCP tool `phone_ocr_screen`

**Files:**
- Modify: `src/phonectl/mcp_server.py`
- Test: `tests/test_mcp_server.py` (append)

**Interfaces:**
- `phone_ocr_screen(min_confidence=0.0)` → `results.ok(capability="ocr.screen", data={"regions": [...],
  "source": ...})` or a `CapabilityUnavailableError` envelope. Read-only (no `run_action`).
- The tool description tells the agent: use this **only when** `phone.observe_ui` / `phone.find` over the
  structured tree returns nothing (custom-drawn/canvas/WebView surfaces); regions carry `confidence` so the
  agent can threshold.

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_mcp_server.py
def test_phone_ocr_screen_tool_registered():
    from phonectl import mcp_server
    assert "phone_ocr_screen" in mcp_server.TOOLS
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_mcp_server.py -v -k "ocr"`
Expected: FAIL (tool not registered).

- [ ] **Step 3: Write minimal implementation**

Register `phone_ocr_screen` in `TOOLS`/`call_tool`, mirroring the existing read-only observation tools
(resolve `observe_ocr`, return `results.ok/err`).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_mcp_server.py -v -k "ocr"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/mcp_server.py tests/test_mcp_server.py
git commit -m "feat: MCP phone_ocr_screen tool (read-only OCR fallback with confidence)"
```

---

### Task 6: Docs

**Files:**
- Modify: `README.md`
- Modify: `android/accessibility-companion/SPEC.md` (ML-Kit OCR method)
- Modify: `docs/superpowers/specs/2026-06-20-phonectl-adb-bridge-design.md`

- [ ] **Step 1: Update docs**

In `README.md`, add an **OCR provider (optional)** section: how it is discovered (`tesseract` on PATH or
companion ML-Kit), how to install (`pkg install tesseract`), the `{text, bounds, confidence}` shape,
`phonectl ocr screen` / `find --ocr-text`, and the guidance that OCR is a **fallback** for surfaces the UI
tree can't see — never the default observe path. Add the capability row to the table. In the companion
SPEC, document the `ocr_image` transport method (ML-Kit). In the design spec, note OCR as the lowest-
priority, optional `observe_ocr` provider that never shadows the structured tree.

Run the full suite before committing:

```bash
pytest -v
```

- [ ] **Step 2: Commit**

```bash
git add README.md android/accessibility-companion/SPEC.md \
        docs/superpowers/specs/2026-06-20-phonectl-adb-bridge-design.md
git commit -m "docs: optional OCR provider — discovery, region shape, ocr screen / find --ocr-text"
```

---

## Dependencies

**Requires:** Plan 1.1 (errors/results/capabilities), Plan 3.1 (`ProviderRegistry` — `screencap`
delegation, capability resolution), Plan 3.4 (region/regex text consumers reused by `find --ocr-text`).
Optional: Plan 4.1 (`Transport` for the companion ML-Kit path).
**Enables:** richer agent recovery on non-standard surfaces; the evaluation suite's image/canvas
benchmarks (Phase X) can assert OCR regions; Phase 6 macros can branch on OCR text when the tree is empty.

## Deferred / out of scope

- **Bundling Tesseract / language data** — the provider only *uses* `tesseract` if present; installation
  is the user's (or Termux `pkg`'s) responsibility.
- **OpenCV template matching / visual anchors / screenshot diffing** (strategy §13.4) — a separate
  optional vision capability; deferred to Phase 7.
- **OCR-targeted tapping** (tap an OCR region by index) — `ocr_screen` returns `bounds`, so a caller can
  already tap `(cx, cy)`; a first-class `tap --ocr-text` is deferred until a use case justifies it.
- **Continuous OCR / OCR events** — OCR is on-demand; any streaming belongs to the Phase 5 daemon.
- **Per-line vs per-word grouping / paragraph reconstruction** — `parse_tsv` emits word-level regions;
  line/paragraph grouping can be layered later without changing the region shape.

## Notes on testability

No `tesseract` binary, screenshot, or device is needed. `ocr.parse_tsv` is **pure** and fixture-tested,
including the low-confidence filter and header/empty-row skipping. `OcrProvider` takes injectable
`runner`/`which`, so the local path is exercised with canned TSV and the unavailable path with a `which`
returning `None`; the companion path uses `LoopbackTransport`. `ocr_screen` takes an injectable temp-file
factory and a fake registry whose `screencap` records the path, so capture-then-OCR is deterministic and
leaves no files. The CLI/MCP layers are tested by patching `_make_ocr_provider` to inject a fake (or
`None`), proving both the success envelope and the capability-unavailable envelope, and that default builds
(no Tesseract, no companion) remain unchanged.
