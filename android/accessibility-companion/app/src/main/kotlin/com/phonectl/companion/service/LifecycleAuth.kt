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
}
