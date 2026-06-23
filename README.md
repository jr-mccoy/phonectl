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

## Getting started: `phonectl setup`

`phonectl setup` is the recommended onboarding wizard. It detects whether `adb` is installed, guides Android 11+ Wireless Debugging pairing, connects to the device, verifies `adb get-state`, and persists the working serial plus the volatile Wireless Debugging connect port for later reconnect attempts.

If `adb` is missing in Termux, install it first:

```bash
pkg install android-tools
```

Run the wizard and answer the three prompts from the Wireless Debugging screen:

```bash
phonectl setup
# Pairing host:port: 127.0.0.1:<pairPort>
# 6-digit pairing code: <code>
# Connect host:port: 127.0.0.1:<connPort>
```

Re-running `phonectl setup` is idempotent: if the device is already connected, phonectl short-circuits with an "already connected" message and does not prompt again. Setup can also report provider modules:

```bash
phonectl setup adb
phonectl setup accessibility
phonectl setup notifications
phonectl setup termux-api
phonectl setup all
```

Each module report states the required permission, current availability, how to enable it, capabilities unlocked, and safety implications. `accessibility` and `notifications` are companion-APK providers planned for Phase 4; `termux-api` is optional and discovered from the local Termux:API commands.

## Diagnostics

`phonectl doctor` checks connectivity; `phonectl doctor --json` returns the structured result envelope with connection state and backend capabilities.

```bash
phonectl doctor
phonectl doctor --json
```

For support, write a redacted diagnostics bundle:

```bash
phonectl doctor --bundle /tmp/phonectl-diag.zip
```

The bundle contains `manifest.json`, `adb-version.txt`, and `adb-devices.txt`. The manifest includes config with secrets masked, capability status, device state, `adb version`, `adb devices -l`, mDNS results when available, host-shim status, and a metadata-only audit tail (`ts`/`verb`/`app`/`hash`).

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

Swipe from (X1, Y1) to (X2, Y2), or in a named direction with density-aware scaling.

```bash
phonectl swipe 540 1600 540 400          # coordinate form: scroll up
phonectl swipe up                        # named direction (full screen)
phonectl swipe down --within i=3         # scroll within container element 3
phonectl swipe left --distance-pct 0.7  # custom distance
```

### Gestures

High-level gesture verbs built on ADB's `input swipe` primitive.

**Named swipe** (density-aware — coordinates computed from `wm size`):
```bash
phonectl swipe up|down|left|right [--within i=N] [--distance-pct 0.5]
```

**Long-press** (zero-distance swipe held for `--duration-ms`):
```bash
phonectl long-press --i N [--duration-ms 1000]
phonectl long-press --x X --y Y
```

**Double-tap** (two taps separated by `--interval-ms`):
```bash
phonectl double-tap --i N [--interval-ms 100]
phonectl double-tap --x X --y Y
```

**Drag** (long-duration swipe — portable ADB drag primitive; `adb shell input draganddrop` is not universally available):
```bash
phonectl drag --x1 X --y1 Y --x2 X --y2 Y [--duration-ms 500]
```

**Fling** (velocity-scaled fast swipe):
```bash
phonectl fling up|down|left|right
```

**Scroll** (container-aware; reads element bounds from snapshot when `--within` is set):
```bash
phonectl scroll down [--within i=N]
phonectl scroll up --within i=5
```

**Scroll-until** (observe→scroll loop; returns the snapshot in which the target appeared, or the last snapshot if `--max` scrolls are exhausted):
```bash
phonectl scroll-until --text "Advanced" [--direction down] [--within i=N] [--max 10]
phonectl scroll-until --selector '{"resource_id":"com.example:id/item"}' --max 5
```

All gesture verbs route through `runtime.run_action` (kill switch, mode, audit, risk policy all apply). Use `--json` for structured output and `--yes` in confirm mode.

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

### `clipboard`

Read, write, or clear the system clipboard.

