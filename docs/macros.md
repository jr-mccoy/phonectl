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

### Bus event name normalization

The event bus (`daemon/events.py`) publishes events with underscore names (`notification_posted`, `ui_changed`, etc.), while the macro trigger vocabulary uses dotted names (`notification.posted`, `ui.text_appears`, etc.) for readability. `TriggerManager.step()` normalizes bus names to dotted names before matching — you write dotted names in macro documents and the daemon handles translation transparently.

**`ui_changed` granularity caveat:** The bus emits a single `ui_changed` event for all UI changes (element appears/disappears, text changes, activity/app transitions). `TriggerManager` maps `ui_changed` to the full set of UI trigger types (`ui.element_appears`, `ui.text_appears`, `ui.element_disappears`, `activity.changed`, `app.opened`, `app.closed`). A macro whose trigger is any of these will receive the event; its `filters` (e.g. `text_regex`, `selector`) are responsible for discriminating the specific change. This means a macro with `{"type": "app.opened"}` and no package filter will fire on every UI change, not just app launches.

### Snapshot-dependent conditions are inert on auto-fired triggers (Plan 6.3 item)

Conditions that inspect `snapshot` or `device` sub-keys — `foreground_package`, `battery_min`, `selector_exists`, `screen_contains`, `risk_below`, `device_unlocked`, `charging`, `wifi_ssid` — evaluate against the data carried inside the triggering bus envelope. The Plan-5.2 bus envelopes do **not** yet carry `snapshot` or `device` sub-keys (those fields arrive as `{}` in the event context). As a result, these conditions are currently inert on auto-fired triggers: `foreground_package` always sees `app=None`, `battery_min` always sees `battery=0`, etc. Full envelope enrichment (attaching a live snapshot and device state to each bus event) is a Plan 6.3 item. Conditions that do not touch `snapshot`/`device` — `always`, `never`, `variable`, `time_window` — work correctly today.

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

## Progressive autonomy & memory

### Autonomy grants

By default every macro action prompts for confirmation (`confirm`). An autonomy grant allows the daemon to skip the prompt for actions up to a specified risk level:

```bash
phonectl autonomy grant reply --max-risk high     # allow auto-run up to high risk
phonectl autonomy revoke reply                     # revoke all grants for macro "reply"
phonectl autonomy list                             # show live (non-expired) grants
```

Grant records are appended to `autonomy.jsonl` in `PHONECTL_HOME`. Revoking adds a revoke record; `list` replays the ledger at the current time, filtering expired entries. The grant ledger stores only operator-supplied identifiers (macro name, max_risk, scope, timestamps) — no device-captured content — so it is not redacted; the redaction guarantee (D12) applies to the `memory/` stores, which hold device-derived metadata.

**Critical-risk actions are never fully autonomous.** A grant with `max_risk=critical` still requires a one-time human approval per action (`confirm`); it does not promote to `allow`. Any action whose risk classifier returns `critical` therefore always confirms.

**`require_confirm` macros.** If the macro document sets `policy.require_confirm: true`, the engine always confirms regardless of any grant.

### Memory stores

The memory layer is a set of narrow, redacted key-value stores for operational metadata only. Personal content (message text, contact names) is never stored. All values pass through the redactor (D12) before being written.

| Store | Purpose |
|---|---|
| `device` | Device metadata (model, OS version) |
| `apps` | Per-app metadata (version, locale) |
| `prefs` | User preferences (quiet hours, etc.) |
| `selectors` | Learned element selectors per app+version+locale |
| `failures` | Retryable failure counts per verb+outcome |

```bash
phonectl memory show prefs               # show the prefs store
phonectl memory show                     # show all stores
phonectl memory export                   # dump all stores to stdout
phonectl memory export backup.json       # write to file
phonectl memory delete prefs             # delete the prefs store
phonectl memory delete                   # delete all stores
```

The memory stores and capture hooks (`capture_selector`, `capture_failure`, `capture_from_runs`) exist and are tested; automatic population from run records is a planned follow-up — currently memory is populated only via explicit writes/RPC. The capture hooks are not yet wired into the daemon run-record path; activation requires action-record enrichment (`matched_i`, `app_version`, `locale` context) and is deferred with the selector-library work.
