package com.droidjig.companion.state

import android.content.Context
import android.content.SharedPreferences
import java.io.File
import java.security.SecureRandom

/**
 * On-device [TrustState] backed by SharedPreferences.
 *
 * The SharedPreferences `stopped` flag is the companion's AUTHORITATIVE stop state (Finding 3):
 * the APK runs under its own Android UID and can never read Termux's `$PHONECTL_HOME/STOP`
 * sentinel, so no env-based file fallback is attempted — pretending otherwise made the "hard
 * guarantee" silently inert. The Python side keeps checking its own STOP file; the two stops are
 * independent and EITHER halts actions (Python via kill_switch_active, companion via the
 * dispatcher's stop gate). An explicit sentinel path can still be configured via
 * [setStopFilePath] for setups where a shared, app-readable location exists; it widens the stop
 * (OR), never narrows it.
 *
 * Capability toggles default per [Capabilities.defaultFor]: sensitive caps ship disabled
 * (safe-by-default, Finding 5), the rest enabled.
 */
class SharedPrefsTrustState(context: Context) : TrustState {

    private val prefs: SharedPreferences =
        context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    override fun isCapabilityEnabled(key: String): Boolean =
        prefs.getBoolean(capKey(key), Capabilities.defaultFor(key))

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

    /**
     * Optional explicit STOP sentinel path (empty disables the file check). No env-based
     * discovery: `System.getenv("PHONECTL_HOME")` is Termux's variable, never the APK's, so a
     * fallback through it could only ever return null while implying file-level parity existed.
     */
    fun setStopFilePath(path: String) {
        prefs.edit().putString(KEY_STOP_FILE, path).apply()
    }

    /**
     * The shared-secret token the loopback socket requires on every request (Finding 2).
     * Generated once on first read and persisted; shown in the settings UI so the user can pair
     * it into `droidjig config` (`companion_token`). Loopback is not a UID boundary on Android,
     * so this token — not the bind address — is what keeps other local apps out.
     */
    fun companionToken(): String {
        prefs.getString(KEY_TOKEN, null)?.takeIf { it.isNotBlank() }?.let { return it }
        val token = generateToken()
        prefs.edit().putString(KEY_TOKEN, token).apply()
        return token
    }

    /**
     * Whether a token is already set, WITHOUT minting one. [companionToken] generates-on-read, so
     * the trust-on-first-use pairing path ([LifecycleReceiver] SET_TOKEN) must use this to decide
     * adoption — reading [companionToken] would itself create a token and close the TOFU window.
     */
    fun hasToken(): Boolean =
        !prefs.getString(KEY_TOKEN, null).isNullOrBlank()

    /**
     * Adopt a droidjig-minted token at first pair (pushed-token v2). Caller MUST have checked
     * [hasToken] is false — this never guards against overwrite itself; the guard lives in
     * [LifecycleReceiver] via [com.droidjig.companion.service.LifecycleAuth.authorizedFirstPair].
     */
    fun setToken(token: String) {
        prefs.edit().putString(KEY_TOKEN, token).apply()
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

    private fun resolveStopFilePath(): String? =
        prefs.getString(KEY_STOP_FILE, null)?.takeIf { it.isNotBlank() }

    companion object {
        const val PREFS = "droidjig_companion"
        const val KEY_STOPPED = "stopped"
        const val KEY_GUARDED = "guarded_packages"
        const val KEY_STOP_FILE = "stop_file_path"
        const val KEY_TOKEN = "companion_token"
        private fun capKey(key: String) = "cap_$key"
    }
}
