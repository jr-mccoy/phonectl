"""Structured-result MCP server handlers, registry, and optional transport."""
from __future__ import annotations

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
    (config.config_dir() / "STOP").write_text("")
    return results.ok(capability="control.stop", data={"stopped": True})


def resume() -> dict:
    p = config.config_dir() / "STOP"
    if p.exists():
        p.unlink()
    return results.ok(capability="control.resume", data={"stopped": False})


def _schema(**props):
    return {"type": "object", "properties": props}


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
    "phone_resume": {"description": "Clear the emergency stop.", "schema": _OBJ, "handler": resume, "needs_build": False},
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


def _make_tool(name, build):
    def tool(**kwargs):
        return call_tool(name, kwargs, build=build)

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
