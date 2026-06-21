# phonectl

phonectl is an Android computer-use bridge over ADB (no root) — observe the screen as structured JSON, act (tap, type, swipe, send key events, launch apps), then re-observe to confirm the action landed. It is designed to run inside a Termux + PRoot-Distro environment on the device itself, with `adb` as the only external dependency, giving an AI agent a tight observe-act-observe loop over any Android app without requiring device root.

---

## Install

### 1. Install adb

**In Termux (host):**
```bash
pkg install android-tools
```

**In a PRoot-Distro Debian/Ubuntu distro:**
```bash
apt-get update && apt-get install -y android-tools-adb
```

### 2. Install phonectl

```bash
# From the repo root (requires setuptools):
pip install -e .
```

---

## Pair and connect (Android 11+ Wireless Debugging)

This is a one-time pairing step. On the phone:

**Settings → Developer options → Wireless debugging → "Pair device with pairing code"**

Note the `IP:PORT` shown for pairing and the 6-digit code, then run:

```bash
# Step 1: pair (use the pairing port and code from the Wireless Debugging screen)
adb pair 127.0.0.1:<pairPort> <code>

# Step 2: connect (use the main Wireless Debugging port, not the pairing port)
adb connect 127.0.0.1:<connPort>

# Step 3: verify
phonectl doctor
# Expected: phonectl: connected (serial=127.0.0.1:<connPort>, state=device)
```

If `phonectl doctor` prints a guidance message instead of "connected", see the topology fallback in [docs/integration-smoke.md](docs/integration-smoke.md).

---

## Command reference

All subcommands connect to the device using the serial stored in `~/.config/phonectl/config.json` (override with `PHONECTL_HOME`).

### `observe`

Dump the current screen as a structured JSON snapshot: foreground app, screen dimensions, a hash of the element set, and a flat list of UI elements with indices, text, resource IDs, bounds, and click-targets.

```bash
phonectl observe
phonectl observe --screenshot                  # also capture a PNG
phonectl observe --screenshot-path /tmp/snap.png
```

### `tap`

Tap an element by its index from the last `observe` output (preferred — device-size-agnostic), or by raw coordinates.

```bash
phonectl tap --index 7
phonectl tap --xy 540 450
```

### `type`

Type text into the focused field.

```bash
phonectl type "hello world"
```

### `swipe`

Swipe from (X1, Y1) to (X2, Y2).

```bash
phonectl swipe 540 1600 540 400    # scroll up
```

### `key`

Send a key event. Friendly names accepted: `back`, `home`, `recents`, `enter`. Any raw Android keycode string also works.

```bash
phonectl key back
phonectl key home
phonectl key enter
phonectl key KEYCODE_VOLUME_UP
```

### `launch`

Start an app by package name using the monkey launcher-intent mechanism (`monkey -p <pkg> -c android.intent.category.LAUNCHER 1`). The package must expose a LAUNCHER activity.

```bash
phonectl launch com.android.settings
phonectl launch com.android.chrome
```

### `wait-for`

Re-observe on a short poll until a UI element matching `--text` or `--id` appears, or until `--timeout` seconds elapse. Requires exactly one of `--text` or `--id`.

```bash
phonectl wait-for --text "Network & internet" --timeout 8
phonectl wait-for --id "android:id/title" --timeout 5
```

Exit codes: `0` on match, `1` on timeout, `2` if neither `--text` nor `--id` is provided.

### `doctor`

Check device connectivity and print a one-line status.

```bash
phonectl doctor
# phonectl: connected (serial=127.0.0.1:PORT, state=device)
```

### Global flag

```bash
phonectl --version
```

---

## Safety

### Three action modes

The `mode` key in `~/.config/phonectl/config.json` (or `$PHONECTL_HOME/config.json`) controls how action verbs behave:

| Mode | Behaviour |
|---|---|
| `auto` | Acts immediately. Default when no mode is set. |
| `confirm` | Prints the intended action and refuses unless `--yes` is passed on the command line. Exit code `3` on refusal. |
| `dry-run` | Observes the screen, prints what would have been done, but does **not** inject any input and does **not** write to the audit log. |

Set the mode by editing `config.json`:
```json
{ "mode": "confirm" }
```

Then pass `--yes` to action verbs to permit execution in confirm mode:
```bash
phonectl tap --index 3 --yes
phonectl type "hello" --yes
```

### Audit log

Every executed action (tap, type, swipe, key, launch) is appended to `~/.config/phonectl/actions.jsonl` as a JSONL record containing the timestamp, verb, target, resulting foreground app, and screen hash. Dry-run actions are not logged.

### Single-writer runtime & audit

