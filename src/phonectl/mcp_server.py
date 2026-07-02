"""Structured-result MCP server handlers, registry, and optional transport."""
from __future__ import annotations

import inspect

from phonectl import (
    actuator,
    audit,
    capabilities as capmod,
    config,
    errors,
    observer,
    policy,
    results,
    runtime,
    ui_parser,
)


def _default_build(cfg):
    from phonectl.cli import build_runtime

    return build_runtime(cfg)


def observe_ui(build=_default_build, *, tree=False, relations=False, screenshot=False, snap_path=None) -> dict:
    try:
        backend, session, conn = build(config.load())
        conn.ensure()
        snap = observer.observe(backend, session, tree=tree, relations=relations, screenshot=screenshot, snap_path=snap_path)
        return results.ok(capability="ui.observe", provider="adb", data=snap)
    except errors.PhonectlError as e:
        return results.err(e, **getattr(e, "lock_state", {}))


def find(build=_default_build, *, selector) -> dict:
    try:
        backend, session, conn = build(config.load())
        conn.ensure()
        snap = observer.observe(backend, session, relations=True)
        matches = ui_parser.match_selector(snap["elements"], selector, snap.get("relations"))
        rel = snap.get("relations", {}) or {}
        by_i = {e["i"]: e for e in snap["elements"]}
        candidates = []
        for i in matches:
            e = by_i[i]
            candidates.append({
                "i": i,
                "text": e["text"],
                "id": e["id"],
                "bounds": e["bounds"],
                "center": e["center"],
                "parent": (rel.get("parent", {}) or {}).get(str(i)),
                "siblings": (rel.get("siblings", {}) or {}).get(str(i), []),
            })
        confidence = 1.0 if len(matches) == 1 else (round(1 / len(matches), 3) if matches else 0.0)
        return results.ok(capability="ui.find", provider="adb", data={"candidates": candidates, "confidence": confidence})
    except errors.PhonectlError as e:
        return results.err(e, **getattr(e, "lock_state", {}))


def capabilities(build=_default_build) -> dict:
    backend, _session, _conn = build(config.load())
    caps = backend.capabilities()
    return results.ok(capability="capabilities", provider="adb", data={"capabilities": caps, "summary": capmod.describe(caps)})


def _action_cfg(dry_run: bool) -> dict:
    cfg = config.load()
    return {**cfg, "mode": "dry-run"} if dry_run else cfg


def _with_reason(target: dict, reason) -> dict:
    return {**target, "reason": reason} if reason else target


def tap(build=_default_build, *, index=None, selector=None, x=None, y=None, expected_hash=None, stale_ok=False, dry_run=False, confirm=False, reason=None, idempotency_key=None) -> dict:
    if selector is not None:
        target = {"selector": selector}
        fn = lambda b, s: actuator.tap(b, s, selector=selector, expected_hash=expected_hash, stale_ok=stale_ok)
    elif index is not None:
        target = {"i": index}
        fn = lambda b, s: actuator.tap(b, s, i=index, expected_hash=expected_hash, stale_ok=stale_ok)
    else:
        target = {"x": x, "y": y}
        fn = lambda b, s: actuator.tap(b, s, x=x, y=y, expected_hash=expected_hash, stale_ok=stale_ok)
    return runtime.run_action("tap", fn, _with_reason(target, reason), build=build, yes=confirm, cfg=_action_cfg(dry_run), idempotency_key=idempotency_key)


def type_text(build=_default_build, *, text, dry_run=False, confirm=False, reason=None, idempotency_key=None) -> dict:
    target = _with_reason({"text": f"<{len(text)} chars>"}, reason)
    return runtime.run_action("type", lambda b, s: actuator.type_text(b, s, text), target, build=build, yes=confirm, cfg=_action_cfg(dry_run), idempotency_key=idempotency_key)


