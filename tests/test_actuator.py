import pytest
from phonectl.session import Session
from phonectl import actuator, observer

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

import pytest
from phonectl import errors

SEL_XML = (
    "<?xml version='1.0'?><hierarchy rotation=\"0\">"
    "<node text=\"Wi-Fi\" class=\"T\" clickable=\"true\" bounds=\"[0,100][500,200]\"/>"
    "</hierarchy>")


class SelBackend:
    def __init__(self): self.taps = []
    def ui_dump(self): return SEL_XML
    def window_dump(self): return "mCurrentFocus=Window{a b com.x/.A}"
    def wm_size(self): return (1080, 2400)
    def input_tap(self, x, y): self.taps.append((x, y))


def test_tap_by_selector_resolves_and_acts():
    b = SelBackend(); s = Session()
    observer.observe(b, s)
    snap = actuator.tap(b, s, selector={"text": "Wi-Fi"})
    assert (250, 150) in b.taps
    assert snap["elements"][0]["text"] == "Wi-Fi"


def test_tap_stale_hash_raises_when_screen_changed():
    b = SelBackend(); s = Session()
    observer.observe(b, s)
    with pytest.raises(errors.StaleSnapshotError):
        actuator.tap(b, s, selector={"text": "Wi-Fi"}, expected_hash="not-the-current-hash")
    assert b.taps == []


def test_tap_stale_ok_proceeds_against_fresh_snapshot():
    b = SelBackend(); s = Session()
    observer.observe(b, s)
    snap = actuator.tap(b, s, selector={"text": "Wi-Fi"}, expected_hash="stale", stale_ok=True)
    assert (250, 150) in b.taps
    assert snap["hash"]


# --- Fixtures for gesture tests ---

GESTURE_XML = (
    "<?xml version='1.0'?><hierarchy rotation=\"0\">"
    "<node index=\"0\" text=\"\" resource-id=\"\" class=\"T\" "
    "content-desc=\"\" clickable=\"true\" scrollable=\"true\" bounds=\"[0,0][100,50]\"/>"
    "</hierarchy>"
)
GESTURE_WINDOW = "mCurrentFocus=Window{a b com.x/.A}"


class FakeGestureBackend:
    def __init__(self):
        self.serial = "d"
        self.tap_count = 0
        self.long_press_called = False
        self.named_swipe_called = False
        self.last_swipe_ms = None
        self.last_swipe_within_bounds = False

    def ui_dump(self): return GESTURE_XML
    def window_dump(self): return GESTURE_WINDOW
    def wm_size(self): return (1080, 2400)
    def lock_state(self): return {"lock_state": "unlocked", "can_act": True, "recommended_user_action": None}

    def input_tap(self, x, y):
        self.tap_count += 1

    def input_swipe(self, x1, y1, x2, y2, ms=200):
        self.last_swipe_ms = ms
        self.last_swipe_within_bounds = True

    def input_long_press(self, x, y, duration_ms=1000):
        self.long_press_called = True

    def input_named_swipe(self, direction, distance_pct=0.5, ms=400):
        self.named_swipe_called = True

    def input_fling(self, direction, velocity=2000):
        self.named_swipe_called = True


@pytest.fixture
def fake_backend():
    return FakeGestureBackend()


@pytest.fixture
def fake_session():
    s = Session()
    s.last = {
        "elements": [{
            "i": 0, "text": "", "id": "", "class": "T", "content_desc": "",
            "clickable": True, "enabled": True, "focused": False, "checkable": False,
            "checked": False, "scrollable": True, "long_clickable": False,
            "password": False, "selected": False, "editable": False,
            "package": "", "bounds": [0, 0, 100, 50], "center": [50, 25],
        }],
        "hash": "abc",
        "app": {},
    }
    return s


# --- Task 1: named_swipe ---

def test_named_swipe_returns_snapshot(fake_backend, fake_session):
    snap = actuator.named_swipe(fake_backend, fake_session, "down")
    assert "elements" in snap
    assert fake_backend.named_swipe_called


def test_named_swipe_unknown_direction_raises(fake_backend, fake_session):
    with pytest.raises(ValueError):
        actuator.named_swipe(fake_backend, fake_session, "diagonal")


