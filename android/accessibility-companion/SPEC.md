# AccessibilityService Companion APK — Design Specification

**Plan 4.1** | Phase 4 of the phonectl platform roadmap.

This document specifies the Kotlin AccessibilityService companion that the Python
`AccessibilityProvider` talks to over a local transport (socket, Plan 4.3). The APK is built
separately from this spec; this document is the input to that build.

**Transport spec:** See `android/foreground-service/SPEC.md` for the full wire protocol (framing,
request/response envelope, `request_id`/`timeout`/`version`, capability negotiation,
`handshake` method, stale-response protection, and the persistent "Stop phonectl" notification /
Quick-Settings tile / per-capability toggle UI).

---

## 1. Service surface

The companion runs as an Android `AccessibilityService` with the following flags:

```xml
<accessibility-service
    android:accessibilityFlags="flagRetrieveInteractiveWindowContent|flagReportViewIds"
    android:canRetrieveWindowContent="true"
    android:canPerformGestures="true"
    android:canRequestFilterKeyEvents="false"
    android:notificationTimeout="100" />
```

It exposes a **request/response** interface over the local transport. Each incoming JSON object is
a **request**; each outgoing JSON object is a **response**. The transport is defined in Plan 4.3
(`android/foreground-service/SPEC.md`); this document defines the **message contract**,
transport-agnostic.

---

## 2. Message contract

### Request envelope

```json
{
  "version": 1,
  "request_id": "<hex uuid>",
  "method": "<method_name>",
  "params": { ... },
  "timeout": 2.0
}
```

### Response envelope

```json
{
  "version": 1,
  "request_id": "<echoed from request>",
  "ok": true,
  "data": { ... }
}
```

On failure:

```json
{
  "version": 1,
  "request_id": "<echoed from request>",
  "ok": false,
  "error": { "code": "<error_code>", "message": "<human-readable>" }
}
```

**Stale-response protection:** the Python side drops any response whose `request_id` does not
match the outstanding request. The companion MUST echo `request_id` exactly.

---

## 3. Methods

### `ping`

Params: `{}`

Returns: `{"pong": true}`

Used by `AccessibilityProvider.is_available()`. No side-effects.

---

### `observe_native`

Params: `{}`

Returns the native UI tree:

```json
{
  "windows": [
    {
      "id": <int>,
      "type": "application" | "system" | "ime" | "accessibility_overlay",
      "package": "<package_name>",
      "nodes": [ <node>, ... ]
    }
  ],
  "screen": { "width": <int>, "height": <int> },
  "keyguard": { "showing": <bool>, "secure": <bool> },
  "focus": { "package": "<focused window's package>", "activity": "<activity class or empty>" }
}
```

`keyguard` mirrors `KeyguardManager` (`isKeyguardLocked` / `isDeviceLocked`) and `focus` names
the focused window's package (activity from the last `TYPE_WINDOW_STATE_CHANGED` event when its
package matches). Together they let the Python provider build the complete snapshot — lock state
and focused app included — from this single RPC, with no per-observe ADB `dumpsys window`
augment. Older payloads without these keys stay valid: the Python side falls back to the ADB
augment.

**Node shape:**

```json
{
  "node_id": "<stable view-id or generated>",
  "text": "<CharSequence or empty string>",
  "class": "<fully qualified class name>",
  "content_desc": "<accessibility content description or empty string>",
  "bounds": [left, top, right, bottom],
  "resource_id": "<viewIdResourceName or empty string>",
  "actions": ["click", "long_click", "scroll_forward", "scroll_backward", "set_text", ...],
  "checkable": false,
  "checked": false,
  "clickable": false,
  "enabled": true,
  "focused": false,
  "scrollable": false,
  "password": false
}
```

Node `actions` lists the `AccessibilityNodeInfo.AccessibilityAction` constants available on that
node. The Python caller uses this list to decide which `semantic_action` calls are valid before
sending them, avoiding round trips.

Implementation: walk `AccessibilityService.getWindows()` recursively, serialise each
`AccessibilityNodeInfo`. Nodes with no text, no content_desc, and no meaningful actions MAY be
omitted to reduce payload size (leave the decision to the implementation).

---

### `gesture`

Params: `{"type": "tap", "x": <int>, "y": <int>}` — single tap at screen coordinates.

Params: `{"type": "swipe", "x1": <int>, "y1": <int>, "x2": <int>, "y2": <int>, "ms": <int>}` —
swipe from `(x1,y1)` to `(x2,y2)` over `ms` milliseconds.

Returns: `{"applied": true}`

Implementation: `GestureDescription.Builder` + `dispatchGesture`. Swipe uses a linear
`StrokeDescription`. Errors (e.g. gesture rejected) return `ok: false`.

---

### `key`

Params: `{"keycode": "<KEYCODE_* name or integer>"}`

Returns: `{"applied": true}`

Implementation: `performGlobalAction` for standard global keys (HOME, BACK, RECENTS,
NOTIFICATIONS, QUICK_SETTINGS), `dispatchGesture` for others, or `rootInActiveWindow` +
`ACTION_ACCESSIBILITY_FOCUS` + key event injection where available.