```bash
phonectl clipboard read               # requires Termux:API (Plan 3.5)
phonectl clipboard write "hello"      # via ADB service call (Android 10+)
phonectl clipboard write "hello" --yes
phonectl clipboard clear --yes
```

**Note:** `clipboard read` is not available via plain ADB because the parcel-based read is ROM-specific and unreliable. It returns a `capability_unavailable` error with install instructions until Termux:API is configured (`phonectl setup termux-api`).

`clipboard write` and `clipboard clear` are mutating operations that route through `runtime.run_action` (audit log, risk policy, kill switch, mode gates all apply).

### `device`

Read device state via the Termux:API provider (requires `phonectl setup termux-api`).

```bash
phonectl device battery          # Battery percentage, status, health, temperature
phonectl device battery --json   # Structured result envelope
phonectl device wifi             # WiFi SSID, IP, BSSID, RSSI
phonectl device wifi --json
```

Returns `capability_unavailable` with install instructions if Termux:API is not configured.

### `tts`

Speak text via Android's TTS engine (requires `phonectl setup termux-api`).

```bash
phonectl tts speak "Hello, world"
phonectl tts speak "Bonjour" --language fr
phonectl tts speak "Fast" --rate 1.5
phonectl tts speak "Hello" --json   # Structured result envelope
```

TTS is fire-and-forget: the command returns as soon as the TTS engine accepts the request. The speech plays asynchronously. Returns `capability_unavailable` if Termux:API is not configured.

### `intent`

Start activities or send broadcasts via `am start` / `am broadcast`.

```bash
phonectl intent start --action android.intent.action.VIEW --data "geo:37.422,-122.084"
phonectl intent start --component com.android.settings/.wifi.WifiSettings --yes
phonectl intent broadcast com.example.MY_ACTION --yes
phonectl intent broadcast com.example.MY_ACTION --extra key=value --yes
```

Intent `start` and `broadcast` are **high-risk** operations (risk level `high`) and require `--yes` or explicit policy override. The risk ledger classifies `intent_broadcast` as `high_risk_verb`. Multiple `--extra K=V` pairs are supported (string extras only; typed extras are deferred).

### `packages`

List, inspect, launch, stop, or clear packages.

```bash
phonectl packages list                          # user-installed packages only
phonectl packages list --all                    # include system packages
phonectl packages resolve com.android.settings  # version, launch activity
phonectl packages launch com.android.settings   # same as `launch` verb
phonectl packages stop com.example.app --yes    # force-stop (high risk)
phonectl packages clear com.example.app --yes   # clear data (critical risk)
```

Risk levels:
- `packages list` / `packages resolve` — read-only; no risk gate.
- `packages launch` — same risk classification as the `launch` verb.
- `packages stop` — **high risk** (`high_risk_verb` signal); requires `--yes` or a `high: allow` policy override.
- `packages clear` — **critical risk** (`critical_verb` signal); requires `--yes` and is **denied by default policy** — override `risk_policy.critical` to `confirm` first.

### `wait-for`

Re-observe on a short poll until a UI element matching `--text` or `--id` appears, or until `--timeout` seconds elapse. Requires exactly one of `--text` or `--id`.

```bash
phonectl wait-for --text "Network & internet" --timeout 8
phonectl wait-for --id "android:id/title" --timeout 5
```

Exit codes: `0` on match, `1` on timeout, `2` if neither `--text` nor `--id` is provided.

### `doctor`

Check device connectivity, print structured JSON, or create a redacted diagnostics bundle.

```bash
phonectl doctor
phonectl doctor --json
phonectl doctor --bundle /tmp/phonectl-diag.zip
# phonectl: connected (serial=127.0.0.1:PORT, state=device)
```


### `mcp`

Launch the optional stdio MCP server so agents can call phonectl as native tools. The live transport needs the optional MCP SDK; handlers and tests remain stdlib-only.

