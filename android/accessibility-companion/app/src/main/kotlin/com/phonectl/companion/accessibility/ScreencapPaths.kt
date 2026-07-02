package com.phonectl.companion.accessibility

/**
 * Screencap output-path constraint (Finding 16): the companion writes screenshots only under its
 * own app-owned storage roots (filesDir/cacheDir/external app dirs), never to an arbitrary
 * client-supplied location. The service canonicalizes the requested path first; this pure check
 * then requires it to sit under one of the canonical allowed roots (string-prefix tricks like a
 * `files-evil/` sibling do not pass). JVM-tested.
 */
object ScreencapPaths {

    fun isAllowed(canonicalPath: String, allowedRoots: List<String>): Boolean {
        if (canonicalPath.isBlank() || !canonicalPath.startsWith("/")) return false
        return allowedRoots.any { root ->
            val r = root.trimEnd('/')
            r.isNotEmpty() && (canonicalPath == r || canonicalPath.startsWith("$r/"))
        }
    }
}
