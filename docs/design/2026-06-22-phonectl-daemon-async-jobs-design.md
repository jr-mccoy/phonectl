# droidjig — daemon async job model (responsive single-writer + pollable jobs)

**Date:** 2026-06-22
**Status:** Design spec (Phase 5 follow-up). Required before the async-jobs implementation plan.
**Author:** Jeremy McCoy (with Claude)

**Reads with:**

- `docs/design/2026-06-22-droidjig-daemon-event-runtime-design.md` — the daemon &
  event-runtime design this spec amends. Plans 5.1 (daemon process + RPC) and 5.2 (event bus +
  snapshot cache) shipped; this spec fixes a correctness bug found in **live device testing** of 5.1.
- Plan **2.1** (`runtime.run_action` single-writer funnel + audit v2) — reused verbatim inside the worker.
- Plan **4.3** (`SocketTransport` loopback framing) — the transport the client/worker ride.

This is a **design document** — goals, the bug, locked decisions with rationale, schemas, and a handoff
to the implementation plan. It contains **no TDD tasks**; those live in the plan.

---

## 1. The bug (found in live testing)

On a real device (Galaxy S25 Ultra over wireless ADB), every daemon-routed mutating action failed from
the frontend with `daemon_unreachable`, **even though the daemon was healthy and the action actually
executed.** Verified evidence:

- A daemon `act` (tap) takes **~16.5s** end-to-end: device input + minting `snapshot_after` (a full
  re-observe / uiautomator dump). A plain daemon `observe` ran **4–9s**.
- `DaemonClient.call` hard-codes a **5.0s** default timeout, and `cli._dispatch` never overrides it. The
  socket read raises on timeout; `client.call`'s blanket `except Exception` rewrites **any** failure to
  `DaemonUnreachableError("daemon call 'act' failed")`.
- The daemon's serve loop is **single-threaded and serial** (`serve_forever` → `_serve_conn` →
  `handle_line` inline), so a 16.5s act blocks *every* other RPC — including a status/ping poll — for its
  whole duration. That is why `observe` (≈4s) usually squeaks under the 5s budget while `act` never does.
- **Worst:** the daemon completes the action server-side regardless of the client giving up. `runs.jsonl`
  accumulated 5 tap records, all `outcome: ok`, including every call the frontend reported as failed
  (rc=1). The agent sees "failure," may retry, and double-acts.

A second, related lifecycle bug surfaced: `droidjig daemon stop` does **not** stop the daemon. The `stop`
RPC handler only writes the emergency-stop `STOP` sentinel file and returns; the process keeps running and
discovery is never cleared. There is a name collision between *stop the kill-switch* and *stop the daemon*.

## 2. Goal & non-goals

**Goal:** make slow device RPCs asynchronous jobs the frontend polls, so:

- a healthy-but-slow daemon never reports `daemon_unreachable`;
- the serve loop stays responsive during long actions (polls answered immediately);
- device access is serialized through one writer (preserved), with honest backpressure;
- a retry can never silently double-execute (idempotent jobs);
- `daemon stop` actually stops the daemon.

**Non-goals (this spec):**

- Routing the **MCP server** through the daemon. `mcp_server.py` still builds an in-process runtime and
  calls `runtime.run_action` directly; it is unaffected by this change. (Future: route MCP through the
  daemon for cross-frontend single-writer.)
- Multi-device / multi-worker parallelism. One device, one worker.
- Persisting the in-memory job registry across daemon restarts. `runs.jsonl` remains the durable record;
  the job registry is process-lifetime only.

## 3. Locked decisions