def swipe(build=_default_build, *, x1, y1, x2, y2, dry_run=False, confirm=False, reason=None, idempotency_key=None) -> dict:
    target = _with_reason({"coords": [x1, y1, x2, y2]}, reason)
    return runtime.run_action("swipe", lambda b, s: actuator.swipe(b, s, x1, y1, x2, y2), target, build=build, yes=confirm, cfg=_action_cfg(dry_run), idempotency_key=idempotency_key)


def key(build=_default_build, *, keycode, dry_run=False, confirm=False, reason=None, idempotency_key=None) -> dict:
    target = _with_reason({"key": keycode}, reason)
    return runtime.run_action("key", lambda b, s: actuator.key(b, s, keycode), target, build=build, yes=confirm, cfg=_action_cfg(dry_run), idempotency_key=idempotency_key)


def launch(build=_default_build, *, package, dry_run=False, confirm=False, reason=None, idempotency_key=None) -> dict:
    target = _with_reason({"package": package}, reason)
    return runtime.run_action("launch", lambda b, s: actuator.launch(b, s, package), target, build=build, yes=confirm, cfg=_action_cfg(dry_run), idempotency_key=idempotency_key)


def _as_registry(backend):
    from phonectl.providers.registry import ProviderRegistry
    return backend if isinstance(backend, ProviderRegistry) else ProviderRegistry([backend])


def clipboard_read(build=_default_build) -> dict:
    from phonectl.providers.clipboard import ClipboardProvider
    try:
        backend, _session, conn = build(config.load())
        conn.ensure()
        return ClipboardProvider(_as_registry(backend)).read()
    except errors.PhonectlError as e:
        return results.err(e, **getattr(e, "lock_state", {}))


def clipboard_write(build=_default_build, *, text, dry_run=False, confirm=False) -> dict:
    from phonectl.providers.clipboard import ClipboardProvider
    cfg = _action_cfg(dry_run)
    backend, _session, _conn = build(config.load())
    return ClipboardProvider(_as_registry(backend)).write(text, build=build, yes=confirm, cfg=cfg)


def clipboard_clear(build=_default_build, *, dry_run=False, confirm=False) -> dict:
    from phonectl.providers.clipboard import ClipboardProvider
    cfg = _action_cfg(dry_run)
    backend, _session, _conn = build(config.load())
    return ClipboardProvider(_as_registry(backend)).clear(build=build, yes=confirm, cfg=cfg)


def intent_start(build=_default_build, *, action=None, data=None, component=None,
                 extras=None, dry_run=False, confirm=False) -> dict:
    from phonectl.providers.intents import IntentProvider
    cfg = _action_cfg(dry_run)
    backend, _session, _conn = build(config.load())
    return IntentProvider(_as_registry(backend)).start(
        action=action, data=data, component=component, extras=extras,
        build=build, yes=confirm, cfg=cfg,
    )


def intent_broadcast(build=_default_build, *, action, extras=None,
                     dry_run=False, confirm=False) -> dict:
    from phonectl.providers.intents import IntentProvider
    cfg = _action_cfg(dry_run)
    backend, _session, _conn = build(config.load())
    return IntentProvider(_as_registry(backend)).broadcast(
        action, extras=extras, build=build, yes=confirm, cfg=cfg
    )


def packages_list(build=_default_build, *, include_system=False) -> dict:
    from phonectl.providers.packages import PackageProvider
    try:
        backend, _session, conn = build(config.load())
        conn.ensure()
        return PackageProvider(_as_registry(backend)).list_packages(include_system=include_system)
    except errors.PhonectlError as e:
        return results.err(e, **getattr(e, "lock_state", {}))


def packages_resolve(build=_default_build, *, package) -> dict:
    from phonectl.providers.packages import PackageProvider
    try:
        backend, _session, conn = build(config.load())
        conn.ensure()
        return PackageProvider(_as_registry(backend)).resolve(package)
    except errors.PhonectlError as e:
        return results.err(e, **getattr(e, "lock_state", {}))


