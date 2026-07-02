package com.phonectl.companion.transport

import com.phonectl.companion.state.Capabilities
import com.phonectl.companion.state.TrustStateStub
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Envelope/dispatch contract — mirrors tests/test_transport.py shapes field-for-field.
 * Responses are parsed and asserted by field (the Python side parses JSON; on-wire key
 * order is irrelevant).
 */
class DispatcherTest {

    private fun request(method: String, params: JSONObject = JSONObject(), id: String = "rid1"): String =
        JSONObject()
            .put("version", 1)
            .put("request_id", id)
            .put("method", method)
            .put("params", params)
            .put("timeout", 2.0)
            .toString()

    private fun dispatch(methods: Map<String, Method>, line: String): JSONObject? {
        val out = Dispatcher(methods).handleLine(line) ?: return null
        return JSONObject(out)
    }

    // test_request_echoes_request_id_and_wraps_data
    @Test
    fun echoesRequestIdAndWrapsData() {
        val methods = mapOf<String, Method>(
            "echo" to { p -> JSONObject().put("said", p.getString("msg")) }
        )
        val resp = dispatch(methods, request("echo", JSONObject().put("msg", "hi"), id = "abc"))!!
        assertEquals(1, resp.getInt("version"))
        assertEquals("abc", resp.getString("request_id"))
        assertTrue(resp.getBoolean("ok"))
        assertEquals("hi", resp.getJSONObject("data").getString("said"))
    }

    // test_request_unknown_method_returns_error_envelope
    @Test
    fun unknownMethodReturnsErrorEnvelope() {
        val resp = dispatch(emptyMap(), request("nope"))!!
        assertFalse(resp.getBoolean("ok"))
        assertEquals("rid1", resp.getString("request_id"))
        assertEquals("unknown_method", resp.getJSONObject("error").getString("code"))
    }

    // test_handler_exception_becomes_error_envelope
    @Test
    fun handlerExceptionBecomesErrorEnvelope() {
        val methods = mapOf<String, Method>(
            "boom" to { _ -> throw RuntimeError("kaboom") }
        )
        val resp = dispatch(methods, request("boom"))!!
        assertFalse(resp.getBoolean("ok"))
        val error = resp.getJSONObject("error")
        assertEquals("handler_error", error.getString("code"))
        assertTrue(error.getString("message").contains("kaboom"))
    }

    @Test
    fun methodExceptionCarriesTypedCode() {
        val methods = mapOf<String, Method>(
            "sem" to { _ -> throw MethodException("unsupported_action", "node lacks click") }
        )
        val resp = dispatch(methods, request("sem"))!!
        assertFalse(resp.getBoolean("ok"))
        assertEquals("unsupported_action", resp.getJSONObject("error").getString("code"))
    }

    @Test
    fun unknownVersionIsRejected() {
        val line = JSONObject().put("version", 2).put("request_id", "v2")
            .put("method", "ping").put("params", JSONObject()).toString()
        val resp = dispatch(mapOf("ping" to { _ -> JSONObject() }), line)!!
        assertFalse(resp.getBoolean("ok"))
        assertEquals("v2", resp.getString("request_id"))
        assertEquals("version_mismatch", resp.getJSONObject("error").getString("code"))
    }

    @Test
    fun nonJsonLineIsSilentlyDropped() {
        assertNull(Dispatcher(emptyMap()).handleLine("not json at all"))
        assertNull(Dispatcher(emptyMap()).handleLine(""))
        assertNull(Dispatcher(emptyMap()).handleLine("[1,2,3]")) // JSON, but not an object
    }

    private val replyMethods = mapOf<String, Method>(
        "notifications_reply" to { _ -> JSONObject().put("sent", true) }
    )

    @Test
    fun disabledCapabilityYieldsCapabilityDisabled() {
        val gate = Capabilities.methodGate(TrustStateStub(disabled = setOf("notifications_reply")))
        val resp = JSONObject(Dispatcher(replyMethods, gate).handleLine(request("notifications_reply"))!!)
        assertFalse(resp.getBoolean("ok"))
        assertEquals("capability_disabled", resp.getJSONObject("error").getString("code"))
    }

