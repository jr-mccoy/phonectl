package com.phonectl.companion.state

import android.content.Context
import android.content.SharedPreferences
import java.io.File

/**
 * On-device [TrustState] backed by SharedPreferences, with STOP-sentinel parity against the
 * `$PHONECTL_HOME/STOP` file.
 *
 * Capability toggles default to enabled (foreground-service SPEC §6). The stop state is true if
 * either the in-app flag is set or the sentinel file exists (best-effort: the APK can only read
 * the file when its path is reachable from the app sandbox; the Python side checks its own copy
 * regardless, so this is the low-latency mirror, not the sole guarantee).
 */
class SharedPrefsTrustState(context: Context) : TrustState {

    private val prefs: SharedPreferences =
        context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    override fun isCapabilityEnabled(key: String): Boolean =
        prefs.getBoolean(capKey(key), Capabilities.DEFAULT_ENABLED)

    fun setCapabilityEnabled(key: String, enabled: Boolean) {
        prefs.edit().putBoolean(capKey(key), enabled).apply()
    }

    override fun isStopped(): Boolean =
        StopSentinel.stopped(inAppFlag = prefs.getBoolean(KEY_STOPPED, false),
            stopFileExists = stopFileExists())

    /** The in-app flag only — used by the UI to distinguish the in-app state from the file. */
    fun isStoppedFlag(): Boolean = prefs.getBoolean(KEY_STOPPED, false)

    fun setStopped(stopped: Boolean) {
        prefs.edit().putBoolean(KEY_STOPPED, stopped).apply()
    }

    override fun guardedPackages(): Set<String> =
        prefs.getStringSet(KEY_GUARDED, emptySet()) ?: emptySet()

    fun setGuardedPackages(packages: Set<String>) {
        prefs.edit().putStringSet(KEY_GUARDED, packages).apply()
    }

    /** Optional override for the STOP sentinel path; empty falls back to env/default discovery. */
    fun setStopFilePath(path: String) {
        prefs.edit().putString(KEY_STOP_FILE, path).apply()
    }

    private fun stopFileExists(): Boolean {
        val path = resolveStopFilePath() ?: return false
        return runCatching { File(path).exists() }.getOrDefault(false)
    }

    private fun resolveStopFilePath(): String? {
        prefs.getString(KEY_STOP_FILE, null)?.takeIf { it.isNotBlank() }?.let { return it }
        System.getenv("PHONECTL_HOME")?.takeIf { it.isNotBlank() }?.let { return "$it/STOP" }
        return null
    }

    companion object {
        const val PREFS = "phonectl_companion"
        const val KEY_STOPPED = "stopped"
        const val KEY_GUARDED = "guarded_packages"
        const val KEY_STOP_FILE = "stop_file_path"
        private fun capKey(key: String) = "cap_$key"
    }
}
