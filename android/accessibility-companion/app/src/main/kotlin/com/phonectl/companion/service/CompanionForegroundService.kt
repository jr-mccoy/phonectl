package com.phonectl.companion.service

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import com.phonectl.companion.R
import com.phonectl.companion.state.Capabilities
import com.phonectl.companion.state.SharedPrefsTrustState
import com.phonectl.companion.transport.CoreHandlers
import com.phonectl.companion.transport.Dispatcher
import com.phonectl.companion.transport.Method
import com.phonectl.companion.transport.Server

/**
 * Foreground service hosting the loopback NDJSON [Server] and the persistent "Stop phonectl"
 * notification (foreground-service SPEC §1/§4).
 *
 * The Stop action does NOT kill the process — it only flips `stopped=true` in SharedPrefs (the
 * companion's authoritative STOP state). Enforcement is on-device: `Capabilities.methodGate`
 * refuses every action method with `stopped` while the flag is set (Finding 3), so a direct client
 * cannot act during STOP even if it ignores the handshake's `stopped` flag. The Python side also
 * re-reads the flag each `handshake` cycle as a second layer. Resume clears it.
 */
class CompanionForegroundService : Service() {

    private lateinit var state: SharedPrefsTrustState
    private var server: Server? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        state = SharedPrefsTrustState(this)
        createChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> state.setStopped(true)
            ACTION_RESUME -> state.setStopped(false)
            // ACTION_START / null / ACTION_REFRESH fall through to (re)start + refresh notification
        }
        startForegroundWithNotification()
        startServer()
        return START_STICKY
    }

    override fun onDestroy() {
        server?.stop()
        server = null
        super.onDestroy()
    }

    // --- transport ---

    private fun startServer() {
        if (server != null) return
        val port = Server.DEFAULT_PORT // port override seam (prefs) — default 8765 for MVP
        val methods = HashMap<String, Method>()
        methods.putAll(CoreHandlers.methods(state))
        methods.putAll(CompanionAccessibilityService.methods(state))
        // NotificationListenerService methods (Plan 4.6) over the same loopback transport.
        methods.putAll(CompanionNotificationListenerService.methods(state))
        // Bundled ML-Kit OCR fallback (Plan 4.7) — no service instance needed.
        methods.putAll(OcrHandler.methods())
        // Per-capability gate refuses a method whose toggle the user switched off. The audit log
        // sink records method + outcome only — never request payloads (SPEC §9). The shared-secret
        // token (Finding 2) is required on every request except `ping`: loopback is not a UID
        // boundary on Android, so the token — not the bind address — keeps other local apps out.
        val dispatcher = Dispatcher(
            methods,
            Capabilities.methodGate(state),
            expectedToken = state.companionToken(),
        ) { line ->
            android.util.Log.i(TRANSPORT_LOG_TAG, line)
        }
        val srv = Server(port = port, dispatcher = dispatcher)
        runCatching { srv.start() }.onSuccess { server = srv }
    }

    // --- notification ---

    private fun startForegroundWithNotification() {
        val notification = if (state.isStoppedFlag()) buildStoppedNotification() else buildRunningNotification()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            startForeground(NOTIF_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
        } else {
            startForeground(NOTIF_ID, notification)
        }
    }

    private fun buildRunningNotification(): Notification =
        baseBuilder()
            .setContentTitle(getString(R.string.notif_running_title))
            .setContentText(getString(R.string.notif_running_text))
            .addAction(0, getString(R.string.notif_action_stop), servicePendingIntent(ACTION_STOP))
            .build()

    private fun buildStoppedNotification(): Notification =
        baseBuilder()
            .setContentTitle(getString(R.string.notif_stopped_title))
            .setContentText(getString(R.string.notif_stopped_text))
            .addAction(0, getString(R.string.notif_action_resume), servicePendingIntent(ACTION_RESUME))
            .build()

    private fun baseBuilder(): NotificationCompat.Builder =
        NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_stop_tile)
            .setContentIntent(settingsPendingIntent())
            .setOngoing(true)            // FLAG_ONGOING_EVENT
            .setAutoCancel(false)        // FLAG_NO_CLEAR
            .setCategory(NotificationCompat.CATEGORY_SERVICE)
            .setVisibility(NotificationCompat.VISIBILITY_PUBLIC)
            .setShowWhen(false)

    private fun servicePendingIntent(action: String): PendingIntent {
        val intent = Intent(this, CompanionForegroundService::class.java).setAction(action)
        return PendingIntent.getService(
            this, action.hashCode(), intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
    }

    private fun settingsPendingIntent(): PendingIntent {
        val intent = packageManager.getLaunchIntentForPackage(packageName)
            ?: Intent().setClassName(packageName, "com.phonectl.companion.ui.SettingsActivity")
        return PendingIntent.getActivity(
            this, 0, intent, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
    }

    private fun createChannel() {
        val mgr = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        val channel = NotificationChannel(
            CHANNEL_ID, getString(R.string.notif_channel_name), NotificationManager.IMPORTANCE_LOW,
        ).apply { description = getString(R.string.notif_channel_desc) }
        mgr.createNotificationChannel(channel)
    }

    companion object {
        const val CHANNEL_ID = "phonectl_automation"
        const val NOTIF_ID = 0x9C7 // arbitrary stable id

        /** Logcat tag for the transport audit line (method + outcome only — never payloads). */
        const val TRANSPORT_LOG_TAG = "phonectl-companion"

        const val ACTION_START = "com.phonectl.companion.action.START"
        const val ACTION_STOP = "com.phonectl.companion.action.STOP"
        const val ACTION_RESUME = "com.phonectl.companion.action.RESUME"
        const val ACTION_REFRESH = "com.phonectl.companion.action.REFRESH"

        /** Start (or refresh) the foreground service with a given action. */
        fun send(context: Context, action: String) {
            val intent = Intent(context, CompanionForegroundService::class.java).setAction(action)
            context.startForegroundService(intent)
        }
    }
}
