package com.phonectl.companion.service

import android.app.NotificationManager
import android.content.ComponentName
import android.content.Context
import android.service.notification.NotificationListenerService

/**
 * NotificationListenerService surface (accessibility-companion SPEC §6 / foreground-service SPEC §6):
 * notifications_list / notifications_reply / notifications_dismiss, dispatched over the same loopback
 * NDJSON transport as the AccessibilityService methods.
 *
 * Per the security note (foreground-service SPEC §9 / index invariant 5), no request payloads —
 * and in particular no reply text — are logged.
 *
 * Methods are wired in subsequent tasks; Task 1 establishes the bound-listener lifecycle, the
 * advertised capability keys (Capabilities.NOTIFICATION_KEYS), and the runtime grant check.
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

    companion object {
        @Volatile
        var instance: CompanionNotificationListenerService? = null
            private set

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
