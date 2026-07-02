package com.phonectl.companion.state

/**
 * The trust/capability state the dispatch handlers read: per-capability enabled toggles, the
 * emergency-stop flag, and the guarded-app list. Pure interface (no Android dependency) so the
 * handlers and their JSON shapes are exercised on the JVM; the on-device implementation
 * (SharedPrefsTrustState) is backed by SharedPreferences + the STOP sentinel file.
 */
interface TrustState {

    /** A capability key (Capabilities.MVP_KEYS) is enabled by the user. Defaults to enabled. */
    fun isCapabilityEnabled(key: String): Boolean

    /**
     * Emergency stop engaged — the in-app flag (authoritative), OR an explicitly configured
     * sentinel file (StopSentinel). Enforced on-device by the dispatcher's stop gate (Finding 3).
     */
    fun isStopped(): Boolean

    /** Packages on which gesture/text actions are refused with `guarded_action` (SPEC §7.6). */
    fun guardedPackages(): Set<String> = emptySet()

    /** The user-enabled map across every advertised capability key (MVP + notifications). */
    fun enabledCapabilityMap(): Map<String, Boolean> =
        Capabilities.ALL_KEYS.associateWith { isCapabilityEnabled(it) }
}