```bash
pip install 'phonectl[mcp]'
phonectl mcp
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
| `phone_resume` | Clear the emergency stop. | none |
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

Every executed action (tap, type, swipe, key, launch) is appended to `~/.config/phonectl/actions.jsonl` as a JSONL record containing the timestamp, verb, target, resulting foreground app, screen hash, and `outcome` (`ok` or `blocked`). Dry-run actions are not logged.

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

### Risk ledger & policy

Before any mutating action executes, `runtime.run_action` observes the current screen and classifies the pending action as `low`, `medium`, `high`, or `critical`. The classifier reads the foreground package and parsed UI element metadata; it does not call adb directly.

| Signal | Level | Trigger |
|---|---|---|
| `guarded_package` | `high` | Foreground package starts with a configured guarded prefix. |
| `password_field` | `high` | A parsed element has `password: true`. |
| `payment_keyword` | `critical` | Screen text contains payment/purchase/bank/card wording. |
| `destructive_keyword` | `critical` | Screen text contains factory reset, wipe, delete account, or uninstall wording. |
| `install_keyword` | `high` | Screen text contains install, allow, grant, subscribe, or send. |
| `otp_like_content` | `medium` | Visible element text contains a 4-8 digit code. |
| `high_risk_verb` | `high` | Verb is `packages_stop`, `intent_broadcast`, or `notifications_reply`. |
| `critical_verb` | `critical` | Verb is `packages_clear`. |

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

Rate limits are bucketed sliding windows over the last minute. Every allowed action counts against `global` and its verb bucket; `high` and `critical` actions also count against `high_risk`. Rate state is stored in `$PHONECTL_HOME/ratelimit.json`. A limit breach returns `rate_limited` with `bucket`.

Use `policy explain` to inspect a decision before acting:

```bash
phonectl policy explain --verb tap --text "Pay now" --json
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
| `1` | Timeout (`wait-for`), connection error, policy denial, rate limit, or busy writer |
| `2` | Kill switch active, or `wait-for` called without `--text`/`--id` |
| `3` | Confirm-mode refusal (action verb called without `--yes`) |


## Termux:API provider (optional)

The `TermuxApiProvider` is an optional second provider that activates automatically when `termux-battery-status` is found on `PATH`. No configuration is required — phonectl detects it at startup.

### Install

```bash
# 1. Install the Termux:API companion app from F-Droid or the Termux add-ons page
# 2. In Termux, install the CLI tools:
pkg install termux-api
# 3. On Android, grant Termux:API the permissions it requests (battery, clipboard, WiFi, etc.)
phonectl setup termux-api   # verify detection and show capability status
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

**Clipboard read upgrade:** `phonectl clipboard read` returns `capability_unavailable` without Termux:API because ADB clipboard reading is ROM-specific and unreliable on Android 10+. Once Termux:API is installed, `clipboard read` upgrades automatically — no configuration change needed.

**New verbs:** `phonectl device battery`, `phonectl device wifi`, and `phonectl tts speak TEXT` are only available when Termux:API is present. All three return structured result envelopes with `--json`.

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

**Optional:** absent the companion APK, phonectl is ADB-first and completely unchanged.
`_make_accessibility_provider()` returns `None` by default until Plan 4.3 supplies a
`SocketTransport`; at that point the provider activates automatically when the companion is
reachable.

**Semantic node actions** let the agent click, long-click, scroll, expand, collapse, or dismiss UI
elements by accessibility node ID — bypassing coordinate-based tapping entirely:

```python
provider.semantic_action("node_id", "scroll_forward")
provider.set_text_native("node_id", "search query")  # ACTION_SET_TEXT, IME-independent
```

**UI event stream** (cursor-based polling):

```python
out = provider.poll_events(since=0)    # {"events": [...], "cursor": 5}
out = provider.poll_events(since=5)    # only events after cursor 5
```

**Design spec:** `android/accessibility-companion/SPEC.md` describes the companion APK's service
surface, message contract, permissions, and error codes.

---

## Companion transport & trust controls

The companion APK communicates with phonectl over a **loopback TCP socket** — a newline-delimited
JSON protocol running on `127.0.0.1` only. Configure the port phonectl connects to:

```json
// ~/.config/phonectl/config.json
{
  "companion_port": 8765,
  "companion_host": "127.0.0.1",
  "companion_timeout": 2.0
}
```

`companion_port` defaults to `null` (unset). When unset, `_make_accessibility_provider()` and
`_make_notifications_provider()` skip the transport and the build stays ADB-first — no companion
required. When set, phonectl pings the companion on startup; if it does not respond, the providers
are omitted silently.

### Transport guarantee: loopback only

`SocketTransport` rejects any `companion_host` that is not `127.0.0.1`, `localhost`, or `::1` with
a `ValueError`. There is no way to connect the transport to an external host. The companion APK
server also binds `127.0.0.1` only — never `0.0.0.0`.

### `phonectl trust status`

Inspect the current handshake from the companion:

```bash
phonectl trust status
phonectl trust status --json
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

