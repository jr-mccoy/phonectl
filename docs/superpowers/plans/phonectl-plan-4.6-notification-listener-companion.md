# Plan 4.6 — NotificationListenerService companion (native Kotlin)

**Phase 2 of the complete companion-APK build** (index:
`phonectl-companion-apk-build-index.md`). Executes `android/accessibility-companion/SPEC.md §6`
and the notification toggles in `android/foreground-service/SPEC.md §6`. Depends on Plan 4.5
(transport, dispatcher, `TrustState`, handshake, settings UI) already in place.

## Context

Plan 4.5 ships the MVP companion (transport + emergency-stop/trust + AccessibilityService) but
defers notifications. The Python side is already done and green: `providers/notifications.py`
prefers the companion over read-only Termux:API, normalizes companion JSON, and exposes
`list` / `wait` / `reply` / `dismiss`. This plan adds the **Kotlin
`NotificationListenerService`** and routes its three methods over the existing loopback transport,
behind four handshake capability keys so the build stays capability-gated.

The Python consumer is the authority. Key facts from `src/phonectl/providers/notifications.py`:
- `list()` reads `data["notifications"]` and `parse_notification(r, source="companion")` reads
  `key, package, title, text, category, post_time, actions:[{title, remote_input}]`;
  `can_reply = any(a["remote_input"])`, `can_dismiss = True` (Python-set).
- `reply(key, text)` → `notifications_reply {key, text}`; `dismiss(key)` →
  `notifications_dismiss {key}`. Both require `ping()` to succeed first.
- `capabilities()` advertises `observe_notifications, notifications_wait, notifications_reply,
  notifications_dismiss` when the companion is reachable.

## Contract (the non-negotiable surface)

- **`notifications_list`** params `{}` → `{notifications:[{key, package, title, text, category,
  post_time, actions:[{title, remote_input}]}]}`. `category` may be JSON `null`; `post_time` is an
  epoch-ms int; `remote_input:true` iff the `Notification.Action` carries a `RemoteInput`.
  Authority fixture: `tests/test_providers_notifications.py` `COMPANION_RAW`:
  ```
  {"key":"0|com.msg|42|tag|10123","package":"com.msg","title":"Alice","text":"see you at 6?",
   "category":"msg","post_time":1718900000000,
   "actions":[{"title":"Reply","remote_input":true},{"title":"Mark read"}]}
  ```
- **`notifications_reply`** params `{key, text}` → `{sent:true}`. Errors: `no_remote_input` (no
  `RemoteInput` action on the notification), `not_found` (key no longer active).
- **`notifications_dismiss`** params `{key}` → `{dismissed:true}`. Error: `not_found`.
- **Handshake** gains four keys (default **on**): `observe_notifications, notifications_wait,
  notifications_reply, notifications_dismiss`. A method whose toggle is off returns
  `{ok:false, error:{code:"capability_disabled"}}`.
- The Python suite stays green **unchanged** — these keys are simply absent until this plan lands.

## New / changed source

```
app/src/main/kotlin/com/phonectl/companion/
  service/CompanionNotificationListenerService.kt   # new — active-notification cache + reply/dismiss
  json/Notifications.kt                             # new — org.json builders for the list shape
  state/Capabilities.kt                             # +4 notification keys
  state/TrustState.kt                               # +4 default-on toggles
  transport/Dispatcher.kt                           # route 3 methods + capability_disabled gate
  ui/SettingsActivity.kt                            # +4 SwitchPreferences + Trust & Safety text
app/src/main/AndroidManifest.xml                    # declare the listener service
app/src/test/kotlin/.../NotificationsContractTest.kt  # JVM tests vs Python fixture
```

## Tasks (one commit each; TDD: failing test → verify fail → minimal impl → verify pass → commit)

