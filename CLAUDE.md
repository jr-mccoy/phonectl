# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Current state

The **observe→act→observe core is built, unit-tested (45 tests, stdlib-only runtime),
reviewed, and validated on a real device** (Samsung Galaxy S25 Ultra over Wireless Debugging
from inside Termux + PRoot). The shipped modules live in `src/phonectl/`: `ui_parser`,
`adb_backend`, `session`, `observer`, `actuator`, `config`, `audit`, `connection`, `cli`.

Source-of-truth documents (read the spec before any plan):

- `docs/superpowers/specs/2026-06-20-phonectl-adb-bridge-design.md` — the design (goals, non-goals, architecture, constraints, safety, risks).
- `docs/superpowers/plans/2026-06-20-phonectl-observe-act-core.md` — the **done** core plan (Tasks 1–8 built the library + CLI; Task 9 was the real-device smoke test).
- `docs/superpowers/specs/2026-06-21-phonectl-resilience-and-followup.md` — the follow-up spec (resilience, safety, polish, deferred backlog).
- `docs/superpowers/plans/2026-06-21-phonectl-*.md` — **six follow-up backlog plans**, each implemented in its own session. Recommended order: `resilience` → `safety-completeness` → `mcp-server` → `setup-wizard` → `accessibility-backend` → `polish`. (`resilience` and `safety-completeness` both create/append `src/phonectl/errors.py`, so keep that order.)

Do not start writing code from a fresh interpretation — execute the relevant plan's tasks in order so the test-first discipline and the commit boundaries are preserved.

## Project: phonectl

A Python CLI that lets an AI agent observe the host Android phone as structured JSON and act on it (`tap`/`type`/`swipe`/`key`/`launch`) over **ADB with no root**, from inside Termux + PRoot-Distro. The contract is an `observe → act → observe` loop driven by element indices, not pixels.

## Commands

```bash
pip install -e .           # install the package + console-script `phonectl`
pytest -v                  # full suite
pytest tests/test_ui_parser.py -v          # one file
pytest tests/test_ui_parser.py::test_parse_bounds -v   # one test
```

There is no linter or formatter configured in the plan; do not add one unless the user asks.

## Architecture invariants (must hold across changes)

- **Backend isolation.** Only `adb_backend.py` knows about `adb`. `observer`, `actuator`, `session`, `cli` speak a backend-agnostic interface so a future AccessibilityService backend is a drop-in swap. Never call `subprocess` or `adb` from anywhere else.
- **`ui_parser` is pure.** XML → `list[dict]` of indexed elements, plus `screen_hash`. No I/O, no subprocess. All edge cases (the trailing `UI hierchary dumped to: /dev/tty` line from `uiautomator dump /dev/tty`, missing attrs) live here, fixture-tested.
- **Element index `i` is the primary target.** Raw `(x,y)` is an escape hatch. This is what makes the agent portable across screen sizes.
- **Every `act()` re-observes.** `actuator.tap/type_text/swipe/key/launch` must return the post-action snapshot via `observer.observe`. The screen-hash change is how the loop knows the action landed.
- **Injectable `runner`.** `AdbBackend.__init__(runner=subprocess.run)` — tests pass a fake runner that records calls. Do not bypass it.
- **Stdlib only at runtime.** Python ≥ 3.9. `pytest` is dev-only. Adding a runtime dep needs an explicit reason; the design picked Python over shell specifically to keep the XML→JSON path pure and testable, not to pull in a framework.
- **Modes and kill-switch gate every action.** CLI action verbs go through `_do_action`, which checks `audit.kill_switch_active()` (sentinel file `$PHONECTL_HOME/STOP`) and `config.get_mode(cfg)` (`auto` / `confirm` / `dry-run`). Default is `auto` in dev, overridden to `confirm` in released builds via config — not hardcoded.
- **Every action appends to `actions.jsonl`** via `audit.log_action` with `ts`, `verb`, `target`, resulting `app`, `hash`.

## Environment & runtime topology

- The agent lives inside a **PRoot-Distro** inside **Termux** on an **unrooted Android 11+** device. `uid 0` in the distro is proot-root, not device root — assume no root anywhere.
- ADB connects over **Wireless Debugging** on `127.0.0.1:<port>`. PRoot shares Termux's (and Android's) loopback, so `adb` runs inside the distro and dials adbd directly. If PRoot blocks the connection, the fallback is a thin shim to host Termux's `adb`; the interface above is unchanged.
- `PHONECTL_HOME` overrides the config dir (default `~/.config/phonectl`). Tests use `monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))` to isolate config + audit + kill-switch state — keep using this pattern.

## Plan-execution discipline

- **One commit per task minimum.** The plan's Task N → Step 5 commit messages are the canonical commit shapes; follow them.
- **TDD order is non-negotiable:** write the failing test, run it to confirm it fails for the right reason, then write the minimum code to pass. Do not pre-implement ahead of the test.
- **Don't claim device behavior you haven't run.** The core was proven by a build-step-zero connectivity smoke (pair, `adb shell echo ok` from inside the distro). Resilience-class work (port recovery, keyguard strings, the host-Termux shim) is ROM-specific and only truly validated against the real device — its plans flag exactly which steps need an on-device smoke. Unit tests use injected fakes; they prove the logic, not the topology.

## What's deferred (do not build without an explicit ask)

The follow-up backlog (resilience/port-recovery, rate-limiting + guarded-package denylist,
the interactive `phonectl setup` wizard, the MCP server wrapper, the AccessibilityService
APK backend, density-aware swipe scaling, and the §4 polish items) is now scoped in the six
`docs/superpowers/plans/2026-06-21-phonectl-*.md` plans. Build a piece only when executing
its plan — don't pull work forward across plan boundaries.

Still un-planned (needs its own brainstorm → spec first): the **autonomous-runner daemon**
(the long-lived loop + wakelock management + scheduler + Termux:Boot autostart + LLM
controller + watchdog) that is phonectl's north-star. The AccessibilityService **APK itself**
(Kotlin) also needs a dedicated Android spec; its plan covers only the Python-side backend.