    @Test
    fun disabledAccessibilityCapabilitiesYieldCapabilityDisabled() {
        val methods = mapOf<String, Method>(
            "observe_native" to { _ -> JSONObject().put("observed", true) },
            "events" to { _ -> JSONObject().put("events", true) },
            "gesture" to { _ -> JSONObject().put("gestured", true) },
            "key" to { _ -> JSONObject().put("keyed", true) },
            "set_text" to { _ -> JSONObject().put("set", true) },
            "semantic" to { _ -> JSONObject().put("semantic", true) },
            "launch" to { _ -> JSONObject().put("launched", true) },
        )
        val methodDisabledCapability = mapOf(
            "observe_native" to "observe_ui_native",
            "events" to "observe_ui_events",
            "gesture" to "act_gesture_native",
            "key" to "act_gesture_native",
            "set_text" to "act_set_text_native",
            "semantic" to "act_semantic_action",
            "launch" to "launch_app",
        )

        for ((method, disabledCapability) in methodDisabledCapability) {
            val gate = Capabilities.methodGate(TrustStateStub(disabled = setOf(disabledCapability)))
            val resp = JSONObject(Dispatcher(methods, gate).handleLine(request(method))!!)
            assertFalse("$method should be blocked by $disabledCapability", resp.getBoolean("ok"))
            assertEquals("capability_disabled", resp.getJSONObject("error").getString("code"))
        }
    }

    @Test
    fun enabledCapabilityPassesThroughGate() {
        val gate = Capabilities.methodGate(TrustStateStub())
        val resp = JSONObject(Dispatcher(replyMethods, gate).handleLine(request("notifications_reply"))!!)
        assertTrue(resp.getBoolean("ok"))
        assertTrue(resp.getJSONObject("data").getBoolean("sent"))
    }

    @Test
    fun unknownMethodTakesPrecedenceOverGate() {
        // No handler -> unknown_method wins even though the (would-be) capability is disabled.
        val gate = Capabilities.methodGate(TrustStateStub(disabled = setOf("notifications_reply")))
        val resp = JSONObject(Dispatcher(emptyMap(), gate).handleLine(request("notifications_reply"))!!)
        assertEquals("unknown_method", resp.getJSONObject("error").getString("code"))
    }

    // --- shared-secret token auth (Finding 2) ---

    private val pingAndEcho = mapOf<String, Method>(
        "ping" to { _ -> JSONObject().put("pong", true) },
        "echo" to { p -> JSONObject().put("said", p.optString("msg")) },
    )

    private fun tokenRequest(method: String, token: String?): String {
        val req = JSONObject().put("version", 1).put("request_id", "t1")
            .put("method", method).put("params", JSONObject())
        if (token != null) req.put("token", token)
        return req.toString()
    }

    @Test
    fun requestWithoutTokenIsRejectedWhenTokenExpected() {
        val d = Dispatcher(pingAndEcho, expectedToken = "secret")
        val resp = JSONObject(d.handleLine(tokenRequest("echo", token = null))!!)
        assertFalse(resp.getBoolean("ok"))
        assertEquals("unauthorized", resp.getJSONObject("error").getString("code"))
    }

    @Test
    fun requestWithWrongTokenIsRejected() {
        val d = Dispatcher(pingAndEcho, expectedToken = "secret")
        val resp = JSONObject(d.handleLine(tokenRequest("echo", token = "nope"))!!)
        assertFalse(resp.getBoolean("ok"))
        assertEquals("unauthorized", resp.getJSONObject("error").getString("code"))
    }

    @Test
    fun requestWithCorrectTokenPasses() {
        val d = Dispatcher(pingAndEcho, expectedToken = "secret")
        val resp = JSONObject(d.handleLine(tokenRequest("echo", token = "secret"))!!)
        assertTrue(resp.getBoolean("ok"))
    }

    @Test
    fun pingIsExemptFromTokenForLiveness() {
        val d = Dispatcher(pingAndEcho, expectedToken = "secret")
        val resp = JSONObject(d.handleLine(tokenRequest("ping", token = null))!!)
        assertTrue(resp.getBoolean("ok"))
        assertTrue(resp.getJSONObject("data").getBoolean("pong"))
    }

    @Test
    fun unauthorizedTakesPrecedenceOverUnknownMethod() {
        // An unauthenticated caller must not be able to probe which methods exist.
        val d = Dispatcher(pingAndEcho, expectedToken = "secret")
        val resp = JSONObject(d.handleLine(tokenRequest("does_not_exist", token = null))!!)
        assertEquals("unauthorized", resp.getJSONObject("error").getString("code"))
    }

    @Test
    fun noTokenExpectedLeavesSocketOpen() {
        // expectedToken defaults to null (unpaired / JVM contract tests) — no token required.
        val d = Dispatcher(pingAndEcho)
        val resp = JSONObject(d.handleLine(tokenRequest("echo", token = null))!!)
        assertTrue(resp.getBoolean("ok"))
    }

    // --- on-device STOP gate (Finding 3) ---

