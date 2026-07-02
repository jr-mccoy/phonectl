# CLAUDE.md

Guidance for Claude Code in this repo. This file is a **signpost** — it stays small and
points into the durable memory store. Read the pointers below before changing code.

## Project: phonectl

A Python CLI that lets an AI agent observe the host Android phone as structured JSON and
act on it (`tap`/`type`/`swipe`/`key`/`launch`) over **ADB with no root**, from inside
Termux + PRoot-Distro. The contract is an `observe → act → observe` loop driven by element
indices, not pixels. The daemon (`phonectl daemon`) is the north-star single-writer: CLI
and MCP are frontends; the daemon is optional but preferred when running.

## Start here (read before any code)

1. **Project memory** — run `crumb resume` for current focus, active decisions, failed
   attempts to avoid, and known traps. The store lives in `.project-memory/`.
2. **Architecture invariants & working discipline** —
   `docs/superpowers/phonectl-architecture-invariants.md`. Load-bearing rules
   (backend isolation, the `runtime.run_action` choke-point, TDD order). **Read before
   touching core layers.**
3. **Roadmap (source of truth for status)** —
   `docs/superpowers/phonectl-platform-roadmap.md`. Read before writing/executing any plan.
4. **Plan index + supersession map** —
   `docs/superpowers/plans/2026-06-22-phonectl-remaining-plans-meta-plan.md`. ⚠️ Its
   tracker table is stale for completed phases — trust `git log` / the roadmap for status
   (see `known-traps.md`).

## Current state (summary — detail via `crumb resume` + roadmap)

Phases 1–6 ✅ complete (implemented + green), plus the companion APK (✅ shipped,
`com.phonectl.companion`, Plans 4.5–4.8). 628 tests pass (1 skipped), stdlib-only runtime
(optional `mcp` extra). Core validated on a real Samsung Galaxy S25 Ultra over Wireless
Debugging. Adversarial review 2026-07 (`docs/adversarial-review-2026-07.md`): Findings
1, 2, 4, 5, 6, 7, 8, 12, 15 fixed; Kotlin-side findings (3, 9, 10, 14, 16) still open.
**Next: Phase 7.1 (Shizuku provider).** Phase X (eval suite) has no plan yet.
⚠️ Default mode is now `confirm` (safe-by-default); tests that exercise actions must
opt into `mode: auto` explicitly.

## Commands

```bash
pip install -e .           # install the package + console-script `phonectl`
pip install -e ".[mcp]"    # also install the optional FastMCP transport
pytest -v                  # full suite
pytest tests/test_ui_parser.py -v                       # one file
pytest tests/test_ui_parser.py::test_parse_bounds -v    # one test
```

No linter or formatter is configured; do not add one unless the user asks.

## Top invariants (full list in the architecture doc above)

- **Backend isolation** — only `adb_backend.py` touches `adb`/`subprocess`.
- **One choke-point** — every action goes through `runtime.run_action` (mode gate,
  kill-switch, risk/policy, rate limit, idempotency, audit). Never bypass it.
- **`ui_parser` is pure**; **element index `i`** is the primary target; **every `act()`
  re-observes**.
- **Stdlib-only runtime** (Python ≥ 3.9); structured `results` envelopes everywhere;
  `PHONECTL_HOME` isolation in tests.
- **TDD is non-negotiable**, one commit per task, execute plan tasks in order; don't claim
  device behavior you haven't run on-device.
