# Current State

_What matters right now. Lifespan: days to ~2 weeks. Keep it short and true._

## Current Focus
Remediating the 2026-07 adversarial review. Python-side immediate fixes are done
(Findings 4, 5, 6, 7, 8, 12, 15 on top of #40's 1 & 2 — see the remediation-status
block in `docs/adversarial-review-2026-07.md`). Remaining: Kotlin/companion findings
(3, 9, 10, 14, 16 — need an Android build + on-device validation), cross-process
idempotency (11), provider fallback (13), then Phase 7.1 (Shizuku).

**Behavior changes to remember:** default mode is now `confirm` (auto is opt-in;
setup wizard seeds confirm); unknown companion capability keys default disabled;
a configured-but-unreachable companion fails closed as `stopped` — and run_action
now builds the companion transport from cfg itself, so the companion STOP flag
actually gates every action.

## Recently Changed
- 5557c6b Merge pull request #33 from jumbodaddystack/claude/phonectl-ocr-companion-1s1etp
- 031e41a docs(companion): mark Phase 4 companion APK complete + cross-reference plans
- c66833d docs(companion): record on-device smoke-matrix results (Plan 4.8 Task 3)
- 13b7fa0 feat(companion): harden version/idle/lifecycle + assert no payload logging
- 941e373 feat(companion): uniform guarded-app refusal + password/payment flags across all methods
- 15eb770 feat(companion): gate ocr_image on observe_ocr + trust toggle
- 8a257f3 feat(companion): ocr_image flattens ML-Kit text blocks into regions
- ca950dc feat(companion): add bundled ML-Kit dependency + observe_ocr capability key
- eb8942f Merge pull request #32 from jumbodaddystack/claude/phonectl-notification-listener-na2q8g
- e06cc99 test(companion): fix replyActionIndex test key collision
- d5073ac feat(companion): gate notification methods + trust toggles + grant guidance
- 2cc5d08 feat(companion): notifications_dismiss via cancelNotification
- 893d5db feat(companion): notifications_reply via RemoteInput.addResultsToIntent
- 162cd6f feat(companion): notifications_list serializes active StatusBarNotifications
- 220e868 feat(companion): declare NotificationListenerService + notification capability keys
- 44f6e24 Merge pull request #31 from jumbodaddystack/claude/phonectl-accessibility-apk-mvp-3lseso
- d57e71b 4.5: correct accessibilityFlags enum name (flagRetrieveInteractiveWindows)
- 4013516 4.5: close <vector> tags in launcher/tile drawables
- a8c31fe 4.5: app theme carries preferenceTheme for SettingsActivity
- 9159a8b 4.5 build fixes: AGP 8.6.1 for compileSdk 35 + qualify nested AccessibilityService types

## Watch Out For
