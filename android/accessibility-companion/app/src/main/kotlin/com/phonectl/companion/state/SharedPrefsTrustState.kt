package com.phonectl.companion.state

import android.content.Context
import android.content.SharedPreferences
import java.io.File
import java.security.SecureRandom

/**
 * On-device [TrustState] backed by SharedPreferences, with STOP-sentinel parity against the
 * `$PHONECTL_HOME/STOP` file.
 *
 * Capability toggles default to enabled (foreground-service SPEC §6). The SharedPreferences flag
 * (set by the Stop notification action / QS tile) is the companion's **authoritative** on-device
 * STOP state — `Capabilities.methodGate` reads it to fail every action closed while stopped
 * (Finding 3), independent of the Python client. The `$PHONECTL_HOME/STOP` sentinel is OR-ed in
 * only as a best-effort extra: it can add a stop but never clear one, and its default
 * `System.getenv("PHONECTL_HOME")` lookup usually resolves to nothing in the APK's own UID (a
 * different UID than Termux) — set an explicit shared path via [setStopFilePath] if you need the
 * file to reach the APK. STOP correctness does not depend on it.
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

    /**
     * The shared-secret token the loopback socket requires on every request (Finding 2).
     * Generated once on first read and persisted; shown in the settings UI so the user can pair
     * it into `phonectl config` (`companion_token`). Loopback is not a UID boundary on Android,
     * so this token — not the bind address — is what keeps other local apps out.
     */
    fun companionToken(): String {
        prefs.getString(KEY_TOKEN, null)?.takeIf { it.isNotBlank() }?.let { return it }
        val token = generateToken()
        prefs.edit().putString(KEY_TOKEN, token).apply()
        return token
    }

    private fun generateToken(): String {
        val bytes = ByteArray(16)
        SecureRandom().nextBytes(bytes)
        return bytes.joinToString("") { "%02x".format(it) }
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
        const val KEY_TOKEN = "companion_token"
        private fun capKey(key: String) = "cap_$key"
    }
}
