# phonectl Platform Roadmap

**Date:** 2026-06-22
**Status:** Active source-of-truth roadmap (supersedes the six 2026-06-21 follow-up plans)
**Scope:** Sequences phonectl's evolution from a shipped observe→act ADB bridge into a fully
autonomous AI harness/toolkit for Android — an "agent operating system for Android" — as a set of
phased, test-first implementation plans.

---

## 1. Why this document exists

phonectl began as an **observe→act→observe ADB bridge** (the shipped core: 9 modules, 45 tests,
validated on a real Samsung Galaxy S25 Ultra). A first wave of six follow-up plans
(resilience, safety-completeness, mcp-server, setup-wizard, accessibility-backend, polish) was written
to harden that bridge.

After those plans were written, a strategy/critique
(`docs/superpowers/phonectl-automation-platform-strategy.md`) reframed the goal: the bridge is the
**primitive layer**, not the product. The product is a local-first **automation platform** an agent can
drive — with selector targeting, a provider/capability graph, structured results, a single-writer
daemon/event runtime, notification/clipboard/intent providers, an AccessibilityService **event**
provider, a Tasker/MacroDroid-style macro engine, a risk ledger, and an evaluation suite.

This roadmap reconciles the two. It **supersedes** the six follow-up plans and re-homes their
still-valuable, fully-specified work into a north-star arc, so no test-first task is lost while the
larger platform takes shape. It is the document to read **before** writing or executing any plan.

**Reading order:**
1. The design spec — `docs/superpowers/specs/2026-06-20-phonectl-adb-bridge-design.md` (foundational
   architecture; still valid).
2. The strategy — `docs/superpowers/phonectl-automation-platform-strategy.md` (the vision being
   operationalized).
3. **This roadmap** (the phase model + plan index).
4. The active plan you are executing (Phase 1.1 and 1.2 are written; the rest are stubbed in the
   meta-plan, `plans/2026-06-22-phonectl-remaining-plans-meta-plan.md`).

## 2. North star

> An agent should not merely ask for `tap(7)`; it should ask for durable capabilities — "open this
> conversation," "collect the latest notification from this app," "wait until a login code arrives,"
> "reply with this text after user confirmation," "restore the previous foreground app." The platform
> executes those goals through low-level ADB, Accessibility, notification, intent, clipboard, and Termux
> providers, but the agent-facing layer is **semantic, observable, cancellable, and policy-checked.**
> *(strategy §18)*

Three product surfaces, one runtime: (1) **low-level computer-use tools** for raw observe/act loops;
(2) **typed Android capability tools** for phone-native jobs; (3) **declarative automations** that run
without the agent polling. The guiding principle is **progressive autonomy** — start in confirm/dry-run,
graduate specific recipes to unattended execution, and inspect or revoke every permission and macro
decision afterward.

## 3. Architecture target

```text
backend/provider capabilities      ← Phase 1, 3, 4 (capability graph)
  ↓
observe/action primitives          ← Phase 0 (done) + Phase 1 (selectors, stale-safe, structured)
  ↓
automation runtime (daemon)        ← Phase 2 (single-writer seam) → Phase 5 (daemon)
  ↓
recipes/macros/agent tools         ← Phase 6 (macro engine)
  ↓
CLI / MCP / local API / optional UI ← Phase 2.3 (structured MCP) + later
```

## 4. Invariants carried forward (must hold across every phase)

These are non-negotiable and are restated in each plan's **Global Constraints**:

- **Stdlib-only runtime** (Python ≥ 3.9; `pytest` dev-only). A new runtime dep needs an explicit reason
  and an optional extra (as the MCP SDK does), never a hard core dependency.
- **Backend isolation.** Only `adb_backend.py` (and later `a11y_backend.py`, Termux/Shizuku providers)
  may touch `subprocess`/`adb`. Everything else speaks a backend-agnostic interface.
- **`ui_parser.py` stays pure.** XML/text → data, no I/O, no subprocess, no sleep. All parsing edge cases
  are fixture-tested here.
- **Element index `i` is a primary target; selectors are the durable target; raw `(x,y)` is the escape
  hatch.** (Phase 1.2 adds selectors alongside indices.)
- **Every `act()` re-observes** and returns the post-action snapshot; the screen-hash change is how the
  loop knows the action landed.
- **Injectable seams** (`runner`, `sleep`, `prompt`, `build`) — no real I/O or wall-clock waits in unit
  tests; isolate config/audit/kill-switch via `monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))`.
- **Modes + kill-switch gate every mutating action** through one funnel.
- **Every action is audited.** (Phase 2.1 adds audit levels/redaction v2.)
- **One commit per task. TDD order is non-negotiable:** write the failing test, run it to confirm it
  fails for the right reason, then write the minimum code to pass.

