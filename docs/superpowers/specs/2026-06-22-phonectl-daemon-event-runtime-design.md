# phonectl — daemon & event runtime (single-writer core + event broker)

**Date:** 2026-06-22
**Status:** Design spec (Phase 5 brainstorm→spec). Required before Plans 5.1 and 5.2.
**Plan 5.1 shipped:** daemon process + loopback JSON-RPC API + single-writer dispatch + durable run records (`runs.jsonl`) + frontend auto-routing. All 9 tasks implemented and green. **Plan 5.2 is next:** snapshot cache + monotonic snapshot IDs + event bus + fanout subscriptions.
**Author:** Jeremy McCoy (with Claude)

**Reads with:**

- `docs/superpowers/phonectl-platform-roadmap.md` — **§5** (Phase 5 row) and **§5.1** ordering rationale
  (the daemon is a *compatible evolution* of the Phase 2.1 single-writer seam, never a rewrite).
- `docs/superpowers/phonectl-automation-platform-strategy.md` — **§22** (the daemon as the heart of the
  platform: single writer + event broker), **§21** (capability contracts, `snapshot_before/after`),
  **§7.4** (centralized serialized execution).
- `docs/superpowers/plans/2026-06-22-phonectl-remaining-plans-meta-plan.md` — **§3 Phase 5 stub** and the
  authoring rules (brainstorm → spec → plan is mandatory before any Phase 5 plan).
- Plan **2.1** (`run_action` single-writer funnel + audit v2), Plan **3.1** (provider/capability graph,
  `cli.build_runtime`), Plan **4.1** (AccessibilityService provider: `poll_events`, the `Transport`
  Protocol seam), Plan **4.3** (`SocketTransport` loopback framing + companion emergency stop).

This is a **design document** — goals, non-goals, architecture, locked decisions with rationale,
schemas, risks, and a handoff to the implementation plans. It contains **no TDD tasks**; those live in
Plans 5.1 and 5.2.

---

## 1. Goal

Make phonectl's runtime a **long-lived process that is the single writer and the event broker for all
phone actions.** Today every CLI invocation (and the MCP server) builds a fresh runtime, observes, acts,
and exits — correct, but stateless: the provider graph is rebuilt per command, there is no shared
snapshot identity across calls, and serialization is only process-local. The daemon closes that gap:

- **One writer.** Every mutating action across every frontend serializes through one global lock owned by
  the daemon, so two agents, a macro, and a human cannot tap/type simultaneously (strategy §7.4, §22).
- **One warm runtime.** The provider graph (`ProviderRegistry`), `Session`, and `Connection` are built
  **once at startup** and kept warm across requests — no per-command rebuild, no per-command reconnect.
- **One policy choke-point.** Mode checks, kill-switch, risk policy, rate limits, and audit happen in
  exactly one place (the existing `runtime.run_action`), reused verbatim inside the daemon.
- **Snapshot identity.** Observations mint **monotonic snapshot IDs**; index-based actions can be pinned
  to the snapshot they were planned against, so a stale index or a foreground-app change is caught
  centrally instead of silently mis-tapping.
- **Events.** An in-process event bus fans UI changes, notifications, and action/lifecycle events out to
  MCP clients, macros (Phase 6), and logs via a cursor-based poll — the platform's nervous system.
- **Durable run records.** One append-only record per action with action/parent IDs, before/after
  snapshots, the provider that handled it, the risk decision, retries, and outcome (strategy §22).

CLI and MCP become **frontends**: they discover a running daemon and route through it, or — if none is
running — execute in-process exactly as they do today. This is the **north-star core**: the seam that
turns a pile of primitives into an observable, cancellable, single-owner automation platform.

## 2. Non-goals

- **No Android/Kotlin in this phase.** The daemon runs as a Python `phonectl daemon` process. Hosting it
  inside the Plan 4.3 companion foreground service, or launching it from **Termux:Boot**, is a noted
  **seam only** (§9.4) — no native code is built here.
- **No macro engine.** Triggers, conditions, declarative automations, and progressive autonomy are
  **Phase 6** (Spec 6.0). The daemon provides the event bus and run records that the macro engine will
  *consume*; it does not interpret macros.
