import pytest
from droidjig.providers.ocr import OcrProvider
from droidjig.providers.transport import LoopbackTransport


def _which_found(name):
    return "/usr/bin/" + name


def _which_missing(name):
    return None


TSV = "\n".join([
    "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext",
    "5\t1\t1\t1\t1\t1\t10\t20\t100\t30\t95.0\tHello",
])


class TsvRunner:
    def __init__(self, tsv):
        self.tsv = tsv
        self.calls = []

    def __call__(self, cmd, *, capture_output=True, text=True, **kw):
        self.calls.append(cmd)
        return type("R", (), {"stdout": self.tsv, "returncode": 0})()


class FakeRegistry:
    def __init__(self):
        self.captured = None

    def screencap(self, path):
        self.captured = path
        return path


def test_ocr_screen_prefers_companion_screen_when_advertised():
    reg = FakeRegistry()

    def screen_handler(params):
        assert params == {}
        return {"regions": [{"text": "Direct", "bounds": [1, 2, 3, 4], "confidence": 0.9}]}

    t = LoopbackTransport({
        "handshake": lambda _p: {
            "version": 1,
            "capabilities": {"observe_ocr": True, "observe_ocr_screen": True},
            "stopped": False,
        },
        "ocr_screen": screen_handler,
    })
    p = OcrProvider(runner=TsvRunner(TSV), which=_which_found, transport=t)

    out = p.ocr_screen(reg)

    assert reg.captured is None
    assert out["source"] == "mlkit"
    assert out["regions"][0]["text"] == "Direct"


def test_ocr_screen_falls_back_to_screencap_when_screen_not_advertised(tmp_path):
    reg = FakeRegistry()
    r = TsvRunner(TSV)
    t = LoopbackTransport({
        "handshake": lambda _p: {
            "version": 1,
            "capabilities": {"observe_ocr": True},
            "stopped": False,
        },
    })
    p = OcrProvider(runner=r, which=_which_found, transport=t)

    out = p.ocr_screen(reg, _tmp=lambda suffix=".png": (0, str(tmp_path / "s.png")))

    assert reg.captured == str(tmp_path / "s.png")
    assert out["source"] == "tesseract"
    assert out["regions"][0]["text"] == "Hello"


# --- Task 1: discovery ---

def test_is_available_with_local_tesseract():
    assert OcrProvider(which=_which_found).is_available() is True


def test_capabilities_false_without_tesseract_or_companion():
    p = OcrProvider(which=_which_missing, transport=None)
    assert p.is_available() is False
    assert all(v is False for v in p.capabilities().values())


# --- Task 2: ocr_image ---

def test_ocr_image_local_tesseract_parses_regions():
    r = TsvRunner(TSV)
    p = OcrProvider(runner=r, which=_which_found)
    regions = p.ocr_image("/tmp/shot.png")
    assert regions[0]["text"] == "Hello"
    assert regions[0]["bounds"] == [10, 20, 110, 50]
    assert any("tesseract" in str(c) for c in r.calls)


def test_ocr_image_companion_path():
    def handler(params):
        assert params["path"] == "/tmp/shot.png"
        return {"regions": [{"text": "World", "bounds": [1, 2, 3, 4], "confidence": 0.9}]}

    p = OcrProvider(which=_which_missing,
                    transport=LoopbackTransport({"ocr_image": handler}))
    regions = p.ocr_image("/tmp/shot.png")
    assert regions[0]["text"] == "World"


def test_ocr_image_unavailable_raises():
    p = OcrProvider(which=_which_missing, transport=None)
    with pytest.raises(Exception):
        p.ocr_image("/tmp/shot.png")


# --- Task 3: ocr_screen ---

def test_ocr_screen_captures_then_ocrs(tmp_path):
    reg = FakeRegistry()
    r = TsvRunner(TSV)
    p = OcrProvider(runner=r, which=_which_found)
    out = p.ocr_screen(reg, _tmp=lambda suffix=".png": (0, str(tmp_path / "s.png")))
    assert reg.captured == str(tmp_path / "s.png")
    assert out["regions"][0]["text"] == "Hello"
    assert out["source"] == "tesseract"
