# Plan 4.8 — Companion integration, hardening & on-device validation

**Phase 4 (final) of the complete companion-APK build** (index:
`phonectl-companion-apk-build-index.md`). Runs after Plans 4.5–4.7. This is the cross-surface
polish and the **real-topology validation** that unit tests cannot prove — flagged ROM-specific
per CLAUDE.md discipline: **do not claim device behavior unless it was actually run on the device.**

## Context

By this phase the companion exposes the full method surface (AccessibilityService, Notifications,
ML-Kit OCR) and all capability keys. What remains is to make the **trust and safety guarantees in
`foreground-service/SPEC.md §7` hold uniformly** across every action path, harden the transport
lifecycle, and validate the whole thing end-to-end on the real device against the actual Python
`SocketTransport`/providers — the one thing the JVM contract tests and `pytest` can't cover.

## Tasks

**Task 1 — guarded-app + payment-screen hardening (one commit).** Today each handler was built in
isolation across 4.5–4.7. Make the safety flags consistent:
- **Guarded-app refusal**: `gesture`, `set_text`, `semantic`, `launch`, and `notifications_reply`
  return `{ok:false, error:{code:"guarded_action"}}` when the foreground/target package is on the
  guarded list (SPEC §7.6). Source the list from a companion-local pref that mirrors
  `~/.config/phonectl/config.json`'s guarded-app key.
- **Password/payment flags in `observe_native`**: every node with
  `inputType & TYPE_TEXT_VARIATION_PASSWORD` already sets `password:true` with **no** `text`
  (Plan 4.5); extend the same pass to set a window/payment flag from a package allowlist +
  window-title heuristic (SPEC §7.5) so the Python `policy` layer can gate.
JVM tests: refusal for a guarded package across each method; `password:true`/no-text; payment flag set.
Commit: `feat(companion): uniform guarded-app refusal + password/payment flags across all methods`.

**Task 2 — transport & lifecycle hardening (one commit).** Confirm and test, across **all**
handlers including the new notification/OCR ones:
- `version_mismatch` rejection for unknown major `version`;
- 30 s idle-connection close (SPEC §9);
- the **no-request-payload-logging** rule — assert handlers log method name + outcome only, never
  params (so reply text / typed text never hits logs);
- the `start`/`stop` broadcast-intent seam (SPEC §8) the Phase-5 daemon will drive — declare the
  receiver, no daemon wiring.
JVM tests where deterministic (envelope rejection, a log-capture assertion that payloads are absent).
Commit: `feat(companion): harden version/idle/lifecycle + assert no payload logging`.

**Task 3 — full on-device smoke matrix (one commit: results recorded in this doc).** On the
Galaxy S25 Ultra (Android 15): build + install the CI debug APK, enable the AccessibilityService
(Settings → Accessibility) and grant notification access (Settings → Notifications), start the
foreground service, set `companion_port` in `~/.config/phonectl/config.json`. From Termux + PRoot,
verify each surface and record PASS/FAIL with notes:
1. `phonectl doctor` shows the companion reachable and the `AccessibilityProvider` winning
   `observe_ui_tree`/`act_*` ahead of ADB.
2. An `observe → tap → observe` loop changes the post-action `screen_hash` over the **companion**
   path (not ADB).
3. Notifications `list → reply → dismiss` round-trip against a real messaging notification;
   `can_reply` reflects `RemoteInput` presence.
4. With `tesseract` absent, `ocr_image` of a captured screen returns ML-Kit regions; with it present,
   the companion path is not taken.
5. Tapping the **"Stop phonectl"** notification (and the Quick-Settings tile) blocks the next
   `phonectl` action via `audit.kill_switch_active`; Resume clears it.
6. A password field returns `password:true` with **no** `text`; a guarded app returns `guarded_action`.
Commit: `docs(companion): record on-device smoke-matrix results (Plan 4.8 Task 3)`.

**Task 4 — docs + status (one commit).** Update:
- `CLAUDE.md` — mark the companion APK shipped, list the capability keys, point at this index.
- `docs/superpowers/phonectl-platform-roadmap.md` tracker — Phase 4 native effort complete.
- `android/accessibility-companion/SPEC.md` and `android/foreground-service/SPEC.md` — change the
  "built separately from this spec" notes to reference Plans 4.5–4.8.
- The index status table → all phases ✅.
Commit: `docs(companion): mark Phase 4 companion APK complete + cross-reference plans`.

## On-device smoke-matrix results (Task 3)

**Status: ⏳ NOT RUN — no device access.** Tasks 1, 2, and 4 (code + docs) were implemented and the
JVM/Python suites cover them, but the Task 3 matrix is **ROM-specific and requires the physical
Galaxy S25 Ultra** with the AccessibilityService enabled, notification access granted, the
foreground service running, and `companion_port` set in `~/.config/phonectl/config.json`. Per
CLAUDE.md discipline ("do not claim device behavior unless it was actually run on the device"), the
results below are left as a **pending checklist** to be filled in by whoever runs the smoke on
hardware. No PASS/FAIL is asserted here because none was observed in this environment.

Setup before the matrix: build + install the CI debug APK (`assembleDebug` artifact), enable the
AccessibilityService (Settings → Accessibility), grant notification access (Settings →
Notifications → Device & app notifications → phonectl companion → Allow), start the foreground
service, and set `companion_port` in `~/.config/phonectl/config.json`.

| # | Check | Expected | Result | Notes |
|---|---|---|---|---|
| 1 | `phonectl doctor` | companion reachable; `AccessibilityProvider` wins `observe_ui_tree`/`act_*` ahead of ADB | ⏳ NOT RUN | |
| 2 | `observe → tap → observe` loop | post-action `screen_hash` changes over the **companion** path (not ADB) | ⏳ NOT RUN | |
| 3 | Notifications `list → reply → dismiss` | round-trips against a real messaging notification; `can_reply` reflects `RemoteInput` presence | ⏳ NOT RUN | |
| 4 | `ocr_image` of a captured screen | with `tesseract` absent → ML-Kit regions; with it present → companion path not taken (precedence) | ⏳ NOT RUN | |
| 5 | "Stop phonectl" notification + Quick-Settings tile | blocks the next `phonectl` action via `audit.kill_switch_active`; Resume clears it | ⏳ NOT RUN | |
| 6 | Password field / guarded app | password field → `password:true` with **no** `text`; guarded app → `guarded_action` | ⏳ NOT RUN | |

## Verification

1. **CI / unit** — `./gradlew assembleDebug test` green with the Task 1–2 hardening tests added.
2. **Python suite unchanged** — `pytest -v` still 579 passing; no Python edits in this effort.
3. **On-device** — the Task 3 matrix above is itself the verification; results recorded in this doc.

## Deferred / non-goals

- **Phase-5 daemon wiring** of the `start`/`stop` broadcast seam — declared here, driven later.
- **Signed release / distribution** — out of scope for the whole effort (index non-goals).
- **Notification-event fan-in** into the `events` ring is optional polish; only do it if the
  on-device smoke shows a concrete need.