- **The daemon is not required for v1 primitives.** Every existing command must keep working with no
  daemon present. Daemonization is a **compatible evolution** (roadmap §5.1, strategy §22 closing): the
  no-daemon path is the unchanged in-process path.
- **No new transport invention.** The wire protocol reuses Plan 4.3's loopback newline-delimited JSON
  framing; this spec does not define a second protocol.
- **No remote access.** The daemon binds **loopback only** (`127.0.0.1`). Cross-device/cloud is a
  permanent non-goal of phonectl (design spec §2).
- **No fork of the action funnel.** `runtime.run_action` is reused verbatim; the daemon does not
  reimplement guardrails.

## 3. Architecture

### 3.1 Where the daemon sits

```text
backend/provider capabilities        (Phase 1, 3, 4)
  ↓
observe/action primitives            (Phase 0 + 1: observer, actuator, ui_parser, selectors)
  ↓
single-writer funnel  run_action()   (Phase 2.1 — the seam the daemon owns)
  ↓
DAEMON  (Phase 5)  ── single writer · snapshot cache · event bus · run records · ONE policy choke-point
  ↑ loopback newline-delimited JSON (Plan 4.3 framing)
  │
CLI frontend            MCP frontend            macros (Phase 6) / logs / local UI
  (DaemonClient)         (DaemonClient)          (event subscribers)
```

The daemon is a thin **server + dispatcher** wrapped around the *already-built* runtime. It does not add a
new execution path; it **hosts** the existing one and gives it identity, persistence, and fanout.

### 3.2 CLI and MCP as frontends

A frontend does, on each invocation:

1. `discover()` → read `$PHONECTL_HOME/daemon.json`, `ping` the advertised port.
2. **If reachable** → construct a `DaemonClient`, send the RPC, return the daemon's results envelope.
3. **If not** (no file, stale file, failed ping) → fall back to the unchanged in-process path
   (`build_runtime` → `observe`/`run_action`), exactly as today.

Because both paths return the **same results envelope** (`results.ok`/`results.err`), the frontend code is
identical aside from "where does the envelope come from." This is what makes daemonization invisible to
callers and keeps the full test suite green with no daemon required.

### 3.3 The warm runtime

The daemon calls `cli.build_runtime(cfg)` **once at startup** and holds the returned
`(ProviderRegistry, Session, Connection)` for its lifetime. Consequences:

- Provider discovery (Termux:API probe, future Accessibility/Notification provider availability) runs
  once, not per command.
- The ADB connection stays warm; on loss the daemon uses Plan 1.3's `reconnect` path (`conn.ensure()` →
  layered last-port → mDNS → bounded port-probe → host-Termux shim seam) rather than failing the request
  outright.
- The `Session` carries `session.last` across requests, which is the substrate the snapshot cache (§7)
  builds monotonic IDs on top of.

Backend isolation is preserved: the daemon talks to providers **only** through the warm
`ProviderRegistry`/`build_runtime`; it never imports `adb_backend` or calls `subprocess`/`adb` directly.

### 3.4 The request/response loop

A single accept loop (stdlib `socket` + `selectors`) binds a loopback TCP server, reads newline-delimited
JSON request lines, dispatches each `method` through a registry (`daemon/rpc.py`), and writes one
newline-delimited JSON response line (a results envelope). Mutating methods (`act`) route through
`run_action`, which already holds the global single-writer lock; read methods (`observe`, `find`,
`status`, `capabilities`, `audit_query`, `policy_explain`) execute against the warm runtime without taking
the writer lock. The loop is **loopback-only** and never binds `0.0.0.0`.

## 4. Locked decisions (with rationale)

These ten decisions are the contract the 5.1 and 5.2 plan authors share verbatim. Each is stated, then
justified.

### D1 — Single writer = the daemon owns the one `run_action()` choke point

The daemon serializes mutating RPCs through `runtime.run_action`, which already holds a process-local
`threading.Lock` and returns `BusyError` when held (Plan 2.1). Inside the daemon that lock *is* the global
single writer, because the daemon is the only process executing actions. The in-process (no-daemon) path
keeps the same lock for its own callers.

