package com.phonectl.companion.transport

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

    // A bare RuntimeException helper so the test reads like the Python `raise RuntimeError`.
    private class RuntimeError(message: String) : RuntimeException(message)
}
