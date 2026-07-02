package com.phonectl.companion.json

import com.phonectl.companion.transport.MethodException
import org.json.JSONArray
import org.json.JSONObject

/**
 * One notification action: its label and whether it carries a `RemoteInput` (direct-reply).
 * Plain data so the org.json shape is built and tested without Android's `Notification.Action`.
 */
data class NotifAction(val title: String, val remoteInput: Boolean)

/**
 * A serialization view of one `StatusBarNotification` (accessibility-companion SPEC §6). The
 * service maps the Android object into this; the JSON builders below are pure and tested against
 * the Python authority fixture (tests/test_providers_notifications.py::COMPANION_RAW).
 */
data class NotifData(
    val key: String,
    val pkg: String,
    val title: String,
    val text: String,
    /** `Notification.category`; null serializes to JSON null (the Python side keeps it as None). */
    val category: String?,
    /** `StatusBarNotification.postTime`, epoch-ms. */
    val postTime: Long,
    val actions: List<NotifAction>,
)

/**
 * Pure org.json builders for the `notifications_list` shape. Field-by-field (no reflection mapper)
 * so the output provably matches COMPANION_RAW. `remote_input` is emitted only when true — matching
 * the fixture, where the non-reply action is `{"title":"Mark read"}` with the key absent.
 */
object Notifications {

    fun action(a: NotifAction): JSONObject {
        val o = JSONObject().put("title", a.title)
        if (a.remoteInput) o.put("remote_input", true)
        return o
    }

    fun notification(n: NotifData): JSONObject {
        val actions = JSONArray()
        for (a in n.actions) actions.put(action(a))
        return JSONObject()
            .put("key", n.key)
            .put("package", n.pkg)
            .put("title", n.title)
            .put("text", n.text)
            .put("category", n.category ?: JSONObject.NULL)
            .put("post_time", n.postTime)
            .put("actions", actions)
    }

    fun list(items: List<NotifData>): JSONObject {
        val arr = JSONArray()
        for (n in items) arr.put(notification(n))
        return JSONObject().put("notifications", arr)
    }

    /**
     * Resolve the index (within the notification's action list) of the first action carrying a
     * `RemoteInput`, for `notifications_reply`. Pure so the error contract is JVM-tested:
     *   - `not_found`        — no active notification has [key].
     *   - `no_remote_input`  — the notification has no inline-reply action.
     * The returned index aligns with the live `Notification.actions` array (same order), so the
     * service can fire the corresponding `Action.actionIntent`.
     */
    fun replyActionIndex(items: List<NotifData>, key: String): Int {
        val n = items.firstOrNull { it.key == key }
            ?: throw MethodException("not_found", "no active notification for the given key")
        val idx = n.actions.indexOfFirst { it.remoteInput }
        if (idx < 0) throw MethodException("no_remote_input", "notification has no inline-reply action")
        return idx
    }

    /**
     * Guard for `notifications_dismiss`: raise `not_found` when no active notification has [key].
     * The dismiss itself (`cancelNotification`) is Android-only; this keeps the error contract pure.
     */
    fun requireActive(items: List<NotifData>, key: String) {
        if (items.none { it.key == key })
            throw MethodException("not_found", "no active notification for the given key")
    }

    /**
     * The source package of the active notification with [key], or null if none matches. The
     * service feeds this to [com.phonectl.companion.state.ActionGate] so `notifications_reply` is
     * refused (`guarded_action`) when the notification belongs to a guarded app (SPEC §7.6). Pure
     * so the guarded-reply contract is JVM-tested.
     */
    fun packageForKey(items: List<NotifData>, key: String): String? =
        items.firstOrNull { it.key == key }?.pkg

    /**
     * Drop notifications from guarded apps before serialization (Finding 10): guarded protection
     * must cover reads, not just actions — a banking app's notification content (balances, OTPs)
     * is exactly what the guarded list exists to keep out of agent-visible output.
     */
    fun filterGuarded(items: List<NotifData>, guarded: Set<String>): List<NotifData> =
        items.filterNot { it.pkg in guarded }
}