# --- Task 2: long_press + double_tap ---

def test_long_press_by_index_returns_snapshot(fake_backend, fake_session):
    snap = actuator.long_press(fake_backend, fake_session, i=0)
    assert "elements" in snap
    assert fake_backend.long_press_called


def test_double_tap_calls_input_tap_twice(fake_backend, fake_session):
    slept = []
    actuator.double_tap(fake_backend, fake_session, i=0, sleep=slept.append)
    assert fake_backend.tap_count == 2
    assert len(slept) == 1


def test_double_tap_requires_target():
    with pytest.raises(ValueError):
        actuator.double_tap(None, None)


# --- Task 3: drag + fling ---

def test_drag_calls_swipe_with_long_duration(fake_backend, fake_session):
    actuator.drag(fake_backend, fake_session, 100, 200, 300, 400)
    assert fake_backend.last_swipe_ms >= 500


def test_fling_returns_snapshot(fake_backend, fake_session):
    snap = actuator.fling(fake_backend, fake_session, "down")
    assert "elements" in snap


# --- Task 4: scroll ---

def test_scroll_full_screen_delegates_to_named_swipe(fake_backend, fake_session):
    snap = actuator.scroll(fake_backend, fake_session, "up")
    assert "elements" in snap
    assert fake_backend.named_swipe_called


def test_scroll_within_container_uses_element_bounds(fake_backend, fake_session):
    snap = actuator.scroll(fake_backend, fake_session, "down", within_i=0)
    assert "elements" in snap
    assert fake_backend.last_swipe_within_bounds


def test_scroll_within_missing_index_raises(fake_backend, fake_session):
    with pytest.raises(ValueError, match="no element"):
        actuator.scroll(fake_backend, fake_session, "up", within_i=999)


# --- Task 5: scroll_until ---

def test_scroll_until_finds_text_on_second_scroll(fake_backend, fake_session):
    call_count = [0]
    found_elements = [
        [],
        [{"i": 0, "text": "Target", "id": "", "class": "", "content_desc": "",
          "clickable": False, "enabled": True, "focused": False, "checkable": False,
          "checked": False, "scrollable": False, "long_clickable": False,
          "password": False, "selected": False, "editable": False,
          "package": "", "bounds": [0, 0, 100, 50], "center": [50, 25]}],
    ]

    def fake_observe(b, s):
        snap = {"elements": found_elements[min(call_count[0], 1)],
                "app": {}, "hash": "h"}
        call_count[0] += 1
        s.last = snap
        return snap

    import phonectl.observer as obs
    original_observe = obs.observe
    obs.observe = fake_observe
    try:
        snap = actuator.scroll_until(
            fake_backend, fake_session, "down", text="Target",
            sleep=lambda _: None,
        )
        assert any(e["text"] == "Target" for e in snap.get("elements", []))
    finally:
        obs.observe = original_observe


def test_scroll_until_returns_last_snapshot_when_not_found(fake_backend, fake_session):
    snap = actuator.scroll_until(
        fake_backend, fake_session, "up", text="NotPresent",
        max_scrolls=2, sleep=lambda _: None,
    )
    assert isinstance(snap, dict)


def test_scroll_until_requires_text_or_selector(fake_backend, fake_session):
    with pytest.raises(ValueError):
        actuator.scroll_until(fake_backend, fake_session, "down")


def test_scroll_until_halts_on_stop_midloop(fake_backend, fake_session):
    # Finding 6: the loop must re-check the kill switch between iterations,
    # not only once at entry.
    from phonectl import errors

    scrolls = []
    halted = [False]

    def halt():
        return halted[0]

    orig = fake_backend.input_named_swipe

    def counting_swipe(*a, **kw):
        scrolls.append(1)
        halted[0] = True   # STOP engages after the first gesture
        return orig(*a, **kw)

    fake_backend.input_named_swipe = counting_swipe
    with pytest.raises(errors.StoppedError):
        actuator.scroll_until(
            fake_backend, fake_session, "down", text="NotPresent",
            max_scrolls=5, sleep=lambda _: None, halt=halt,
        )
    assert len(scrolls) == 1


