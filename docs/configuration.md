# Configuration, results, and tuning

The result envelope every layer returns, the config keys that change behavior, how droidjig
recovers a dropped connection, and what to turn when it feels slow.

---

## Structured results & capabilities

`droidjig` has a stable structured-result contract for JSON-capable surfaces. `droidjig observe --json` and `droidjig doctor --json` return an envelope with `ok: true`; typed platform errors return `ok: false` with actionable flags instead of tracebacks.

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

---

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