All mutating action verbs route through `runtime.run_action`, the single writer for UI changes. The funnel applies the kill switch and mode checks, serializes concurrent writers with a process-local lock, stamps each call with a `request_id`, executes the action, re-observes, and writes the audit record.

Action verbs accept `--json` to print the full structured result envelope:

```bash
phonectl tap --xy 100 200 --json
phonectl type "hello" --request-id req-123 --idempotency-key msg-1 --json
```

The action envelope includes `verb`, `target`, and `request_id`. A repeated `--idempotency-key` replays the first process-local envelope with `idempotent_replay: true` instead of executing the action again. Durable cross-process idempotency is deferred to the daemon runtime.

Single-writer control errors use stable codes:

| Code | Exit | Meaning |
|---|---:|---|
| `busy` | `1` | Another action holds the process-local writer lock; retry later. |
| `stopped` | `2` | The `STOP` kill-switch file is present. |
| `confirmation_required` | `3` | Confirm mode refused the action because `--yes` was not supplied. |

Set `audit_level` in `config.json` to control audit detail:

| Level | Behavior |
|---|---|
| `none` | Write no audit record. |
| `metadata` | Write timestamp, verb, request ID, resulting app, and hash only. |
| `redacted` | Default. Write metadata plus a redacted target. |
| `full` | Write the raw target. |

Redaction scrubs OTP-like numeric codes, email addresses, phone numbers, card-like numbers, and URL query secrets such as `token=`, `access_token=`, `code=`, and `key=`. Non-sensitive selectors such as `{"text": "Wi-Fi"}` are left unchanged.

Audit inspection commands:

```bash
phonectl audit tail --limit 20
phonectl audit export audit.json
phonectl audit export audit-full.json --no-redact
phonectl audit purge
```

### Kill switch

Create the file `~/.config/phonectl/STOP` (or `$PHONECTL_HOME/STOP`) to instantly refuse all action verbs regardless of mode:

```bash
touch ~/.config/phonectl/STOP    # engage
rm ~/.config/phonectl/STOP       # disengage
```

Any action verb while `STOP` is present exits with code `2` and prints:
```
phonectl: action refused (kill switch STOP present)
```

---

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success |
| `1` | Timeout (`wait-for`) or connection error |
| `2` | Kill switch active, or `wait-for` called without `--text`/`--id` |
| `3` | Confirm-mode refusal (action verb called without `--yes`) |


## Structured results & capabilities

`phonectl` now has a stable structured-result contract for JSON-capable surfaces. `phonectl observe --json` and `phonectl doctor --json` return an envelope with `ok: true`; typed platform errors return `ok: false` with actionable flags instead of tracebacks.

Successful envelope shape:

```json
{
  "ok": true,
  "capability": "ui.observe",
  "provider": "adb",
  "data": { "elements": [] }
}
```

Error envelope shape:

```json
{
  "ok": false,
  "error": {
    "code": "device_locked",
    "message": "device is locked, unlock it",
    "retryable": false,
    "requires_user": true,
    "user_action": "Unlock the phone manually."
  }
}
```

Stable error codes:

| Code | Retryable | Requires user | Meaning |
|---|---:|---:|---|
| `observe_failed` | true | false | Screen observation failed but may succeed later. |
| `device_locked` | false | true | The device is locked and needs manual unlock. |
| `stale_snapshot` | true | false | A stored UI snapshot no longer matches the screen. |
| `capability_unavailable` | false | true | The active provider cannot perform the requested capability. |
| `guarded_action` | false | true | Policy or a guardrail blocked the action. |
| `rate_limited` | true | false | Action rate limiting blocked the request temporarily. |
| `busy` | true | false | Another action holds the process-local writer lock. |
| `stopped` | false | true | The kill switch is active. |
| `confirmation_required` | false | true | The action requires explicit confirmation. |

Capability keys exposed by providers:

- `observe_ui_tree`
- `observe_screenshot`
- `act_tap`
- `act_type`
- `act_key`
- `launch_app`
- `send_intent`
- `read_notifications`
- `reply_notifications`
- `read_clipboard`
- `write_clipboard`
- `write_secure_settings`
- `persistent_events`
- `requires_adb`
- `requires_accessibility`
- `requires_notification_listener`

Examples:

```bash
phonectl observe --json
```

```json
{
  "ok": true,
  "capability": "ui.observe",
  "provider": "adb",
  "data": {
    "app": {"package": "com.android.settings", "activity": ".Settings"},
    "elements": []
  }
}
```

```bash
phonectl doctor --json
```