# ── semantic-first acting: companion node actions win over coordinate gestures ──

SEM_XML = (
    "<?xml version='1.0'?><hierarchy rotation=\"0\">"
    "<node text=\"Wi-Fi\" resource-id=\"a:id/t\" class=\"T\" content-desc=\"\""
    " clickable=\"true\" bounds=\"[0,100][500,200]\""
    " node-id=\"w1.0\" actions=\"click,long_click\"/>"
    "<node text=\"Label\" resource-id=\"\" class=\"T\" content-desc=\"\""
    " clickable=\"true\" bounds=\"[0,300][500,400]\""
    " node-id=\"w1.1\" actions=\"\"/>"
    "</hierarchy>")


class SemanticBackend:
    """Companion-style registry fake: native tree + semantic surface."""

    def __init__(self, semantic=True):
        self._semantic = semantic
        self.taps = []
        self.long_presses = []
        self.semantic_calls = []

    def capabilities(self):
        from phonectl import capabilities
        return capabilities.make(observe_ui_tree=True, act_tap=True,
                                 act_semantic_action=self._semantic)

    def ui_dump(self):
        return SEM_XML

    def window_dump(self):
        return "mCurrentFocus=Window{a b com.x/.A}"

    def wm_size(self):
        return (1080, 2400)

    def input_tap(self, x, y):
        self.taps.append((x, y))

    def input_long_press(self, x, y, duration_ms=1000):
        self.long_presses.append((x, y, duration_ms))

    def semantic_action(self, node_id, action):
        self.semantic_calls.append((node_id, action))
        return {"performed": action}


def test_tap_by_index_prefers_semantic_click():
    b, s = SemanticBackend(), Session()
    observer.observe(b, s)
    actuator.tap(b, s, i=0)
    assert b.semantic_calls == [("w1.0", "click")]
    assert b.taps == []


def test_tap_by_selector_prefers_semantic_click():
    b, s = SemanticBackend(), Session()
    observer.observe(b, s)
    actuator.tap(b, s, selector={"text": "Wi-Fi"})
    assert b.semantic_calls == [("w1.0", "click")]
    assert b.taps == []


def test_tap_falls_to_coordinates_when_node_lacks_click_action():
    # Coordinate taps hit-test through to a clickable ancestor; ACTION_CLICK on a
    # non-clickable node would just fail — so only advertised actions go semantic.
    b, s = SemanticBackend(), Session()
    observer.observe(b, s)
    actuator.tap(b, s, i=1)
    assert b.semantic_calls == []
    assert b.taps == [(250, 350)]


def test_tap_falls_to_coordinates_without_semantic_capability():
    b, s = SemanticBackend(semantic=False), Session()
    observer.observe(b, s)
    actuator.tap(b, s, i=0)
    assert b.semantic_calls == []
    assert b.taps == [(250, 150)]


def test_tap_by_xy_never_goes_semantic():
    b, s = SemanticBackend(), Session()
    observer.observe(b, s)
    actuator.tap(b, s, x=250, y=150)
    assert b.semantic_calls == []
    assert b.taps == [(250, 150)]


def test_tap_on_adb_tree_stays_coordinate():
    # ScriptBackend has no capabilities()/semantic_action() — plain ADB path unchanged.
    b, s = ScriptBackend(), Session()
    observer.observe(b, s)
    actuator.tap(b, s, i=0)
    assert ("tap", 540, 450) in b.calls


def test_long_press_by_index_prefers_semantic_long_click():
    b, s = SemanticBackend(), Session()
    observer.observe(b, s)
    actuator.long_press(b, s, i=0)
    assert b.semantic_calls == [("w1.0", "long_click")]
    assert b.long_presses == []


def test_long_press_with_custom_duration_stays_coordinate():
    # ACTION_LONG_CLICK has no duration; an explicit non-default hold expresses
    # intent the semantic action cannot honor.
    b, s = SemanticBackend(), Session()
    observer.observe(b, s)
    actuator.long_press(b, s, i=0, duration_ms=3000)
    assert b.semantic_calls == []
    assert b.long_presses == [(250, 150, 3000)]
