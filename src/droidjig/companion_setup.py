"""Guided, idempotent bring-up of the droidjig companion (spec 2026-07-02).

Device contact goes through an injected ``adb(*args) -> CompletedProcess`` seam
(``AdbBackend.run_adb`` in production); this module never imports subprocess.
"""
from __future__ import annotations

import hashlib
import shlex
import time
import xml.etree.ElementTree as ET

from droidjig import config as _config

PACKAGE = "com.droidjig.companion"
ACCESSIBILITY_COMPONENT = f"{PACKAGE}/{PACKAGE}.service.CompanionAccessibilityService"
LIFECYCLE_COMPONENT = f"{PACKAGE}/.service.LifecycleReceiver"
START_ACTION = "com.droidjig.companion.action.START_SERVICE"
SET_TOKEN_ACTION = "com.droidjig.companion.action.SET_TOKEN"
TOKEN_EXTRA = "token"
PREFS_REL = "shared_prefs/droidjig_companion.xml"
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
    if "signatures do not match" in (res.stdout + res.stderr).lower():
        out("signature mismatch — uninstalling old build (resets token + grants)")
        adb("uninstall", PACKAGE)
        res = adb("install", apk_path)
    if res.returncode != 0 or "Success" not in res.stdout:
        return step("install", "failed", res.stderr.strip() or res.stdout.strip() or "adb install failed", ok=False)
    cfg["companion_apk_sha"] = sha
    return step("install", "done", f"installed {apk_path}")


def _confirm(assume_yes, prompt, what) -> bool:
    if assume_yes:
        return True
    return prompt(f"Grant/start: {what}? [y/N]: ").strip().lower() in ("y", "yes")


def ensure_accessibility(adb, out, *, assume_yes, prompt) -> dict:
    current = adb("shell", "settings", "get", "secure",
                  "enabled_accessibility_services").stdout.strip()
    if ACCESSIBILITY_COMPONENT in current:
        return step("accessibility", "skipped", "service already enabled")
    out("This enables an AccessibilityService that can read the screen and inject gestures.")
    if not _confirm(assume_yes, prompt, "enable companion AccessibilityService"):
        return step("accessibility", "failed", "declined (re-run with --yes)", ok=False)
    value = (ACCESSIBILITY_COMPONENT if current in ("", "null")
             else current + ":" + ACCESSIBILITY_COMPONENT)
    adb("shell", "settings", "put", "secure", "enabled_accessibility_services", value)
    adb("shell", "settings", "put", "secure", "accessibility_enabled", "1")
    return step("accessibility", "done", "AccessibilityService enabled")


_NOTIF_LISTENER_HINT = (
    "Manual step (adb cannot grant this): open the companion app and tap "
    "'Notification access' to enable inline-reply/dismiss.")


def ensure_notifications(adb, out) -> dict:
    dump = adb("shell", "dumpsys", "package", PACKAGE).stdout
    granted = "POST_NOTIFICATIONS: granted=true" in dump
    result = step("notifications", "skipped", "POST_NOTIFICATIONS already granted")
    if not granted:
        adb("shell", "pm", "grant", PACKAGE, "android.permission.POST_NOTIFICATIONS")
        result = step("notifications", "done", "granted POST_NOTIFICATIONS")
    out(_NOTIF_LISTENER_HINT)
    return result


def read_token_via_runas(adb) -> "str | None":
    res = adb("shell", "run-as", PACKAGE, "cat", PREFS_REL)
    if res.returncode != 0:
        return None
    return parse_token(res.stdout)


def acquire_token(adb, cfg, out, *, prompt) -> dict:
    if cfg.get("companion_token"):
        return step("token", "skipped", "companion_token already set")
    token = read_token_via_runas(adb)
    source = "run-as"
    if not token:
        adb("shell", "am", "start", "-n", f"{PACKAGE}/.ui.SettingsActivity")
        out("Copy the token from the companion app's Pairing section.")
        token = prompt("Paste companion token: ").strip()
        source = "prompt"
    if not token:
        return step("token", "failed", "no token acquired", ok=False)
    cfg["companion_token"] = token
    _config.save(cfg)
    return step("token", "done", f"paired via {source}")


