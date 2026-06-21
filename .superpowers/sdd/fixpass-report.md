# Fix Pass Report

**Branch:** feat/observe-act-core  
**Date:** 2026-06-21  
**Tests:** 45/45 passing, pristine

---

## Fix A — `window_dump` uses wrong dumpsys command

**File:** `src/phonectl/adb_backend.py:31`

**Change:** `self._adb("shell", "dumpsys", "window", "windows")` → `self._adb("shell", "dumpsys", "window")`

`dumpsys window windows` omits `mCurrentFocus` on the test device, so `observe()` returned `app.package = ""`. Dropping the trailing `windows` argument returns the full window manager state that includes `mCurrentFocus`.

**Test added** (`tests/test_adb_backend.py::test_window_dump_builds_correct_command`):  
Asserts command is `["adb","-s","d","shell","dumpsys","window"]` and returned stdout includes `mCurrentFocus`.

**Device validation:**
- Device woken via `adb -s 192.168.0.109:42041 shell input keyevent KEYCODE_WAKEUP`
- `phonectl observe` output:
  - `app.package = "com.termux"`
  - `app.activity = "com.termux.app.TermuxActivity"`
- Before fix: `app.package` was empty string. After fix: non-empty.

---

## Fix B — `input_text` doesn't escape shell metacharacters

**File:** `src/phonectl/adb_backend.py:43-47`

**Change:** Replaced `text.replace(" ", "%s")` with `shlex.quote(text)`. Added `import shlex` at top.

Shell metacharacters (`$`, `&`, `|`, `;`, `\`, `(`, `)`, `'`, `"`, etc.) previously passed raw to the device shell. `shlex.quote` wraps text in single quotes and correctly handles spaces, superseding the old `%s` substitution.

**Test added** (`tests/test_adb_backend.py::test_input_text_shell_quotes_metacharacters`):  
Asserts `b.input_text("a b$c")` builds command `["adb","-s","d","shell","input","text", shlex.quote("a b$c")]` (i.e. `'a b$c'`).

---

## Fix C — `type` leaks raw text into audit log

**File:** `src/phonectl/cli.py:71-72`

**Change:** Target dict changed from `{"text": args.text}` to `{"text": f"<{len(args.text)} chars>"}`. The actuator lambda still receives `args.text` unchanged so real typing is unaffected.

This prevents passwords/OTPs from persisting in cleartext in `actions.jsonl`, dry-run output, and confirm-mode prompts.

**Test added** (`tests/test_cli.py::test_type_redacts_text_in_audit_log`):  
- Added `input_text(self, t)` method to `FakeBackend` class.
- Asserts `("text", "hunter2") in fb.calls` — real text reached the backend.
- Asserts `"hunter2" not in log` — raw text absent from audit log.
- Asserts `"<7 chars>" in log` — redacted surrogate present.

---

## Fix D — `requires-python = ">=3.9"` vs PEP 604 union syntax

**Files:** `src/phonectl/observer.py`, `src/phonectl/cli.py`

**Change:** Added `from __future__ import annotations` as first line in both files.

`X | None` in function signatures and type annotations is evaluated eagerly at runtime on Python 3.9 (PEP 604 unions require 3.10+). Adding `from __future__ import annotations` makes all annotations strings (lazy), enabling 3.9 compatibility without changing `pyproject.toml`.

- `observer.py` line 14: `snap_path: str | None = None`
- `cli.py` line 25: `-> int | None`

Confirmed modules still import and all 45 tests pass on Python 3.13.

---

## Fix E — README launch mechanism description

**File:** `README.md:106`

**Change:** Replaced `"Start an app by package name using \`am start\`, bypassing the launcher."` with `"Start an app by package name using the monkey launcher-intent mechanism (\`monkey -p <pkg> -c android.intent.category.LAUNCHER 1\`). The package must expose a LAUNCHER activity."`

The actual backend (`adb_backend.py::launch`) uses the monkey command, not `am start`. The test `test_launch_uses_monkey` has always verified this.

---

## Files Changed

- `src/phonectl/adb_backend.py` — Fix A (window_dump command) + Fix B (shlex.quote, import shlex)
- `src/phonectl/cli.py` — Fix C (_cmd_type redaction) + Fix D (from __future__ import annotations)
- `src/phonectl/observer.py` — Fix D (from __future__ import annotations)
- `tests/test_adb_backend.py` — Fix A + B tests
- `tests/test_cli.py` — Fix C test (FakeBackend.input_text + test_type_redacts_text_in_audit_log)
- `README.md` — Fix E (launch description)

---

## Concerns

None. All 45 tests pass (42 original + 3 new). Device validation confirmed Fix A resolves the blank `app.package` issue. No regressions.
