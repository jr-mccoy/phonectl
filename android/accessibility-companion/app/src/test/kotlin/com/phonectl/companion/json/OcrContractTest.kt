package com.phonectl.companion.json

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Cross-language contract: the Kotlin `ocr_image` serialization must equal the Python authority
 * fixture (tests/test_providers_ocr.py — the companion path returns
 * `{"text":"World","bounds":[1,2,3,4],"confidence":0.9}`). Pure (no Android/ML-Kit) so it runs on
 * the JVM; the ML-Kit `Text` flattening in OcrHandler is the thin on-device adapter that produces
 * these [OcrRegion]s.
 */
class OcrContractTest {

    // tests/test_providers_ocr.py::test_ocr_image_companion_path region shape.
    private val worldRaw = JSONObject(
        """{"text":"World","bounds":[1,2,3,4],"confidence":0.9}"""
    )

    private val world = OcrRegion(text = "World", bounds = listOf(1, 2, 3, 4), confidence = 0.9)

    @Test
    fun serializedRegionEqualsPythonFixtureFieldForField() {
        assertTrue(
            "serialized=${Ocr.region(world)} expected=$worldRaw",
            Ocr.region(world).similar(worldRaw),
        )
    }

    @Test
    fun regionsWrapsUnderKey() {
        val data = Ocr.regions(listOf(world))
        val arr = data.getJSONArray("regions")
        assertEquals(1, arr.length())
        assertTrue(arr.getJSONObject(0).similar(worldRaw))
    }

    @Test
    fun boundsAreLeftTopRightBottomInOrder() {
        val arr = Ocr.region(world).getJSONArray("bounds")
        assertEquals(1, arr.getInt(0))
        assertEquals(2, arr.getInt(1))
        assertEquals(3, arr.getInt(2))
        assertEquals(4, arr.getInt(3))
    }

    @Test
    fun noConfidenceFilteringHappensCompanionSide() {
        // A low-confidence region survives serialization — Python owns `min_confidence`, not the
        // companion, so every recognized region is returned.
        val low = OcrRegion(text = "faint", bounds = listOf(5, 6, 7, 8), confidence = 0.01)
        val data = Ocr.regions(listOf(world, low))
        val arr = data.getJSONArray("regions")
        assertEquals(2, arr.length())
        assertEquals(0.01, arr.getJSONObject(1).getDouble("confidence"), 1e-9)
    }
}
