package com.phonectl.companion.service

import android.app.Notification
import android.app.NotificationManager
import android.content.ComponentName
import android.content.Context
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import com.phonectl.companion.json.NotifAction
import com.phonectl.companion.json.NotifData
import com.phonectl.companion.json.Notifications
import com.phonectl.companion.state.TrustState
import com.phonectl.companion.transport.Method
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

    private fun notificationsList(): JSONObject {
        val items = activeNotificationsSafe().map { toNotifData(it) }
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

    companion object {
        @Volatile
        var instance: CompanionNotificationListenerService? = null
            private set

        /**
         * NotificationListenerService-backed method handlers, plugged into the foreground service's
         * dispatcher. Each resolves [instance] lazily; if the listener is not connected the handler
         * raises (becomes a handler_error envelope). Per-method capability gating is applied in the
         * dispatcher (Plan 4.6 Task 5), not here. [state] is unused today but kept symmetric with
         * the AccessibilityService factory for future per-handler checks.
         */
        @Suppress("UNUSED_PARAMETER")
        fun methods(state: TrustState): Map<String, Method> {
            fun svc(): CompanionNotificationListenerService =
                instance ?: throw IllegalStateException("notification listener not connected")
            return mapOf(
                "notifications_list" to { _ -> svc().notificationsList() },
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
