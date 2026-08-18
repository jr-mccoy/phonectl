# droidjig Automation Platform Strategy

**Date:** 2026-06-21  
**Status:** Strategy / critique / standalone roadmap  
**Scope:** This document reviews the project's original design plans and turns the critique into a standalone product and architecture roadmap for evolving `droidjig` from an ADB observe/act bridge into a local Android automation platform for agents.

> **Internal planning document.** This is a working instrument, not user documentation:
> it thinks in phase numbers and plan IDs, and it records intent that may not have shipped.
> For what droidjig actually does today, read the [README](../README.md); for what is built
> versus deferred, read [roadmap.md § Phase status](roadmap.md). The `plans/` directory it
> references is a local working tree and is not published.

## 1. Executive summary

The existing plans are a strong foundation. They define a local, no-root Android control layer that observes the phone as structured JSON and acts through ADB primitives, then exposes those capabilities through CLI and MCP. The plans are especially strong on testability, stdlib-only Python for the core, real-device validation, backend isolation, auditability, and safety gating.

The main strategic gap is that the current design is still mostly an **observe-command-act** bridge. Apps like Tasker and MacroDroid are broader automation systems: event engines, trigger/condition/action runtimes, plugin hosts, notification processors, intent senders, variable stores, schedulers, and permission brokers. If `droidjig` aims to give agents true phone “superpowers,” the current plans should become the lower layer of a richer automation platform.

The recommended direction is:

1. Keep the existing ADB-first CLI/MCP foundation.
2. Add robust selector-based targeting and hierarchy-preserving observation.
3. Introduce backend/provider capability discovery instead of assuming every backend can do the same things.
4. Add providers for AccessibilityService, notifications, clipboard, intents, Termux:API, and optionally Shizuku/root.
5. Centralize execution in a daemon/event runtime with serialized actions, policy enforcement, audit logging, and event subscriptions.
6. Build a Tasker/MacroDroid-inspired trigger/condition/action macro layer on top.
7. Preserve local-first operation and rebuild unavailable third-party features ourselves when licensing or integration constraints prevent direct reuse.

## 2. Current foundation: what is strong

### 2.1 Clear local-first goal

The core objective is sound: let an agent observe the host Android phone as structured JSON and act on it with taps, text, swipes, key events, and app launch over ADB, without root.

This is the right first milestone because ADB gives immediate, device-wide capabilities while keeping the first implementation testable from Python.

### 2.2 Good isolation boundaries

The existing architecture correctly confines device-specific ADB knowledge to backend code while keeping UI parsing pure and orchestration testable. That separation should remain a core invariant, but the concept of “backend” should evolve into a provider/capability graph as the app grows.

### 2.3 Structured UI first, screenshots second

The plans correctly treat the structured UI tree as the primary observation surface and screenshots as fallback. This is usually better than vision-first automation because it is more deterministic, easier to test, and more accessible to agents.

### 2.4 Safety is already part of the foundation

The existing safety model includes modes, audit logging, a kill switch, guarded packages, and rate limiting. This is exactly the right instinct for an agent-controlled phone automation system. Later work should deepen this into a risk policy engine rather than replacing it.

### 2.5 Real-device feedback is being incorporated

The follow-up plans already capture real-world failures such as Wireless Debugging port volatility and `uiautomator` idle-state errors. That feedback loop is important and should continue: every major capability should eventually have a manual real-device walkthrough plus unit tests around deterministic pieces.

## 3. Core critique: the app should become an automation platform

The current plans expose verbs such as:

- `observe`
- `tap`
- `type`
- `swipe`
- `key`
- `launch`
- `wait_for`
- `doctor`
- `reconnect`

These are necessary primitives, but they are not enough to match the useful capabilities of Tasker, MacroDroid, or other mature Android automation tools.

A fuller automation platform needs:

- Triggers.
- Conditions.
- Actions.
- Variables.
- Event subscriptions.
- Schedulers.
- Notification handling.
- Clipboard support.
- Intent/deep-link support.
- App/package management.
- User confirmation flows.
- Plugin/provider capabilities.
- A policy/risk engine.
- A macro runtime.
- A daemon/event loop.

The recommended layered architecture is:

```text
backend/provider capabilities
  ↓
observe/action primitives
  ↓
automation runtime
  ↓
recipes/macros/agent tools
  ↓
CLI / MCP / local API / optional UI
```

The existing ADB observe/act work should be treated as the primitive layer, not the final product shape.

## 4. Backend model: move from one backend to provider capabilities

### 4.1 Why one backend interface will become strained

The current backend concept assumes a small common interface: observe the UI, tap, type, swipe, key, launch, and get state. This works for ADB and can be adapted to an AccessibilityService, but it will not scale cleanly across all useful Android automation powers.

Different providers have different strengths:

| Capability | ADB | AccessibilityService | Notification listener | Termux:API | Shizuku | Root |
|---|---:|---:|---:|---:|---:|---:|
| UI tree | Good but flaky | Excellent/live | No | No | Sometimes | Yes |
| Tap/swipe | Yes | Yes | No | No | Sometimes | Yes |
| Type text | Limited | Good with `ACTION_SET_TEXT` | No | Clipboard-assisted | Sometimes | Yes |
| Launch app | Yes | Limited | No | Sometimes | Yes | Yes |
| Send intents | Yes | Limited | No | Sometimes | Yes | Yes |
| Read notifications | Limited | Limited | Excellent | Sometimes | Maybe | Yes |
| Reply to notifications | Limited | Limited | Excellent | No | Maybe | Yes |
| Clipboard | Limited | Sometimes | No | Good | Good | Good |
| Sensors | No | No | No | Good | Maybe | Yes |
| System settings | Some | No | No | Some | Better | Best |
| Persistence | Weak | Good | Good | Good | Good | Good |
| Event-driven triggers | Weak | Good | Excellent | Good | Good | Good |

