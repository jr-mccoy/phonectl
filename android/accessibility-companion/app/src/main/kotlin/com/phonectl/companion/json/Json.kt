package com.phonectl.companion.json

import org.json.JSONArray
import org.json.JSONObject

/**
 * Tiny hand-rolled org.json builders. No reflection-based mapper (Moshi/Gson) so the
 * node/window/envelope shapes are built field-by-field and provably match the Python
 * fixtures (native_tree.to_compat_xml / test_transport / test_trust).
 *
 * org.json ships in the Android platform at runtime and is supplied as a test dependency
 * on the JVM unit-test classpath.
 */
object Json {

    /** Build a JSONObject from key/value pairs; null values become JSON null. */
    fun obj(vararg pairs: Pair<String, Any?>): JSONObject {
        val o = JSONObject()
        for ((k, v) in pairs) o.put(k, v ?: JSONObject.NULL)
        return o
    }

    /** Build a JSONArray from any iterable of values. */
    fun arr(items: Iterable<Any?>): JSONArray {
        val a = JSONArray()
        for (item in items) a.put(item ?: JSONObject.NULL)
        return a
    }
}
