package com.phonectl.companion

import com.phonectl.companion.accessibility.NativeTreeJson
import com.phonectl.companion.accessibility.NodeData
import com.phonectl.companion.accessibility.WindowData
import com.phonectl.companion.state.Capabilities
import com.phonectl.companion.transport.CoreHandlers
import com.phonectl.companion.transport.Dispatcher
import com.phonectl.companion.transport.Method
import com.phonectl.companion.state.TrustStateStub
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Cross-language contract parity: the Kotlin-serialized JSON, when run through a faithful port of
 * the Python `native_tree.to_compat_xml`, produces exactly the XML that tests/test_native_tree.py
 * asserts. This binds the APK's wire output to the Python consumer's expectations.
 */
class ContractParityTest {

    // tests/test_native_tree.py NATIVE
    private val native = listOf(
        WindowData(
            id = 1, type = "application", pkg = "com.example",
            nodes = listOf(
                NodeData(
                    nodeId = "n1", text = "Wi-Fi", className = "android.widget.TextView",
                    contentDesc = "", bounds = listOf(44, 380, 1036, 520),
                    clickable = true, enabled = true, scrollable = false, password = false,
                ),
                NodeData(
                    nodeId = "n2", text = "", className = "android.widget.EditText",
                    contentDesc = "Search", bounds = listOf(0, 100, 1080, 200),
                    clickable = true, enabled = true, scrollable = false, password = true,
                ),
            ),
        ),
    )

    @Test
    fun serializedTreeProducesExpectedCompatXml() {
        val tree = NativeTreeJson.tree(native, screenWidth = 1080, screenHeight = 2400)
        val xml = toCompatXml(tree)
        // The exact assertions from tests/test_native_tree.py::test_to_compat_xml_maps_bounds_and_flags
        assertTrue(xml, xml.contains("bounds=\"[44,380][1036,520]\""))
        assertTrue(xml, xml.contains("password=\"true\""))
        assertTrue(xml, xml.contains("content-desc=\"Search\""))
        // test_to_compat_xml_is_parseable_by_ui_parser: Wi-Fi text present
        assertTrue(xml, xml.contains("text=\"Wi-Fi\""))
    }

    @Test
    fun specialCharsAreEscaped() {
        val tree = NativeTreeJson.tree(
            listOf(
                WindowData(
                    id = 1, type = "application", pkg = "x",
                    nodes = listOf(
                        NodeData(
                            nodeId = "n", text = "a & b \"q\"", className = "T",
                            contentDesc = "", bounds = listOf(0, 0, 1, 1),
                        )
                    ),
                )
            ),
            1, 1,
        )
        val xml = toCompatXml(tree)
        // tests/test_native_tree.py::test_to_compat_xml_escapes_special_chars
        assertTrue(xml, xml.contains("&amp;"))
    }

    @Test
    fun handshakeAdvertisesExactlyTheMvpKeys() {
        val methods: Map<String, Method> = CoreHandlers.methods(TrustStateStub())
        val line = JSONObject().put("version", 1).put("request_id", "c1")
            .put("method", "handshake").put("params", JSONObject()).toString()
        val data = JSONObject(Dispatcher(methods).handleLine(line)).getJSONObject("data")
        val keys = data.getJSONObject("capabilities").keys().asSequence().toSet()
        assertEquals(Capabilities.MVP_KEYS.toSet(), keys)
    }

    // --- faithful Kotlin port of src/phonectl/native_tree.py::to_compat_xml ---

    private val flags = listOf("checkable", "checked", "clickable", "enabled", "focused", "scrollable", "password")

    private fun toCompatXml(tree: JSONObject): String {
        val sb = StringBuilder("<?xml version=\"1.0\" encoding=\"UTF-8\"?><hierarchy rotation=\"0\">")
        val windows = tree.getJSONArray("windows")
        for (wi in 0 until windows.length()) {
            val nodes = windows.getJSONObject(wi).getJSONArray("nodes")
            for (ni in 0 until nodes.length()) {
                sb.append(nodeXml(nodes.getJSONObject(ni)))
            }
        }
        sb.append("</hierarchy>")
        return sb.toString()
    }

    private fun nodeXml(node: JSONObject): String {
        val b = node.getJSONArray("bounds")
        val parts = mutableListOf(
            attr("text", node.optString("text", "")),
            attr("class", node.optString("class", "")),
            attr("content-desc", node.optString("content_desc", "")),
            attr("bounds", "[${b.getInt(0)},${b.getInt(1)}][${b.getInt(2)},${b.getInt(3)}]"),
        )
        for (flag in flags) parts.add(attr(flag, if (node.optBoolean(flag, false)) "true" else "false"))
        return "<node " + parts.joinToString(" ") + " />"
    }

    // Mirror xml.sax.saxutils.quoteattr (double-quoted): escape & < > and ".
    private fun attr(name: String, value: String): String {
        val escaped = value
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("\"", "&quot;")
        return "$name=\"$escaped\""
    }
}