| # | Decision | Rationale |
|---|----------|-----------|
| 1 | **Async scope = `act`, `observe`, `find`.** Fast RPCs (`ping`, `status`, `shutdown`, `resume`, `audit_query`, `capabilities`, `policy_explain`, `events_poll`) stay synchronous with a config-driven generous timeout (`sync_timeout`). | These three touch the device and are slow/variable; the rest are sub-second. `events_poll`'s first call lazily builds the poller and can be slow once — the generous `sync_timeout` covers it. |
| 2 | **Client UX = block-and-poll by default**, `--detach` to return the job-id immediately. New `droidjig job <id>` to query/wait. | Preserves today's "run it, get the result" CLI/MCP contract; `--detach` enables fire-and-forget for autonomous flows. |
| 3 | **One background worker + bounded FIFO queue (`job_queue_max`, default 8).** Over the cap → `BusyError`. | One device job at a time = the real single-writer; avoids races on the shared, non-thread-safe `Session`/`Connection`. Bounded queue prevents runaway backlog while letting block-and-poll callers wait their turn. |
| 4 | **Idempotency: auto-keyed dedupe.** CLI/MCP auto-generate an `idempotency_key` per logical action; the daemon keeps a short-TTL (`idempotency_ttl`, default 300s) cache of `key → job_id`. A resubmit with a known key returns the existing job (in-flight or recently completed) instead of re-running. | Closes the double-execution hole even when a caller retries from a fresh process that lost the job-id. Matches the safety-first north-star. |
| 5 | **Lifecycle fix: add a `shutdown` RPC** that terminates the process (sets `_running=False`, closes the socket, removes discovery). Keep `stop`/`resume` as the emergency kill-switch sentinel. Point `droidjig daemon stop` at `shutdown`. | Resolves the name collision; makes `daemon stop` actually stop the daemon. |
| 6 | **Error taxonomy:** new `JobTimeoutError` (`code="job_timeout"`, `requires_user=True`, **not** auto-retryable) carrying the `job_id` and "still running; query with `droidjig job <id>`". Reserve `daemon_unreachable` strictly for connection-refused. | A poll-cap timeout no longer loses the job or masquerades as unreachable; the result is reattachable. Distinguishes "can't reach" (safe retry) from "running, may have acted" (do not blind-retry). |

## 4. Architecture

### 4.1 New component — `daemon/jobs.py`

```
Job:
  job_id: str            # uuid4 hex
  method: str            # "act" | "observe" | "find"
  params: dict
  status: str            # "queued" | "running" | "done" | "error"
  result_env: dict|None  # the results envelope once terminal
  idempotency_key: str|None
  ts_created/ts_started/ts_finished: float

JobRegistry(now=time.time, queue_max=8, idempotency_ttl=300):
  submit(method, params) -> job_id      # dedupe by idempotency_key; enqueue; cap -> BusyError
  get(job_id) -> Job | None
  start(run_fn)                          # spawn the single worker thread
  stop()                                 # join the worker on shutdown
```

- **Worker loop:** dequeue → set `running` → acquire `_write_lock` → call `run_fn(method, params)` (the
  refactored handler body) → store `result_env` + terminal status → release lock → append `runs.jsonl`
  (for `act`).
- **Dedupe:** on `submit`, if `idempotency_key` maps to a job that is `queued`/`running`, or `done`/`error`
  within `idempotency_ttl`, return that `job_id` without enqueuing.
- **Single worker** is the serializer; `_write_lock` is retained as belt-and-suspenders and to document the
  single-writer invariant.

### 4.2 `daemon/server.py` changes

- Refactor the `act`/`observe`/`find` handler bodies into plain callables (`_run_act`, `_run_observe`,
  `_run_find`) that the worker invokes. The RPC handlers become thin: `job_id = self.jobs.submit(method,
  params); return results.ok(capability="daemon.job_accepted", data={"job_id": job_id, "status":
  "accepted"})`.
- New RPC `job_poll` (params `{job_id}`): returns `{status, result_env}`; when terminal, `result_env` is the
  full action/observe envelope. Fast — answered immediately even while the worker is busy.
- New RPC `shutdown`: sets `_running=False`, closes the socket, `discovery.remove()`, stops the worker.
- **Move the write-lock into the worker.** `act` is no longer in `handle_line`'s `MUTATING` lock path (its
  RPC submit is fast and non-mutating at the RPC layer); the worker holds `_write_lock` during execution.
  `stop`/`resume` remain fast sentinel writes.
- The single-threaded `serve_forever` is unchanged and now correct: all device work runs off-loop in the
  worker, so every connection is served in well under `sync_timeout`.

### 4.3 `daemon/client.py` changes

- `call()`: distinguish **connection-refused / no-listener** (→ `DaemonUnreachableError`) from a **read
  timeout** on a reachable socket (→ a `timeout` envelope, not unreachable). Per-RPC calls are all fast now
  (submit + poll), so the existing 5s suffices for each individual call.
- Add `submit_and_wait(method, params, *, overall_timeout, poll_interval) -> env`: submit → loop `job_poll`
  every `poll_interval` until terminal or `overall_timeout`. On cap, return `results.err(JobTimeoutError(...,
  job_id=...))`.

### 4.4 `cli.py` changes

