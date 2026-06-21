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
| `STOP` | Kill-switch sentinel — create to disable all actions |

---

## Status

The observe-act-observe core (library + CLI) is implemented and unit-tested. The real-device connectivity proof (build-step-zero: pairing `adb` inside PRoot against `adbd` over the loopback) and the end-to-end smoke run are manual steps that require a physical Android 11+ phone with Wireless Debugging enabled. See [docs/integration-smoke.md](docs/integration-smoke.md) for the full procedure.

Features deferred to follow-on work: mDNS auto-discovery for silent reconnect after reboot, `phonectl setup` interactive wizard, guarded-package denylist, MCP server wrapper, and AccessibilityService APK backend.