A single `Backend` protocol should not pretend all providers are equivalent.

### 4.2 Add capability discovery

Every backend/provider should expose a capability document, for example:

```json
{
  "observe_ui_tree": true,
  "observe_screenshot": true,
  "act_tap": true,
  "act_type": true,
  "act_key": true,
  "launch_app": true,
  "send_intent": true,
  "read_notifications": false,
  "reply_notifications": false,
  "read_clipboard": false,
  "write_clipboard": false,
  "write_secure_settings": false,
  "persistent_events": false,
  "requires_adb": true,
  "requires_accessibility": false,
  "requires_notification_listener": false
}
```

The runtime can then choose the best provider for each operation and explain why a feature is unavailable.

### 4.3 Prefer a provider graph over a single active backend

Long term, use a composite runtime:

```text
Runtime
 ├── UiObserverProvider: AccessibilityService preferred, ADB fallback
 ├── GestureProvider: AccessibilityService preferred, ADB fallback
 ├── TextInputProvider: AccessibilityService / IME / clipboard / ADB fallback
 ├── ShellProvider: ADB
 ├── IntentProvider: ADB / companion APK / Termux
 ├── NotificationProvider: NotificationListenerService
 ├── ClipboardProvider: Termux:API / companion APK / ADB fallback
 ├── SensorProvider: Termux:API / companion APK
 ├── PackageProvider: ADB / Shizuku
 └── PolicyProvider: local config + screen risk classifier
```

This avoids forcing every backend to implement unnatural methods and makes the app extensible.

## 5. Observation model improvements

### 5.1 Keep element indices, but add selectors

Element index `i` is useful for fast agent loops, but it is only stable within one observation of one screen. It can break when lists scroll, keyboards open, ads appear, layouts update, translations change, or OEM skins reorder nodes.

Keep index targeting, but add selector targeting:

```json
{
  "selector": {
    "text": "Wi-Fi",
    "text_regex": "^Wi-?Fi$",
    "content_desc": "Search",
    "resource_id": "android:id/title",
    "class": "android.widget.TextView",
    "clickable": true,
    "enabled": true,
    "checked": false,
    "bounds_near": [44, 380, 1036, 520],
    "ancestor_text": "Network",
    "sibling_text": "Connected devices",
    "nth_match": 0
  }
}
```

Suggested commands:

```bash
droidjig tap --selector '{"text":"Wi-Fi","clickable":true}'
droidjig tap --text "Wi-Fi"
droidjig tap --id android:id/title --nth 1
droidjig wait-for --selector '{"text_regex":"Continue|Next"}'
```

MCP tools should accept both `i` and selector objects.

### 5.2 Preserve hierarchy, not only a flat list

A flat element list is agent-friendly but loses critical relationships. Many tasks require structure:

- Tap the switch in the row labeled “Wi-Fi.”
- Fill the field below the “Password” label.
- Extract all rows from a list.
- Tap a sibling button beside a piece of text.
- Prefer a child action over a parent container.

The snapshot should include both flat elements and hierarchy metadata:

```json
{
  "elements": [...],
  "tree": {
    "i": 0,
    "class": "android.widget.FrameLayout",
    "children": [...]
  },
  "relations": {
    "parent": {"7": 3},
    "children": {"3": [4, 5, 6, 7]},
    "siblings": {"7": [5, 6, 8]},
    "ancestors": {"7": [3, 1, 0]}
  }
}
```

If payload size is a concern, expose `droidjig observe --tree` or an MCP option.

### 5.3 Capture richer element metadata

The element model should preserve more state when available:

- `enabled`
- `focused`
- `focusable`
- `selected`
- `checked`
- `checkable`
- `scrollable`
- `long_clickable`
- `password`
- `package`
- `index`
- `editable`
- `visible`
- `actions`
- `hint_text`
- `error_text`
- `state_description`
- `pane_title`
- `collection_info`
- `range_info`

This matters for avoiding disabled controls, detecting switch state, preventing password leakage, selecting better text-entry methods, and reasoning about scrollable containers.

### 5.4 Add structured extraction APIs

Agents often need to read structured data, not only tap controls. Add commands/tools such as:

```bash
droidjig find --text-regex "Total|Balance|Amount"
droidjig extract list --container-id com.foo:id/recycler
droidjig extract form
droidjig get focused-field
droidjig get clipboard
droidjig notifications list
```

Potential extraction targets:

- Lists/recycler views.
- Forms.
- Dialogs.
- Tables/grids.
- Current focused field.
- Visible text by region.
- Notification content.
- Clipboard content.

### 5.5 Support stale-snapshot protection

Before acting on `i`, verify that the screen hash still matches the snapshot used to choose the index, unless the caller explicitly opts out.

Potential action request shape:

```json
{
  "verb": "tap",
  "i": 7,
  "expected_hash": "abc123",
  "stale_ok": false
}
```

If the hash changed, the runtime should re-observe and either resolve a selector again or ask the agent to choose again.

## 6. Action model improvements

### 6.1 Text input needs multiple strategies

`adb shell input text` has many edge cases:

- Unicode.
- Newlines.
- Emojis.
- Non-Latin scripts.
- Password fields.
- IME autocorrect.
- Apps that block paste.
- Shell metacharacters.
- Very long text.

Preferred strategy order:

1. Accessibility `ACTION_SET_TEXT` for editable fields.
2. Companion APK text injection.
3. Clipboard set + paste.
4. ADB keyboard IME such as an open-source ADB keyboard approach, if acceptable.
5. `adb shell input text` as fallback.

Every text path must keep audit redaction and sensitive-field policies.

### 6.2 Add long-click, double-tap, drag, pinch, and gestures

Tasker/MacroDroid-style automation often needs more than tap/swipe:

- Long press.
- Double tap.
- Drag from element to element.
- Pinch/zoom.
- Multi-pointer gestures.
- Scroll inside a specific container.
- Fling.
- Press-and-hold then move.

AccessibilityService can dispatch gestures more naturally than ADB for some of these.

### 6.3 Named swipes should be container-aware

Named swipes are useful, but the runtime should support both screen-level and container-level scrolling:

```bash
droidjig swipe up
droidjig swipe up --within i=12
droidjig scroll --selector '{"scrollable":true}' --direction down
droidjig scroll-until --text "Advanced" --max 10
```

If an AccessibilityService provider exposes scroll actions, prefer semantic scroll actions over coordinate swipes.

### 6.4 Add intent and deep-link actions

ADB and Android intents are extremely powerful and more reliable than UI navigation.

Suggested commands:

```bash
droidjig intent start --action android.intent.action.VIEW --data "geo:37.7749,-122.4194"
droidjig intent start --component com.foo/.MainActivity
droidjig intent broadcast --action com.example.ACTION
droidjig app open-url "https://example.com"
droidjig app open-settings com.foo
```

Risk-classify intents because they can send messages, open payment links, change settings, or trigger app-specific side effects.

### 6.5 Add app/package management wrappers

Expose safe wrappers around common package operations:

- List installed packages.
- Resolve launcher activity.
- Launch activity.
- Force-stop app.
- Open app info.
- Read app version.
- Detect default browser/launcher/SMS app.
- Grant/revoke permissions where ADB allows.
- Install/uninstall APK with strong confirmation.
- Clear app data with critical-risk policy.

Do not force agents to handcraft raw shell commands for common operations.

## 7. Resilience improvements

### 7.1 Constrain port probing

Layered port recovery is important, but probing must be bounded and transparent:

- Probe only loopback or known device IP.
- Retry last-known-good port first.
- Limit concurrency.
- Use short timeouts.
- Avoid broad network scans by default.
- Persist failed ports with backoff.
- Emit clear diagnostics explaining what was tried.
- Provide `droidjig reconnect <port>` as a manual escape hatch.

### 7.2 Return structured lock state

Instead of only raising an error when locked, observations should be able to return structured blocked state:

```json
{
  "lock_state": "unlocked | locked_swipe_only | locked_secure | biometric_prompt | work_profile_locked | unknown",
  "can_act": false,
  "recommended_user_action": "Unlock the phone manually."
}
```

Nuances to handle:

- Swipe-only locks.
- PIN/password locks.
- Biometric prompts.
- Work profile lock.
- Secure screens with black screenshots.
- Notifications visible on lock screen.

The default policy should not store or enter user PINs. If ever supported, it must require explicit opt-in and strong confirmation.

### 7.3 Validate observation freshness

Retrying `uiautomator` is necessary, but stale valid XML is also dangerous. Add freshness checks:

- Include `observed_at` timestamps.
- Capture focused app before and after dump; retry if it changed.
- Capture rotation/screen size before and after; retry if changed.
- Optionally require N identical hashes over a settle window.
- Add `observe --settle fast|stable|none`.
- Verify hash before act-by-index.

### 7.4 Add action serialization

Multiple callers may exist: CLI, MCP, daemon macros, background events, or another agent. Add a single action queue or lock:

- Only one mutating action at a time.
- Request IDs.
- Cancellation.
- Idempotency keys.
- Clear “busy” status.
- Emergency stop signal.
- Stop-current-macro action.

The kill switch remains the hard global stop.

## 8. Safety and policy improvements

### 8.1 Move from guarded packages to risk classification

Package-prefix guarding is useful but incomplete. Risky actions can occur in browsers, system dialogs, app overlays, or unknown apps.

Add a risk classifier:

```json
{
  "risk": {
    "level": "low | medium | high | critical",
    "reasons": [
      "package matches guarded prefix",
      "screen contains payment keyword",
      "system permission dialog",
      "password field present",
      "install/uninstall action visible"
    ],
    "policy": "allow | confirm | deny"
  }
}
```

Risk signals:

- Foreground package.
- System package names such as permission controller, package installer, settings, Play Store, browser.
- Screen text: pay, send, transfer, buy, subscribe, delete, factory reset, allow, grant, install, uninstall.
- Password fields.
- OTP-like content.
- Notification action labels.
- User-configured deny/confirm lists.

### 8.2 Distinguish rate limits by action class

A single actions-per-minute limit is a good start but too coarse. Add buckets:

```json
{
  "rate_limits": {
    "tap": "120/min",
    "type": "30/min",
    "launch": "20/min",
    "high_risk": "1/min",
    "global": "180/min"
  }
}
```

Also consider:

- Burst and sustained rates.
- Per-app limits.
- Cooldowns after failed actions.
- Stop after repeated taps in the same region.
- Stop after repeated unchanged screen hashes.
- Lower caps on guarded screens.

### 8.3 Strengthen audit privacy

Typed text redaction is necessary but not sufficient. Audit logs can reveal app names, screen labels, notification contents, clipboard contents, URLs, screenshot paths, and sensitive screen hashes.

Add audit levels:

- `none`
- `metadata`
- `redacted`
- `full`

Add commands:

```bash
droidjig audit tail
droidjig audit purge
droidjig audit export --redacted
```

