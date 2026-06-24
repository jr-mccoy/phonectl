# Plan 4.5 — Accessibility Companion APK (MVP loop), native Kotlin build

**Phase 4 native effort** | Executes `android/accessibility-companion/SPEC.md` (4.1) and
`android/foreground-service/SPEC.md` (4.3). Notification listener (4.2) and ML-Kit OCR (4.4) are
deferred to a follow-up cut.

## Context

phonectl today drives the phone over **no-root ADB** (Wireless Debugging on `127.0.0.1`). That
works for the observe→act loop but can't provide a *persistent* on-device observe/act surface:
real-time UI events, gesture injection that survives locked-down apps, an in-app emergency-stop,
and (later) notifications/OCR. Phase 4 already shipped the **entire Python side**
(`providers/accessibility.py`, `providers/transport.py`, `providers/notifications.py`,
`providers/ocr.py`, `trust.py`, `native_tree.py`) and it is green. What's missing is the **other
half of the contract**: the Kotlin companion app the Python side talks to. The two SPECs plus the
existing Python consumer and its test fixtures are an **executable specification** — the APK must
match them field-for-field.

This plan builds the **MVP companion**: the foreground-service transport, the emergency-stop /
trust surface, and the AccessibilityService methods — enough to replace ADB for the core loop and
add real-time UI events. **Out of scope this cut** (deferred): the NotificationListenerService
(4.2) and ML-Kit OCR (4.4). Their Python providers already exist and degrade cleanly when their
handshake capability keys are absent, so deferring them is a no-op on the Python side.

The build is greenfield: `android/` currently contains **only the two SPEC.md files** — no Gradle,
Kotlin, or manifest. All scaffolding is created here, and a **GitHub Actions** workflow produces an
installable debug APK on every push.

## Contract (the non-negotiable surface the APK must satisfy)

Derived from the Python consumer + its test fixtures — these are the authority, the SPECs are the
prose:

- **Wire framing** (`src/phonectl/providers/transport.py`): newline-delimited JSON, one object per
  line, `\n`-terminated. Loopback only.
- **Request envelope** (Python→APK): `{version:1, request_id:<hex>, method, params:{}, timeout:<s>}`.
- **Response envelope** (APK→Python): success `{version:1, request_id:<echoed>, ok:true, data:{...}}`;
  error `{version:1, request_id:<echoed>, ok:false, error:{code, message}}`.
- **Stale-response protection**: every response MUST echo `request_id` exactly; never emit
  unsolicited responses (Python drops non-matching IDs until match or timeout).
- **`native_tree.to_compat_xml`** consumes `observe_native` output verbatim: `{windows:[{id, type,
  package, nodes:[{node_id, text, class, content_desc, bounds:[l,t,r,b], checkable, checked,
  clickable, enabled, focused, scrollable, password}]}], screen:{width,height}}`. Field names and
  bounds order are fixed.
- **Handshake** (`trust.negotiate` / `trust.gate_capabilities`): `{version:1, capabilities:{<key>:bool},
  stopped:bool}`. MVP advertises the keys the Python side recognizes
  (`src/phonectl/capabilities.py`): `observe_ui_native`, `observe_ui_events`, `act_gesture_native`,
  `act_set_text_native`, `act_semantic_action`. (Notification/OCR keys omitted this cut.)
- **Method responses**: `ping`→`{pong:true}` (Python only checks `ok`), `gesture`→`{applied:true}`,
  `set_text`→`{applied:true}`, `key`→`{applied:true}`, `semantic`→`{performed:<action>}`,
  `launch`→`{launched:true}`, `screencap`→`{path:<path>}`, `events`→`{events:[...], cursor:<int>}`.

The MVP must keep the **Python suite green unchanged** and pass an on-device smoke driving the real
`SocketTransport`/`AccessibilityProvider` against the running APK.

## Build target & toolchain (decided)

- **Language/build**: Kotlin + Gradle (Kotlin DSL), Android Gradle Plugin. Plain Android **Views**
  for the tiny settings UI (a few `SwitchPreference`s + a Trust & Safety text block) — no Compose,
  keep the APK lean and deps minimal.