Keys absent from the companion's `capabilities` map default to **enabled** — the toggle set only
ever removes grants, never invents capabilities the provider does not support.

### Emergency stop

The companion APK's persistent "Stop phonectl" notification and Quick-Settings tile set a
`stopped=true` flag in the `handshake` response. This flag is registered as an `extra_check` in
`audit.kill_switch_active`. When either the `STOP` sentinel file **or** `stopped=true` is active,
**all** action verbs (CLI, MCP, and later daemon) are blocked immediately with exit code `2`.

The precedence rule: **file sentinel OR companion stop flag blocks**. The file kill-switch is the
hard guarantee (survives APK crashes); the companion flag is the low-latency path for the running
session. A flaky companion socket never wedges the CLI — socket exceptions in extra checks are
swallowed and treated as "not stopped".

```bash
# File-based kill switch (always available, no companion needed)
touch ~/.config/phonectl/STOP    # engage
rm ~/.config/phonectl/STOP       # disengage

# Companion-based stop (requires APK running and connected)
# Tap "Stop phonectl" in the persistent notification or the Quick-Settings tile
```

**Design spec:** `android/foreground-service/SPEC.md` covers the foreground service, loopback TCP
server, persistent stop notification, Quick-Settings tile, per-capability toggle UI, and trust
guarantees.

---

## Notifications

phonectl treats notifications as a **first-class provider**, not UI-scraping. The `NotificationsProvider` exposes four capabilities with per-notification `can_reply`/`can_dismiss` flags derived from each notification's actions and `RemoteInput`.

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
phonectl notifications list                          # list all current notifications
phonectl notifications list --package com.msg        # filter by package
phonectl notifications list --json                   # structured result envelope

phonectl notifications wait --package com.msg --timeout 30  # poll until match
phonectl notifications wait --title-contains "Alice" --json

phonectl notifications reply KEY "on my way" --yes  # send RemoteInput reply (high-risk)
phonectl notifications reply KEY "ok" --json --yes