    private val stopGateMethods = mapOf<String, Method>(
        "ping" to { _ -> JSONObject().put("pong", true) },
        "handshake" to { _ -> JSONObject().put("stopped", true) },
        "observe_native" to { _ -> JSONObject().put("observed", true) },
        "gesture" to { _ -> JSONObject().put("applied", true) },
        "key" to { _ -> JSONObject().put("applied", true) },
        "set_text" to { _ -> JSONObject().put("applied", true) },
        "semantic" to { _ -> JSONObject().put("performed", "click") },
        "launch" to { _ -> JSONObject().put("launched", true) },
        "screencap" to { _ -> JSONObject().put("path", "/x.png") },
        "ocr_screen" to { _ -> JSONObject().put("regions", true) },
        "events" to { _ -> JSONObject().put("events", true) },
        "notifications_list" to { _ -> JSONObject().put("notifications", true) },
        "notifications_reply" to { _ -> JSONObject().put("sent", true) },
        "notifications_dismiss" to { _ -> JSONObject().put("dismissed", true) },
        "ocr_image" to { _ -> JSONObject().put("regions", true) },
    )

    @Test
    fun dispatcherRefusesAllActionMethodsWhenStopped() {
        // Enforcement must be on-device (fail closed), not delegated to the Python client:
        // a direct socket client gets `stopped` for every method while STOP is engaged.
        val d = Dispatcher(stopGateMethods, stopped = { true })
        for (method in stopGateMethods.keys - setOf("ping", "handshake")) {
            val resp = JSONObject(d.handleLine(request(method))!!)
            assertFalse("$method must be refused while stopped", resp.getBoolean("ok"))
            assertEquals("stopped", resp.getJSONObject("error").getString("code"))
        }
    }

    @Test
    fun handshakeAndPingStillAllowedWhenStopped() {
        // The Python side learns about STOP via handshake.stopped; ping stays open for liveness.
        val d = Dispatcher(stopGateMethods, stopped = { true })
        assertTrue(JSONObject(d.handleLine(request("ping"))!!).getBoolean("ok"))
        assertTrue(JSONObject(d.handleLine(request("handshake"))!!).getBoolean("ok"))
    }

    @Test
    fun stopGateReReadsStateOnEveryRequest() {
        var stopped = false
        val d = Dispatcher(stopGateMethods, stopped = { stopped })
        assertTrue(JSONObject(d.handleLine(request("gesture"))!!).getBoolean("ok"))
        stopped = true
        val resp = JSONObject(d.handleLine(request("gesture"))!!)
        assertEquals("stopped", resp.getJSONObject("error").getString("code"))
    }

    @Test
    fun stoppedTakesPrecedenceOverCapabilityGate() {
        val gate = Capabilities.methodGate(TrustStateStub(disabled = setOf("notifications_reply")))
        val d = Dispatcher(stopGateMethods, gate, stopped = { true })
        val resp = JSONObject(d.handleLine(request("notifications_reply"))!!)
        assertEquals("stopped", resp.getJSONObject("error").getString("code"))
    }

    // --- no-payload logging (Plan 4.8 Task 2 / SPEC §9) ---

    @Test
    fun logRecordsMethodAndOkOutcomeWithoutPayload() {
        val logs = mutableListOf<String>()
        val methods = mapOf<String, Method>(
            "set_text" to { _ -> JSONObject().put("applied", true) }
        )
        val d = Dispatcher(methods, log = { logs.add(it) })
        // The typed text must never reach the log; only method + outcome do.
        d.handleLine(request("set_text", JSONObject().put("text", "hunter2-secret")))
        assertTrue("expected a method=set_text outcome=ok line, got $logs",
            logs.any { it.contains("method=set_text") && it.contains("outcome=ok") })
        assertFalse("payload leaked into log: $logs", logs.any { it.contains("hunter2-secret") })
    }

    @Test
    fun logRecordsErrorOutcomeAndCodeWithoutPayload() {
        val logs = mutableListOf<String>()
        val methods = mapOf<String, Method>(
            "notifications_reply" to { _ -> throw MethodException("guarded_action", "refused") }
        )
        val d = Dispatcher(methods, log = { logs.add(it) })
        d.handleLine(request("notifications_reply", JSONObject().put("text", "topsecret-reply")))
        assertTrue("expected outcome=err code=guarded_action, got $logs",
            logs.any {
                it.contains("method=notifications_reply") && it.contains("outcome=err") &&
                    it.contains("code=guarded_action")
            })
        assertFalse("payload leaked into log: $logs", logs.any { it.contains("topsecret-reply") })
    }

    @Test
    fun unknownMethodAndVersionMismatchAreLoggedAsErrors() {
        val logs = mutableListOf<String>()
        val d = Dispatcher(emptyMap(), log = { logs.add(it) })
        d.handleLine(request("nope"))
        assertTrue(logs.any { it.contains("outcome=err") && it.contains("code=unknown_method") })
        val v2 = JSONObject().put("version", 2).put("request_id", "v2")
            .put("method", "ping").put("params", JSONObject()).toString()
        d.handleLine(v2)
        assertTrue(logs.any { it.contains("outcome=err") && it.contains("code=version_mismatch") })
    }

    // A bare RuntimeException helper so the test reads like the Python `raise RuntimeError`.
    private class RuntimeError(message: String) : RuntimeException(message)
}
