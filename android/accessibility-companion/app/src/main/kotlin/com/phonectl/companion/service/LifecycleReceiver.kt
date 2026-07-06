package com.phonectl.companion.service

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import com.phonectl.companion.state.SharedPrefsTrustState

/**
 * Lifecycle seam (foreground-service SPEC §8): the Phase-5 daemon starts/stops the foreground
 * service via `adb shell am broadcast … --es token <paired-token>`. This receiver only translates
 * those broadcasts into service start/stop — no daemon wiring, no reconnect/watchdog policy (the
 * daemon owns that).
 *
 * The receiver is exported (so `am broadcast` can reach it), which on Android means ANY installed
 * app can send these actions. Finding 14: broadcasts must therefore carry the paired companion
 * token (the same shared secret the loopback socket requires); unauthenticated broadcasts are
 * silently ignored. A signature permission is not an option here — the daemon's `adb shell` runs
 * as the shell UID, which can never hold a custom signature permission.
 *
 * Distinct from the `stopped` emergency flag: START_SERVICE/STOP_SERVICE manage the service
 * process lifecycle, whereas the Stop notification/tile only flip `stopped`.
 */
class LifecycleReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val state = SharedPrefsTrustState(context)
        val supplied = intent.getStringExtra(EXTRA_TOKEN)

        // SET_TOKEN uses trust-on-first-use: adopt a phonectl-minted token ONLY when none is set
        // yet, checked WITHOUT minting one (hasToken, not companionToken which generates-on-read).
        // Once a token exists it is never overwritten by a broadcast (pushed-token v2 design note).
        if (intent.action == ACTION_SET_TOKEN) {
            if (LifecycleAuth.authorizedFirstPair(supplied, state.hasToken())) {
                state.setToken(supplied!!)
            }
            return
        }

        // START/STOP require the already-paired token (Finding 14). companionToken() here also
        // establishes a token on a never-paired companion, keeping the pre-v2 behavior intact.
        if (!LifecycleAuth.authorized(supplied, state.companionToken())) return
        when (intent.action) {
            ACTION_START -> CompanionForegroundService.send(context, CompanionForegroundService.ACTION_START)
            ACTION_STOP -> context.stopService(Intent(context, CompanionForegroundService::class.java))
        }
    }

    companion object {
        const val ACTION_START = "com.phonectl.companion.action.START_SERVICE"
        const val ACTION_STOP = "com.phonectl.companion.action.STOP_SERVICE"

        /** Trust-on-first-use pairing (pushed-token v2): adopt a minted token when none is set. */
        const val ACTION_SET_TOKEN = "com.phonectl.companion.action.SET_TOKEN"

        /** String extra carrying the paired companion token (`--es token <value>`). */
        const val EXTRA_TOKEN = "token"
    }
}
