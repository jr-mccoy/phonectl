# Providers

droidjig talks to the phone through a graph of providers, not a single backend. ADB is always
there; the AccessibilityService companion, Termux:API, and OCR are optional and activate when
detected. The registry picks the best provider per capability, degrades cleanly when one is
absent, and reports truthfully which one served each call.

Start at [Provider graph](#provider-graph) for the selection rules, or jump to the provider
you are setting up.

---

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