**Rationale.** `run_action` is the funnel for mode/kill-switch/idempotency/policy/rate-limit/audit. Forking
it would duplicate (and inevitably drift from) every guardrail. **Reuse it verbatim** — the daemon adds
*identity and persistence around* the funnel, not a second funnel.
**Trade-off.** A long action blocks other mutating callers; they get a structured `busy` envelope rather
than queuing. Queuing/priority is deliberately deferred (open question §12).

### D2 — Transport = reuse Plan 4.3's loopback newline-delimited JSON framing

The daemon binds a **loopback TCP server on `127.0.0.1` only** and speaks the same framing as Plan 4.3's
`SocketTransport` (`src/phonectl/providers/transport.py`: `SocketTransport`, `next_request_id`).

- **Request line:** `{"method", "params", "request_id", "timeout", "version"}`.
- **Response line:** the results envelope shape — `{"ok", …, "request_id", "version"}` with either `"data"`
  (on `ok`) or `"error"` (on failure). Bodies are built by `results.ok()`/`results.err()` so the Plan-1.1
  structured-result invariant holds **end to end**.

**Rationale.** Phase 4 already designed a loopback, request-id'd, version-negotiated, stale-response-safe
JSON line protocol for the companion. Reusing it means one wire format across companion *and* daemon, one
place for framing bugs, and stdlib-only (`socket`, `json`).
**Trade-off.** Newline-delimited JSON cannot stream partial responses; events are delivered by **polling**
(`events_poll`, §8), not server-push. Acceptable: the cursor poll is the same contract Plan 4.1's
`poll_events` already uses, and it keeps the protocol trivially stdlib-implementable.

> **Sequencing note.** `transport.py`'s `SocketTransport` ships in **Plan 4.3, which is written but not yet
> implemented.** Plan 5.1 reuses that framing. If 5.1 is executed before 4.3 lands, 5.1 must either depend
> on 4.3 first or lift the small framing/`next_request_id` helper into a shared location — it must **not**
> invent a second framing. This ordering is called out in the handoff (§13).

### D3 — Daemon discovery via `$PHONECTL_HOME/daemon.json`

On start the daemon writes `daemon.json` and removes it on **clean** stop:

```json
{"pid": 41234, "host": "127.0.0.1", "port": 51123, "version": 1, "started_at": "2026-06-22T18:04:11Z"}
```

Frontends `discover()` → read `daemon.json` → `ping`. If reachable, route through `DaemonClient`;
otherwise execute in-process (unchanged behavior). A **stale** `daemon.json` whose `ping` fails is ignored
(and may be cleaned). **No static daemon port lives in config** — the port is chosen at start (OS-assigned
ephemeral) and published via `daemon.json`.

**Rationale.** A published-port file with a liveness ping is the simplest correct discovery: it survives
crashes (stale file is self-correcting on failed ping), needs no registry, and keeps `PHONECTL_HOME`
isolation (tests point `PHONECTL_HOME` at `tmp_path` and get a private daemon).
**Trade-off.** A crashed daemon leaves a stale file until the next failed ping. Mitigated by treating any
unreachable advertised endpoint as "no daemon."

### D4 — New package `src/phonectl/daemon/`

- **Plan 5.1:** `daemon/server.py` (accept loop + dispatch + warm lifecycle), `daemon/client.py`
  (`DaemonClient`), `daemon/rpc.py` (method registry), `daemon/discovery.py` (`daemon.json` read/write +
  ping).
- **Plan 5.2:** `daemon/snapshots.py` (`SnapshotCache`), `daemon/events.py` (`EventBus` + sources + poller
  thread).

**Rationale.** A dedicated package keeps the daemon a clean, swappable layer over the runtime, with the
5.1/5.2 split mirroring "process + RPC" vs "snapshots + events."

### D5 — RPC method set

**5.1 methods:** `ping`, `status`, `observe`, `act` (verb routed through `run_action`), `find`,
`capabilities`, `policy_explain`, `audit_query`, `stop`, `resume`. Each returns a results envelope.
**5.2 additions:** `observe` returns `snapshot_id`; `act` returns `snapshot_before`/`snapshot_after`;
`events_poll(since, max)` (cursor-based); `events_subscribe` semantics.

**Rationale.** This mirrors the existing surface (observe/act/find/capabilities/policy explain/audit) plus
daemon-only control (`ping`/`status`/`stop`/`resume`). `stop`/`resume` map onto the existing kill-switch
(STOP sentinel), so emergency stop works the same with or without the daemon.

