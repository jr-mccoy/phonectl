# Current State

_What matters right now. Lifespan: days to ~2 weeks. Keep it short and true._

## Current Focus
2026-07 adversarial review remediation is code-complete: all sixteen findings are
fixed (1–2 in PR #40; 4–8, 12, 15 on `claude/system-improvement-emxdl8`; 3, 9, 10,
11, 13, 14, 16 on `claude/system-companion-app-improve-ru7udk`). ⚠️ Validation debt
on the last branch: the Python suite was NOT run (session constraint) and the Kotlin
JVM tests need an Android build; re-run `pytest -v`, a Gradle test build, and the
on-device smoke matrix before merging. Then: the human-only sensitive-action policy
layer (review roadmap item 16) and Phase 7.1 (Shizuku).

**Behavior changes to remember:** default mode is now `confirm` (auto is opt-in;
setup wizard seeds confirm); unknown companion capability keys default disabled;
a configured-but-unreachable companion fails closed as `stopped` — and run_action
now builds the companion transport from cfg itself, so the companion STOP flag
actually gates every action.

**New behavior from the companion/system branch:** the companion dispatcher refuses
every method except ping/handshake while stopped (on-device fail-closed; SharedPrefs
is the authoritative stop — the env-based STOP-file fallback was inert cross-UID and
is gone); observe_native returns a tree `generation` that set_text/semantic must echo
(`stale_generation` → StaleSnapshotError) and ambiguous node ids are refused; guarded
apps are unreadable (observe/screencap/OCR/events/notifications), not just
untouchable; companion screencap writes only under its own storage so the Python
provider no longer advertises observe_screenshot; LifecycleReceiver broadcasts need
`--es token <paired-token>`; idempotency + the single-writer lock persist to
$PHONECTL_HOME (idempotency.json / action.lock flock); the registry falls back to the
next capable provider on runtime failure (never on policy refusals) and envelopes
carry truthful `provider` + `provider_fallback`. Companion error codes now map to
typed errors (fixed latent `errors.ActionError` AttributeError).

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
