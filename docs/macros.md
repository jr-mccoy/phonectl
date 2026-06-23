# phonectl Macros

Macros are declarative JSON documents that drive the `observe→act→observe` loop via the **macro engine** (Phase 6.1). They are executed through the same `runtime.run_action` funnel as CLI/MCP actions, so all safety invariants — kill-switch, rate-limiting, policy gate, audit log — apply identically.

## Anatomy

```json
{
  "name": "morning_briefing",
  "description": "Open news app and read headlines",
  "version": "1",
  "variables": {
    "app_package": "com.google.android.apps.news"
  },
  "actions": [
    { "type": "launch", "package": "${app_package}" },
    { "type": "wait", "seconds": 2 },
    { "type": "tap", "target": { "i": 3 } }
  ]
}
```

## Top-level fields

| Field | Required | Description |
|---|---|---|
| `name` | yes | Identifier (letters, digits, `_`, `-`) |
| `description` | no | Human-readable purpose |
| `version` | no | Semver or free-form string |
| `variables` | no | Default variable values (macro scope) |
| `trigger` | no | Automatic trigger specification (see below) |
| `conditions` | no | List of conditions that must hold before the macro fires |
| `limits` | no | Per-macro rate-limiting configuration |
| `actions` | yes | List of steps (may be empty) |

## Trigger vocabulary (Phase 6.2)

A `trigger` block makes a macro fire automatically — either on a schedule or in response to a device event. Macros without a `trigger` are manual-only (run via CLI/MCP).

### Schedule triggers

**`schedule.interval`** — fires every N seconds (no alignment; first fire is N seconds after arming).

```json
{ "type": "schedule.interval", "every_seconds": 300 }
```

**`schedule.time`** — fires once per day at a specific wall-clock time, optionally filtered to specific weekdays (0 = Monday, 6 = Sunday).

```json
{ "type": "schedule.time", "at": "07:30", "weekdays": [0, 1, 2, 3, 4] }
```

### Event triggers

**`clipboard.changed`** — fires when the clipboard content changes.

```json
{ "type": "clipboard.changed" }
```

**`notification.posted`** — fires when a notification is posted, optionally filtered by package.

```json
{ "type": "notification.posted", "package": "com.example.app" }
```

**`notification.dismissed`** — fires when a notification is dismissed.

```json
{ "type": "notification.dismissed", "package": "com.example.app" }
```

**`ui.foreground_changed`** — fires when the foreground app changes.

```json
{ "type": "ui.foreground_changed" }
```

**`manual`** — never fires automatically; always requires explicit invocation.

```json
{ "type": "manual" }
```

### Daemon scheduler

The `Scheduler` class in `phonectl.daemon.triggers` arms each enabled scheduled macro on its first `due()` call and fires it once the wall-clock time reaches the armed target. It uses only injected time (`datetime.now`) — no real clock in tests.

The `TriggerManager` class drains the event bus on each `step()` call and fires any event-driven macros whose triggers match and whose conditions all hold.

Both are invoked from the `events_poll` RPC handler after each bus drain. If either raises an exception, the error is swallowed silently so the daemon event loop is never wedged.

**On-device smoke:** Because the scheduler depends on real provider events and the real clock, behavior in CI is limited to unit tests with injected fakes. On-device smoke testing (a macro that fires every 10 seconds and taps a button) should be done manually.

## Conditions

The `conditions` list is evaluated before any trigger fires. All conditions must hold. Available conditions (Phase 6.2):

| Condition type | Checks |
|---|---|
| `selector_exists` | At least one UI element matches the given selector |
| `risk_below` | Estimated risk of the next action is below the threshold |
| `variable_equals` | A named variable equals the given value |
| `time_between` | Current wall-clock time is between `start` and `end` (HH:MM) |

## Per-macro rate limits

The `limits` block prevents a macro from running too often. All fields are optional.

```json
{
  "max_per_minute": 4,
  "max_per_hour": 20,
  "max_per_day": 100,
  "cooldown_seconds": 15
}
```

Rate-limit history is stored in `$PHONECTL_HOME/macro_runs_history.json`.

## Phone verbs (action steps)

Each step with `type` in `PHONE_VERBS` is executed by `runtime.run_action`:

`tap`, `type`, `set_text`, `swipe`, `scroll_until`, `launch`, `key`, `intent`, `clipboard_read`, `clipboard_write`, `clipboard_clear`, `notification_reply`, `notification_dismiss`

**Target field** — same structure as CLI/MCP (`{ "i": N }`, `{ "selector": {...} }`, `{ "x": X, "y": Y }`).

Variable interpolation works in string values: `"${variable_name}"`.

## Control-flow steps

| Type | Key subfields | Description |
|---|---|---|
| `if` | `condition`, `then`, `else` | Branch |
| `switch` | `variable`, `cases` (object), `default` | Multi-branch |
| `for_each` | `in` (`"${var}"`), `as`, `do` | Iterate over a list variable |
| `loop` | `while`, `max_iterations`, `do` | Bounded loop |
| `retry` | `max_attempts`, `backoff_seconds`, `do` | Re-run transient failures with bounded backoff |
| `try` | `do`, `catch` (optional), `finally` (optional) | Exception handling |
| `race` | `branches` (list of step lists) | Run branches; first non-error wins |
| `confirm` | `message` | Pause and wait for user confirmation |
| `wait` | `seconds` | Sleep |
| `set` | `variable`, `value` | Set a runtime-scope variable |
| `stop` | `message` (optional) | End macro immediately |
| `audit_note` | `message` | Write a note to the audit log |

## Variable scopes (priority: runtime > macro > trigger > secret)

- **runtime** — set by `set` steps during execution
- **macro** — declared in `variables`
- **trigger** — injected by scheduler/event subscriptions (Phase 6.2)
- **secret** — injected from secret store; values are redacted in audit log

## CLI

```bash
phonectl macro validate path/to/macro.json   # lint only
phonectl macro run     path/to/macro.json     # execute
phonectl macro run     path/to/macro.json --yes  # skip confirm steps
phonectl macro status                         # list recent runs
phonectl macro cancel  <run_id>               # cancel (daemon only)
phonectl macro enable  path/to/macro.json     # register + enable a macro
phonectl macro disable <name>                 # disable a macro by name
phonectl macro list                           # list all registered macros and their enabled state
```

`enable`, `disable`, and `list` run in-process (they only touch the registry store; no daemon required). The `--json` flag is supported for all three.

## MCP tools

| Tool | Description |
|---|---|
| `phone_macro_validate` | Validate a macro document; returns `{valid, errors}` |
| `phone_macro_run` | Run a macro document; returns run envelope |
| `phone_macro_status` | List recent macro runs |

## Daemon routing

When `phonectl daemon` is running, `phonectl macro run` routes to `macro_run` RPC under the single-writer lock. The daemon also exposes `macro_cancel` to cancel an in-flight run by `run_id`.

## Audit trail

Every macro run appends a `macro_run` record to `runs.jsonl` with `run_id`, `macro_name`, `trigger`, `outcome`, `steps_run`, `started_at`, `ended_at`, and `cancelled`. Each action step's audit record includes `parent_task_id=run_id` for full lineage.
