# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Current state

**Phases 1–5 are complete (implemented + green), plus two Phase-5 extensions.**
555 test functions across 58 files (554 pass, 1 skipped), stdlib-only runtime (optional `mcp` extra for FastMCP transport).
The core was validated on a real device (Samsung Galaxy S25 Ultra over Wireless Debugging from inside
Termux + PRoot).

**Shipped modules** (`src/phonectl/`):

| Layer | Modules |
|---|---|
| Core observe/act | `ui_parser`, `adb_backend`, `session`, `observer`, `actuator`, `cli` |
| Config / audit / kill-switch | `config`, `audit`, `connection` |
| Phase-1 seams | `errors`, `results`, `capabilities`, `backend` |
| Phase 1.3/1.4 | resilience baked into `connection`/`observer`/`adb_backend`; `setup`, `diagnostics` |
| Phase 2 | `runtime`, `redact`, `risk`, `ratelimit`, `policy`, `mcp_server` |
| Phase 3–4 providers | `providers/` package: `registry`, `accessibility`, `clipboard`, `intents`, `notifications`, `ocr`, `packages`, `termux`, `transport` |
| Phase 4 support | `trust`, `native_tree`, `ocr` (pure TSV parser) |
| Phase 5 daemon | `daemon/` package: `server`, `client`, `rpc`, `discovery`, `jobs`, `events`, `snapshots`, `poller`, `records` |
| Phase 6.1 macro | `macro/` package: `schema`, `variables`, `conditions`, `engine`, `records`, `loader` |
| Phase 6.2 macro | `macro/` package: `triggers`, `scheduler`, `limits`, `registry` (conditions extended); `daemon/triggers.py`: `TriggerManager`, `Scheduler` |

**Phase statuses:**

- **1.1–1.4** ✅ Core foundation (structured results, selectors, resilience, setup/diagnostics)
- **2.1–2.3** ✅ Runtime serialization, risk/policy ledger, MCP server
- **3.1–3.5** ✅ Provider graph, clipboard/intents/packages, scroll/gestures, structured extraction, Termux:API
- **4.1–4.4** ✅ AccessibilityService provider, NotificationListener, foreground-service transport + trust UX, OCR
- **5.1–5.2** ✅ Daemon process + JSON-RPC/socket API, event bus + snapshot cache
- **Daemon extensions** ✅ Async job model (JobRegistry, detach/poll) + idempotency-cache TTL eviction
- **6.1** ✅ Macro runtime core (schema, variables, engine, records, loader, CLI group, MCP tools)
- **6.2** ✅ Macro triggers + scheduler + event subscriptions (pure trigger matcher, full condition vocabulary, monotonic scheduler, per-macro limits, enabled-macro registry, daemon TriggerManager/Scheduler, `macro_enable/disable/list` RPC + CLI). Merged to master `f08ce9d`.
- **6.3** 📝 Written, not yet executed — **Phase 6.3 is next**
- **7.1–7.4** 📝 Written, not yet executed
- **Phase X** (evaluation suite) — not yet a full plan

Source-of-truth documents (read spec → roadmap → plan before any code):

- `docs/superpowers/specs/2026-06-20-phonectl-adb-bridge-design.md` — foundational design. **Still valid.**
- `docs/superpowers/phonectl-automation-platform-strategy.md` — strategy: phonectl as local automation platform / "agent OS for Android".
- **`docs/superpowers/phonectl-platform-roadmap.md` — ACTIVE source-of-truth roadmap.** Read before writing or executing any plan.
- `docs/superpowers/plans/2026-06-22-phonectl-remaining-plans-meta-plan.md` — the **index** with supersession map and implementation tracker table. **Note:** the table's Phase 4–5 rows are stale (they show "not yet executed" but all have landed); use git log / this CLAUDE.md for current status.

Phase 1–5 implementation plans (all ✅ done):

