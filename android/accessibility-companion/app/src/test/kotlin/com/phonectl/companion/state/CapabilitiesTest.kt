package com.phonectl.companion.state

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class CapabilitiesTest {

    @Test
    fun methodGateCoversAccessibilityMethods() {
        val expected = mapOf(
            "observe_native" to "observe_ui_native",
            "events" to "observe_ui_events",
            "gesture" to "act_gesture_native",
            "key" to "act_gesture_native",
            "set_text" to "act_set_text_native",
            "semantic" to "act_semantic_action",
            "launch" to "launch_app",
        )

        for ((method, capability) in expected) {
            assertEquals(capability, Capabilities.METHOD_CAPABILITY[method])
        }
    }

    @Test
    fun methodGateBlocksEachDisabledAccessibilityCapability() {
        val methodsByCapability = mapOf(
            "observe_ui_native" to listOf("observe_native"),
            "observe_ui_events" to listOf("events"),
            "act_gesture_native" to listOf("gesture", "key"),
            "act_set_text_native" to listOf("set_text"),
            "act_semantic_action" to listOf("semantic"),
            "launch_app" to listOf("launch"),
        )

        for ((capability, methods) in methodsByCapability) {
            val gate = Capabilities.methodGate(TrustStateStub(disabled = setOf(capability)))
            for (method in methods) {
                assertEquals("$method should be blocked by $capability", "capability_disabled", gate(method))
            }
        }
    }

    @Test
    fun methodGateAllowsEnabledAndUnmappedMethods() {
        val gate = Capabilities.methodGate(TrustStateStub())
        for (method in listOf("observe_native", "events", "gesture", "key", "set_text", "semantic", "launch")) {
            assertNull("$method should be allowed when its capability is enabled", gate(method))
        }
        assertNull(gate("unmapped_method"))
    }

    // --- on-device STOP gate (Finding 3): fail closed independent of the Python client ---

    @Test
    fun methodGateRefusesEveryActionMethodWhenStopped() {
        val gate = Capabilities.methodGate(TrustStateStub(stopped = true))
        val actionMethods = listOf(
            "observe_native", "events", "gesture", "key", "set_text", "semantic", "launch",
            "notifications_list", "notifications_reply", "notifications_dismiss",
            "ocr_image", "ocr_screen", "unmapped_method",
        )
        for (method in actionMethods) {
            assertEquals("$method must fail closed when stopped", "stopped", gate(method))
        }
    }

    @Test
    fun methodGateStillAllowsHandshakeAndPingWhenStopped() {
        val gate = Capabilities.methodGate(TrustStateStub(stopped = true))
        assertNull("ping must stay available for liveness when stopped", gate("ping"))
        assertNull("handshake must stay available to report stopped state", gate("handshake"))
    }

    @Test
    fun stopTakesPrecedenceOverCapabilityDisabled() {
        val gate = Capabilities.methodGate(
            TrustStateStub(disabled = setOf("act_set_text_native"), stopped = true)
        )
        assertEquals("stopped", gate("set_text"))
    }
}