### D6 — Provider lifecycle: build once, keep warm, reconnect via 1.3

The daemon calls `build_runtime(cfg)` once at startup and keeps the `ProviderRegistry + Session +
Connection` warm across requests. On connection loss it uses Plan 1.3's reconnect path. **5.1** owns
lifecycle/health; **5.2** wires event sources to the *same warm providers*
(`AccessibilityProvider.poll_events` from 4.1, `NotificationsProvider` events from 4.2).

**Rationale.** Warmth is the entire performance and correctness argument for a daemon: shared connection,
shared session, one discovery. Reusing 1.3's reconnect keeps recovery logic in one place.
**Trade-off.** A warm process can drift (e.g., the ADB port rotates). `status`/health surface this, and
`conn.ensure()` runs at the top of `run_action` so each action still self-heals.

### D7 — Durable run records → `$PHONECTL_HOME/runs.jsonl` (extends audit v2)

Append one record per action (schema in §9). This **extends** Plan 2.1's audit
(`audit.log_action`/`actions.jsonl`) with a higher layer keyed by `action_id`/`parent_task_id`. It does
**not** replace `actions.jsonl`.

**Rationale.** `actions.jsonl` is the redaction-aware audit trail; `runs.jsonl` is the *run ledger* macros
and the eventual UI will join on (action lineage, before/after snapshots, retries, approvals — strategy
§22). Two files, two purposes, one append discipline.

### D8 — Snapshot cache (5.2)

Monotonic IDs `"snap_1"`, `"snap_2"`, … . The cache maps `snapshot_id → snapshot dict`. `observe()`
**mints + caches** a new id. Every `act()` **invalidates** (the re-observe invariant) and yields a fresh
id. Index-based acts may carry an **expected snapshot_id**; if it differs from the current cached id — or
the foreground app changed — the daemon raises `errors.StaleSnapshotError` → `results.err`.

**Rationale.** This gives the platform the `snapshot_before`/`snapshot_after` identities strategy §21
specifies, and turns Plan 1.2's per-snapshot stale protection into a *cross-request* guarantee: an index
planned against `snap_4` cannot be silently executed against `snap_7`.

### D9 — Event bus (5.2)

In-process pub/sub with a **monotonic event sequence**. Envelope: `{"seq", "type", "ts", "source",
"data"}`. Types: `ui_changed`, `notification_posted`, `action_started`, `action_finished`, `lifecycle`.
Sources: `AccessibilityProvider.poll_events` (ui), `NotificationsProvider` (notifications), internal
action/lifecycle hooks. Subscribers (MCP clients, macros, logs) consume via `events_poll(since, max)` →
`{"events": [...], "cursor": int}` — the **same cursor contract** as Plan 4.1's `poll_events`. A background
**poller thread** drains provider event sources into the bus.

**Rationale.** A monotonic-seq bus with a cursor poll is the minimum that supports replay-from-cursor,
multiple independent subscribers, and a stdlib-only transport. Reusing the 4.1 cursor contract means MCP
and macros already know the shape.
**Trade-off.** Poll latency (vs push). Bounded buffer → old events age out; a far-behind subscriber sees a
cursor jump (open question §12).

### D10 — Termux:Boot autostart + companion foreground-service hosting = design-spec only

The daemon **may later** be started by Termux:Boot or hosted in the Plan 4.3 foreground service. This spec
**notes the seam and builds no Android/Kotlin.** For now the daemon runs as `phonectl daemon` (foreground).
`daemon_autostart` (config, default `false`) is the future hook the CLI can read to launch the daemon
on first use.

**Rationale.** Autostart is an ROM-specific, native-adjacent concern; gating it behind a documented seam
keeps Phase 5 pure-Python and testable while leaving the obvious upgrade path.

## 5. RPC method table