**Task 1 — service declaration + capability keys.** Declare
`CompanionNotificationListenerService` in `AndroidManifest.xml` with
`BIND_NOTIFICATION_LISTENER_SERVICE` and the
`android.service.notification.NotificationListenerService` intent filter. Add the four keys to
`Capabilities.kt` and `TrustState.kt` (default on) and include them in the `handshake` response.
Add a grant-check helper wrapping `NotificationManager.isNotificationListenerAccessGranted`.
JVM test: handshake JSON now contains the four keys with `true`, alongside the Phase-1 keys.
Commit: `feat(companion): declare NotificationListenerService + notification capability keys`.

**Task 2 — `notifications_list`.** `CompanionNotificationListenerService` keeps the bound
listener; the handler iterates `getActiveNotifications()` and `Notifications.kt` serializes each
`StatusBarNotification`: `key` from `sbn.key`, `package` from `sbn.packageName`, `title`/`text`
from `Notification.extras` (`EXTRA_TITLE`/`EXTRA_TEXT`), `category` from `notification.category`
(emit JSON `null` when absent), `post_time` from `sbn.postTime`, and `actions` from
`notification.actions` — each `{title: action.title, remote_input: action.remoteInputs != null}`.
JVM contract test builds a fake SBN-equivalent and asserts the serialized object equals
`COMPANION_RAW` field-for-field.
Commit: `feat(companion): notifications_list serializes active StatusBarNotifications`.

**Task 3 — `notifications_reply`.** Resolve the SBN by `key` from the active set; find the first
`Action` whose `remoteInputs` is non-empty; build the result bundle via
`RemoteInput.addResultsToIntent` and fire `action.actionIntent` (`PendingIntent.send`) →
`{sent:true}`. Return `{ok:false,code:"no_remote_input"}` when no action has a `RemoteInput`, and
`{ok:false,code:"not_found"}` when the key is absent. JVM tests cover both error codes (key-not-
found and no-remote-input) using a stubbed active-set.
Commit: `feat(companion): notifications_reply via RemoteInput.addResultsToIntent`.

**Task 4 — `notifications_dismiss`.** `cancelNotification(key)` → `{dismissed:true}`; return
`{ok:false,code:"not_found"}` when the key is no longer active. JVM test asserts both outcomes.
Commit: `feat(companion): notifications_dismiss via cancelNotification`.

**Task 5 — gating + trust UI + setup guidance.** In `Dispatcher.kt`, gate each of the three
methods on its `TrustState` toggle, returning `{ok:false,code:"capability_disabled"}` when off.
Add a `SwitchPreference` per notification key to `SettingsActivity` and extend the Trust & Safety
section: "Notifications read: title, text, and action labels of active notifications; replies are
sent only on behalf of phonectl commands." Add a setup hint pointing at
Settings → Notifications → Device & app notifications → [companion] → Allow, surfacing the
`isNotificationListenerAccessGranted` state. JVM test: a disabled toggle yields `capability_disabled`.
Commit: `feat(companion): gate notification methods + trust toggles + grant guidance`.

## Verification

1. **CI / unit** — `./gradlew assembleDebug test` green; the new `NotificationsContractTest`
   asserts `notifications_list` JSON equals `COMPANION_RAW` and the error codes match.
2. **Python suite unchanged** — `pytest -v` stays at 579 passing; the four new keys are additive.
3. **On-device** (deferred to Plan 4.8's smoke matrix; ROM-specific): grant notification access,
   then `phonectl` `list` → `reply` → `dismiss` round-trips against a real messaging notification,
   and `can_reply` reflects the `RemoteInput` presence.

## Deferred / non-goals

- **Notification *events*** (push-style `notification` entries in the `events` ring) — the
  `events` method already exists from Plan 4.5; surfacing `NotificationListener` callbacks into it
  is Plan 4.8 hardening, not required for `list/reply/dismiss`.
- **Termux:API parity** — the Python side already falls back to read-only Termux:API; no Kotlin work.
- **Per-payload logging** — method names + outcomes only (never reply text), per index invariant 5.
