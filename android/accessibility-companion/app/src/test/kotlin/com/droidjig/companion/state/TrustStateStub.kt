package com.droidjig.companion.state

/** Shared in-memory [TrustState] for JVM tests (no SharedPreferences). */
class TrustStateStub(
    private val disabled: Set<String> = emptySet(),
    private val stopped: Boolean = false,
    private val guarded: Set<String> = emptySet(),
) : TrustState {
    override fun isCapabilityEnabled(key: String): Boolean = key !in disabled
    override fun isStopped(): Boolean = stopped
    override fun guardedPackages(): Set<String> = guarded
}
