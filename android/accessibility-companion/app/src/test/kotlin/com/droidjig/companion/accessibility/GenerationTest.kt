package com.droidjig.companion.accessibility

import com.droidjig.companion.transport.MethodException
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.fail
import org.junit.Test

/**
 * Observation-binding contract (Finding 9): observe_native returns a tree-generation token;
 * set_text/semantic carrying a stale token are refused with `stale_generation`, and node-id
 * lookups that match more than one node are refused with `ambiguous_node_id` instead of silently
 * acting on the first match.
 */
class GenerationTest {

    // --- requested-generation extraction ---

    @Test
    fun requestedReadsGenerationParam() {
        assertEquals(7L, Generation.requested(JSONObject().put("generation", 7L)))
    }

    @Test
    fun requestedIsNullWhenParamAbsent() {
        // Absent means the caller opted out (back-compat) — the gate then allows the action.
        assertNull(Generation.requested(JSONObject()))
    }

    // --- freshness gate ---

    @Test
    fun requireFreshPassesOnMatchingGeneration() {
        Generation.requireFresh(requested = 42L, current = 42L) // must not throw
    }

    @Test
    fun requireFreshAllowsOptedOutCallers() {
        Generation.requireFresh(requested = null, current = 42L) // must not throw
    }

    @Test
    fun setTextRejectsStaleGeneration() {
        try {
            Generation.requireFresh(requested = 41L, current = 42L)
            fail("expected stale_generation")
        } catch (e: MethodException) {
            assertEquals("stale_generation", e.code)
        }
    }

    // --- ambiguous node-id refusal ---

    @Test
    fun findByIdRefusesAmbiguousResourceId() {
        // viewIdResourceName is non-unique (list rows share ids); acting on "the first match"
        // silently targets the wrong node, so two or more matches must refuse.
        try {
            NodeId.requireUnambiguous(matchCount = 2, nodeId = "com.app:id/row_title")
            fail("expected ambiguous_node_id")
        } catch (e: MethodException) {
            assertEquals("ambiguous_node_id", e.code)
        }
    }

    @Test
    fun uniqueMatchPassesUnambiguousCheck() {
        NodeId.requireUnambiguous(matchCount = 1, nodeId = "com.app:id/ok") // must not throw
        NodeId.requireUnambiguous(matchCount = 0, nodeId = "com.app:id/gone") // not-found handled later
    }
}
