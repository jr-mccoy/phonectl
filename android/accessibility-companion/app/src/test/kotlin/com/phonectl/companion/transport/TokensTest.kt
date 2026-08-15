package com.phonectl.companion.transport

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Behavioral contract for the constant-time token comparator (audit D3). The timing property
 * itself is structural — it comes from [java.security.MessageDigest.isEqual], which compares the
 * whole buffer instead of short-circuiting — so what is pinned here is that swapping in that
 * primitive did not change which tokens are accepted.
 */
class TokensTest {

    @Test
    fun matchingTokenIsEqual() {
        assertTrue(Tokens.equal("s3cret-token", "s3cret-token"))
    }

    @Test
    fun differingTokenIsNotEqual() {
        assertFalse(Tokens.equal("s3cret-tokeN", "s3cret-token"))
    }

    @Test
    fun prefixIsNotEqual() {
        // Length must not be mistaken for a match — the old `!=` and the new comparator agree.
        assertFalse(Tokens.equal("s3cret", "s3cret-token"))
    }

    @Test
    fun longerCandidateIsNotEqual() {
        assertFalse(Tokens.equal("s3cret-token-plus", "s3cret-token"))
    }

    @Test
    fun nullTokenIsNotEqual() {
        assertFalse(Tokens.equal(null, "s3cret-token"))
    }

    @Test
    fun emptyTokenIsNotEqual() {
        // `optString("token", "")` yields "" for an absent field; it must never match.
        assertFalse(Tokens.equal("", "s3cret-token"))
    }

    @Test
    fun nonAsciiTokenIsNotEqual() {
        assertFalse(Tokens.equal("tökén", "s3cret-token"))
    }
}