Add redaction for:

- Password fields.
- OTP-like strings.
- Emails.
- Phone numbers.
- Card-like numbers.
- URLs with tokens.
- Clipboard contents.
- Notification text from guarded apps.

### 8.4 Add user-facing emergency controls

For a companion APK or daemon:

- Persistent notification with “Stop droidjig.”
- Quick Settings tile kill switch.
- Home-screen shortcut to pause automation.
- Physical-button emergency gesture if possible.
- Clear visual/audible feedback when automation is active.

## 9. Setup and diagnostics improvements

### 9.1 Make setup modular

Current setup focuses on ADB/Wireless Debugging. A full platform needs modular setup:

```bash
droidjig setup adb
droidjig setup accessibility
droidjig setup notifications
droidjig setup termux-api
droidjig setup shizuku
droidjig setup all
```

Each module should report:

- Required permission.
- Current status.
- How to enable it.
- What capabilities it unlocks.
- Safety implications.

### 9.2 Detect Android version and environment actively

Add checks for:

- Android version.
- `adb` availability and version.
- ADB server startup.
- Wireless Debugging status where detectable.
- Paired/offline/stale serial.
- Multiple devices.
- PRoot loopback behavior.
- Host-Termux shim availability.
- Termux:API availability.
- Battery optimization restrictions.
- AccessibilityService enabled state.
- Notification listener enabled state.

### 9.3 Add diagnostics bundles

Add:

```bash
droidjig doctor --json
droidjig doctor --bundle /tmp/droidjig-diagnostics.zip
```

Bundle contents should include:

- Config with secrets redacted.
- Python version.
- OS/environment details.
- `adb version`.
- `adb devices -l`.
- Current serial/get-state.
- Recent connection errors.
- mDNS result.
- Host-Termux shim status.
- Provider capability status.
- Last non-sensitive audit entries.

## 10. MCP and API improvements

### 10.1 Return structured results instead of CLI-shaped tuples

MCP clients should not have to interpret CLI return codes. Runtime calls should return structured results:

```json
{
  "ok": false,
  "error": {
    "type": "DeviceLockedError",
    "code": "device_locked",
    "message": "Device is locked.",
    "retryable": false,
    "requires_user": true
  }
}
```

The CLI can map these structured errors to exit codes. MCP and daemon APIs should preserve the structure.

### 10.2 Add policy and capability tools

Suggested MCP tools:

- `droidjig_capabilities`
- `droidjig_policy_get`
- `droidjig_policy_set`
- `droidjig_mode_get`
- `droidjig_mode_set`
- `droidjig_guarded_packages_get`
- `droidjig_audit_tail`
- `droidjig_stop`
- `droidjig_resume`
- `droidjig_diagnostics`

### 10.3 Add selector-aware and dry-run tool calls

MCP action tools should support:

- Index target.
- Selector target.
- Raw coordinate target.
- Expected screen hash.
- Dry-run.
- Human-readable reason.

Example:

```json
{
  "selector": {"text": "Wi-Fi", "clickable": true},
  "expected_hash": "abc123",
  "dry_run": true,
  "reason": "Open Wi-Fi settings for the user-requested network check."
}
```

## 11. AccessibilityService companion strategy

### 11.1 Treat AccessibilityService as an event provider, not just a backend

The AccessibilityService should eventually provide:

- Live UI tree snapshots.
- UI change events.
- Window focus changes.
- Semantic actions.
- Gesture dispatch.
- Text setting.
- Scroll actions.
- Accessibility action metadata.

ADB should remain available for shell, package, and system tasks.

### 11.2 Do not only serialize to uiautomator XML

Uiautomator-format XML compatibility is useful for reuse, but native Accessibility data is richer. Support both:

1. Compatibility XML mode.
2. Native JSON mode with windows, nodes, actions, and metadata.

Example native shape:

```json
{
  "windows": [
    {
      "id": 3,
      "type": "application",
      "package": "com.example",
      "nodes": [
        {
          "node_id": "...",
          "text": "Wi-Fi",
          "class": "android.widget.TextView",
          "actions": ["click", "long_click"],
          "bounds": [44, 380, 1036, 520]
        }
      ]
    }
  ]
}
```

### 11.3 Prefer a persistent low-latency transport

Broadcast plus result files is simple and testable, but a continuous automation platform likely needs lower latency and cleaner request handling.

Consider:

- Localhost TCP socket.
- WebSocket.
- Foreground service IPC.
- Bound service/AIDL if appropriate.
- ContentProvider call API.

Every request should include:

- Request ID.
- Timeout.
- Version.
- Capability negotiation.
- Error code.
- Stale response protection.

### 11.4 Add explicit trust UX

Accessibility is extremely sensitive. The companion should provide:

- Clear explanation of what is read and controlled.
- Local-only guarantee.
- No network by default.
- Per-capability toggles.
- Persistent active notification.
- Emergency stop.
- Audit visibility.
- Guarded-app behavior.
- Password/payment screen warnings.

## 12. Tasker/MacroDroid-inspired automation runtime

### 12.1 Trigger types

Potential triggers:

- App opened.
- App closed.
- Activity changed.
- Text appears on screen.
- Element appears.
- Element disappears.
- Notification posted.
- Notification removed.
- Notification contains text.
- Clipboard changed.
- Time/date schedule.
- Alarm.
- Calendar event.
- Battery level.
- Charging state.
- Wi-Fi connected/disconnected.
- Wi-Fi SSID match.
- Bluetooth device connected.
- Headphones connected.
- NFC tag scanned.
- Location/geofence.
- Screen on/off.
- Device unlocked.
- Orientation changed.
- Volume button pressed.
- Shake/sensor threshold.
- File changed.
- HTTP webhook received.
- SMS received, where permissioned.
- Call state changed, where permissioned.

