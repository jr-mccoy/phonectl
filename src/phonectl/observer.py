from __future__ import annotations

import re
import time
from phonectl import errors, ui_parser

_FOCUS_RE = re.compile(r"([A-Za-z0-9_.]+)/([A-Za-z0-9_.]+)")

def parse_focused_app(window_dump: str) -> dict:
    for line in window_dump.splitlines():
        if "mCurrentFocus" in line or "mFocusedApp" in line:
            m = _FOCUS_RE.search(line)
            if m:
                return {"package": m.group(1), "activity": m.group(2)}
    return {"package": "", "activity": ""}


def _orientation(xml: str, w: int, h: int) -> str:
    rot = ui_parser.parse_rotation(xml)
    if rot in (1, 3):
        return "landscape"
    return "portrait" if h >= w else "landscape"


def _window_dump(backend) -> str:
    fn = getattr(backend, "window_dump", None)
    return fn() if fn is not None else ""


def _lock_state(backend, window_dump: str = "") -> dict:
    # Prefer parsing a dump the caller already paid for — `dumpsys window` is a
    # full device round trip, so observe() fetches it once and shares it between
    # the lock check and the focused-app parse.
    if window_dump:
        return ui_parser.parse_lock_state(window_dump)
    fn = getattr(backend, "lock_state", None)
    if fn is not None:
        return fn()
    kg = getattr(backend, "keyguard", None)
    if kg is not None and kg():
        return {"lock_state": "locked_secure", "can_act": False,
                "recommended_user_action": "Unlock the phone manually."}
    return {"lock_state": "unlocked", "can_act": True, "recommended_user_action": None}


def _raise_locked(ls: dict) -> None:
    exc = errors.DeviceLockedError(ls["recommended_user_action"] or "device is locked, unlock it")
    exc.lock_state = ls
    raise exc


def observe(backend, session, screenshot: bool = False, snap_path: str | None = None,
            tree: bool = False, relations: bool = False,
            attempts: int = 3, settle: float = 0.5, sleep=time.sleep) -> dict:
    xml = ""
    for attempt in range(attempts):
        xml = backend.ui_dump()
        if not ui_parser.is_error_dump(xml):
            break
        # An error dump on a locked/asleep screen never heals by retrying:
        # check the lock now and fail fast with actionable guidance.
        ls = _lock_state(backend, _window_dump(backend))
        if not ls["can_act"]:
            _raise_locked(ls)
        if attempt < attempts - 1:
            sleep(settle)
    if ui_parser.is_error_dump(xml):
        raise errors.ObserveError("screen not idle — is it asleep or locked?")

    # One `dumpsys window` serves both the lock check and the focused app.
    # Taken after the UI dump so the reported app reflects the settled screen.
    window = _window_dump(backend)
    ls = _lock_state(backend, window)
    if not ls["can_act"]:
        _raise_locked(ls)

    elements = ui_parser.parse_elements(xml)
    w, h = backend.wm_size()
    app = parse_focused_app(window)
    snap = {
        "app": app,
        "screen": {"w": w, "h": h, "orientation": _orientation(xml, w, h)},
        "hash": ui_parser.screen_hash(elements),
        "elements": elements,
        "observed_at": time.time(),
        "lock_state": ls["lock_state"],
        "can_act": ls["can_act"],
        "recommended_user_action": ls["recommended_user_action"],
        "screenshot": None,
    }
    if tree:
        snap["tree"] = ui_parser.build_tree(xml)
    if relations:
        snap["relations"] = ui_parser.parse_relations(xml)
    if screenshot and snap_path:
        snap["screenshot"] = backend.screencap(snap_path)
    session.set_snapshot(snap)
    return snap