---

### `set_text`

Two modes:

**Mode `set`** — `ACTION_SET_TEXT` on a specific node (IME-independent, precise):

Params: `{"node_id": "<node_id>", "text": "<value>", "mode": "set"}`

Implementation: find the node by `node_id` in the current window tree, call
`node.performAction(ACTION_SET_TEXT, bundle)` where `bundle` has
`EXTRA_DATA_TEXT_CHARACTER_SEQUENCE`.

**Mode `type`** — focus the currently focused node and set its text:

Params: `{"text": "<value>", "mode": "type"}`

Implementation: find the focused editable node (`isFocused && isEditable`), apply
`ACTION_SET_TEXT` on it.

Both modes return: `{"applied": true}` on success, `ok: false` if the node is not found or the
action is not supported.

---

### `semantic`

Params: `{"node_id": "<node_id>", "action": "click" | "long_click" | "scroll_forward" | "scroll_backward" | "expand" | "collapse" | "dismiss"}`

Maps action strings to `AccessibilityNodeInfo` action constants:

| String | Constant |
|---|---|
| `click` | `ACTION_CLICK` |
| `long_click` | `ACTION_LONG_CLICK` |
| `scroll_forward` | `ACTION_SCROLL_FORWARD` |
| `scroll_backward` | `ACTION_SCROLL_BACKWARD` |
| `expand` | `ACTION_EXPAND` |
| `collapse` | `ACTION_COLLAPSE` |
| `dismiss` | `ACTION_DISMISS` |

Returns: `{"performed": "<action>"}` on success; `ok: false` with `code: "unsupported_action"` if
the node does not list the action in its `actions` array.

---

### `launch`

Params: `{"package": "<package_name>"}`

Returns: `{"launched": true}`

Implementation: `context.startActivity(Intent(Intent.ACTION_MAIN).setPackage(package)
.addCategory(Intent.CATEGORY_LAUNCHER).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))`.

---

### `screencap`

Params: `{"path": "<absolute path on device>"}`

Returns: `{"path": "<path>"}`

Implementation: `takeScreenshot` (API 30+) or fallback via `MediaProjection` if available.
Writes a PNG to `path`. Returns `ok: false` on API < 30 without a fallback.

`path` must resolve under the companion's own storage roots (files/cache dirs, internal or
app-specific external); anything else is refused with `path_rejected`. Cross-UID callers that
need the pixels use `screenshot` instead.

---

### `screenshot`

Params: `{}`

Returns: `{"format": "png", "data": "<base64 PNG bytes>"}`

