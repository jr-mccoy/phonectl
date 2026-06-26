package com.phonectl.companion.transport

import org.json.JSONException
import org.json.JSONObject

/** A method handler: takes the request `params` object and returns the success `data` object. */
typealias Method = (JSONObject) -> JSONObject

/**
 * Routes one NDJSON request line to its method handler and wraps the result (or exception)
 * into the success/error envelope.
 *
 * - Non-JSON / non-object lines are silently dropped (returns null — no response emitted).
 * - Unknown major `version` -> {ok:false, error:{code:"version_mismatch"}}.
 * - Unknown method -> {ok:false, error:{code:"unknown_method"}}.
 * - Method gated off by [gate] -> {ok:false, error:{code:"capability_disabled"}}.
 * - MethodException -> {ok:false, error:{code:<typed>}}.
 * - Any other throwable -> {ok:false, error:{code:"handler_error"}}.
 *
 * [gate] maps a method name to a blocking error code (or null to allow); it lets the trust layer
 * refuse a method whose per-capability toggle is off (Plan 4.6 / foreground-service SPEC §6)
 * without each handler re-checking. Defaults to ungated. Pure (no Android dependency) so it is
 * exercised by JVM contract tests.
 */
class Dispatcher(
    private val methods: Map<String, Method>,
    private val gate: (String) -> String? = { null },
) {

    /** Returns the response line to write back, or null if the line should be silently dropped. */
    fun handleLine(line: String): String? {
        val trimmed = line.trim()
        if (trimmed.isEmpty()) return null

        val req = try {
            JSONObject(trimmed)
        } catch (e: JSONException) {
            return null // not a JSON object — drop silently
        }

        val requestId = req.opt("request_id")

        val version = req.optInt("version", -1)
        if (version != Envelope.VERSION) {
            return Envelope.error(requestId, "version_mismatch", "unsupported version: $version")
                .toString()
        }

        val method = req.optString("method", "")
        val handler = methods[method]
            ?: return Envelope.error(requestId, "unknown_method", "no handler for '$method'")
                .toString()

        gate(method)?.let { code ->
            return Envelope.error(requestId, code, "'$method' is disabled in the companion settings")
                .toString()
        }

        val params = req.optJSONObject("params") ?: JSONObject()

        return try {
            Envelope.success(requestId, handler(params)).toString()
        } catch (e: MethodException) {
            Envelope.error(requestId, e.code, e.message ?: e.code).toString()
        } catch (e: Throwable) {
            // Security note (foreground-service SPEC §9): never log request payloads.
            Envelope.error(requestId, "handler_error", e.message ?: e.toString()).toString()
        }
    }
}
