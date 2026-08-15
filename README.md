# droidjig

[![Python tests](https://github.com/jumbodaddystack/phonectl/actions/workflows/python.yml/badge.svg)](https://github.com/jumbodaddystack/phonectl/actions/workflows/python.yml)
[![Android companion APK](https://github.com/jumbodaddystack/phonectl/actions/workflows/android.yml/badge.svg)](https://github.com/jumbodaddystack/phonectl/actions/workflows/android.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)

droidjig is an Android computer-use bridge over ADB (no root) — observe the screen as structured JSON, act (tap, type, swipe, send key events, launch apps), then re-observe to confirm the action landed. It is designed to run inside a Termux + PRoot-Distro environment on the device itself, with `adb` as the only external dependency, giving an AI agent a tight observe-act-observe loop over any Android app without requiring device root.

```bash
# Observe: the screen as indexed elements (each also carries content_desc, enabled,
# scrollable, editable, package, center, and the other element flags — trimmed here)
$ droidjig observe --json | jq '.data.elements[1:3] | map({i, text, id, clickable, bounds})'
[
  { "i": 1, "text": "Wi-Fi",     "id": "android:id/title", "clickable": true, "bounds": [44, 380, 1036, 520] },
  { "i": 2, "text": "Bluetooth", "id": "android:id/title", "clickable": true, "bounds": [44, 540, 1036, 680] }
]

# Act: by element, not by pixel
$ droidjig tap --index 1
$ droidjig tap --text "Wi-Fi"
$ droidjig tap --selector '{"text_regex":"^(Wi-?Fi|Bluetooth)$","clickable":true}'
```

**Why it's built this way.** Targeting elements instead of coordinates is what makes an agent
portable across screen sizes and ROMs. Re-observing after every action is how the loop knows the
action actually landed. And because the thing on the other end is an autonomous agent driving a
real phone, every action funnels through one choke-point (`runtime.run_action`) that applies the
mode gate, kill switch, risk classification, policy, rate limit, idempotency, and audit log — there
is no second path.

**Design notes:** [architecture invariants](docs/architecture.md) ·
[subsystem design docs](docs/design/) · [roadmap](docs/roadmap.md) ·
[adversarial security review](docs/adversarial-review-2026-07.md)

> **Safety.** This tool grants an AI agent real control of a real phone. The default action mode
> is `confirm` (every action asks first). Read [Safety](#safety) before switching to `auto`, and
> read the [security review](docs/adversarial-review-2026-07.md) before any unattended use — the
> project's honest position is that it is built for *supervised* agent use.

---

## Contents

- [Install](#install) · [Pair and connect](#pair-and-connect-android-11-wireless-debugging) · [Getting started](#getting-started-droidjig-setup) · [Diagnostics](#diagnostics)
- [Command reference](#command-reference) — [`observe`](#observe), [`tap`](#tap), [`type`](#type), [`swipe`](#swipe), [gestures](#gestures), [`key`](#key), [`launch`](#launch), [`clipboard`](#clipboard), [`device`](#device), [`tts`](#tts), [`intent`](#intent), [`packages`](#packages), [`wait-for`](#wait-for), [`doctor`](#doctor), [`mcp`](#mcp)
- [Safety](#safety) — [action modes](#three-action-modes), [audit log](#audit-log), [risk ledger & policy](#risk-ledger--policy), [kill switch](#kill-switch), [exit codes](#exit-codes)
- Providers — [Termux:API](#termuxapi-provider-optional), [AccessibilityService companion APK](#accessibilityservice-provider-companion-apk), [OCR](#ocr-provider-optional), [provider graph](#provider-graph)
- [Companion setup](#companion-setup) · [transport & trust controls](#companion-transport--trust-controls) · [notifications](#notifications)
- [Structured results & capabilities](#structured-results--capabilities) · [Configuration](#configuration) · [Status](#status)
- [Selector targeting](#selector-targeting-and-tree-observation) · [Resilience](#resilience-and-connection-recovery) · [Performance tuning](#performance-tuning) · [Structured extraction](#structured-extraction)
- [Daemon](#daemon) · [Macros](#macros) · [Development](#development) · [License](#license)

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

### 2. Install droidjig

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
droidjig doctor
# Expected: droidjig: connected (serial=127.0.0.1:<connPort>, state=device)
```

If `droidjig doctor` prints a guidance message instead of "connected", see the topology fallback in [docs/integration-smoke.md](docs/integration-smoke.md).

---

## Getting started: `droidjig setup`

`droidjig setup` is the recommended onboarding wizard. It detects whether `adb` is installed, guides Android 11+ Wireless Debugging pairing, connects to the device, verifies `adb get-state`, and persists the working serial plus the volatile Wireless Debugging connect port for later reconnect attempts.

If `adb` is missing in Termux, install it first:

```bash
pkg install android-tools
```

Run the wizard and answer the three prompts from the Wireless Debugging screen:

```bash
droidjig setup
# Pairing host:port: 127.0.0.1:<pairPort>
# 6-digit pairing code: <code>
# Connect host:port: 127.0.0.1:<connPort>
```

Re-running `droidjig setup` is idempotent: if the device is already connected, droidjig short-circuits with an "already connected" message and does not prompt again. Setup can also report provider modules:

```bash
droidjig setup adb
droidjig setup accessibility
droidjig setup notifications
droidjig setup termux-api
droidjig setup all
```

Each module report states the required permission, current availability, how to enable it, capabilities unlocked, and safety implications. `accessibility` and `notifications` are served by the companion APK; `termux-api` is optional and discovered from the local Termux:API commands.

## Diagnostics

`droidjig doctor` checks connectivity; `droidjig doctor --json` returns the structured result envelope with connection state and backend capabilities.

```bash
droidjig doctor
droidjig doctor --json
```

For support, write a redacted diagnostics bundle:

```bash
droidjig doctor --bundle /tmp/droidjig-diag.zip
```

The bundle contains `manifest.json`, `adb-version.txt`, and `adb-devices.txt`. The manifest includes config with secrets masked, capability status, device state, `adb version`, `adb devices -l`, mDNS results when available, host-shim status, and a metadata-only audit tail (`ts`/`verb`/`app`/`hash`).

---

## Command reference

All subcommands connect to the device using the serial stored in `~/.config/droidjig/config.json` (override with `DROIDJIG_HOME`).

### `observe`

Dump the current screen as a structured JSON snapshot: foreground app, screen dimensions, a hash of the element set, and a flat list of UI elements with indices, text, resource IDs, bounds, and click-targets.

```bash
droidjig observe
droidjig observe --screenshot                  # also capture a PNG
droidjig observe --screenshot-path /tmp/snap.png
```

### `tap`

Tap an element by its index from the last `observe` output (preferred — device-size-agnostic), or by raw coordinates.

```bash
droidjig tap --index 7
droidjig tap --xy 540 450
```

### `type`

Type text into the focused field.

```bash
droidjig type "hello world"
```

### `swipe`

Swipe from (X1, Y1) to (X2, Y2), or in a named direction with density-aware scaling.

```bash
droidjig swipe 540 1600 540 400          # coordinate form: scroll up
droidjig swipe up                        # named direction (full screen)
droidjig swipe down --within i=3         # scroll within container element 3
droidjig swipe left --distance-pct 0.7  # custom distance
```

### Gestures

High-level gesture verbs built on ADB's `input swipe` primitive.

**Named swipe** (density-aware — coordinates computed from `wm size`):
```bash
droidjig swipe up|down|left|right [--within i=N] [--distance-pct 0.5]
```

**Long-press** (zero-distance swipe held for `--duration-ms`):
```bash
droidjig long-press --i N [--duration-ms 1000]
droidjig long-press --x X --y Y
```

**Double-tap** (two taps separated by `--interval-ms`):
```bash
droidjig double-tap --i N [--interval-ms 100]
droidjig double-tap --x X --y Y
```

**Drag** (long-duration swipe — portable ADB drag primitive; `adb shell input draganddrop` is not universally available):
```bash
droidjig drag --x1 X --y1 Y --x2 X --y2 Y [--duration-ms 500]
```

**Fling** (velocity-scaled fast swipe):
```bash
droidjig fling up|down|left|right
```

**Scroll** (container-aware; reads element bounds from snapshot when `--within` is set):
```bash
droidjig scroll down [--within i=N]
droidjig scroll up --within i=5
```

**Scroll-until** (observe→scroll loop; returns the snapshot in which the target appeared, or the last snapshot if `--max` scrolls are exhausted):
```bash
droidjig scroll-until --text "Advanced" [--direction down] [--within i=N] [--max 10]
droidjig scroll-until --selector '{"resource_id":"com.example:id/item"}' --max 5
```

All gesture verbs route through `runtime.run_action` (kill switch, mode, audit, risk policy all apply). Use `--json` for structured output and `--yes` in confirm mode.

### `key`

Send a key event. Friendly names accepted: `back`, `home`, `recents`, `enter`. Any raw Android keycode string also works.

```bash
droidjig key back
droidjig key home
droidjig key enter
droidjig key KEYCODE_VOLUME_UP
```

### `launch`

Start an app by package name using the monkey launcher-intent mechanism (`monkey -p <pkg> -c android.intent.category.LAUNCHER 1`). The package must expose a LAUNCHER activity.

```bash
droidjig launch com.android.settings
droidjig launch com.android.chrome
```

### `clipboard`

Read, write, or clear the system clipboard.

```bash
droidjig clipboard read               # requires Termux:API (Plan 3.5)
droidjig clipboard write "hello"      # via ADB service call (Android 10+)
droidjig clipboard write "hello" --yes
droidjig clipboard clear --yes
```

**Note:** `clipboard read` is not available via plain ADB because the parcel-based read is ROM-specific and unreliable. It returns a `capability_unavailable` error with install instructions until Termux:API is configured (`droidjig setup termux-api`).

`clipboard write` and `clipboard clear` are mutating operations that route through `runtime.run_action` (audit log, risk policy, kill switch, mode gates all apply).

### `device`

Read device state via the Termux:API provider (requires `droidjig setup termux-api`).

```bash
droidjig device battery          # Battery percentage, status, health, temperature
droidjig device battery --json   # Structured result envelope
droidjig device wifi             # WiFi SSID, IP, BSSID, RSSI
droidjig device wifi --json
```

Returns `capability_unavailable` with install instructions if Termux:API is not configured.

### `tts`

Speak text via Android's TTS engine (requires `droidjig setup termux-api`).

```bash
droidjig tts speak "Hello, world"
droidjig tts speak "Bonjour" --language fr
droidjig tts speak "Fast" --rate 1.5
droidjig tts speak "Hello" --json   # Structured result envelope
```

TTS is fire-and-forget: the command returns as soon as the TTS engine accepts the request. The speech plays asynchronously. Returns `capability_unavailable` if Termux:API is not configured.

### `intent`

Start activities or send broadcasts via `am start` / `am broadcast`.

```bash
droidjig intent start --action android.intent.action.VIEW --data "geo:37.422,-122.084"
droidjig intent start --component com.android.settings/.wifi.WifiSettings --yes
droidjig intent broadcast com.example.MY_ACTION --yes
droidjig intent broadcast com.example.MY_ACTION --extra key=value --yes
```

Intent `start` and `broadcast` are **high-risk** operations (risk level `high`) and require `--yes` or explicit policy override. The risk ledger classifies both `intent_start` and `intent_broadcast` as `high_risk_verb`; an `intent start` whose action or data targets the dialer or SMS (`tel:`, `sms:`/`smsto:`, `ACTION_CALL`/`DIAL`/`SENDTO`) is classified **critical** and denied by default policy. Multiple `--extra K=V` pairs are supported (string extras only; typed extras are deferred).

### `packages`

List, inspect, launch, stop, or clear packages.

```bash
droidjig packages list                          # user-installed packages only
droidjig packages list --all                    # include system packages
droidjig packages resolve com.android.settings  # version, launch activity
droidjig packages launch com.android.settings   # same as `launch` verb
droidjig packages stop com.example.app --yes    # force-stop (high risk)
droidjig packages clear com.example.app --yes   # clear data (critical risk)
```

Risk levels:
- `packages list` / `packages resolve` — read-only; no risk gate.
- `packages launch` — same risk classification as the `launch` verb.
- `packages stop` — **high risk** (`high_risk_verb` signal); requires `--yes` or a `high: allow` policy override.
- `packages clear` — **critical risk** (`critical_verb` signal); requires `--yes` and is **denied by default policy** — override `risk_policy.critical` to `confirm` first.

### `wait-for`

Re-observe on a short poll until a UI element matching `--text` or `--id` appears, or until `--timeout` seconds elapse. Requires exactly one of `--text` or `--id`.

```bash
droidjig wait-for --text "Network & internet" --timeout 8
droidjig wait-for --id "android:id/title" --timeout 5
```

Exit codes: `0` on match, `1` on timeout, `2` if neither `--text` nor `--id` is provided.

### `doctor`

Check device connectivity, print structured JSON, or create a redacted diagnostics bundle.

```bash
droidjig doctor
droidjig doctor --json
droidjig doctor --bundle /tmp/droidjig-diag.zip
# droidjig: connected (serial=127.0.0.1:PORT, state=device)
```


### `mcp`

Launch the optional stdio MCP server so agents can call droidjig as native tools. The live transport needs the optional MCP SDK; handlers and tests remain stdlib-only.

```bash
pip install 'droidjig[mcp]'
droidjig mcp
```

Every MCP tool returns the same structured result envelope used by `--json` CLI actions. Agents should branch on `ok`, `error.code`, `requires_user`, `risk_level`, and `reasons` instead of parsing human text.

Tool catalog:

| Tool | Purpose | Key args |
|---|---|---|
| `phone_observe_ui` | Return the foreground UI snapshot. | `tree`, `relations`, `screenshot` |
| `phone_find` | Resolve a selector against a fresh observation. | `selector` |
| `phone_capabilities` | Report backend capability flags and summary text. | none |
| `phone_tap` | Tap by selector, index, or coordinates via `run_action`. | `selector`, `index`, `x`, `y`, `expected_hash`, `stale_ok`, `dry_run`, `confirm`, `reason`, `idempotency_key` |
| `phone_type` | Type into the focused field via `run_action`. | `text`, `dry_run`, `confirm`, `reason`, `idempotency_key` |
| `phone_swipe` | Swipe between points via `run_action`. | `x1`, `y1`, `x2`, `y2`, `dry_run`, `confirm` |
| `phone_key` | Send a key event via `run_action`. | `keycode`, `dry_run`, `confirm` |
| `phone_launch` | Launch a package via `run_action`. | `package`, `dry_run`, `confirm` |
| `phone_policy_explain` | Explain risk and policy before acting. | `verb`, `selector`, `index`, `x`, `y` |
| `phone_audit_query` | Read recent redacted audit entries. | `limit` |
| `phone_stop` | Engage the emergency stop. | none |
| `phone_clipboard_read` | Read clipboard text (requires Termux:API). | none |
| `phone_clipboard_write` | Write text to the clipboard. | `text`, `dry_run`, `confirm` |
| `phone_clipboard_clear` | Clear the clipboard. | `dry_run`, `confirm` |
| `phone_intent_start` | Start an activity via `am start`. | `action`, `data`, `component`, `extras`, `dry_run`, `confirm` |
| `phone_intent_broadcast` | Send a broadcast via `am broadcast`. | `action`, `extras`, `dry_run`, `confirm` |
| `phone_packages_list` | List installed packages. | `include_system` |
| `phone_packages_resolve` | Resolve package metadata (version, launch activity). | `package` |
| `phone_packages_launch` | Launch a package. | `package`, `dry_run`, `confirm` |
| `phone_packages_stop` | Force-stop a package (high risk). | `package`, `dry_run`, `confirm` |
| `phone_packages_clear` | Clear package data (critical risk; set `confirm=true`). | `package`, `confirm`, `dry_run` |
| `phone_named_swipe` | Swipe in a named direction with density-aware scaling. | `direction`, `distance_pct`, `ms`, `within_index`, `dry_run`, `confirm` |
| `phone_long_press` | Long-press by index, selector, or coordinates. | `index`, `selector`, `x`, `y`, `duration_ms`, `dry_run`, `confirm` |
| `phone_double_tap` | Double-tap by index, selector, or coordinates. | `index`, `selector`, `x`, `y`, `interval_ms`, `dry_run`, `confirm` |
| `phone_drag` | Drag from (x1,y1) to (x2,y2) via long-duration swipe. | `x1`, `y1`, `x2`, `y2`, `duration_ms`, `dry_run`, `confirm` |
| `phone_fling` | Fling in a direction with velocity-scaled speed. | `direction`, `dry_run`, `confirm` |
| `phone_scroll` | Scroll in a direction, optionally within a scrollable container. | `direction`, `within_index`, `distance_pct`, `dry_run`, `confirm` |
| `phone_scroll_until` | Scroll until text or selector appears or max_scrolls is exhausted. | `direction`, `text`, `selector`, `max_scrolls`, `within_index` |
| `phone_notifications_list` | List current notifications; each item includes `can_reply`/`can_dismiss` flags. | `package` |
| `phone_notifications_wait` | Poll until a matching notification appears or timeout elapses. | `package`, `title_contains`, `text_contains`, `timeout` |
| `phone_notifications_reply` | Reply to a notification via RemoteInput (**high-risk**; companion required). | `key`, `text`, `confirm`, `dry_run` |
| `phone_notifications_dismiss` | Dismiss a notification (companion required). | `key`, `confirm`, `dry_run` |
| `phone_ocr_screen` | OCR the current screen and return text regions with bounds and confidence. **Use only as a fallback** when `phone_observe_ui`/`phone_find` return nothing (canvas/WebView/game surfaces). Requires `tesseract` on PATH or the companion ML-Kit OCR provider. | `min_confidence` |
| `phone_extract_list` | Extract rows from a scrollable list container. | `container_index` |
| `phone_extract_form` | Extract form fields with associated labels. | none |
| `phone_get_focused_field` | Return the currently focused text field, or null. | none |
| `phone_find_text` | Find elements whose text matches a regex pattern (`re.search`). | `pattern` |
| `phone_macro_validate` | Validate a macro document; returns `{valid, errors}`. | `macro` |
| `phone_macro_run` | Run a declarative macro document; returns the run envelope. | `macro`, `yes` |
| `phone_macro_status` | List recent macro runs from `runs.jsonl`. | `limit` |

Example observe envelope:

```json
{"ok": true, "capability": "ui.observe", "provider": "adb", "data": {"elements": []}}
```

Example blocked action envelope:

```json
{
  "ok": false,
  "error": {"code": "confirmation_required", "requires_user": true},
  "verb": "tap",
  "risk_level": "high",
  "reasons": ["password_field"]
}
```

The action tools are thin frontends over `runtime.run_action`, so MCP and CLI use the same single-writer lock, kill switch, dry-run mode, confirmation policy, rate limits, re-observe-after-act behavior, and audit log.

There is deliberately **no `phone_resume` tool** (and no daemon `resume` RPC). Engaging the
kill switch (`phone_stop`) is agent-reachable, but *clearing* it is a human-only, out-of-band
action — a kill switch the agent can turn off is not a kill switch. Resume from the host with
`droidjig resume`, by removing the `STOP` sentinel, or from the companion notification/tile.

### Global flag

```bash
droidjig --version
```

---

## Safety

### Three action modes

The `mode` key in `~/.config/droidjig/config.json` (or `$DROIDJIG_HOME/config.json`) controls how action verbs behave:

| Mode | Behaviour |
|---|---|
| `auto` | Acts immediately. Opt-in: set it explicitly in `config.json`. |
| `confirm` | Prints the intended action and refuses unless `--yes` is passed on the command line. Exit code `3` on refusal. **Default when no mode is set.** |
| `dry-run` | Observes the screen, prints what would have been done, but does **not** inject any input and does **not** write to the audit log. |

Set the mode by editing `config.json`:
```json
{ "mode": "confirm" }
```

Then pass `--yes` to action verbs to permit execution in confirm mode:
```bash
droidjig tap --index 3 --yes
droidjig type "hello" --yes
```

### Audit log

Every executed action (tap, type, swipe, key, launch) is appended to `~/.config/droidjig/actions.jsonl` as a JSONL record containing the timestamp, verb, target, resulting foreground app, screen hash, and `outcome` (`ok` or `blocked`). Dry-run actions are not logged.

### Single-writer runtime & audit

All mutating action verbs route through `runtime.run_action`, the single writer for UI changes. The funnel applies the kill switch and mode checks, serializes concurrent writers — both within a process (thread lock) and across droidjig processes (an `flock` on `$DROIDJIG_HOME/action.lock`) — stamps each call with a `request_id`, executes the action, re-observes, and writes the audit record.

Action verbs accept `--json` to print the full structured result envelope:

```bash
droidjig tap --xy 100 200 --json
droidjig type "hello" --request-id req-123 --idempotency-key msg-1 --json
```

The action envelope includes `verb`, `target`, and `request_id`. A repeated `--idempotency-key` replays the first envelope with `idempotent_replay: true` instead of executing the action again. Replay envelopes persist to `$DROIDJIG_HOME/idempotency.json` (TTL `idempotency_ttl`, default 300 s), so deduplication holds across one-shot CLI invocations, not just within the daemon process.

Single-writer control errors use stable codes:

| Code | Exit | Meaning |
|---|---:|---|
| `busy` | `1` | Another action (in this or another droidjig process) holds the single-writer lock; retry later. |
| `stopped` | `2` | The `STOP` kill-switch file is present. |
| `confirmation_required` | `3` | Confirm mode refused the action because `--yes` was not supplied. |

### Risk ledger & policy

Before any mutating action executes, `runtime.run_action` observes the current screen and classifies the pending action as `low`, `medium`, `high`, or `critical`. The classifier reads the foreground package and parsed UI element metadata; it does not call adb directly.

| Signal | Level | Trigger |
|---|---|---|
| `guarded_package` | `high` | Foreground package starts with a configured guarded prefix. |
| `password_field` | `high` | A parsed element has `password: true`. |
| `payment_keyword` | `critical` | Screen text contains payment/purchase/bank/card wording. |
| `destructive_keyword` | `critical` | Screen text contains factory reset, wipe, delete account, or uninstall wording. |
| `install_keyword` | `high` | Screen text contains install or sideload wording. |
| `otp_like_content` | `medium` | Visible element text contains a 4-8 digit code. |
| `high_risk_verb` | `high` | Verb is `packages_stop`, `intent_start`, `intent_broadcast`, or `notifications_reply`. |
| `critical_verb` | `critical` | Verb is `packages_clear`. |
| `critical_intent` | `critical` | `intent_start` action/data targets the dialer or SMS (`tel:`, `sms:`/`smsto:`/`mms:`, `ACTION_CALL`/`DIAL`/`SENDTO`). |

Effective default policy:

```json
{
  "risk_policy": {
    "low": "allow",
    "medium": "allow",
    "high": "confirm",
    "critical": "deny"
  },
  "guarded_packages": [],
  "rate_limits": {
    "tap": 120,
    "type": 30,
    "swipe": 120,
    "key": 120,
    "launch": 20,
    "high_risk": 1,
    "global": 180
  }
}
```

The `risk_policy` values are `allow`, `confirm`, or `deny`. A risk-policy `confirm` returns `confirmation_required` unless the action is re-run with `--yes`; `deny` returns `guarded_action`. Successful action envelopes and policy/rate failures include `risk_level` and `reasons`.

Rate limits are bucketed sliding windows over the last minute. Every allowed action counts against `global` and its verb bucket; `high` and `critical` actions also count against `high_risk`. Rate state is stored in `$DROIDJIG_HOME/ratelimit.json`. A limit breach returns `rate_limited` with `bucket`.

Use `policy explain` to inspect a decision before acting:

```bash
droidjig policy explain --verb tap --text "Pay now" --json
```

```json
{
  "risk_level": "critical",
  "reasons": [{"signal": "payment_keyword", "detail": "screen text matches payment_keyword"}],
  "decision": "deny",
  "recommended_action": "blocked by policy; override risk_policy to permit"
}
```

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
droidjig audit tail --limit 20
droidjig audit export audit.json
droidjig audit export audit-full.json --no-redact
droidjig audit purge
```

### Kill switch

Engage the kill switch to instantly refuse all action verbs regardless of mode. Any of these work:

```bash
droidjig stop                    # engage (host CLI)
touch ~/.config/droidjig/STOP    # engage (or create the sentinel directly)
```

Clearing it is a **human-only, out-of-band** step — there is no agent-facing resume (no
`phone_resume` MCP tool, no daemon `resume` RPC), so an agent cannot turn its own brakes off:

```bash
droidjig resume                  # disengage (host CLI, human action)
rm ~/.config/droidjig/STOP       # or remove the sentinel directly
```

`droidjig stop`/`resume` are host commands; the agent's only reachable control is engaging
`STOP` (via `phone_stop` or the daemon `stop` RPC). Any action verb while `STOP` is present
exits with code `2` and prints:
```
droidjig: action refused (kill switch STOP present)
```

---

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success |
| `1` | Timeout (`wait-for`), connection error, policy denial, rate limit, or busy writer |
| `2` | Kill switch active, or `wait-for` called without `--text`/`--id` |
| `3` | Confirm-mode refusal (action verb called without `--yes`) |
| `4` | Internal error — a bug in droidjig, not a refusal. Reported as an `internal_error` envelope; set `DROIDJIG_DEBUG=1` to re-raise and get the traceback |
| `130` | Interrupted with Ctrl-C (128 + SIGINT) |

Codes `1`–`3` mean droidjig worked and declined; `4` means droidjig itself broke. A closed
pipe (`droidjig observe --json | head`) exits `0`.


## Termux:API provider (optional)

The `TermuxApiProvider` is an optional second provider that activates automatically when `termux-battery-status` is found on `PATH`. No configuration is required — droidjig detects it at startup.

### Install

```bash
# 1. Install the Termux:API companion app from F-Droid or the Termux add-ons page
# 2. In Termux, install the CLI tools:
pkg install termux-api
# 3. On Android, grant Termux:API the permissions it requests (battery, clipboard, WiFi, etc.)
droidjig setup termux-api   # verify detection and show capability status
```

### What it enables

Once installed, the provider registers itself with the following capabilities:

| Capability | Termux:API | ADB |
|---|:---:|:---:|
| `read_clipboard` | ✓ | ✗ (unreliable) |
| `write_clipboard` | ✓ | ✓ |
| `device_battery` | ✓ | ✗ |
| `device_wifi_info` | ✓ | ✗ |
| `tts_speak` | ✓ | ✗ |

**Clipboard read upgrade:** `droidjig clipboard read` returns `capability_unavailable` without Termux:API because ADB clipboard reading is ROM-specific and unreliable on Android 10+. Once Termux:API is installed, `clipboard read` upgrades automatically — no configuration change needed.

**New verbs:** `droidjig device battery`, `droidjig device wifi`, and `droidjig tts speak TEXT` are only available when Termux:API is present. All three return structured result envelopes with `--json`.

### Provider priority

`TermuxApiProvider` is prepended to the provider registry ahead of `AdbBackend`, so it takes priority for any capability it supports. `AdbBackend` handles everything else (UI observation, tap, swipe, launch, etc.).

---

## AccessibilityService provider (companion APK)

`AccessibilityProvider` is an optional third provider that talks to a companion Android
AccessibilityService APK over a local transport. When the companion is connected, it wins over ADB
for `observe_ui_tree` and all `act_*` capabilities, providing a richer, lower-latency surface.

**What it unlocks:**

| Capability | AccessibilityProvider | Termux:API | ADB |
|---|:---:|:---:|:---:|
| `observe_ui_tree` | ✓ (native JSON) | ✗ | ✓ (uiautomator XML) |
| `observe_ui_native` | ✓ | ✗ | ✗ |
| `observe_ui_events` | ✓ | ✗ | ✗ |
| `act_tap` | ✓ (GestureDescription) | ✗ | ✓ (input tap) |
| `act_type` | ✓ (ACTION_SET_TEXT) | ✗ | ✓ (input text) |
| `act_key` | ✓ | ✗ | ✓ |
| `act_set_text_native` | ✓ | ✗ | ✗ |
| `act_gesture_native` | ✓ | ✗ | ✗ |
| `act_semantic_action` | ✓ | ✗ | ✗ |
| `launch_app` | ✓ | ✗ | ✓ |

**Compatibility mode:** `ui_dump()` still returns uiautomator-compatible XML (converted by
`native_tree.to_compat_xml`), so element index `i` and all selectors work identically across
providers. Existing code that uses index-based targeting is unaffected.

**Optional:** absent the companion APK, droidjig is ADB-first and completely unchanged.
`_make_accessibility_provider()` returns `None` by default until Plan 4.3 supplies a
`SocketTransport`; at that point the provider activates automatically when the companion is
reachable.

**Semantic node actions** let the agent click, long-click, scroll, expand, collapse, or dismiss UI
elements by accessibility node ID — bypassing coordinate-based tapping entirely:

```python
provider.semantic_action("node_id", "scroll_forward")
provider.set_text_native("node_id", "search query")  # ACTION_SET_TEXT, IME-independent
```

Node ids are **bound to the observation they came from**: `observe_native` returns a
tree-`generation` token, the provider echoes it on `set_text`/`semantic`, and the companion
refuses the request with `stale_generation` (surfaced as `stale_snapshot`) when the tree changed
in between — re-observe and retry. A node id that matches more than one node (list rows often
share a `viewIdResourceName`) is refused with `ambiguous_node_id` rather than silently acting on
the first match.

**UI event stream** (cursor-based polling):

```python
out = provider.poll_events(since=0)    # {"events": [...], "cursor": 5}
out = provider.poll_events(since=5)    # only events after cursor 5
```

**Design spec:** `android/accessibility-companion/SPEC.md` describes the companion APK's service
surface, message contract, permissions, and error codes.

---

## Companion setup

`droidjig companion setup` is the one-command bring-up for the companion APK: install the APK,
enable the AccessibilityService, grant `POST_NOTIFICATIONS`, acquire the pairing token, start the
socket server, and verify the connection with an authenticated handshake. It's **idempotent** —
each step checks whether it's already done and skips if so, so re-running it is safe.

```bash
droidjig companion setup                                # auto-detects the newest app-debug.apk
droidjig companion setup --apk /path/to/app-debug.apk    # use an explicit APK path
droidjig companion setup --yes                           # non-interactive: accept the grant/start prompts
droidjig companion setup --json                          # structured step-by-step result envelope
```

| Command | What it does |
|---|---|
| `droidjig companion setup [--apk PATH] [--yes] [--json]` | Installs → enables AccessibilityService → grants notifications → pairs token → starts server → verifies |
| `droidjig companion status [--json]` | Read-only report: `installed`, `accessibility`, `socket`, `token_paired` |

When `--apk` is omitted, droidjig auto-detects the newest `app-debug.apk` under `~/Download`,
`/sdcard/Download`, or `/storage/emulated/0/Download`; if none is found it exits `2` and asks for
`--apk PATH`. Each run prints one line per step (`install`, `accessibility`, `notifications`,
`token`, `server`, `verify`) with a `skipped`/`done`/`failed` status.

The `accessibility` and `server` steps grant or start something on-device, so — same safe-by-default
posture as every other mutating command — they print what they're about to do first and require
`--yes`, or an interactive `y/N` confirmation, before proceeding.

**Token acquisition:** on a debug build, the token is read automatically off-device via
`adb shell run-as com.droidjig.companion cat shared_prefs/droidjig_companion.xml` — no prompt
needed. On other builds `run-as` doesn't work for a release-signed package, so droidjig opens the
companion's Pairing screen instead and prompts you to paste the token shown there.

**The one manual step `setup` can't automate:** enabling **Notification access**
(`NotificationListenerService`) for the companion app — there is no `adb`/secure-settings
equivalent for this toggle. `companion setup` prints a reminder to open the companion app and tap
**Notification access** yourself; everything else in the flow is automated.

```bash
droidjig companion status --json
# {
#   "installed": true,
#   "accessibility": true,
#   "socket": true,
#   "token_paired": true
# }
```

### `config get` / `config set`

Typed access to `~/.config/droidjig/config.json` keys (`companion_port`, `companion_host`,
`companion_token`, `companion_timeout`, and the rest of the config defaults table):

```bash
droidjig config get companion_port
droidjig config get companion_port --json
droidjig config set companion_port 8765
droidjig config set companion_host 127.0.0.1
```

`config get` returns the raw value (`null` if unset). `config set` validates the key against the
known config defaults and coerces the value to that key's declared type (bool/int/float/str),
exiting `2` with `unknown config key` for anything not in the defaults table.

---

## Companion transport & trust controls

The companion APK communicates with droidjig over a **loopback TCP socket** — a newline-delimited
JSON protocol running on `127.0.0.1` only. Configure the port droidjig connects to:

```json
// ~/.config/droidjig/config.json
{
  "companion_port": 8765,
  "companion_host": "127.0.0.1",
  "companion_token": "<paste from the companion app>",
  "companion_timeout": 2.0
}
```

`companion_port` defaults to `null` (unset). When unset, `_make_accessibility_provider()` and
`_make_notifications_provider()` skip the transport and the build stays ADB-first — no companion
required. When set, droidjig pings the companion on startup; if it does not respond, the providers
are omitted silently.

### Pairing: loopback is not a security boundary on Android

The companion APK server binds `127.0.0.1` only — never `0.0.0.0` — and `SocketTransport` rejects
any `companion_host` that is not `127.0.0.1`, `localhost`, or `::1`. **But loopback is not an app
boundary on Android:** any installed app with `INTERNET` permission can `connect()` to a loopback
port opened by another app. Binding `127.0.0.1` alone does **not** keep other local apps out.

So the companion requires a **shared-secret token** on every request except the `ping` liveness
probe. Pair it once:

1. Open the companion app → **Pairing** → copy the **Companion token**.
2. Put it in `~/.config/droidjig/config.json` as `companion_token` (above).

`SocketTransport` stamps the token onto every request; the companion refuses any action, read, or
handshake that lacks it with an `unauthorized` error. Without a matching token the companion is
unusable — a companion with a configured port but no paired token reports `reachable=false`.

The token defends the companion from being *driven* by other apps. It does **not** by itself stop
a hostile app that binds the fixed default port `8765` *first* from impersonating the companion to
droidjig (the client would send its token to the imposter). If that matters in your threat model,
set a non-default `companion_port` and start the companion before any untrusted app.

### `droidjig trust status`

Inspect the current handshake from the companion:

```bash
droidjig trust status
droidjig trust status --json
```

```json
{
  "ok": true,
  "capability": "trust.status",
  "data": {
    "reachable": true,
    "version": 1,
    "stopped": false,
    "capabilities": {
      "observe_ui_native": true,
      "act_gesture_native": true,
      "act_set_text_native": false
    }
  }
}
```

When `companion_port` is not configured, `reachable` is `false` and `capabilities` is `{}`.

### Per-capability toggles

The companion APK exposes a per-capability toggle UI. The user enables or disables each capability
(e.g. `act_set_text_native`) in the APK settings screen. The enabled set is returned by
`handshake.capabilities` and intersected with each provider's technically-supported capabilities by
`trust.gate_capabilities`. A disabled toggle removes the grant from the provider graph; the registry
falls back to ADB or returns `CapabilityUnavailableError` for that capability.

Keys absent from the companion's `capabilities` map default to **disabled** — a capability the
handshake did not explicitly affirm is not exercised, and the toggle set never invents
capabilities the provider does not support.

### Guarded apps

Packages on the companion's guarded list are protected on the device side for **both actions and
observation**: gestures, key events, text entry, semantic actions, and `launch` are refused with
`guarded_action`; `observe_native`, `screencap`, and `ocr_screen` refuse when a guarded app is in
the foreground (guarded windows are also dropped from split-screen observations); the UI event
stream and `notifications_list` filter guarded packages out; and `notifications_reply`/`dismiss`
refuse notifications from guarded apps. Screenshots additionally only ever land under the
companion's own app storage — a client-supplied output path outside it is refused
(`path_rejected`).

### Emergency stop

The companion APK's persistent "Stop droidjig" notification and Quick-Settings tile set a
`stopped=true` flag in the `handshake` response. This flag is registered as an `extra_check` in
`audit.kill_switch_active`. When either the `STOP` sentinel file **or** `stopped=true` is active,
**all** action verbs (CLI, MCP, and later daemon) are blocked immediately with exit code `2`.

The precedence rule: **file sentinel OR companion stop flag blocks**. The file kill-switch is the
hard guarantee (survives APK crashes); the companion flag is the low-latency path for the running
session. The companion check **fails closed**: when a companion is configured
(`companion_port` set) but unreachable or erroring at the moment an action is gated, the action is
refused as `stopped` rather than silently proceeding. If the companion is intentionally offline,
unset `companion_port` (or fix connectivity) to act over ADB alone — a setup with no companion
configured never consults this check.

The companion also enforces its stop **on-device**: while `stopped=true`, the companion's own
dispatcher refuses every method except `ping` (liveness) and `handshake` (how the stop is
observed) with a `stopped` error. A direct socket client — anything that bypasses the droidjig
Python layer entirely — cannot act through the companion while it is stopped.

```bash
# File-based kill switch (always available, no companion needed)
touch ~/.config/droidjig/STOP    # engage
rm ~/.config/droidjig/STOP       # disengage

# Companion-based stop (requires APK running and connected)
# Tap "Stop droidjig" in the persistent notification or the Quick-Settings tile
```

**Design spec:** `android/foreground-service/SPEC.md` covers the foreground service, loopback TCP
server, persistent stop notification, Quick-Settings tile, per-capability toggle UI, and trust
guarantees.

---

## Notifications

droidjig treats notifications as a **first-class provider**, not UI-scraping. The `NotificationsProvider` exposes four capabilities with per-notification `can_reply`/`can_dismiss` flags derived from each notification's actions and `RemoteInput`.

### Source precedence

| Source | `list` | `wait` | `reply` | `dismiss` |
|---|:---:|:---:|:---:|:---:|
| Companion APK (`NotificationListenerService`) | ✓ | ✓ | ✓ | ✓ |
| Termux:API (`termux-notification-list`) | ✓ | ✓ (poll) | ✗ | ✗ |
| Neither | ✗ | ✗ | ✗ | ✗ |

When neither source is present, all notification verbs return a `capability_unavailable` envelope with setup instructions.

### Notification shape

Each notification item in the `list` result has:

```json
{
  "key": "0|com.msg|42|tag|10123",
  "package": "com.msg",
  "title": "Alice",
  "text": "see you at 6?",
  "category": "msg",
  "post_time": 1718900000000,
  "actions": ["Reply", "Mark read"],
  "can_reply": true,
  "can_dismiss": true
}
```

`can_reply` is `true` only when a companion action has a `RemoteInput` (i.e. the notification exposes a direct-reply field). The Termux:API path always reports `can_reply=false, can_dismiss=false`.

### CLI verbs

```bash
droidjig notifications list                          # list all current notifications
droidjig notifications list --package com.msg        # filter by package
droidjig notifications list --json                   # structured result envelope

droidjig notifications wait --package com.msg --timeout 30  # poll until match
droidjig notifications wait --title-contains "Alice" --json

droidjig notifications reply KEY "on my way" --yes  # send RemoteInput reply (high-risk)
droidjig notifications reply KEY "ok" --json --yes

droidjig notifications dismiss KEY --yes             # dismiss one notification
droidjig notifications dismiss KEY --json --yes
```

`KEY` is the opaque `key` field from a `notifications list` item.

### Risk policy

| Verb | Risk level | Reason |
|---|---|---|
| `notifications_list` | read-only; not gated | |
| `notifications_wait` | read-only; not gated | |
| `notifications_reply` | **high** | Sends visible content into arbitrary apps |
| `notifications_dismiss` | low (default) | Removes a notification |

`notifications_reply` is a `high_risk_verb` — it requires `--yes` in confirm mode or a `high: allow` policy override. In the default policy it triggers confirmation. Use `droidjig policy explain --verb notifications_reply` to inspect before acting.

---

## Provider graph

`build_runtime()` returns a `ProviderRegistry` that wraps one or more `Backend`-conforming providers in priority order. At minimum the registry holds a single `AdbBackend`; `TermuxApiProvider`, `AccessibilityProvider`, `NotificationsProvider`, and `OcrProvider` are prepended when their underlying service is discovered at runtime.

### How the registry works

The registry satisfies the `Backend` Protocol via explicit delegation methods. Each method calls `_require(cap_key)`, which finds the first provider with that capability set to `True`, records its class name in `_last_used`, and delegates. If no provider has the capability, `CapabilityUnavailableError` is raised.

```python
# Query which provider handles a capability
registry.for_capability("act_tap")         # → AdbBackend instance (or None)
registry.capabilities()                    # → merged bool dict
registry.capabilities_by_provider()        # → [{"provider": "AdbBackend", "caps": {...}}, ...]
```

`capabilities_by_provider()` shape:

```json
[
  {
    "provider": "AdbBackend",
    "caps": {
      "observe_ui_tree": true,
      "observe_screenshot": true,
      "act_tap": true,
      "act_type": true,
      "act_key": true,
      "launch_app": true,
      "send_intent": true,
      "requires_adb": true,
      "read_notifications": false,
      "read_clipboard": false
    }
  }
]
```

### Provider priority

Priority order is positional: the first provider in the list wins for each capability. Adding a higher-priority provider means prepending it to the list in `build_runtime()` with no other changes required:

```python
registry = ProviderRegistry([AccessibilityProvider(...), AdbBackend(...)])
# AccessibilityService wins for observe_ui_tree; ADB handles everything else.
```

### `provider` field in result envelopes

Every result envelope from `run_action` and `observe --json` includes a `provider` field reflecting the class name of the provider that handled the last delegation call:

```json
{"ok": true, "capability": "ui.tap", "provider": "AdbBackend", "data": {...}}
```

When the backend is a bare `AdbBackend` (not wrapped in a registry), `provider` falls back to `"adb"`.

---

## Structured results & capabilities

`droidjig` now has a stable structured-result contract for JSON-capable surfaces. `droidjig observe --json` and `droidjig doctor --json` return an envelope with `ok: true`; typed platform errors return `ok: false` with actionable flags instead of tracebacks.

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
| `busy` | true | false | Another action (in this or another droidjig process) holds the single-writer lock. |
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
- `read_clipboard` — ADB: `false`; set to `true` by Termux:API provider (Plan 3.5)
- `write_clipboard` — ADB: `true`
- `write_secure_settings`
- `persistent_events`
- `requires_adb`
- `requires_accessibility`
- `requires_notification_listener`
- `packages_list` — ADB: `true`
- `packages_stop` — ADB: `true`
- `packages_clear` — ADB: `true`
- `intent_start` — ADB: `true`
- `intent_broadcast` — ADB: `true`
- `device_battery` — ADB: `false`; Termux:API: `true`
- `device_wifi_info` — ADB: `false`; Termux:API: `true`
- `tts_speak` — ADB: `false`; Termux:API: `true`
- `observe_notifications` — companion: `true`; Termux:API: `true` (list-only); ADB: `false`
- `notifications_wait` — companion: `true`; Termux:API: `true` (poll-only); ADB: `false`
- `notifications_reply` — companion: `true`; Termux:API: `false`; ADB: `false`
- `notifications_dismiss` — companion: `true`; Termux:API: `false`; ADB: `false`
- `observe_ocr` — OcrProvider (Tesseract on PATH): `true`; OcrProvider (companion ML-Kit): `true`; all others: `false`

Examples:

```bash
droidjig observe --json
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
droidjig doctor --json
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

Config directory: `~/.config/droidjig/` (override: `DROIDJIG_HOME` env var)

| File | Purpose |
|---|---|
| `config.json` | Device serial, mode, audit level, risk policy, guarded packages, and rate limits |
| `actions.jsonl` | Append-only audit log of executed and blocked actions |
| `ratelimit.json` | Recent per-bucket action timestamps for sliding-window rate limits |
| `config.json:audit_level` | Audit detail (`none`, `metadata`, `redacted`, or `full`) |
| `STOP` | Kill-switch sentinel — create to disable all actions |

---

## Status

**Shipped and unit-tested (836 tests):** structured results and capabilities; selector and tree
observation; resilience and the setup wizard; the `run_action` single-writer funnel with risk
policy and audit v2; the provider/capability graph (clipboard, intents, packages, gestures,
extraction, Termux:API); the companion APK and its event providers (accessibility,
notifications, OCR); the daemon and event runtime; and the macro engine with progressive
autonomy. Core behavior is validated on a real Samsung Galaxy S25 Ultra over Wireless Debugging.

**Next:** a Shizuku provider, to reduce the dependence on an ADB connection.

**Caveats worth knowing.** Unit tests run against injected fakes — they prove the logic, not the
device topology. The real-device connectivity proof and the end-to-end smoke matrix are manual
steps needing a physical Android 11+ phone with Wireless Debugging enabled; see
[docs/integration-smoke.md](docs/integration-smoke.md). The Android instrumented tests
(`connectedAndroidTest`) do not run in CI. Phase status lives in [docs/roadmap.md](docs/roadmap.md);
security posture and remaining risk in
[docs/adversarial-review-2026-07.md](docs/adversarial-review-2026-07.md).

## Selector targeting and tree observation

`droidjig` supports durable selector-based targeting in addition to snapshot-local element indices and raw coordinates. Selectors are JSON objects whose present keys all match: `text`, `text_regex`, `content_desc`, `resource_id`, `class`, boolean element flags such as `clickable`, `enabled`, `checked`, `editable`, relation predicates `ancestor_text` and `sibling_text`, `bounds_near` (`[x1,y1,x2,y2]` center-in-box), and `nth_match` (zero-based pick after ranking).

Examples:

```bash
droidjig tap --text "Wi-Fi"
droidjig tap --id android:id/title --nth 1
droidjig tap --selector '{"text_regex":"^(Wi-?Fi|Bluetooth)$","clickable":true}'
droidjig observe --tree --relations
```

Use `--expected-hash HASH` on actions to prevent acting when the observed screen has changed. If the current hash differs, `droidjig` re-observes once and raises the typed `stale_snapshot` error unless `--stale-ok` is supplied, in which case it proceeds against the fresh snapshot.

## Resilience and connection recovery

`droidjig` is designed to survive common unattended-use failures without exposing raw Python tracebacks.

### Config keys

The config file (`~/.config/droidjig/config.json`, or `$DROIDJIG_HOME/config.json`) supports these connection recovery keys:

| Key | Meaning |
|---|---|
| `serial` | Current ADB serial or Wireless Debugging `ip:port`. |
| `last_port` | Last-known-good Wireless Debugging `ip:port`; `ensure()` and `reconnect` retry it first. |
| `probe_ports` | Optional list of candidate Wireless Debugging ports for the bounded PRoot/Termux port-probe fallback. |
| `ensure_ttl` | Seconds a successful connection check stays trusted before `ensure()` re-runs `adb get-state` (default `5.0`; `0` re-checks on every call). A link that drops inside the window surfaces as the next command's failure and self-heals on the first check after expiry. |

Example:

```json
{
  "serial": "127.0.0.1:5555",
  "last_port": "127.0.0.1:5555",
  "probe_ports": [40001, 40002, 40003]
}
```

### `reconnect`

Use `droidjig reconnect [port]` when Wireless Debugging rotated or dropped its connection:

```bash
droidjig reconnect 127.0.0.1:43210  # explicitly connect and persist this port
droidjig reconnect                   # layered recovery: last_port/serial, mDNS, probe_ports, shim seam
```

Without an explicit port, recovery tries the last-known-good address first, then `adb mdns services`, then any configured `probe_ports` on the same device IP. If every layer fails, it prints the normal setup guidance and exits nonzero.

## Performance tuning

Over Wireless Debugging the adb round trip dominates every operation, so `droidjig` spends round trips sparingly:

- **Combined observe dump (automatic).** `observe` fetches the UI hierarchy and the window state (focused app + lock screen) in a **single** adb call, with the `dumpsys window` output filtered device-side down to the handful of lines the parsers read. If the device shell can't serve the combined form, `droidjig` transparently falls back to separate calls.
- **`wm_size` caching (automatic).** The physical screen size is cached for 300 s and invalidated on serial change.
- **`ensure_ttl`** (default `5.0`): how long a successful connection check stays trusted before the next `adb get-state`. Set `0` to re-check before every command.
- **`action_observe_ttl`** (default `0` = off): opt-in for agent/daemon loops. When set (e.g. `1.0`), an action skips its pre-action observe if the session already holds a snapshot younger than the window — typically the previous action's post-act observe. Policy, risk, and rate limiting still run against that snapshot, and every action still **re-observes after acting**. Leave at `0` for one-shot CLI use (each CLI process starts with no snapshot, so it always observes anyway) or whenever you want policy to always see a freshly fetched screen:

```bash
droidjig config set action_observe_ttl 1.0   # daemon-driven agent loops
droidjig config set ensure_ttl 0             # most conservative: re-check the link every command
```

With the daemon warm, a typical `tap` costs ~4 adb round trips (connection check + pre-observe + input + post-observe), dropping to ~2 in-window with `action_observe_ttl` set.

### Companion-path performance

With the companion APK paired, the fast path gets faster still — and it's all automatic:

- **Persistent connections.** The companion server keeps connections open; the transport now reuses one TCP connection across requests instead of connect-per-RPC, with a preemptive reconnect before the server's idle timeout. Lost-response ambiguity is handled conservatively: read-only calls are retried on a fresh connection, gestures and `set_text` are never blindly replayed.
- **Cached liveness.** Provider capability scans no longer ping the companion per delegated call; a 5 s liveness cache is refreshed by every successful RPC and invalidated by any transport failure.
- **One tree serialization per observe.** A companion observe used to fetch the native tree twice (once for the elements, once for the screen size); it's now a single `observe_native` RPC.
- **Lock/focus truth stays cheap.** The companion can't see the keyguard or the focused-window record, so the registry fills that in from adb's brief (device-side-filtered) window dump — one small adb call rather than a full `dumpsys window`, and the snapshot's `app.package` stays accurate for the `guarded_packages` policy signal.

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

Plain-text output stays one line, e.g. `droidjig: Unlock the phone manually.` If `uiautomator` reports the transient idle-state failure after retries, the typed observation error is `screen not idle — is it asleep or locked?` rather than an XML parse traceback.

## Structured extraction

Read structured data from the UI — enumerate RecyclerView rows, extract form labels and values, locate the focused text field, filter elements by text pattern, or extract all visible text within a region.

```bash
# Extract all rows from a scrollable list (auto-detects the container)
droidjig extract list --json

# Extract rows from a specific container by element index
droidjig extract list --container-i 3 --json

# Extract form fields with associated labels
droidjig extract form --json

# Find elements whose text matches a regex (UI tree)
droidjig find --text-regex "Total|Balance" --json

# Find text by OCR when the UI tree returns nothing (canvas/game/WebView surfaces)
droidjig find --ocr-text "Balance" --json

# Get the currently focused text field
droidjig get focused-field --json

# Get all elements overlapping a screen region (x1 y1 x2 y2)
droidjig get text-in-region --bounds 0 0 1080 400 --json
```

`extract form` automatically requests the UI relations graph to resolve label–field associations via sibling proximity; if no relations are available it falls back to Y-coordinate overlap. Password fields have their value replaced with `[redacted]` in all outputs.

`find --text-regex` searches element text only (uses `re.search`, so no anchoring needed). For `content-desc` matching use `--selector '{"content_desc": "..."}'` with `observe` or `tap`.

`find --ocr-text REGEX` is the escape hatch for surfaces the UI tree can't see: it OCR-scans the screen and filters regions whose text matches the regex. Requires the OCR provider (see below).

---

## OCR provider (optional)

The `OcrProvider` reads text from screenshots when the structured UI tree is empty or
unavailable — custom-drawn surfaces, canvas/game UIs, WebViews that don't expose nodes,
or image content. It is **discovered at runtime and never a hard dependency**: it activates
when a local `tesseract` binary is on `PATH`, or when the companion APK advertises an ML-Kit
OCR capability.

### Install

```bash
# In Termux (host):
pkg install tesseract

# In a PRoot-Distro Debian/Ubuntu distro:
apt-get install -y tesseract-ocr
```

Once installed, the provider registers itself automatically — no configuration required.
`droidjig doctor --json` will show `observe_ocr: true` in the capabilities map.

### What it enables

Once Tesseract is present (or the companion ML-Kit OCR is active), the following become available:

| Capability | OcrProvider (Tesseract) | OcrProvider (ML-Kit) | ADB / other |
|---|:---:|:---:|:---:|
| `observe_ocr` | ✓ | ✓ | ✗ |

### OCR region shape

Each region returned by `ocr screen` has:

```json
{
  "text": "Wi-Fi",
  "bounds": [44, 380, 164, 420],
  "confidence": 0.965
}
```

`bounds` uses the same `[left, top, right, bottom]` convention as UI elements; `confidence` is
`0.0–1.0` (normalized from Tesseract's 0–100 `conf` column).

### CLI verbs

```bash
# OCR the current screen and print all detected text regions
droidjig ocr screen

# Structured result envelope
droidjig ocr screen --json

# Filter by minimum confidence
droidjig ocr screen --min-confidence 0.5 --json

# Find text by OCR when the UI tree returns nothing
droidjig find --ocr-text "Balance" --json
droidjig find --ocr-text "Total.*Due" --json
```

### Priority

`OcrProvider` is **appended last** in the provider registry, so it is the lowest-priority
provider. It satisfies only `observe_ocr` and never shadows `observe_ui_tree` — the structured
UI tree always takes precedence. OCR is strictly a **fallback** for surfaces the tree can't see.

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


## Macros

Macros are declarative JSON documents that run a sequence of phone actions, control-flow steps, and variable interpolations through the standard safety funnel (`runtime.run_action`). Every action is kill-switch-gated, rate-limited, policy-checked, and appended to `actions.jsonl` with `parent_task_id` linking it to the macro run.

```bash
droidjig macro validate path/to/macro.json   # lint
droidjig macro run     path/to/macro.json     # execute
droidjig macro status                         # list recent runs
droidjig macro cancel  <run_id>               # cancel (daemon only)
```

See [docs/macros.md](docs/macros.md) for the full schema reference and control-flow step catalogue.

### Autonomy grants & memory

```bash
droidjig autonomy grant <macro> --max-risk high   # allow unattended run up to high risk
droidjig autonomy revoke <macro>                  # revoke all grants for a macro
droidjig autonomy list                            # list live grants
droidjig memory show [<store>]                    # inspect a memory store (or all)
droidjig memory export [<file>]                   # export all stores
droidjig memory delete [<store>]                  # delete a store (or all)
```

See [docs/macros.md § Progressive autonomy & memory](docs/macros.md#progressive-autonomy--memory) for the confirm-default rule, critical-risk policy, and the D12 redaction promise.

---

## Development

```bash
git clone https://github.com/jumbodaddystack/phonectl.git
cd droidjig
pip install -e ".[dev]"     # package + console script + pytest
pip install -e ".[dev,mcp]" # also the optional FastMCP transport

pytest -v                                               # full suite
pytest tests/test_ui_parser.py -v                       # one file
pytest tests/test_ui_parser.py::test_parse_bounds -v    # one test
```

The suite is 836 tests, all against injected fakes — no device required (one test needs the optional `mcp` extra and skips without it). Tests marked
`device` need real hardware over Wireless Debugging and are excluded in CI
(`pytest -m "not device"`).

The Android companion APK lives in `android/accessibility-companion/` and builds
independently:

```bash
cd android/accessibility-companion
./gradlew assembleDebug test
```

**Before changing a core layer, read [`docs/architecture.md`](docs/architecture.md).** The
invariants there are load-bearing — in particular, only `adb_backend.py` may touch `adb` or
`subprocess`, and every action must go through `runtime.run_action`. Tests come first: write
the failing test, confirm it fails for the right reason, then write the code that passes it.

## License

[MIT](LICENSE) © Jeremy McCoy