### 12.2 Conditions

Potential conditions:

- Foreground package equals/does not equal.
- Current screen contains text.
- Selector exists.
- Device locked/unlocked.
- Battery above/below threshold.
- Charging/not charging.
- Wi-Fi SSID equals.
- Bluetooth device connected.
- Time window.
- Location region.
- Variable comparison.
- Network available.
- DND state.
- Work profile active.
- Headphones connected.
- Last action succeeded.
- Risk level below threshold.

### 12.3 Actions

Potential actions:

- Tap.
- Long press.
- Type.
- Set text.
- Swipe.
- Scroll until.
- Launch app.
- Open URL/deep link.
- Send intent.
- Press notification action.
- Reply to notification.
- Dismiss notification.
- Set clipboard.
- Get clipboard.
- Take screenshot.
- OCR region.
- Read screen text.
- Extract list.
- Fill form.
- Toggle supported settings.
- Set brightness.
- Set volume.
- Media play/pause/next/previous.
- Send SMS, where permissioned.
- HTTP request.
- Webhook callback.
- File read/write/copy.
- Speak text.
- Show toast/dialog.
- Vibrate.
- Wait.
- Branch.
- Loop.
- Set variable.
- Stop macro.
- Ask user for confirmation.
- Open system settings page.
- Grant/revoke permission where allowed.
- Force-stop app with confirmation.
- Clear app data with critical confirmation.
- Install/uninstall APK with critical confirmation.

### 12.4 Control flow

The macro runtime should support:

- If/else.
- Switch/case.
- Loops.
- For-each over elements/list items.
- Retry with backoff.
- Timeout.
- Try/finally cleanup.
- Wait until condition.
- Race: wait for any of several conditions.
- Limited parallel tasks.
- Cancellation.
- Error handling.
- Variables and scoped variables.
- Return values.

### 12.5 Macro schema example

A local macro format could be YAML or JSON. Example:

```yaml
name: Reply to known notification
trigger:
  type: notification
  package: com.example.chat
  text_regex: "urgent"
conditions:
  - type: device_unlocked
  - type: risk_below
    level: high
actions:
  - type: notification_reply
    text: "I saw this and will respond soon."
  - type: audit_note
    text: "Auto-replied to urgent notification"
policy:
  require_confirm: false
  max_runs_per_hour: 3
```

## 13. Open-source resources and proven ideas

This section is not a commitment to vendor or license compatibility. When direct integration is not possible or not desirable, rebuild the relevant capability behind `droidjig`'s local-first provider interfaces.

### 13.1 Android UI automation and control

#### scrcpy

Study for:

- Efficient screen streaming.
- Input injection design.
- Device connection management.
- Clipboard synchronization.
- Low-latency control ideas.

Potential use:

- Optional interoperability or inspiration for transport/control mechanisms.

#### OpenSTF minicap/minitouch ecosystem

Study for:

- Fast screenshot/video capture.
- Touch event injection.
- Low-level control architecture.

Potential limitation:

- Device/ABI compatibility and setup complexity.

#### uiautomator2 / atx-agent

Study for:

- Selector API design.
- Watchers.
- XPath-like querying.
- App lifecycle helpers.
- Real-world Android UI edge cases.

Potential use:

- Inspiration for selector syntax and waits, even if `droidjig` keeps stdlib-only core.

#### Appium

Study for:

- W3C action model.
- Element IDs.
- Wait conditions.
- Capability negotiation.
- UiAutomator2/Espresso tradeoffs.

Potential use:

- Design reference, not necessarily a dependency.

#### Auto.js-like tools

Study for:

- AccessibilityService scripting ergonomics.
- Selector APIs.
- Event loops.
- Gesture abstractions.

Potential use:

- Rebuild similar local scripting capabilities where licensing or distribution constraints prevent direct reuse.

### 13.2 Android integration providers

#### Termux:API

Useful for:

- Battery.
- Camera.
- Clipboard.
- Contacts.
- Dialogs.
- Location.
- Media.
- Notifications.
- Sensors.
- SMS.
- Telephony.
- Toasts.
- Text-to-speech.
- Wi-Fi info.

Potential role:

- Optional provider for Termux users.

#### Shizuku

Useful for:

- Privileged Android APIs without full root for the app.
- Enhanced system integration.

Potential role:

- Optional advanced provider with clear setup and safety disclosures.

#### Root provider

Useful for:

- Full control for users who explicitly opt in.

Policy:

- Not required for core value.
- Strongly separated and clearly labeled.

### 13.3 Automation app inspiration

#### Tasker

Study concepts:

- Profiles.
- Tasks.
- Scenes.
- Variables.
- Plugins.
- Intent integration.
- User confirmation and dialogs.

#### MacroDroid

Study taxonomy:

- Triggers.
- Actions.
- Constraints.
- Device settings.
- Connectivity.
- Media.
- Notifications.
- Applications.
- Webhooks.
- Variables.
- Control flow.

#### Easer

Study for:

- Open-source Android automation architecture.
- Trigger/action model.
- Local-first automation patterns.

### 13.4 OCR and vision

#### Tesseract

Potential role:

- Optional local OCR fallback for screenshots.

#### ML Kit Text Recognition

Potential role:

- Companion APK OCR provider.

#### OpenCV

Potential role:

- Optional image matching, template matching, visual anchors, and screenshot diffing.

### 13.5 IPC/API approaches

#### JSON-RPC

Potential role:

- Simple stdlib-friendly daemon protocol.

