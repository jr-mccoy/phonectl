package com.phonectl.companion.service

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * LifecycleReceiver auth contract (Finding 14): the exported receiver only honors START_SERVICE /
 * STOP_SERVICE broadcasts that carry the paired companion token — any app can send `am broadcast`,
 * so without this an unprivileged local app could stop the companion (DoS) or start it to widen
 * the attack window.
 */
class LifecycleAuthTest {

    @Test
    fun broadcastWithPairedTokenIsAuthorized() {
        assertTrue(LifecycleAuth.authorized(supplied = "secret", expected = "secret"))
    }

    @Test
    fun unprivilegedBroadcastIsRejected() {
        assertFalse(LifecycleAuth.authorized(supplied = null, expected = "secret"))
        assertFalse(LifecycleAuth.authorized(supplied = "", expected = "secret"))
        assertFalse(LifecycleAuth.authorized(supplied = "wrong", expected = "secret"))
    }

    @Test
    fun failsClosedWithoutAnExpectedToken() {
        // No paired token on record -> nothing can authorize (companionToken() always generates
        // one on-device, so this is a defensive backstop, not a reachable open state).
        assertFalse(LifecycleAuth.authorized(supplied = "anything", expected = null))
        assertFalse(LifecycleAuth.authorized(supplied = "", expected = ""))
    }
}