- `docs/superpowers/plans/2026-06-20-phonectl-observe-act-core.md`
- `docs/superpowers/plans/2026-06-22-phonectl-structured-results-and-capabilities.md` — 1.1
- `docs/superpowers/plans/2026-06-22-phonectl-selector-and-tree-observation.md` — 1.2
- `docs/superpowers/plans/2026-06-22-phonectl-resilience-and-connection-recovery.md` — 1.3
- `docs/superpowers/plans/2026-06-22-phonectl-setup-and-diagnostics.md` — 1.4
- `docs/superpowers/plans/2026-06-22-phonectl-action-serialization-and-audit-v2.md` — 2.1
- `docs/superpowers/plans/2026-06-22-phonectl-risk-classifier-and-ledger.md` — 2.2
- `docs/superpowers/plans/2026-06-22-phonectl-structured-result-mcp-server.md` — 2.3
- `docs/superpowers/plans/2026-06-21-phonectl-provider-capability-graph.md` — 3.1
- `docs/superpowers/plans/2026-06-21-phonectl-clipboard-intent-packages.md` — 3.2
- `docs/superpowers/plans/2026-06-21-phonectl-scroll-and-gestures.md` — 3.3
- `docs/superpowers/plans/2026-06-21-phonectl-structured-extraction.md` — 3.4
- `docs/superpowers/plans/2026-06-21-phonectl-termux-api-provider.md` — 3.5
- `docs/superpowers/plans/phonectl-plan-4.1-accessibility-native-provider.md` — 4.1
- `docs/superpowers/plans/phonectl-plan-4.2-notification-listener-provider.md` — 4.2
- `docs/superpowers/plans/phonectl-plan-4.3-foreground-service-transport-and-trust-ux.md` — 4.3
- `docs/superpowers/plans/phonectl-plan-4.4-ocr-provider.md` — 4.4
- `docs/superpowers/plans/phonectl-plan-5.1-daemon-process-and-rpc-api.md` — 5.1
- `docs/superpowers/plans/phonectl-plan-5.2-event-bus-and-snapshot-cache.md` — 5.2
- `docs/superpowers/plans/2026-06-22-phonectl-daemon-async-jobs.md` — daemon async job model (done)
- `docs/superpowers/plans/2026-06-23-phonectl-idempotency-cache-eviction.md` — idempotency-cache TTL eviction (done)

Design specs (read before writing Phase 5+ plans):

- `docs/superpowers/specs/2026-06-22-phonectl-daemon-event-runtime-design.md` — daemon / event runtime spec (5.0)
- `docs/superpowers/specs/2026-06-22-phonectl-daemon-async-jobs-design.md` — async job model design
- `docs/superpowers/specs/2026-06-23-phonectl-idempotency-cache-eviction-design.md` — idempotency-cache design
- `docs/superpowers/specs/2026-06-22-phonectl-macro-engine-design.md` — macro engine spec (6.0, **read before executing Phase 6**)

Phase 6–7 implementation plans (📝 written, not yet executed):

- `docs/superpowers/plans/phonectl-plan-6.1-macro-runtime-core.md` — ✅ done
- `docs/superpowers/plans/phonectl-plan-6.2-triggers-scheduler-and-event-subscriptions.md` — ✅ done
- `docs/superpowers/plans/phonectl-plan-6.3-progressive-autonomy-and-memory-layer.md` — **NEXT UP**
- `docs/superpowers/plans/phonectl-plan-7.1-shizuku-provider.md`
- `docs/superpowers/plans/phonectl-plan-7.2-optional-root-provider.md`
- `docs/superpowers/plans/phonectl-plan-7.3-low-latency-transport.md`
- `docs/superpowers/plans/phonectl-plan-7.4-tasker-macrodroid-interop.md`

Android APK design specs (companion app, not yet built):

- `android/accessibility-companion/SPEC.md` — AccessibilityService + companion APK
- `android/foreground-service/SPEC.md` — foreground service transport + emergency-stop UX

Superseded plans (traceability only — do not execute):

- `docs/superpowers/plans/archive/2026-06-21-phonectl-*.md` — six superseded follow-up plans

Do not start writing code from a fresh interpretation — execute the relevant plan's tasks in order so the test-first discipline and the commit boundaries are preserved.

## Project: phonectl

A Python CLI that lets an AI agent observe the host Android phone as structured JSON and act on it (`tap`/`type`/`swipe`/`key`/`launch`) over **ADB with no root**, from inside Termux + PRoot-Distro. The contract is an `observe → act → observe` loop driven by element indices, not pixels. The daemon (`phonectl daemon`) is the north-star single-writer: CLI and MCP are frontends; the daemon is optional but preferred when running.

## Commands

```bash
pip install -e .           # install the package + console-script `phonectl`
pip install -e ".[mcp]"    # also install the optional FastMCP transport
pytest -v                  # full suite (555 tests, ~58 files)
pytest tests/test_ui_parser.py -v          # one file
pytest tests/test_ui_parser.py::test_parse_bounds -v   # one test
```

There is no linter or formatter configured in the plan; do not add one unless the user asks.

## Architecture invariants (must hold across changes)