The pixels travel over the token-authenticated loopback socket; the companion persists
**nothing** — no path parameter exists, so there is no on-device file-write surface at all.
The caller (a different Android UID that cannot read the companion's storage) decodes and
stores the image under its own storage. Refused with `guarded_action` when the foreground
app is guarded, like `observe_native`/`screencap`; `screencap_unavailable` when capture or
PNG encoding fails.

**Capability toggle:** `observe_screenshot` gates this method (`capability_disabled` when off).

---

### `events`

Params: `{"since": <cursor_int>, "max": <int>}`

Returns:

```json
{
  "events": [
    {
      "seq": <monotonic int>,
      "type": "window_state_changed" | "content_changed" | "view_focused" | "view_clicked" | "notification",
      "package": "<package>",
      "ts": <epoch_ms>
    }
  ],
  "cursor": <new_cursor_int>
}
```

Implementation: the service buffers `AccessibilityEvent` callbacks in a ring buffer (max 200
events). `events` returns all events with `seq > since`, up to `max`. The `cursor` in the
response equals the `seq` of the last returned event (or `since` if none). The Python caller
passes `cursor` back as `since` on the next call to get only new events.

If `since` is 0, return the most recent `max` events.

---

## 4. Compatibility mode

The Python side converts native JSON to uiautomator-compatible XML via `native_tree.to_compat_xml`
so that `observer`/`ui_parser` work unchanged. JSON is the canonical wire format; the companion
does NOT need to emit XML. The compatibility conversion is entirely on the Python side.

---

## 5. Permissions and trust

The companion APK requires:

- `android.permission.BIND_ACCESSIBILITY_SERVICE` — implicit, from the `<service>` element.
- The user must enable the service in Settings → Accessibility.

**Trust model:**
- The companion only listens on a local loopback socket (Plan 4.3). No network interface is
  bound. No analytics, no telemetry, no cloud calls.
- The companion does not implement its own authentication. Trust is physical: only processes on
  the same device can reach the loopback socket.
- The Python side (Plan 4.3) will add a trust toggle (enabled/disabled) in `phonectl setup`; the
  companion respects a local enable-file or a `phonectl`-specific preference.

---

## 6. NotificationListenerService methods (Plan 4.2)

The companion APK also implements a `NotificationListenerService` that surfaces notifications as a
provider. These methods are dispatched over the **same** local transport as the
`AccessibilityService` methods above.

### `notifications_list`

Params: `{}`

Returns:

```json
{
  "notifications": [
    {
      "key": "0|com.msg|42|tag|10123",
      "package": "com.msg",
      "title": "Alice",
      "text": "see you at 6?",
      "category": "msg",
      "post_time": 1718900000000,
      "actions": [
        {"title": "Reply", "remote_input": true},
        {"title": "Mark read"}
      ]
    }
  ]
}
```

`actions` lists the notification actions. An action with `"remote_input": true` supports direct
reply via `notifications_reply`. The Python side sets `can_reply` on the normalized item when any
action has `remote_input: true`.

Implementation: iterate `NotificationListenerService.getActiveNotifications()`, serialize each
`StatusBarNotification` with its `Notification.actions` and `RemoteInput` presence.

---

### `notifications_reply`

Params: `{"key": "<StatusBarNotification key>", "text": "<reply text>"}`

Returns: `{"sent": true}`

Implementation: find the `StatusBarNotification` by `key`, locate the first `Action` with a
`RemoteInput`, construct a `PendingIntent` with the reply text filled into the `RemoteInput`'s
result key via `RemoteInput.addResultsToIntent`, and fire the intent.

Returns `ok: false` with `code: "no_remote_input"` if the notification has no `RemoteInput`
action, and `code: "not_found"` if the key is not in the active notification list.

---

### `notifications_dismiss`

Params: `{"key": "<StatusBarNotification key>"}`

Returns: `{"dismissed": true}`

Implementation: call `NotificationListenerService.cancelNotification(key)`. Returns `ok: false`
with `code: "not_found"` if the key is no longer active.

---

### Permission and grant flow

The `NotificationListenerService` requires `android.permission.BIND_NOTIFICATION_LISTENER_SERVICE`
(implicit from the `<service>` element) plus a runtime grant:

```
Settings → Notifications → Device & app notifications → [companion APK] → Allow
```

`phonectl setup notifications` guides the user through this flow, checks the grant with
`NotificationManager.isNotificationListenerAccessGranted`, and reports whether the service is
currently active.

---

## 7. ML-Kit OCR method (Plan 4.4)

### `ocr_screen`

Request params: `{}`. Captures the current display with Android `takeScreenshot()` inside the accessibility companion, feeds the in-memory bitmap directly to ML Kit, and returns the same response shape as `ocr_image`:

```json
{ "regions": [] }
```

This path does not persist a screenshot and should be preferred by clients when the handshake advertises `observe_ocr_screen: true`.

### `ocr_image`

Params: `{"path": "<absolute path on device — existing PNG>"}`

Returns:

```json
{
  "regions": [
    {
      "text": "Wi-Fi",
      "bounds": [44, 380, 164, 420],
      "confidence": 0.965
    }
  ]
}
```

`bounds` are `[left, top, right, bottom]` in screen pixels, matching the convention used by the
structured UI tree. `confidence` is `0.0–1.0`.

Implementation: read the PNG at `path` using `BitmapFactory.decodeFile`, run
`com.google.mlkit.vision.text.TextRecognition` (bundled ML-Kit), and serialize each
`TextBlock`/`TextLine`/`TextElement` as a flat region list. The Python caller receives the
regions and applies a `min_confidence` filter.

**Capability toggle:** `observe_ocr_screen` gates `ocr_screen`; `observe_ocr` must be `true` in the handshake's capabilities map for `ocr_image` to be dispatched; when the toggle is off, the companion returns `ok: false` with
`code: "capability_disabled"`.

**Source precedence:** for whole-screen OCR, the Python `OcrProvider` prefers companion `ocr_screen` when advertised to avoid a path-based screenshot handoff. `ocr_image` remains for backward compatibility and for explicit image paths; local Tesseract can still be used when direct companion screen OCR is unavailable.

---

## 8. Non-goals (Plan 4.1 scope)

- The **Kotlin implementation** — this spec was the design input; the code now ships as
  `com.phonectl.companion` under `android/accessibility-companion/`, built as a debug-APK
  artifact by the `android.yml` workflow.
- **`SocketTransport`** — specified in `android/foreground-service/SPEC.md`.
- **Multi-touch / pinch** — the `gesture` method is extensible; pinch is Phase 7 if needed.
- **Network exposure** — local-only, explicitly out of scope.
- **Background event loop** — `poll_events` is a single-call primitive; the daemon (Phase 5)
  drives continuous fanout.

---

## 8. Error codes

| `error.code` | Meaning |
|---|---|
| `unknown_method` | The requested method is not implemented |
| `node_not_found` | `node_id` not found in the current window tree |
| `unsupported_action` | The node does not support the requested semantic action |
| `gesture_rejected` | The gesture dispatcher rejected the gesture |
| `screencap_unavailable` | Screenshot API not available on this device/API level |
| `handler_error` | Unexpected exception in the handler |
| `no_remote_input` | The notification has no `RemoteInput` action |
| `not_found` | The notification key is no longer in the active list |