| Method | Params | Returns (`data` on `ok`) | Mutating? | Plan |
|---|---|---|---|---|
| `ping` | — | `{"pong": true, "version", "pid"}` | no | 5.1 |
| `status` | — | warm-runtime health: provider list, connection state, last snapshot id, event cursor, uptime | no | 5.1 |
| `observe` | `screenshot?`, `tree?`, `relations?` | snapshot dict (5.2: + `snapshot_id`) | no | 5.1 / 5.2 |
| `act` | `verb`, `target` (`i`/selector/`x,y`), `yes?`, `idempotency_key?`, `expected_snapshot_id?` | post-action snapshot (5.2: + `snapshot_before`/`snapshot_after`) | **yes** | 5.1 / 5.2 |
| `find` | `selector` | matched element(s) + confidence | no | 5.1 |
| `capabilities` | — | merged capability map + `capabilities_by_provider` | no | 5.1 |
| `policy_explain` | `verb`, `target` | `risk_level`, `decision`, `reasons`, `recommended_action` | no | 5.1 |
| `audit_query` | filters (`since?`, `verb?`, `limit?`) | recent audit/run records (redacted) | no | 5.1 |
| `stop` | — | sets the STOP sentinel (emergency stop) | control | 5.1 |
| `resume` | — | clears the STOP sentinel | control | 5.1 |
| `events_poll` | `since` (cursor), `max` | `{"events": [...], "cursor": int}` | no | 5.2 |
| `events_subscribe` | filter (`types?`, `source?`) | subscription/cursor semantics over `events_poll` | no | 5.2 |

Unknown methods return a structured `unknown_method` error envelope. All returns are results envelopes;
mutating `act` is the only method that takes the single-writer lock (via `run_action`).

## 6. Wire protocol

- **Framing.** Newline-delimited JSON over a loopback TCP connection — Plan 4.3's `SocketTransport`
  framing, reused. One request line in, one response line out. Stdlib `socket` + `json` + `selectors`.
- **Request envelope:** `{"method", "params", "request_id", "timeout", "version"}`. `request_id` from
  `next_request_id` (the same monotonic helper the companion transport uses); the daemon echoes it on the
  response for correlation.
- **Response envelope:** the results envelope shape — `{"ok", …, "request_id", "version"}`, with `"data"`
  (success) or `"error": {"code", "message", "retryable", "requires_user", "user_action"}` (failure),
  built by `results.ok()`/`results.err()`. Capability/provider/`risk_level`/`reasons` fields ride through
  unchanged from `run_action`.
- **Versioning.** A numeric `version` on both request and `daemon.json`. The daemon rejects an
  incompatible client version with a structured error rather than misparsing; bumping the protocol is an
  additive, negotiated step (matching the 4.x version/capability handshake).
- **Stale-response protection.** Because `request_id` is echoed, a client that times out and a late
  response arriving on a reused connection are distinguishable — the same protection Plan 4.x's transport
  defines. The daemon never reorders responses on a single connection.
- **Errors at the transport edge.** Connect/ping failures surface to the frontend as the additive daemon
  error code `daemon_unreachable` (introduced in 5.1), which triggers the in-process fallback rather than
  a hard failure.

## 7. Snapshot model

- **Minting.** `observe()` produces a snapshot dict (via `observer.observe`, which also sets
  `session.last`), assigns the next monotonic id `snap_N`, and caches `snapshot_id → snapshot`.
- **Invalidation = re-observe.** Per the platform invariant, every `act()` re-observes; the daemon
  enforces this centrally and mints a **fresh** id for the post-action snapshot. The pre-action id becomes
  `snapshot_before`; the new one `snapshot_after` (strategy §21).
- **Foreground check.** An index/selector act may carry `expected_snapshot_id`. The daemon compares it to
  the current cached id **and** compares the foreground app (`snapshot["app"]`) before resolving an index
  to coordinates. A mismatch on either → `errors.StaleSnapshotError` → `results.err` with
  `requires_user`/retry guidance — never a silent mis-tap.
- **Stale-index protection.** This generalizes Plan 1.2's `expected_hash`/`stale_ok` per-snapshot
  protection into a cross-request guarantee: an index is only valid against the snapshot it was planned
  against. Raw `(x,y)` remains the escape hatch (no snapshot pin); selectors remain the durable target
  that survives reordering.
- **Cache bound.** The cache is bounded (recent N snapshots); evicted ids fail `StaleSnapshotError` if
  referenced, which is the correct, safe outcome.

## 8. Event model

