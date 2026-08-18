# The daemon

`droidjig daemon` is the single-writer runtime. When it is running the CLI auto-routes actions
to it over loopback JSON-RPC; when it is not, the in-process primitives work exactly as they
always did. The daemon reuses `runtime.run_action` verbatim, so the safety properties in
[safety.md](safety.md) hold identically in-process and over the wire.

---

## Daemon

`droidjig daemon` makes the runtime a **long-lived single-writer process** that keeps the provider graph, session, and connection warm across requests and brokers all actions through one global write lock.

### Starting the daemon

```bash
droidjig daemon start
# droidjig daemon listening on 127.0.0.1:<PORT> (Ctrl-C to stop)
```

The daemon binds to an **ephemeral loopback TCP port** (`127.0.0.1` only — non-loopback is refused). It writes its address to `$DROIDJIG_HOME/daemon.json` and removes it on clean shutdown.

Because loopback is not an app boundary on Android, the daemon also mints a **per-run shared-secret token** on startup and writes it into `daemon.json` — which lives under `$DROIDJIG_HOME` (the Termux app's private storage), unreadable by other apps. Every RPC except the `ping` liveness probe must present that token; an unauthenticated request is refused with an `unauthorized` error. The CLI/MCP read the token out of `daemon.json` automatically, so this is invisible in normal use.

### Frontend auto-routing

Once a daemon is running, every `droidjig` CLI command (and the MCP server) **transparently routes through it** — no flags needed. `discover()` reads `daemon.json`, pings the endpoint, and on success the frontend sends a JSON-RPC call instead of building an in-process runtime. When no daemon is found, the original in-process path is used unchanged — daemonization is a **compatible evolution**.

### Daemon commands

```bash
droidjig daemon start          # run daemon in foreground (Ctrl-C to stop)
droidjig daemon status --json  # check if a daemon is running and its state
droidjig daemon stop           # send the shutdown RPC and terminate the daemon
```

`droidjig daemon stop` calls the daemon's `shutdown` RPC and waits for it to exit cleanly. This is **distinct** from the emergency kill-switch: the `STOP` sentinel (`STOP` file or companion flag) still interrupts individual actions regardless of daemon state. The daemon exposes a `stop` RPC (engage), but **no `resume` RPC** — clearing the kill switch is a host-only human action (`droidjig resume` or removing the sentinel).

### Async job model

When a daemon is running, `act`, `observe`, and `find` verbs are dispatched as **async jobs** on the daemon. The CLI **block-and-polls** by default — it submits the job, then polls `job_poll` until the job is terminal, timing out after `act_timeout` seconds (default 60 s). A slow-but-healthy daemon no longer falsely reports `daemon_unreachable`.

**`--detach`** on any action verb returns immediately with a job id instead of waiting:

```bash
droidjig tap --index 3 --detach
# droidjig: job job_abc123 (use: droidjig job job_abc123)
```

**`droidjig job <id> [--wait] [--json]`** queries or waits on a job:

```bash
droidjig job job_abc123           # print current status
droidjig job job_abc123 --wait    # block until terminal (cap = act_timeout)
droidjig job job_abc123 --json    # structured job envelope
```

Job statuses: `accepted` (queued), `running`, `done`, `error`.

### Loopback-only + token auth

The daemon binds and listens on **`127.0.0.1` exclusively**. `daemon_host` is validated and a non-loopback address is rejected with a clear error. The socket is never exposed to the network. Loopback alone is **not** an app boundary on Android, so the daemon additionally requires the per-run shared-secret token (written to `daemon.json`, unreadable by other apps) on every RPC except `ping` — see "Starting the daemon" above.

### Config keys

| Key | Default | Description |
|---|---|---|
| `daemon_host` | `"127.0.0.1"` | Loopback address to bind on (loopback-only, non-loopback is rejected) |
| `daemon_autostart` | `false` | Reserved for Termux:Boot autostart (not yet wired) |
| `act_timeout` | `60.0` | Wall-clock cap (seconds) for CLI block-and-poll on async jobs |
| `sync_timeout` | `15.0` | Client timeout for fast synchronous RPCs (status, shutdown, etc.) |
| `poll_interval` | `0.5` | Cadence (seconds) for `job_poll` during block-and-poll and `droidjig job --wait` |
| `job_queue_max` | `8` | Maximum pending-job FIFO depth; excess submissions return a `busy` error |
| `idempotency_ttl` | `300.0` | How long (seconds) a finished job stays eligible for idempotency deduplication |

Set via `$DROIDJIG_HOME/config.json`:

```json
{
  "daemon_host": "127.0.0.1",
  "daemon_autostart": false,
  "act_timeout": 60.0,
  "poll_interval": 0.5,
  "job_queue_max": 8
}
```

### Run records (`runs.jsonl`)

Every action dispatched through the daemon is appended as a structured run record to `$DROIDJIG_HOME/runs.jsonl`. Each record carries: `action_id`, `parent_task_id` (optional, for multi-step task tracking), `request_id`, `verb`, `target`, `provider`, `snapshot_before`, `snapshot_after`, `risk` decision, `retries`, `outcome`, and `user_approved`. This is a new layer on top of `actions.jsonl` — audit logging is unchanged.

### Events & snapshots

The daemon is the single writer **and** event broker.

**Snapshot cache:** `observe` returns a monotonic `snapshot_id` (`snap_1`, `snap_2`, …). Every `act` records `snapshot_before` / `snapshot_after` on the response envelope and the `runs.jsonl` record, making the re-observe invariant explicit. Index-based acts may optionally carry a `snapshot_id` field; the daemon rejects with a `stale_snapshot` error (→ re-observe) if the id no longer matches the current snapshot or the foreground app changed since the snapshot was taken (strategy §21).

**Event bus:** All events share the envelope `{"seq": int, "type": str, "ts": float, "source": str, "data": dict}`. Event types:

| Type | Source | When |
|---|---|---|
| `ui_changed` | `accessibility` | Provider polled a UI state change |
| `notification_posted` | `notifications` | New notification key seen by diff |
| `action_started` | `daemon` | An act passed the stale check and was dispatched |
| `action_finished` | `daemon` | An act completed (success or error) |
| `lifecycle` | `daemon` | Daemon started or stopped |

**Subscription — cursor-based long-poll:** hold the last `cursor`, call `events_poll(since=cursor, max=N)`, process the batch, repeat with the new cursor. Server-push streaming (`events_subscribe`) is a later evolution; the cursor contract makes it a drop-in addition.

```python
# RPC call
{"method": "events_poll", "params": {"since": 0, "max": 50}}
# Response
{"ok": true, "data": {"events": [...], "cursor": 5}}
```

### Discovery file (`daemon.json`)

```json
{
  "pid": 12345,
  "host": "127.0.0.1",
  "port": 54321,
  "version": 1,
  "token": "<per-run shared secret>",
  "started_at": 1750000000.0
}
```

The `token` is the shared secret every RPC (except `ping`) must present. `daemon.json` lives under `$DROIDJIG_HOME` — the app's private storage — so other apps cannot read it.

### No daemon required

In-process primitives (`observe`, `tap`, `type`, etc.) work exactly as they always did when no daemon is running. The daemon is a **compatible evolution** — it adds single-writer coordination and run records, it does not gate the v1 primitives.

### Termux:Boot autostart (seam only)

The `daemon_autostart` config key exists and `droidjig daemon start` runs in the foreground. Autostart via Termux:Boot and companion foreground-service hosting are deliberate seams — the interfaces are in place, the wiring is not yet built.
