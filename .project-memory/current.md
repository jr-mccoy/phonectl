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

**Perf pass 2026-07-04** (branch `claude/system-performance-optimization-2x4rnm`):
an action now costs ~6 adb round trips instead of ~10 — observe() fetches
`dumpsys window` once (shared by lock check + focused-app parse, taken after the
UI dump so app freshness is unchanged; lock check now also fail-fasts on error
dumps), `AdbBackend.wm_size` is cached (300s TTL, invalidated on serial change,
`wm_size_ttl=0` disables), and `DaemonClient.submit_and_wait` ramps polls
50ms→poll_interval instead of a flat 0.5s. Unit-tested (719 green); an on-device
smoke of the observe→act loop is still advisable before merging.

**Perf/automaticity pass 2 (2026-07-04, branch
`claude/system-performance-automaticity-vu7um4`):** observe now costs ONE adb
round trip instead of two — `AdbBackend.observe_dump()` runs `uiautomator dump`
plus a device-side-grepped `dumpsys window` in a single exec-out (falls back to
split calls when the shell/grep can't; a junk window section is never parsed as
"unlocked"). The registry delegates `observe_dump` per-provider so the companion
tree still wins by priority. `Connection.ensure()` trusts a successful check for
`ensure_ttl` (default 5 s; 0 disables) instead of spawning `adb get-state` every
call. New opt-in `action_observe_ttl` (default 0 = off) lets daemon loops reuse
the previous post-act snapshot for the next action's policy gate. A warm-daemon
tap is now ~4 round trips (~2 with action_observe_ttl). All unit-tested; an
on-device smoke (esp. the combined dump on the S25 Ultra's shell) is advisable
before merging.

**Companion-path pass (same branch, same day):** the companion's speed advantage
was being squandered — connect-per-RPC (the Kotlin Server supports long-lived
conns), a ping per provider per registry delegation, TWO observe_native
tree serializations per observe, a full adb `dumpsys window` lock fallback, and
(bug) snapshots with an EMPTY app.package that blinded the guarded_packages risk
signal on the companion path. Fixed: SocketTransport persists one conn (20s
reuse window < server's 30s idle-close; read-only methods may retry on a fresh
conn, gesture/set_text never replayed), ping() has a 5s cache refreshed by any
successful RPC, AccessibilityProvider.observe_dump() = one native RPC with
wm_size served from the payload, the registry augments an empty window from
AdbBackend.window_brief() (filtered dumpsys; offline adb no longer kills a
companion observe), and runtime memoizes the STOP-check transport per
(host,port,token). Handshake-per-action fail-closed STOP semantics unchanged.
On-device smoke advisable: idle-reconnect behavior against the real companion.

**Companion-first routing pass (2026-07-05, branch
`claude/accessibility-companion-automation-98hqxi`):** when the APK is up, the
system now does as much as possible through it. (1) long_press / named_swipe /
fling / screen-level scroll no longer slip through `ProviderRegistry.__getattr__`
to ADB — the registry delegates them on `act_tap` and `AccessibilityProvider`
serves them natively (long press = same-point stroke; named swipe/fling computed
from the cached screen size with AdbBackend's timing curve). (2) `input_key`
pre-flights against the companion's global-action set (HOME/BACK/RECENTS/
APP_SWITCH/NOTIFICATIONS/QUICK_SETTINGS) and raises CapabilityUnavailableError
locally for anything else, so ENTER/TAB/etc. fall to ADB without a doomed RPC.
(3) The compat XML now carries `resource-id` (Kotlin: NodeData.resourceId from
viewIdResourceName — id-selectors were blind on the companion path), `node-id`,
and `actions`; parse_elements surfaces `node_id`/`actions` only on companion
trees. (4) Semantic-first acting: tap/long_press by i/selector drive
ACTION_CLICK/ACTION_LONG_CLICK via `registry.semantic_action` when the element
advertises the action — generation-bound (Finding 9), so a mid-flight tree
change surfaces as StaleSnapshotError instead of a mis-tap; explicit x/y,
non-default long-press durations, unadvertised actions, and ADB trees stay on
the coordinate gesture path. ⚠️ Validation debt: Kotlin JVM tests need an
Android SDK (not in the session env) and the on-device smoke matrix was NOT
run; Python suite is green (785 passed). Known remaining ADB dependencies when
the companion is up: screenshots (Finding 16, by design), keyguard/focused-
window augmentation per observe (`window_brief`), arbitrary keycode injection,
and `Connection.ensure()` itself. Identified follow-up for full ADB-free
operation: teach the companion to report keyguard + focused window natively in
observe_native and let ensure() accept a live companion as sufficient.

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
