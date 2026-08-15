from __future__ import annotations

import os
import shutil
from copy import deepcopy

from droidjig import config

ADBKEY_PATH = os.path.expanduser("~/.android/adbkey")

INSTALL_GUIDANCE = (
    "adb is not installed. In Termux run:\n"
    "    pkg install android-tools\n"
    "then re-run: droidjig setup"
)

WIRELESS_GUIDANCE = (
    "Wireless debugging requires Android 11 or newer.\n"
    "On the phone, enable:\n"
    "    Settings > Developer options > Wireless debugging\n"
    "Tap 'Pair device with pairing code' to read the pairing host:port and 6-digit code."
)

MODULES = ("adb", "accessibility", "notifications", "termux-api")

_MODULE_META = {
    "adb": {
        "required_permission": "Wireless debugging (Developer options)",
        "cap_key": "requires_adb",
        "how_to_enable": "Run: droidjig setup (pairs over Wireless Debugging).",
        "capabilities_unlocked": "observe UI, tap/type/swipe/key, launch apps, send intents.",
        "safety": "Full input control of the device; gated by droidjig modes + kill switch.",
    },
    "accessibility": {
        "required_permission": "AccessibilityService enabled for the droidjig companion app",
        "cap_key": "requires_accessibility",
        "how_to_enable": "Settings > Accessibility > droidjig > On (companion APK, Phase 4).",
        "capabilities_unlocked": "native UI tree + UI event stream + reliable set-text/gestures.",
        "safety": "Reads on-screen content and dispatches gestures; per-capability toggles in the app.",
    },
    "notifications": {
        "required_permission": "Notification access for the droidjig companion app",
        "cap_key": "read_notifications",
        "how_to_enable": "Settings > Notifications > Notification access > droidjig (companion APK, Phase 4).",
        "capabilities_unlocked": "read/wait/reply/dismiss notifications.",
        "safety": "Exposes notification contents; redaction policies apply to logs.",
    },
    "termux-api": {
        "required_permission": "Termux:API app + termux-api package",
        "cap_key": None,
        "how_to_enable": "Install Termux:API app + `pkg install termux-api` (optional, Phase 3.5).",
        "capabilities_unlocked": "battery/clipboard/sensors/notifications/TTS bridges (optional).",
        "safety": "Optional, never a hard dependency; discovered at runtime.",
    },
}


def _connect_without_persisting(conn, addr: str) -> dict:
    before = deepcopy(conn.cfg)
    try:
        conn.connect(addr, persist=False)
    except TypeError:
        conn.connect(addr)
    return before


def run_setup(conn, prompt=input, out=print, which=shutil.which, exists=os.path.exists) -> int:
    out("droidjig setup — let's get your phone connected.")

    if which("adb") is None:
        out(INSTALL_GUIDANCE)
        return 1

    if conn.backend.get_state() == "device":
        conn.cfg.setdefault("mode", "confirm")
        config.save(conn.cfg)
        out(f"droidjig: already connected (serial={conn.backend.serial}). Nothing to do.")
        return 0

    if hasattr(conn, "rediscover") and conn.cfg.get("serial"):
        answer = prompt(f"Last serial {conn.cfg['serial']} is offline. Try reconnecting? [Y/n]: ").strip().lower()
        if answer in ("", "y", "yes"):
            reconnect_failed = False
            try:
                conn.rediscover()
            except ConnectionError:
                reconnect_failed = True
            if conn.backend.get_state() == "device":
                conn.cfg.setdefault("mode", "confirm")
                config.save(conn.cfg)
                out(f"droidjig: reconnected (serial={conn.backend.serial}).")
                return 0
            if not reconnect_failed:
                reconnect_failed = True
            if reconnect_failed:
                out("droidjig: reconnect failed; falling back to full pairing.")

    out(WIRELESS_GUIDANCE)
    pair_addr = prompt("Pairing host:port (e.g. 127.0.0.1:37000): ").strip()
    code = prompt("6-digit pairing code: ").strip()
    conn.pair(pair_addr, code)

    connect_addr = prompt("Connect host:port (e.g. 127.0.0.1:41000): ").strip()
    before_connect_cfg = _connect_without_persisting(conn, connect_addr)

    state = conn.backend.get_state()
    if state != "device":
        conn.cfg.clear()
        conn.cfg.update(before_connect_cfg)
        out(f"droidjig: device did not come online (get-state={state!r}). Re-check Wireless debugging and re-run: droidjig setup")
        return 2

    conn.cfg["serial"] = conn.backend.serial or connect_addr
    conn.cfg.setdefault("mode", "confirm")
    if ":" in connect_addr:
        conn.cfg["last_port"] = connect_addr.rsplit(":", 1)[-1]
    config.save(conn.cfg)

    if exists(ADBKEY_PATH):
        out(f"adb identity key present: {ADBKEY_PATH}")
    else:
        out(f"note: {ADBKEY_PATH} not found yet; adb creates it on first server start.")

    out(f"droidjig: connected (serial={conn.backend.serial}, state={state}).")
    return 0


def module_report(module, *, caps, which=shutil.which) -> dict:
    if module not in _MODULE_META:
        raise ValueError(f"unknown setup module: {module!r} (known: {', '.join(MODULES)})")
    meta = _MODULE_META[module]
    if module == "termux-api":
        available = which("termux-battery") is not None
    else:
        available = bool(caps.get(meta["cap_key"]))
    return {
        "module": module,
        "required_permission": meta["required_permission"],
        "available": available,
        "status": "available" if available else "not available",
        "how_to_enable": meta["how_to_enable"],
        "capabilities_unlocked": meta["capabilities_unlocked"],
        "safety": meta["safety"],
    }


def _print_report(rep, out) -> None:
    out(f"[{rep['module']}] {rep['status']} — {rep['required_permission']}")
    out(f"    enable: {rep['how_to_enable']}")
    out(f"    unlocks: {rep['capabilities_unlocked']}")
    out(f"    safety: {rep['safety']}")


def run_module(module, conn, *, prompt=input, out=print, which=shutil.which, exists=os.path.exists) -> int:
    caps = conn.backend.capabilities() if hasattr(conn.backend, "capabilities") else {}
    if module == "adb":
        return run_setup(conn, prompt=prompt, out=out, which=which, exists=exists)
    if module == "all":
        rc = run_setup(conn, prompt=prompt, out=out, which=which, exists=exists)
        for name in MODULES:
            if name != "adb":
                _print_report(module_report(name, caps=caps, which=which), out)
        return rc
    _print_report(module_report(module, caps=caps, which=which), out)
    return 0