- **`minSdk 31` (Android 12), `targetSdk` latest.** minSdk 31 is **security-relevant, not
  arbitrary**: the trust model (foreground-service SPEC §9) relies on Android 12+ **per-app loopback
  network-namespace isolation** as the boundary that stops other apps reaching the server socket,
  and `takeScreenshot` (API 30+) is used directly. Target device is a Galaxy S25 Ultra (Android 15),
  so this costs nothing.
- **CI**: GitHub Actions workflow (`.github/workflows/android.yml`) — JDK 17 +
  `android-actions/setup-android`, `./gradlew assembleDebug test`, upload the APK as a build
  artifact. All Gradle wrapper files committed so CI and a local desktop build are identical.

## Project layout (new)

```
android/accessibility-companion/
  SPEC.md                      # existing — the design input
  app/
    build.gradle.kts
    src/main/AndroidManifest.xml
    src/main/kotlin/com/phonectl/companion/
      transport/   Server.kt  Envelope.kt  Dispatcher.kt   # NDJSON loopback TCP + request routing
      service/     CompanionForegroundService.kt           # foreground svc, hosts Server, owns notification
                   CompanionAccessibilityService.kt        # the AccessibilityService methods
                   StopTileService.kt                      # Quick-Settings tile
      state/       TrustState.kt  Capabilities.kt          # SharedPreferences-backed toggles + stopped flag
      ui/          SettingsActivity.kt                     # per-capability toggles + Trust & Safety section
      json/        Json.kt                                 # tiny org.json builders (no Moshi/Gson dep)
    src/test/kotlin/...                                    # JVM unit tests for serialization + envelopes
  settings.gradle.kts
  build.gradle.kts
  gradle/wrapper/gradle-wrapper.properties
  gradlew  gradlew.bat
.github/workflows/android.yml
```

Use `org.json` (bundled in Android, available on the JVM test classpath via a test dep) for
serialization so node/envelope shapes are built by hand and provably match the Python fixtures — no
reflection-based mapper that could silently rename a field.

## Implementation tasks (one commit each)

**Task 1 — Gradle/Kotlin/CI scaffold.** Create the Gradle project, `AndroidManifest.xml` (declares
the foreground service, the AccessibilityService with the SPEC §1 flags
`flagRetrieveInteractiveWindowContent|flagReportViewIds`, `canPerformGestures=true`, the TileService,
the SettingsActivity, and `FOREGROUND_SERVICE`/`POST_NOTIFICATIONS` perms), and the GitHub Actions
workflow. Verify: CI builds an empty-but-installable `assembleDebug` APK.

**Task 2 — Loopback transport + envelope + dispatch loop.** `Server.kt`: `ServerSocket` bound to
`127.0.0.1:<companion_port>` (default 8765), **refuse any non-loopback host**; per-connection handler
thread reading NDJSON lines; silently drop non-JSON lines; reject unknown major `version` with
`{ok:false,error:{code:"version_mismatch"}}`; **echo `request_id` on every response**; never emit
unsolicited responses; close idle connections after 30s. `Dispatcher.kt` routes `method`→handler and
wraps results/exceptions into the success/error envelope (`handler_error` on unexpected throw). JVM
unit tests assert exact envelope JSON against the `test_transport.py` shapes.

**Task 3 — handshake + ping + capability/stopped state.** `TrustState.kt` (SharedPreferences):
per-capability enabled set (all **on** by default per SPEC §6) and the `stopped` flag. `handshake`
returns `{version:1, capabilities:{...gated keys...}, stopped:<bool>}`; `ping` returns `{pong:true}`.
**STOP-sentinel parity**: `stopped` reads true if *either* the in-app flag is set *or* the
`$PHONECTL_HOME/STOP` file exists (foreground-service SPEC §4). Unit-test the handshake JSON against
`test_trust.py`'s expected keys.

**Task 4 — emergency-stop notification + Quick-Settings tile.** Foreground service posts the ongoing,
non-dismissable "Stop phonectl" notification (`FLAG_ONGOING_EVENT|FLAG_NO_CLEAR`) with a Stop action
that sets `stopped=true` and posts a confirmation; a Resume path clears it. `StopTileService.kt`
flips the same flag (active/greyed states). Neither kills the process — they only flip the flag the
Python side re-reads via `handshake` (→ `audit.kill_switch_active`).

