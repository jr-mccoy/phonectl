package com.droidjig.companion.service

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import com.google.android.gms.tasks.Tasks
import com.google.mlkit.vision.common.InputImage
import com.google.mlkit.vision.text.Text
import com.google.mlkit.vision.text.TextRecognition
import com.google.mlkit.vision.text.latin.TextRecognizerOptions
import com.droidjig.companion.json.Ocr
import com.droidjig.companion.json.OcrRegion
import com.droidjig.companion.transport.Method
import com.droidjig.companion.transport.MethodException
import org.json.JSONObject
import java.util.concurrent.TimeUnit

/**
 * `ocr_image` handler (Plan 4.7 / accessibility-companion SPEC §7): decode a PNG the caller already
 * captured (typically via `screencap`) and run ML-Kit's bundled on-device Latin text recognition
 * over it, returning a flat region list `{regions:[{text, bounds, confidence}]}`.
 *
 * Precedence: the Python `OcrProvider` (src/droidjig/providers/ocr.py) is **tesseract-first** — its
 * `_local_ok()` short-circuits to the local `tesseract` binary before ever calling this method, so
 * `ocr_image` is the fallback path used only when `tesseract` is absent from the Termux PATH.
 *
 * Threshold ownership: this returns **all** regions unfiltered. Python applies `min_confidence`
 * over the returned regions, so no confidence filtering happens companion-side.
 *
 * On-device only (decode + ML-Kit); the pure region serialization lives in [Ocr] and is JVM-tested.
 */
object OcrHandler {

    /** Stay inside the Python transport's 10 s `ocr_image` budget; the dispatcher thread blocks. */
    private const val TIMEOUT_SECONDS = 9L

    /**
     * Dispatcher method map. No [TrustState] dependency — the `observe_ocr` toggle gates this
     * method in the dispatcher via [com.droidjig.companion.state.Capabilities.methodGate], not here.
     */
    fun methods(): Map<String, Method> = mapOf(
        "ocr_image" to { p -> ocrImage(p) },
    )

    fun ocrImage(params: JSONObject): JSONObject {
        val path = params.optString("path", "")
        if (path.isBlank()) throw MethodException("screencap_unavailable", "no path")

        // BitmapFactory returns null on a missing/undecodable file — reuse screencap_unavailable
        // (the contract's read/decode error) rather than inventing a new code.
        val bitmap = BitmapFactory.decodeFile(path)
            ?: throw MethodException("screencap_unavailable", "cannot decode image at $path")

        return ocrBitmap(bitmap)
    }

    /** Run OCR over an in-memory screenshot bitmap without requiring or creating a PNG file. */
    fun ocrBitmap(bitmap: Bitmap): JSONObject {
        val recognizer = TextRecognition.getClient(TextRecognizerOptions.DEFAULT_OPTIONS)
        val text = try {
            Tasks.await(
                recognizer.process(InputImage.fromBitmap(bitmap, 0)),
                TIMEOUT_SECONDS, TimeUnit.SECONDS,
            )
        } catch (e: Exception) {
            throw MethodException("screencap_unavailable", "OCR failed: ${e.message}")
        } finally {
            recognizer.close()
        }
        return Ocr.regions(flatten(text))
    }

    /**
     * Flatten ML-Kit's `Text.TextBlock → Text.Line` tree into a flat region list — one region per
     * line. `bounds` come from the line's `boundingBox` (`[left, top, right, bottom]` screen px);
     * `confidence` from the line where the model exposes it, defaulting to `1.0` only when the API
     * returns none. Lines without a bounding box are skipped (no usable coordinates).
     */
    fun flatten(text: Text): List<OcrRegion> {
        val out = ArrayList<OcrRegion>()
        for (block in text.textBlocks) {
            for (line in block.lines) {
                val box = line.boundingBox ?: continue
                out.add(
                    OcrRegion(
                        text = line.text,
                        bounds = listOf(box.left, box.top, box.right, box.bottom),
                        confidence = lineConfidence(line),
                    )
                )
            }
        }
        return out
    }

    private fun lineConfidence(line: Text.Line): Double {
        val c = line.confidence
        return if (c != null && !c.isNaN()) c.toDouble() else 1.0
    }
}
