package com.droidjig.companion.accessibility

import com.droidjig.companion.transport.MethodException
import org.json.JSONObject

/**
 * Observation binding (Finding 9): node ids — index paths and even viewIdResourceName — are only
 * meaningful against the tree the caller observed. `observe_native` returns a monotonically
 * increasing `generation` token (bumped by the service on every tree-mutating accessibility
 * event); `set_text`/`semantic` requests may echo it back and are refused with `stale_generation`
 * when the tree has changed since that observation, instead of silently resolving the id against
 * a different tree.
 *
 * An absent `generation` param means the caller opted out (back-compat with pre-Finding-9
 * clients); the droidjig Python provider always sends the token from its last observation.
 *
 * Pure (no Android dependency) — exercised by JVM tests.
 */
object Generation {

    /** The generation the request was reasoned over, or null when the caller opted out. */
    fun requested(params: JSONObject): Long? =
        if (params.has("generation")) params.optLong("generation") else null

    /** Refuse a request whose observation no longer matches the live tree. */
    fun requireFresh(requested: Long?, current: Long) {
        if (requested != null && requested != current) {
            throw MethodException(
                "stale_generation",
                "observation generation $requested is stale (current $current) — re-observe",
            )
        }
    }
}