- `_dispatch`: async methods (`act`/`observe`/`find`) route through `submit_and_wait` when the daemon is
  reachable (or submit-only when `--detach`); fast methods use `client.call` directly; no daemon → existing
  in-process path unchanged.
- Auto-generate an `idempotency_key` (and `request_id`) for each act if absent.
- New `droidjig job <id>` command (`--wait` to block on a detached job).
- `--detach` flag on the act verbs; on `--detach`, print the `job_id` and exit 0.
- `_cmd_daemon` `stop` branch → call the new `shutdown` RPC (not `stop`).

### 4.5 Config additions (`config.DEFAULTS`)

| Key | Default | Meaning |
|-----|---------|---------|
| `act_timeout` | 60.0 | Overall wall-clock cap for block-and-poll on async jobs. |
| `sync_timeout` | 15.0 | Client timeout for synchronous fast RPCs. |
| `poll_interval` | 0.5 | Job-poll cadence. |
| `job_queue_max` | 8 | FIFO queue depth; over → `BusyError`. |
| `idempotency_ttl` | 300.0 | How long a completed job stays dedupe-eligible. |

## 5. Data flow — `act`, block-and-poll (default)

1. `droidjig tap --xy 720 1500` → `_act_params` (auto `idempotency_key` + `request_id`) → `_dispatch`.
2. Daemon reachable → `client.submit("act", params)` → daemon `JobRegistry.submit` enqueues, returns
   `{job_id, status:"accepted"}` **immediately**.
3. Frontend polls `client.call("job_poll", {job_id})` every `poll_interval` until terminal or `act_timeout`.
4. Terminal → print the result envelope (today's UX). Cap exceeded → `JobTimeoutError` with the `job_id` and
   "query with `droidjig job <id>`" (the job keeps running; result lands in the registry + `runs.jsonl`).
5. Worker: dequeue → `_write_lock` → `runtime.run_action` + mint `snapshot_after` → append `runs.jsonl` →
   store `result_env` in the `Job`.

`--detach`: steps 1–2 then print `job_id` and exit; the caller later runs `droidjig job <id> --wait`.

## 6. Error handling

- **Connection refused / no discovery** → `DaemonUnreachableError` (retryable); frontend falls back to the
  in-process path as today.
- **Poll cap exceeded** → `JobTimeoutError` (requires_user, not auto-retryable), carries `job_id`.
- **Queue full** → `BusyError` (retryable) at submit time.
- **Handler raised** → the worker stores an `error` envelope (existing `run_action`/`dispatch` mapping);
  `job_poll` returns it; the frontend prints it like any other typed error.

## 7. Testing strategy (handoff to the plan)

- **`jobs.py`:** `submit` returns id; dedupe by `idempotency_key` (in-flight + within TTL); queue cap →
  `BusyError`; worker runs job → `result_env` stored + status terminal; two jobs serialize (single-writer).
- **`server.py`:** `act`/`observe`/`find` return `{job_id, status:"accepted"}`; `job_poll` returns the
  result envelope when terminal; `shutdown` stops the serve loop + removes discovery; `stop` still only
  writes the `STOP` sentinel (kill-switch unchanged).
- **`client.py`:** `submit_and_wait` returns the result on completion; returns `JobTimeoutError` (with
  `job_id`) on cap; connection-refused → `DaemonUnreachableError`; reachable read-timeout → `timeout`
  envelope (not unreachable).
- **`cli.py`:** `_dispatch` block-and-poll path; `--detach` prints job-id; `droidjig job <id>`; `daemon
  stop` → `shutdown`.
- **Regression:** update existing daemon tests that assume synchronous `act`/`observe` envelopes
  (`test_daemon_server.py`, `test_daemon_rpc.py`) to the job model. Re-run the full suite (was 434 passing).
- **Live re-validation** on the device: `daemon start` → `tap` completes via block-and-poll (no false
  failure) → `runs.jsonl` gets exactly one record per logical action (no duplicates on retry) → `daemon
  stop` actually terminates the process.

## 8. Risks

- **Worker thread + shared warm runtime:** only the worker touches `Session`/`Connection`, so reads/writes
  stay single-threaded; the serve loop never touches device state. Keep it that way.
- **Idempotency TTL eviction** must not race the worker writing a result; guard the registry with a lock.
- **`act_timeout` vs real latency:** 60s is generous for a ~16.5s act, but a slow/contended device could
  still exceed it — that now degrades gracefully to a reattachable `JobTimeoutError`, not a false failure.