**New invariant added in Phase 1:** every runtime/provider/MCP call returns a **structured result**
(`{ok, …, error{code,message,retryable,requires_user,user_action}}`) — never a bare CLI tuple or a raw
traceback. Capability-unavailable, device-locked, stale-snapshot, and policy-blocked outcomes are
distinguishable and actionable (strategy §10.1, §21).

## 5. Phase model

Each phase is a set of plans. **★** marks the two plans written in full this iteration; everything else
is scoped in the meta-plan and written when its phase begins.

| Phase | Theme | Plans | Re-homes / draws from |
|---|---|---|---|
| **0** | Observe→act core | *(done — shipped, 45 tests, real-device validated)* | `2026-06-20-phonectl-observe-act-core.md` |
| **1** | Platform-ready foundation (the seams) | ★**1.1** Structured results, errors & capabilities · ★**1.2** Selector + tree observation · **1.3** Resilience & connection recovery · **1.4** Setup wizard + diagnostics bundle | old *resilience*, *setup-wizard*, *polish*; strategy §4.2, §5, §7, §9, §10.1, §21, §27#1–2 |
| **2** | Single-writer runtime & safety policy | **2.1** Action serialization + request IDs + audit v2 · **2.2** Risk classifier / risk ledger · **2.3** Structured-result MCP server | old *safety-completeness*, *mcp-server*; strategy §7.4, §8, §10, §20, §22, §24 |
| **3** | Practical automation primitives (provider graph) | **3.1** Provider/capability graph refactor · **3.2** Clipboard + intent + app/package providers · **3.3** Scroll-until + gestures · **3.4** Structured extraction APIs · **3.5** Termux:API provider | old *polish* (named swipe); strategy §4.3, §5.4, §6, §13.2 |
| ✅ **4** | Companion APK event providers | **4.1** AccessibilityService native tree/events/gestures/set-text · **4.2** NotificationListenerService provider · **4.3** Foreground-service transport + emergency-stop + trust UX · **4.4** Optional OCR provider · **native APK** ✅ Plans 4.5–4.8 (Kotlin `com.phonectl.companion`: scaffold/transport/trust, notifications, ML-Kit OCR, hardening) — CI debug-APK artifact; on-device smoke matrix (4.8 Task 3) ⏳ NOT RUN (needs hardware) | old *accessibility-backend*; strategy §8.4, §11, §19 |
| **5** | Daemon & event runtime (north-star core) | **Spec 5.0** daemon brainstorm→spec · **5.1** daemon process + JSON-RPC/socket API · **5.2** event bus + subscriptions + snapshot cache | strategy §22 (currently-unplanned daemon) |
| ✅ **6** | Macro runtime & progressive autonomy | **Spec 6.0** macro engine spec · ✅ **6.1** macro runtime core (done) · ✅ **6.2** triggers + scheduler + macro_enable/disable/list (done) · ✅ **6.3** progressive autonomy + memory/state layer (done) | strategy §12, §18, §23, §25 |
| **7** | Ecosystem & advanced providers | **7.1** Shizuku · **7.2** optional root · **7.3** scrcpy/minicap low-latency transport · **7.4** Tasker/MacroDroid interop | strategy §11.3, §13 |
| **X** | Cross-cutting: evaluation suite | Eval harness + fake-provider simulator (introduced in Phase 1, grows each phase) | strategy §26, §27#7 |

### 5.1 Phase ordering rationale

- **Phase 1 adds the seams now so later phases don't require rewrites** (strategy §27 closing guidance:
  "Add small seams now: capability discovery, selectors, structured errors, request IDs, audit fields").
- **Within Phase 1 we reverse strategy §27's listed order** (it lists selectors before capabilities):
  Plan **1.1** (errors/results/capabilities) lands first because the typed-error + structured-result
  envelope is what Plan **1.2**'s stale-snapshot protection and every later capability-unavailable path
  depend on. Both remain "seams added now."
- **Phase 2 introduces the single-writer seam early** (`run_action` funnel + request IDs) even before the
  daemon exists, so the daemon (Phase 5) is a compatible evolution, not a rewrite (strategy §22 final
  paragraph).
- **The companion APK (Phase 4) is deferred behind pure-Python providers (Phase 3)** so the platform is
  useful on day one over ADB alone, and Accessibility/Notifications become *additive event surfaces*.
- **Daemon and macro engine require a brainstorm→spec before their plans** (Specs 5.0 and 6.0). They are
  the north-star core and must not be bolted onto ad-hoc commands.
