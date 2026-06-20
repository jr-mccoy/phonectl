import pytest
from phonectl.session import Session
from phonectl import observer

class CannedBackend:
    def __init__(self, xml, window, size=(1080, 2400)):
        self._xml, self._window, self._size = xml, window, size
    def ui_dump(self): return self._xml
    def window_dump(self): return self._window
    def wm_size(self): return self._size
    def screencap(self, path): return path

XML = """<?xml version='1.0'?><hierarchy rotation="0">
<node index="0" text="Wi-Fi" resource-id="android:id/title" class="TextView"
 content-desc="" clickable="true" bounds="[44,380][1036,520]"/></hierarchy>"""

WINDOW = "  mCurrentFocus=Window{a b com.android.settings/com.android.settings.Settings}"

def test_parse_focused_app():
    app = observer.parse_focused_app(WINDOW)
    assert app == {"package": "com.android.settings", "activity": "com.android.settings.Settings"}

def test_observe_builds_snapshot_and_updates_session():
    s = Session()
    snap = observer.observe(CannedBackend(XML, WINDOW), s)
    assert snap["app"]["package"] == "com.android.settings"
    assert snap["screen"] == {"w": 1080, "h": 2400, "orientation": "portrait"}
    assert snap["elements"][0]["text"] == "Wi-Fi"
    assert "hash" in snap and snap["hash"]
    assert s.last is snap

def test_session_resolve_returns_center():
    s = Session()
    observer.observe(CannedBackend(XML, WINDOW), s)
    assert s.resolve(0) == (540, 450)

def test_parse_focused_app_short_dot_activity():
    line = "mCurrentFocus=Window{8b1c2d3 u0 com.android.settings/.Settings}"
    assert observer.parse_focused_app(line) == {
        "package": "com.android.settings", "activity": ".Settings"}

def test_parse_focused_app_empty_returns_blank():
    assert observer.parse_focused_app("") == {"package": "", "activity": ""}

def test_session_resolve_no_snapshot_raises():
    s = Session()
    with pytest.raises(KeyError):
        s.resolve(0)

def test_session_resolve_unknown_index_raises():
    s = Session()
    observer.observe(CannedBackend(XML, WINDOW), s)
    with pytest.raises(KeyError):
        s.resolve(99)
