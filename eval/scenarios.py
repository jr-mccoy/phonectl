"""The seven strategy §26 benchmark scenarios.

Each scenario drives the real ``runtime.run_action`` pipeline over a ``ScriptedBackend`` and returns
a report ``{name, passed, detail, summary}`` where ``summary`` is the §26 metric bundle. Scenarios
1-5 exercise the happy paths (nav, form-fill, OTP, list extraction) and the safety hand-off; 6-7 are
adversarial probes asserting the structured-error and policy-deny paths. All run without a device.

Callers must have PHONECTL_HOME isolated (the pytest gate uses monkeypatch; ``python -m eval`` uses
``harness.isolated_home``).
"""
from __future__ import annotations

import re

from phonectl import actuator, config, ui_parser
from eval.harness import Harness, scripted_build
from eval.simulator import ScriptedBackend, node, screen


def _report(name, passed, harness, detail=""):
    return {"name": name, "passed": bool(passed), "detail": detail, "summary": harness.summary()}


def settings_nav():
    """Launch Settings, find Wi-Fi by selector, tap it, land on the Wi-Fi screen."""
    home = screen(
        node(text="Wi-Fi", rid="com.android.settings:id/title", bounds="[0,300][1080,460]"),
        node(text="Bluetooth", rid="com.android.settings:id/title", bounds="[0,460][1080,620]"))
    wifi = screen(
        node(text="Wi-Fi settings", bounds="[0,0][1080,160]"),
        node(text="On", bounds="[0,160][1080,320]"))
    b = ScriptedBackend([home, wifi], package="com.android.settings")
    h = Harness()
    build = scripted_build(b)
    env = h.run("tap", lambda bk, s: actuator.tap(bk, s, selector={"text": "Wi-Fi"}),
                {"selector": {"text": "Wi-Fi"}}, build=build, yes=True)
    passed = env["ok"] and b.current_index == 1 and "Wi-Fi settings" in b.ui_dump()
    return _report("settings_nav", passed, h)


def form_fill_unicode():
    """Fill a text field with Unicode (accents, CJK, emoji, Greek); verify it is forwarded intact."""
    text = "Café 日本語 😀 Ω"
    form = screen(
        node(text="", rid="com.eval.app:id/name", cls="android.widget.EditText",
             bounds="[0,300][1080,460]"))
    b = ScriptedBackend([form])
    h = Harness()
    env = h.run("type", lambda bk, s: actuator.type_text(bk, s, text),
                {"text": f"<{len(text)} chars>"}, build=scripted_build(b), yes=True)
    passed = env["ok"] and b.texts == [text]
    return _report("form_fill_unicode", passed, h)


def notification_otp():
    """Extract an OTP from notification text and type it into the code field."""
    notif_text = "Your verification code is 482913. Do not share it."
    m = re.search(r"\b(\d{4,8})\b", notif_text)
    code = m.group(1) if m else ""
    field = screen(
        node(text="", rid="com.eval.app:id/otp", cls="android.widget.EditText",
             bounds="[0,300][1080,460]"))
    b = ScriptedBackend([field])
    h = Harness()
    env = h.run("type", lambda bk, s: actuator.type_text(bk, s, code),
                {"text": f"<{len(code)} chars>"}, build=scripted_build(b), yes=True)
    passed = env["ok"] and code == "482913" and b.texts == ["482913"]
    return _report("notification_otp", passed, h)


def messaging_dry_run():
    """A reply in confirm mode without --yes must be refused (human hand-off) and never executed."""
    field = screen(
        node(text="", rid="com.eval.msg:id/reply", cls="android.widget.EditText",
             bounds="[0,300][1080,460]"))
    b = ScriptedBackend([field], package="com.eval.msg")
    h = Harness()
    cfg = config.load()
    cfg["mode"] = "confirm"
    env = h.run("type", lambda bk, s: actuator.type_text(bk, s, "on my way"),
                {"text": "<9 chars>"}, build=scripted_build(b), cfg=cfg, yes=False)
    passed = (not env["ok"] and env["error"]["code"] == "confirmation_required"
              and b.texts == [])
    return _report("messaging_dry_run", passed, h)


def list_extraction_dedup():
    """Scroll a list and extract unique rows (deduplicated)."""
    container = node(rid="com.eval.app:id/list",
                     cls="androidx.recyclerview.widget.RecyclerView",
                     bounds="[0,200][1080,2000]", clickable=False,
                     desc="Results list", scrollable=True)
    rows = [node(text=t, bounds=f"[0,{200 + i * 180}][1080,{360 + i * 180}]")
            for i, t in enumerate(["Alice", "Bob", "Alice", "Carol", "Bob"])]
    scr = screen(container, *rows)
    b = ScriptedBackend([scr])
    h = Harness()
    build = scripted_build(b)
    env = h.run("swipe", lambda bk, s: actuator.swipe(bk, s, 540, 1600, 540, 600),
                {"x1": 540, "y1": 1600, "x2": 540, "y2": 600}, build=build, yes=True)
    elements = build.session.last["elements"]
    container_i = next((e["i"] for e in elements if "RecyclerView" in e.get("class", "")), None)
    rows_out = ui_parser.extract_list(elements, container_i=container_i)
    seen, unique = set(), []
    for e in rows_out:
        t = e.get("text", "")
        if t and t not in seen:
            seen.add(t)
            unique.append(t)
    passed = env["ok"] and unique == ["Alice", "Bob", "Carol"]
    return _report("list_extraction_dedup", passed, h, detail=f"unique={unique}")


def recovery_drill():
    """A locked device must yield a structured, typed error envelope — not a crash."""
    b = ScriptedBackend([screen(node(text="Home"))], locked=True)
    h = Harness()
    env = h.run("tap", lambda bk, s: actuator.tap(bk, s, x=100, y=100),
                {"x": 100, "y": 100}, build=scripted_build(b), yes=True)
    passed = (not env["ok"]
              and env["error"]["code"] in ("device_locked", "observe_failed")
              and env["error"]["requires_user"])
    return _report("recovery_drill", passed, h, detail=env.get("error", {}).get("code"))


def safety_drill():
    """A payment screen classifies critical and is denied by default policy, even with --yes."""
    pay = screen(
        node(text="Confirm payment of $9.99", bounds="[0,200][1080,360]"),
        node(text="Pay now", rid="com.shop:id/pay", bounds="[0,400][1080,560]"))
    b = ScriptedBackend([pay], package="com.shop")
    h = Harness()
    env = h.run("tap", lambda bk, s: actuator.tap(bk, s, selector={"text": "Pay now"}),
                {"selector": {"text": "Pay now"}}, build=scripted_build(b), yes=True)
    passed = (not env["ok"] and env["error"]["code"] == "guarded_action"
              and env.get("risk_level") == "critical" and b.taps == [])
    return _report("safety_drill", passed, h, detail=env.get("risk_level"))


SCENARIOS = [
    settings_nav,
    form_fill_unicode,
    notification_otp,
    messaging_dry_run,
    list_extraction_dedup,
    recovery_drill,
    safety_drill,
]


def run_all() -> list:
    return [s() for s in SCENARIOS]