- **The evaluation suite is cross-cutting**, seeded in Phase 1 with a fake-provider simulator and grown
  with each new capability (strategy §26).

## 6. Supersession map

The six 2026-06-21 follow-up plans are **superseded** by this roadmap and moved to `plans/archive/`.
Their fully-specified tasks are re-homed (no work lost); the meta-plan carries the task-level mapping.

| Superseded plan | Re-homed into |
|---|---|
| `2026-06-21-phonectl-resilience.md` | Plan **1.3** (errors hierarchy split to **1.1**) |
| `2026-06-21-phonectl-safety-completeness.md` | Plan **2.2** (risk ledger generalizes rate-limit + guarded-package) |
| `2026-06-21-phonectl-mcp-server.md` | Plan **2.3** (re-targeted onto the structured-result envelope) |
| `2026-06-21-phonectl-setup-wizard.md` | Plan **1.4** |
| `2026-06-21-phonectl-accessibility-backend.md` | Plan **4.1** (`Backend` Protocol seam pulled forward to **1.1**) |
| `2026-06-21-phonectl-polish.md` | Distributed: named swipe → **3.3**; monotonic `wait_for` + rotation-aware orientation → **1.2/1.3**; remaining cleanups → opportunistic within the touching plan |

## 7. Coverage check against the strategy

This roadmap covers all 14 of the strategy's §16 highest-priority backlog items and all 7 of its §27
planning artifacts:

- §16 #1 selectors → 1.2 · #2 hierarchy/metadata → 1.2 · #3 capability discovery → 1.1 · #4 structured
  errors → 1.1 · #5 stale-snapshot protection → 1.2 · #6 action serialization + request IDs → 2.1 ·
  #7 risk classification → 2.2 · #8 clipboard → 3.2 · #9 intents → 3.2 · #10 notification listener → 4.2 ·
  #11 Accessibility as event provider → 4.1 · #12 diagnostics bundles → 1.4 · #13 daemon → 5 · #14 macro
  model → 6.
- §27 #1 selector-and-tree → 1.2 · #2 provider-capabilities → 1.1 · #3 daemon-runtime → Spec 5.0/5.x ·
  #4 notification-provider → 4.2 · #5 accessibility-companion → 4.1 · #6 macro-engine → Spec 6.0/6.x ·
  #7 evaluation-suite → Phase X.

## 8. Status legend used in plan docs

`[ ]` task step pending · one commit per task · each task ends green (`pytest -v`) · manual/device-gated
steps are explicitly flagged and never run in CI.

## 9. Phase 6 deferred notes

- **Capture hooks not yet wired into the daemon run-record path.** `memory.capture_from_runs` (and
  `capture_selector` / `capture_failure`) are implemented and tested but not yet wired into the daemon
  run-record path; activation requires action-record enrichment (selector `matched_i` + `app_version`/`locale`
  context) and is deferred with the selector-library override work (Phase 7+).

## 10. Companion setup (`phonectl companion setup`) — implemented

✅ Done: `docs/superpowers/plans/2026-07-02-phonectl-companion-setup.md` (design spec:
`docs/superpowers/specs/2026-07-02-phonectl-companion-startup-design.md`). `phonectl companion
setup` is the idempotent one-command bring-up for the Phase 4 companion APK — install → enable
AccessibilityService → grant `POST_NOTIFICATIONS` → acquire the pairing token → start the socket
server → verify with an authenticated handshake — gated by `--yes`/y-N confirmation on the
grant/start steps. `phonectl companion status` and `phonectl config get`/`config set` shipped
alongside it. See the README's "Companion setup" section for usage.

Follow-ups intentionally left out of scope (each needs its own plan):

1. **ADB Wireless-Debugging port-rotation reconnect.** Port rotation, dead mDNS, and reconnect
   handling is a separate connection-layer concern. Candidate directions: one-time USB
   `adb tcpip <fixed-port>`, persistent `rediscover()` on every command, or a dedicated `phonectl
   reconnect` flow. `companion setup` assumes a live adb connection and only surfaces `device
   offline` today.
2. **Approach A — phonectl-minted pushed token (v2).** Release-build token automation via a
   phonectl-minted secret pushed at first pair (replacing `adb run-as` / manual paste for
   non-debug builds). Requires an APK/Kotlin change (a TOFU first-pair path) — a natural v2 once
   an Android build loop exists.
3. **Kotlin Finding-5 gap.** `Capabilities.DEFAULT_ENABLED = true` was never flipped in the
   companion app — it still ships **all** capabilities enabled by default. The Python-side half of
   Finding 5 (safe-by-default gating) landed; the companion-side default does not yet match it.
   Independent of companion setup — file as its own remediation.
