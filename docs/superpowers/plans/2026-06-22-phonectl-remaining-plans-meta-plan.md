# phonectl Remaining-Plans Meta-Plan

**Date:** 2026-06-22
**Status:** Authoring index for Phases 6 → 7 + cross-cutting evaluation suite (Phases 1–3 complete; all four Phase-4 plans written; Phase-5 spec + both Phase-5 plans now written)
**Reads with:** `docs/superpowers/phonectl-platform-roadmap.md` (the phase model this indexes).

This document is the **instruction set for writing the remaining implementation plans**. **All four
Phase-1 plans, all three Phase-2 plans, and all five Phase-3 plans are written and shipped
(implemented + green); all four Phase-4 plans are now written.** Phase 1: 1.1
(`2026-06-22-phonectl-structured-results-and-capabilities.md`), 1.2
(`2026-06-22-phonectl-selector-and-tree-observation.md`), 1.3
(`2026-06-22-phonectl-resilience-and-connection-recovery.md`), 1.4
(`2026-06-22-phonectl-setup-and-diagnostics.md`). Phase 2: 2.1
(`2026-06-22-phonectl-action-serialization-and-audit-v2.md`), 2.2
(`2026-06-22-phonectl-risk-classifier-and-ledger.md`), 2.3
(`2026-06-22-phonectl-structured-result-mcp-server.md`). Phase 3: 3.1
(`2026-06-21-phonectl-provider-capability-graph.md`), 3.2
(`2026-06-21-phonectl-clipboard-intent-packages.md`), 3.3
(`2026-06-21-phonectl-scroll-and-gestures.md`), 3.4
(`2026-06-21-phonectl-structured-extraction.md`), 3.5
(`2026-06-21-phonectl-termux-api-provider.md`). Phase 4 adopts a clearer plan-file naming
convention — `phonectl-plan-<N.M>-<slug>.md` — so the plan number is obvious when browsing the repo: 4.1
(`phonectl-plan-4.1-accessibility-native-provider.md`), 4.2
(`phonectl-plan-4.2-notification-listener-provider.md`), 4.3
(`phonectl-plan-4.3-foreground-service-transport-and-trust-ux.md`), 4.4
(`phonectl-plan-4.4-ocr-provider.md`). Phase 5 is **spec-first**: the daemon design spec
(`docs/superpowers/specs/2026-06-22-phonectl-daemon-event-runtime-design.md`) was written first, then its
two implementation plans — 5.1 (`phonectl-plan-5.1-daemon-process-and-rpc-api.md`) and 5.2
(`phonectl-plan-5.2-event-bus-and-snapshot-cache.md`). Everything from Phase 6 onward is **scoped here**
and turned into a full TDD plan when its phase begins — one plan document at a time, in roadmap order
(Phase 6, the macro engine, is likewise spec-first).

---

## 0. Implementation tracker (updated 2026-06-22)

Use this table before reading commit history. "Written" means the plan document exists; "Complete" means code/docs/tests have landed and the plan document has an implementation-status note.

