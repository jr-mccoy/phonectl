package com.phonectl.companion.state

import com.phonectl.companion.transport.CoreHandlers
import com.phonectl.companion.transport.Dispatcher
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Handshake/ping contract — the handshake shape matches foreground-service SPEC §3 and the
 * keys the Python side recognizes (tests/test_trust.py negotiate fields: version, capabilities,
 * stopped). Capability defaults are enabled (SPEC §6); a disabled toggle flips its bool.
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
    fun handshakeAdvertisesAllKeysEnabledByDefault() {
        val data = handshake(FakeTrustState())
        assertEquals(1, data.getInt("version"))
        assertFalse(data.getBoolean("stopped"))
        val caps = data.getJSONObject("capabilities")
        val expected = setOf(
            "observe_ui_native", "observe_ui_events", "act_gesture_native",
            "act_set_text_native", "act_semantic_action",
            "observe_notifications", "notifications_wait", "notifications_reply",
            "notifications_dismiss",
            "observe_ocr",
        )
        assertEquals(expected, caps.keys().asSequence().toSet())
        for (key in expected) assertTrue("$key should default enabled", caps.getBoolean(key))
    }

    @Test
    fun handshakeAdvertisesTheFourNotificationKeysEnabledByDefault() {
        val caps = handshake(FakeTrustState()).getJSONObject("capabilities")
        for (key in Capabilities.NOTIFICATION_KEYS) {
            assertTrue("$key should be present", caps.has(key))
            assertTrue("$key should default enabled", caps.getBoolean(key))
        }
    }

    @Test
    fun handshakeAdvertisesObserveOcrEnabledByDefault() {
        val caps = handshake(FakeTrustState()).getJSONObject("capabilities")
        assertTrue("observe_ocr should be present", caps.has("observe_ocr"))
        assertTrue("observe_ocr should default enabled", caps.getBoolean("observe_ocr"))
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
