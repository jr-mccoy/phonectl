import pytest
from phonectl.session import Session
from phonectl import actuator

XML_A = """<?xml version='1.0'?><hierarchy rotation="0">
<node index="0" text="Wi-Fi" resource-id="android:id/title" class="TextView"
 content-desc="" clickable="true" bounds="[44,380][1036,520]"/></hierarchy>"""
XML_B = """<?xml version='1.0'?><hierarchy rotation="0">
<node index="0" text="Bluetooth" resource-id="android:id/title" class="TextView"
 content-desc="" clickable="true" bounds="[44,540][1036,680]"/></hierarchy>"""
WINDOW = "mCurrentFocus=Window{a b com.android.settings/.Settings}"

class ScriptBackend:
    """Returns XML_A first, then XML_B on subsequent dumps; records actions."""
    def __init__(self):
        self.calls = []
        self._dumps = [XML_A, XML_B, XML_B, XML_B]
    def ui_dump(self):
        return self._dumps.pop(0) if len(self._dumps) > 1 else self._dumps[0]
    def window_dump(self): return WINDOW
    def wm_size(self): return (1080, 2400)
    def input_tap(self, x, y): self.calls.append(("tap", x, y))
    def input_text(self, t): self.calls.append(("text", t))
    def input_key(self, k): self.calls.append(("key", k))
    def input_swipe(self, x1, y1, x2, y2, ms=200): self.calls.append(("swipe", x1, y1, x2, y2, ms))

def test_tap_by_index_resolves_center_then_reobserves():
    s = Session()
    from phonectl import observer
    b = ScriptBackend()
    observer.observe(b, s)                 # seed snapshot (XML_A)
    snap = actuator.tap(b, s, i=0)
    assert ("tap", 540, 450) in b.calls
    assert snap["elements"][0]["text"] == "Bluetooth"   # re-observed XML_B

def test_tap_by_xy_does_not_require_snapshot():
    s = Session()
    b = ScriptBackend()
    actuator.tap(b, s, x=100, y=200)
    assert ("tap", 100, 200) in b.calls

def test_key_maps_friendly_names():
    s = Session()
    b = ScriptBackend()
    actuator.key(b, s, "back")
    assert ("key", "KEYCODE_BACK") in b.calls

def test_wait_for_finds_text_after_polling():
    s = Session()
    b = ScriptBackend()
    calls = []
    snap = actuator.wait_for(b, s, text="Bluetooth", timeout=2, interval=0,
                             sleep=lambda *_: calls.append(1))
    assert snap is not None
    assert any(e["text"] == "Bluetooth" for e in snap["elements"])

def test_swipe_records_and_reobserves():
    s = Session()
    b = ScriptBackend()
    from phonectl import observer
    observer.observe(b, s)  # seed snapshot (XML_A)
    snap = actuator.swipe(b, s, 100, 200, 100, 800)
    assert ("swipe", 100, 200, 100, 800, 200) in b.calls
    assert snap["elements"][0]["text"] == "Bluetooth"  # re-observed XML_B

def test_wait_for_requires_text_or_id():
    s = Session()
    b = ScriptBackend()
    with pytest.raises(ValueError):
        actuator.wait_for(b, s)
