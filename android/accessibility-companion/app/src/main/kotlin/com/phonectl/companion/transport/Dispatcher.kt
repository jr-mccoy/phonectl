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
 * without each handler re-checking. Defaults to ungated.
 *
 * [expectedToken] is the paired shared secret (Finding 2). On Android, loopback is NOT a UID
 * boundary: any local app with INTERNET permission can connect to the companion socket. When a
 * token is set, every method except the `ping` liveness probe must present a matching `token`
 * field or is refused with `unauthorized` — checked before method lookup so an unauthenticated
 * caller cannot even probe which methods exist. Null (unpaired) leaves the socket open, matching
 * the JVM contract tests; the on-device service always supplies one (SharedPrefsTrustState).
 *
 * [stopped] is the emergency-stop probe (Finding 3). It is re-read on EVERY request so the gate
 * tracks the live TrustState, and it is enforced here — on-device, before any handler runs — not
 * delegated to the Python client: a direct socket client acting despite STOP gets a `stopped`
 * error for every method except the `ping` liveness probe and `handshake` (which is how the
 * Python side learns `stopped=true` in the first place). Defaults to never-stopped for the pure
 * JVM contract tests; the foreground service wires `state::isStopped`.
 *
 * [log] receives a one-line audit record per request: the method name and outcome **only** —
 * `method=<name> outcome=ok` or `method=<name> outcome=err code=<code>`. It is NEVER passed the
 * request `params` or the response `data`, so typed text / reply bodies cannot reach the log
 * (foreground-service SPEC §9 / index invariant 5). Defaults to a no-op; the foreground service
 * wires an `android.util.Log` sink. Pure (no Android dependency) so it is JVM-tested.
 */
class Dispatcher(
    private val methods: Map<String, Method>,
    private val gate: (String) -> String? = { null },
    private val expectedToken: String? = null,
    private val stopped: () -> Boolean = { false },
    private val log: (String) -> Unit = {},
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
        val method = req.optString("method", "")

        val version = req.optInt("version", -1)
        if (version != Envelope.VERSION) {
            return fail(requestId, method, "version_mismatch", "unsupported version: $version")
        }

        // Shared-secret auth (Finding 2): loopback is not a trust boundary on Android. `ping`
        // stays open for liveness; every other method must present the paired token.
        if (expectedToken != null && method != "ping" && req.optString("token", "") != expectedToken) {
            return fail(requestId, method, "unauthorized", "missing or invalid token")
        }

        // Emergency-stop gate (Finding 3): enforced on-device so a direct client cannot act while
        // STOP is engaged. `ping` stays open for liveness; `handshake` stays open because its
        // `stopped` flag is how the Python side observes the stop in the first place.
        if (method != "ping" && method != "handshake" && stopped()) {
            return fail(requestId, method, "stopped", "companion emergency stop is engaged")
        }

        val handler = methods[method]
            ?: return fail(requestId, method, "unknown_method", "no handler for '$method'")

        gate(method)?.let { code ->
            return fail(requestId, method, code, "'$method' is disabled in the companion settings")
        }

        val params = req.optJSONObject("params") ?: JSONObject()

        return try {
            val response = Envelope.success(requestId, handler(params)).toString()
            log("method=$method outcome=ok")
            response
        } catch (e: MethodException) {
            fail(requestId, method, e.code, e.message ?: e.code)
        } catch (e: Throwable) {
            // Security note (foreground-service SPEC §9): the log line carries the code only — never
            // the exception message, which could echo payload-derived text.
            fail(requestId, method, "handler_error", e.message ?: e.toString())
        }
    }

    /** Build an error envelope and emit the method+outcome+code audit line (no payload). */
    private fun fail(requestId: Any?, method: String, code: String, message: String): String {
        log("method=$method outcome=err code=$code")
        return Envelope.error(requestId, code, message).toString()
    }
}
