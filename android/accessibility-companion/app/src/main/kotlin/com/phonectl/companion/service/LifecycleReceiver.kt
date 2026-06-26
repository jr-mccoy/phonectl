package com.phonectl.companion.service

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/**
 * Lifecycle seam (foreground-service SPEC §8): the Phase-5 daemon starts/stops the foreground
 * service via `adb shell am broadcast`. This receiver only translates those broadcasts into
 * service start/stop — no daemon wiring, no reconnect/watchdog policy (the daemon owns that).
 *
 * Distinct from the `stopped` emergency flag: START_SERVICE/STOP_SERVICE manage the service
 * process lifecycle, whereas the Stop notification/tile only flip `stopped`.
 */
class LifecycleReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        when (intent.action) {
            ACTION_START -> CompanionForegroundService.send(context, CompanionForegroundService.ACTION_START)
            ACTION_STOP -> context.stopService(Intent(context, CompanionForegroundService::class.java))
        }
    }

    companion object {
        const val ACTION_START = "com.phonectl.companion.action.START_SERVICE"
        const val ACTION_STOP = "com.phonectl.companion.action.STOP_SERVICE"
    }
}
