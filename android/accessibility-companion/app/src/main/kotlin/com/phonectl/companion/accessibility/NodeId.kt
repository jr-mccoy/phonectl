package com.phonectl.companion.accessibility

/**
 * Deterministic, re-resolvable node ids (accessibility-companion SPEC + Plan 4.5 Task 5):
 *
 * - Prefer the stable `viewIdResourceName` (enabled by flagReportViewIds).
 * - For nodes without one, assign a path-based id (window id + child-index path) so the same
 *   node yields the same id while the tree is unchanged, letting set_text/semantic re-find it on
 *   a later request.
 *
 * Pure — exercised by JVM tests.
 */
object NodeId {

    fun pathId(windowId: Int, childPath: List<Int>): String =
        buildString {
            append("w").append(windowId)
            for (index in childPath) append("/").append(index)
        }

    fun resolve(viewIdResourceName: String?, windowId: Int, childPath: List<Int>): String =
        viewIdResourceName?.takeIf { it.isNotBlank() } ?: pathId(windowId, childPath)

    /**
     * Refuse ambiguous lookups (Finding 9): `viewIdResourceName` is not unique (list rows share
     * ids), and acting on "the first match" silently targets the wrong node. Zero matches is not
     * an error here — the caller raises `node_not_found` for that case.
     */
    fun requireUnambiguous(matchCount: Int, nodeId: String) {
        if (matchCount > 1) {
            throw com.phonectl.companion.transport.MethodException(
                "ambiguous_node_id",
                "node id '$nodeId' matches $matchCount nodes — refine the target and re-observe",
            )
        }
    }
}

/**
 * Password-field detection (trust SPEC §7.1): a node is a password field if AccessibilityNodeInfo
 * reports it as a password, or its inputType carries a password variation. Such nodes set
 * `password:true` and NEVER include their `text`.
 *
 * InputType masks are stable platform constants (android.text.InputType); duplicated here as
 * literals to keep this helper pure and JVM-testable.
 */
object PasswordGuard {
    private const val TYPE_MASK_CLASS = 0x0000000f
    private const val TYPE_MASK_VARIATION = 0x00000ff0

    private const val TYPE_CLASS_TEXT = 0x00000001
    private const val TYPE_CLASS_NUMBER = 0x00000002

    private const val TYPE_TEXT_VARIATION_PASSWORD = 0x00000080
    private const val TYPE_TEXT_VARIATION_WEB_PASSWORD = 0x000000e0
    private const val TYPE_NUMBER_VARIATION_PASSWORD = 0x00000010

    fun isPassword(inputType: Int, nodeIsPassword: Boolean): Boolean {
        if (nodeIsPassword) return true
        val klass = inputType and TYPE_MASK_CLASS
        val variation = inputType and TYPE_MASK_VARIATION
        return when (klass) {
            TYPE_CLASS_TEXT ->
                variation == TYPE_TEXT_VARIATION_PASSWORD ||
                    variation == TYPE_TEXT_VARIATION_WEB_PASSWORD
            TYPE_CLASS_NUMBER -> variation == TYPE_NUMBER_VARIATION_PASSWORD
            else -> false
        }
    }
}

/**
 * The semantic-action vocabulary (accessibility-companion SPEC §semantic). The string -> Android
 * AccessibilityAction constant mapping lives in the service (it needs the Android constants); the
 * pure set is used to validate requests and to advertise per-node `actions`.
 */
object SemanticActions {
    val SUPPORTED: Set<String> = setOf(
        "click", "long_click", "scroll_forward", "scroll_backward", "expand", "collapse", "dismiss",
    )

    fun isSupported(action: String): Boolean = action in SUPPORTED
}