#### WebSocket

Potential role:

- Event streaming between companion APK, daemon, and clients.

#### gRPC

Potential role:

- Richer future protocol if dependencies become acceptable.

## 14. Additional edge cases to design for

### 14.1 UI and display edge cases

- Split-screen.
- Picture-in-picture.
- Freeform windows.
- Samsung DeX / desktop mode.
- External displays.
- Foldables.
- Display cutouts.
- Gesture navigation insets.
- Status/navigation bars.
- Keyboard/IME insets.
- Font-size and display-size changes.
- RTL languages.
- Localization.
- Dark/light theme differences.
- WebViews with poor accessibility trees.
- Canvas/game UIs.

### 14.2 Android security and policy edge cases

- `FLAG_SECURE` screens.
- Permission dialogs.
- Package installer dialogs.
- Accessibility permission screens.
- Device admin screens.
- VPN install dialogs.
- Work profile separation.
- Enterprise-managed devices.
- Biometric prompts.
- Play Protect warnings.
- Banking/payment screens.
- Password managers.
- 2FA apps.

### 14.3 Connectivity and environment edge cases

- Wireless Debugging disabled after reboot.
- Port rotation after sleep.
- mDNS unavailable in PRoot.
- Multiple ADB devices.
- Stale serial.
- ADB server hung.
- Host-Termux vs PRoot path differences.
- Android version below Wireless Debugging support.
- OEM-specific debugging behavior.
- Battery optimization killing services.

### 14.4 Automation correctness edge cases

- Stale UI snapshots.
- Hash changed between observe and act.
- Overlay appears before tap.
- Toast/snackbar hidden from tree.
- Keyboard opens and shifts coordinates.
- Repeated unchanged actions.
- Accidental double execution.
- Concurrent callers.
- Macro cancellation mid-action.
- App crashes or ANRs.
- Slow animations.
- Scroll boundaries.
- Infinite wait loops.

## 15. Recommended roadmap

### Phase 1: Harden the current foundation

- Finish resilience work.
- Finish safety completeness.
- Finish setup wizard.
- Finish MCP wrapper.
- Finish polish.
- Add selector targeting.
- Preserve hierarchy in observations.
- Add structured runtime errors.
- Add capability discovery.
- Add action serialization.

### Phase 2: Add practical automation primitives

- Clipboard provider.
- Intent/deep-link provider.
- App/package provider.
- Notification listener design.
- Scroll-until and selector actions.
- Structured extraction APIs.
- Diagnostics bundle.
- Risk classifier.

### Phase 3: Build companion APK providers

- AccessibilityService native JSON tree.
- UI event stream.
- Gesture dispatch.
- NotificationListenerService.
- Foreground service transport.
- Persistent emergency stop notification.
- Optional OCR provider.
- Optional clipboard provider.

### Phase 4: Add automation runtime

- Trigger/condition/action engine.
- Macro format.
- Variables.
- Scheduler.
- Event subscriptions.
- Policy engine.
- Macro audit logs.
- User confirmation actions.
- Macro cancellation.

### Phase 5: Ecosystem integration and advanced providers

- Termux:API provider.
- Shizuku provider.
- Optional root provider.
- Optional scrcpy/minicap-inspired low-latency paths.
- Tasker/MacroDroid intent/plugin interoperability where practical.
- Optional local web UI.

## 16. Highest-priority backlog items

If this strategy is converted into immediate implementation plans, start with:

1. Add selector-based targeting alongside element indices.
2. Preserve hierarchy and richer metadata in `observe()`.
3. Add backend/provider capability discovery.
4. Convert runtime/CLI/MCP errors to structured results.
5. Add stale-snapshot protection before index-based actions.
6. Add action serialization and request IDs.
7. Expand safety from package denylist to risk classification.
8. Add clipboard support.
9. Add intent/deep-link support.
10. Add notification listener design.
11. Promote AccessibilityService from optional backend to event provider.
12. Add diagnostics bundles.
13. Design the daemon/event runtime.
14. Design the macro schema and trigger/condition/action model.

## 17. Product principle

`droidjig` should remain local-first, transparent, testable, and user-controlled. It should incorporate proven open-source ideas wherever licenses and architecture allow, but it should not depend on proprietary automation apps or closed ecosystems for its core value. If a useful Tasker/MacroDroid-style capability cannot be directly reused, rebuild the capability behind `droidjig`'s provider interfaces with explicit permissions, safety policy, auditability, and local execution.

## 18. Additional brainstorming pass: turn primitives into an agent operating system

The next strategic leap is to stop thinking of `droidjig` as a bag of tools and start treating it as a small **agent operating system for Android**. The agent should not merely ask for `tap(7)`; it should ask for durable capabilities such as "open this conversation," "collect the latest notification from this app," "wait until a login code arrives," "reply with this text after user confirmation," or "restore the phone to the previous foreground app." The platform can still execute those goals through low-level ADB, Accessibility, notification, intent, clipboard, and Termux providers, but the agent-facing layer should be semantic, observable, cancellable, and policy-checked.

This implies three product surfaces, all backed by the same runtime:

1. **Low-level computer-use tools** for raw observe/act loops when the agent genuinely needs flexible UI control.
2. **Typed Android capability tools** for common phone-native jobs such as notifications, intents, clipboard, packages, files, media, contacts, SMS where available, and settings.
3. **Declarative automations** that can run without the agent actively polling: triggers, conditions, actions, variables, schedules, retries, cancellation, and user approval steps.

The key design principle is **progressive autonomy**. A user should be able to start with manual/confirm mode, graduate specific recipes into unattended execution, and revoke or inspect every permission and macro decision later.

