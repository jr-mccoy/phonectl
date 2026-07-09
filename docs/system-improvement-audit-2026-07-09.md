# phonectl System Improvement Audit

**Date:** 2026-07-09  
**Goal audited against:** make phonectl maximally useful to an autonomous agent while allowing the agent to handle as much work independently as is safe.  
**Scope:** Python CLI, MCP surface, daemon, macro engine, provider graph, safety policy, Android Accessibility companion APK, docs, and existing audit/roadmap material.

## Executive summary

phonectl has the right core shape for an agent-operated Android control plane: structured observations, a single mutating-action funnel, provider capability discovery, idempotency, a daemon job model, redacted audit records, and a macro runtime. The biggest remaining opportunity is to raise the agent-facing layer from “safe low-level phone tools” to “self-directing task execution”: planners should be able to discover capabilities, choose fallbacks, run long tasks, recover from transient failures, and produce human-readable evidence without requiring constant prompt-level supervision.

The highest-leverage improvements are:

1. Add an **agent task API** above raw tools: goal plans, checkpoints, budgets, evidence, rollback hints, and final reports.
2. Add a **durable selector/app memory layer** that is automatically learned from successful actions and re-used before falling back to coordinates.
3. Make MCP/macros **asynchronous and daemon-backed** by default so long-running work survives client timeouts and reconnects.
4. Expand **self-healing device connectivity** into an agent-callable `recover` workflow with bounded attempts and structured diagnostics.
5. Treat the **Accessibility companion APK as a first-class autonomy provider**: production-hardening, health reporting, and contract parity should be audited alongside Python.
6. Add a **capability-aware planner contract** so the agent can ask “what can I do now, and what setup step unlocks the next capability?”

## Current strengths to preserve

- **Single action choke point.** Mutating actions route through `runtime.run_action`, which checks STOP, confirmation mode, policy, rate limits, idempotency, cross-process locking, post-action auditing, and provider fallback metadata. Keep all future mutating primitives behind this seam.
- **Safe default mode.** `config.get_mode()` defaults to `confirm`, which is appropriate for a tool intended to hand phone control to agents.
- **Structured result envelopes.** The code consistently returns `{ok, data/error, capability, provider}` envelopes, which is essential for autonomous recovery.
- **Provider registry design.** The registry/fallback model is the right foundation for ADB, companion Accessibility, notifications, OCR, Termux:API, and future Shizuku/root transports.
- **Daemon jobs.** `act`, `observe`, `find`, and `macro_run` are already asynchronous jobs in the daemon, which is the right architecture for long-running agent tasks.
- **Human-only resume.** The previous agent-reachable resume surface has been removed from MCP and daemon RPC, preserving the emergency-stop trust boundary.
- **Companion APK safety posture.** The companion source is present in this checkout and already has important hardening: token-authenticated loopback transport, on-device STOP enforcement, sensitive capability defaults off, guarded-app checks, generated node IDs bound to observation generations, and Kotlin/Python contract parity tests. The remaining audit recommendations should build on these rather than treating the APK as future-only.


## Companion Accessibility APK considerations

This audit now explicitly includes the companion APK. The companion is not just an optional implementation detail: for autonomous agents it is the highest-value provider because it can supply native UI trees, Accessibility events, semantic actions, IME-independent text setting, notifications, screenshots, OCR, and a persistent on-device STOP UX.

Current companion strengths observed in this checkout:

- The foreground service wires the Accessibility, notification, and OCR handlers into one loopback NDJSON server, protected by a shared token and live STOP gate.
- The dispatcher refuses unauthenticated non-`ping` requests, enforces STOP for all action/observation methods except `ping` and `handshake`, and logs method/outcome only.
- Sensitive capabilities default off on fresh install (`set_text`, notification reply, OCR, screenshot), while lower-risk observe/gesture/launch/list capabilities remain usable.
- Native-tree node identity is bound to an observation generation so stale `set_text`/semantic actions can fail closed instead of applying to a changed tree.
- JVM contract tests assert the Kotlin native-tree JSON serializes into the Python-compatible XML shape.

