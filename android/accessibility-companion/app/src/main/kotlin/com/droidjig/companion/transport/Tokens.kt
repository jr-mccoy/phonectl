package com.droidjig.companion.transport

import java.security.MessageDigest

/**
 * Constant-time comparison for the companion's shared-secret token.
 *
 * On Android, loopback is NOT a UID boundary (Finding 2): any local app with INTERNET
 * permission can open the companion socket and retry indefinitely, with no network jitter
 * to hide behind. Kotlin's `!=` on String delegates to [String.equals], which short-circuits
 * on the first differing character and so leaks a per-character timing signal.
 *
 * [MessageDigest.isEqual] compares the full buffer without early return (and folds the length
 * difference into the same accumulator), which is the JVM's standard secret-comparison
 * primitive.
 */
internal object Tokens {

    /** True when [presented] matches [expected] byte for byte. A null/absent token never matches. */
    fun equal(presented: String?, expected: String): Boolean {
        if (presented == null) return false
        return MessageDigest.isEqual(
            presented.toByteArray(Charsets.UTF_8),
            expected.toByteArray(Charsets.UTF_8),
        )
    }
}