## 19. Research-informed provider notes

A brief web research pass reinforces the provider direction rather than a single-backend design:

- Android's AccessibilityService API can observe UI events and, when configured for it, dispatch custom gestures such as taps, swipes, and multi-touch. This makes Accessibility the right persistent, low-latency UI provider once the companion APK exists, while ADB remains a powerful bootstrap and shell provider.
- Android notification automation should be a first-class provider, not a UI-scraping afterthought. NotificationListenerService is the platform primitive for observing posted notifications, and direct reply flows are built around notification actions and RemoteInput. The correct abstraction is therefore `notifications.list`, `notifications.wait`, `notifications.dismiss`, and `notifications.reply` with capability flags for whether a specific notification exposes a reply action.
- Termux:API is useful as a bridge to Android device functions from command-line programs, especially for sensors, clipboard, notifications, telephony-adjacent functions, camera, battery, vibration, and TTS. It should be an optional provider discovered at runtime, not a mandatory dependency.
- Shizuku is best treated as an optional privilege provider that grants app code access to APIs with ADB/shell-like privileges on supported devices. It can close gaps between pure ADB and an installed companion APK, but it must be opt-in because its setup and privilege model are more advanced than the baseline no-root flow.

These sources do not change the core roadmap; they sharpen it. ADB-first remains the correct bootstrap. Accessibility and NotificationListener become the persistent event surfaces. Termux:API and Shizuku become optional capability boosters.

## 20. Agent-facing tool taxonomy

The MCP layer should avoid exposing only the raw CLI verbs. Agents perform better when tools have crisp contracts, stable schemas, and explicit failure modes. A suggested taxonomy:

### 20.1 Observation tools

- `phone.observe_ui(options)` — returns foreground app, screen metrics, elements, tree, relations, optional screenshot, and snapshot ID.
- `phone.find(selector, options)` — resolves a selector against the current or fresh snapshot and returns candidates with confidence and relation context.
- `phone.describe_screen()` — returns a compact, agent-friendly summary of likely tasks, primary actions, forms, lists, and hazards.
- `phone.watch_ui(selector_or_predicate, timeout)` — waits for UI state without busy polling.
- `phone.get_foreground_app()` — cheap app/activity query.

### 20.2 Action tools

- `phone.tap(target)` — accepts index, selector, semantic target, or coordinates, but always records how the target resolved.
- `phone.set_text(target, text, mode)` — prefers Accessibility `ACTION_SET_TEXT`, falls back to focus+input, clipboard paste, or ADB text escaping.
- `phone.scroll_until(selector, container, direction, limit)` — a first-class primitive because scrolling lists is one of the most common and brittle agent loops.
- `phone.navigate(action)` — back, home, recents, notifications shade, quick settings, app switch where supported.
- `phone.launch(package_or_intent)` — package launch, activity launch, deep link, or explicit intent.
- `phone.confirmed_action(intent)` — wraps high-risk actions in a user-visible approval flow.

### 20.3 Android capability tools

- `phone.notifications.list/filter/wait/reply/dismiss`.
- `phone.clipboard.read/write/clear`.
- `phone.intents.send/preview`.
- `phone.packages.list/resolve/launch/stop/clear_cache_when_allowed`.
- `phone.files.pick/share/open` for SAF-mediated user-approved file operations.
- `phone.media.capture_screenshot/capture_screen_recording` where permitted.
- `phone.device.state` for battery, charging, network, lock state, orientation, volume, do-not-disturb, locale, and permission state.

### 20.4 Runtime tools

- `phone.macro.create/validate/run/cancel/status`.
- `phone.events.subscribe` for UI, notification, clipboard, package, power, network, and schedule events.
- `phone.policy.explain(action)` to tell the agent why an action is allowed, denied, or needs confirmation.
- `phone.audit.query/export` for traceability.
- `phone.diagnostics.bundle` for support and bug reports.

## 21. Capability contracts and graceful degradation

Every tool should expose both a **logical capability** and the **provider path** that satisfied it. For example, `phone.set_text` might report:

```json
{
  "ok": true,
  "capability": "ui.set_text",
  "provider": "accessibility",
  "fallbacks_considered": ["adb_input_text", "clipboard_paste"],
  "target_resolution": {"selector": {"id": "com.example:id/message"}, "matched_i": 14},
  "snapshot_before": "snap_abc",
  "snapshot_after": "snap_def"
}
```

If the action is unavailable, the error should be actionable:

```json
{
  "ok": false,
  "error": {
    "code": "CAPABILITY_UNAVAILABLE",
    "capability": "notifications.reply",
    "missing_provider": "notification_listener",
    "user_action": "Enable droidjig Notification Access in Android Settings > Notifications > Device & app notifications > Notification access."
  }
}
```

This matters because an autonomous agent must distinguish "try a different UI route" from "ask the user to grant a permission" from "this phone cannot do that." Silent boolean failures are not acceptable at platform scale.

## 22. The daemon as the heart of the platform

A daemon should become the single writer and event broker for all phone actions. CLI and MCP clients should become frontends. The daemon provides:

- **Serialized action execution** so two agents or tools cannot tap/type simultaneously.
- **Snapshot cache and invalidation** with monotonic snapshot IDs, foreground app checks, and stale-index protection.
- **Provider lifecycle management** for ADB connections, companion APK WebSocket/Unix socket transports, notification subscriptions, Termux command probes, and optional Shizuku sessions.
- **Event fanout** to MCP clients, macros, logs, and optional local UI.
- **Policy enforcement** at one choke point rather than duplicated safety checks in every command.
- **Durable run records** with action IDs, parent task IDs, before/after snapshots, provider choice, errors, retries, and user approvals.