Companion-specific gaps to prioritize for autonomy:

1. **Expose companion health through Python/MCP.** The agent should see token paired/unpaired, service running, Accessibility enabled, NotificationListener enabled, current STOP state, disabled sensitive caps, last handshake latency, and last transport error.
2. **Make companion setup unlock paths machine-actionable.** `phone_capability_plan` should distinguish ADB-only, Termux:API, and companion-backed routes, and mark which steps require a human in Android Settings.
3. **Add on-device event streaming to task runs.** Accessibility and notification events should feed the proposed `task_run` checkpoints so agents can wait on events instead of polling screenshots/UI dumps.
4. **Broaden companion test coverage beyond JVM pure contracts.** Keep JVM tests for pure contract logic, but add documented connected-device smoke tests for service start, token pairing, STOP notification/tile behavior, AccessibilityService observe/semantic/set_text, NotificationListener reply/dismiss, and OCR/screenshot toggles.
5. **Surface capability toggles as policy inputs.** If a sensitive companion capability is disabled, the agent should receive a structured `requires_user` setup step rather than repeatedly trying a fallback that cannot satisfy the goal.

## Priority 1 — Add an agent task layer above raw phone tools

### Problem

The MCP surface exposes many useful primitives, but it still asks the agent to orchestrate fragile details: observe, choose a target, act, re-observe, interpret result, retry, capture evidence, and decide whether to stop. Macros help when a procedure is known in advance, but there is no first-class “task run” object for open-ended goals.

### Proposed improvement

Introduce a daemon-backed `task_run` API and matching MCP tools:

- `phone_task_start(goal, constraints, budget, evidence_policy, dry_run=false)`
- `phone_task_poll(task_id)`
- `phone_task_cancel(task_id)`
- `phone_task_report(task_id)`

Each task should contain:

- goal text and explicit success criteria;
- allowed apps/packages and maximum risk level;
- time/action budgets;
- current plan and completed checkpoints;
- observations and redacted evidence snapshots;
- selected actions with policy explanations;
- failure reason and suggested human action.

### Why it helps autonomy

Agents stop treating phonectl as a bag of syscalls and instead get a durable state machine. If the model context resets, the task can resume from daemon state. If a human reviews the run, they see the same checkpoints the agent used.

### Acceptance tests

- Starting a task creates a durable record with budget and constraints.
- Polling a task returns the latest checkpoint without blocking.
- Cancelling a task stops future actions but preserves the report.
- A task that hits a confirmation-required action pauses with a human-readable next step.

## Priority 2 — Learn and reuse durable selectors automatically

### Problem

Element indices are useful in a single observation, but an autonomous agent needs stable affordances across app restarts, screen changes, locales, and provider switches. The roadmap notes that memory capture hooks exist but are not wired into the daemon run-record path.

### Proposed improvement

Wire selector capture into every successful action that targeted an element:

1. On a successful action, record the target selector, matched element metadata, package, app version, locale, provider, and post-action hash.
2. Before using a raw index/coordinate in a later task, query the selector memory for a stable candidate.
3. If selector confidence is ambiguous, return a structured ambiguity error with alternatives rather than guessing.
4. Expose `phone_selector_memory_find`, `phone_selector_memory_update`, and `phone_selector_memory_forget` for agent-visible curation.

### Why it helps autonomy

The agent can build a reusable map of each app instead of rediscovering the UI from scratch every time. This also makes tasks more robust after minor UI shifts.

### Acceptance tests

- A successful indexed tap stores a selector record with package and provider context.
- A later action can resolve the stored selector when the index changes.
- Ambiguous selector matches fail closed with candidate details.
- Sensitive text is redacted in selector memory.

## Priority 3 — Make MCP macro execution daemon-backed and asynchronous

### Problem

The daemon’s `macro_run` path is job-backed, but MCP `phone_macro_run` constructs an in-process engine and blocks until completion. That reintroduces timeout/reconnect fragility for agent clients and bypasses daemon-level job lifecycle benefits.

