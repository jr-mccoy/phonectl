package com.phonectl.companion.service

import android.app.Notification
import android.app.NotificationManager
import android.app.RemoteInput
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import com.phonectl.companion.json.NotifAction
import com.phonectl.companion.json.NotifData
import com.phonectl.companion.json.Notifications
import com.phonectl.companion.state.ActionGate
import com.phonectl.companion.state.TrustState
import com.phonectl.companion.transport.Method
import com.phonectl.companion.transport.MethodException
import org.json.JSONObject

/**
 * NotificationListenerService surface (accessibility-companion SPEC §6 / foreground-service SPEC §6):
 * notifications_list / notifications_reply / notifications_dismiss, dispatched over the same loopback
 * NDJSON transport as the AccessibilityService methods.
 *
 * Per the security note (foreground-service SPEC §9 / index invariant 5), no request payloads —
 * and in particular no reply text — are logged.
 */
class CompanionNotificationListenerService : NotificationListenerService() {

    override fun onListenerConnected() {
        super.onListenerConnected()
        instance = this
    }

    override fun onListenerDisconnected() {
        if (instance === this) instance = null
        super.onListenerDisconnected()
    }

    override fun onDestroy() {
        if (instance === this) instance = null
        super.onDestroy()
    }

    // --- notifications_list ---

    private fun notificationsList(state: TrustState): JSONObject {
        // Guarded-app protection covers reads too (Finding 10): a guarded app's notification
        // content (balances, OTPs, message bodies) never reaches the transport.
        val items = Notifications.filterGuarded(
            activeNotificationsSafe().map { toNotifData(it) },
            state.guardedPackages(),
        )
        return Notifications.list(items)
    }

    /** `getActiveNotifications()` can throw if the listener is not bound — degrade to empty. */
    private fun activeNotificationsSafe(): List<StatusBarNotification> =
        runCatching { activeNotifications?.toList() ?: emptyList() }.getOrDefault(emptyList())

    private fun toNotifData(sbn: StatusBarNotification): NotifData {
        val n = sbn.notification
        val extras = n?.extras
        val actions = (n?.actions?.toList() ?: emptyList()).map { a ->
            NotifAction(
                title = a.title?.toString() ?: "",
                remoteInput = a.remoteInputs?.isNotEmpty() == true,
            )
        }
        return NotifData(
            key = sbn.key,
            pkg = sbn.packageName ?: "",
            title = extras?.getCharSequence(Notification.EXTRA_TITLE)?.toString() ?: "",
            text = extras?.getCharSequence(Notification.EXTRA_TEXT)?.toString() ?: "",
            category = n?.category,
            postTime = sbn.postTime,
            actions = actions,
        )
    }

    // --- notifications_reply ---

    private fun reply(params: JSONObject, state: TrustState): JSONObject {
        val key = params.optString("key", "")
        val text = params.optString("text", "")
        val sbns = activeNotificationsSafe()
        val datas = sbns.map { toNotifData(it) }
        // Guarded-app refusal (foreground-service SPEC §7.6): refuse replying to a notification that
        // belongs to a guarded app. Checked before the resolver so a guarded match returns
        // guarded_action; an unknown key still falls through to not_found below.
        val source = Notifications.packageForKey(datas, key)
        if (ActionGate.isGuarded(source, state.guardedPackages())) {
            throw MethodException("guarded_action", "reply refused for guarded app '$source'")
        }
        // Validate (not_found / no_remote_input) against the pure, JVM-tested resolver, then fire
        // the matching live Action — actions[idx] aligns with the serialized order.
        val idx = Notifications.replyActionIndex(datas, key)
        // replyActionIndex guarantees a reply-capable action exists at [idx]; re-guard for nullable.
        val actions = sbns.first { it.key == key }.notification.actions
            ?: throw MethodException("no_remote_input", "notification has no inline-reply action")
        val action = actions[idx]
        val remoteInputs = action.remoteInputs
            ?: throw MethodException("no_remote_input", "notification has no inline-reply action")
        val results = Bundle()
        for (ri in remoteInputs) results.putCharSequence(ri.resultKey, text)
        val intent = Intent()
        RemoteInput.addResultsToIntent(remoteInputs, intent, results)
        action.actionIntent.send(this, 0, intent)
        return JSONObject().put("sent", true)
    }

    // --- notifications_dismiss ---

    private fun dismiss(params: JSONObject, state: TrustState): JSONObject {
        val key = params.optString("key", "")
        val datas = activeNotificationsSafe().map { toNotifData(it) }
        // Dismissing a guarded app's notification is an action on it — refuse like reply
        // (Finding 10). Checked before the resolver so a guarded match returns guarded_action.
        val source = Notifications.packageForKey(datas, key)
        if (ActionGate.isGuarded(source, state.guardedPackages())) {
            throw MethodException("guarded_action", "dismiss refused for guarded app '$source'")
        }
        Notifications.requireActive(datas, key)
        cancelNotification(key)
        return JSONObject().put("dismissed", true)
    }

    companion object {
        @Volatile
        var instance: CompanionNotificationListenerService? = null
            private set

        /**
         * NotificationListenerService-backed method handlers, plugged into the foreground service's
         * dispatcher. Each resolves [instance] lazily; if the listener is not connected the handler
         * raises (becomes a handler_error envelope). Per-method capability gating is applied in the
         * dispatcher (Plan 4.6 Task 5); [state] is threaded into `reply` for the guarded-app
         * refusal (Plan 4.8 / foreground-service SPEC §7.6).
         */
        fun methods(state: TrustState): Map<String, Method> {
            fun svc(): CompanionNotificationListenerService =
                instance ?: throw IllegalStateException("notification listener not connected")
            return mapOf(
                "notifications_list" to { _ -> svc().notificationsList(state) },
                "notifications_reply" to { p -> svc().reply(p, state) },
                "notifications_dismiss" to { p -> svc().dismiss(p, state) },
            )
        }

        /**
         * Whether the user has granted notification-listener access to this companion
         * (Settings → Notifications → Device & app notifications → [companion] → Allow). Surfaced
         * in SettingsActivity as a setup hint.
         */
        fun isAccessGranted(context: Context): Boolean {
            val mgr = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            val component = ComponentName(context, CompanionNotificationListenerService::class.java)
            return runCatching { mgr.isNotificationListenerAccessGranted(component) }
                .getOrDefault(false)
        }
    }
}
