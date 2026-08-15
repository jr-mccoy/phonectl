import time
from droidjig import observer, errors

KEYMAP = {
    "back": "KEYCODE_BACK",
    "home": "KEYCODE_HOME",
    "recents": "KEYCODE_APP_SWITCH",
    "enter": "KEYCODE_ENTER",
}

def _check_stale(backend, session, expected_hash=None, stale_ok=False) -> None:
    if expected_hash is None:
        return
    if session.last is None or session.last.get("hash") != expected_hash:
        observer.observe(backend, session)
    if not stale_ok and session.last.get("hash") != expected_hash:
        raise errors.StaleSnapshotError("snapshot hash differs from expected_hash")


def _semantic_node(backend, session, i, selector, action):
    """The node_id to drive natively, or None when the coordinate path must serve.

    Semantic-first: when the snapshot came from the companion's native tree, the
    targeted element advertises ``action``, and the backend can perform semantic
    node actions, prefer the accessibility action over a coordinate gesture — it
    is generation-bound (Finding 9) and immune to layout drift. Elements that do
    not advertise the action stay on coordinates: a coordinate tap hit-tests
    through to the clickable ancestor, which ACTION_CLICK on the node would not.
    """
    caps_fn = getattr(backend, "capabilities", None)
    if caps_fn is None or getattr(backend, "semantic_action", None) is None:
        return None
    if session.last is None:
        return None
    if i is None and selector is not None:
        matches = session.find(selector)
        if not matches:
            return None   # the coordinate path raises the proper StaleSnapshotError
        i = matches[0]
    if i is None:
        return None
    el = next((e for e in session.last.get("elements", []) if e["i"] == i), None)
    if el is None or not el.get("node_id") or action not in el.get("actions", ()):
        return None
    try:
        if not caps_fn().get("act_semantic_action"):
            return None
    except Exception:
        return None
    return el["node_id"]


def tap(backend, session, i=None, x=None, y=None, selector=None, expected_hash=None, stale_ok=False) -> dict:
    _check_stale(backend, session, expected_hash, stale_ok)
    if x is not None and y is not None:
        pass
    elif i is not None or selector is not None:
        node_id = _semantic_node(backend, session, i, selector, "click")
        if node_id is not None:
            backend.semantic_action(node_id, "click")
            return observer.observe(backend, session)
        if i is not None:
            x, y = session.resolve(i)
        else:
            x, y = session.resolve_selector(selector)
    else:
        raise ValueError("tap requires x/y, i, or selector")
    backend.input_tap(x, y)
    return observer.observe(backend, session)

def type_text(backend, session, text: str, expected_hash=None, stale_ok=False) -> dict:
    _check_stale(backend, session, expected_hash, stale_ok)
    backend.input_text(text)
    return observer.observe(backend, session)

def swipe(backend, session, x1, y1, x2, y2, ms: int = 200, expected_hash=None, stale_ok=False) -> dict:
    _check_stale(backend, session, expected_hash, stale_ok)
    backend.input_swipe(x1, y1, x2, y2, ms)
    return observer.observe(backend, session)

def key(backend, session, keycode: str, expected_hash=None, stale_ok=False) -> dict:
    _check_stale(backend, session, expected_hash, stale_ok)
    backend.input_key(KEYMAP.get(keycode, keycode))
    return observer.observe(backend, session)

def launch(backend, session, package: str) -> dict:
    backend.launch(package)
    return observer.observe(backend, session)

def _matches(el, text, id):
    if text is not None and el["text"] == text:
        return True
    if id is not None and el["id"] == id:
        return True
    return False

_DIRECTIONS = {"up", "down", "left", "right"}


def named_swipe(backend, session, direction: str, *,
                distance_pct: float = 0.5, ms: int = 400,
                within_i=None, expected_hash=None, stale_ok=False) -> dict:
    _check_stale(backend, session, expected_hash, stale_ok)
    if direction not in _DIRECTIONS:
        raise ValueError(f"unknown swipe direction: {direction!r}")
    if within_i is not None:
        elements = (session.last or {}).get("elements", [])
        el = next((e for e in elements if e["i"] == within_i), None)
        if el is not None:
            x1b, y1b, x2b, y2b = el["bounds"]
            cx, cy = (x1b + x2b) // 2, (y1b + y2b) // 2
            half_x = int((x2b - x1b) * distance_pct / 2)
            half_y = int((y2b - y1b) * distance_pct / 2)
            if direction == "up":
                backend.input_swipe(cx, cy + half_y, cx, cy - half_y, ms)
            elif direction == "down":
                backend.input_swipe(cx, cy - half_y, cx, cy + half_y, ms)
            elif direction == "left":
                backend.input_swipe(cx + half_x, cy, cx - half_x, cy, ms)
            else:
                backend.input_swipe(cx - half_x, cy, cx + half_x, cy, ms)
            return observer.observe(backend, session)
    backend.input_named_swipe(direction, distance_pct, ms)
    return observer.observe(backend, session)