### Proposed improvement

Change MCP macro execution to submit through the daemon when available, matching CLI behavior:

- return `{job_id, status: accepted}` immediately for long macros;
- provide `phone_job_poll` or macro-specific poll tooling in MCP;
- keep a small in-process fallback only when daemon discovery fails;
- include macro run IDs in task/audit records.

### Why it helps autonomy

Agents can launch multi-minute flows, poll progress, survive MCP client timeouts, and avoid duplicate execution after a transient disconnect.

### Acceptance tests

- MCP `phone_macro_run` returns an accepted job when the daemon is available.
- Polling returns the macro result envelope after completion.
- Reusing the same idempotency key does not run the macro twice.

## Priority 4 — Add an explicit recovery workflow

### Problem

Connection recovery exists in pieces: config stores volatile wireless-debugging ports, daemon discovery scans loopback, diagnostics can produce bundles, and setup guides pairing. But an agent needs one bounded, structured workflow to restore usefulness when the phone is offline, locked, companion-unavailable, or a provider capability disappears.

### Proposed improvement

Add `phonectl recover --json` and MCP `phone_recover`:

1. Check current config, adb server, known serial, and daemon status.
2. Try cached serial reconnect.
3. Scan the configured port range when safe.
4. Restart daemon if enabled.
5. Verify companion handshake if configured.
6. Return a concise status: recovered, partially recovered, needs human pairing, or failed.

### Why it helps autonomy

Instead of surfacing a raw `device offline` error, the agent can attempt a bounded fix and then either continue or ask the human for the exact pairing/action needed.

### Acceptance tests

- Offline cached serial triggers reconnect attempts with a bounded maximum.
- Missing companion token returns `needs_user` with setup instructions.
- Recovery never clears STOP or bypasses confirmation mode.
- The recovery report includes each attempted step and result.

## Priority 5 — Provide a capability-aware setup planner

### Problem

`setup` and `capabilities` report useful facts, but an agent needs to reason from goal to missing capability: e.g. “to read notifications, install/enable companion or Termux:API; to reply, companion NotificationListener is required; to OCR, install tesseract or enable ML Kit.”

### Proposed improvement

Add `phone_capability_plan(goal=None)` and CLI `phonectl capabilities plan` that returns:

- currently available capabilities;
- unavailable capabilities relevant to the goal;
- unlock paths ranked by safety and difficulty;
- commands or human phone settings steps;
- whether each unlock requires physical/human action.

### Why it helps autonomy

The agent can self-serve setup when possible and ask for targeted human assistance when not possible.

### Acceptance tests

- A notification-reading goal maps to `observe_notifications` and lists unlock paths.
- A notification-reply goal distinguishes read-only Termux:API from companion reply support.
- OCR goals list local tesseract and companion OCR alternatives.

## Priority 6 — Add an evidence and reporting layer

### Problem

Audits are machine-readable and redacted, but autonomous agents need a compact final report: what changed, what evidence supports success, and what was intentionally not done.

### Proposed improvement

Add a report builder that can be used by tasks, macros, and CLI:

- before/after app and screen hash;
- selected redacted observations;
- action timeline;
- confirmations requested/received;
- errors and recovery attempts;
- optional screenshot paths when explicitly enabled.

### Why it helps autonomy

A human can trust and review agent work without replaying raw logs. The agent can also use reports as memory for later runs.

### Acceptance tests

- Reports redact typed text and notification bodies by default.
- Reports cite action IDs and audit record hashes.
- Reports can be exported as JSON and Markdown.

## Priority 7 — Tighten macro safety and expressiveness for unattended use

### Problem

The macro engine already gates unattended actions, but macro documents do not yet encode enough operational intent for robust autonomous execution: setup requirements, app/package allowlists, rollback/compensation, evidence expectations, and timeout budgets per phase.

### Proposed improvement

Extend macro schema with optional sections:

