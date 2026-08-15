"""Optional OCR provider — local Tesseract or companion ML-Kit; discovered at runtime."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

from droidjig import capabilities as caps_mod
from droidjig import errors
from droidjig import ocr as ocr_mod
from droidjig.providers.transport import next_request_id
from droidjig import trust


class OcrProvider:
    def __init__(self, runner=subprocess.run, which=shutil.which, transport=None) -> None:
        self._runner = runner
        self._which = which
        self._t = transport

    def _local_ok(self) -> bool:
        return self._which("tesseract") is not None

    def _companion_ok(self) -> bool:
        try:
            return self._t is not None and bool(self._t.ping())
        except Exception:  # noqa: BLE001
            return False

    def _companion_caps(self) -> dict:
        if self._t is None:
            return {}
        return trust.negotiate(self._t).capabilities

    def _companion_screen_ok(self) -> bool:
        return bool(self._companion_caps().get("observe_ocr_screen"))

    def is_available(self) -> bool:
        return self._local_ok() or self._companion_ok()

    def capabilities(self) -> dict:
        if not self.is_available():
            return caps_mod.make()
        screen = self._companion_screen_ok()
        return caps_mod.make(observe_ocr=True, observe_ocr_screen=screen)

    def ocr_image(self, path: str, *, min_confidence: float = 0.0) -> list:
        if self._local_ok():
            res = self._runner(
                ["tesseract", path, "stdout", "--psm", "6", "tsv"],
                capture_output=True, text=True,
            )
            return ocr_mod.parse_tsv(res.stdout, min_confidence=min_confidence)
        if self._companion_ok():
            rid = next_request_id()
            resp = self._t.request("ocr_image", {"path": path}, request_id=rid, timeout=10.0)
            if resp.get("request_id") != rid or not resp.get("ok"):
                raise errors.ObserveError("companion OCR failed or returned a stale response")
            regions = resp.get("data", {}).get("regions", [])
            return [r for r in regions if r.get("confidence", 1.0) >= min_confidence]
        raise errors.CapabilityUnavailableError(
            "OCR unavailable: install tesseract or the companion ML-Kit OCR provider"
        )

    def _companion_ocr_screen(self, *, min_confidence: float) -> list:
        rid = next_request_id()
        resp = self._t.request("ocr_screen", {}, request_id=rid, timeout=10.0)
        if resp.get("request_id") != rid or not resp.get("ok"):
            raise errors.ObserveError("companion screen OCR failed or returned a stale response")
        regions = resp.get("data", {}).get("regions", [])
        return [r for r in regions if r.get("confidence", 1.0) >= min_confidence]

    def ocr_screen(self, registry, *, min_confidence: float = 0.0, _tmp=None) -> dict:
        if self._t is not None and self._companion_screen_ok():
            return {"regions": self._companion_ocr_screen(min_confidence=min_confidence), "source": "mlkit"}

        make_tmp = _tmp or (lambda suffix=".png": tempfile.mkstemp(suffix=suffix))
        fd, path = make_tmp()
        try:
            if _tmp is None:
                try:
                    os.close(fd)
                except OSError:
                    pass
            registry.screencap(path)
            regions = self.ocr_image(path, min_confidence=min_confidence)
        finally:
            try:
                if _tmp is None and os.path.exists(path):
                    os.remove(path)
            except OSError:
                pass
        source = "tesseract" if self._local_ok() else "mlkit"
        return {"regions": regions, "source": source}
