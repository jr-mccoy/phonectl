package com.droidjig.companion.transport

import org.json.JSONObject

/**
 * Request/response envelope construction.
 *
 * Wire contract (from src/droidjig/providers/transport.py):
 *   request  : {version:1, request_id:<hex>, method, params:{}, timeout:<s>}
 *   success  : {version:1, request_id:<echoed>, ok:true,  data:{...}}
 *   error    : {version:1, request_id:<echoed>, ok:false, error:{code, message}}
 *
 * Every response MUST echo request_id exactly (stale-response protection): the Python
 * SocketTransport drops any response whose request_id does not match the pending request.
 */
object Envelope {

    const val VERSION = 1

    fun success(requestId: Any?, data: JSONObject): JSONObject =
        JSONObject()
            .put("version", VERSION)
            .put("request_id", requestId ?: JSONObject.NULL)
            .put("ok", true)
            .put("data", data)

    fun error(requestId: Any?, code: String, message: String): JSONObject =
        JSONObject()
            .put("version", VERSION)
            .put("request_id", requestId ?: JSONObject.NULL)
            .put("ok", false)
            .put(
                "error",
                JSONObject().put("code", code).put("message", message)
            )
}

/**
 * Thrown by a method handler to return a typed error envelope (SPEC §8 error codes:
 * node_not_found, unsupported_action, gesture_rejected, screencap_unavailable,
 * guarded_action, capability_disabled, ...). Any other throwable becomes `handler_error`.
 */
class MethodException(val code: String, message: String) : Exception(message)
