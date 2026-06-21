import time
from phonectl import observer, errors

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


def tap(backend, session, i=None, x=None, y=None, selector=None, expected_hash=None, stale_ok=False) -> dict:
    _check_stale(backend, session, expected_hash, stale_ok)
    if x is not None and y is not None:
        pass
    elif i is not None:
        x, y = session.resolve(i)
    elif selector is not None:
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