```yaml
requires:
  capabilities: [ui.observe, ui.tap]
  packages: [com.example.app]
budget:
  max_actions: 20
  max_seconds: 120
evidence:
  require_final_selector: {text: Done}
on_failure:
  - type: key
    keycode: back
```

### Why it helps autonomy

Macros become safer reusable skills rather than fixed tap scripts. The agent can inspect requirements before running and can stop with a clear reason when the environment is not ready.

### Acceptance tests

- Macro validation rejects unknown required capabilities.
- Runtime stops when max action or time budget is exceeded.
- Failure compensation actions run only when policy permits them.

## Priority 8 — Add provider health scoring and fallback explanations

### Problem

Provider fallback metadata exists after actions, but the agent cannot ask for a health-ranked provider view before choosing a method. This matters when ADB UIAutomator is stale, Accessibility is available, OCR is fallback-only, or the terminal screen causes idle failures.

### Proposed improvement

Add provider health probes:

- freshness/latency of last successful observation;
- current capability set;
- common failure reason;
- recommended provider for goal type;
- fallback chain that will be attempted.

Expose as `phone_provider_health` and include in diagnostics bundles.

### Why it helps autonomy

The agent can pick native tree observation over OCR, know when to press HOME before observing a busy terminal, and explain why a slower fallback was used.

### Acceptance tests

- Health output includes each provider, capabilities, last_ok timestamp, and last_error code.
- A provider failure is visible without throwing away the registry fallback result.
- OCR is marked fallback-only unless explicitly requested.

## Priority 9 — Add an agent-facing dry-run planner

### Problem

`mode: dry-run` simulates an individual action, not a whole task. Agents need to preview a sequence: what actions would be attempted, which would require confirmation, and which capabilities are missing.

### Proposed improvement

Add dry-run planning for macros/tasks:

- validate capabilities and policy before execution;
- estimate risk and confirmation points;
- identify missing setup;
- return an ordered action preview with redacted targets.

### Why it helps autonomy

The agent can ask the human for one approval over a transparent plan rather than repeatedly stopping mid-run.

### Acceptance tests

- Dry-running a macro with a high-risk step returns a confirmation checkpoint.
- Dry-running with missing notification capability returns setup guidance.
- Dry-run never mutates device state or audit action logs as successful actions.

## Priority 10 — Improve documentation freshness and status hygiene

### Problem

The roadmap still contains at least one stale follow-up note saying the companion-side safe defaults are incomplete, while the adversarial review says the companion half is now complete. Stale status is costly for agents because they rely on docs to choose work.

### Proposed improvement

Add a small `docs/status.md` source of truth that lists:

- shipped capabilities;
- known limitations;
- hardware-gated/manual test gaps;
- active top priorities;
- stale/superseded documents.

Update old plan documents to point to `docs/status.md` rather than carrying long-lived status claims inline.

### Why it helps autonomy

Future agents can quickly decide what to build next without re-auditing contradictory historical documents.

### Acceptance tests

- Status doc is linked from README and roadmap.
- Stale roadmap note is replaced by a pointer to the current status.
- CI includes a lightweight check for unresolved contradiction markers if a convention is adopted.

## Suggested implementation order

1. Documentation/status cleanup (cheap, unblocks future agents).
2. MCP job polling + daemon-backed macro run (small code change, high reliability payoff).
3. Recovery workflow (directly improves independent operation).
4. Selector memory wiring (large autonomy payoff, requires careful tests).
5. Companion health/status surfacing in MCP and diagnostics.
6. Task API and reports (new agent-native product layer).
7. Macro schema extensions and dry-run planning.
8. Provider health scoring.

## Open questions

- Should high-level task planning live inside phonectl, or should phonectl expose task state while the LLM remains the planner?
- Should agent-facing MCP tools default to daemon-only mode once daemon autostart is stable?
- What is the minimum human confirmation UX for approving an entire bounded task plan instead of individual actions?
- Which evidence artifacts are acceptable to persist by default on a personal phone, and which require opt-in?
