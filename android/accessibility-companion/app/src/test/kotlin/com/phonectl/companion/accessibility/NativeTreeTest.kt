package com.phonectl.companion.accessibility

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The serialized node/window/tree JSON matches the shape native_tree.to_compat_xml consumes,
 * field-for-field with tests/test_native_tree.py's NATIVE fixture.
 */
class NativeTreeTest {

    // Mirrors tests/test_native_tree.py NATIVE
    private val n1 = NodeData(
        nodeId = "n1", text = "Wi-Fi", className = "android.widget.TextView", contentDesc = "",
        bounds = listOf(44, 380, 1036, 520), clickable = true, enabled = true,
        scrollable = false, password = false,
    )
    private val n2 = NodeData(
        nodeId = "n2", text = "", className = "android.widget.EditText", contentDesc = "Search",
        bounds = listOf(0, 100, 1080, 200), clickable = true, enabled = true,
        scrollable = false, password = true,
    )

    @Test
    fun nodeShapeMatchesFixture() {
        val j = NativeTreeJson.node(n1)
        assertEquals("n1", j.getString("node_id"))
        assertEquals("Wi-Fi", j.getString("text"))
        assertEquals("android.widget.TextView", j.getString("class"))
        assertEquals("", j.getString("content_desc"))
        val b = j.getJSONArray("bounds")
        assertEquals(listOf(44, 380, 1036, 520), (0 until b.length()).map { b.getInt(it) })
        assertTrue(j.getBoolean("clickable"))
        assertTrue(j.getBoolean("enabled"))
        assertFalse(j.getBoolean("scrollable"))
        assertFalse(j.getBoolean("password"))
        // every flag the compat XML reads must be present
        for (flag in listOf("checkable", "checked", "clickable", "enabled", "focused", "scrollable", "password")) {
            assertTrue("missing flag $flag", j.has(flag))
        }
        assertTrue(j.has("actions"))
    }

    @Test
    fun passwordNodeCarriesPasswordTrue() {
        val j = NativeTreeJson.node(n2)
        assertTrue(j.getBoolean("password"))
        assertEquals("Search", j.getString("content_desc"))
    }

    @Test
    fun treeWrapsWindowsAndScreen() {
        val tree = NativeTreeJson.tree(
            listOf(WindowData(id = 1, type = "application", pkg = "com.example", nodes = listOf(n1, n2))),
            screenWidth = 1080, screenHeight = 2400,
        )
        val windows = tree.getJSONArray("windows")
        assertEquals(1, windows.length())
        val w0 = windows.getJSONObject(0)
        assertEquals(1, w0.getInt("id"))
        assertEquals("application", w0.getString("type"))
        assertEquals("com.example", w0.getString("package"))
        assertEquals(2, w0.getJSONArray("nodes").length())
        val screen: JSONObject = tree.getJSONObject("screen")
        assertEquals(1080, screen.getInt("width"))
        assertEquals(2400, screen.getInt("height"))
    }
}
