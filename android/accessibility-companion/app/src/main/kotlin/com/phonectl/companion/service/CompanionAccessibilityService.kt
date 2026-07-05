package com.phonectl.companion.service

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.Path
import android.graphics.Rect
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.Display
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import android.view.accessibility.AccessibilityWindowInfo
import com.phonectl.companion.accessibility.EventRing
import com.phonectl.companion.accessibility.Generation
import com.phonectl.companion.accessibility.NativeTreeJson
import com.phonectl.companion.accessibility.NodeData
import com.phonectl.companion.accessibility.NodeId
import com.phonectl.companion.accessibility.ObserveFlags
import com.phonectl.companion.accessibility.PasswordGuard
import com.phonectl.companion.accessibility.ScreencapPaths
import com.phonectl.companion.accessibility.SemanticActions
import com.phonectl.companion.accessibility.WindowData
import com.phonectl.companion.state.ActionGate
import com.phonectl.companion.state.TrustState
import com.phonectl.companion.transport.Method
import com.phonectl.companion.transport.MethodException
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicLong
import java.util.concurrent.atomic.AtomicReference

/**
 * The AccessibilityService surface (accessibility-companion SPEC §3): observe_native, gesture,
 * key, set_text, semantic, launch, screencap, events.
 *
 * Per the security note (foreground-service SPEC §9), no request payloads are logged.
 */
class CompanionAccessibilityService : AccessibilityService() {

    private val events = EventRing()

    /**
     * Tree-generation token (Finding 9): bumped on every tree-mutating accessibility event.
     * observe_native returns the current value; set_text/semantic refuse a request whose echoed
     * generation no longer matches, so an action is bound to the observation it was reasoned over.
     */
    private val treeGeneration = AtomicLong(0)

    override fun onServiceConnected() {
        super.onServiceConnected()
        instance = this
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        event ?: return
        if (event.eventType == AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED ||
            event.eventType == AccessibilityEvent.TYPE_WINDOW_CONTENT_CHANGED
        ) {
            treeGeneration.incrementAndGet()
        }
        val type = eventTypeName(event.eventType) ?: return
        val pkg = event.packageName?.toString() ?: ""
        events.add(type, pkg, event.eventTime)
    }

    override fun onInterrupt() {}

    override fun onDestroy() {
        if (instance === this) instance = null
        super.onDestroy()
    }

    // --- observe_native ---

    private fun observeNative(state: TrustState): JSONObject {
        // Guarded protection covers observation, not just actions (Finding 10): refuse reading a
        // guarded foreground app's tree outright, and drop guarded windows (split-screen) from an
        // otherwise-allowed observation.
        requireUnguardedForeground(state, what = "observation")
        val guarded = state.guardedPackages()
        // Capture the generation BEFORE walking: if the tree mutates mid-walk the event bumps the
        // counter and a later action carrying this token is (correctly, conservatively) stale.
        val generation = treeGeneration.get()
        val windows = mutableListOf<WindowData>()
        for (window in safeWindows()) {
            val root = window.root ?: continue
            if (ActionGate.isGuarded(root.packageName?.toString(), guarded)) {
                root.recycle()
                continue
            }
            val nodes = mutableListOf<NodeData>()
            walk(root, window.id, emptyList(), nodes)
            windows.add(
                WindowData(
                    id = window.id,
                    type = windowTypeName(window.type),
                    pkg = root.packageName?.toString() ?: "",
                    nodes = nodes,
                )
            )
            root.recycle()
        }
        val dm = resources.displayMetrics
        // Attach password/payment flags (trust SPEC §7) for the Python policy layer. Additive
        // `flags` key — native_tree.to_compat_xml ignores it.
        return NativeTreeJson.tree(windows, dm.widthPixels, dm.heightPixels)
            .put("flags", ObserveFlags.compute(windows))
            .put("generation", generation)
    }

    private fun safeWindows(): List<AccessibilityWindowInfo> =
        try { windows ?: emptyList() } catch (e: Exception) { emptyList() }

    /** Refuse gesture/text actions in guarded apps (foreground-service SPEC §7.6). */
    private fun requireUnguarded(state: TrustState) = requireUnguardedForeground(state, "actions")

