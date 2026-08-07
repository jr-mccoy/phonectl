# phonectl — macro runtime & progressive autonomy (declarative automations)

**Date:** 2026-06-22
**Status:** Design spec (Phase 6 brainstorm→spec). Required before Plans 6.1, 6.2, and 6.3.
**Author:** Jeremy McCoy (with Claude)

**Reads with:**

- `docs/roadmap.md` — **§5** (Phase 6 row) and **§5.1** ordering rationale
  (the macro engine *consumes* the Phase 5 daemon's event bus + run records; it does not invent a second
  runtime, and the daemon/macro engine must not be bolted onto ad-hoc commands).
- `docs/strategy.md` — **§12** (Tasker/MacroDroid-inspired
  trigger/condition/action taxonomy + control flow + macro schema example), **§18** (progressive
  autonomy), **§23** (macro/runtime design sketch: `run_id`, scoped variables, idempotency, bounded
  backoff with high-risk re-check, foreground approval), **§24** (risk ledger the policy gate reuses),
  **§25** (memory/state/knowledge layer — narrow + user-controlled).
- `docs/design/2026-06-22-phonectl-daemon-event-runtime-design.md` — the daemon spec this builds
  on: the **single-writer `run_action` choke-point**, the **event bus** (`events_poll` cursor contract),
  **snapshot IDs**, and **`runs.jsonl`** run-record lineage (`action_id`/`parent_task_id`).
- `docs/architecture.md` — the load-bearing invariants this design must not break
  (backend isolation, the `run_action` choke-point, re-observe after every act).
- The runtime surfaces a macro builds on: `runtime.run_action` (funnel + audit v2),
  `risk`/`policy`/`ratelimit`, `ProviderRegistry` + `cli.build_runtime`, the action +
  capability surface a macro's action steps map onto, and the daemon (event bus, snapshot
  cache, run records).

This is a **design document** — goals, non-goals, architecture, locked decisions with rationale, schemas,
risks, and a handoff to the implementation plans. It contains **no TDD tasks**; those live in Plans 6.1,
6.2, and 6.3.

---

## 1. Goal

Turn phonectl's primitives into **declarative automations that run without the agent polling.** Today an
agent (or a human at the CLI/MCP) drives every step of an observe→act loop. A macro is a **signed,
auditable plan** (strategy §23) that the platform executes on its own: a trigger fires, conditions are
checked, a sequence of actions runs through the *existing* single-writer funnel, every step is audited and
joined to a durable run record, and high-risk steps stop for confirmation unless the user has explicitly
graduated the recipe to unattended. Concretely the macro engine delivers:

- **A declarative macro document** (trigger / conditions / actions / control flow / permissions / limits)
  that is **parsed and validated, never executed as code** — the same purity discipline as `ui_parser`.
- **A control-flow executor** (sequence, if/else, switch, for-each, loop, retry-with-backoff, timeout,
  try/finally, wait-until, race, cancellation) with **scoped variables** (trigger / macro / secret /
  runtime) and `${var}` interpolation.
- **Action steps that route through `runtime.run_action`** — so every macro action inherits the
  single-writer lock, mode/kill-switch gate, risk policy, rate limits, and audit, with **zero** new
  execution path.
- **Triggers + a scheduler** that fire macros from the Phase 5.2 **event bus** (notification posted, UI
  element appears, app opened, clipboard changed, …) and from **time/schedule** rules — without busy
  polling.
- **Progressive autonomy:** a recipe starts in confirm/dry-run and is **graduated** to unattended per an
  explicit, recorded, revocable user **grant**; crossing a risk boundary without a grant requires
  foreground approval.
- **A narrow, user-controlled memory layer** (device profile, app profiles, selector library, user
  preferences, failure memory) that makes recipes more robust over time — operational metadata only,
  aggressively redacted, trivially exportable and deletable.

