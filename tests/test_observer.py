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


def test_observe_default_omits_tree_and_relations(tmp_path):
    s = Session()
    snap = observer.observe(CannedBackend(XML, WINDOW), s)
    assert "tree" not in snap and "relations" not in snap
    assert "observed_at" in snap


def test_observe_opt_in_tree_and_relations():
    s = Session()
    snap = observer.observe(CannedBackend(XML, WINDOW), s, tree=True, relations=True)
    assert snap["tree"]["class"]
    assert "siblings" in snap["relations"]


# --- perf contract: observe must not multiply device round trips ---

LOCKED_WINDOW = (
    "  mDreamingLockscreen=true\n"
    "  KeyguardServiceDelegate secure=true showing=true\n"
    "  mCurrentFocus=Window{a b com.android.systemui/com.android.systemui.Keyguard}"
)


class CountingBackend(CannedBackend):
    """Mirrors AdbBackend's shape: lock_state() costs a full window_dump too."""

    def __init__(self, xml, window, size=(1080, 2400)):
        super().__init__(xml, window, size)
        self.window_dumps = 0
        self.ui_dumps = 0

    def ui_dump(self):
        self.ui_dumps += 1
        return super().ui_dump()

    def window_dump(self):
        self.window_dumps += 1
        return super().window_dump()

    def lock_state(self):
        from phonectl import ui_parser
        return ui_parser.parse_lock_state(self.window_dump())


def test_observe_calls_window_dump_exactly_once():
    b = CountingBackend(XML, WINDOW)
    observer.observe(b, Session())
    assert b.window_dumps == 1
    assert b.ui_dumps == 1


def test_observe_locked_raises_device_locked_with_lock_state():
    from phonectl import errors
    b = CountingBackend(XML, LOCKED_WINDOW)
    with pytest.raises(errors.DeviceLockedError) as ei:
        observer.observe(b, Session())
    assert ei.value.lock_state["lock_state"] == "locked_secure"
    assert ei.value.lock_state["can_act"] is False


def test_observe_error_dump_while_locked_raises_without_retry_sleeps():
    from phonectl import errors
    b = CountingBackend("ERROR: could not get idle state.", LOCKED_WINDOW)
    sleeps = []
    with pytest.raises(errors.DeviceLockedError):
        observer.observe(b, Session(), sleep=sleeps.append)
    assert b.ui_dumps == 1        # locked is terminal: no point retrying the dump
    assert sleeps == []


def test_observe_error_dump_unlocked_retries_then_observe_error():
    from phonectl import errors
    b = CountingBackend("ERROR: could not get idle state.", WINDOW)
    sleeps = []
    with pytest.raises(errors.ObserveError):
        observer.observe(b, Session(), attempts=3, settle=0.5, sleep=sleeps.append)
    assert b.ui_dumps == 3
    assert sleeps == [0.5, 0.5]


def test_observe_app_parsed_from_same_single_dump():
    b = CountingBackend(XML, WINDOW)
    snap = observer.observe(b, Session())
    assert snap["app"]["package"] == "com.android.settings"
    assert b.window_dumps == 1


# ── combined observe_dump: one round trip serves xml + window ────────────────

class CombinedBackend(CannedBackend):
    """Backend offering the single-round-trip combined dump."""
    def __init__(self, xml, window, size=(1080, 2400)):
        super().__init__(xml, window, size)
        self.combined_calls = 0
        self.ui_calls = 0
        self.window_calls = 0

    def observe_dump(self):
        self.combined_calls += 1
        return self._xml, self._window

    def ui_dump(self):
        self.ui_calls += 1
        return self._xml

    def window_dump(self):
        self.window_calls += 1
        return self._window


def test_observe_prefers_combined_dump_and_skips_separate_calls():
    b = CombinedBackend(XML, WINDOW)
    snap = observer.observe(b, Session())
    assert snap["app"]["package"] == "com.android.settings"
    assert b.combined_calls == 1
    assert b.ui_calls == 0
    assert b.window_calls == 0   # window came out of the same round trip


def test_observe_combined_none_window_falls_back_to_window_dump():
    b = CombinedBackend(XML, WINDOW)
    b.observe_dump = lambda: (XML, None)
    snap = observer.observe(b, Session())
    assert snap["app"]["package"] == "com.android.settings"
    assert b.window_calls == 1   # combined form degraded -> separate fetch


def test_observe_combined_locked_fail_fast_uses_combined_window():
    locked = ("mCurrentFocus=Window{a b com.sec.android.app.launcher/.Launcher}\n"
              "mDreamingLockscreen=true\nKeyguardServiceDelegate showing=true secure=true\n")
    b = CombinedBackend("ERROR: could not get idle state.", locked)
    import pytest as _pytest
    from phonectl import errors
    with _pytest.raises(errors.DeviceLockedError):
        observer.observe(b, Session(), sleep=lambda s: None)
    assert b.combined_calls == 1   # fail-fast on the first attempt
    assert b.window_calls == 0     # lock check reused the combined window