- **Envelope:** `{"seq", "type", "ts", "source", "data"}` with a process-monotonic `seq`.
- **Types:** `ui_changed` (foreground/screen-hash change), `notification_posted`, `action_started`,
  `action_finished`, `lifecycle` (daemon up/down, provider connect/disconnect, reconnect).
- **Sources:**
  - `AccessibilityProvider.poll_events` (Plan 4.1) → `ui_changed` (when the companion is present).
  - `NotificationsProvider` (Plan 4.2) → `notification_posted` (companion, or degraded Termux:API read).
  - Internal hooks around `run_action` → `action_started` / `action_finished`; daemon lifecycle →
    `lifecycle`.
- **Poller thread.** A single background thread drains the provider event sources (each exposing a cursor
  poll) into the bus, advancing each source's cursor and assigning bus `seq`s. It is a **reader** against
  the warm providers; it takes no writer lock.
- **Consumption.** Subscribers call `events_poll(since, max)` → `{"events": [...], "cursor": int}` — the
  same cursor contract as Plan 4.1's `poll_events`. `events_subscribe` provides filtered cursor semantics
  over the same buffer.
- **Fanout.** The same bus feeds MCP clients (agent observability), macros (Phase 6 triggers), and logs.
  The daemon does not interpret events; it brokers them.

## 9. Durable run records (`runs.jsonl`)

One append-only JSON object per action, written by the daemon alongside (not instead of) `actions.jsonl`:

```json
{
  "action_id": "act_8f2c…",
  "parent_task_id": "task_19a…",
  "request_id": "b41d…",
  "verb": "tap",
  "target": {"selector": {"resource_id": "com.example:id/send"}, "matched_i": 14},
  "provider": "AdbBackend",
  "snapshot_before": "snap_4",
  "snapshot_after": "snap_5",
  "risk_level": "medium",
  "decision": "allow",
  "reasons": ["foreground=com.example", "no_password_field"],
  "retries": 0,
  "outcome": "ok",
  "user_approved": false,
  "ts": "2026-06-22T18:05:02Z"
}
```

- `action_id` / `parent_task_id` are the **new lineage layer** (action → parent task/macro run). This is
  what macros (Phase 6) and any future UI join on.
- `request_id`, `verb`, `target`, `provider`, `risk_level`/`decision`/`reasons`, `outcome` already flow
  out of `run_action`'s results envelope; the daemon captures them into the record. `snapshot_before`/
  `snapshot_after`, `retries`, and `user_approved` are added by the daemon.
- **Relationship to audit v2.** `actions.jsonl` (Plan 2.1) remains the redaction-aware audit trail and is
  unchanged. `runs.jsonl` extends it; neither replaces the other. Both honor `PHONECTL_HOME` isolation.

## 10. Security model

- **Loopback only.** The server binds `127.0.0.1` and never `0.0.0.0`. Config exposes `daemon_host`
  (default `"127.0.0.1"`) and **rejects any non-loopback value**. No remote surface exists.
- **Single-writer serialization.** All mutating RPCs serialize through `run_action`'s lock; concurrent
  mutators get a structured `busy` envelope. No two callers can act simultaneously (strategy §7.4).
- **One policy choke-point.** Mode (`auto`/`confirm`/`dry-run`), kill-switch, risk policy, and rate limits
  are enforced once inside `run_action`. Frontends cannot bypass them — they can only *call* the daemon,
  which calls the funnel.
- **Kill-switch / emergency stop.** `stop`/`resume` map onto the existing STOP sentinel
  (`audit.kill_switch_active`); the Plan 4.3 companion emergency stop folds into the same state, so the
  persistent "Stop phonectl" notification halts the daemon's actions identically to the file sentinel.
  Because the check is inside `run_action`, STOP halts the daemon *and* the in-process path uniformly.
- **No new privilege.** The daemon talks to providers only through the warm `ProviderRegistry`; it gains
  no capability ADB/the companion didn't already grant, and never calls `adb`/`subprocess` itself.
- **Discovery hygiene.** `daemon.json` lives under `PHONECTL_HOME`; a stale/unreachable advertisement is
  ignored, so a crashed daemon cannot redirect a frontend to a wrong endpoint.

