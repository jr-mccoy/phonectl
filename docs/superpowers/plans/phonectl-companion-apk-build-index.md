# Companion APK — Complete Build Index

**Phase 4 native effort.** This index ties together the full Kotlin companion-app build that
satisfies the two Android design specs and the already-shipped Python provider seam. Read this
first, then execute the phase plans in order.

## Why this exists

phonectl drives the phone over no-root ADB today. Phase 4 of the platform roadmap added a second,
richer observe/act surface: a Kotlin **companion APK** that the existing Python providers talk to
over a loopback socket. The **entire Python half is shipped and green** —
`providers/accessibility.py`, `providers/notifications.py`, `providers/ocr.py`,
`providers/transport.py`, `trust.py`, `native_tree.py`. What's missing is the Kotlin app: `android/`
holds only two design specs and zero code.

The two specs are an **executable specification** — the APK must match the Python consumers and
their pytest fixtures field-for-field:

- `android/accessibility-companion/SPEC.md` — message contract + AccessibilityService,
  NotificationListener, ML-Kit OCR methods.
- `android/foreground-service/SPEC.md` — loopback TCP transport, handshake, emergency-stop,
  Quick-Settings tile, per-capability trust UI.

This effort finishes the app **completely**: Accessibility + Notifications + ML-Kit OCR + full trust
UX. Distribution is a **CI debug-APK artifact only** — no signed-release/Play pipeline this round.

## Phase map

| Phase | Plan | Scope | Status |
|---|---|---|---|
| 1 | `phonectl-plan-4.5-accessibility-companion-apk-mvp.md` | Gradle/CI scaffold, loopback transport, emergency-stop/trust, AccessibilityService | ✅ done |
| 2 | `phonectl-plan-4.6-notification-listener-companion.md` | NotificationListenerService methods + gating + toggles | ✅ done |
| 3 | `phonectl-plan-4.7-ocr-companion.md` | ML-Kit OCR `ocr_image` + `observe_ocr` gate | ✅ done |
| 4 | `phonectl-plan-4.8-companion-integration-and-validation.md` | Guarded-app/payment hardening, lifecycle hardening, on-device smoke, docs | ✅ done (code + docs; on-device smoke ⏳ NOT RUN — needs hardware) |

Phase 1 (Plan 4.5) already existed and is **kept unchanged** — it is the MVP loop. Phases 2–4 are the
follow-on cuts that complete the app. Executed 1 → 2 → 3 → 4; each phase kept CI green and the Python
suite green before the next began. The companion APK is **shipped** (CI debug-APK artifact); the only
outstanding item is the ROM-specific on-device smoke matrix in Plan 4.8 Task 3, which requires the
physical device and has not been run.

## Shared toolchain & layout decisions (apply to every phase)

Established by Plan 4.5; later phases extend, not re-decide:

- **Kotlin + Gradle (Kotlin DSL)**, Android Gradle Plugin. Plain Android **Views** + `SwitchPreference`
  for the settings UI — no Compose, keep the APK lean.
- **`minSdk 31` (Android 12), `targetSdk` latest.** Security-relevant: the trust boundary
  (`foreground-service/SPEC.md §9`) relies on Android 12+ per-app loopback network-namespace isolation,
  and `takeScreenshot` (API 30+) is used directly. Target device is a Galaxy S25 Ultra (Android 15).
- **`org.json` for serialization** (bundled in Android, available on the JVM test classpath) — node /
  envelope / region shapes are hand-built and provably match the Python fixtures. **No Moshi/Gson** —
  no reflection-based mapper that could silently rename a field. Exception: Phase 3 adds the **only**
  third-party runtime dep, bundled `com.google.mlkit:text-recognition` (≈ a few MB APK growth — the
  one accepted size cost, isolated to the OCR path).
- **CI**: GitHub Actions `.github/workflows/android.yml` — JDK 17 + `setup-android`,
  `./gradlew assembleDebug test`, debug APK uploaded as a build artifact. Wrapper files committed so CI
  and a local desktop build are identical.
- **Package root**: `com.phonectl.companion`. Source layout under
  `android/accessibility-companion/app/src/main/kotlin/com/phonectl/companion/`:
  `transport/`, `service/`, `state/`, `ui/`, `json/`; JVM tests under `app/src/test/kotlin/`.