def packages_launch(build=_default_build, *, package, dry_run=False, confirm=False) -> dict:
    from phonectl.providers.packages import PackageProvider
    cfg = _action_cfg(dry_run)
    backend, _session, _conn = build(config.load())
    return PackageProvider(_as_registry(backend)).launch(package, build=build, yes=confirm, cfg=cfg)


def packages_stop(build=_default_build, *, package, dry_run=False, confirm=False) -> dict:
    from phonectl.providers.packages import PackageProvider
    cfg = _action_cfg(dry_run)
    backend, _session, _conn = build(config.load())
    return PackageProvider(_as_registry(backend)).stop(package, build=build, yes=confirm, cfg=cfg)


def packages_clear(build=_default_build, *, package, confirm=False, dry_run=False) -> dict:
    from phonectl.providers.packages import PackageProvider
    cfg = _action_cfg(dry_run)
    backend, _session, _conn = build(config.load())
    return PackageProvider(_as_registry(backend)).clear(package, build=build, yes=confirm, cfg=cfg)


def named_swipe(build=_default_build, *, direction, distance_pct=0.5, ms=400,
                within_index=None, dry_run=False, confirm=False) -> dict:
    cfg = _action_cfg(dry_run)
    return runtime.run_action(
        "named_swipe",
        lambda b, s: actuator.named_swipe(b, s, direction, distance_pct=distance_pct,
                                           ms=ms, within_i=within_index),
        {"direction": direction},
        build=build, yes=confirm, cfg=cfg,
    )


def long_press(build=_default_build, *, index=None, selector=None, x=None, y=None,
               duration_ms=1000, dry_run=False, confirm=False) -> dict:
    cfg = _action_cfg(dry_run)
    return runtime.run_action(
        "long_press",
        lambda b, s: actuator.long_press(b, s, i=index, selector=selector, x=x, y=y,
                                          duration_ms=duration_ms),
        {"i": index, "x": x, "y": y},
        build=build, yes=confirm, cfg=cfg,
    )


def double_tap(build=_default_build, *, index=None, selector=None, x=None, y=None,
               interval_ms=100, dry_run=False, confirm=False) -> dict:
    cfg = _action_cfg(dry_run)
    return runtime.run_action(
        "double_tap",
        lambda b, s: actuator.double_tap(b, s, i=index, selector=selector, x=x, y=y,
                                          interval_ms=interval_ms),
        {"i": index, "x": x, "y": y},
        build=build, yes=confirm, cfg=cfg,
    )


def drag(build=_default_build, *, x1, y1, x2, y2, duration_ms=500,
         dry_run=False, confirm=False) -> dict:
    cfg = _action_cfg(dry_run)
    return runtime.run_action(
        "drag",
        lambda b, s: actuator.drag(b, s, x1, y1, x2, y2, duration_ms),
        {"coords": [x1, y1, x2, y2]},
        build=build, yes=confirm, cfg=cfg,
    )


def fling(build=_default_build, *, direction, dry_run=False, confirm=False) -> dict:
    cfg = _action_cfg(dry_run)
    return runtime.run_action(
        "fling",
        lambda b, s: actuator.fling(b, s, direction),
        {"direction": direction},
        build=build, yes=confirm, cfg=cfg,
    )


def scroll(build=_default_build, *, direction, within_index=None,
           distance_pct=0.5, dry_run=False, confirm=False) -> dict:
    cfg = _action_cfg(dry_run)
    return runtime.run_action(
        "scroll",
        lambda b, s: actuator.scroll(b, s, direction, within_i=within_index,
                                      distance_pct=distance_pct),
        {"direction": direction},
        build=build, yes=confirm, cfg=cfg,
    )


def extract_list(build=_default_build, *, container_index=None) -> dict:
    try:
        backend, session, conn = build(config.load())
        conn.ensure()
        snap = observer.observe(backend, session)
        rows = ui_parser.extract_list(snap["elements"], container_i=container_index)
        return results.ok(capability="extraction.list", provider="adb", data={"rows": rows})
    except errors.PhonectlError as e:
        return results.err(e, **getattr(e, "lock_state", {}))