def push_token(adb, cfg, out, *, token=None, mint=None) -> dict:
    """Pushed-token v2 (trust-on-first-use): mint a token and broadcast it to the companion so a
    release build needs neither `run-as` nor manual paste. The companion adopts it ONLY when it has
    no token yet (LifecycleAuth.authorizedFirstPair); once a token exists the broadcast is ignored,
    so this is safe to call idempotently. Additive — leaves the run-as/paste paths intact.
    See docs/design/2026-07-06-pushed-token-v2-design.md.
    """
    if cfg.get(TOKEN_KEY):
        return step("push_token", "skipped", "companion_token already set")
    token = token or (mint or _mint_token)()
    adb("shell", "am", "broadcast", "-a", SET_TOKEN_ACTION,
        "--es", TOKEN_EXTRA, shlex.quote(token), "-n", LIFECYCLE_COMPONENT)
    cfg[TOKEN_KEY] = token
    _config.save(cfg)
    out("Pushed a minted pairing token to the companion (adopted only if none was set).")
    return step("push_token", "done", "token minted and pushed")


def _mint_token() -> str:
    import secrets
    return secrets.token_hex(16)  # 32 hex chars, matching the companion's own generateToken()


def _socket_up(adb, port=DEFAULT_PORT) -> bool:
    return f":{port}" in adb("shell", "ss", "-tln").stdout


def start_server(adb, token, cfg, out, *, assume_yes, prompt,
                 sleep=time.sleep, attempts=10) -> dict:
    if _socket_up(adb):
        cfg["companion_host"] = "127.0.0.1"
        cfg["companion_port"] = DEFAULT_PORT
        _config.save(cfg)
        return step("server", "skipped", f"socket :{DEFAULT_PORT} already listening")
    out(f"This starts the companion's remote-control socket on 127.0.0.1:{DEFAULT_PORT}.")
    if not _confirm(assume_yes, prompt, "start companion server"):
        return step("server", "failed", "declined (re-run with --yes)", ok=False)
    adb("shell", "am", "broadcast", "-a", START_ACTION,
        "--es", TOKEN_EXTRA, shlex.quote(token), "-n", LIFECYCLE_COMPONENT)
    for _ in range(attempts):
        if _socket_up(adb):
            cfg["companion_host"] = "127.0.0.1"
            cfg["companion_port"] = DEFAULT_PORT
            _config.save(cfg)
            return step("server", "done", f"socket :{DEFAULT_PORT} up")
        sleep(1)
    return step("server", "failed", f"socket :{DEFAULT_PORT} never came up", ok=False)


def verify(cfg, *, negotiate=None, transport_factory=None) -> dict:
    if negotiate is None:
        from droidjig import trust
        negotiate = trust.negotiate
    if transport_factory is None:
        from droidjig.providers.transport import SocketTransport
        transport_factory = SocketTransport
    t = transport_factory(cfg.get("companion_host", "127.0.0.1"),
                          int(cfg.get("companion_port") or DEFAULT_PORT),
                          token=cfg.get("companion_token"))
    hs = negotiate(t, timeout=3.0)
    caps = hs.capabilities or {}
    data = {"reachable": hs.reachable, "stopped": hs.stopped, "capabilities": caps}
    if not hs.reachable:
        return {**step("verify", "failed", "companion unreachable (token/socket?)", ok=False),
                "data": data}
    on = sorted(k for k, v in caps.items() if v)
    return {**step("verify", "done", f"reachable; {len(on)} caps: {', '.join(on)}"),
            "data": data}


def status(adb, cfg) -> dict:
    """Read-only companion state for `droidjig companion status` (no device mutation)."""
    accessibility = ACCESSIBILITY_COMPONENT in adb(
        "shell", "settings", "get", "secure", "enabled_accessibility_services").stdout
    return {
        "installed": _installed(adb),
        "accessibility": accessibility,
        "socket": _socket_up(adb),
        "token_paired": bool(cfg.get("companion_token")),
    }


def run_companion_setup(adb, cfg, *, apk_path, assume_yes=False,
                        prompt=input, out=print, sleep=time.sleep, verify_kwargs=None) -> dict:
    steps: list = []

    def record(result):
        steps.append(result)
        return result["ok"]

    if not record(ensure_installed(adb, apk_path, cfg, out)):
        return {"ok": False, "steps": steps}
    if not record(ensure_accessibility(adb, out, assume_yes=assume_yes, prompt=prompt)):
        return {"ok": False, "steps": steps}
    record(ensure_notifications(adb, out))  # never fatal
    if not record(acquire_token(adb, cfg, out, prompt=prompt)):
        return {"ok": False, "steps": steps}
    token = cfg.get("companion_token")
    if not record(start_server(adb, token, cfg, out, assume_yes=assume_yes,
                               prompt=prompt, sleep=sleep)):
        return {"ok": False, "steps": steps}
    record(verify(cfg, **(verify_kwargs or {})))
    return {"ok": all(s["ok"] for s in steps), "steps": steps}
