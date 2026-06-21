from __future__ import annotations

import re
import time
from phonectl import ui_parser

_FOCUS_RE = re.compile(r"([A-Za-z0-9_.]+)/([A-Za-z0-9_.]+)")

def parse_focused_app(window_dump: str) -> dict:
    for line in window_dump.splitlines():
        if "mCurrentFocus" in line or "mFocusedApp" in line:
            m = _FOCUS_RE.search(line)
            if m:
                return {"package": m.group(1), "activity": m.group(2)}
    return {"package": "", "activity": ""}

def observe(backend, session, screenshot: bool = False, snap_path: str | None = None, tree: bool = False, relations: bool = False) -> dict:
    xml = backend.ui_dump()
    elements = ui_parser.parse_elements(xml)
    w, h = backend.wm_size()
    app = parse_focused_app(backend.window_dump())
    snap = {
        "app": app,
        "screen": {"w": w, "h": h, "orientation": "portrait" if h >= w else "landscape"},
        "hash": ui_parser.screen_hash(elements),
        "elements": elements,
        "observed_at": time.time(),
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