    /**
     * Refuse when the current foreground package is guarded. Shared by the action verbs and — per
     * Finding 10 — by the observation/capture surfaces (observe_native, screencap, ocr_screen):
     * a guarded app must be unreadable, not just untouchable.
     */
    private fun requireUnguardedForeground(state: TrustState, what: String) {
        val pkg = currentPackage()
        if (ActionGate.isGuarded(pkg, state.guardedPackages())) {
            throw MethodException("guarded_action", "$what refused in guarded app '$pkg'")
        }
    }

    /**
     * Refuse `launch` when the *target* package is guarded (foreground-service SPEC §7.6). Unlike
     * [requireUnguarded], the guarded package here is the one the request asks to open, not the
     * current foreground app — launching a guarded app is itself an action we refuse.
     */
    private fun requireUnguardedTarget(params: JSONObject, state: TrustState) {
        val pkg = params.optString("package", "")
        if (ActionGate.isGuarded(pkg, state.guardedPackages())) {
            throw MethodException("guarded_action", "launch refused for guarded app '$pkg'")
        }
    }

    private fun currentPackage(): String? {
        val root = rootInActiveWindow ?: return null
        val pkg = root.packageName?.toString()
        root.recycle()
        return pkg
    }

    private fun walk(node: AccessibilityNodeInfo, windowId: Int, path: List<Int>, out: MutableList<NodeData>) {
        val data = serialize(node, windowId, path)
        if (data != null) out.add(data)
        for (i in 0 until node.childCount) {
            val child = node.getChild(i) ?: continue
            walk(child, windowId, path + i, out)
            child.recycle()
        }
    }

    private fun serialize(node: AccessibilityNodeInfo, windowId: Int, path: List<Int>): NodeData? {
        val actions = nodeActionStrings(node)
        val text = node.text?.toString() ?: ""
        val contentDesc = node.contentDescription?.toString() ?: ""
        // Omit purely structural nodes with nothing useful (SPEC allows it). Keep id paths stable
        // because the structural child-index path is computed regardless of omission.
        val interesting = text.isNotEmpty() || contentDesc.isNotEmpty() ||
            node.isClickable || node.isScrollable || node.isEditable || actions.isNotEmpty()
        if (!interesting) return null

        val password = PasswordGuard.isPassword(node.inputType, node.isPassword)
        val rect = Rect().also { node.getBoundsInScreen(it) }
        return NodeData(
            nodeId = NodeId.resolve(node.viewIdResourceName, windowId, path),
            text = if (password) "" else text, // password guard: never expose typed text
            className = node.className?.toString() ?: "",
            contentDesc = contentDesc,
            bounds = listOf(rect.left, rect.top, rect.right, rect.bottom),
            resourceId = node.viewIdResourceName ?: "",
            actions = actions,
            checkable = node.isCheckable,
            checked = node.isChecked,
            clickable = node.isClickable,
            enabled = node.isEnabled,
            focused = node.isFocused,
            scrollable = node.isScrollable,
            password = password,
        )
    }

    // --- gesture ---

    private fun gesture(params: JSONObject) {
        val path = Path()
        val durationMs: Long
        when (params.optString("type")) {
            "tap" -> {
                val x = params.getInt("x").toFloat()
                val y = params.getInt("y").toFloat()
                path.moveTo(x, y)
                durationMs = 50L
            }
            "swipe" -> {
                path.moveTo(params.getInt("x1").toFloat(), params.getInt("y1").toFloat())
                path.lineTo(params.getInt("x2").toFloat(), params.getInt("y2").toFloat())
                durationMs = params.optInt("ms", 200).toLong()
            }
            else -> throw MethodException("gesture_rejected", "unknown gesture type")
        }
        val stroke = GestureDescription.StrokeDescription(path, 0, durationMs)
        val description = GestureDescription.Builder().addStroke(stroke).build()
        if (!dispatchGestureBlocking(description)) {
            throw MethodException("gesture_rejected", "gesture dispatch failed")
        }
    }

    private fun dispatchGestureBlocking(description: GestureDescription): Boolean {
        val latch = CountDownLatch(1)
        val completed = AtomicBoolean(false)
        val callback = object : AccessibilityService.GestureResultCallback() {
            override fun onCompleted(gestureDescription: GestureDescription?) {
                completed.set(true); latch.countDown()
            }
            override fun onCancelled(gestureDescription: GestureDescription?) {
                completed.set(false); latch.countDown()
            }
        }
        val dispatched = dispatchGesture(description, callback, Handler(Looper.getMainLooper()))
        if (!dispatched) return false
        latch.await(5, TimeUnit.SECONDS)
        return completed.get()
    }