## 11. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Stale `daemon.json` after a crash redirects a frontend. | `discover()` always `ping`s; any unreachable advertised endpoint is ignored and the in-process path is used. |
| Warm runtime drifts (ADB port rotates, companion disconnects). | `conn.ensure()` at the top of every `run_action` self-heals via 1.3; `status`/`lifecycle` events surface degradation; reconnect path reused, not reinvented. |
| Long-running action starves other mutating callers. | Deliberate `busy` envelope (no hidden queue in 5.x); queuing/priority deferred to an open question, not silently added. |
| Snapshot cache grows unbounded. | Bounded recent-N cache; evicted ids fail `StaleSnapshotError` (safe). |
| Far-behind event subscriber misses events past the buffer. | Bounded buffer + monotonic `seq`; a cursor jump is observable (subscriber detects a gap), not a silent drop. |
| Executing 5.1 before Plan 4.3 lands `SocketTransport`. | Handoff (§13) requires depending on 4.3's framing or lifting the shared framing helper — never inventing a second protocol. |
| Someone forks `run_action` inside the daemon. | Explicitly forbidden (D1); reuse verbatim so guardrails can't drift. |
| Non-loopback `daemon_host` misconfigured. | Config validation rejects non-loopback hosts outright. |
| Tests leak across daemons. | `PHONECTL_HOME` isolation; ephemeral OS-assigned port; `daemon.json` is per-`PHONECTL_HOME`. |

## 12. Open questions

1. **Action queuing vs `busy`.** 5.x returns `busy` for a held lock. Should the daemon ever queue mutating
   requests (with a bounded depth + priority for emergency stop)? Deferred; revisit when macros (Phase 6)
   want fire-and-wait semantics.
2. **Event delivery floor.** Is poll-only sufficient, or will a long-poll / chunked-streaming variant be
   needed for low-latency macro triggers? The framing supports a future long-poll without protocol change.
3. **Subscriber back-pressure.** Per-subscriber cursors over one bounded buffer vs per-subscriber queues —
   what's the eviction policy when a subscriber lags?
4. **Multi-frontend identity.** Should `parent_task_id` be minted by the frontend (so an MCP "task" spans
   many `act`s) or only by the daemon? Affects the run-record lineage join with Phase 6 macros.
5. **Autostart trigger.** When `daemon_autostart=true`, does the *first frontend* spawn the daemon, or only
   an explicit `phonectl daemon`/Termux:Boot? (Design-spec seam; no code in Phase 5.)
6. **Health-driven restart.** Should a wedged warm runtime self-restart `build_runtime`, or only report
   unhealthy via `status` and let the operator restart `phonectl daemon`?

## 13. Handoff to implementation plans

This spec is split into two TDD implementation plans. Both keep the **full suite green**, add no runtime
dependency (stdlib `socket`/`json`/`threading`/`selectors`/`signal`), and require **no daemon for any
existing primitive** (the in-process path is the unchanged fallback).

**Plan 5.1 — daemon process + RPC API.** Creates `daemon/discovery.py` (`daemon.json` read/write + ping),
`daemon/server.py` (loopback accept loop + dispatch + warm `build_runtime` lifecycle/health + the
single-writer routing of `act` through `run_action`), `daemon/rpc.py` (method registry for `ping`,
`status`, `observe`, `act`, `find`, `capabilities`, `policy_explain`, `audit_query`, `stop`, `resume`),
`daemon/client.py` (`DaemonClient`), the `runs.jsonl` run-record writer, the frontend routing in CLI/MCP
(discover → route or in-process fallback), the `phonectl daemon` CLI verb, config keys `daemon_host`
(loopback-only, validated) and `daemon_autostart` (default `false`), and the additive daemon error codes
(`daemon_unreachable`, `unknown_method`). **Reuses** Plan 4.3's `SocketTransport` framing /
`next_request_id` — if 5.1 precedes 4.3, it depends on 4.3's framing first or lifts the shared helper; it
must not invent a second protocol. **Reuses** `run_action` verbatim — no fork.

