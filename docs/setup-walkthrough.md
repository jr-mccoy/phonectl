# phonectl setup walkthrough

`phonectl setup` is an interactive, testable onboarding flow for Android 11+ Wireless Debugging.

## Prompt-by-prompt flow

1. Ensure `adb` exists on `PATH`.
2. Show Wireless Debugging guidance:
   - Open **Settings > Developer options > Wireless debugging**.
   - Tap **Pair device with pairing code**.
3. Prompt for the pairing host and port, for example `127.0.0.1:37000`.
4. Prompt for the 6-digit pairing code shown by Android.
5. Run the connection seam's `pair(host_port, code)`.
6. Prompt for the main Wireless Debugging connect host and port, for example `127.0.0.1:41000`.
7. Run the connection seam's `connect(host_port)`.
8. Verify `backend.get_state() == "device"`.
9. Persist `mode=auto`, `serial`, and `last_port` in phonectl config.
10. Report whether the adb identity key (`~/.android/adbkey`) exists yet.

## `adb` absent branch

If `adb` is not installed, setup prints Termux guidance and exits before prompting, pairing, or connecting:

```bash
pkg install android-tools
phonectl setup
```

The wizard never silently installs packages.

## Already-connected fast path

Re-running setup is safe. If `backend.get_state()` is already `device` at startup, setup persists default config values if needed, prints an "already connected" message, and does not consume any prompts.

If the device is offline but a previous serial is configured and the connection object exposes `rediscover()`, setup can try that reconnect path before falling back to the full pairing flow.

## Module reports

`phonectl setup all` runs the ADB setup path and then reports the other provider modules. A sample report looks like:

```text
[accessibility] not available — AccessibilityService enabled for the phonectl companion app
    enable: Settings > Accessibility > phonectl > On (companion APK, Phase 4).
    unlocks: native UI tree + UI event stream + reliable set-text/gestures.
    safety: Reads on-screen content and dispatches gestures; per-capability toggles in the app.
[notifications] not available — Notification access for the phonectl companion app
    enable: Settings > Notifications > Notification access > phonectl (companion APK, Phase 4).
    unlocks: read/wait/reply/dismiss notifications.
    safety: Exposes notification contents; redaction policies apply to logs.
[termux-api] not available — Termux:API app + termux-api package
    enable: Install Termux:API app + `pkg install termux-api` (optional, Phase 3.5).
    unlocks: battery/clipboard/sensors/notifications/TTS bridges (optional).
    safety: Optional, never a hard dependency; discovered at runtime.
```

## `termux-api` setup module

`phonectl setup termux-api` checks whether Termux:API is installed and reports what it enables.

### Detection

The provider calls `shutil.which("termux-battery-status")`. If the binary is found, the module reports the full list of capabilities unlocked. If not, it prints installation instructions.

### Install steps (shown by the module when not available)

```text
[termux-api] not available — Termux:API app + termux-api package
    enable: Install the Termux:API companion app (F-Droid / Termux add-ons page), then:
            pkg install termux-api
            Grant Termux:API app permissions on Android (battery, clipboard, WiFi).
    unlocks: clipboard read, phonectl device battery, phonectl device wifi, phonectl tts speak.
    safety: Optional, never a hard dependency; discovered at runtime.
```

### When available

```text
[termux-api] available — termux-battery-status found on PATH
    capabilities: read_clipboard, write_clipboard, device_battery, device_wifi_info, tts_speak
    note: TermuxApiProvider is prepended to the registry; it takes priority over ADB for clipboard.
```

No configuration is written — discovery is fully automatic on every startup.

## Recovery note

Wireless Debugging connect ports are volatile across sleep, reboot, or toggling Wireless Debugging. The wizard stores `last_port` so the resilience reconnect path can retry it first. Manual recovery is simply re-running:

```bash
phonectl setup
```

## Diagnostics bundle

After setup, collect a redacted support bundle:

```bash
phonectl doctor --bundle /tmp/phonectl-diag.zip
```

Inspect `manifest.json` in the zip to confirm sensitive config keys are masked and `audit_tail` contains only metadata (`ts`, `verb`, `app`, and `hash`).

## Manual real-device verification

```bash
pkg install android-tools          # if adb is missing
phonectl setup                     # answer prompts from Wireless Debugging
phonectl doctor                    # expect connected state
phonectl setup                     # expect already-connected fast path
phonectl setup all                 # expect module reports
phonectl doctor --bundle /tmp/phonectl-diag.zip
```

Active Android-version gating is deferred; Android 11+ is surfaced as setup guidance because pairing-code Wireless Debugging is itself an Android 11+ feature.