phonectl notifications dismiss KEY --yes             # dismiss one notification
phonectl notifications dismiss KEY --json --yes
```

`KEY` is the opaque `key` field from a `notifications list` item.

### Risk policy

| Verb | Risk level | Reason |
|---|---|---|
| `notifications_list` | read-only; not gated | |
| `notifications_wait` | read-only; not gated | |
| `notifications_reply` | **high** | Sends visible content into arbitrary apps |
| `notifications_dismiss` | low (default) | Removes a notification |

`notifications_reply` is a `high_risk_verb` — it requires `--yes` in confirm mode or a `high: allow` policy override. In the default policy it triggers confirmation. Use `phonectl policy explain --verb notifications_reply` to inspect before acting.

---

## Provider graph

`build_runtime()` returns a `ProviderRegistry` that wraps one or more `Backend`-conforming providers in priority order. In Phase 3.1 the registry holds a single `AdbBackend`; future phases will add `TermuxApiProvider` (Phase 3.5) and `AccessibilityServiceProvider` (Phase 4.1) by prepending them to the list.

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
# Phase 4.1 example (not yet shipped):
registry = ProviderRegistry([AccessibilityServiceProvider(), AdbBackend(...)])
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
| `config.json` | Device serial, mode, audit level, risk policy, guarded packages, and rate limits |
| `actions.jsonl` | Append-only audit log of executed and blocked actions |
| `ratelimit.json` | Recent per-bucket action timestamps for sliding-window rate limits |
| `config.json:audit_level` | Audit detail (`none`, `metadata`, `redacted`, or `full`) |
| `STOP` | Kill-switch sentinel — create to disable all actions |

---

## Status

The observe-act-observe core (library + CLI) is implemented and unit-tested. The real-device connectivity proof (build-step-zero: pairing `adb` inside PRoot against `adbd` over the loopback) and the end-to-end smoke run are manual steps that require a physical Android 11+ phone with Wireless Debugging enabled. See [docs/integration-smoke.md](docs/integration-smoke.md) for the full procedure.

Features deferred to follow-on work: `phonectl setup` interactive wizard, MCP server wrapper, richer provider graph, macro runtime, and AccessibilityService APK backend.

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

## Structured extraction

Read structured data from the UI — enumerate RecyclerView rows, extract form labels and values, locate the focused text field, filter elements by text pattern, or extract all visible text within a region.

```bash
# Extract all rows from a scrollable list (auto-detects the container)
phonectl extract list --json

# Extract rows from a specific container by element index
phonectl extract list --container-i 3 --json

# Extract form fields with associated labels
phonectl extract form --json

# Find elements whose text matches a regex (UI tree)
phonectl find --text-regex "Total|Balance" --json

# Find text by OCR when the UI tree returns nothing (canvas/game/WebView surfaces)
phonectl find --ocr-text "Balance" --json

# Get the currently focused text field
phonectl get focused-field --json

# Get all elements overlapping a screen region (x1 y1 x2 y2)
phonectl get text-in-region --bounds 0 0 1080 400 --json
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
`phonectl doctor --json` will show `observe_ocr: true` in the capabilities map.

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
phonectl ocr screen

# Structured result envelope
phonectl ocr screen --json

# Filter by minimum confidence
phonectl ocr screen --min-confidence 0.5 --json

