# droidjig — Architecture Invariants

The load-bearing rules of the codebase: properties that must hold across changes, and the
reasoning behind them. Stable over time, unlike phase status (see `docs/roadmap.md`).
Read this before changing a core layer.

## Architecture invariants (must hold across changes)

- **Backend isolation.** Only `adb_backend.py` knows about `adb`. All other layers speak the `backend.Backend` Protocol or the `ProviderRegistry`. Never call `subprocess` or `adb` from anywhere else.
- **Provider graph.** `ProviderRegistry` (`providers/registry.py`) selects the best provider per capability with graceful degradation and reports the provider path that satisfied each call. Add new providers to the registry, not to `cli.py` directly. Each provider has capability discovery, opt-in config, and degrades cleanly when its underlying service is absent.
- **`ui_parser` is pure.** XML → `list[dict]` of indexed elements, plus `screen_hash`. No I/O, no subprocess. All edge cases (the trailing `UI hierchary dumped to: /dev/tty` line, missing attrs) live here, fixture-tested.
- **Element index `i` is the primary target.** Raw `(x,y)` is an escape hatch. This is what makes the agent portable across screen sizes.
- **Every `act()` re-observes.** `actuator.tap/type_text/swipe/key/launch` must return the post-action snapshot via `observer.observe`. The screen-hash change is how the loop knows the action landed.
- **Injectable `runner`.** `AdbBackend.__init__(runner=subprocess.run)` — tests pass a fake runner that records calls. Do not bypass it.
- **Stdlib only at runtime.** Python ≥ 3.10. `pytest` is dev-only. The `mcp` optional extra (`pip install -e ".[mcp]"`) gates the FastMCP transport in `mcp_server.py` — the rest of the runtime stays stdlib-only. Adding a hard runtime dep needs an explicit reason.
- **Every runtime action goes through `runtime.run_action`.** This is the single choke-point for mode gating (`auto`/`confirm`/`dry-run`), kill-switch check (`audit.kill_switch_active()`), risk classification + policy decision, rate limiting, idempotency, and audit logging. Do not bypass it.
- **Daemon is the single writer in daemon mode.** When `droidjig daemon` is running, `cli._dispatch` auto-routes actions to it over loopback JSON-RPC. The daemon reuses `runtime.run_action` verbatim — the safety invariants hold identically in-process and over the wire.
- **Every action appends to `actions.jsonl`** via `audit.log_action` with `ts`, `verb`, `target`, resulting `app`, `hash`. Durable `runs.jsonl` records are appended by the daemon for each async job.
- **Structured results everywhere.** Every runtime/provider/MCP call returns a `results` envelope (`results.ok` / `results.err`). Never return bare tuples from these layers.
- **All JSON state goes through `state.py`.** Reads use `state.read_json`/`state.read_jsonl` (a corrupt, truncated or unreadable file degrades to the default — it must never raise out of a command); writes use `state.write_json` (temp file + `fsync` + `os.replace`, so a reader never sees a half-written file and an interrupted write keeps the previous good state). These files live on a phone, where `kill -9`, a dead battery and a full disk are routine. Never call `json.loads(path.read_text())` or `path.write_text(json.dumps(...))` on a state file directly.
- **`DROIDJIG_HOME` isolation.** Tests use `monkeypatch.setenv("DROIDJIG_HOME", str(tmp_path))` to isolate config + audit + kill-switch + rate-limit history. Keep using this pattern.

## Environment & runtime topology

- The agent lives inside a **PRoot-Distro** inside **Termux** on an **unrooted Android 11+** device. `uid 0` in the distro is proot-root, not device root — assume no root anywhere.
- ADB connects over **Wireless Debugging** on `127.0.0.1:<port>`. PRoot shares Termux's (and Android's) loopback, so `adb` runs inside the distro and dials adbd directly. If PRoot blocks the connection, the fallback is a thin shim to host Termux's `adb`; the interface above is unchanged.
- The optional companion APK (✅ shipped, Kotlin `com.droidjig.companion`; see `android/`) communicates over a loopback `SocketTransport` (`providers/transport.py`). The transport degrades cleanly when the APK is absent.
- `DROIDJIG_HOME` overrides the config dir (default `~/.config/droidjig`). `daemon.json` is written there by the daemon on startup and used for port discovery.

## Testing discipline

- **Tests come first.** Write the failing test, confirm it fails for the right reason, then write
  the minimum code to pass it. The suite is the specification of intended behavior.
- **Unit tests prove logic, not topology.** They run against injected fakes (`runner=`, fake
  transports), so they cannot prove device behavior. Resilience work (port recovery, keyguard
  strings, the host-Termux shim), the companion transport, and OCR are all ROM-specific and are
  only truly verified by the on-device smoke matrix in `docs/integration-smoke.md`.
- **Don't document device behavior that hasn't been run on a device.** Where a claim is
  unvalidated, the docs say so explicitly.

## Known gaps and deferred work

- **Next up: Phase 7.1 (Shizuku provider).** Phases 7.1–7.4 follow Phase 6 (complete).
- **Trigger conditions that depend on a snapshot are currently inert on auto-fired triggers**
  (`foreground_package`, `battery_min`) — the event-bus envelope does not yet carry
  `snapshot`/`device` enrichment. Also pending on the daemon event loop: DEBUG logging of
  swallowed trigger errors in `events_poll`, `Scheduler._armed` cleanup on disable, and routing
  trigger/scheduler fires through `JobRegistry` (today they execute inline in the
  single-threaded `events_poll` handler).
- **`memory.capture_from_runs` is not wired into the daemon run-record path.** It needs
  action-record enrichment first (selector `matched_i` plus `app_version`/`locale` context),
  which folds into the deferred selector-library override work.
- **The companion APK** (Kotlin — AccessibilityService + foreground service) is scoped by
  `android/accessibility-companion/SPEC.md` and `android/foreground-service/SPEC.md`. It ships;
  the Python-side provider seam talks to it over the loopback transport and degrades cleanly
  when it is absent.