def extract_form(build=_default_build) -> dict:
    try:
        backend, session, conn = build(config.load())
        conn.ensure()
        snap = observer.observe(backend, session, relations=True)
        fields = ui_parser.extract_form(snap["elements"], relations=snap.get("relations"))
        return results.ok(capability="extraction.form", provider="adb", data={"fields": fields})
    except errors.PhonectlError as e:
        return results.err(e, **getattr(e, "lock_state", {}))


def get_focused_field(build=_default_build) -> dict:
    try:
        backend, session, conn = build(config.load())
        conn.ensure()
        snap = observer.observe(backend, session)
        el = ui_parser.get_focused_field(snap["elements"])
        return results.ok(capability="extraction.focused_field", provider="adb", data={"element": el})
    except errors.PhonectlError as e:
        return results.err(e, **getattr(e, "lock_state", {}))


def find_text(build=_default_build, *, pattern) -> dict:
    try:
        backend, session, conn = build(config.load())
        conn.ensure()
        snap = observer.observe(backend, session)
        matches = ui_parser.find_by_text_regex(snap["elements"], pattern)
        return results.ok(capability="extraction.find", provider="adb", data={"matches": matches})
    except errors.PhonectlError as e:
        return results.err(e, **getattr(e, "lock_state", {}))


def scroll_until_mcp(build=_default_build, *, direction="down", text=None,
                     selector=None, max_scrolls=10, within_index=None) -> dict:
    try:
        backend, session, conn = build(config.load())
        conn.ensure()
        snap = actuator.scroll_until(backend, session, direction,
                                     text=text, selector=selector,
                                     max_scrolls=max_scrolls,
                                     within_i=within_index)
        return results.ok(capability="gesture.scroll_until", provider="adb", data=snap)
    except errors.PhonectlError as e:
        return results.err(e, **getattr(e, "lock_state", {}))


def policy_explain(build=_default_build, *, verb="tap", index=None, selector=None, x=None, y=None) -> dict:
    try:
        backend, session, conn = build(config.load())
        conn.ensure()
        snap = observer.observe(backend, session)
        if selector is not None:
            target = {"selector": selector}
        elif index is not None:
            target = {"i": index}
        elif x is not None:
            target = {"x": x, "y": y}
        else:
            target = {}
        return results.ok(capability="policy.explain", provider="adb", data=policy.explain(snap, verb, target, config.load()))
    except errors.PhonectlError as e:
        return results.err(e, **getattr(e, "lock_state", {}))


def audit_query(*, limit=20) -> dict:
    return results.ok(capability="audit.query", data={"entries": audit.read_entries(limit=limit)})


def stop() -> dict:
    audit.engage_stop("")
    return results.ok(capability="control.stop", data={"stopped": True})


# There is intentionally no `resume()` handler here: clearing the kill switch is a
# human-only, out-of-band action (Finding 1). The agent-facing MCP surface can
# engage STOP (phone_stop) but cannot clear it — resume via `phonectl resume` on
# the host or the companion notification/tile.


def _notifications_provider(build):
    from phonectl.providers.registry import ProviderRegistry
    backend, _session, _conn = build(config.load())
    registry = backend if isinstance(backend, ProviderRegistry) else ProviderRegistry([backend])
    return registry.for_capability("observe_notifications")


def notifications_list(build=_default_build, *, package=None) -> dict:
    try:
        provider = _notifications_provider(build)
        if provider is None:
            return results.err(
                errors.CapabilityUnavailableError("observe_notifications not available"),
                capability="notifications.list",
                user_action="Install the phonectl companion APK or Termux:API.",
            )
        items = provider.list(package=package)
        return results.ok(capability="notifications.list", data=items)
    except errors.PhonectlError as e:
        return results.err(e)


