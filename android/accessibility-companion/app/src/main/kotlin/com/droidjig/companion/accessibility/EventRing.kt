package com.droidjig.companion.accessibility

import com.droidjig.companion.json.Json
import org.json.JSONObject

/**
 * Bounded ring buffer of UI events (accessibility-companion SPEC §events). The service appends one
 * entry per AccessibilityEvent callback; the `events` method returns entries with `seq > since`,
 * up to `max`, echoing the cursor (the seq of the last returned event, or `since` if none). When
 * `since <= 0` the most recent `max` events are returned.
 *
 * Pure (no Android dependency); timestamps are supplied by the caller so it stays deterministic
 * and JVM-testable.
 */
class EventRing(private val capacity: Int = 200) {

    data class Event(val seq: Long, val type: String, val pkg: String, val ts: Long)

    private val buffer = ArrayDeque<Event>()
    private var nextSeq = 1L

    @Synchronized
    fun add(type: String, pkg: String, ts: Long): Long {
        val seq = nextSeq++
        buffer.addLast(Event(seq, type, pkg, ts))
        while (buffer.size > capacity) buffer.removeFirst()
        return seq
    }

    /** Returns (selected events, new cursor). */
    @Synchronized
    fun query(since: Long, max: Int): Pair<List<Event>, Long> {
        val cap = max.coerceAtLeast(0)
        val selected = if (since <= 0L) {
            buffer.toList().takeLast(cap)
        } else {
            buffer.asSequence().filter { it.seq > since }.take(cap).toList()
        }
        val cursor = selected.lastOrNull()?.seq ?: since
        return selected to cursor
    }

    /**
     * [excludePackages] drops events from guarded apps (Finding 10) AFTER selection, so the
     * cursor still advances past them and polling never re-delivers a guarded event.
     */
    fun queryJson(since: Long, max: Int, excludePackages: Set<String> = emptySet()): JSONObject {
        val (events, cursor) = query(since, max)
        val arr = Json.arr(
            events.filter { it.pkg !in excludePackages }.map {
                JSONObject()
                    .put("seq", it.seq)
                    .put("type", it.type)
                    .put("package", it.pkg)
                    .put("ts", it.ts)
            }
        )
        return JSONObject().put("events", arr).put("cursor", cursor)
    }
}
