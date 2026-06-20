# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Current state

This repo is **spec + plan, no code yet**. The two source-of-truth documents are:

- `docs/superpowers/specs/2026-06-20-phonectl-adb-bridge-design.md` — the design (goals, non-goals, architecture, constraints, safety, risks).
- `docs/superpowers/plans/2026-06-20-phonectl-observe-act-core.md` — the TDD task-by-task implementation plan. Tasks 1–8 build the library + CLI; Task 9 is a manual real-device smoke test. Each task has a failing-test step and a one-task-per-commit rule.

Read the spec first, then the plan. Do not start writing code from a fresh interpretation — execute the plan's tasks in order so the test-first discipline and the commit boundaries are preserved.

## Project: phonectl

A Python CLI that lets an AI agent observe the host Android phone as structured JSON and act on it (`tap`/`type`/`swipe`/`key`/`launch`) over **ADB with no root**, from inside Termux + PRoot-Distro. The contract is an `observe → act → observe` loop driven by element indices, not pixels.

## Commands (once Task 1 lands)

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
- **Do not skip Task 9 mentally.** The build-step-zero connectivity proof (pair, `adb shell echo ok` from inside the distro) is what validates — or refutes, triggering the host-Termux shim — the whole topology. If you're about to claim observe/act works, you've actually run it against a real device.

## What's deferred (do not build without an explicit ask)

mDNS auto-discovery, the full interactive `phonectl setup` wizard, guarded-package denylist, MCP server wrapper, AccessibilityService APK backend, density-aware swipe scaling. These are listed at the bottom of the plan — they belong to follow-on plans, not this one.