## Cross-phase invariants (must hold every commit)

1. **The Python fixtures are the authority.** If a shape mismatches, the **APK** changes to match the
   fixture — never the reverse. `pytest -v` (579 tests) stays green **unchanged** across all phases.
2. **Capability-gated rollout.** Every new surface lands behind a handshake capability key that is
   simply absent until the APK advertises it, so the Python providers degrade cleanly mid-build.
   Keys: Phase 1 → `observe_ui_native, observe_ui_events, act_gesture_native, act_set_text_native,
   act_semantic_action`; Phase 2 adds `observe_notifications, notifications_wait, notifications_reply,
   notifications_dismiss`; Phase 3 adds `observe_ocr`. `trust.gate_capabilities` iterates keys
   generically, so adding keys is safe.
3. **Stale-response protection.** Every response echoes `request_id` exactly; the service never emits
   unsolicited responses (`providers/transport.py` drops non-matching IDs).
4. **Loopback only.** `ServerSocket` binds `127.0.0.1`; non-loopback hosts are refused.
5. **Never log request payloads** — method names + outcomes only — to avoid capturing typed text
   (`foreground-service/SPEC.md §9`).
6. **TDD + one commit per task.** Write the failing JVM test asserting the JSON against the Python
   fixture, verify it fails, write the minimal handler, verify it passes, commit. JVM contract tests
   reference the literal fixtures in `tests/test_providers_notifications.py`,
   `tests/test_providers_ocr.py`, `tests/test_native_tree.py`, `tests/test_transport.py`,
   `tests/test_trust.py`.

## Wire contract (one-screen reference)

Authority = the Python consumers in `src/phonectl/`.

- **Framing** (`providers/transport.py`): NDJSON, loopback. Request `{version:1, request_id:<hex>,
  method, params:{}, timeout:<s>}`. Response success `{version:1, request_id:<echoed>, ok:true,
  data:{...}}`; error `{...ok:false, error:{code,message}}`.
- **observe_native** (`native_tree.to_compat_xml`): `{windows:[{id,type,package,nodes:[{node_id,text,
  class,content_desc,bounds:[l,t,r,b],checkable,checked,clickable,enabled,focused,scrollable,
  password}]}], screen:{width,height}}`.
- **handshake** (`trust.negotiate`/`gate_capabilities`): `{version:1, capabilities:{<key>:bool},
  stopped:bool}`. `stopped:true` ⇒ `audit.kill_switch_active` blocks all actions (mirrors
  `$PHONECTL_HOME/STOP`).
- **Method returns**: `ping`→`{pong:true}`, `gesture/set_text/key`→`{applied:true}`,
  `semantic`→`{performed:<action>}`, `launch`→`{launched:true}`, `screencap`→`{path}`,
  `events`→`{events:[{seq,type,package,ts}], cursor}`.
- **Notifications** (`providers/notifications.py`): `notifications_list`→`{notifications:[{key,package,
  title,text,category,post_time,actions:[{title,remote_input}]}]}`; `notifications_reply`(`{key,text}`)
  →`{sent:true}` / err `no_remote_input`,`not_found`; `notifications_dismiss`(`{key}`)→`{dismissed:true}`
  / err `not_found`.
- **OCR** (`providers/ocr.py`): `ocr_image`(`{path}`)→`{regions:[{text,bounds:[l,t,r,b],confidence}]}`;
  gated by `observe_ocr` (err `capability_disabled`). Python tries `tesseract` first; companion is the
  fallback.

## Verification (whole effort)

1. **CI / unit** — `./gradlew assembleDebug test` builds the debug APK and runs the JVM contract tests;
   each phase adds tests asserting its JSON equals the Python fixtures. APK uploaded as a CI artifact.
2. **Python suite unchanged** — `pytest -v` green at 579 passing after every phase.
3. **On-device smoke** — the Phase 4 matrix on the real S25 Ultra (ROM-specific; flagged per CLAUDE.md
   — do not claim device behavior unless run).

## Non-goals

- **Signed release / Play distribution** — debug APK artifact only this effort.
- **Compose UI** — plain Views + `SwitchPreference`.
- **TLS / auth token** — loopback + Android 12+ per-app namespace isolation is the boundary.
- **No Python-side changes** — the providers, fixtures, and 579 tests are the fixed contract.
