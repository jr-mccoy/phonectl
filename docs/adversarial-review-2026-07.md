# phonectl — Adversarial Technical Review

> **Status: all 16 findings remediated.** This is a self-commissioned adversarial review of
> phonectl, conducted 2026-07-01, followed by a full remediation pass. It is published as a
> record of the project's security posture and how it was arrived at. **The risk assessment
> below is the snapshot as of the review date, before any fixes landed** — see
> [Remediation](#remediation) for what shipped in response, and
> [Residual risk](#residual-risk) for what is still owed.

**Date:** 2026-07-01
**Scope:** Full system — Python CLI, provider architecture, MCP interface, daemon, macro
engine, Android companion APK/specs, transport/trust model, docs, tests, packaging.
**Method:** Firsthand source reading of every core layer plus the companion Kotlin, a full
test run (`586 passed, 1 skipped` at the time of review), and cross-checking documented
claims against code. All findings cite real `file:line` evidence.

## Remediation

All 16 findings were fixed across three passes:

| Findings | What shipped |
|---|---|
| 1, 2 | Emergency STOP is no longer agent-reachable; local transports (companion socket, daemon RPC) are authenticated. |
| 4, 5, 6, 7, 8, 12, 15 | `intent_start` risk classification with `tel`/`sms` as critical; safe defaults (`mode: confirm`, unknown capabilities disabled); `scroll_until` routed through `run_action` with mid-loop STOP checks; intent/launch shell quoting; fail-closed companion STOP check actually wired into `run_action`; clipboard audit logs a length surrogate rather than content; uniform exit codes. |
| 3, 9, 10, 11, 13, 14, 16 | On-device STOP gate in the companion dispatcher (SharedPrefs authoritative; the inert env STOP-file fallback removed); tree-generation tokens binding `set_text`/semantic actions to the observation they came from, with ambiguous-node-id refusal; guarded-app checks extended to observe/screencap/OCR/events/notification reads; `screencap` constrained to companion-owned storage; token-authenticated `LifecycleReceiver`; cross-process idempotency store plus an `action.lock` flock; registry runtime fallback with truthful `provider` fields (policy refusals never fall back). |

The Kotlin-side fixes are compiled and JVM-unit-tested green in CI — the `android.yml`
workflow runs `./gradlew assembleDebug test`, building the debug APK and executing
`GenerationTest`, `GuardsAndFlagsTest`, `LifecycleAuthTest`, and `DispatcherTest`.

## Residual risk

- **Not exercised on a real Android runtime:** instrumented tests (`connectedAndroidTest` —
  CI runs no emulator) and the manual on-device smoke matrix in `docs/integration-smoke.md`.
- **Still open by design:** the longer-term human-only sensitive-action policy layer
  (roadmap item 16). Until it lands, the honest posture remains the one in the table
  below — phonectl is built for *supervised* agent use, not unattended autonomy.

---

## Executive Summary

*Everything from here to the end of the findings is the review as written on 2026-07-01,
preserved unedited. It describes the codebase **before** the remediation above.*

The *architecture* is sound: clean layering, a real single-writer choke-point
(`runtime.run_action`), uniform structured result envelopes, a stdlib-only runtime, 586
passing tests, and unusually honest inline documentation. The choke-point genuinely holds
across the CLI, the daemon, and the macro engine.

The *safety and trust model* has holes that matter specifically because the stated purpose
is to let an autonomous AI agent drive a real phone.

**Overall risk level: High** for autonomous agent use; **Moderate** for supervised/manual use.

| Use case | Ready? |
|---|---|
| Personal manual CLI use | Yes, with caveats (set `mode: confirm`) |
| Supervised AI-agent use | Not yet, but close (remove agent-reachable `resume`; default `confirm`; don't expose companion to untrusted local apps) |
| Autonomous AI-agent use | No |
| Public release | No (docs assert guarantees the code doesn't fully deliver) |

### Top 5 issues

1. **The agent can disable its own kill switch.** `phone_resume` (MCP) and `resume` (daemon
   RPC) delete the `STOP` file with no gating (`mcp_server.py:355`, `daemon/server.py:115-120`).
2. **Three unauthenticated local control surfaces.** The companion socket
   (`Server.kt`), the daemon RPC (`daemon/server.py:366-383`), and MCP `resume` are all
   reachable by other apps on the device — Android loopback is *not* a UID boundary — and all
   can clear the kill switch and drive full device control.
3. **The companion's STOP flag does not gate its own handlers** (`CompanionForegroundService.kt:73`);
   enforcement is delegated to the Python client, so any direct client acts despite STOP.
4. **Risk classification misses high-impact verbs** — `intent_start`, `tap`, `type`, `swipe`,
   `key`, `launch` carry no verb-level risk (`risk.py:44-45`); the README claims `intent start`
   is high-risk.
5. **Unsafe by default** — `mode` defaults to `auto` (no confirmation, `config.py:44`); companion
   capability toggles default to enabled (`Capabilities.kt:51`).

---

## Severity-ranked Findings

### Finding 1 — Agent can clear its own emergency STOP (MCP + daemon)

**Severity:** Critical · **Likelihood:** High · **Area:** MCP / Daemon / Risk Policy

**Problem.** `resume()` unconditionally deletes the `STOP` sentinel and is exposed as the
ungated `phone_resume` MCP tool (`mcp_server.py:355-359`, registered `:509`) and as the
daemon `resume` RPC (`daemon/server.py:115-120`). Both are reachable by the agent/clients
with no confirmation or capability check.

**Why it matters.** A kill switch reachable by the thing it is meant to stop is not a kill
switch. An agent "trying to make progress" will plausibly call it.

**Fix.** Remove `resume` (and reconsider `stop`) from agent-facing surfaces. Require an
out-of-band human action to resume (host CLI, companion notification/tile).

**Tests.** `test_mcp_resume_not_agent_accessible`, `test_daemon_resume_not_in_agent_surface`.

---

### Finding 2 — Unauthenticated local transports (companion socket, daemon RPC)

**Severity:** Critical · **Likelihood:** Medium · **Area:** Companion APK / Daemon / Transport

**Problem.** The companion binds `127.0.0.1` and drops non-loopback peers (`Server.kt:40,61`)
but has **no shared secret / token** anywhere (`CoreHandlers.kt`, `Dispatcher.kt`,
`Envelope.kt`); the port is a fixed default `8765`. The daemon serves NDJSON RPC and accepts
any connection with no auth (`daemon/server.py:366-383,473-484`). On Android, loopback is not
isolated between apps: any installed app with INTERNET permission can `connect()` and drive
gestures, text entry, notification replies, screenshots, OCR (companion) or `act`/`macro_run`/
`stop`/`resume`/`memory_delete` (daemon).

**Why it matters.** The companion wraps a full AccessibilityService — an open local RPC into it
is a privilege-escalation and screen-exfiltration surface. Port-squatting is the inverse: a
hostile app binding `8765` first can feed the Python client fabricated observations (the client
has no auth to detect the imposter — `providers/transport.py:43-78`).

**Fix.** Add a shared token to both transports. The daemon can write a token into `daemon.json`
(already unreadable by other apps) and require it per-RPC. The companion needs a pairing step
(display a token in the APK UI, paste into `phonectl` config) since cross-UID file sharing is
awkward. Document explicitly that loopback is not a security boundary on Android.

**Tests.** Kotlin `server_rejects_unauthenticated_request`; `test_daemon_rejects_rpc_without_token`.

---

### Finding 3 — Companion STOP does not gate its own action handlers (fail-open on-device)

**Severity:** Critical · **Likelihood:** Medium · **Area:** Companion APK / Kill Switch

**Problem.** The dispatcher gate is `Capabilities.methodGate(state)`
(`CompanionForegroundService.kt:73`), which checks only per-capability toggles
(`Capabilities.kt:95-98`) and never consults `state.isStopped()`. STOP only affects the
handshake's reported flag and the notification UI; the comment admits enforcement is delegated
to Python (`CompanionForegroundService.kt:25-27`). Worse, the `$PHONECTL_HOME/STOP` file
"hard guarantee" is inert cross-UID: `resolveStopFilePath()` reads the *APK's*
`System.getenv("PHONECTL_HOME")` (`SharedPrefsTrustState.kt:56-60`), which the APK (a different
Android UID than Termux) will not have and cannot read.

**Why it matters.** Any direct client bypasses STOP entirely, and the file fallback meant to
survive crashes never reaches the APK.

**Fix.** Add `isStopped()` to the dispatcher gate so every method fails closed with a `stopped`
error, independent of the Python client. Have the companion own its STOP state authoritatively
(SharedPrefs) rather than reading a cross-UID file.

**Tests.** Kotlin `dispatcher_refuses_all_action_methods_when_stopped`,
`handshake_and_ping_still_allowed_when_stopped`.

---

### Finding 4 — Risk classifier misses `intent_start` and core verbs; README overstates the gate

**Severity:** High · **Likelihood:** High · **Area:** Risk Policy / Docs

**Problem.** `HIGH_RISK_VERBS = {packages_stop, intent_broadcast, notifications_reply}`,
`CRITICAL_VERBS = {packages_clear}` (`risk.py:44-45`). Everything else — `tap`, `type`,
`swipe`, `key`, `launch`, and **`intent_start`** — has no verb-level signal; risk is `low`
unless *observed screen text* matches a keyword. `intent_start` is arbitrary `am start`
(`tel:` dialer, `ACTION_CALL`, arbitrary components, deep links) and often shows no triggering
keyword. README (`README.md:263`) claims intent `start` is high-risk and requires `--yes` —
false in code. Content signals are also noisy: `install_keyword` includes "allow", "grant",
"send", "subscribe" (`risk.py:30`) → confirmation fatigue; `otp_like_content` fires on any 4–8
digit number.

**Why it matters.** In default `auto` mode, `low` actions just run. An agent can dial, fire a
deep link, or launch a payment intent with zero gating on a benign-looking screen.

**Fix.** Add `intent_start` to `HIGH_RISK_VERBS` (classify `tel:`/`CALL`/`sms:` critical).
Trim `install_keyword`. Reconcile README with code.

**Tests.** `test_intent_start_is_high_risk`, `test_intent_start_tel_is_critical`,
`test_verb_risk_matrix`.

---

### Finding 5 — Unsafe by default: `mode: auto` + default-enabled companion toggles

**Severity:** High · **Likelihood:** High · **Area:** Config / Companion / Agent Safety

**Problem.** `get_mode` defaults to `"auto"` (`config.py:44`) → actions execute with no
confirmation. Companion `DEFAULT_ENABLED = true` (`Capabilities.kt:51`); SharedPrefs default
enabled (`SharedPrefsTrustState.kt:21-22`). Python `gate_capabilities` defaults unknown keys to
enabled (`trust.py:38-39`, documented `README.md:683-684`). Everything is on and unconfirmed
until a user opts out.

**Fix.** Default `mode` to `confirm` (or a new `agent` mode that denies high/critical outright).
Default sensitive companion caps (`set_text`, `notifications_reply`, `ocr`, `screencap`) to
disabled. Default unknown capability keys to disabled.

**Tests.** `test_default_mode_is_confirm`, `test_unknown_capability_defaults_disabled`.

**Status (companion half — done).** The Python half landed earlier (`mode: confirm`,
`gate_capabilities` defaults unknown keys off). The companion half is now complete: the scalar
`Capabilities.DEFAULT_ENABLED = true` is replaced by a per-key `DEFAULT_ENABLED_BY_KEY`
(`Capabilities.defaultFor`) so a fresh install ships the sensitive caps
(`act_set_text_native`, `notifications_reply`, `observe_ocr`, `observe_ocr_screen`,
`observe_screenshot`) **disabled** while the MVP observe/gesture/launch/notification-list caps
stay enabled. `SharedPrefsTrustState` and the `SettingsActivity` switch defaults consult the same
map (no UI-vs-handshake mismatch); the user opts into sensitive caps in the Settings UI.
`notifications_dismiss` stays enabled (nuisance vector, not confidentiality/spend). JVM contract
tests updated (`HandshakeTest`, `OcrContractTest`, `CapabilitiesTest`).

---

### Finding 6 — `scroll_until` bypasses the choke-point (MCP) and can't be interrupted mid-loop (CLI)

**Severity:** High · **Likelihood:** Medium · **Area:** MCP / Architecture / Kill Switch

**Problem.** `scroll_until` loops mutating `scroll()` gestures (`actuator.py:162-177`). MCP
`scroll_until_mcp` calls it directly (`mcp_server.py:314-325`) — no kill switch, rate limit,
risk gate, or audit. CLI wraps the whole loop in one `run_action` (`cli.py:370`) but the inner
`scroll()` calls are direct, so the loop is gated once at entry and cannot be halted mid-loop.
Contradicts `README.md:189,375`.

**Fix.** Route each iteration through `run_action`, or at minimum re-check the kill switch and
rate limit inside the loop; make the MCP path go through the funnel.

**Tests.** `test_scroll_until_mcp_routes_through_run_action`,
`test_scroll_until_halts_on_stop_midloop`.

---

### Finding 7 — `intent_start`/`intent_broadcast` device-side args are not quoted

**Severity:** High · **Likelihood:** Medium · **Area:** ADB / Security

**Problem.** `input_text` and `clipboard_write` correctly `shlex.quote` for the device shell
(`adb_backend.py:46-50,86-87`), but `intent_start`/`intent_broadcast` pass `action`, `data`,
`component`, and extras as bare args (`adb_backend.py:92-111`). `adb shell am start …` is
concatenated and re-parsed by the device shell, so an unquoted value with spaces, `;`, `&`,
`$`, backticks, or quotes is re-tokenized on-device. `--data` containing `&` (common in URLs)
breaks even without a malicious actor.

**Fix.** `shlex.quote` every device-shell token in `intent_start`/`intent_broadcast` (and
`launch`'s package).

**Tests.** Parametrized `test_intent_fields_shell_quoted` over `; & $ \` ' " %s <space>`.

---

### Finding 8 — Kill switch is fail-open on the companion path; docs present it as safe

**Severity:** High · **Likelihood:** Medium · **Area:** Kill Switch / Audit

**Problem.** `kill_switch_active` checks the local `STOP` file (fail-closed) but wraps every
`extra_check` — including `companion_stopped` — in `try/except Exception: continue`
(`audit.py:10-15`); `companion_stopped` → `negotiate` returns `stopped=False` on any transport
exception (`trust.py:19-20,34-35`). So an unreachable/slow/erroring companion at the moment STOP
is engaged is read as "not stopped" and the action proceeds. README documents the swallow
(`README.md:695-696`) while also claiming STOP "blocks **all** action verbs immediately"
(`README.md:691`).

**Fix.** Treat an unreachable-but-configured companion as stopped (fail-closed), or surface a
distinct `stop_check_unavailable` error. Distinguish "no companion configured" from "companion
configured but unreachable."

**Tests.** `test_stop_check_failclosed_when_companion_configured_but_unreachable`.

---

### Finding 9 — Node IDs are unstable index paths with no observation binding

**Severity:** High · **Likelihood:** Medium · **Area:** Companion APK / Agent Safety

**Problem.** Nodes lacking `viewIdResourceName` get id `w<windowId>/<childIndex>/...`
(`NodeId.kt:15-22`). Any tree change shifts the path, so the same id resolves to a different
node; `findByIdRec` re-walks the *current* tree by recomputed path
(`CompanionAccessibilityService.kt:360-373`). Even `viewIdResourceName` is non-unique (list rows
share ids) and `findByIdRec` returns the **first** match. No generation/version token binds an
action to its observation. Presented as durable (`README.md:602-608`). The Python `expected_hash`
guard is opt-in (`actuator.py:11-17`) and doesn't cover the node-id path.

**Fix.** Return a tree-generation token per observation; require it on `set_text`/`semantic`;
reject stale generations and ambiguous node-id matches.

**Tests.** Kotlin `findById_refuses_ambiguous_resource_id`, `set_text_rejects_stale_generation`.

---

### Finding 10 — Guarded-app protection covers actions but not observation/screenshot/OCR

**Severity:** High · **Likelihood:** Medium · **Area:** Companion APK / Privacy

**Problem.** `requireUnguarded` blocks `gesture`/`key`/`set_text`/`semantic`; `launch` checks the
target (`CompanionAccessibilityService.kt:93-111,388-402`). But `observe_native`, `screencap`,
`ocr_screen`, `events`, and notification reads have no guarded check. A banking app on the
guarded list is still fully readable: UI tree (passwords blanked at `:144`, everything else
exposed), a full-res PNG to an arbitrary path (`screencap` `:308-342`), and OCR of the screen.

**Fix.** Apply guarded-app checks to observation/capture/OCR/notification reads for guarded
packages. Document precisely what "guarded" protects.

**Tests.** Kotlin `observe_native_refuses_guarded_app`, `screencap_refuses_guarded_foreground`.

---

### Finding 11 — Idempotency and single-writer lock are process-local (one-shot CLI)

**Severity:** Medium · **Likelihood:** High · **Area:** Runtime / Architecture

**Problem.** `_idempotency_cache` and `_action_lock` are module globals (`runtime.py:11-12`).
Each CLI invocation is a fresh process, so `--idempotency-key` never dedupes across invocations
and two concurrent CLI processes get no mutual exclusion. The daemon (long-lived) does honor
both, and the rate limit persists to disk (`ratelimit.json`). Idempotency's process-locality is
disclosed (`README.md:423`); the single-writer lock claim (`README.md:414`) is not caveated.

**Fix.** Persist idempotency + a file lock to `$PHONECTL_HOME` for the one-shot CLI, or make the
docs unambiguous and warn at runtime when a key is supplied without a daemon.

**Tests.** `test_idempotency_key_dedupes_across_processes`, `test_two_cli_processes_serialize`.

---

### Finding 12 — `clipboard write` logs the first 20 chars of content

**Severity:** Medium · **Likelihood:** Medium · **Area:** Audit / Privacy

**Problem.** `ClipboardProvider.write` passes `repr(text[:20])` as the audit target
(`clipboard.py:37`), unlike `type` which uses `<N chars>` (`cli.py:269`). Redaction
(`redact.py:7-20`) only masks emails, `token=`-style secrets, and digit runs — an alphanumeric
password matches none and is logged (at `redacted`; fully at `full`).

**Fix.** Use `<N chars>` for the clipboard target; broaden redaction to high-entropy tokens or
never log clipboard content.

**Tests.** `test_clipboard_write_target_is_length_only`, `test_redact_masks_alphanumeric_secret`.

---

### Finding 13 — No runtime provider fallback; `provider` field often mislabeled

**Severity:** Medium · **Likelihood:** Medium · **Area:** Architecture / Providers

**Problem.** `ProviderRegistry.for_capability` selects the first provider advertising a
capability (`registry.py:17-21`); `_require` raises if none do (`:48-55`). There is no
try-next-provider on runtime failure — a companion that advertises `observe_ui_tree` but throws
mid-request hard-fails with no ADB fallback. Cross-provider coordinate/index semantics differ
(accessibility bounds vs ADB pixels vs OCR regions), and MCP observe handlers hardcode
`provider="adb"` (`mcp_server.py:31,57,65`) so the agent can't detect a switch.

**Fix.** Wrap delegation to fall through to the next capable provider on runtime failure (with an
audit note). Populate `provider` truthfully everywhere (`registry.last_used`).

**Tests.** `test_action_falls_back_to_adb_when_companion_raises`,
`test_observe_reports_true_provider`.

---

### Finding 14 — `LifecycleReceiver` exported with no permission

**Severity:** Medium · **Likelihood:** Low · **Area:** Companion APK / Security

**Problem.** `LifecycleReceiver` is `exported="true"` with `START_SERVICE`/`STOP_SERVICE` and no
permission (`AndroidManifest.xml:78-85`). Any app or `am broadcast` can start/stop the companion
(DoS or widen the attack window). Combined with Finding 2, the companion has no authenticated
control surface.

**Fix.** Protect with a signature-level permission or the same token as the socket.

**Tests.** Manifest lint; instrumentation test that an unprivileged broadcast is rejected.

---

### Finding 15 — Inconsistent exit codes across command handlers

**Severity:** Low · **Likelihood:** High · **Area:** CLI / UX

**Problem.** `_do_action` maps `stopped→2`, `confirmation_required→3` (`cli.py:229-234`,
documented `README.md:526-533`), but `intent`/`clipboard`/`notifications` handlers return
`0 if ok else 1` (`cli.py:776,790,814,836`). A STOP or confirm-refusal on those commands exits
`1`, contradicting the documented codes and breaking script/agent recovery logic.

**Fix.** Route these through the same envelope→exit-code mapping.

**Tests.** `test_intent_start_stopped_exits_2`, `test_clipboard_write_confirm_required_exits_3`.

---

### Finding 16 — `screencap` writes to client-supplied path; OCR/observe return raw sensitive text

**Severity:** Medium · **Likelihood:** Low · **Area:** Companion APK / Privacy

**Problem.** `screencap` takes `path` from request params and writes a full-res screenshot there
(`CompanionAccessibilityService.kt:308-342`), no guarded check, no path validation. `ocr_screen`
returns all regions un-redacted (`:306`). Python OCR/observe return raw text too; redaction is
audit-only. Combined with Findings 2 and 10, on-screen secrets are readable and screenshots
writable to attacker-chosen locations.

**Fix.** Constrain `screencap` output to app-internal storage; enforce guarded-app checks on
capture/OCR; consider optional on-device OCR redaction.

**Tests.** Kotlin `screencap_rejects_paths_outside_app_dir`, `ocr_respects_guarded_app`.

---

## What is done well (preserve these)

- The choke-point genuinely holds across CLI, **daemon** (`daemon/server.py:310-317`; write
  lock + single JobRegistry worker), and **macros** (`macro/engine.py:197-200`; a `stopped`
  envelope halts the run). Macros add an autonomy pre-gate on top.
- Daemon has stronger stale protection than CLI/MCP: `snapshot_id` validated against foreground
  (`daemon/server.py:280-296`).
- Typed text and clipboard writes are shell-quoted for the device shell (`adb_backend.py:50,87`).
- `type` / `notifications_reply` audit only `<N chars>` (`cli.py:269`, `mcp_server.py:91,414`).
- Password fields blanked in the companion tree (`CompanionAccessibilityService.kt:144`).
- Diagnostics bundle excludes audit `target` and masks secret-named config keys
  (`diagnostics.py:17-40`).
- Kotlin dispatcher logs method+outcome only, never payloads (`Dispatcher.kt:26-28`).
- Request-id matching for stale-response protection (`transport.py:68`, `notifications.py:78`).
- **No cloud/telemetry** — outbound is only ADB + loopback (verified: no network egress in the
  runtime).
- Loopback binding enforced on both ends (`transport.py:47`, `Server.kt:40`, `daemon/server.py:21`).

---

## Edge Case Inventory (uncovered / weakly covered)

**Android / ADB / Termux**
- Wireless-debugging port rotation after reboot: partially handled via mDNS + `probe_ports`
  (`connection.py:51-79`), but `probe_ports` defaults empty and mDNS is OEM-dependent; no test
  with a rotated port.
- Multiple devices/emulators: `-s serial` only added when set (`adb_backend.py:11-12`); no serial
  + >1 device is unhandled.
- `unauthorized`/`offline`/`reconnecting`: `ensure()` only checks `state == "device"`
  (`connection.py:26-39`); auth prompts fall through to reconnect that won't fix them.
- Pairing vs connect port confusion: separated in code but not validated.
- **Unicode/emoji cannot be typed via `adb shell input text`** on most Android versions —
  silently drops/mistypes; not documented; companion `set_text` differs (can do Unicode).
- `wm size` parses a single line (`adb_backend.py:36-41`) and breaks if `Override size` is also
  printed.

**Screen state**
- Locked/biometric: `biometric_prompt` and `work_profile_locked` enumerated but not classified.
- Permission dialogs / overlays between observe and act shift indices; only opt-in `expected_hash`
  guards it.
- Rotation / split-screen / cutouts / gesture-nav insets not accounted for in gesture math.

**Companion transport**
- Malformed/partial dropped silently (`Dispatcher.kt:41-45`); `readLine()` unbounded → giant-line
  memory DoS (client `transport.py:33` and server).
- Companion crash/restart: instance nulled on destroy; Python sees hard failure, no fallback.
- Battery optimization killing the foreground service: Python `ping` fails and providers drop
  silently (`README.md:639`) — the agent isn't told the companion vanished mid-task.

**Notifications / OCR**
- Reply targets by `key`; messaging apps reuse keys on update → reply may land in a conversation
  now showing a different message.
- `notifications_dismiss` classified `low` (`risk.py`) — an agent can dismiss a 2FA/alert with no
  gate.

**Risk / locale**
- Keyword signals are English-only (`risk.py:9-31`) → risk signals vanish on localized UIs.
- Macro autonomy gate reads an unpopulated `__snapshot__` (`engine.py:51`) so it is content-blind
  (backstopped by `run_action` re-observing).

---

## Docs vs Implementation Mismatches

1. `intent start` risk — `README.md:263` (high) vs `risk.py:44` (low). (Finding 4)
2. Kill switch scope — `README.md:691` ("blocks all immediately") vs fail-open companion path
   (`audit.py:10-15`), ungated MCP `scroll_until` (Finding 6), on-device STOP not enforced
   (Finding 3).
3. "Loopback only" framed as a security guarantee (`README.md:641-645`) — on Android it is not,
   and there is no auth (Finding 2).
4. Single-writer lock presented as general (`README.md:414`); it is process-local
   (`runtime.py:11`, Finding 11).
5. "All gesture verbs route through run_action" (`README.md:189,375`) — `scroll_until` via MCP
   does not.
6. Password redaction wording — `README.md:1085` says `[redacted]`; companion sets `""`
   (`CompanionAccessibilityService.kt:144`).
7. Redaction coverage (`README.md:499`) omits that alphanumeric secrets are not scrubbed
   (Finding 12).
8. Stale README "Status" section (`README.md:974-978`) says MCP/provider graph/macros/APK are
   "deferred," contradicting the rest of the README and CLAUDE.md.
9. Test count — CLAUDE.md says 579; actual is **586 passed, 1 skipped**.
10. Exit codes (`README.md:526-533`) not honored by intent/clipboard/notification commands
    (Finding 15).
11. `Capabilities.MVP_KEYS` comment (`Capabilities.kt:19-24`) says notification/OCR keys are
    omitted "this cut," but they ship in `ALL_KEYS` with handlers.

---

## Missing Test Plan (prioritized)

**P0 — safety-critical**
- `test_verb_risk_matrix` / `test_intent_start_is_high_risk` (Finding 4).
- `test_mcp_resume_not_agent_accessible`, `test_daemon_resume_not_in_agent_surface` (Finding 1).
- `test_scroll_until_mcp_routes_through_run_action`, `test_scroll_until_halts_on_stop_midloop`
  (Finding 6).
- `test_stop_check_failclosed_when_companion_configured_but_unreachable` (Finding 8).
- Kotlin `dispatcher_refuses_all_actions_when_stopped` (Finding 3).
- Kotlin `server_rejects_unauthenticated_request`, `test_daemon_rejects_rpc_without_token`
  (Finding 2).

**P1 — quoting / privacy**
- Parametrized `test_intent_fields_shell_quoted` (Finding 7).
- `test_clipboard_write_target_is_length_only`, `test_redact_masks_high_entropy_token` (Finding 12).
- Kotlin `observe/screencap/ocr_respect_guarded_app` (Findings 10, 16).
- `test_type_unicode_behavior` (documents the ADB silent-drop trap).

**P2 — provider realism & fallback**
- `test_action_falls_back_when_first_provider_raises`, `test_observe_reports_true_provider`
  (Finding 13). Requires a fake device that models device-side shell re-parsing and runtime
  failure, not just command construction.
- `test_registry_priority_is_documented_order` with a 3-provider stack.
- `test_stale_snapshot_default_behavior` (with/without `expected_hash`).

**P3 — connection & transport**
- `test_reconnect_after_port_rotation`, `test_multiple_devices_requires_serial`,
  `test_unauthorized_state_surfaces_typed_error`.
- `test_transport_rejects_oversized_line`, Kotlin `server_drops_partial_and_giant_lines`.

**P4 — conformance & CLI**
- `test_exit_codes_uniform_across_commands` (Finding 15).
- A **docs-vs-code conformance test** parsing the README command/flag and MCP tool tables,
  asserting every documented flag exists in argparse, every MCP tool in `TOOLS`, and documented
  risk levels match `risk.py`. Would have caught Findings 4, 15, and mismatch #8.

The existing suite is broad but leans on asserting *command construction*; the highest-value
shift is a fake ADB/companion that models real Android semantics (device-side re-parsing,
Unicode limits, tree mutation, provider failure).

---

## Security and Privacy Review (summary)

1. **Unauthenticated local transports** (companion + daemon) — the top issue; loopback is not a
   boundary on Android. (Finding 2)
2. **Kill-switch weaknesses** — not reliably fail-closed and reachable by the agent. (Findings 1, 3, 8)
3. **Content exposure of guarded apps** — protection is action-only. (Findings 10, 16)
4. **Intent argument quoting** — untrusted values reach the device shell unquoted. (Finding 7)
5. **Audit leakage** — clipboard content prefix logged; redaction misses non-numeric secrets.
   (Finding 12)

"No telemetry" is genuinely enforced — no network egress beyond ADB and loopback.

---

## Agent-Safety Review (summary)

- The agent can turn off its own brakes (Findings 1, and daemon `resume`).
- Default `auto` mode + low-risk-by-default verbs mean an autonomous agent acts continuously with
  essentially no gate except critical-content screens and `pm clear` (Findings 4, 5).
- Stale observation → wrong action: tap-by-index and node-id semantic actions are not bound to the
  observation the agent reasoned over unless it opts into `expected_hash` (Finding 9).
- Loops can spiral: `scroll_until` bypasses gating/audit via MCP (Finding 6).
- **"Confirm" is not a human-in-the-loop for an autonomous agent** — it is satisfied by the agent
  passing `yes=True`/`--yes` (`runtime.py:122,158`). The only hard barrier is `deny` (critical),
  which is narrow. There is no policy layer reserving sensitive actions (calls, payments,
  messaging, settings, broadcasts) for genuine human approval distinct from the agent's own
  `--yes`.

Supervised use with `mode: confirm` and a harness where a human (not the agent) supplies
confirmation is defensible. Autonomous use is not until Findings 1–6 are addressed and a real
human-only sensitive-action policy exists.

---

## Improvement Roadmap

### Immediate fixes (before any real AI-agent use)
1. Remove `resume` from agent-facing MCP and daemon surfaces; make resume human-only. (F1)
2. Add `isStopped()` to the companion dispatcher gate — fail closed on-device. (F3)
3. Add token auth to the companion socket and daemon RPC. (F2)
4. Classify `intent_start` high (tel/CALL/sms critical); reconcile README. (F4)
5. Route `scroll_until` through `run_action` on CLI and MCP; re-check STOP inside the loop. (F6)
6. Shell-quote all intent/launch device-shell tokens. (F7)
7. Default `mode` to `confirm`; default sensitive companion caps and unknown keys to disabled. (F5)

### Near-term hardening (before public release)
8. Companion STOP fail-closed when configured-but-unreachable. (F8)
9. Guarded-app checks on observe/screencap/OCR/notification reads; constrain `screencap` paths.
   (F10, F16)
10. Bind actions to observations via generation tokens; refuse ambiguous node-ids. (F9)
11. Fix clipboard audit target; broaden redaction. (F12)
12. Persist CLI idempotency + file lock, or make docs unambiguous and warn. (F11)
13. Truthful `provider` field; uniform exit codes. (F13, F15)
14. Permission-protect `LifecycleReceiver`. (F14)
15. Fix stale README "Status" section and other doc mismatches; add the conformance test.

### Longer-term architecture improvements
16. A real **sensitive-action policy layer** distinct from agent-suppliable `--yes`: categories
    (calls, payments, messaging, settings, package clear, broadcasts, destructive intents)
    requiring out-of-band human approval; an `agent` mode that denies them outright.
17. Runtime provider fallback with explicit surfacing of which provider served each request.
18. Companion liveness heartbeat surfaced to the agent (battery-kill → typed "companion lost").
19. Localize/expand risk detection beyond English; add structured (non-keyword) signals.
20. Replace command-construction-only tests with a realistic fake device.

---

## Final Verdict

Fix first, in order: (1) agent-reachable `resume`, (2) companion on-device STOP gate, (3) token
auth on local transports, (4) `intent_start` risk + default `confirm` mode, (5) `scroll_until`
bypass, (6) intent argument quoting. Those convert the system from "demo-safe" to
"supervised-safe."

Readiness:
1. **Personal manual CLI use — Ready** (set `mode: confirm`; note intent args aren't quoted).
2. **Supervised AI-agent use — Not yet, but close** (remove agent `resume`, default `confirm`,
   don't expose the companion to untrusted local apps).
3. **Autonomous AI-agent use — Not ready** (blocked on Findings 1–6 and a human-only sensitive-
   action policy; current gates are largely self-satisfiable by the agent).
4. **Public release — Not ready** (docs assert guarantees the code doesn't fully deliver — close
   the gaps or correct the claims).

The internal safety *architecture* is better than a first pass suggests — the choke-point holds
across CLI, daemon, and macros. The *trust boundary* is the weak point: three unauthenticated
local control surfaces (companion socket, daemon RPC, MCP `resume`), all reachable by other apps
on the device and all able to clear the kill switch. Authentication on local transports plus
removing agent-reachable `resume` is the single highest-leverage fix, and it is the same fix in
three places.