```json
{
  "ok": true,
  "provider": "adb",
  "data": {
    "connected": true,
    "serial": "127.0.0.1:PORT",
    "state": "device",
    "capabilities": {
      "observe_ui_tree": true,
      "observe_screenshot": true,
      "act_tap": true,
      "act_type": true,
      "act_key": true,
      "launch_app": true,
      "send_intent": true,
      "requires_adb": true
    }
  }
}
```

---

## Configuration

Config directory: `~/.config/phonectl/` (override: `PHONECTL_HOME` env var)

| File | Purpose |
|---|---|
| `config.json` | Device serial and mode (`auto` / `confirm` / `dry-run`) |
| `actions.jsonl` | Append-only audit log of executed actions |
| `config.json:audit_level` | Audit detail (`none`, `metadata`, `redacted`, or `full`) |
| `STOP` | Kill-switch sentinel — create to disable all actions |

---

## Status

The observe-act-observe core (library + CLI) is implemented and unit-tested. The real-device connectivity proof (build-step-zero: pairing `adb` inside PRoot against `adbd` over the loopback) and the end-to-end smoke run are manual steps that require a physical Android 11+ phone with Wireless Debugging enabled. See [docs/integration-smoke.md](docs/integration-smoke.md) for the full procedure.

Features deferred to follow-on work: mDNS auto-discovery for silent reconnect after reboot, `phonectl setup` interactive wizard, guarded-package denylist, MCP server wrapper, and AccessibilityService APK backend.

## Selector targeting and tree observation

`phonectl` supports durable selector-based targeting in addition to snapshot-local element indices and raw coordinates. Selectors are JSON objects whose present keys all match: `text`, `text_regex`, `content_desc`, `resource_id`, `class`, boolean element flags such as `clickable`, `enabled`, `checked`, `editable`, relation predicates `ancestor_text` and `sibling_text`, `bounds_near` (`[x1,y1,x2,y2]` center-in-box), and `nth_match` (zero-based pick after ranking).

Examples:

```bash
phonectl tap --text "Wi-Fi"
phonectl tap --id android:id/title --nth 1
phonectl tap --selector '{"text_regex":"^(Wi-?Fi|Bluetooth)$","clickable":true}'
phonectl observe --tree --relations
```

Use `--expected-hash HASH` on actions to prevent acting when the observed screen has changed. If the current hash differs, `phonectl` re-observes once and raises the typed `stale_snapshot` error unless `--stale-ok` is supplied, in which case it proceeds against the fresh snapshot.

## Resilience and connection recovery

`phonectl` is designed to survive common unattended-use failures without exposing raw Python tracebacks.

### Config keys

The config file (`~/.config/phonectl/config.json`, or `$PHONECTL_HOME/config.json`) supports these connection recovery keys:

| Key | Meaning |
|---|---|
| `serial` | Current ADB serial or Wireless Debugging `ip:port`. |
| `last_port` | Last-known-good Wireless Debugging `ip:port`; `ensure()` and `reconnect` retry it first. |
| `probe_ports` | Optional list of candidate Wireless Debugging ports for the bounded PRoot/Termux port-probe fallback. |

Example:

```json
{
  "serial": "127.0.0.1:5555",
  "last_port": "127.0.0.1:5555",
  "probe_ports": [40001, 40002, 40003]
}
```

### `reconnect`

Use `phonectl reconnect [port]` when Wireless Debugging rotated or dropped its connection:

```bash
phonectl reconnect 127.0.0.1:43210  # explicitly connect and persist this port
phonectl reconnect                   # layered recovery: last_port/serial, mDNS, probe_ports, shim seam
```

Without an explicit port, recovery tries the last-known-good address first, then `adb mdns services`, then any configured `probe_ports` on the same device IP. If every layer fails, it prints the normal setup guidance and exits nonzero.

### Lock-state and idle-state behavior

Snapshots now include structured lock-state fields:

```json
{
  "lock_state": "unlocked",
  "can_act": true,
  "recommended_user_action": null
}
```

The recognized lock-state values are `unlocked`, `locked_swipe_only`, `locked_secure`, `biometric_prompt`, `work_profile_locked`, and `unknown`; current ADB detection classifies `unlocked`, `locked_secure`, and `locked_swipe_only`.

If the device is locked, `observe --json` returns an error envelope with the same top-level fields, for example:

```json
{
  "ok": false,
  "error": { "code": "device_locked", "message": "Unlock the phone manually." },
  "lock_state": "locked_secure",
  "can_act": false,
  "recommended_user_action": "Unlock the phone manually."
}
```

Plain-text output stays one line, e.g. `phonectl: Unlock the phone manually.` If `uiautomator` reports the transient idle-state failure after retries, the typed observation error is `screen not idle — is it asleep or locked?` rather than an XML parse traceback.
