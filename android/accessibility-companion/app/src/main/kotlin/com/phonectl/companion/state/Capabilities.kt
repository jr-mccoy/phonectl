package com.phonectl.companion.state

import org.json.JSONObject

/**
 * The capability keys this companion advertises in `handshake.capabilities`.
 *
 * These are exactly the keys the Python side recognizes (src/phonectl/capabilities.py) for the
 * AccessibilityService companion: the MVP accessibility keys plus the notification (Plan 4.6)
 * and OCR (Plan 4.7) keys, all shipped with handlers. The Python providers degrade cleanly when
 * a key is absent or toggled off.
 *
 * Pure (no Android dependency) so the handshake shape is exercised by JVM contract tests against
 * the foreground-service SPEC §3 example and tests/test_trust.py.
 */
object Capabilities {

    val MVP_KEYS: List<String> = listOf(
        "observe_ui_native",
        "observe_ui_events",
        "act_gesture_native",
        "act_set_text_native",
        "act_semantic_action",
        "launch_app",
    )

    /**
     * NotificationListenerService capability keys (Plan 4.6 / foreground-service SPEC §6). Default
     * on like the MVP keys; the Python side intersects them with the technically-supported set.
     */
    val NOTIFICATION_KEYS: List<String> = listOf(
        "observe_notifications",
        "notifications_wait",
        "notifications_reply",
        "notifications_dismiss",
    )

    /**
     * OCR capability key (Plan 4.7 / accessibility-companion SPEC §7). Default on like the other
     * keys; gates the `ocr_image` method. The Python OcrProvider is tesseract-first, so this is the
     * fallback path only — the companion returns *all* regions and Python applies `min_confidence`.
     */
    val OCR_KEYS: List<String> = listOf(
        "observe_ocr",
        "observe_ocr_screen",
    )

    /**
     * Screenshot capability key: gates the `screenshot` method, which returns the PNG as
     * base64 over the token-authenticated socket. The companion persists nothing (Finding 16
     * still holds — it never writes outside its own storage; here it writes nothing at all);
     * the Python caller stores the bytes under its own storage.
     */
    val SCREENSHOT_KEYS: List<String> = listOf(
        "observe_screenshot",
    )

    /** Every capability key the companion advertises in `handshake.capabilities`. */
    val ALL_KEYS: List<String> = MVP_KEYS + NOTIFICATION_KEYS + OCR_KEYS + SCREENSHOT_KEYS

    /** Defaults: all capabilities enabled on first install (foreground-service SPEC §6). */
    const val DEFAULT_ENABLED = true

    /**
     * Build the `handshake` response data:
     *   {version:1, capabilities:{<key>:bool, ...}, stopped:<bool>}
     *
     * @param enabled the user-enabled toggle set; keys absent here default to DEFAULT_ENABLED.
     */
    fun handshakeData(enabled: Map<String, Boolean>, stopped: Boolean): JSONObject {
        val caps = JSONObject()
        for (key in ALL_KEYS) {
            caps.put(key, enabled[key] ?: DEFAULT_ENABLED)
        }
        return JSONObject()
            .put("version", 1)
            .put("capabilities", caps)
            .put("stopped", stopped)
    }

    /**
     * Transport methods whose per-capability toggle gates them on the device side (Plan 4.6 Task 5).
     * A method maps to the capability key the user can switch off; when off, the dispatcher refuses
     * the call with `capability_disabled` (defence in depth — the Python side also drops the grant).
     */
    val METHOD_CAPABILITY: Map<String, String> = mapOf(
        "observe_native" to "observe_ui_native",
        "events" to "observe_ui_events",
        "gesture" to "act_gesture_native",
        "key" to "act_gesture_native",
        "set_text" to "act_set_text_native",
        "semantic" to "act_semantic_action",
        "launch" to "launch_app",
        "notifications_list" to "observe_notifications",
        "notifications_reply" to "notifications_reply",
        "notifications_dismiss" to "notifications_dismiss",
        "ocr_image" to "observe_ocr",
        "ocr_screen" to "observe_ocr_screen",
        "screenshot" to "observe_screenshot",
        // "screencap" is the pre-screenshot-RPC path-based capture, superseded but still wired
        // in the dispatcher; gate it on the same toggle so a disabled observe_screenshot can't
        // be bypassed through the older method name.
        "screencap" to "observe_screenshot",
    )

    /**
     * A per-method gate for [com.phonectl.companion.transport.Dispatcher]: returns
     * `"capability_disabled"` when the method's controlling toggle is off, else null (allowed).
     * Methods absent from [METHOD_CAPABILITY] are always allowed.
     */
    fun methodGate(state: TrustState): (String) -> String? = gate@{ method ->
        val key = METHOD_CAPABILITY[method] ?: return@gate null
        if (state.isCapabilityEnabled(key)) null else "capability_disabled"
    }
}

/**
 * STOP combination (foreground-service SPEC §4): the emergency stop is engaged when EITHER the
 * in-app flag is set OR an explicitly configured sentinel file exists. The in-app SharedPrefs
 * flag is the authoritative on-device state (Finding 3 — the APK's UID can never read Termux's
 * `$PHONECTL_HOME/STOP`, so the Python-side file is a separate, independent stop); the optional
 * file only ever widens the stop.
 */
object StopSentinel {
    fun stopped(inAppFlag: Boolean, stopFileExists: Boolean): Boolean = inAppFlag || stopFileExists
}