The daemon should start as an explicit `droidjig daemon` process, then later support Termux boot integration or a companion APK foreground service. Do not require a daemon for v1 primitives, but design new APIs so daemonization is a compatible evolution.

## 23. Macro/runtime design sketch

A macro should be a signed, auditable plan, not an opaque script. A YAML or JSON macro might look like:

```yaml
name: reply_to_priority_message
permissions:
  notifications.read: ["com.whatsapp", "org.signal"]
  notifications.reply: ["com.whatsapp", "org.signal"]
  ui.act: confirm
trigger:
  type: notification.posted
  filters:
    package_in: ["com.whatsapp", "org.signal"]
    text_regex: "urgent|asap|emergency"
conditions:
  - type: time_window
    after: "08:00"
    before: "22:00"
  - type: battery_min
    percent: 15
actions:
  - type: agent.summarize_notification
    save_as: summary
  - type: user.confirm
    message: "Reply to priority message? ${summary}"
  - type: notification.reply
    text: "I saw this and will respond shortly."
limits:
  max_runs_per_hour: 5
  cooldown_seconds: 300
```

Important runtime semantics:

- Each run has a `run_id`, parent trigger event, policy decision, and cancellation token.
- Variables are scoped: trigger variables, macro variables, secret variables, and runtime outputs.
- Actions are idempotent where possible and explicitly non-idempotent where not possible.
- Retries use bounded backoff and must not replay high-risk actions without policy re-check.
- Macros can require foreground user approval before crossing risk boundaries.

## 24. Safety model expansion: from denylist to risk ledger

The safety system should evolve from package denylist plus modes into a **risk ledger**:

| Risk level | Examples | Default behavior |
|---|---|---|
| Low | observe UI, read battery, list installed packages | allow + audit |
| Medium | tap benign settings, copy clipboard, launch app | allow in auto for trusted macros, audit |
| High | send message, post notification reply, change connectivity, install/uninstall package | require policy grant or confirmation |
| Critical | payments, purchases, password managers, banking, destructive data deletion, device admin, unknown consent dialogs | deny or require explicit one-time human approval |

The runtime should classify risk from multiple signals: package name, activity name, UI text, notification category, action type, selector text, intent action, amount/currency regexes, password fields, and whether the action leaves the device. The classifier does not need to be perfect; it needs to be conservative, explainable, and overrideable by the user.

Add a `policy.explain` output that agents can read before acting. This discourages blind retries when an action is blocked.

## 25. Memory, state, and knowledge layer

A capable autonomous phone agent needs durable memory, but it must be narrow and user-controlled:

- **Device profile:** Android version, OEM skin, screen metrics, density, navigation mode, wireless debugging behavior, installed providers, permissions granted, known flaky commands.
- **App profiles:** package names, launch intents, common selectors, known screens, risk hints, deep links, notification capabilities.
- **User preferences:** confirmation thresholds, quiet hours, guarded apps, allowed contacts/apps for autonomous replies, redaction rules.
- **Selector library:** stable selectors learned from successful interactions, keyed by app version and locale.
- **Failure memory:** provider errors, stale selector rates, uiautomator failure modes, reconnect history.

This should not become unrestricted personal-data hoarding. Store only operational metadata by default, redact text aggressively in logs, and make export/delete easy.

## 26. Evaluation and benchmark suite

The project should define repeatable phone-agent benchmarks early, even before all providers exist:

1. **Settings navigation:** launch Settings, find Wi-Fi, toggle a harmless setting only in confirm/dry-run tests, return home.
2. **Form filling:** open a local test APK or web page, fill fields with Unicode text, submit, verify state.
3. **Notification OTP flow:** receive a synthetic notification, extract code, paste into a test field.
4. **Messaging dry run:** detect a reply-capable notification, produce a would-reply action, require confirmation, and audit the denial/approval path.
5. **List extraction:** scroll a long RecyclerView and extract unique rows without duplicates.
6. **Recovery drill:** kill ADB connection, rotate screen, lock/wake device, and verify structured errors or reconnection.
7. **Safety drill:** show fake purchase/payment/password screens and verify policy blocks or requires confirmation.

Benchmarks should report success rate, median latency, action count, stale-target rate, provider fallback count, and number of human interventions.

## 27. Concrete next planning artifacts to add

Convert this brainstorming into focused implementation docs in this order:

1. `2026-06-21-droidjig-selector-and-tree-observation.md` — selectors, hierarchy, relations, stale snapshots, and matching confidence.
2. `2026-06-21-droidjig-provider-capabilities.md` — capability schema, provider registry, graceful degradation, structured errors.
3. `2026-06-21-droidjig-daemon-runtime.md` — single-writer action queue, event bus, run IDs, snapshot cache, audit schema v2.
4. `2026-06-21-droidjig-notification-provider.md` — companion APK NotificationListener design, reply semantics, privacy redaction, permission UX.
5. `2026-06-21-droidjig-accessibility-companion.md` — AccessibilityService event stream, gesture dispatch, text setting, transport, safety constraints.
6. `2026-06-21-droidjig-macro-engine.md` — trigger/condition/action schema, variables, scheduler, policy gates, cancellation.
7. `2026-06-21-droidjig-evaluation-suite.md` — benchmark tasks, fixtures, fake provider simulator, real-device manual lane.

The implementation order should still protect the already-planned ADB foundation. The mistake to avoid is bolting platform concepts onto ad hoc CLI commands later. Add small seams now: capability discovery, selectors, structured errors, request IDs, and audit fields. Those seams make the larger automation platform possible without rewriting v1.