# Find text by OCR when the UI tree returns nothing
phonectl find --ocr-text "Balance" --json
phonectl find --ocr-text "Total.*Due" --json
```

### Priority

`OcrProvider` is **appended last** in the provider registry, so it is the lowest-priority
provider. It satisfies only `observe_ocr` and never shadows `observe_ui_tree` — the structured
UI tree always takes precedence. OCR is strictly a **fallback** for surfaces the tree can't see.

---

## Daemon (Phase 5.1)

`phonectl daemon` makes the runtime a **long-lived single-writer process** that keeps the provider graph, session, and connection warm across requests and brokers all actions through one global write lock.

### Starting the daemon

```bash
phonectl daemon start
# phonectl daemon listening on 127.0.0.1:<PORT> (Ctrl-C to stop)
```

The daemon binds to an **ephemeral loopback TCP port** (`127.0.0.1` only — non-loopback is refused). It writes its address to `$PHONECTL_HOME/daemon.json` and removes it on clean shutdown.

### Frontend auto-routing

Once a daemon is running, every `phonectl` CLI command (and the MCP server) **transparently routes through it** — no flags needed. `discover()` reads `daemon.json`, pings the endpoint, and on success the frontend sends a JSON-RPC call instead of building an in-process runtime. When no daemon is found, the original in-process path is used unchanged — daemonization is a **compatible evolution**.

### Daemon commands

```bash
phonectl daemon start          # run daemon in foreground (Ctrl-C to stop)
phonectl daemon status --json  # check if a daemon is running and its state
phonectl daemon stop           # send the shutdown RPC and terminate the daemon
```

`phonectl daemon stop` calls the daemon's `shutdown` RPC and waits for it to exit cleanly. This is **distinct** from the emergency kill-switch: the `stop`/`resume` sentinel (`STOP` file or companion flag) still interrupts individual actions regardless of daemon state.

### Async job model

When a daemon is running, `act`, `observe`, and `find` verbs are dispatched as **async jobs** on the daemon. The CLI **block-and-polls** by default — it submits the job, then polls `job_poll` until the job is terminal, timing out after `act_timeout` seconds (default 60 s). A slow-but-healthy daemon no longer falsely reports `daemon_unreachable`.

**`--detach`** on any action verb returns immediately with a job id instead of waiting:

```bash
phonectl tap --index 3 --detach
# phonectl: job job_abc123 (use: phonectl job job_abc123)
```

**`phonectl job <id> [--wait] [--json]`** queries or waits on a job:

```bash
phonectl job job_abc123           # print current status
phonectl job job_abc123 --wait    # block until terminal (cap = act_timeout)
phonectl job job_abc123 --json    # structured job envelope
```

Job statuses: `accepted` (queued), `running`, `done`, `error`.

### Loopback-only

The daemon binds and listens on **`127.0.0.1` exclusively**. `daemon_host` is validated and a non-loopback address is rejected with a clear error. The socket is never exposed to the network.

### Config keys

| Key | Default | Description |
|---|---|---|
| `daemon_host` | `"127.0.0.1"` | Loopback address to bind on (loopback-only, non-loopback is rejected) |
| `daemon_autostart` | `false` | Reserved for Termux:Boot autostart (not yet wired) |
| `act_timeout` | `60.0` | Wall-clock cap (seconds) for CLI block-and-poll on async jobs |
| `sync_timeout` | `15.0` | Client timeout for fast synchronous RPCs (status, shutdown, etc.) |
| `poll_interval` | `0.5` | Cadence (seconds) for `job_poll` during block-and-poll and `phonectl job --wait` |
| `job_queue_max` | `8` | Maximum pending-job FIFO depth; excess submissions return a `busy` error |
| `idempotency_ttl` | `300.0` | How long (seconds) a finished job stays eligible for idempotency deduplication |

Set via `$PHONECTL_HOME/config.json`:

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

Every action dispatched through the daemon is appended as a structured run record to `$PHONECTL_HOME/runs.jsonl`. Each record carries: `action_id`, `parent_task_id` (optional, for multi-step task tracking), `request_id`, `verb`, `target`, `provider`, `snapshot_before`, `snapshot_after`, `risk` decision, `retries`, `outcome`, and `user_approved`. This is a new layer on top of `actions.jsonl` — audit logging is unchanged.

### Events & snapshots (Phase 5.2)

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
  "started_at": 1750000000.0
}
```

### No daemon required

In-process primitives (`observe`, `tap`, `type`, etc.) work exactly as they always did when no daemon is running. The daemon is a **compatible evolution** — it adds single-writer coordination and run records, it does not gate the v1 primitives.

### Termux:Boot autostart (seam only)

`daemon_autostart` config key exists and `phonectl daemon start` runs foreground. Autostart via Termux:Boot and companion foreground-service hosting are noted as seams; they land in Phase 5.2+.


## Macros

Macros are declarative JSON documents that run a sequence of phone actions, control-flow steps, and variable interpolations through the standard safety funnel (`runtime.run_action`). Every action is kill-switch-gated, rate-limited, policy-checked, and appended to `actions.jsonl` with `parent_task_id` linking it to the macro run.

```bash
phonectl macro validate path/to/macro.json   # lint
phonectl macro run     path/to/macro.json     # execute
phonectl macro status                         # list recent runs
phonectl macro cancel  <run_id>               # cancel (daemon only)
```

See [docs/macros.md](docs/macros.md) for the full schema reference and control-flow step catalogue.