| Plan | Plan doc | Implementation status | Landing commit(s) |
|---|---|---|---|
| 1.1 Structured results/errors/capabilities | `2026-06-22-phonectl-structured-results-and-capabilities.md` | ✅ Complete | recorded by `0d994ff` |
| 1.2 Selector + tree observation | `2026-06-22-phonectl-selector-and-tree-observation.md` | ✅ Complete | `20b82a9` |
| 1.3 Resilience + connection recovery | `2026-06-22-phonectl-resilience-and-connection-recovery.md` | ✅ Complete | `d5f2125` |
| 1.4 Setup wizard + diagnostics bundle | `2026-06-22-phonectl-setup-and-diagnostics.md` | ✅ Complete | this branch |
| 2.1 Action serialization + audit v2 | `2026-06-22-phonectl-action-serialization-and-audit-v2.md` | ✅ Complete | `f5415b4` → `b7328c4` |
| 2.2 Risk classifier + risk ledger | `2026-06-22-phonectl-risk-classifier-and-ledger.md` | ✅ Complete | `162744d` → `69b57c8` |
| 2.3 Structured-result MCP server | `2026-06-22-phonectl-structured-result-mcp-server.md` | ✅ Complete | `c0aa779` |
| 3.1 Provider/capability graph refactor | `2026-06-21-phonectl-provider-capability-graph.md` | ✅ Complete | `ed26d1e` → `841c4eb` (PR #15) |
| 3.2 Clipboard + intent/packages providers | `2026-06-21-phonectl-clipboard-intent-packages.md` | ✅ Complete | `0038e3c` → `8ac0d79` (PR #18) |
| 3.3 Scroll-until + gestures | `2026-06-21-phonectl-scroll-and-gestures.md` | ✅ Complete | `a96ca54` → `9ee1eb8` (PR #19) |
| 3.4 Structured extraction APIs | `2026-06-21-phonectl-structured-extraction.md` | ✅ Complete | `c54b614` → `b8292a4` (PR #20) |
| 3.5 Termux:API provider | `2026-06-21-phonectl-termux-api-provider.md` | ✅ Complete | `d69263f` → `193b441` (PR #21) |
| 4.1 AccessibilityService native provider | `phonectl-plan-4.1-accessibility-native-provider.md` | 📝 Written, not yet executed | — |
| 4.2 NotificationListenerService provider | `phonectl-plan-4.2-notification-listener-provider.md` | 📝 Written, not yet executed | — |
| 4.3 Foreground-service transport + trust UX | `phonectl-plan-4.3-foreground-service-transport-and-trust-ux.md` | 📝 Written, not yet executed | — |
| 4.4 Optional OCR provider | `phonectl-plan-4.4-ocr-provider.md` | 📝 Written, not yet executed | — |
| 5.0 Daemon & event runtime design spec | `specs/2026-06-22-phonectl-daemon-event-runtime-design.md` | 📝 Written (spec) | — |
| 5.1 Daemon process + JSON-RPC/socket API | `phonectl-plan-5.1-daemon-process-and-rpc-api.md` | 📝 Written, not yet executed | — |
| 5.2 Event bus + subscriptions + snapshot cache | `phonectl-plan-5.2-event-bus-and-snapshot-cache.md` | 📝 Written, not yet executed | — |

**Next unimplemented written plan:** Phase 4.1 AccessibilityService native provider (`phonectl-plan-4.1-accessibility-native-provider.md`) — Phase 4 executes before Phase 5; the daemon (Phase 5) depends on the Phase-4 event sources.

## 1. Authoring rules (apply to every plan written from this index)

- **File name:** `docs/superpowers/plans/phonectl-plan-<N.M>-<slug>.md`, where `<N.M>` is the roadmap
  plan number (e.g. `phonectl-plan-4.1-accessibility-native-provider.md`). This convention — adopted at
  Phase 4 — leads with the plan number so files sort in roadmap order and each document's scope is
  obvious when browsing the repo. Earlier plans (Phases 1–3) keep their original
  `YYYY-MM-DD-phonectl-<slug>.md` names for traceability; **do not rename them.**
- **Document template** (match `2026-06-22-phonectl-structured-results-and-capabilities.md` exactly):
  1. Title + the `> **For agentic workers:** REQUIRED SUB-SKILL …` banner.
  2. A "Plan N.M of the platform roadmap" line + **Goal / Architecture / Tech Stack**.
  3. **Global Constraints** — restate the roadmap §4 invariants verbatim.
  4. **Shared conventions** used by the plan (config keys, error codes, etc.).
  5. Numbered **Tasks**, each = one commit, each with checkbox steps: *Write failing test → Run to
     confirm fail (name the expected error) → Minimal implementation (show the code) → Run to pass →
     [Run full suite on the last task] → Commit (with the exact message)*.
  6. **Dependencies / Deferred-out-of-scope / Notes on testability** trailers.
- **TDD is non-negotiable:** failing test first, confirm it fails for the right reason, minimal code,
  green, commit. Characterization tests are allowed only for already-shipped branches (note them).
- **Invariants:** stdlib-only runtime; backend isolation; pure `ui_parser`; index/selector/`(x,y)`
  targeting; re-observe after act; injectable seams; `PHONECTL_HOME` isolation; one commit per task; the
  Phase-1 structured-result invariant (every runtime/provider/MCP call returns a `results` envelope).
- **brainstorm → spec → plan** is **required before** any Phase 5 (daemon) or Phase 6 (macro) plan. Write
  the spec doc (`docs/superpowers/specs/…`) first; only then the implementation plan(s).
- **Reuse the canonical seams from Phase 1:** import `errors.py`, `results.py`, `capabilities.py`,
  `backend.Backend` — never redefine them.

## 2. Supersession map (no specified work is lost)

| Superseded 2026-06-21 plan | Re-homed into | Notes |
|---|---|---|
| resilience | **1.1** (errors hierarchy) + **1.3** (all behavior) | errors.py born in 1.1; retry/lock/port-recovery in 1.3 |
| safety-completeness | **2.2** | rate-limit + guarded-package generalized into the risk ledger |
| mcp-server | **2.3** | re-targeted onto the `results` envelope + selector-aware/capability tools |
| setup-wizard | **1.4** | + modular `setup <module>` + diagnostics bundle |
| accessibility-backend | **1.1** (`Backend` Protocol) + **4.1** (native provider) | Protocol seam pulled forward; APK provider in Phase 4 |
| polish | **1.2 / 1.3 / 3.3** + opportunistic | see §4 below |

## 3. Plan stubs (write these in this order)

Each stub gives: **goal**, **files**, **key interfaces**, **dependencies**, **strategy refs**. Expand to
the full template when authoring.

### Phase 1 (finish the foundation) — ✅ all written

**1.3 — Resilience & connection recovery** — ✅ **WRITTEN** as
`docs/superpowers/plans/2026-06-22-phonectl-resilience-and-connection-recovery.md` (9 tasks: re-homes the
superseded resilience plan's Tasks 2–9, drops its Task 1, and adds structured lock-state + the monotonic
`wait_for` and rotation-aware orientation polish folds). Original scope below, retained for traceability.
*Goal:* survive unattended use — auto-wake, retry the `uiautomator` "could not get idle state" dump,
detect the lock screen, rediscover the volatile Wireless-Debugging port, and return **structured
lock-state** (strategy §7.2) rather than only raising.
*Files:* modify `ui_parser.py` (`is_error_dump`, `parse_rotation`, `parse_keyguard`, `parse_mdns_services`
— all pure), `adb_backend.py` (`wake()`, `keyguard()`, `mdns_services()`), `observer.py` (retry/settle
loop + lock guard raising `errors.ObserveError`/`DeviceLockedError` from 1.1; rotation-aware orientation),
`connection.py` (`ensure()` auto-WAKEUP + persisted `last_port`; `rediscover()` layered last-port → mDNS →
bounded port-probe → host-Termux shim seam), `cli.py` (`reconnect` verb; lock-state in `observe --json`).
*Key interfaces:* re-home the old resilience plan's Tasks 2–9 verbatim, but **delete its Task 1** (errors
born in 1.1) and **emit structured lock-state** in the snapshot/envelope (`lock_state`, `can_act`,
`recommended_user_action`).
*Config keys added:* `last_port`, `probe_ports`. *Deps:* 1.1 (errors). *Strategy:* §7, §14.3.
*Folds polish:* monotonic `wait_for` deadline; rotation-aware orientation via `parse_rotation`.

**1.4 — Setup wizard + diagnostics bundle** — ✅ **WRITTEN** as
`docs/superpowers/plans/2026-06-22-phonectl-setup-and-diagnostics.md` (10 tasks: re-homes the superseded
setup-wizard plan's Tasks 1–6 and adds modular `setup <module>` reports + the `redact_config`/`collect`/
`bundle` diagnostics pipeline and `doctor --bundle`). Original scope below, retained for traceability.
*Goal:* interactive onboarding + a support bundle. `phonectl setup` walks Wireless-Debugging
pairing/connect/verify/persist; make it **modular** (`setup adb|accessibility|notifications|termux-api|
all`) where each module reports required permission, current status, how to enable, capabilities
unlocked, and safety implications (strategy §9.1). Add `doctor --json`/`doctor --bundle <zip>` emitting a
redacted diagnostics bundle (§9.3).
*Files:* create `setup.py`, `diagnostics.py`, `tests/test_setup.py`, `tests/test_diagnostics.py`,
`docs/setup-walkthrough.md`; modify `cli.py`, `README.md`.
*Key interfaces:* `run_setup(conn, prompt=input, out=print, which=shutil.which, exists=os.path.exists)`;
`diagnostics.bundle(path)` collecting config (secrets redacted), `adb version`, `adb devices -l`,
get-state, recent errors, mDNS result, host-shim status, **provider capability status** (via
`backend.capabilities()`), last non-sensitive audit entries.
*Deps:* 1.1 (capabilities, results); opportunistic on 1.3 (`reconnect`, gated via `hasattr`).
*Strategy:* §9.

### Phase 2 (single-writer runtime & safety policy) — ✅ all written

**2.1 — Action serialization + request IDs + audit v2** — ✅ **WRITTEN** as
`docs/superpowers/plans/2026-06-22-phonectl-action-serialization-and-audit-v2.md` (9 tasks: additive
single-writer error codes → `runtime.run_action` funnel → process-local lock/`busy` → idempotency keys →
pure `redact` module → audit v2 levels → `audit tail|purge|export` → wire `cli._do_action` → docs). Original
scope below, retained for traceability.
*Goal:* one mutating action at a time, with request IDs, idempotency keys, a clear "busy" status, and an
emergency stop — the single-writer seam the daemon (Phase 5) will own (strategy §7.4, §22). Plus audit v2:
levels (`none|metadata|redacted|full`), broader redaction (OTP/email/phone/card/URL-token/clipboard), and
`audit tail|purge|export --redacted` (§8.3).
*Files:* create `runtime.py` (extract the `cli._do_action` funnel into `run_action(verb, fn, target, *,
yes, cfg, build) -> results-envelope`; add a process-local action lock + `request_id`), `redact.py`; modify
`audit.py` (levels + redaction), `cli.py`, `config.py`.
*Key interfaces:* `run_action(...)` returns the **Phase-1 results envelope** (not a bare tuple); every
action carries `request_id` + optional `idempotency_key`; `audit.log_action` honors `audit_level`. Adds
three additive `errors.py` codes (`busy`/`stopped`/`confirmation_required`) for the single-writer
control-flow.
*Deps:* 1.1 (results/errors). *Strategy:* §7.4, §8.3, §22. *Note:* `run_action` is designed so the daemon
becomes a compatible evolution (single writer), not a rewrite.

**2.2 — Risk classifier / risk ledger** *(supersedes safety-completeness)* — ✅ **WRITTEN** as
`docs/superpowers/plans/2026-06-22-phonectl-risk-classifier-and-ledger.md` (7 tasks: pure `risk` classifier
→ pure `policy` decide/explain → pure `ratelimit` sliding-window + repeated-hash → `run_action` policy gate →
`run_action` rate gate w/ persisted history → audit blocked + `policy explain` verb → docs). Original scope
below, retained for traceability.
*Goal:* replace the flat guarded-package denylist + single rate-limit with a **risk ledger** (strategy
§24) that classifies each action `low|medium|high|critical` from multiple signals (foreground package,
activity, screen text keywords like pay/send/transfer/install/uninstall/factory-reset, password fields,
OTP-like content, intent action, notification category, user lists) and a per-class **policy**
(`allow|confirm|deny`). Add `policy.explain(action)` so an agent reads *why* before acting (§24 closing).
Bucketed rate limits (`tap`/`type`/`launch`/`high_risk`/`global`) + cooldowns + repeated-screen-hash
stop (§8.2).
*Files:* create `risk.py` (pure classifier over a snapshot + action), `ratelimit.py` (pure bucketed
sliding-window), `policy.py` (decision + explain); modify `runtime.run_action` to consult them and raise
`errors.GuardedActionError`/`RateLimitError` (from 1.1) into the results envelope.
*Config keys:* `risk_policy`, `rate_limits`, `guarded_packages` (now one signal among many).
*Deps:* 1.1 (errors/results), 2.1 (`run_action`). *Strategy:* §8, §24.

**2.3 — Structured-result MCP server** *(supersedes mcp-server)* — ✅ **WRITTEN** as
`docs/superpowers/plans/2026-06-22-phonectl-structured-result-mcp-server.md` (6 tasks: observation handlers →
`run_action`-routed action handlers → policy/audit/stop/resume meta tools → `TOOLS` registry + `call_tool` →
gated FastMCP transport + `phonectl mcp` + optional extra → docs). Original scope below, retained for
traceability.
*Goal:* expose phonectl as native agent tools over stdio MCP, returning the **Phase-1 results envelope**
(not CLI tuples), with selector-aware + dry-run + expected-hash tool args (strategy §10, §20, §21), and
capability/policy tools.
*Files:* create `mcp_server.py` (handlers + gated FastMCP adapter); modify `pyproject.toml` (optional
`mcp` extra), `cli.py` (`phonectl mcp`).
*Key interfaces:* re-home the old mcp-server plan's handler/registry/transport design, but each handler
returns `results.ok/err`; add `phone.find(selector)` (with confidence), `phone.observe_ui(tree/relations)`
, `phone_capabilities`, `phone.policy.explain`, `phone.audit.query`, `phone.stop/resume` (§20).
*Deps:* 1.1 (results/capabilities), 1.2 (selectors), 2.1 (`run_action`), 2.2 (policy.explain).
*Strategy:* §10, §20, §21.

### Phase 3 (practical automation primitives — provider graph)

**3.1 — Provider/capability graph refactor** — ✅ **WRITTEN** as
`docs/superpowers/plans/2026-06-21-phonectl-provider-capability-graph.md` (5 tasks: providers package +
`ProviderRegistry` with union capabilities + `_require`/`_last_used` + Backend Protocol delegation +
`__getattr__` ADB helper passthrough → `cli.build_runtime()` returns registry → `runtime.run_action` uses
`last_used` instead of hardcoded `"adb"`). Original scope below, retained for traceability.
*Goal:* move from one active backend to a **composite runtime** that selects the best provider per
capability with graceful degradation, reporting the **provider path** that satisfied each call (strategy
§4.3, §21).
*Files:* create `providers/` (registry + per-capability resolution), `runtime` provider wiring; modify
`backend.Backend` consumers. *Deps:* 1.1 (capabilities/results). *Strategy:* §4.3.

**3.2 — Clipboard + intent/deep-link + app/package providers** — ✅ **WRITTEN** as
`docs/superpowers/plans/2026-06-21-phonectl-clipboard-intent-packages.md` (11 tasks: new capability keys →
`adb_backend` clipboard/intent/packages methods → `HIGH_RISK_VERBS`/`CRITICAL_VERBS` in `risk.py` →
`ClipboardProvider`/`IntentsProvider`/`PackagesProvider` → provider registration → CLI verbs → MCP tools →
docs). Original scope below, retained for traceability.
*Goal:* first-class `clipboard read/write/clear`, `intent start/broadcast` + deep links, and `packages
list/resolve/launch/stop/clear` (strategy §6.4, §6.5) — risk-classified (2.2).
*Files:* create `providers/clipboard.py`, `providers/intents.py`, `providers/packages.py`; CLI/MCP verbs.
*Deps:* 2.2 (risk), 3.1 (graph). *Strategy:* §6.4, §6.5.

**3.3 — Scroll-until + gestures** *(folds polish named-swipe)* — ✅ **WRITTEN** as
`docs/superpowers/plans/2026-06-21-phonectl-scroll-and-gestures.md` (7 tasks: named swipe ADB primitives →
long-press/double-tap/drag/fling ADB → actuator gesture functions → `scroll`/`scroll_until` actuator →
CLI swipe/long-press/double-tap/drag/fling/scroll verbs → MCP tools → docs). Original scope below,
retained for traceability.
*Goal:* `scroll-until --text/--selector`, container-aware scroll (`--within i=`, uses the `scrollable`
metadata from 1.2 Task 1), long-press/double-tap/drag/fling, and **named swipe directions
(up/down/left/right) with density-aware scaling** (the old polish Task 9).
*Files:* modify `actuator.py`, `adb_backend.py` (gesture primitives), `cli.py`. *Deps:* 1.2 (selectors/
metadata). *Strategy:* §6.2, §6.3.

**3.4 — Structured extraction APIs** — ✅ **WRITTEN** as
`docs/superpowers/plans/2026-06-21-phonectl-structured-extraction.md` (7 tasks: `extract_list` →
`extract_form` with password redaction → `get_focused_field` → `find_by_text_regex` →
`get_visible_text_in_region` → CLI verbs → MCP tools). Original scope below, retained for traceability.
*Goal:* read structured data, not just tap — `extract list`, `extract form`, `get focused-field`,
`find --text-regex`, read visible text by region (strategy §5.4).
*Files:* `ui_parser` pure extractors + CLI/MCP. *Deps:* 1.2 (tree/relations). *Strategy:* §5.4.

**3.5 — Termux:API provider (optional)** — ✅ **WRITTEN** as
`docs/superpowers/plans/2026-06-21-phonectl-termux-api-provider.md` (7 tasks: new capability keys →
`TermuxApiProvider` with `is_available()`/`clipboard_read/write`/`battery_status`/`wifi_info`/`tts_speak` →
`build_runtime()` prepends Termux:API if available → CLI `device battery/wifi`/`tts speak` verbs → MCP
tools → docs). Original scope below, retained for traceability.
*Goal:* optional provider for battery/clipboard/sensors/notifications/TTS/etc., **discovered at runtime**
(strategy §13.2, §19), never a hard dependency.
*Files:* `providers/termux.py` (capability-gated). *Deps:* 3.1. *Strategy:* §13.2, §19.

### Phase 4 (companion APK event providers) *(supersedes accessibility-backend)* — ✅ all written

These four plans introduce the companion-APK provider surface. They scope only the **Python-side
provider seam plus an Android design spec** per plan; the Kotlin APK itself is built from those specs
in a separate native effort. All four share one transport seam (`providers/transport.py`, born in 4.1):
a request/response `Transport` Protocol with `request_id` / `timeout` / version / capability negotiation /
stale-response protection, so a provider degrades cleanly when the companion is absent.

**4.1 — AccessibilityService native provider** — ✅ **WRITTEN** as
`docs/superpowers/plans/phonectl-plan-4.1-accessibility-native-provider.md` (8 tasks: `Transport`
Protocol + in-proc fake → capability keys + `AccessibilityProvider` discovery → native JSON tree +
pure `native_tree.to_compat_xml` for uiautomator-compatible `ui_dump` → gesture dispatch +
`ACTION_SET_TEXT` → semantic node actions → UI event polling → `build_runtime` wiring (prepended above
ADB for native observe/gesture) → Android APK design spec + docs). Native JSON tree + UI event stream +
gesture dispatch + `ACTION_SET_TEXT`, satisfying `backend.Backend` and adding event capabilities; Python
seam + an Android APK design spec (`android/`). *Strategy:* §11. *Deps:* 1.1 (Protocol), 3.1 (graph).

**4.2 — NotificationListenerService provider** — ✅ **WRITTEN** as
`docs/superpowers/plans/phonectl-plan-4.2-notification-listener-provider.md` (8 tasks: capability keys +
`NotificationsProvider` over the 4.1 transport with a degraded Termux:API `termux-notification-list`
read path → `list()` → per-notification `can_reply` flags from RemoteInput actions → `wait(predicate,
timeout)` → `reply`/`dismiss` routed through `run_action` (mutating, risk-classified) → CLI
`notifications list|wait|reply|dismiss` → MCP tools → docs). `notifications list/wait/reply/dismiss` with
per-notification reply capability flags (strategy §19, §20.3). *Deps:* 1.1, 2.1 (`run_action`), 2.2
(risk), 3.1 (graph), 4.1 (transport).

**4.3 — Foreground-service transport + emergency-stop + trust UX** — ✅ **WRITTEN** as
`docs/superpowers/plans/phonectl-plan-4.3-foreground-service-transport-and-trust-ux.md` (7 tasks:
low-latency `SocketTransport` (localhost) implementing the 4.1 `Transport` seam → version/capability
handshake → per-capability toggle surface intersected into provider `capabilities()` → emergency-stop
state folded into `audit.kill_switch_active()` → CLI `trust status` + transport preference (socket →
broadcast/file fallback) → Android APK design spec (foreground service + persistent "Stop phonectl"
notification + Quick-Settings tile + per-capability toggle UI) → docs). Low-latency localhost socket/IPC,
persistent "Stop phonectl" notification + Quick-Settings tile, per-capability toggles (strategy §8.4,
§11.3, §11.4). *Deps:* 2.1 (kill switch/stop), 4.1 (transport seam + providers).

**4.4 — Optional OCR provider** — ✅ **WRITTEN** as
`docs/superpowers/plans/phonectl-plan-4.4-ocr-provider.md` (6 tasks: capability key + `OcrProvider`
runtime discovery (`tesseract` on PATH or companion ML-Kit over transport) → pure
`ocr.parse_tsv` regions parser + `ocr_image` → `ocr_screen` via registry `screencap` → `build_runtime`
wiring (optional) + CLI `ocr screen` / `find --ocr-text` → MCP `phone_ocr_screen` → docs).
Tesseract/ML-Kit fallback for screenshots (strategy §13.4). *Deps:* 1.1, 3.1 (graph), 3.4 (region text
consumers); optional on 4.1 (transport for the ML-Kit path).

### Phase 5 (daemon & event runtime) — **spec first** — ✅ all written

The daemon is the north-star core: the **single writer + event broker** for all phone actions, with
CLI/MCP as frontends. Phase 5 was written spec-first (the discipline required for the daemon): the design
spec was authored before either implementation plan. The two plans share a locked contract — loopback
newline-JSON RPC reusing the Plan 4.3 transport framing, `daemon.json` discovery (no static port), the
single writer reusing `runtime.run_action` verbatim, a warm `ProviderRegistry` lifecycle, monotonic
snapshot IDs with stale-index protection, and durable `runs.jsonl` records extending audit v2 — so the
daemon is a **compatible evolution, not a rewrite**, and is never required for v1 primitives.

**Spec 5.0 — daemon brainstorm→spec** — ✅ **WRITTEN** as
`docs/superpowers/specs/2026-06-22-phonectl-daemon-event-runtime-design.md`: single writer + event broker,
snapshot cache + invalidation (monotonic snapshot IDs, foreground checks, stale-index protection),
provider lifecycle, event fanout, one policy choke-point, durable run records (strategy §22). CLI/MCP
become frontends; daemon starts as `phonectl daemon`, later Termux:Boot / companion foreground service
(noted as a seam). Locks the RPC method set, wire protocol, daemon.json discovery, snapshot/event models,
and the run-record schema; hands off to Plans 5.1 and 5.2.

**5.1 — Daemon process + JSON-RPC/socket API** — ✅ **WRITTEN** as
`docs/superpowers/plans/phonectl-plan-5.1-daemon-process-and-rpc-api.md` (9 tasks: additive daemon errors
+ `daemon/` package + `daemon.json` discovery → `rpc.py` method registry → `DaemonServer.handle_line`
single-writer dispatch → warm-once provider lifecycle with `act` via `runtime.run_action` → RPC handlers
(observe/find/capabilities/policy_explain/audit_query/stop/resume/status) → durable `runs.jsonl` records →
`DaemonClient` over `SocketTransport` → frontend auto-routing in `cli.py` → `phonectl daemon` command +
config keys + docs). Loopback-only; tests drive `handle_line` synchronously (no real sockets). *Strategy:*
§22. *Deps:* 2.1 (single-writer `run_action`), 3.1 (providers), 4.3 (transport framing).

**5.2 — Event bus + subscriptions + snapshot cache** — ✅ **WRITTEN** as
`docs/superpowers/plans/phonectl-plan-5.2-event-bus-and-snapshot-cache.md` (8 tasks: `SnapshotCache`
(monotonic `snap_N` ids + foreground accessor) → `observe` mints a `snapshot_id` → stale-index protection
on `act` → snapshot invalidation + `snapshot_before`/`snapshot_after` (backfilling `runs.jsonl`) →
`EventBus` (seq/publish/cursor-poll) → internal action/lifecycle event hooks → step-wise provider
`EventPoller` (drains 4.1 `poll_events` + 4.2 notifications) → `events_poll` RPC + subscription docs).
Cursor-based event contract matches Plan 4.1; no threads/sockets in tests. *Strategy:* §21, §22.
*Deps:* 5.1 (DaemonServer), 4.1 (UI events), 4.2 (notification events).

### Phase 6 (macro runtime & progressive autonomy) — **spec first**

**Spec 6.0 — macro engine spec:** trigger/condition/action schema (strategy §12, §23), signed/auditable
macro YAML, scoped variables, scheduler, policy gates, bounded-backoff retries with high-risk re-check,
cancellation tokens, `run_id` + parent trigger event.
**6.1** macro runtime core (control flow + variables + run records). **6.2** triggers + scheduler + event
subscriptions. **6.3** progressive autonomy (confirm → graduated unattended) + memory/state layer (device
profile, app profiles, selector library, user prefs, failure memory — strategy §25, narrow + user-
controlled). *Deps:* Phase 5 (daemon/events), 2.2 (policy).

### Phase 7 (ecosystem & advanced providers)

**7.1** Shizuku provider (opt-in privilege) · **7.2** optional root provider (strongly separated) ·
**7.3** scrcpy/minicap-inspired low-latency transport · **7.4** Tasker/MacroDroid intent/plugin interop.
*Strategy:* §11.3, §13.

### Cross-cutting — Phase X: evaluation suite

*Goal:* repeatable phone-agent benchmarks + a **fake-provider simulator** so most of the suite runs
without a device (strategy §26, §27#7). Tasks: settings-navigation, form-fill (Unicode), notification-OTP
flow, messaging dry-run (confirm/audit path), list extraction (dedup), recovery drill (kill ADB / rotate /
lock), safety drill (fake payment/password screens → policy blocks). Report success rate, median latency,
action count, stale-target rate, provider-fallback count, human interventions.
*Introduce in Phase 1* (fake-provider simulator + first two benchmarks), grow each phase.
*Files:* `eval/` harness + fixtures + simulator. *Deps:* grows with each capability.

## 4. Polish-item disposition (old polish plan's 10 items)

1. Drop `_adb_bytes` test sentinel → opportunistic in the next plan touching `adb_backend.py` (1.3).
2. `json.dumps(target)` in confirm/dry-run messages → fold into 2.1 (`run_action` rewrite).
3. Drop unused `cfg` param from `_guard_action` → opportunistic in 2.1.
4. Dry-run emits the observed snapshot → already true via the results envelope (1.1/2.1).
5. `parse_rotation` + rotation-aware orientation → **1.3**.
6. `screen_hash` bounds CSV encoding → opportunistic (only if a real bug appears; keep recipe stable for
   1.2's hash-stability guarantee).
7. `wait_for` monotonic deadline → **1.3** (or opportunistic in 1.2 Task 6's actuator touch).
8. Document `id`-kwarg shadow in `wait_for` → fold into 1.2/1.3 docs.
9. Named swipe directions + density scaling → **3.3**.
10. README/spec drift fixes → each plan's docs task already updates README + spec.

## 5. Done-ness for the whole arc

The platform is "north-star complete" when: providers are capability-discovered and degrade gracefully;
the daemon is the single writer with an event bus; macros run with triggers/conditions/actions, scoped
variables, policy gates, and cancellation; progressive autonomy lets a user graduate recipes to
unattended with full audit/inspect/revoke; and the evaluation suite reports stable success rates across
the §26 benchmarks. Each phase ships independently and leaves the suite green.
