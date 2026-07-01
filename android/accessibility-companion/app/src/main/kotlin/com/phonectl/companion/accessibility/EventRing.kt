package com.phonectl.companion.accessibility

import com.phonectl.companion.json.Json
import org.json.JSONObject
import java.util.concurrent.locks.ReentrantLock
import kotlin.concurrent.withLock

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

    private val lock = ReentrantLock()
    private val changed = lock.newCondition()
    private val buffer = ArrayDeque<Event>()
    private var nextSeq = 1L

    fun add(type: String, pkg: String, ts: Long): Long = lock.withLock {
        val seq = nextSeq++
        buffer.addLast(Event(seq, type, pkg, ts))
        while (buffer.size > capacity) buffer.removeFirst()
        changed.signalAll()
        seq
    }

    /** Returns (selected events, new cursor). */
    fun query(since: Long, max: Int): Pair<List<Event>, Long> = lock.withLock {
        queryLocked(since, max)
    }

    fun waitFor(since: Long, max: Int, timeoutMs: Long): Pair<List<Event>, Long> = lock.withLock {
        var remainingNanos = timeoutMs.coerceAtLeast(0L) * 1_000_000L
        var result = queryLocked(since, max)
        while (result.first.isEmpty() && remainingNanos > 0L) {
            try {
                remainingNanos = changed.awaitNanos(remainingNanos)
            } catch (e: InterruptedException) {
                Thread.currentThread().interrupt()
                break
            }
            result = queryLocked(since, max)
        }
        result
    }

    private fun queryLocked(since: Long, max: Int): Pair<List<Event>, Long> {
        val cap = max.coerceAtLeast(0)
        val selected = if (since <= 0L) {
            buffer.toList().takeLast(cap)
        } else {
            buffer.asSequence().filter { it.seq > since }.take(cap).toList()
        }
        val cursor = selected.lastOrNull()?.seq ?: since
        return selected to cursor
    }

    fun queryJson(since: Long, max: Int): JSONObject {
        val (events, cursor) = query(since, max)
        return toJson(events, cursor)
    }

    fun waitJson(since: Long, max: Int, timeoutMs: Long): JSONObject {
        val (events, cursor) = waitFor(since, max, timeoutMs)
        return toJson(events, cursor)
    }

    private fun toJson(events: List<Event>, cursor: Long): JSONObject {
        val arr = Json.arr(
            events.map {
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
