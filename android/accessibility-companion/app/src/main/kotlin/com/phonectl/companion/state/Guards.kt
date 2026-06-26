package com.phonectl.companion.state

/**
 * Guarded-app refusal (foreground-service SPEC §7.6): gesture/text actions are refused with the
 * `guarded_action` error code when the current foreground package is on the guarded list. Pure
 * decision — exercised by JVM tests; the service supplies the current package and the configured set.
 */
object ActionGate {
    fun isGuarded(currentPackage: String?, guarded: Set<String>): Boolean =
        currentPackage != null && currentPackage.isNotBlank() && currentPackage in guarded
}
