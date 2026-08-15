package com.droidjig.companion.state

import com.droidjig.companion.transport.CoreHandlers
import com.droidjig.companion.transport.Dispatcher
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Handshake/ping contract — the handshake shape matches foreground-service SPEC §3 and the
 * keys the Python side recognizes (tests/test_trust.py negotiate fields: version, capabilities,
 * stopped). Fresh-install capability defaults are per-key (sensitive caps off, Finding 5); a
 * user-set toggle flips its bool. [FakeTrustState] models a chosen toggle state (everything the
 * user could enable is on unless listed in `disabled`), NOT the install default — the real
 * per-key default path is exercised directly via [Capabilities.handshakeData] with an empty map.
 */
class HandshakeTest {

    /** In-memory TrustState for JVM tests (no SharedPreferences). */
    private class FakeTrustState(
        val disabled: Set<String> = emptySet(),
        val stopped: Boolean = false,
        val guarded: Set<String> = emptySet(),
    ) : TrustState {
        override fun isCapabilityEnabled(key: String) = key !in disabled
        override fun isStopped() = stopped
        override fun guardedPackages() = guarded
    }

    private fun handshake(state: TrustState): JSONObject {
        val line = JSONObject().put("version", 1).put("request_id", "h1")
            .put("method", "handshake").put("params", JSONObject()).toString()
        val resp = JSONObject(Dispatcher(CoreHandlers.methods(state)).handleLine(line))
        assertTrue(resp.getBoolean("ok"))
        return resp.getJSONObject("data")
    }

    @Test
    fun handshakeAdvertisesExactlyTheKnownKeys() {
        val data = handshake(FakeTrustState())
        assertEquals(1, data.getInt("version"))
        assertFalse(data.getBoolean("stopped"))
        val caps = data.getJSONObject("capabilities")
        val expected = setOf(
            "observe_ui_native", "observe_ui_events", "act_gesture_native",
            "act_set_text_native", "act_semantic_action", "launch_app",
            "observe_notifications", "notifications_wait", "notifications_reply",
            "notifications_dismiss",
            "observe_ocr", "observe_ocr_screen",
            "observe_screenshot",
        )
        assertEquals(expected, caps.keys().asSequence().toSet())
    }

    @Test
    fun freshInstallDefaultsSensitiveCapsOffAndRestOn() {
        // The real per-key default path: handshakeData with no user toggles set falls back to
        // Capabilities.defaultFor(key). Sensitive caps ship OFF (Finding 5); everything else ON.
        val caps = Capabilities.handshakeData(emptyMap(), stopped = false)
            .getJSONObject("capabilities")
        val sensitiveOff = setOf(
            "act_set_text_native", "notifications_reply",
            "observe_ocr", "observe_ocr_screen", "observe_screenshot",
        )
        // Guard against silent drift in the sensitive set.
        assertEquals(sensitiveOff, Capabilities.SENSITIVE_KEYS)
        for (key in Capabilities.ALL_KEYS) {
            assertEquals("$key default", key !in sensitiveOff, caps.getBoolean(key))
        }
    }

    @Test
    fun disabledNotificationToggleFlipsHandshakeBool() {
        val data = handshake(FakeTrustState(disabled = setOf("notifications_reply")))
        val caps = data.getJSONObject("capabilities")
        assertFalse(caps.getBoolean("notifications_reply"))
        assertTrue(caps.getBoolean("observe_notifications"))
    }

    @Test
    fun disabledToggleFlipsHandshakeBool() {
        val data = handshake(FakeTrustState(disabled = setOf("act_set_text_native")))
        val caps = data.getJSONObject("capabilities")
        assertFalse(caps.getBoolean("act_set_text_native"))
        assertTrue(caps.getBoolean("act_gesture_native"))
    }

    @Test
    fun stoppedFlagPropagatesToHandshake() {
        val data = handshake(FakeTrustState(stopped = true))
        assertTrue(data.getBoolean("stopped"))
    }

    @Test
    fun pingReturnsPongTrue() {
        val line = JSONObject().put("version", 1).put("request_id", "p1")
            .put("method", "ping").put("params", JSONObject()).toString()
        val resp = JSONObject(Dispatcher(CoreHandlers.methods(FakeTrustState())).handleLine(line))
        assertTrue(resp.getBoolean("ok"))
        assertTrue(resp.getJSONObject("data").getBoolean("pong"))
    }

    @Test
    fun stopSentinelParity() {
        assertFalse(StopSentinel.stopped(inAppFlag = false, stopFileExists = false))
        assertTrue(StopSentinel.stopped(inAppFlag = true, stopFileExists = false))
        assertTrue(StopSentinel.stopped(inAppFlag = false, stopFileExists = true))
        assertTrue(StopSentinel.stopped(inAppFlag = true, stopFileExists = true))
    }
}