- **Backend isolation.** Only `adb_backend.py` knows about `adb`. All other layers speak the `backend.Backend` Protocol or the `ProviderRegistry`. Never call `subprocess` or `adb` from anywhere else.
- **Provider graph.** `ProviderRegistry` (`providers/registry.py`) selects the best provider per capability with graceful degradation and reports the provider path that satisfied each call. Add new providers to the registry, not to `cli.py` directly. Each provider has capability discovery, opt-in config, and degrades cleanly when its underlying service is absent.
- **`ui_parser` is pure.** XML → `list[dict]` of indexed elements, plus `screen_hash`. No I/O, no subprocess. All edge cases (the trailing `UI hierchary dumped to: /dev/tty` line, missing attrs) live here, fixture-tested.
- **Element index `i` is the primary target.** Raw `(x,y)` is an escape hatch. This is what makes the agent portable across screen sizes.
- **Every `act()` re-observes.** `actuator.tap/type_text/swipe/key/launch` must return the post-action snapshot via `observer.observe`. The screen-hash change is how the loop knows the action landed.
- **Injectable `runner`.** `AdbBackend.__init__(runner=subprocess.run)` — tests pass a fake runner that records calls. Do not bypass it.
- **Stdlib only at runtime.** Python ≥ 3.9. `pytest` is dev-only. The `mcp` optional extra (`pip install -e ".[mcp]"`) gates the FastMCP transport in `mcp_server.py` — the rest of the runtime stays stdlib-only. Adding a hard runtime dep needs an explicit reason.
- **Every runtime action goes through `runtime.run_action`.** This is the single choke-point for mode gating (`auto`/`confirm`/`dry-run`), kill-switch check (`audit.kill_switch_active()`), risk classification + policy decision, rate limiting, idempotency, and audit logging. Do not bypass it.
- **Daemon is the single writer in daemon mode.** When `phonectl daemon` is running, `cli._dispatch` auto-routes actions to it over loopback JSON-RPC. The daemon reuses `runtime.run_action` verbatim — the safety invariants hold identically in-process and over the wire.
- **Every action appends to `actions.jsonl`** via `audit.log_action` with `ts`, `verb`, `target`, resulting `app`, `hash`. Durable `runs.jsonl` records are appended by the daemon for each async job.
- **Structured results everywhere.** Every runtime/provider/MCP call returns a `results` envelope (`results.ok` / `results.err`). Never return bare tuples from these layers.
- **`PHONECTL_HOME` isolation.** Tests use `monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))` to isolate config + audit + kill-switch + rate-limit history. Keep using this pattern.

## Environment & runtime topology

- The agent lives inside a **PRoot-Distro** inside **Termux** on an **unrooted Android 11+** device. `uid 0` in the distro is proot-root, not device root — assume no root anywhere.
- ADB connects over **Wireless Debugging** on `127.0.0.1:<port>`. PRoot shares Termux's (and Android's) loopback, so `adb` runs inside the distro and dials adbd directly. If PRoot blocks the connection, the fallback is a thin shim to host Termux's `adb`; the interface above is unchanged.
- The optional companion APK (not yet built; see `android/`) will communicate over a loopback `SocketTransport` (`providers/transport.py`). The transport degrades cleanly when the APK is absent.
- `PHONECTL_HOME` overrides the config dir (default `~/.config/phonectl`). `daemon.json` is written there by the daemon on startup and used for port discovery.

## Plan-execution discipline

- **One commit per task minimum.** The plan's Task N → Step 5 commit messages are the canonical commit shapes; follow them.
- **TDD order is non-negotiable:** write the failing test, run it to confirm it fails for the right reason, then write the minimum code to pass. Do not pre-implement ahead of the test.
- **Don't claim device behavior you haven't run.** Resilience-class work (port recovery, keyguard strings, the host-Termux shim), companion-APK transport, and OCR are ROM-specific — their plans flag exactly which steps need an on-device smoke. Unit tests use injected fakes; they prove the logic, not the topology.
- **brainstorm → spec → plan is required before Phase 6+ work.** Read the macro-engine design spec (`docs/superpowers/specs/2026-06-22-phonectl-macro-engine-design.md`) before executing any Phase 6 plan.

## What's deferred (do not build without an explicit ask)

**Execution order:** Phases 6.1 and 6.2 are ✅ done. Phase 6.3 (progressive autonomy + memory layer) is the next plan to execute.
Phases 7.1–7.4 follow Phase 6. Phase X (evaluation suite) has no full plan yet.

**Deferred from 6.2 → fold into 6.3:** bus-envelope `snapshot`/`device` enrichment (snapshot-dependent conditions like `foreground_package`/`battery_min` are currently inert on auto-fired triggers); DEBUG logging of swallowed trigger errors in the daemon `events_poll` loop; `Scheduler._armed` cleanup on disable; routing trigger/scheduler fires through `JobRegistry` (today they execute inline in the single-threaded `events_poll` handler).

Do not pull work forward across plan boundaries and do not bolt platform concepts onto ad-hoc commands.

The companion **APK itself** (Kotlin — AccessibilityService + foreground service) is a separate native effort scoped by `android/accessibility-companion/SPEC.md` and `android/foreground-service/SPEC.md`; Phase 4 ships only the Python-side provider seam against those specs.
