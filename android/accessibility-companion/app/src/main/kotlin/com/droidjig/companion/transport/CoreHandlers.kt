package com.droidjig.companion.transport

import com.droidjig.companion.state.Capabilities
import com.droidjig.companion.state.TrustState
import org.json.JSONObject

/**
 * Transport-level method handlers that do not need the AccessibilityService: liveness (`ping`)
 * and capability/stop negotiation (`handshake`). Pure given a [TrustState] so the handshake JSON
 * is exercised by JVM contract tests.
 */
object CoreHandlers {

    fun methods(state: TrustState): Map<String, Method> = mapOf(
        // Python only checks `ok`; {pong:true} is a superset of the foreground-service SPEC's {}.
        "ping" to { _ -> JSONObject().put("pong", true) },
        "handshake" to { _ ->
            Capabilities.handshakeData(state.enabledCapabilityMap(), state.isStopped())
        },
    )
}
