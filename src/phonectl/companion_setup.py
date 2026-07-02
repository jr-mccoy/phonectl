"""Guided, idempotent bring-up of the phonectl companion (spec 2026-07-02).

Device contact goes through an injected ``adb(*args) -> CompletedProcess`` seam
(``AdbBackend.run_adb`` in production); this module never imports subprocess.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

PACKAGE = "com.phonectl.companion"
ACCESSIBILITY_COMPONENT = f"{PACKAGE}/{PACKAGE}.service.CompanionAccessibilityService"
LIFECYCLE_COMPONENT = f"{PACKAGE}/.service.LifecycleReceiver"
START_ACTION = "com.phonectl.companion.action.START_SERVICE"
TOKEN_EXTRA = "token"
PREFS_REL = "shared_prefs/phonectl_companion.xml"
TOKEN_KEY = "companion_token"
DEFAULT_PORT = 8765


def step(name: str, status: str, message: str = "", ok: bool = True) -> dict:
    return {"name": name, "ok": ok, "status": status, "message": message}


def parse_token(xml_text: str) -> "str | None":
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None
    el = root.find(f".//string[@name='{TOKEN_KEY}']")
    if el is None or not (el.text or "").strip():
        return None
    return el.text.strip()