    // --- key ---

    private fun key(params: JSONObject) {
        val keycode = params.opt("keycode")?.toString() ?: ""
        val action = GLOBAL_KEYS[keycode.removePrefix("KEYCODE_").uppercase()]
            ?: throw MethodException("unsupported_action", "unsupported key '$keycode'")
        if (!performGlobalAction(action)) {
            throw MethodException("gesture_rejected", "global action rejected")
        }
    }

    // --- set_text ---

    private fun setText(params: JSONObject) {
        Generation.requireFresh(Generation.requested(params), treeGeneration.get())
        val text = params.optString("text", "")
        val node = when (params.optString("mode", "set")) {
            "type" -> findFocusedEditable()
                ?: throw MethodException("node_not_found", "no focused editable node")
            else -> {
                val nodeId = params.optString("node_id", "")
                findById(nodeId) ?: throw MethodException("node_not_found", "node '$nodeId' not found")
            }
        }
        val args = Bundle().apply {
            putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text)
        }
        val ok = node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)
        node.recycle()
        if (!ok) throw MethodException("unsupported_action", "ACTION_SET_TEXT not supported")
    }

    private fun findFocusedEditable(): AccessibilityNodeInfo? {
        for (window in safeWindows()) {
            val root = window.root ?: continue
            val focused = root.findFocus(AccessibilityNodeInfo.FOCUS_INPUT)
            root.recycle()
            if (focused != null && focused.isEditable) return focused
        }
        return null
    }

    // --- semantic ---

    private fun semantic(params: JSONObject): JSONObject {
        Generation.requireFresh(Generation.requested(params), treeGeneration.get())
        val action = params.optString("action", "")
        if (!SemanticActions.isSupported(action)) {
            throw MethodException("unsupported_action", "unknown semantic action '$action'")
        }
        val nodeId = params.optString("node_id", "")
        val node = findById(nodeId) ?: throw MethodException("node_not_found", "node '$nodeId' not found")
        val constant = SEMANTIC_TO_ACTION[action]!!
        val available = node.actionList.any { it.id == constant }
        if (!available) {
            node.recycle()
            throw MethodException("unsupported_action", "node does not support '$action'")
        }
        val ok = node.performAction(constant)
        node.recycle()
        if (!ok) throw MethodException("unsupported_action", "action '$action' rejected")
        return JSONObject().put("performed", action)
    }

    // --- launch ---

    private fun launch(params: JSONObject) {
        val pkg = params.optString("package", "")
        val intent = (packageManager.getLaunchIntentForPackage(pkg)
            ?: Intent(Intent.ACTION_MAIN).setPackage(pkg).addCategory(Intent.CATEGORY_LAUNCHER))
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        try {
            startActivity(intent)
        } catch (e: Exception) {
            throw MethodException("node_not_found", "cannot launch '$pkg'")
        }
    }

    // --- screencap / screenshot-backed OCR ---

    private fun captureScreenshotBitmap(): Bitmap {
        val latch = CountDownLatch(1)
        val bitmapRef = AtomicReference<Bitmap?>(null)
        takeScreenshot(
            Display.DEFAULT_DISPLAY,
            { it.run() },
            object : AccessibilityService.TakeScreenshotCallback {
                override fun onSuccess(result: AccessibilityService.ScreenshotResult) {
                    try {
                        val bitmap = Bitmap.wrapHardwareBuffer(result.hardwareBuffer, result.colorSpace)
                        bitmapRef.set(bitmap?.copy(Bitmap.Config.ARGB_8888, false))
                    } finally {
                        result.hardwareBuffer.close()
                        latch.countDown()
                    }
                }

                override fun onFailure(errorCode: Int) {
                    latch.countDown()
                }
            },
        )
        latch.await(5, TimeUnit.SECONDS)
        return bitmapRef.get() ?: throw MethodException("screencap_unavailable", "screenshot failed")
    }

    private fun ocrScreen(state: TrustState): JSONObject {
        // The screen's pixels include the guarded app's content — refuse like observe (Finding 10).
        requireUnguardedForeground(state, what = "screen OCR")
        return OcrHandler.ocrBitmap(captureScreenshotBitmap())
    }

    /**
     * `screenshot` — the PNG as base64 over the socket. Nothing touches disk on this side
     * (Finding 16 stays intact: the companion never writes outside its own storage); the
     * Python caller decodes and persists the bytes under ITS storage, across the UID boundary
     * the old path-based screencap could never cross. java.util.Base64 keeps it JVM-testable.
     */
    private fun screenshot(state: TrustState): JSONObject {
        requireUnguardedForeground(state, what = "screenshot")
        val bitmap = captureScreenshotBitmap()
        val baos = java.io.ByteArrayOutputStream()
        if (!bitmap.compress(Bitmap.CompressFormat.PNG, 100, baos)) {
            throw MethodException("screencap_unavailable", "PNG encode failed")
        }
        return JSONObject()
            .put("format", "png")
            .put("data", java.util.Base64.getEncoder().encodeToString(baos.toByteArray()))
    }

    /**
     * Writable screenshot roots (Finding 16): the companion's own files/cache dirs (internal and
     * app-specific external). Canonicalized so the pure prefix check cannot be tricked.
     */
    private fun screencapRoots(): List<String> =
        listOfNotNull(filesDir, cacheDir, getExternalFilesDir(null), externalCacheDir)
            .mapNotNull { runCatching { it.canonicalPath }.getOrNull() }

    private fun screencap(params: JSONObject, state: TrustState): JSONObject {
        requireUnguardedForeground(state, what = "screencap")
        val requested = params.optString("path", "")
        if (requested.isBlank()) throw MethodException("screencap_unavailable", "no path")
        val path = runCatching { File(requested).canonicalPath }.getOrNull()
            ?: throw MethodException("path_rejected", "cannot resolve the requested path")
        if (!ScreencapPaths.isAllowed(path, screencapRoots())) {
            throw MethodException(
                "path_rejected",
                "screencap writes only under the companion's own storage",
            )
        }
        val latch = CountDownLatch(1)
        val ok = AtomicBoolean(false)
        takeScreenshot(
            Display.DEFAULT_DISPLAY,
            { it.run() },
            object : AccessibilityService.TakeScreenshotCallback {
                override fun onSuccess(result: AccessibilityService.ScreenshotResult) {
                    try {
                        val bitmap = Bitmap.wrapHardwareBuffer(result.hardwareBuffer, result.colorSpace)
                            ?: return
                        FileOutputStream(File(path)).use { out ->
                            bitmap.copy(Bitmap.Config.ARGB_8888, false)
                                .compress(Bitmap.CompressFormat.PNG, 100, out)
                        }
                        ok.set(true)
                    } catch (e: Exception) {
                        ok.set(false)
                    } finally {
                        result.hardwareBuffer.close()
                        latch.countDown()
                    }
                }

                override fun onFailure(errorCode: Int) {
                    latch.countDown()
                }
            },
        )
        latch.await(5, TimeUnit.SECONDS)
        if (!ok.get()) throw MethodException("screencap_unavailable", "screenshot failed")
        return JSONObject().put("path", path)
    }

    // --- shared node lookup ---

    /**
     * Resolve a node id across all windows, refusing ambiguity (Finding 9): resource ids are not
     * unique (list rows share them), and silently acting on the first match targets the wrong
     * node. Two or more matches raise `ambiguous_node_id`; zero returns null (caller raises
     * `node_not_found`).
     */
    private fun findById(nodeId: String): AccessibilityNodeInfo? {
        if (nodeId.isBlank()) return null
        val matches = mutableListOf<AccessibilityNodeInfo>()
        for (window in safeWindows()) {
            val root = window.root ?: continue
            collectById(root, window.id, emptyList(), nodeId, matches)
            root.recycle()
        }
        if (matches.size > 1) {
            matches.forEach { it.recycle() }
            NodeId.requireUnambiguous(matches.size, nodeId) // throws ambiguous_node_id
        }
        return matches.firstOrNull()
    }

    private fun collectById(
        node: AccessibilityNodeInfo, windowId: Int, path: List<Int>, target: String,
        out: MutableList<AccessibilityNodeInfo>,
    ) {
        if (NodeId.resolve(node.viewIdResourceName, windowId, path) == target) {
            out.add(AccessibilityNodeInfo.obtain(node))
        }
        for (i in 0 until node.childCount) {
            val child = node.getChild(i) ?: continue
            collectById(child, windowId, path + i, target, out)
            child.recycle()
        }
    }

    private fun nodeActionStrings(node: AccessibilityNodeInfo): List<String> =
        node.actionList.mapNotNull { ACTION_TO_NAME[it.id] }

    companion object {
        @Volatile
        var instance: CompanionAccessibilityService? = null
            private set

        /**
         * The AccessibilityService-backed method handlers, plugged into the foreground service's
         * dispatcher. Each handler resolves [instance] lazily so it works regardless of service
         * start order; if the service is not connected it returns a handler_error envelope.
         */
        fun methods(state: TrustState): Map<String, Method> {
            fun svc(): CompanionAccessibilityService =
                instance ?: throw IllegalStateException("accessibility service not connected")
            return mapOf(
                "observe_native" to { _ -> svc().observeNative(state) },
                "gesture" to { p -> svc().run { requireUnguarded(state); gesture(p) }; JSONObject().put("applied", true) },
                "key" to { p -> svc().run { requireUnguarded(state); key(p) }; JSONObject().put("applied", true) },
                "set_text" to { p -> svc().run { requireUnguarded(state); setText(p) }; JSONObject().put("applied", true) },
                "semantic" to { p -> svc().run { requireUnguarded(state); semantic(p) } },
                "launch" to { p -> svc().run { requireUnguardedTarget(p, state); launch(p) }; JSONObject().put("launched", true) },
                "screencap" to { p -> svc().screencap(p, state) },
                "screenshot" to { _ -> svc().screenshot(state) },
                "ocr_screen" to { _ -> svc().ocrScreen(state) },
                // Guarded packages are filtered out of the event stream (Finding 10).
                "events" to { p ->
                    svc().events.queryJson(
                        p.optLong("since", 0), p.optInt("max", 50),
                        excludePackages = state.guardedPackages(),
                    )
                },
            )
        }

        private val GLOBAL_KEYS = mapOf(
            "HOME" to AccessibilityService.GLOBAL_ACTION_HOME,
            "BACK" to AccessibilityService.GLOBAL_ACTION_BACK,
            "RECENTS" to AccessibilityService.GLOBAL_ACTION_RECENTS,
            "APP_SWITCH" to AccessibilityService.GLOBAL_ACTION_RECENTS,
            "NOTIFICATIONS" to AccessibilityService.GLOBAL_ACTION_NOTIFICATIONS,
            "QUICK_SETTINGS" to AccessibilityService.GLOBAL_ACTION_QUICK_SETTINGS,
        )

        private val SEMANTIC_TO_ACTION = mapOf(
            "click" to AccessibilityNodeInfo.ACTION_CLICK,
            "long_click" to AccessibilityNodeInfo.ACTION_LONG_CLICK,
            "scroll_forward" to AccessibilityNodeInfo.ACTION_SCROLL_FORWARD,
            "scroll_backward" to AccessibilityNodeInfo.ACTION_SCROLL_BACKWARD,
            "expand" to AccessibilityNodeInfo.ACTION_EXPAND,
            "collapse" to AccessibilityNodeInfo.ACTION_COLLAPSE,
            "dismiss" to AccessibilityNodeInfo.ACTION_DISMISS,
        )

        // Reverse map for advertising a node's available actions (plus set_text).
        private val ACTION_TO_NAME: Map<Int, String> =
            SEMANTIC_TO_ACTION.entries.associate { (k, v) -> v to k } +
                mapOf(AccessibilityNodeInfo.ACTION_SET_TEXT to "set_text")

        private fun eventTypeName(eventType: Int): String? = when (eventType) {
            AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED -> "window_state_changed"
            AccessibilityEvent.TYPE_WINDOW_CONTENT_CHANGED -> "content_changed"
            AccessibilityEvent.TYPE_VIEW_FOCUSED -> "view_focused"
            AccessibilityEvent.TYPE_VIEW_CLICKED -> "view_clicked"
            AccessibilityEvent.TYPE_NOTIFICATION_STATE_CHANGED -> "notification"
            else -> null
        }

        private fun windowTypeName(type: Int): String = when (type) {
            AccessibilityWindowInfo.TYPE_APPLICATION -> "application"
            AccessibilityWindowInfo.TYPE_INPUT_METHOD -> "ime"
            AccessibilityWindowInfo.TYPE_ACCESSIBILITY_OVERLAY -> "accessibility_overlay"
            else -> "system"
        }
    }
}
