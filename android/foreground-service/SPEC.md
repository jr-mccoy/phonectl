# Foreground Service Transport + Emergency-Stop + Trust UX — Android Design Spec

**Plan 4.3** | Phase 4 of the phonectl platform roadmap.

This document specifies the Android-side foreground service that hosts the loopback TCP server
the Python `SocketTransport` connects to. This document was the input to the build; the Kotlin
implementation now ships, built by Plans 4.5–4.8 (`com.phonectl.companion`, CI debug-APK artifact).
See `docs/superpowers/plans/phonectl-companion-apk-build-index.md`.

---

## 1. Foreground service

The companion runs as an Android `Service` with `startForeground` so the OS keeps it alive. It
binds a **loopback TCP server** on `127.0.0.1` only — never `0.0.0.0` or any external interface.

```kotlin
// Bind loopback only; refuse any other host in configuration.
serverSocket = ServerSocket()
serverSocket.bind(InetSocketAddress("127.0.0.1", port))
```

The port is user-configurable (default `8765`) and is communicated to the Python side via
`companion_port` in `~/.config/phonectl/config.json`.

### Thread model

Each accepted client connection spawns a handler thread. The handler reads newline-delimited JSON
requests, dispatches to the appropriate subsystem (AccessibilityService, NotificationListenerService,
etc.), and writes newline-delimited JSON responses. The connection is long-lived; a client may send
multiple requests over one connection.

---

## 2. Wire protocol

**Framing:** newline-delimited JSON — one JSON object per line, terminated by `\n`. No length
prefix. Lines that are not valid JSON are silently dropped.

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

or on error:

```json
{
  "version": 1,
  "request_id": "<echoed from request>",
  "ok": false,
  "error": { "code": "<string>", "message": "<string>" }
}
```

### Stale-response protection

Every response **must** echo the `request_id` from the triggering request. The Python
`SocketTransport` drops any response whose `request_id` does not match the pending request.
The service must never emit unsolicited responses.

### Version negotiation

`version` is currently `1`. The service should reject requests with an unknown major version and
return `{"ok": false, "error": {"code": "version_mismatch", ...}}`.

---

## 3. Methods

### `ping`

Liveness check. The service must respond within the caller's `timeout`.

**Response `data`:** `{}`

### `handshake`

Returns the companion's current state: protocol version, per-capability user-enabled set, and the
emergency-stop flag.

**Response `data`:**

```json
{
  "version": 1,
  "capabilities": {
    "observe_ui_native": true,
    "observe_ui_events": false,
    "act_gesture_native": true,
    "act_set_text_native": true,
    "act_semantic_action": true,
    "observe_notifications": true,
    "notifications_wait": true,
    "notifications_reply": true,
    "notifications_dismiss": true
  },
  "stopped": false
}
```

`capabilities` reflects the **user-enabled toggle set** (what the user has granted in the APK's
per-capability UI). The Python side intersects this with each provider's technically-supported
capabilities via `trust.gate_capabilities`.

`stopped` is `true` when the user has engaged the "Stop phonectl" emergency control. A `true` value
causes the Python `audit.kill_switch_active` to return `true` via the `extra_checks` mechanism,
blocking all CLI, MCP, and daemon actions immediately.

All other methods (observe_native, act_gesture, etc.) are specified in
`android/accessibility-companion/SPEC.md`.

---

## 4. Persistent "Stop phonectl" notification

The foreground service posts a persistent **ongoing** notification that cannot be dismissed while
the service is running. The notification has a single action button:

- **Label:** "Stop phonectl"
- **Action:** Sets `stopped = true` in the handshake response and posts a second notification
  confirming the stop. Does **not** kill the service process — the Python side re-reads `handshake`
  on the next cycle and blocks actions via `kill_switch_active`.
- **Resume:** The companion's per-capability toggle UI includes a "Resume" button that sets
  `stopped = false`.

This mirrors the `$PHONECTL_HOME/STOP` sentinel file: **either** the file or `stopped=true` in
the handshake is sufficient to block actions. The file is the hard guarantee (survives APK crashes);
the companion flag is the low-latency path for the running session.

The notification must be posted with `NotificationCompat.FLAG_ONGOING_EVENT` and
`NotificationCompat.FLAG_NO_CLEAR`.

