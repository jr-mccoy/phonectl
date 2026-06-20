import time
from phonectl import observer

KEYMAP = {
    "back": "KEYCODE_BACK",
    "home": "KEYCODE_HOME",
    "recents": "KEYCODE_APP_SWITCH",
    "enter": "KEYCODE_ENTER",
}

def tap(backend, session, i=None, x=None, y=None) -> dict:
    if i is not None:
        x, y = session.resolve(i)
    if x is None or y is None:
        raise ValueError("tap requires either i or both x and y")
    backend.input_tap(x, y)
    return observer.observe(backend, session)

def type_text(backend, session, text: str) -> dict:
    backend.input_text(text)
    return observer.observe(backend, session)

def swipe(backend, session, x1, y1, x2, y2, ms: int = 200) -> dict:
    backend.input_swipe(x1, y1, x2, y2, ms)
    return observer.observe(backend, session)

def key(backend, session, keycode: str) -> dict:
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
             interval: float = 0.5, sleep=time.sleep):
    deadline = timeout
    while True:
        snap = observer.observe(backend, session)
        if any(_matches(e, text, id) for e in snap["elements"]):
            return snap
        deadline -= max(interval, 0.0001)
        if deadline <= 0:
            return None
        sleep(interval)