def notifications_wait(build=_default_build, *, package=None, title_contains=None,
                       text_contains=None, timeout=30) -> dict:
    try:
        provider = _notifications_provider(build)
        if provider is None:
            return results.err(
                errors.CapabilityUnavailableError("observe_notifications not available"),
                capability="notifications.wait",
                user_action="Install the phonectl companion APK or Termux:API.",
            )
        match = provider.wait(package=package, title_contains=title_contains,
                              text_contains=text_contains, timeout=float(timeout))
        return results.ok(capability="notifications.wait", data=match)
    except errors.PhonectlError as e:
        return results.err(e)


def notifications_reply(build=_default_build, *, key, text, confirm=False, dry_run=False) -> dict:
    try:
        provider = _notifications_provider(build)
        if provider is None:
            return results.err(
                errors.CapabilityUnavailableError("observe_notifications not available"),
                capability="notifications.reply",
                user_action="Install the phonectl companion APK.",
            )
        cfg = _action_cfg(dry_run)
        return runtime.run_action(
            "notifications_reply",
            lambda b, s: provider.reply(key, text),
            {"key": key, "text": f"<{len(text)} chars>"},
            build=build, yes=confirm, cfg=cfg,
        )
    except errors.PhonectlError as e:
        return results.err(e)


def notifications_dismiss(build=_default_build, *, key, confirm=False, dry_run=False) -> dict:
    try:
        provider = _notifications_provider(build)
        if provider is None:
            return results.err(
                errors.CapabilityUnavailableError("observe_notifications not available"),
                capability="notifications.dismiss",
                user_action="Install the phonectl companion APK.",
            )
        cfg = _action_cfg(dry_run)
        return runtime.run_action(
            "notifications_dismiss",
            lambda b, s: provider.dismiss(key),
            {"key": key},
            build=build, yes=confirm, cfg=cfg,
        )
    except errors.PhonectlError as e:
        return results.err(e)


def ocr_screen(build=_default_build, *, min_confidence=0.0) -> dict:
    try:
        backend, _session, _conn = build(config.load())
        registry = _as_registry(backend)
        p = registry.for_capability("observe_ocr")
        if p is None:
            return results.err(
                errors.CapabilityUnavailableError("observe_ocr not available"),
                capability="ocr.screen",
                user_action="Install 'tesseract' (pkg install tesseract) or the companion ML-Kit OCR provider.",
            )
        data = p.ocr_screen(registry, min_confidence=float(min_confidence))
        return results.ok(capability="ocr.screen", provider=type(p).__name__, data=data)
    except errors.PhonectlError as e:
        return results.err(e, **getattr(e, "lock_state", {}))


def _schema(**props):
    return {"type": "object", "properties": props}


def _macro_validate_mcp(macro, **_):
    from phonectl.macro import schema as _ms
    errs = _ms.validate(macro)
    return results.ok(capability="macro.validate", data={"valid": not errs, "errors": errs})


def _macro_run_mcp(macro, *, build, yes=False, **_):
    from phonectl.macro import schema as _ms
    from phonectl.macro.engine import Engine
    from phonectl.cli import macro_fn_for
    macro_obj = _ms.parse(macro)
    eng = Engine(build=build, fn_for=macro_fn_for)
    return eng.run(macro_obj, yes=yes)


def _macro_status_mcp(limit=10, **_):
    from phonectl.macro import records as _mrec
    return results.ok(capability="macro.status",
                      data={"runs": _mrec.read(kind="macro_run", limit=limit)})


_OBJ = {"type": "object", "properties": {}}
_TARGET_PROPS = {
    "index": {"type": "integer"},
    "selector": {"type": "object"},
    "x": {"type": "integer"},
    "y": {"type": "integer"},
    "expected_hash": {"type": "string"},
    "stale_ok": {"type": "boolean"},
    "dry_run": {"type": "boolean"},
    "confirm": {"type": "boolean"},
    "reason": {"type": "string"},
    "idempotency_key": {"type": "string"},
}