---

## 5. Quick-Settings tile

A `TileService` toggles the entire automation surface on/off:

- **Active state:** companion running, `stopped=false`. Tile shows a distinct icon.
- **Inactive state:** sets `stopped=true` (equivalent to tapping "Stop phonectl" notification).
  Tile shows a greyed icon.
- Tapping the tile again while inactive sets `stopped=false` (resume).

The tile does not stop or start the foreground service — it only flips the `stopped` flag. The
service lifecycle is managed separately (see §8 below).

---

## 6. Per-capability toggle UI

The companion APK exposes a settings screen listing every capability the AccessibilityService and
NotificationListenerService can provide:

| Capability key              | Description                        |
|-----------------------------|------------------------------------|
| `observe_ui_native`         | Read on-screen UI elements         |
| `observe_ui_events`         | Stream real-time UI change events  |
| `act_gesture_native`        | Perform touch gestures             |
| `act_set_text_native`       | Fill text fields via AccessibilityNodeInfo |
| `act_semantic_action`       | Trigger semantic accessibility actions |
| `observe_notifications`     | Read notifications                 |
| `notifications_wait`        | Wait for a matching notification   |
| `notifications_reply`       | Reply to inline-reply notifications|
| `notifications_dismiss`     | Dismiss notifications              |

Each toggle is a `SwitchPreference`. The current enabled set is returned by `handshake.capabilities`
and intersected by the Python `trust.gate_capabilities` function — a disabled toggle removes the
grant from the provider graph; the registry transparently falls back to ADB or reports
`CapabilityUnavailableError`.

Defaults: all capabilities are **enabled** on first install. The user opts out per capability.

---

## 7. Trust guarantees (strategy §11.4)

The companion settings screen must display a **Trust & Safety** section explaining:

1. **What is read:** the on-screen UI tree (element text, resource IDs, bounds, clickability) and
   notifications (title, text, actions). **No passwords are read** from password fields
   (`inputType == TYPE_TEXT_VARIATION_PASSWORD`); the service must explicitly guard these.
2. **What is controlled:** touch gestures (within the user's gesture-enabled area), text insertion,
   and notification actions — only on behalf of phonectl CLI/MCP commands, never autonomously.
3. **Local only:** the TCP server binds `127.0.0.1` only. No data leaves the device over the
   network. The loopback + Android app sandboxing is the trust boundary.
4. **Audit visibility:** every action is logged to `~/.config/phonectl/actions.jsonl` on the Python
   side. Users can read this log at any time.
5. **Password/payment screen warnings:** the service detects password fields and payment-screen
   indicators (app package allowlist, window title heuristics) and sets a flag in `observe_native`
   responses. The Python `policy` module uses these flags to deny or confirm-gate actions.
6. **Guarded-app behavior:** the service refuses gesture actions in apps on the guarded-app list
   (configured in `~/.config/phonectl/config.json`). It returns `{"ok": false, "error": {"code":
   "guarded_action", ...}}` for these requests.

---

## 8. Lifecycle and autostart seam (Phase 5)

The foreground service is started explicitly by the user (e.g., via a launcher shortcut or the
Quick-Settings tile). **Termux:Boot autostart** is a Phase 5 daemon concern; this plan only defines
the seam:

- The service exports a `start`/`stop` broadcast intent the Phase 5 daemon can send via
  `adb shell am broadcast`.
- The daemon owns the persistent connection lifecycle, watchdog, and reconnect policy.

The loopback TCP server **becomes the daemon's IPC surface** in Phase 5 — the same protocol, the
same port, but the daemon holds the persistent connection rather than the CLI reconnecting per
command.

---

## 9. Security notes

- **No TLS / auth token** at this stage. Loopback-only + Android app sandboxing is the trust
  boundary. Other apps on the device cannot bind to the server socket because Android's network
  namespace isolates loopback per-app (on Android 12+ with per-app network namespaces). An auth
  token can be added when multi-client access (daemon + external clients) lands in Phase 5.
- The service must close accepted connections after a configurable idle timeout (default 30 s) to
  avoid resource leaks.
- The service **must not** log request payloads — only method names and outcomes — to avoid
  capturing sensitive text (e.g., from `act_set_text_native`).
