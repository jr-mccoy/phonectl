package com.phonectl.companion.accessibility

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class NodeIdAndEventsTest {

    @Test
    fun resolvePrefersResourceName() {
        assertEquals("com.app:id/ok", NodeId.resolve("com.app:id/ok", 1, listOf(0, 3)))
    }

    @Test
    fun resolveFallsBackToDeterministicPath() {
        assertEquals("w1/0/3", NodeId.resolve(null, 1, listOf(0, 3)))
        assertEquals("w1/0/3", NodeId.resolve("", 1, listOf(0, 3)))
        // same tree position -> same id, so set_text/semantic can re-find it
        assertEquals(NodeId.pathId(2, listOf(1, 4, 2)), NodeId.pathId(2, listOf(1, 4, 2)))
        assertEquals("w2", NodeId.pathId(2, emptyList()))
    }

    @Test
    fun passwordGuardDetectsPasswordVariations() {
        // explicit isPassword flag
        assertTrue(PasswordGuard.isPassword(0, nodeIsPassword = true))
        // TYPE_CLASS_TEXT | TYPE_TEXT_VARIATION_PASSWORD = 0x81
        assertTrue(PasswordGuard.isPassword(0x81, nodeIsPassword = false))
        // web password 0xe1
        assertTrue(PasswordGuard.isPassword(0xe1, nodeIsPassword = false))
        // number password: TYPE_CLASS_NUMBER | 0x10 = 0x12
        assertTrue(PasswordGuard.isPassword(0x12, nodeIsPassword = false))
        // plain text field 0x01 is NOT a password
        assertFalse(PasswordGuard.isPassword(0x01, nodeIsPassword = false))
    }

    @Test
    fun eventRingSinceZeroReturnsMostRecentMax() {
        val ring = EventRing()
        for (i in 1..10) ring.add("content_changed", "com.app", ts = i.toLong())
        val j = ring.queryJson(since = 0, max = 3)
        val events = j.getJSONArray("events")
        assertEquals(3, events.length())
        assertEquals(8L, events.getJSONObject(0).getLong("seq"))
        assertEquals(10L, j.getLong("cursor"))
    }

    @Test
    fun eventRingSinceCursorReturnsOnlyNewer() {
        val ring = EventRing()
        for (i in 1..5) ring.add("view_clicked", "com.app", ts = i.toLong())
        val j = ring.queryJson(since = 3, max = 50)
        val events = j.getJSONArray("events")
        assertEquals(2, events.length())
        assertEquals(4L, events.getJSONObject(0).getLong("seq"))
        assertEquals(5L, j.getLong("cursor"))
    }

    @Test
    fun eventRingNoNewerEventsEchoesSince() {
        val ring = EventRing()
        ring.add("window_state_changed", "com.app", ts = 1)
        val j = ring.queryJson(since = 99, max = 50)
        assertEquals(0, j.getJSONArray("events").length())
        assertEquals(99L, j.getLong("cursor"))
    }

    @Test
    fun eventRingEvictsBeyondCapacity() {
        val ring = EventRing(capacity = 3)
        for (i in 1..5) ring.add("content_changed", "com.app", ts = i.toLong())
        val j = ring.queryJson(since = 0, max = 50)
        // only the last 3 survive; oldest surviving seq is 3
        assertEquals(3, j.getJSONArray("events").length())
        assertEquals(3L, j.getJSONArray("events").getJSONObject(0).getLong("seq"))
    }

    @Test
    fun eventRingWaitReturnsImmediateEvent() {
        val ring = EventRing()
        ring.add("content_changed", "com.app", ts = 1)
        val j = ring.waitJson(since = 0, max = 10, timeoutMs = 1_000)
        assertEquals(1, j.getJSONArray("events").length())
        assertEquals(1L, j.getLong("cursor"))
    }

    @Test
    fun eventRingWaitTimesOutWithNoEvents() {
        val ring = EventRing()
        val start = System.currentTimeMillis()
        val j = ring.waitJson(since = 1, max = 10, timeoutMs = 25)
        assertEquals(0, j.getJSONArray("events").length())
        assertEquals(1L, j.getLong("cursor"))
        assertTrue(System.currentTimeMillis() - start >= 15)
    }

    @Test
    fun eventRingWaitAdvancesCursor() {
        val ring = EventRing()
        ring.add("content_changed", "com.app", ts = 1)
        val first = ring.waitJson(since = 0, max = 10, timeoutMs = 0)
        ring.add("view_clicked", "com.app", ts = 2)
        val second = ring.waitJson(since = first.getLong("cursor"), max = 10, timeoutMs = 0)
        assertEquals(1, second.getJSONArray("events").length())
        assertEquals(2L, second.getLong("cursor"))
    }

    @Test
    fun semanticActionVocabulary() {
        assertTrue(SemanticActions.isSupported("click"))
        assertTrue(SemanticActions.isSupported("scroll_forward"))
        assertFalse(SemanticActions.isSupported("teleport"))
    }
}