**Task 5 — AccessibilityService methods.** `CompanionAccessibilityService.kt`:
- `observe_native`: walk `getWindows()`/`rootInActiveWindow` recursively → the exact node/window
  JSON `native_tree.to_compat_xml` expects. **`node_id` must be re-resolvable**: prefer
  `viewIdResourceName` (enabled by `flagReportViewIds`); for nodes without one, assign a
  **deterministic path-based id** (window-id + child-index path) so the same node yields the same id
  while the tree is unchanged, letting `set_text`/`semantic` re-find it on a later request.
  **Password guard**: nodes with `inputType & TYPE_TEXT_VARIATION_PASSWORD` set `password:true` and
  **never** include their `text` (trust SPEC §7.1).
- `gesture` (tap/swipe via `GestureDescription.Builder`+`dispatchGesture`), `key`
  (`performGlobalAction` for HOME/BACK/RECENTS/NOTIFICATIONS/QUICK_SETTINGS), `set_text` (modes `set`
  via `ACTION_SET_TEXT` on the resolved node, `type` on the focused editable node), `semantic`
  (string→`AccessibilityAction` map; `{ok:false,code:"unsupported_action"}` when the node lacks it),
  `launch` (explicit `ACTION_MAIN`/`CATEGORY_LAUNCHER` intent), `screencap` (`takeScreenshot` API 30+,
  PNG to `path`), `events` (200-entry ring buffer of `AccessibilityEvent`s; return `seq > since` up
  to `max`, echo `cursor`; `since==0` returns most-recent `max`).

**Task 6 — trust UI + guarded behavior.** `SettingsActivity.kt`: a `SwitchPreference` per capability
key (writes `TrustState`) and a **Trust & Safety** text section (what's read / controlled / local-only /
audit path / password+payment warnings) per foreground-service SPEC §7. Enforce **guarded-app refusal**:
gesture/text methods return `{ok:false,error:{code:"guarded_action"}}` for packages on the guarded
list, and set password/payment flags in `observe_native` for the Python `policy` layer to gate on.

**Task 7 — JVM contract tests + lifecycle seam.** JVM unit tests (run in CI) over `json/` + the
handlers: assert the serialized node/window/envelope/handshake JSON equals the literal shapes in the
Python fixtures (`test_native_tree.py` `NATIVE`, `test_transport.py`, `test_trust.py`). Add the
`start`/`stop` broadcast intents (foreground-service SPEC §8) the Phase-5 daemon will send via
`adb shell am broadcast` — declare the receiver, no daemon wiring.

## Verification (end-to-end)

1. **CI / unit**: `./gradlew assembleDebug test` (GitHub Actions) — builds the APK and runs the JVM
   contract tests proving the JSON shapes match the Python fixtures. APK uploaded as a CI artifact.
2. **Python suite unchanged**: `pytest -v` stays green — the MVP adds no Python changes; deferred
   notification/OCR keys are simply absent from the handshake and those providers degrade.
3. **On-device smoke** (the real topology — flagged ROM-specific per CLAUDE.md discipline): install
   the APK on the S25 Ultra, enable the service in Settings → Accessibility, start the foreground
   service, set `companion_port` in `~/.config/phonectl/config.json`, then from inside Termux+PRoot:
   - `phonectl setup` / `phonectl doctor` shows the companion reachable and the AccessibilityProvider
     winning `observe_ui_tree`/`act_*` ahead of ADB.
   - Run an `observe → tap → observe` loop and confirm the post-action `screen_hash` changes (the
     companion path, not ADB).
   - Tap the **"Stop phonectl"** notification → next `phonectl` action is blocked by
     `audit.kill_switch_active`; Resume clears it.
   - Confirm a password field comes back with `password:true` and **no** `text`.

## Notes / non-goals

- **Deferred to a follow-up cut** (Python side already present and inert without the keys):
  NotificationListenerService (`notifications_list/reply/dismiss`, 4.2) and ML-Kit OCR (`ocr_image`,
  4.4 — Python prefers local `tesseract` anyway).
- No TLS/auth token this stage; loopback-only + Android per-app namespace isolation is the boundary
  (revisit when multi-client/daemon access lands in Phase 5).
- The service **must not** log request payloads — method names + outcomes only — to avoid capturing
  typed text (foreground-service SPEC §9).
