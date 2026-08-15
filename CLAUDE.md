# CLAUDE.md

Guidance for Claude Code in this repo. Kept small — it points into the real docs rather
than duplicating them.

## Project: droidjig

A Python CLI that lets an AI agent observe the host Android phone as structured JSON and
act on it (`tap`/`type`/`swipe`/`key`/`launch`) over **ADB with no root**, from inside
Termux + PRoot-Distro. The contract is an `observe → act → observe` loop driven by element
indices, not pixels. The daemon (`droidjig daemon`) is the single-writer north star: CLI
and MCP are frontends; the daemon is optional but preferred when running.

## Read before changing core layers

1. **`docs/architecture.md`** — architecture invariants (backend isolation, the
   `runtime.run_action` choke-point, re-observe after every act) and testing discipline.
   These are load-bearing; breaking one is a bug even if the tests pass.
2. **`docs/roadmap.md`** — phase status, the source of truth for what is and isn't built.
3. **`docs/design/`** — the design note behind each subsystem (daemon, macro engine,
   idempotency, companion startup, pushed-token v2). Read the relevant one before
   reworking that subsystem.

## Current state

Phases 1–6 are complete and green, plus the companion APK (`com.droidjig.companion`).
836 tests pass; the runtime is stdlib-only, with an optional `mcp` extra. Core
behavior is validated on a real Samsung Galaxy S25 Ultra over Wireless Debugging.
See `docs/adversarial-review-2026-07.md` for the security review and its remediation status.
**Next: Phase 7.1 (Shizuku provider).**

⚠️ The default action mode is `confirm` (safe-by-default); tests that exercise actions must
opt into `mode: auto` explicitly.

## Commands

```bash
pip install -e ".[dev]"    # install the package + console-script `droidjig` + pytest
pip install -e ".[mcp]"    # also install the optional FastMCP transport
pytest -v                  # full suite
pytest tests/test_ui_parser.py -v                       # one file
pytest tests/test_ui_parser.py::test_parse_bounds -v    # one test
```

No linter or formatter is configured; do not add one unless asked.

## Working discipline

- **TDD order is non-negotiable.** Write the failing test, run it to confirm it fails for
  the right reason, then write the minimum code to pass.
- **One logical change per commit**, with a conventional-commit subject (`feat:`, `fix:`,
  `docs:`, `perf:`, `test:`) and a scope where one applies.
- **Never claim device behavior that hasn't been run on a device.** Unit tests use injected
  fakes — they prove logic, not topology. The on-device matrix is in
  `docs/integration-smoke.md`.
- **Never bypass `runtime.run_action`.** Every action goes through it: mode gate,
  kill-switch, risk/policy, rate limit, idempotency, audit.