TOOLS = {
    "phone_observe_ui": {"description": "Observe the foreground UI as structured JSON.", "schema": _schema(tree={"type": "boolean"}, relations={"type": "boolean"}, screenshot={"type": "boolean"}), "handler": observe_ui, "needs_build": True},
    "phone_find": {"description": "Resolve a selector against a fresh snapshot.", "schema": _schema(selector={"type": "object"}), "handler": find, "needs_build": True},
    "phone_capabilities": {"description": "List provider capabilities.", "schema": _OBJ, "handler": capabilities, "needs_build": True},
    "phone_tap": {"description": "Tap by index, selector, or coordinates.", "schema": _schema(**_TARGET_PROPS), "handler": tap, "needs_build": True},
    "phone_type": {"description": "Type text into the focused field.", "schema": _schema(text={"type": "string"}, dry_run={"type": "boolean"}, confirm={"type": "boolean"}, reason={"type": "string"}, idempotency_key={"type": "string"}), "handler": type_text, "needs_build": True},
    "phone_swipe": {"description": "Swipe between two points.", "schema": _schema(x1={"type": "integer"}, y1={"type": "integer"}, x2={"type": "integer"}, y2={"type": "integer"}, dry_run={"type": "boolean"}, confirm={"type": "boolean"}), "handler": swipe, "needs_build": True},
    "phone_key": {"description": "Send a key event.", "schema": _schema(keycode={"type": "string"}, dry_run={"type": "boolean"}, confirm={"type": "boolean"}), "handler": key, "needs_build": True},
    "phone_launch": {"description": "Launch an app by package.", "schema": _schema(package={"type": "string"}, dry_run={"type": "boolean"}, confirm={"type": "boolean"}), "handler": launch, "needs_build": True},
    "phone_policy_explain": {"description": "Explain the risk/policy decision for an action.", "schema": _schema(verb={"type": "string"}, **_TARGET_PROPS), "handler": policy_explain, "needs_build": True},
    "phone_audit_query": {"description": "Read recent redacted audit entries.", "schema": _schema(limit={"type": "integer"}), "handler": audit_query, "needs_build": False},
    "phone_stop": {"description": "Engage the emergency stop.", "schema": _OBJ, "handler": stop, "needs_build": False},
    # phone_resume is intentionally NOT exposed: clearing the kill switch is a
    # human-only, out-of-band action (Finding 1). Resume via `phonectl resume`
    # on the host or the companion notification/tile.
    # Phase 3.2: clipboard, intent, packages
    "phone_clipboard_read": {"description": "Read clipboard text (requires Termux:API).", "schema": _OBJ, "handler": clipboard_read, "needs_build": True},
    "phone_clipboard_write": {"description": "Write text to the clipboard.", "schema": _schema(text={"type": "string"}, dry_run={"type": "boolean"}, confirm={"type": "boolean"}), "handler": clipboard_write, "needs_build": True},
    "phone_clipboard_clear": {"description": "Clear the clipboard.", "schema": _schema(dry_run={"type": "boolean"}, confirm={"type": "boolean"}), "handler": clipboard_clear, "needs_build": True},
    "phone_intent_start": {"description": "Start an activity via am start.", "schema": _schema(action={"type": "string"}, data={"type": "string"}, component={"type": "string"}, extras={"type": "object"}, dry_run={"type": "boolean"}, confirm={"type": "boolean"}), "handler": intent_start, "needs_build": True},
    "phone_intent_broadcast": {"description": "Send an intent broadcast via am broadcast.", "schema": _schema(action={"type": "string"}, extras={"type": "object"}, dry_run={"type": "boolean"}, confirm={"type": "boolean"}), "handler": intent_broadcast, "needs_build": True},
    "phone_packages_list": {"description": "List installed packages.", "schema": _schema(include_system={"type": "boolean"}), "handler": packages_list, "needs_build": True},
    "phone_packages_resolve": {"description": "Resolve package metadata.", "schema": _schema(package={"type": "string"}), "handler": packages_resolve, "needs_build": True},
    "phone_packages_launch": {"description": "Launch an app by package name.", "schema": _schema(package={"type": "string"}, dry_run={"type": "boolean"}, confirm={"type": "boolean"}), "handler": packages_launch, "needs_build": True},
    "phone_packages_stop": {"description": "Force-stop a package (high risk).", "schema": _schema(package={"type": "string"}, dry_run={"type": "boolean"}, confirm={"type": "boolean"}), "handler": packages_stop, "needs_build": True},
    "phone_packages_clear": {"description": "Clear package data (critical risk; confirm=true required).", "schema": _schema(package={"type": "string"}, confirm={"type": "boolean"}, dry_run={"type": "boolean"}), "handler": packages_clear, "needs_build": True},
    # Phase 3.3: gesture primitives
    "phone_named_swipe": {"description": "Swipe in a named direction (up/down/left/right) with density-aware scaling.", "schema": _schema(direction={"type": "string"}, distance_pct={"type": "number"}, ms={"type": "integer"}, within_index={"type": "integer"}, dry_run={"type": "boolean"}, confirm={"type": "boolean"}), "handler": named_swipe, "needs_build": True},
    "phone_long_press": {"description": "Long-press by element index, selector, or coordinates.", "schema": _schema(index={"type": "integer"}, selector={"type": "object"}, x={"type": "integer"}, y={"type": "integer"}, duration_ms={"type": "integer"}, dry_run={"type": "boolean"}, confirm={"type": "boolean"}), "handler": long_press, "needs_build": True},
    "phone_double_tap": {"description": "Double-tap by element index, selector, or coordinates.", "schema": _schema(index={"type": "integer"}, selector={"type": "object"}, x={"type": "integer"}, y={"type": "integer"}, interval_ms={"type": "integer"}, dry_run={"type": "boolean"}, confirm={"type": "boolean"}), "handler": double_tap, "needs_build": True},
    "phone_drag": {"description": "Drag from (x1,y1) to (x2,y2) using a long-duration swipe.", "schema": _schema(x1={"type": "integer"}, y1={"type": "integer"}, x2={"type": "integer"}, y2={"type": "integer"}, duration_ms={"type": "integer"}, dry_run={"type": "boolean"}, confirm={"type": "boolean"}), "handler": drag, "needs_build": True},
    "phone_fling": {"description": "Fling in a direction with velocity-scaled speed.", "schema": _schema(direction={"type": "string"}, dry_run={"type": "boolean"}, confirm={"type": "boolean"}), "handler": fling, "needs_build": True},
    "phone_scroll": {"description": "Scroll in a direction, optionally within a scrollable container.", "schema": _schema(direction={"type": "string"}, within_index={"type": "integer"}, distance_pct={"type": "number"}, dry_run={"type": "boolean"}, confirm={"type": "boolean"}), "handler": scroll, "needs_build": True},
    "phone_scroll_until": {"description": "Scroll until text or selector appears, or max_scrolls is exhausted.", "schema": _schema(direction={"type": "string"}, text={"type": "string"}, selector={"type": "object"}, max_scrolls={"type": "integer"}, within_index={"type": "integer"}), "handler": scroll_until_mcp, "needs_build": True},
    # Phase 4.2: notifications (companion NotificationListenerService or Termux:API read-only)
    "phone_notifications_list": {"description": "List current notifications; each item includes can_reply/can_dismiss flags.", "schema": _schema(package={"type": "string"}), "handler": notifications_list, "needs_build": True},
    "phone_notifications_wait": {"description": "Poll until a matching notification appears or timeout elapses.", "schema": _schema(package={"type": "string"}, title_contains={"type": "string"}, text_contains={"type": "string"}, timeout={"type": "integer"}), "handler": notifications_wait, "needs_build": True},
    "phone_notifications_reply": {"description": "Reply to a notification via RemoteInput (high-risk; companion required).", "schema": _schema(key={"type": "string"}, text={"type": "string"}, confirm={"type": "boolean"}, dry_run={"type": "boolean"}), "handler": notifications_reply, "needs_build": True},
    "phone_notifications_dismiss": {"description": "Dismiss a notification (companion required).", "schema": _schema(key={"type": "string"}, confirm={"type": "boolean"}, dry_run={"type": "boolean"}), "handler": notifications_dismiss, "needs_build": True},
    # Phase 4.4: optional OCR fallback (use only when phone_observe_ui / phone_find return nothing — canvas/WebView/game surfaces)
    "phone_ocr_screen": {
        "description": (
            "OCR the current screen and return text regions with bounds and confidence. "
            "Use ONLY as a fallback when phone_observe_ui or phone_find return no elements "
            "(custom-drawn/canvas/WebView surfaces). Requires tesseract on PATH or the companion ML-Kit OCR provider."
        ),
        "schema": _schema(min_confidence={"type": "number"}),
        "handler": ocr_screen,
        "needs_build": True,
    },
    # Phase 3.4: structured extraction
    "phone_extract_list": {"description": "Extract rows from a scrollable list container.", "schema": _schema(container_index={"type": "integer"}), "handler": extract_list, "needs_build": True},
    "phone_extract_form": {"description": "Extract form fields with associated labels.", "schema": _OBJ, "handler": extract_form, "needs_build": True},
    "phone_get_focused_field": {"description": "Return the currently focused text field, or null.", "schema": _OBJ, "handler": get_focused_field, "needs_build": True},
    "phone_find_text": {"description": "Find elements whose text matches a regex pattern (re.search).", "schema": _schema(pattern={"type": "string"}), "handler": find_text, "needs_build": True},
    # Phase 6.1: macro engine
    "phone_macro_validate": {"description": "Validate a macro document; returns {valid, errors}.", "schema": _schema(macro={"type": "object"}), "handler": _macro_validate_mcp, "needs_build": False},
    "phone_macro_run": {"description": "Run a declarative macro document; returns run envelope.", "schema": _schema(macro={"type": "object"}, yes={"type": "boolean"}), "handler": _macro_run_mcp, "needs_build": True},
    "phone_macro_status": {"description": "List recent macro runs from runs.jsonl.", "schema": _schema(limit={"type": "integer"}), "handler": _macro_status_mcp, "needs_build": False},
}


