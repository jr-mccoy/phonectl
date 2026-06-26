package com.phonectl.companion.json

import org.json.JSONArray
import org.json.JSONObject

/**
 * One OCR region: recognized [text], its screen-pixel [bounds] as `[left, top, right, bottom]`
 * (the same order as `observe_native`), and [confidence] in `0.0–1.0`.
 *
 * This is the companion-side analog of [NotifData] for OCR: a plain data view that the ML-Kit
 * flattening in [com.phonectl.companion.service.OcrHandler] populates, and that the pure org.json
 * builders below serialize — so the wire shape is exercised on the JVM without Android/ML-Kit.
 */
data class OcrRegion(val text: String, val bounds: List<Int>, val confidence: Double)

/**
 * Pure org.json builders for the `ocr_image` result shape
 * (src/phonectl/providers/ocr.py / tests/test_providers_ocr.py):
 *   {regions:[{text, bounds:[left,top,right,bottom], confidence}, ...]}
 *
 * The companion returns **all** regions unfiltered — the Python `OcrProvider` owns the
 * `min_confidence` threshold (and is tesseract-first, so this method is the fallback path only).
 */
object Ocr {

    fun region(r: OcrRegion): JSONObject {
        val bounds = JSONArray()
        for (v in r.bounds) bounds.put(v)
        return JSONObject()
            .put("text", r.text)
            .put("bounds", bounds)
            .put("confidence", r.confidence)
    }

    fun regions(items: List<OcrRegion>): JSONObject {
        val arr = JSONArray()
        for (r in items) arr.put(region(r))
        return JSONObject().put("regions", arr)
    }
}
