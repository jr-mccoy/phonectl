package com.phonectl.companion.service

import android.app.Service
import android.content.Intent
import android.os.IBinder

/** Foreground service hosting the loopback server + Stop notification. Built out in Tasks 2/4. */
class CompanionForegroundService : Service() {
    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        return START_STICKY
    }
}