def call_tool(name, args, build=_default_build) -> dict:
    entry = TOOLS.get(name)
    if entry is None:
        return results.err(("unknown_tool", f"no such tool: {name}"))
    try:
        if entry["needs_build"]:
            return entry["handler"](build, **args)
        return entry["handler"](**args)
    except errors.PhonectlError as e:
        return results.err(e, **getattr(e, "lock_state", {}))


def _annotation_for_schema(prop: dict):
    typ = prop.get("type")
    if typ == "boolean":
        return bool
    if typ == "integer":
        return int
    if typ == "object":
        return dict
    if typ == "string":
        return str
    return inspect.Signature.empty


def _make_tool(name, build):
    entry = TOOLS[name]

    def tool(**kwargs):
        return call_tool(name, kwargs, build=build)

    handler_sig = inspect.signature(entry["handler"])
    params = []
    for param_name, prop in entry["schema"].get("properties", {}).items():
        handler_param = handler_sig.parameters.get(param_name)
        default = inspect.Signature.empty
        if handler_param is not None and handler_param.default is not inspect.Signature.empty:
            default = handler_param.default
        params.append(
            inspect.Parameter(
                param_name,
                inspect.Parameter.KEYWORD_ONLY,
                default=default,
                annotation=_annotation_for_schema(prop),
            )
        )
    tool.__name__ = name
    tool.__qualname__ = name
    tool.__doc__ = entry["description"]
    tool.__signature__ = inspect.Signature(params)
    return tool


def _register(app, build=_default_build) -> list[str]:
    names = []
    for name, entry in TOOLS.items():
        app.tool(name=name, description=entry["description"])(_make_tool(name, build))
        names.append(name)
    return names


def serve(build=_default_build) -> None:
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as e:
        raise errors.CapabilityUnavailableError("MCP SDK not installed; pip install phonectl[mcp]") from e
    app = FastMCP("phonectl")
    _register(app, build=build)
    app.run()
