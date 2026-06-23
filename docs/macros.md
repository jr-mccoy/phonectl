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
| `actions` | yes | List of steps (may be empty) |

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
```

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
