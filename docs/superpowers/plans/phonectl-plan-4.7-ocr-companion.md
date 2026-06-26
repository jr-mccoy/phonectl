# Plan 4.7 — ML-Kit OCR companion (native Kotlin)

**Phase 3 of the complete companion-APK build** (index:
`phonectl-companion-apk-build-index.md`). Executes `android/accessibility-companion/SPEC.md §7` /
the companion side of Plan 4.4. Depends on Plan 4.5 (transport, dispatcher, `TrustState`,
handshake, `screencap`) being in place. Adds the **only third-party runtime dependency** in the
APK: bundled `com.google.mlkit:text-recognition`.

## Context

The Python `providers/ocr.py` already does OCR two ways and is green: it tries the local
`tesseract` binary **first** and only falls back to the companion `ocr_image` method when
`tesseract` is absent. This plan implements that companion fallback in Kotlin using ML-Kit's
bundled on-device text recognition — no network, no Play Services dependency at call time.

Authority facts from `src/phonectl/providers/ocr.py`:
- `ocr_image(path)` calls the transport method `ocr_image {path}` with a **10 s** timeout, checks
  `request_id` + `ok`, then reads `data["regions"]` and filters `confidence >= min_confidence`
  **on the Python side**. So the companion returns *all* regions; the threshold is Python's job.
- `capabilities()` advertises `observe_ocr=True` when OCR is available. The SPEC requires
  `observe_ocr:true` in the handshake for `ocr_image` to dispatch.
- Fixture shape (`tests/test_providers_ocr.py`): a region is
  `{"text": <str>, "bounds": [l,t,r,b], "confidence": <0.0–1.0 float>}`.

## Contract (the non-negotiable surface)

- **`ocr_image`** params `{path}` (absolute path to an existing PNG) →
  `{regions:[{text, bounds:[left,top,right,bottom], confidence}]}`. `bounds` are screen pixels in
  the same `[l,t,r,b]` order as `observe_native`; `confidence` is `0.0–1.0`. Return **all** regions
  unfiltered (Python applies `min_confidence`).
- **Gating**: the method dispatches only when the `observe_ocr` toggle is on; when off, return
  `{ok:false, error:{code:"capability_disabled"}}` (SPEC §7). On a missing/undecodable file return
  `{ok:false, error:{code:"screencap_unavailable"}}` — reuse the existing read/decode error rather
  than inventing a new code — with a clear message.
- **Handshake** gains one key, `observe_ocr`. Default per SPEC §7: the toggle gates dispatch;
  ship it **on** by default to match the other capabilities, documented in the Trust & Safety text.
- The Python suite stays green **unchanged** — `observe_ocr` is additive and the Python OCR provider
  prefers `tesseract` regardless.

## New / changed source

```
app/build.gradle.kts                              # + com.google.mlkit:text-recognition (bundled)
app/src/main/kotlin/com/phonectl/companion/
  service/OcrHandler.kt                            # new — decode PNG + ML-Kit TextRecognition
  json/Ocr.kt                                      # new — org.json region-list builder
  state/Capabilities.kt                            # + observe_ocr
  state/TrustState.kt                              # + observe_ocr toggle (default on)
  transport/Dispatcher.kt                          # route ocr_image + capability_disabled gate
  ui/SettingsActivity.kt                           # + observe_ocr SwitchPreference + Trust text
app/src/test/kotlin/.../OcrContractTest.kt         # JVM test vs Python region fixture
```

## Tasks (one commit each; TDD: failing test → verify fail → minimal impl → verify pass → commit)

**Task 1 — dependency + capability key.** Add the bundled ML-Kit text-recognition dependency to
`app/build.gradle.kts` (the bundled variant so the model ships in the APK — note the ≈ few-MB size
cost in the index's toolchain table). Add `observe_ocr` to `Capabilities.kt` and `TrustState.kt`
(default on) and into the `handshake` response. JVM test: handshake JSON contains `observe_ocr:true`.
Commit: `feat(companion): add bundled ML-Kit dependency + observe_ocr capability key`.

**Task 2 — `ocr_image` handler.** `OcrHandler.kt`: `BitmapFactory.decodeFile(path)` (→
`screencap_unavailable` on null), run `TextRecognition.getClient(...)` on an
`InputImage.fromBitmap`, await the result, and flatten `Text.TextBlock` → `TextLine` →
`TextElement` into a flat region list. Each region: `text` from the line/element, `bounds` from
`boundingBox` (`[rect.left, rect.top, rect.right, rect.bottom]`), `confidence` from the recognizer
(ML-Kit `Text.Element` confidence where available, else the line/block confidence; default `1.0`
only when the API exposes none). `Ocr.kt` builds `{regions:[...]}`. The handler runs synchronously
within the request's 10 s budget (the dispatcher thread blocks on the ML-Kit `Task`). JVM contract
test: feed a stub recognizer result and assert the serialized region equals
`{"text":"World","bounds":[1,2,3,4],"confidence":0.9}` (the `test_providers_ocr.py` shape), and
assert **no** Python-style filtering happens companion-side (all regions returned).
Commit: `feat(companion): ocr_image flattens ML-Kit text blocks into regions`.

**Task 3 — gate + toggle + precedence note.** In `Dispatcher.kt`, dispatch `ocr_image` only when
`observe_ocr` is on, else `{ok:false,code:"capability_disabled"}`. Add the `observe_ocr`
`SwitchPreference` and a Trust & Safety note: "OCR reads text from a screenshot you captured;
images never leave the device (on-device ML-Kit)." Document — no code — that the Python
`OcrProvider` is **tesseract-first** (`providers/ocr.py` `_local_ok()` short-circuits before the
companion), so this method is the fallback path only. JVM test: disabled toggle →
`capability_disabled`.
Commit: `feat(companion): gate ocr_image on observe_ocr + trust toggle`.

## Verification

1. **CI / unit** — `./gradlew assembleDebug test` green; `OcrContractTest` asserts the region JSON
   matches the Python fixture and the `capability_disabled` gate works. Confirm the bundled-model
   dependency resolves in CI (the APK artifact size step reflects the growth).
2. **Python suite unchanged** — `pytest -v` stays at 579 passing; `observe_ocr` is additive and the
   `min_confidence` filter still runs Python-side over the returned regions.
3. **On-device** (deferred to Plan 4.8's smoke matrix; ROM-specific): with `tesseract` **absent**
   from the Termux PATH, `phonectl` OCR of a captured screen returns ML-Kit regions; with
   `tesseract` present, the companion path is not taken (precedence holds).

## Deferred / non-goals

- **Unbundled / Play-Services ML-Kit variant** — use the bundled model so OCR works offline with no
  Play dependency; revisit only if APK size becomes a problem.
- **Region merging / layout reconstruction** — return a flat region list; any grouping is a Python
  concern.
- **OCR confidence thresholding companion-side** — Python owns `min_confidence`; the companion
  returns everything.
