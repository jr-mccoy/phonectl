# phonectl — Architecture Invariants & Working Discipline

Durable rules for working in this repo. Stable across changes (unlike phase status,
which lives in the roadmap + `crumb resume`). CLAUDE.md points here; read this before
changing core layers.

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
- The optional companion APK (✅ shipped, Kotlin `com.phonectl.companion`; see `android/`) communicates over a loopback `SocketTransport` (`providers/transport.py`). The transport degrades cleanly when the APK is absent.
- `PHONECTL_HOME` overrides the config dir (default `~/.config/phonectl`). `daemon.json` is written there by the daemon on startup and used for port discovery.

## Plan-execution discipline

- **One commit per task minimum.** The plan's Task N → Step 5 commit messages are the canonical commit shapes; follow them.
- **TDD order is non-negotiable:** write the failing test, run it to confirm it fails for the right reason, then write the minimum code to pass. Do not pre-implement ahead of the test.
- **Don't claim device behavior you haven't run.** Resilience-class work (port recovery, keyguard strings, the host-Termux shim), companion-APK transport, and OCR are ROM-specific — their plans flag exactly which steps need an on-device smoke. Unit tests use injected fakes; they prove the logic, not the topology.
- **brainstorm → spec → plan is required before Phase 6+ work.** Read the macro-engine design spec (`docs/superpowers/specs/2026-06-22-phonectl-macro-engine-design.md`) before executing any Phase 6 plan.
- **Execute the relevant plan's tasks in order** — do not start writing code from a fresh interpretation. This preserves the test-first discipline and commit boundaries. Do not pull work forward across plan boundaries or bolt platform concepts onto ad-hoc commands.

## What's deferred (do not build without an explicit ask)

- **Phase 7.1 (Shizuku provider) is the next plan to execute.** Phases 7.1–7.4 follow Phase 6 (✅ complete). Phase X (evaluation suite) has no full plan yet.
- **Deferred from 6.2 → fold into 6.3/later:** bus-envelope `snapshot`/`device` enrichment (snapshot-dependent conditions like `foreground_package`/`battery_min` are currently inert on auto-fired triggers); DEBUG logging of swallowed trigger errors in the daemon `events_poll` loop; `Scheduler._armed` cleanup on disable; routing trigger/scheduler fires through `JobRegistry` (today they execute inline in the single-threaded `events_poll` handler).
- **Deferred from 6.3:** wiring `memory.capture_from_runs` into the daemon run-record path (needs action-record enrichment — selector `matched_i` + `app_version`/`locale`; folds into the deferred selector-library override work).
- The companion **APK itself** (Kotlin — AccessibilityService + foreground service) was a separate native effort scoped by `android/accessibility-companion/SPEC.md` and `android/foreground-service/SPEC.md`; it is now ✅ shipped (Plans 4.5–4.8). Phase 4's Python-side provider seam talks to it over the loopback transport.
