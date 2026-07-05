package com.phonectl.companion.accessibility

import com.phonectl.companion.json.Json
import org.json.JSONArray
import org.json.JSONObject

/**
 * Pure representation + serialization of the native UI tree. The Android tree walk
 * (CompanionAccessibilityService) flattens AccessibilityNodeInfo/Window into these data classes;
 * this file produces the exact JSON `native_tree.to_compat_xml` consumes:
 *
 *   {windows:[{id, type, package, nodes:[<node>]}], screen:{width, height}}
 *
 * Node shape (accessibility-companion SPEC §observe_native):
 *   {node_id, text, class, content_desc, bounds:[l,t,r,b], actions:[...],
 *    checkable, checked, clickable, enabled, focused, scrollable, password}
 *
 * No Android dependency — exercised by JVM contract tests against tests/test_native_tree.py NATIVE.
 */

data class NodeData(
    val nodeId: String,
    val text: String,
    val className: String,
    val contentDesc: String,
    val bounds: List<Int>, // [left, top, right, bottom]
    val resourceId: String = "", // viewIdResourceName — compat XML resource-id / element id
    val actions: List<String> = emptyList(),
    val checkable: Boolean = false,
    val checked: Boolean = false,
    val clickable: Boolean = false,
    val enabled: Boolean = true,
    val focused: Boolean = false,
    val scrollable: Boolean = false,
    val password: Boolean = false,
)

data class WindowData(
    val id: Int,
    val type: String, // application | system | ime | accessibility_overlay
    val pkg: String,
    val nodes: List<NodeData>,
)

object NativeTreeJson {

    fun node(n: NodeData): JSONObject =
        JSONObject()
            .put("node_id", n.nodeId)
            .put("text", n.text)
            .put("class", n.className)
            .put("content_desc", n.contentDesc)
            .put("bounds", JSONArray(n.bounds))
            .put("resource_id", n.resourceId)
            .put("actions", Json.arr(n.actions))
            .put("checkable", n.checkable)
            .put("checked", n.checked)
            .put("clickable", n.clickable)
            .put("enabled", n.enabled)
            .put("focused", n.focused)
            .put("scrollable", n.scrollable)
            .put("password", n.password)

    fun window(w: WindowData): JSONObject =
        JSONObject()
            .put("id", w.id)
            .put("type", w.type)
            .put("package", w.pkg)
            .put("nodes", Json.arr(w.nodes.map { node(it) }))

    fun tree(windows: List<WindowData>, screenWidth: Int, screenHeight: Int): JSONObject =
        JSONObject()
            .put("windows", Json.arr(windows.map { window(it) }))
            .put("screen", JSONObject().put("width", screenWidth).put("height", screenHeight))
}
