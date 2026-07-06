package com.phonectl.companion.service

/**
 * Authorization decision for the exported [LifecycleReceiver] (Finding 14): a broadcast is only
 * honored when it carries the paired companion token — the same shared secret the loopback socket
 * requires — because `exported="true"` means ANY installed app (or `adb shell am broadcast`) can
 * reach the receiver. Fail-closed: no expected token means nothing authorizes.
 *
 * Pure (no Android dependency) — exercised by JVM tests.
 */
object LifecycleAuth {
    fun authorized(supplied: String?, expected: String?): Boolean =
        !expected.isNullOrBlank() && supplied == expected

    /**
     * Trust-on-first-use adoption decision for pushed-token v2 (SET_TOKEN): adopt a phonectl-minted
     * token ONLY when it is non-blank AND no token is set yet. Load-bearing invariant — once a token
     * exists, an unauthenticated broadcast can never overwrite it (a malicious local app must not be
     * able to re-key the companion). See docs/superpowers/specs/2026-07-06-pushed-token-v2-design.md.
     */
    fun authorizedFirstPair(supplied: String?, hasExistingToken: Boolean): Boolean =
        !supplied.isNullOrBlank() && !hasExistingToken
}
