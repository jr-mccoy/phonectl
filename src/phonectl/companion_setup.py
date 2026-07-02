"""Guided, idempotent bring-up of the phonectl companion (spec 2026-07-02).

Device contact goes through an injected ``adb(*args) -> CompletedProcess`` seam
(``AdbBackend.run_adb`` in production); this module never imports subprocess.
"""
from __future__ import annotations

import hashlib
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


def _sha256(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _installed(adb) -> bool:
    return PACKAGE in adb("shell", "pm", "list", "packages", PACKAGE).stdout


def ensure_installed(adb, apk_path, cfg, out) -> dict:
    sha = _sha256(apk_path)
    if _installed(adb) and cfg.get("companion_apk_sha") == sha:
        return step("install", "skipped", f"{PACKAGE} already current")
    res = adb("install", "-r", apk_path)
    if res.returncode != 0 and "signatures do not match" in (res.stdout + res.stderr).lower():
        out("signature mismatch — uninstalling old build (resets token + grants)")
        adb("uninstall", PACKAGE)
        res = adb("install", apk_path)
    if res.returncode != 0 or "Success" not in res.stdout:
        return step("install", "failed", res.stderr.strip() or res.stdout.strip(), ok=False)
    cfg["companion_apk_sha"] = sha
    return step("install", "done", f"installed {apk_path}")