def long_press(backend, session, *, i=None, x=None, y=None, selector=None,
               duration_ms: int = 1000, expected_hash=None, stale_ok=False) -> dict:
    _check_stale(backend, session, expected_hash, stale_ok)
    if x is None or y is None:
        if i is None and selector is None:
            raise ValueError("long_press requires x/y, i, or selector")
        # ACTION_LONG_CLICK has no duration; a non-default hold expresses intent
        # the semantic action cannot honor, so it stays on the gesture path.
        if duration_ms == 1000:
            node_id = _semantic_node(backend, session, i, selector, "long_click")
            if node_id is not None:
                backend.semantic_action(node_id, "long_click")
                return observer.observe(backend, session)
        if i is not None:
            x, y = session.resolve(i)
        else:
            x, y = session.resolve_selector(selector)
    backend.input_long_press(x, y, duration_ms)
    return observer.observe(backend, session)


def double_tap(backend, session, *, i=None, x=None, y=None, selector=None,
               interval_ms: int = 100, sleep=time.sleep,
               expected_hash=None, stale_ok=False) -> dict:
    _check_stale(backend, session, expected_hash, stale_ok)
    if x is None or y is None:
        if i is not None:
            x, y = session.resolve(i)
        elif selector is not None:
            x, y = session.resolve_selector(selector)
        else:
            raise ValueError("double_tap requires x/y, i, or selector")
    backend.input_tap(x, y)
    sleep(interval_ms / 1000)
    backend.input_tap(x, y)
    return observer.observe(backend, session)


def drag(backend, session, x1, y1, x2, y2, duration_ms: int = 500,
         expected_hash=None, stale_ok=False) -> dict:
    _check_stale(backend, session, expected_hash, stale_ok)
    backend.input_swipe(x1, y1, x2, y2, duration_ms)
    return observer.observe(backend, session)


def fling(backend, session, direction: str,
          expected_hash=None, stale_ok=False) -> dict:
    _check_stale(backend, session, expected_hash, stale_ok)
    backend.input_fling(direction)
    return observer.observe(backend, session)


def scroll(backend, session, direction: str, *,
           within_i=None, distance_pct: float = 0.5, ms: int = 400,
           expected_hash=None, stale_ok=False) -> dict:
    _check_stale(backend, session, expected_hash, stale_ok)
    if within_i is not None:
        elements = (session.last or {}).get("elements", [])
        el = next((e for e in elements if e["i"] == within_i), None)
        if el is None:
            raise ValueError(f"no element with index {within_i} in current snapshot")
        x1b, y1b, x2b, y2b = el["bounds"]
        cx, cy = (x1b + x2b) // 2, (y1b + y2b) // 2
        half_x = int((x2b - x1b) * distance_pct / 2)
        half_y = int((y2b - y1b) * distance_pct / 2)
        if direction == "up":
            backend.input_swipe(cx, cy + half_y, cx, cy - half_y, ms)
        elif direction == "down":
            backend.input_swipe(cx, cy - half_y, cx, cy + half_y, ms)
        elif direction == "left":
            backend.input_swipe(cx + half_x, cy, cx - half_x, cy, ms)
        elif direction == "right":
            backend.input_swipe(cx - half_x, cy, cx + half_x, cy, ms)
        else:
            raise ValueError(f"unknown scroll direction: {direction!r}")
    else:
        backend.input_named_swipe(direction, distance_pct, ms)
    return observer.observe(backend, session)


def scroll_until(backend, session, direction: str, *,
                 text=None, selector=None, max_scrolls: int = 10,
                 within_i=None, sleep=time.sleep, halt=None) -> dict:
    if text is None and selector is None:
        raise ValueError("scroll_until requires text or selector")
    from droidjig import ui_parser
    for _ in range(max_scrolls):
        # `halt` re-checks the kill switch between iterations: the funnel gates
        # the loop once at entry, but STOP engaged mid-loop must still bite.
        if halt is not None and halt():
            raise errors.StoppedError("scroll_until halted (kill switch STOP present)")
        snap = observer.observe(backend, session)
        elements = snap.get("elements", [])
        if text is not None and any(e.get("text") == text for e in elements):
            return snap
        if selector is not None and ui_parser.match_selector(elements, selector):
            return snap
        scroll(backend, session, direction, within_i=within_i)
        sleep(0.3)
    return observer.observe(backend, session)


def wait_for(backend, session, text=None, id=None, timeout: float = 5.0,
             interval: float = 0.5, sleep=time.sleep, monotonic=time.monotonic):
    # `id` intentionally shadows the builtin to mirror the element field name `id`;
    # it is only ever compared, never used as the builtin.
    if text is None and id is None:
        raise ValueError("wait_for requires text or id")
    deadline = monotonic() + timeout
    while True:
        snap = observer.observe(backend, session)
        if any(_matches(e, text, id) for e in snap["elements"]):
            return snap
        if monotonic() >= deadline:
            return None
        sleep(interval)
