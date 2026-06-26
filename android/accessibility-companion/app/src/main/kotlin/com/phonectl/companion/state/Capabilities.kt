package com.phonectl.companion.state

import org.json.JSONObject

/**
 * The capability keys this MVP companion advertises in `handshake.capabilities`.
 *
 * These are exactly the keys the Python side recognizes (src/phonectl/capabilities.py) for the
 * AccessibilityService companion. Notification (4.2) and OCR (4.4) keys are intentionally omitted
 * this cut — their Python providers degrade cleanly when the keys are absent.
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
    )

    /** Every capability key the companion advertises in `handshake.capabilities`. */
    val ALL_KEYS: List<String> = MVP_KEYS + NOTIFICATION_KEYS + OCR_KEYS

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
        "notifications_list" to "observe_notifications",
        "notifications_reply" to "notifications_reply",
        "notifications_dismiss" to "notifications_dismiss",
        "ocr_image" to "observe_ocr",
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
 * STOP-sentinel parity (foreground-service SPEC §4): the emergency stop is engaged when EITHER the
 * in-app flag is set OR the `$PHONECTL_HOME/STOP` sentinel file exists. The file is the hard
 * guarantee (survives APK crashes); the in-app flag is the low-latency path for the running session.
 */
object StopSentinel {
    fun stopped(inAppFlag: Boolean, stopFileExists: Boolean): Boolean = inAppFlag || stopFileExists
}