The macro engine is the layer that finally answers the north star (roadmap §2): the agent asks for durable
capabilities ("reply to a priority message after confirmation," "collect the latest notification from this
app"), and the platform runs them as observable, cancellable, policy-checked recipes.

## 2. Non-goals

- **No new action execution path.** Every macro action step executes through `runtime.run_action`
  verbatim. The engine orchestrates; it never re-implements tap/type/launch or the guardrails around them
  (strategy §22, §24).
- **No new event source or transport.** Triggers consume the Phase 5.2 event bus via the existing
  `events_poll(since, max)` cursor contract; the scheduler adds *time* fires only. The engine invents no
  wire protocol and binds no socket.
- **No arbitrary code execution.** A macro is declarative data (JSON; YAML behind an optional extra at the
  I/O edge only). There is no `eval`, no embedded Python, no shell step that bypasses providers/policy.
  An `http`/`webhook` action is a *deferred* capability, gated like any network-leaving action (§9, open
  questions).
- **No Android/Kotlin in this phase.** Macros run inside the Python daemon (or in-process). Hosting the
  scheduler in a companion foreground service / Termux:Boot is a noted **seam only** (reusing the daemon
  spec's D10), no native code is built here.
- **No unrestricted memory / personal-data hoarding.** The memory layer (§8) stores **operational
  metadata only** by default, redacts text aggressively, and makes export/delete easy (strategy §25). It
  is not a contacts/messages cache.
- **No macro engine required for v1 primitives.** Everything below is additive; the existing CLI/MCP/daemon
  surface is unchanged when no macro is defined, and the full suite stays green.

## 3. Architecture

### 3.1 Where the macro engine sits

```text
backend/provider capabilities        (Phase 1, 3, 4)
  ↓
observe/action primitives            (Phase 0 + 1)
  ↓
single-writer funnel  run_action()   (Phase 2.1 — reused verbatim by every macro action step)
  ↓
DAEMON  (Phase 5)  ── single writer · snapshot cache · EVENT BUS · run records (runs.jsonl)
  ↓ events_poll cursor              ↑ run_action            ↑ append run records
MACRO ENGINE (Phase 6) ── schema · variables · control-flow executor · triggers · scheduler · autonomy · memory
  ↑ macro_* RPC / CLI / MCP
agent (creates/validates/runs/cancels macros)        user (grants/revokes autonomy, inspects/exports memory)
```

The engine is a **consumer of the daemon**: it subscribes to the event bus to fire triggers, executes
action steps through `run_action`, and writes macro-run lineage into `runs.jsonl`. With no daemon present,
a macro runs **in-process** against `cli.build_runtime` exactly as a one-shot CLI action does today —
daemonization stays a compatible evolution.

### 3.2 The three layers (mapped to the three plans)

- **Plan 6.1 — runtime core.** The pure `schema` (parse/validate), `variables` (scoped resolution +
  interpolation), and `engine` (control-flow executor that dispatches action steps through `run_action`)
  plus the `macro_validate/run/cancel/status` surface and macro-run records. No triggers yet — a macro is
  run *explicitly* (`phonectl macro run <doc>`), which is fully testable without the event bus.
- **Plan 6.2 — triggers + scheduler.** The pure `triggers` (match an event envelope/snapshot against a
  trigger spec), `conditions` (evaluate a condition list against device/snapshot/variable state), and
  `scheduler` (compute next-fire for time/schedule triggers), wired into a daemon **TriggerManager** that
  drains the event bus and fires matching macros — with per-macro `max_runs_per_hour`/`cooldown` limits.
- **Plan 6.3 — progressive autonomy + memory.** The `autonomy` ledger (grant/revoke + a pure decision that
  graduates a recipe from confirm→unattended) wired into the engine's per-action gate, and the narrow,
  redacted, user-controlled `memory` layer (device/app profiles, selector library, user prefs, failure
  memory) with capture hooks and export/delete.

### 3.3 What a macro run *is*

A run is a daemon (or in-process) execution of one macro document with:

- a `run_id` and a **parent trigger event** (or `manual` when run explicitly),
- a **policy decision** recorded up front and re-checked before each risk boundary,
- a **cancellation token** checked cooperatively between steps (plus the global kill-switch),
- four **variable scopes** (trigger / macro / secret / runtime outputs),
- one **`runs.jsonl` record per action step**, all sharing the run's `parent_task_id = run_id` so the
  action lineage joins back to the macro (daemon spec §9).

## 4. Locked decisions (with rationale)

These twelve decisions are the contract the 6.1/6.2/6.3 plan authors share verbatim.

### D1 — A macro is declarative data, parsed by a pure module — never executed as code

`macro/schema.py` is **pure** (the `ui_parser` discipline): `dict → typed Macro` plus a list of validation
errors; no I/O, no `subprocess`, no `eval`. The executor interprets that typed structure. There is no
embedded Python, no shell step.
**Rationale.** A "signed, auditable plan, not an opaque script" (strategy §23) is only auditable if it is
inert data. Purity also makes the whole schema fixture-testable.
**Trade-off.** Expressiveness is bounded by the declared action/condition/control-flow vocabulary; new
behavior means a new typed step, not arbitrary code. This is the safety property, not a limitation to fix.

### D2 — Canonical macro format is JSON; YAML is an optional `[yaml]` extra at the I/O edge only

The pure parser consumes an already-parsed `dict`. The thin file loader reads **JSON** with stdlib `json`;
`.yaml`/`.yml` is supported **only** when the optional `[yaml]` extra (PyYAML) is installed, and the YAML
text is converted to a `dict` at the loader boundary — never inside the pure parser.
**Rationale.** Stdlib-only-runtime invariant (roadmap §4). The strategy writes examples in YAML for
readability, but YAML is not stdlib; gating it as an optional extra (the same pattern as the MCP SDK in
2.3) keeps the core pure and dependency-free while still accepting the documented format.
**Trade-off.** Authors without the extra write JSON. Acceptable; the schema is identical either way.

### D3 — Every action step executes through `runtime.run_action` (no fork)

Each macro action maps to the **same** `(verb, fn, target)` triple the CLI/MCP already pass to
`run_action`. The engine never calls `actuator`/providers directly for a mutating step.
**Rationale.** `run_action` is the single writer + the one place mode/kill-switch/risk/rate-limit/audit
live (Plan 2.1/2.2). Reusing it verbatim means a macro can never bypass a guardrail, and the daemon's
single-writer lock serializes macro steps against ad-hoc agent actions automatically.
**Trade-off.** A macro step gets a structured `busy` envelope if another writer holds the lock; the engine
treats `busy` as a retryable step outcome (bounded backoff, D7), not a crash.

### D4 — Triggers consume the Phase 5.2 event bus via the existing cursor poll

Event-driven triggers are matched against the `{"seq","type","ts","source","data"}` envelopes drained from
`events_poll(since, max)`. The macro engine adds **no** new event source; `ui_changed` /
`notification_posted` / etc. already flow from the 4.1/4.2 providers into the bus (daemon spec §8).
**Rationale.** One nervous system. Reusing the bus means triggers, MCP observability, and logs all see the
same monotonic-seq stream, and a far-behind TriggerManager detects a cursor gap rather than silently
missing fires.
**Trade-off.** Trigger latency is poll latency (bounded by the daemon's poller interval); acceptable for
the targeted automations and explicitly the same floor as agent event consumption (daemon open question §2).

### D5 — Time/schedule triggers use a stdlib monotonic scheduler — no cron dependency

`macro/scheduler.py` exposes a **pure** `next_fire(spec, *, now)` (time-of-day windows, intervals,
weekday masks) and the daemon runs a single scheduler thread that sleeps to the nearest next-fire using a
**monotonic** clock (the Plan 1.3 `wait_for` discipline), then enqueues the macro.
**Rationale.** Stdlib-only; deterministic and fully unit-testable via an injected clock. Cron syntax can be
added later as a parser over the same `next_fire`.
**Trade-off.** Sub-second scheduling and wall-clock DST corner cases are out of scope; documented.

### D6 — Four variable scopes, secrets redacted everywhere

Scopes resolve in order **runtime → macro → trigger → secret** for reads; writes target an explicit scope
(default `runtime`). `secret`-scope values are **never** written to `runs.jsonl`/`actions.jsonl`/logs — they
pass through `redact.py` (Plan 2.1) and render as `***`. `${var}` interpolation is pure string
substitution against the merged scope view.
**Rationale.** Strategy §23 requires scoped variables and secret variables; the redaction story is already
built (2.1) and must extend to macro variables so a recipe can hold an OTP/token without leaking it.
**Trade-off.** No nested object paths in v1 (`${a.b}`); flat names only, documented.

### D7 — Retries use bounded backoff and re-check policy before replaying a high-risk action

A `retry` control step (and an action step's `retry:` field) retries on **retryable** error envelopes only
(`busy`, `rate_limited`, `observe_failed`, `stale_snapshot`), with bounded backoff. Before **replaying** a
step the engine re-classified as **high/critical** risk, it **re-runs the policy gate** — a retry must not
silently replay a high-risk action whose context changed (strategy §23).
**Rationale.** Idempotency is "where possible" (strategy §23); high-risk replay is the dangerous case and
must re-pass policy + autonomy, not assume the first grant still holds.
**Trade-off.** Some retryable-but-non-idempotent actions are conservatively not retried; the schema lets an
author mark a step `idempotent: false` to suppress retry entirely.

### D8 — Cooperative cancellation token + the global kill-switch

Each run carries a `CancellationToken`. The executor checks it **between steps** and at each backoff sleep;
`macro_cancel(run_id)` flips it. The global STOP sentinel (`audit.kill_switch_active`) halts macro action
steps for free, because they go through `run_action` (D3).
**Rationale.** Strategy §12.4/§23 require cancellation; cooperative checks keep it stdlib-simple and
deterministic in tests (no thread kill). The kill-switch already covers the emergency case.
**Trade-off.** A step already in-flight inside `run_action` finishes before cancellation is observed; the
token catches the *next* step. Long single steps are bounded by their own `timeout:`.

### D9 — Macro-run lineage extends `runs.jsonl`; a `MacroRun` summary record is added

Every action step a macro runs sets `parent_task_id = run_id` on its `runs.jsonl` record (daemon spec §9),
so action lineage joins to the macro. The engine additionally appends one **`MacroRun` summary** record per
run (`run_id`, `macro_name`, `trigger`/`parent_event_seq`, `policy_decision`, `outcome`, `steps_run`,
`started_at`/`ended_at`, `cancelled`) to `runs.jsonl` (a `kind` field distinguishes action vs macro-run
records). `actions.jsonl` (audit v2) is unchanged.
**Rationale.** This reuses the daemon's run-ledger discipline (one append-only file, `PHONECTL_HOME`
isolation) and gives the eventual UI/agent a single place to ask "what did this macro do."
**Trade-off.** Two record kinds in one file; the `kind` discriminator + the `read(kind=…)` helper keep
readers simple.

### D10 — One policy choke-point per action, plus an autonomy gate above it

Before each action step the engine calls `policy.explain` (Plan 2.2) to get `risk_level`/`decision`; the
**autonomy gate** (D11) then maps that to `allow | confirm | deny` given the macro's `permissions`/`policy`
block and the user's standing grants. `run_action` *still* enforces mode/policy independently — the engine
gate is an **additional** guard above the funnel, never a replacement that could weaken it.
**Rationale.** Defense in depth: a macro cannot grant itself more than `run_action` already permits; it can
only *further restrict* or require confirmation. Strategy §24's `policy.explain` is read before acting to
discourage blind retries.
**Trade-off.** Two evaluations (engine gate + funnel) per action; cheap and pure.

### D11 — Progressive autonomy = an explicit, recorded, revocable grant ledger

A recipe runs in **confirm** by default. The user graduates it with a **grant**
(`autonomy.grant(macro, scope, max_risk, expires?)`) recorded append-only in `autonomy.jsonl`. The pure
`autonomy.decide(macro, action_risk, grants, *, now)` returns `allow | confirm | deny`: it `allow`s
unattended only when a live grant covers the macro at ≥ the action's risk level; otherwise `confirm`
(foreground approval) or `deny` for critical without explicit one-time approval. Grants are revocable
(`autonomy.revoke`) and inspectable (`autonomy.list`).
**Rationale.** Strategy §18/§2: "start in confirm/dry-run, graduate specific recipes to unattended, and
inspect or revoke every permission and macro decision afterward." An append-only ledger gives the audit +
revoke story for free.
**Trade-off.** Confirmation in an unattended context (no human present) blocks the run with a structured
`confirmation_required` envelope rather than proceeding — the safe default.

### D12 — Memory is narrow, operational-only, redacted, and user-exportable/deletable

`memory.py` persists JSON under `$PHONECTL_HOME/memory/` with five typed stores: **device profile**, **app
profiles**, **user preferences**, **selector library**, **failure memory** (strategy §25). Capture hooks
record *operational metadata only* (which selector resolved, which command was flaky, reconnect counts) —
**all text passes through `redact.py`**. `memory export`/`memory delete` dump/clear the stores; nothing is
stored that the user cannot see and remove.
**Rationale.** "Durable memory, but narrow and user-controlled" (strategy §25). Reusing `redact.py` keeps
one redaction policy across audit, run records, and memory.
**Trade-off.** The selector library improves robustness but can go stale across app updates; entries are
keyed by app version + locale so a stale entry self-invalidates rather than mis-targeting.

## 5. Macro document schema

A macro is a JSON object (YAML accepted via the optional extra, D2). Top-level keys:

| Key | Type | Meaning |
|---|---|---|
| `name` | string (required) | Stable macro identifier. |
| `version` | int (default 1) | Macro-document schema version. |
| `permissions` | object | Capability grants the macro requests, e.g. `{"notifications.reply": ["com.whatsapp"], "ui.act": "confirm"}` (strategy §23). |
| `trigger` | object | Trigger spec (6.2). Absent ⇒ manual-only (`phonectl macro run`). |
| `conditions` | list | Condition specs (6.2); all must hold for the run to proceed. |
| `variables` | object | Initial `macro`-scope variables. |
| `actions` | list | Ordered action/control-flow steps (6.1). |
| `policy` | object | `{ "require_confirm": bool, "max_risk": "low|medium|high|critical" }`. |
| `limits` | object | `{ "max_runs_per_hour": int, "cooldown_seconds": int }` (6.2). |

**Action step shapes (6.1):**

```jsonc
{ "type": "tap", "target": { "selector": { "resource_id": "…:id/send" } } }
{ "type": "set_text", "target": { "i": 14 }, "text": "${reply}" }
{ "type": "launch", "package": "com.example" }
{ "type": "wait", "seconds": 2 }                                   // or { "until": <condition> , "timeout": 10 }
{ "type": "set", "var": "reply", "value": "On my way" }            // scope defaults to runtime
{ "type": "if", "condition": <cond>, "then": [ …steps ], "else": [ …steps ] }
{ "type": "switch", "on": "${state}", "cases": { "a": [ … ] }, "default": [ … ] }
{ "type": "for_each", "in": "${rows}", "as": "row", "do": [ … ] }
{ "type": "loop", "while": <cond>, "do": [ … ], "max_iterations": 50 }
{ "type": "retry", "do": [ … ], "max_attempts": 3, "backoff_seconds": 1.0 }
{ "type": "race", "any": [ <cond>, … ], "timeout": 15 }
{ "type": "try", "do": [ … ], "finally": [ … ] }
{ "type": "confirm", "message": "Reply to ${sender}? ${summary}" } // user approval gate
{ "type": "stop" }                                                // end the macro
{ "type": "audit_note", "text": "…" }
```

Action steps whose `type` is a phone verb (`tap`, `set_text`/`type`, `swipe`, `scroll_until`, `launch`,
`key`, `intent`, `clipboard_*`, `notification_*`, …) map to the **existing** `(verb, fn, target)` triple and
execute through `run_action` (D3). Control-flow steps (`if`/`switch`/`for_each`/`loop`/`retry`/`race`/`try`/
`wait`/`set`/`confirm`/`stop`/`audit_note`) are interpreted by the engine and take no writer lock except via
the action steps nested inside them.

## 6. Trigger & condition vocabulary (6.2)

**Triggers** (matched against event-bus envelopes or scheduled): `notification.posted`,
`notification.removed`, `ui.element_appears`, `ui.element_disappears`, `ui.text_appears`, `app.opened`,
`app.closed`, `activity.changed`, `clipboard.changed`, `power.charging_changed`, `power.battery_level`,
`connectivity.wifi`, `schedule.time`, `schedule.interval`, `manual`. Each carries `filters`
(e.g. `package_in`, `text_regex`, `selector`) matched **purely** against the event/snapshot.

**Conditions** (pure, evaluated against device/snapshot/variable state): `foreground_package`,
`screen_contains`, `selector_exists`, `device_unlocked`, `battery_min`, `charging`, `wifi_ssid`,
`time_window`, `variable` (compare), `risk_below`, `last_action_ok`, `network_available`.

Both are **pure predicates** (`triggers.matches(spec, event) -> bool`, `conditions.evaluate(spec, ctx) ->
bool`); the daemon TriggerManager and the engine supply the event/context.

## 7. RPC / CLI / MCP surface

**Daemon RPC additions** (results envelopes, daemon spec §5 style):

| Method | Plan | Mutating? | Meaning |
|---|---|---|---|
| `macro_validate` | 6.1 | no | Parse + validate a doc; return errors or a normalized plan. |
| `macro_run` | 6.1 | **yes** | Run a doc now (`manual` trigger); returns `run_id` + outcome (or streams via run records). |
| `macro_cancel` | 6.1 | control | Flip a run's cancellation token. |
| `macro_status` | 6.1 | no | Live/last run state for a `run_id` or macro name. |
| `macro_enable` / `macro_disable` | 6.2 | control | Register/unregister a macro's trigger with the TriggerManager. |
| `macro_list` | 6.2 | no | Registered macros + enabled state + recent runs. |
| `autonomy_grant` / `autonomy_revoke` / `autonomy_list` | 6.3 | control/no | Manage the progressive-autonomy grant ledger. |
| `memory_show` / `memory_export` / `memory_delete` | 6.3 | no/control | Inspect / export / clear the memory stores. |

**CLI verbs:** `phonectl macro validate|run|cancel|status|enable|disable|list`,
`phonectl autonomy grant|revoke|list`, `phonectl memory show|export|delete`. Each routes through the daemon
when reachable (Plan 5.1 `_dispatch`) or runs in-process. **MCP tools** (Plan 2.3 registry):
`phone.macro.create/validate/run/cancel/status`, `phone.events.subscribe` (already in 5.2),
`phone.policy.explain` (already in 2.3) — the engine adds the macro tools onto the existing registry.

## 8. Memory & state layer (6.3)

Stores under `$PHONECTL_HOME/memory/` (one JSON file each), all redacted on write:

- **`device.json`** — Android version, OEM skin, screen metrics/density, navigation mode, wireless-debugging
  behavior, installed providers, granted permissions, known-flaky commands.
- **`apps.json`** — per-package: launch intents, common selectors, known screens, risk hints, deep links,
  notification capabilities.
- **`prefs.json`** — confirmation thresholds, quiet hours, guarded apps, allowed contacts/apps for
  autonomous replies, redaction rules.
- **`selectors.json`** — stable selectors learned from successful interactions, keyed by `package` + app
  `version` + `locale`.
- **`failures.json`** — provider errors, stale-selector rates, uiautomator failure modes, reconnect history.

Capture hooks fire from run records (selector that resolved → `selectors.json`; retryable failures →
`failures.json`), **after** `redact.py`. `memory_export` returns the merged stores; `memory_delete` clears
one or all. Default storage is operational metadata only (strategy §25); message/contact content is never
persisted.

## 9. Security model

- **No new privilege, no new writer.** Macro actions go through `run_action` (D3): single-writer lock,
  mode/kill-switch, risk policy, rate limits, audit — all reused. A macro cannot do anything an ad-hoc
  action cannot.
- **Two-layer gating.** The autonomy gate (D10/D11) can only *further restrict* the funnel; critical
  actions need explicit one-time approval, and unattended runs that hit a confirm boundary stop with
  `confirmation_required` rather than proceeding.
- **Secrets never leak.** `secret`-scope variables are redacted in every record/log (D6) via `redact.py`.
- **Emergency stop covers macros.** The STOP sentinel halts macro action steps (they call `run_action`),
  and `macro_cancel` cooperatively stops the orchestration (D8).
- **Inert documents.** No `eval`/shell/code step (D1); the only network-leaving action (`http`/`webhook`)
  is deferred and, when added, is risk-classified as a leaving-the-device action (strategy §24).
- **Narrow, deletable memory.** Operational metadata only, redacted, `PHONECTL_HOME`-isolated, exportable
  and deletable (D12).
- **Loopback only.** No new socket; triggers ride the daemon's loopback event bus.

## 10. Risks & mitigations

| Risk | Mitigation |
|---|---|
| A macro silently performs a high-risk action unattended. | Autonomy gate (D11): confirm by default; unattended only under a live grant; critical needs one-time approval; confirm-in-no-human → `confirmation_required` (safe stop). |
| A retry replays a destructive action whose context changed. | D7: high-risk replay re-runs the policy gate; non-idempotent steps can opt out of retry. |
| A secret (OTP/token) leaks into logs/run records. | D6: `secret` scope always redacted via `redact.py`, the same policy as audit v2. |
| A runaway loop/`for_each` taps forever. | `max_iterations` on `loop`, `max_runs_per_hour`/`cooldown` on triggers (6.2), the global rate ledger (2.2), and cancellation/kill-switch. |
| A YAML dependency creeps into the pure core. | D2: pure parser consumes a `dict`; YAML only at the loader edge behind an optional `[yaml]` extra. |
| Trigger storms from a chatty event source. | Cursor-bounded poll + per-macro limits + cooldown; a cursor gap is observable, not a silent flood. |
| Memory grows into personal-data hoarding. | D12: operational-metadata only, redacted, version/locale-keyed selectors that self-invalidate, easy export/delete. |
| Someone forks `run_action` inside the engine. | Explicitly forbidden (D3); action steps map to the existing triple and call the funnel. |
| Scheduler drift / DST surprises. | Monotonic-clock scheduling (D5), pure `next_fire` with injected clock; sub-second + DST corner cases documented out of scope. |
| Macro engine required where no daemon runs. | In-process fallback (6.1) runs an explicit macro against `build_runtime`; triggers/scheduler need the daemon but explicit `macro run` does not. |

## 11. Open questions

1. **`http`/`webhook` actions.** Leaving-the-device network steps are deferred. When added, are they a
   distinct high-risk capability with their own allowlist, or folded into the risk ledger's "leaves the
   device" signal?
2. **Macro signing.** Strategy §23 says "signed" plans. Is a content hash + autonomy-grant binding enough
   for v1, or is real cryptographic signing (and a key story) needed before unattended graduation?
3. **Parallelism.** §12.4 lists "limited parallel tasks." The single-writer lock serializes *actions*; do
   we ever need concurrent *observation* branches, or is sequential + `race` sufficient?
4. **`parent_task_id` minting across frontends.** Daemon open question §4 resurfaces: does an MCP "task"
   spanning many `act`s reuse a macro `run_id`, or is there a separate task layer above runs?
5. **Selector-library trust.** When does a learned selector override an author-specified one — never, or
   only when the author's selector fails and the library has a higher-confidence match for this app
   version/locale?
6. **Scheduler hosting.** When the daemon is absent, who fires time triggers — only Termux:Boot/companion
   (D5 seam), or a best-effort foreground `phonectl macro watch`?

## 12. Handoff to implementation plans

This spec is split into three TDD implementation plans. All three keep the **full suite green**, add **no
core runtime dependency** (YAML is an optional `[yaml]` extra at the loader edge only), reuse
`run_action`/`policy`/`ratelimit`/`redact`/`results`/`errors` verbatim, and require **no macro for any
existing primitive**.

**Plan 6.1 — macro runtime core.** `macro/schema.py` (pure parse/validate), `macro/variables.py` (pure
scoped resolution + `${var}` interpolation), `macro/engine.py` (control-flow executor dispatching action
steps through `run_action`, cancellation token, bounded-backoff retry with high-risk re-check), the
macro-run records (`MacroRun` summary + `parent_task_id` lineage in `runs.jsonl`), the daemon
`macro_validate/run/cancel/status` RPC + CLI verbs + MCP tools, and the optional `[yaml]` loader extra.

**Plan 6.2 — triggers + scheduler + event subscriptions.** `macro/triggers.py` (pure event/snapshot
matching), `macro/conditions.py` (pure condition evaluation), `macro/scheduler.py` (pure monotonic
`next_fire`), the daemon **TriggerManager** (drains the 5.2 event bus, matches registered macros, enforces
`max_runs_per_hour`/`cooldown`, enqueues runs through the engine), the scheduler thread, and
`macro_enable/disable/list` RPC + CLI.

**Plan 6.3 — progressive autonomy + memory/state layer.** `macro/autonomy.py` (append-only grant ledger +
pure `decide`), wired as the engine's per-action gate above `run_action`; `macro/memory.py` (five redacted,
`PHONECTL_HOME`-isolated stores + capture hooks from run records + export/delete), and the
`autonomy_grant/revoke/list` + `memory_show/export/delete` RPC + CLI.

Together, 6.1–6.3 deliver the strategy §12/§18/§23/§25 macro engine: declarative, auditable automations
with control flow, scoped variables, triggers + a scheduler, one policy choke-point + an autonomy gate,
cancellation, and a narrow user-controlled memory — built **on** the Phase 5 daemon, never bolted onto
ad-hoc commands.