**Plan 5.2 — snapshot cache + event bus.** Creates `daemon/snapshots.py` (`SnapshotCache`: monotonic
`snap_N` ids, cache, `observe`-mints / `act`-invalidates, `expected_snapshot_id` + foreground-app
stale-index protection → `StaleSnapshotError`) and `daemon/events.py` (`EventBus` with monotonic `seq`, the
`{"seq","type","ts","source","data"}` envelope, the background poller thread draining
`AccessibilityProvider.poll_events` and `NotificationsProvider` events into the bus, and the internal
action/lifecycle hooks). Extends the RPC surface: `observe` returns `snapshot_id`; `act` returns
`snapshot_before`/`snapshot_after`; adds `events_poll(since, max)` → `{"events":[...],"cursor":int}` (the
Plan 4.1 cursor contract) and `events_subscribe`. Wires event sources to the **same warm providers** 5.1
keeps alive.

Together, 5.1 and 5.2 deliver the strategy §22 daemon: single writer, snapshot cache + invalidation,
provider lifecycle, event fanout, one policy choke-point, and durable run records — as a **compatible
evolution** of the Phase 2.1 seam, never a rewrite.

---

## Plan 5.2 — Implemented contracts (as shipped)

### Snapshot cache (`daemon/snapshots.py` — `SnapshotCache`)

- `put(snapshot) -> str` — mints `snap_N` (monotonic, injectable counter), caches under that id, sets it current.
- `get(snapshot_id) -> dict | None` — retrieve cached snapshot.
- `current_id` / `current_foreground` — read-only properties; both `None` before first `put`.
- `foreground_of(snapshot_id) -> str | None` — `snapshot["app"]["package"]` for any cached id.
- `validate(expected_id, *, current_foreground) -> None` — no-op when `expected_id is None`. Raises `errors.StaleSnapshotError` when (a) `expected_id != current_id`, or (b) `expected_id == current_id` but both `current_foreground` and the pinned foreground are non-None and differ.

**RPC integration:** `observe` calls `snapshots.put(snap)` and adds `snapshot_id` to the envelope. `act` captures `snapshot_before = snapshots.current_id` before dispatch, calls `snapshots.put(session.last)` after a successful action to mint `snapshot_after`, and sets both on the envelope and the `runs.jsonl` record. The stale check runs **before** `_fn_for` or `runtime.run_action` — a mismatched `snapshot_id` param aborts immediately with `code="stale_snapshot"`.

### Event bus (`daemon/events.py` — `EventBus`)

Event envelope: `{"seq": int, "type": str, "ts": float, "source": str, "data": dict}`.

Valid types (`EVENT_TYPES` frozenset): `ui_changed`, `notification_posted`, `action_started`, `action_finished`, `lifecycle`. Publishing an unknown type raises `ValueError` before mutating the log.

- `publish(type, data, *, source) -> dict` — next seq, appends event, returns it.
- `poll(since=0, *, max=100) -> {"events": [...], "cursor": int}` — events with `seq > since`, bounded by `max`; `cursor` is the last emitted `seq` (or `since` when none).
- `latest_seq` — highest assigned seq, or 0.

### Provider poller (`daemon/poller.py` — `EventPoller`)

`EventPoller(bus, *, ui_source=None, notif_source=None)` — sources are injected; either may be `None`.

- `drain_once(*, max_events=50) -> int` — pulls from the UI source's cursor (publishing as `ui_changed`, `source="accessibility"`), diffs notifications `list()` against seen keys (publishing new ones as `notification_posted`, `source="notifications"`). Returns count published. No threads.
- `tick(max_events)` — alias for `drain_once`.

### RPC additions (`daemon/server.py`)

| Method | Change |
|---|---|
| `observe` | Returns `snapshot_id` in the top-level envelope |
| `act` | Validates `snapshot_id` param (stale check); returns `snapshot_before`/`snapshot_after`; emits `action_started`/`action_finished` on `self.events`; backfills both fields on the `runs.jsonl` record |
| `events_poll` | New: calls `self.poller.drain_once()` then `self.events.poll(since, max)` → `results.ok(data={"events":[...],"cursor":int})` |

Lifecycle: `bind()` publishes `lifecycle {"phase":"started"}`; `shutdown()` publishes `lifecycle {"phase":"stopped"}`.

### `events_subscribe` — deferred

Real server-push streaming (websocket/SSE-style fanout) is explicitly deferred. The cursor-based `events_poll` is the v1 subscription contract; `events_subscribe` is a drop-in evolution over the same cursor shape.
