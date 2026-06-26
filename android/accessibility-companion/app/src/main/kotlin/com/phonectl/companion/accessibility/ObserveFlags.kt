package com.phonectl.companion.accessibility

import org.json.JSONObject

/**
 * Password/payment screen flags attached to observe_native (trust SPEC §7.1/§7.5) for the Python
 * `policy` layer to deny or confirm-gate actions. Additive top-level `flags` object — ignored by
 * native_tree.to_compat_xml, available to policy consumers.
 *
 * Pure — exercised by JVM tests.
 */
object ObserveFlags {

    // Known payment / wallet packages (heuristic allowlist).
    val PAYMENT_PACKAGES: Set<String> = setOf(
        "com.google.android.apps.walletnfcrel",
        "com.google.android.gms", // Google Pay sheet host
        "com.paypal.android.p2pmobile",
        "com.squareup.cash",
        "com.venmo",
    )

    // Window-title / on-screen text heuristics.
    val PAYMENT_KEYWORDS: List<String> = listOf(
        "payment", "checkout", "card number", "cvv", "credit card", "pay now", "billing",
    )

    fun passwordPresent(windows: List<WindowData>): Boolean =
        windows.any { w -> w.nodes.any { it.password } }

    fun paymentSuspected(windows: List<WindowData>): Boolean {
        if (windows.any { it.pkg in PAYMENT_PACKAGES }) return true
        return windows.any { w ->
            w.nodes.any { n ->
                val haystack = (n.text + " " + n.contentDesc).lowercase()
                PAYMENT_KEYWORDS.any { haystack.contains(it) }
            }
        }
    }

    fun compute(windows: List<WindowData>): JSONObject =
        JSONObject()
            .put("password_present", passwordPresent(windows))
            .put("payment_suspected", paymentSuspected(windows))
}
