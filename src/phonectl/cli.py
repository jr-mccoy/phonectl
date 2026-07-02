from __future__ import annotations

import argparse
import json
from phonectl import (
    __version__,
    actuator,
    audit,
    config,
    errors,
    observer,
    policy,
    results,
    runtime,
    setup as setup_mod,
    diagnostics,
    ui_parser,
)
from phonectl.adb_backend import AdbBackend
from phonectl.providers.registry import ProviderRegistry
from phonectl.providers.termux import TermuxApiProvider
from phonectl.providers.accessibility import AccessibilityProvider  # noqa: F401
from phonectl.providers.notifications import NotificationsProvider
from phonectl.providers.transport import SocketTransport
from phonectl.providers.ocr import OcrProvider
from phonectl import trust
from phonectl.session import Session
from phonectl.connection import Connection, GUIDANCE
from phonectl.daemon import discovery as _daemon_discovery
from phonectl.daemon.client import DaemonClient


def macro_fn_for(step, scopes):
    from phonectl import actuator, errors
    from phonectl.macro import variables as _mvars

    def _interp(v):
        return _mvars.interpolate(v, scopes) if isinstance(v, str) else v

    verb = step["type"]
    target = dict(step.get("target", {}))
    if verb == "tap":
        if "i" in target:
            i = target["i"]
            return lambda b, s: actuator.tap(b, s, i=i)
        if "selector" in target:
            sel = target["selector"]
            return lambda b, s: actuator.tap(b, s, selector=sel)
        return lambda b, s: actuator.tap(b, s, x=target["x"], y=target["y"])
    if verb in ("type", "set_text"):
        text = _interp(step.get("text", target.get("text", "")))
        return lambda b, s: actuator.type_text(b, s, text)
    if verb == "launch":
        pkg = _interp(step["package"])
        return lambda b, s: actuator.launch(b, s, pkg)
    if verb == "key":
        kc = step["keycode"]
        return lambda b, s: actuator.key(b, s, kc)
    if verb == "swipe":
        return lambda b, s: actuator.swipe(b, s, **target)
    raise errors.MacroValidationError(f"no macro fn mapping for verb {verb!r}")


def _daemon_client(cfg):
    def _ping(host, port):
        return DaemonClient(host, port).is_running()
    info = _daemon_discovery.discover(ping=_ping)
    if info is None:
        return None
    return DaemonClient.from_discovery(info)


def _dispatch(method, params, in_process_fn, *, cfg=None, async_job=False, detach=False):
    client = _daemon_client(cfg)
    if client is None:
        return in_process_fn()
    if not async_job:
        return client.call(method, params)
    if detach:
        return client.call(method, params)          # returns {job_id, status: accepted}
    cfg = cfg or config.load()
    return client.submit_and_wait(
        method, params,
        overall_timeout=cfg.get("act_timeout", 60.0),
        poll_interval=cfg.get("poll_interval", 0.5),
    )


def _act_params(args, verb, target):
    import uuid
    p = {"verb": verb, "target": target,
         "yes": getattr(args, "yes", False),
         "request_id": getattr(args, "request_id", None) or uuid.uuid4().hex,
         "idempotency_key": getattr(args, "idempotency_key", None) or uuid.uuid4().hex}
    if isinstance(target, dict):
        p.update({k: v for k, v in target.items() if k not in p})
    return p


def _make_backend(cfg) -> AdbBackend:
    return AdbBackend(serial=cfg.get("serial"))


def _make_companion_transport(cfg):
    port = cfg.get("companion_port")
    if not port:
        return None
    # companion_token pairs the CLI with the APK (Finding 2): the token is shown in
    # the companion UI and pasted into config; loopback alone is not a trust boundary.
    return SocketTransport(cfg.get("companion_host", "127.0.0.1"), int(port),
                           token=cfg.get("companion_token"))


def _make_termux_provider():
    p = TermuxApiProvider()
    return p if p.is_available() else None


def _make_accessibility_provider():
    cfg = config.load()
    transport = _make_companion_transport(cfg)
    if transport is None or not transport.ping():
        return None
    hs = trust.negotiate(transport)
    return trust.GatedProvider(AccessibilityProvider(transport), hs.capabilities)


def _make_notifications_provider():
    termux = _make_termux_provider()
    transport = _make_companion_transport(config.load())
    hs = trust.negotiate(transport) if transport is not None else None
    p = NotificationsProvider(transport=transport, termux=termux)
    if not p.is_available():
        return None
    if hs is not None and hs.capabilities:
        return trust.GatedProvider(p, hs.capabilities)
    return p


def _make_ocr_provider():
    cfg = config.load()
    transport = _make_companion_transport(cfg)
    hs = trust.negotiate(transport) if transport is not None else None
    p = OcrProvider(transport=transport)
    if not p.is_available():
        return None
    if hs is not None and hs.capabilities and not p._local_ok():
        return trust.GatedProvider(p, hs.capabilities)
    return p


def build_runtime(cfg, backend=None):
    adb = backend or _make_backend(cfg)
    if isinstance(adb, ProviderRegistry):
        registry = adb
    else:
        providers = [p for p in [
            _make_accessibility_provider(),
            _make_notifications_provider(),
            _make_termux_provider(),
            adb,
            _make_ocr_provider(),   # appended last — lowest priority, observe_ocr only
        ] if p is not None]
        registry = ProviderRegistry(providers)
    session = Session()
    conn = Connection(registry, cfg)
    return registry, session, conn


def _emit(snap) -> None:
    print(json.dumps(snap, indent=2))


def _cmd_observe(args):
    cfg = config.load()

    def in_process():
        backend, session, conn = build_runtime(cfg)
        conn.ensure()
        snap = observer.observe(backend, session, screenshot=args.screenshot,
                                snap_path=args.screenshot_path, tree=args.tree,
                                relations=args.relations)
        provider = getattr(backend, "last_used", None) or "adb"
        return results.ok(capability="ui.observe", provider=provider, data=snap)

    env = _dispatch("observe", {
        "screenshot": args.screenshot, "snap_path": args.screenshot_path,
        "tree": args.tree, "relations": args.relations,
    }, in_process, cfg=cfg, async_job=True)
    if not env.get("ok"):
        if getattr(args, "json", False):
            print(json.dumps(env, indent=2))
        else:
            print(f"phonectl: {env['error']['message']}")
        return 1
    if getattr(args, "json", False):
        print(json.dumps(env, indent=2))
    else:
        _emit(env["data"])
    return 0


def _exit_code(env) -> int:
    """Uniform envelope→exit-code mapping (README: stopped→2, confirm→3)."""
    if env.get("ok"):
        return 0
    code = env.get("error", {}).get("code")
    return {"stopped": 2, "confirmation_required": 3}.get(code, 1)


def _do_action(args, verb, fn, target):
    cfg = config.load()

    def in_process():
        return runtime.run_action(
            verb,
            fn,
            target,
            build=build_runtime,
            yes=getattr(args, "yes", False),
            cfg=cfg,
            request_id=getattr(args, "request_id", None),
            idempotency_key=getattr(args, "idempotency_key", None),
        )

    detach = getattr(args, "detach", False)
    env = _dispatch("act", _act_params(args, verb, target), in_process,
                    cfg=cfg, async_job=True, detach=detach)
    if detach and env.get("ok") and "job_id" in env.get("data", {}):
        print(f"phonectl: job {env['data']['job_id']} (use: phonectl job {env['data']['job_id']})")
        return 0
    if env["ok"]:
        if getattr(args, "json", False):
            print(json.dumps(env, indent=2))
        elif env.get("dry_run"):
            print(f"phonectl: dry-run {verb} {target} (not executed)")
        else:
            _emit(env["data"])
        return 0
    if getattr(args, "json", False):
        print(json.dumps(env, indent=2))
    else:
        print(f"phonectl: {env['error']['message']}")
    return _exit_code(env)


def _selector_from_args(args):
    sel = None
    if getattr(args, "selector", None):
        sel = json.loads(args.selector)
    elif getattr(args, "text", None) is not None:
        sel = {"text": args.text}
    elif getattr(args, "id", None) is not None:
        sel = {"resource_id": args.id}
    if sel is not None and getattr(args, "nth", None) is not None:
        sel = dict(sel)
        sel["nth_match"] = args.nth
    return sel


def _cmd_tap(args):
    sel = _selector_from_args(args)
    if sel is not None:
        return _do_action(
            args, "tap",
            lambda b, s: actuator.tap(b, s, selector=sel, expected_hash=args.expected_hash, stale_ok=args.stale_ok),
            {"selector": sel},
        )
    if args.index is not None:
        return _do_action(args, "tap", lambda b, s: actuator.tap(b, s, i=args.index, expected_hash=args.expected_hash, stale_ok=args.stale_ok),
                          {"i": args.index})
    x, y = args.xy
    return _do_action(args, "tap", lambda b, s: actuator.tap(b, s, x=x, y=y, expected_hash=args.expected_hash, stale_ok=args.stale_ok),
                      {"x": x, "y": y})


def _cmd_type(args):
    return _do_action(args, "type", lambda b, s: actuator.type_text(b, s, args.text),
                      {"text": f"<{len(args.text)} chars>"})


_DIRECTIONS = {"up", "down", "left", "right"}


def _parse_within(within_str):
    if within_str is None:
        return None
    return int(within_str.split("=")[-1])


def _cmd_swipe(args):
    swipe_args = args.swipe_args
    if len(swipe_args) == 1 and swipe_args[0] in _DIRECTIONS:
        direction = swipe_args[0]
        within_i = _parse_within(getattr(args, "within", None))
        distance_pct = getattr(args, "distance_pct", 0.5)
        return _do_action(
            args, "named_swipe",
            lambda b, s: actuator.named_swipe(b, s, direction,
                                               distance_pct=distance_pct,
                                               within_i=within_i),
            {"direction": direction, "distance_pct": distance_pct, "within_i": within_i},
        )
    if len(swipe_args) == 4:
        try:
            x1, y1, x2, y2 = [int(a) for a in swipe_args]
        except ValueError:
            print("phonectl: swipe coords must be integers")
            return 2
        return _do_action(args, "swipe",
                          lambda b, s: actuator.swipe(b, s, x1, y1, x2, y2),
                          {"coords": [x1, y1, x2, y2]})
    print("phonectl: swipe requires a direction (up/down/left/right) or x1 y1 x2 y2")
    return 2


def _cmd_long_press(args):
    sel = _selector_from_args(args)
    i = getattr(args, "i", None)
    x = getattr(args, "x", None)
    y = getattr(args, "y", None)
    return _do_action(
        args, "long_press",
        lambda b, s: actuator.long_press(b, s, i=i, x=x, y=y, selector=sel,
                                          duration_ms=args.duration_ms),
        {"i": i, "x": x, "y": y, "selector": sel, "duration_ms": args.duration_ms},
    )


def _cmd_double_tap(args):
    sel = _selector_from_args(args)
    i = getattr(args, "i", None)
    x = getattr(args, "x", None)
    y = getattr(args, "y", None)
    return _do_action(
        args, "double_tap",
        lambda b, s: actuator.double_tap(b, s, i=i, x=x, y=y, selector=sel,
                                          interval_ms=args.interval_ms),
        {"i": i, "x": x, "y": y, "selector": sel, "interval_ms": args.interval_ms},
    )


def _cmd_drag(args):
    x1, y1, x2, y2 = args.x1, args.y1, args.x2, args.y2
    return _do_action(
        args, "drag",
        lambda b, s: actuator.drag(b, s, x1, y1, x2, y2, args.duration_ms),
        {"coords": [x1, y1, x2, y2], "duration_ms": args.duration_ms},
    )


def _cmd_fling(args):
    return _do_action(
        args, "fling",
        lambda b, s: actuator.fling(b, s, args.direction),
        {"direction": args.direction},
    )


def _cmd_scroll(args):
    within_i = _parse_within(getattr(args, "within", None))
    return _do_action(
        args, "scroll",
        lambda b, s: actuator.scroll(b, s, args.direction, within_i=within_i),
        {"direction": args.direction, "within_i": within_i},
    )


def _cmd_scroll_until(args):
    within_i = _parse_within(getattr(args, "within", None))
    direction = getattr(args, "direction", "down")
    text = getattr(args, "text", None)
    sel = _selector_from_args(args)
    max_scrolls = getattr(args, "max", 10)
    if text is None and sel is None:
        print("phonectl: scroll-until requires --text or --selector")
        return 2
    return _do_action(
        args, "scroll_until",
        lambda b, s: actuator.scroll_until(b, s, direction,
                                            text=text, selector=sel,
                                            max_scrolls=max_scrolls,
                                            within_i=within_i,
                                            halt=audit.kill_switch_active),
        {"direction": direction, "text": text, "selector": sel,
         "max_scrolls": max_scrolls, "within_i": within_i},
    )


def _cmd_key(args):
    return _do_action(args, "key", lambda b, s: actuator.key(b, s, args.keycode),
                      {"key": args.keycode})


def _cmd_launch(args):
    return _do_action(args, "launch", lambda b, s: actuator.launch(b, s, args.package),
                      {"package": args.package})


def _cmd_wait_for(args):
    sel = _selector_from_args(args)
    if sel is None:
        print("phonectl: wait-for requires --text, --id, or --selector")
        return 2
    cfg = config.load()
    backend, session, conn = build_runtime(cfg)
    conn.ensure()
    if getattr(args, "selector", None) is not None or getattr(args, "nth", None) is not None:
        deadline = args.timeout
        snap = None
        while True:
            cur = observer.observe(backend, session, relations=True)
            if ui_parser.match_selector(cur["elements"], sel, cur.get("relations")):
                snap = cur
                break
            deadline -= 0.5
            if deadline <= 0:
                break
    else:
        snap = actuator.wait_for(backend, session, text=args.text, id=args.id,
                                 timeout=args.timeout)
    if snap is None:
        print("phonectl: wait-for timed out")
        return 1
    _emit(snap)
    return 0


def _cmd_reconnect(args):
    cfg = config.load()
    backend, session, conn = build_runtime(cfg)
    try:
        if args.port:
            conn.connect(args.port)
            if backend.get_state() != "device":
                print(f"phonectl: {GUIDANCE}")
                return 1
            print(f"phonectl: reconnected to {args.port}")
            return 0
        addr = conn.rediscover()
        print(f"phonectl: reconnected to {addr}")
        return 0
    except ConnectionError as e:
        print(f"phonectl: {e}")
        return 1


def _cmd_doctor(args):
    cfg = config.load()
    backend, session, conn = build_runtime(cfg)
    if getattr(args, "bundle", None):
        path = diagnostics.bundle(args.bundle, backend, cfg)
        print(f"phonectl: diagnostics bundle written to {path}")
        return 0
    try:
        conn.ensure()
    except ConnectionError as e:
        if getattr(args, "json", False):
            print(json.dumps(results.err(("connection_failed", str(e)),
                                         user_action="Start adb, connect a device, and authorize USB debugging."),
                             indent=2))
        else:
            print(str(e))
        return 1
    data = {
        "connected": True,
        "serial": backend.serial,
        "state": backend.get_state(),
        "capabilities": backend.capabilities(),
    }
    if getattr(args, "json", False):
        print(json.dumps(results.ok(provider="adb", data=data), indent=2))
    else:
        print(f"phonectl: connected (serial={backend.serial}, state={data['state']})")
    return 0


def _cmd_setup(args):
    cfg = config.load()
    backend, session, conn = build_runtime(cfg)
    return setup_mod.run_module(args.module, conn)


def _cmd_policy(args):
    if getattr(args, "policy_cmd", None) != "explain":
        print("phonectl: policy requires explain")
        return 2
    cfg = config.load()
    backend, session, conn = build_runtime(cfg)
    conn.ensure()
    snap = observer.observe(backend, session)
    target = _selector_from_args(args)
    if target is None and getattr(args, "index", None) is not None:
        target = {"i": args.index}
    out = policy.explain(snap, args.verb, target or {}, cfg)
    if getattr(args, "json", False):
        print(json.dumps(out, indent=2))
    else:
        print(f"phonectl: {args.verb} -> {out['decision']} (risk={out['risk_level']})")
    return 0


def _cmd_stop(args):
    """Engage the emergency kill switch from the host (out-of-band, human-driven)."""
    audit.engage_stop("stopped via CLI\n")
    env = results.ok(capability="control.stop", data={"stopped": True})
    if getattr(args, "json", False):
        print(json.dumps(env, indent=2))
    else:
        print("phonectl: STOP engaged (kill switch active)")
    return 0


def _cmd_resume(args):
    """Clear the emergency kill switch — deliberately host-only (Finding 1).

    Resume is not reachable from any agent surface (MCP tool or daemon RPC); a
    human must run this on the host (or use the companion notification/tile).
    """
    cleared = audit.clear_stop()
    env = results.ok(capability="control.resume", data={"stopped": False})
    if getattr(args, "json", False):
        print(json.dumps(env, indent=2))
    else:
        print("phonectl: STOP cleared" if cleared else "phonectl: no STOP was engaged")
    return 0


def _cmd_audit(args):
    if args.audit_cmd == "tail":
        for rec in audit.read_entries(limit=args.limit):
            print(json.dumps(rec))
        return 0
    if args.audit_cmd == "purge":
        n = audit.purge()
        print(f"phonectl: purged {n} audit record(s)")
        return 0
    if args.audit_cmd == "export":
        path = audit.export(args.path, redacted=not args.no_redact)
        print(f"phonectl: exported audit log to {path}")
        return 0
    print("phonectl: audit requires tail|purge|export")
    return 2


def _cmd_extract_list(args):
    cfg = config.load()
    backend, session, conn = build_runtime(cfg)
    conn.ensure()
    snap = observer.observe(backend, session, tree=False, relations=False)
    container_i = getattr(args, "container_i", None)
    rows = ui_parser.extract_list(snap["elements"], container_i=container_i)
    env = results.ok(capability="extraction.list",
                     provider=getattr(backend, "last_used", None) or "adb",
                     data={"rows": rows})
    if getattr(args, "json", False):
        print(json.dumps(env, indent=2))
    else:
        for row in rows:
            print(row.get("text") or row.get("content_desc") or f"i={row['i']}")
    return 0


def _cmd_extract_form(args):
    cfg = config.load()
    backend, session, conn = build_runtime(cfg)
    conn.ensure()
    snap = observer.observe(backend, session, tree=False, relations=True)
    fields = ui_parser.extract_form(snap["elements"], relations=snap.get("relations"))
    env = results.ok(capability="extraction.form",
                     provider=getattr(backend, "last_used", None) or "adb",
                     data={"fields": fields})
    if getattr(args, "json", False):
        print(json.dumps(env, indent=2))
    else:
        for f in fields:
            label = f.get("label") or ""
            print(f"{label}={f['value']!r}")
    return 0


def _cmd_find(args):
    import re
    cfg = config.load()
    backend, session, conn = build_runtime(cfg)
    ocr_pattern = getattr(args, "ocr_text", None)
    if ocr_pattern is not None:
        p = backend.for_capability("observe_ocr")
        if p is None:
            env = results.err(
                errors.CapabilityUnavailableError("OCR not available"),
                capability="extraction.find",
                user_action="Install 'tesseract' (pkg install tesseract) or the companion ML-Kit OCR provider.",
            )
            if getattr(args, "json", False):
                print(json.dumps(env, indent=2))
            else:
                print(f"phonectl: {env['error']['message']}")
            return 1
        ocr_data = p.ocr_screen(backend)
        rx = re.compile(ocr_pattern)
        matches = [r for r in ocr_data["regions"] if rx.search(r["text"])]
        env = results.ok(capability="extraction.find", provider="ocr", data={"matches": matches})
        if getattr(args, "json", False):
            print(json.dumps(env, indent=2))
        else:
            for m in matches:
                print(f"text={m['text']!r} bounds={m['bounds']}")
        return 0
    conn.ensure()
    snap = observer.observe(backend, session)
    matches = ui_parser.find_by_text_regex(snap["elements"], args.text_regex)
    env = results.ok(capability="extraction.find",
                     provider=getattr(backend, "last_used", None) or "adb",
                     data={"matches": matches})
    if getattr(args, "json", False):
        print(json.dumps(env, indent=2))
    else:
        for m in matches:
            print(f"i={m['i']} text={m['text']!r}")
    return 0


def _cmd_ocr_screen(args):
    cfg = config.load()
    backend, session, conn = build_runtime(cfg)
    p = backend.for_capability("observe_ocr")
    if p is None:
        env = results.err(
            errors.CapabilityUnavailableError("OCR not available"),
            capability="ocr.screen",
            user_action="Install 'tesseract' (pkg install tesseract) or the companion ML-Kit OCR provider.",
        )
        if getattr(args, "json", False):
            print(json.dumps(env, indent=2))
        else:
            print(f"phonectl: {env['error']['message']}")
        return 1
    data = p.ocr_screen(backend, min_confidence=getattr(args, "min_confidence", 0.0))
    env = results.ok(capability="ocr.screen", provider=type(p).__name__, data=data)
    if getattr(args, "json", False):
        print(json.dumps(env, indent=2))
    else:
        for r in data["regions"]:
            print(f"{r['text']}  {r['bounds']}")
    return 0


def _cmd_get_focused_field(args):
    cfg = config.load()
    backend, session, conn = build_runtime(cfg)
    conn.ensure()
    snap = observer.observe(backend, session)
    el = ui_parser.get_focused_field(snap["elements"])
    env = results.ok(capability="extraction.focused_field",
                     provider=getattr(backend, "last_used", None) or "adb",
                     data={"element": el})
    if getattr(args, "json", False):
        print(json.dumps(env, indent=2))
    elif el:
        print(f"i={el['i']} text={el.get('text', '')!r} hint={el.get('hint_text', '')!r}")
    else:
        print("phonectl: no focused field")
    return 0


def _cmd_get_text_in_region(args):
    cfg = config.load()
    backend, session, conn = build_runtime(cfg)
    conn.ensure()
    snap = observer.observe(backend, session)
    bounds = tuple(args.bounds)
    elements = ui_parser.get_visible_text_in_region(snap["elements"], bounds)
    env = results.ok(capability="extraction.text_in_region",
                     provider=getattr(backend, "last_used", None) or "adb",
                     data={"elements": elements})
    if getattr(args, "json", False):
        print(json.dumps(env, indent=2))
    else:
        for e in elements:
            print(e.get("text") or e.get("content_desc") or f"i={e['i']}")
    return 0


def _cmd_device_battery(args):
    cfg = config.load()
    registry, session, conn = build_runtime(cfg)
    p = registry.for_capability("device_battery")
    if p is None:
        env = results.err(
            errors.CapabilityUnavailableError("device_battery not available"),
            capability="device.battery",
            user_action="Install Termux:API and run 'phonectl setup termux-api'.",
        )
        if getattr(args, "json", False):
            print(json.dumps(env, indent=2))
        else:
            print(f"phonectl: {env['error']['message']}")
        return 1
    data = p.battery_status()
    env = results.ok(capability="device.battery", provider=type(p).__name__, data=data)
    if getattr(args, "json", False):
        print(json.dumps(env, indent=2))
    else:
        print(f"Battery: {data.get('percentage')}% ({data.get('status')})")
    return 0


def _cmd_device_wifi(args):
    cfg = config.load()
    registry, session, conn = build_runtime(cfg)
    p = registry.for_capability("device_wifi_info")
    if p is None:
        env = results.err(
            errors.CapabilityUnavailableError("device_wifi_info not available"),
            capability="device.wifi",
            user_action="Install Termux:API and run 'phonectl setup termux-api'.",
        )
        if getattr(args, "json", False):
            print(json.dumps(env, indent=2))
        else:
            print(f"phonectl: {env['error']['message']}")
        return 1
    data = p.wifi_info()
    env = results.ok(capability="device.wifi", provider=type(p).__name__, data=data)
    if getattr(args, "json", False):
        print(json.dumps(env, indent=2))
    else:
        print(f"WiFi: ssid={data.get('ssid')} ip={data.get('ip')}")
    return 0


def _cmd_tts_speak(args):
    cfg = config.load()
    registry, session, conn = build_runtime(cfg)
    p = registry.for_capability("tts_speak")
    if p is None:
        env = results.err(
            errors.CapabilityUnavailableError("tts_speak not available"),
            capability="tts.speak",
            user_action="Install Termux:API and run 'phonectl setup termux-api'.",
        )
        if getattr(args, "json", False):
            print(json.dumps(env, indent=2))
        else:
            print(f"phonectl: {env['error']['message']}")
        return 1
    p.tts_speak(args.text,
                language=getattr(args, "language", None),
                rate=getattr(args, "rate", None))
    env = results.ok(capability="tts.speak", provider=type(p).__name__)
    if getattr(args, "json", False):
        print(json.dumps(env, indent=2))
    return 0


def _cmd_trust_status(args):
    cfg = config.load()
    transport = _make_companion_transport(cfg)
    if transport is None:
        data = {"reachable": False, "version": 0, "capabilities": {}, "stopped": False}
    else:
        hs = trust.negotiate(transport)
        data = {"reachable": hs.reachable, "version": hs.version,
                "capabilities": hs.capabilities, "stopped": hs.stopped}
    env = results.ok(capability="trust.status", data=data)
    if getattr(args, "json", False):
        print(json.dumps(env, indent=2))
    else:
        print(f"companion: reachable={data['reachable']} version={data['version']} "
              f"stopped={data['stopped']}")
    return 0


def _cmd_mcp(args):
    from phonectl import mcp_server

    try:
        mcp_server.serve()
        return 0
    except errors.CapabilityUnavailableError as e:
        print(f"phonectl: {e}")
        return 1


def _cmd_clipboard_read(args):
    cfg = config.load()
    backend, session, conn = build_runtime(cfg)
    conn.ensure()
    from phonectl.providers.clipboard import ClipboardProvider
    env = ClipboardProvider(backend).read()
    if getattr(args, "json", False):
        print(json.dumps(env, indent=2))
    elif env["ok"]:
        print(env["data"]["text"])
    else:
        print(f"phonectl: {env['error']['message']}")
    return _exit_code(env)


def _cmd_clipboard_write(args):
    cfg = config.load()
    backend, session, conn = build_runtime(cfg)
    from phonectl.providers.clipboard import ClipboardProvider
    env = ClipboardProvider(backend).write(
        args.text, build=build_runtime, yes=getattr(args, "yes", False), cfg=cfg
    )
    if getattr(args, "json", False):
        print(json.dumps(env, indent=2))
    elif not env["ok"]:
        print(f"phonectl: {env['error']['message']}")
    return _exit_code(env)


def _cmd_clipboard_clear(args):
    cfg = config.load()
    backend, session, conn = build_runtime(cfg)
    from phonectl.providers.clipboard import ClipboardProvider
    env = ClipboardProvider(backend).clear(
        build=build_runtime, yes=getattr(args, "yes", False), cfg=cfg
    )
    if getattr(args, "json", False):
        print(json.dumps(env, indent=2))
    elif not env["ok"]:
        print(f"phonectl: {env['error']['message']}")
    return _exit_code(env)


def _cmd_intent_start(args):
    extras = {}
    for kv in getattr(args, "extra", []) or []:
        k, _, v = kv.partition("=")
        extras[k] = v
    cfg = config.load()
    backend, session, conn = build_runtime(cfg)
    from phonectl.providers.intents import IntentProvider
    env = IntentProvider(backend).start(
        action=getattr(args, "action", None),
        data=getattr(args, "data", None),
        component=getattr(args, "component", None),
        extras=extras or None,
        build=build_runtime,
        yes=getattr(args, "yes", False),
        cfg=cfg,
    )
    if getattr(args, "json", False):
        print(json.dumps(env, indent=2))
    elif not env["ok"]:
        print(f"phonectl: {env['error']['message']}")
    return _exit_code(env)


def _cmd_intent_broadcast(args):
    extras = {}
    for kv in getattr(args, "extra", []) or []:
        k, _, v = kv.partition("=")
        extras[k] = v
    cfg = config.load()
    backend, session, conn = build_runtime(cfg)
    from phonectl.providers.intents import IntentProvider
    env = IntentProvider(backend).broadcast(
        args.action,
        extras=extras or None,
        build=build_runtime,
        yes=getattr(args, "yes", False),
        cfg=cfg,
    )
    if getattr(args, "json", False):
        print(json.dumps(env, indent=2))
    elif not env["ok"]:
        print(f"phonectl: {env['error']['message']}")
    return _exit_code(env)


def _cmd_packages_list(args):
    cfg = config.load()
    backend, session, conn = build_runtime(cfg)
    conn.ensure()
    from phonectl.providers.packages import PackageProvider
    env = PackageProvider(backend).list_packages(include_system=getattr(args, "all", False))
    if getattr(args, "json", False):
        print(json.dumps(env, indent=2))
    elif env["ok"]:
        for pkg in env["data"]["packages"]:
            print(pkg)
    else:
        print(f"phonectl: {env['error']['message']}")
    return _exit_code(env)


def _cmd_packages_resolve(args):
    cfg = config.load()
    backend, session, conn = build_runtime(cfg)
    conn.ensure()
    from phonectl.providers.packages import PackageProvider
    env = PackageProvider(backend).resolve(args.package)
    if getattr(args, "json", False):
        print(json.dumps(env, indent=2))
    elif env["ok"]:
        d = env["data"]
        print(f"{d['package']}  version={d['version_name']}  activity={d['launch_activity']}")
    else:
        print(f"phonectl: {env['error']['message']}")
    return _exit_code(env)


def _cmd_packages_launch(args):
    cfg = config.load()
    backend, session, conn = build_runtime(cfg)
    from phonectl.providers.packages import PackageProvider
    env = PackageProvider(backend).launch(
        args.package, build=build_runtime, yes=getattr(args, "yes", False), cfg=cfg
    )
    if getattr(args, "json", False):
        print(json.dumps(env, indent=2))
    elif not env["ok"]:
        print(f"phonectl: {env['error']['message']}")
    return _exit_code(env)


def _cmd_packages_stop(args):
    cfg = config.load()
    backend, session, conn = build_runtime(cfg)
    from phonectl.providers.packages import PackageProvider
    env = PackageProvider(backend).stop(
        args.package, build=build_runtime, yes=getattr(args, "yes", False), cfg=cfg
    )
    if getattr(args, "json", False):
        print(json.dumps(env, indent=2))
    elif not env["ok"]:
        print(f"phonectl: {env['error']['message']}")
    return _exit_code(env)


def _cmd_packages_clear(args):
    cfg = config.load()
    backend, session, conn = build_runtime(cfg)
    from phonectl.providers.packages import PackageProvider
    env = PackageProvider(backend).clear(
        args.package, build=build_runtime, yes=getattr(args, "yes", False), cfg=cfg
    )
    if getattr(args, "json", False):
        print(json.dumps(env, indent=2))
    elif not env["ok"]:
        print(f"phonectl: {env['error']['message']}")
    return _exit_code(env)


def _cmd_notifications_list(args):
    cfg = config.load()
    registry, session, conn = build_runtime(cfg)
    provider = registry.for_capability("observe_notifications")
    if provider is None:
        env = results.err(
            errors.CapabilityUnavailableError("observe_notifications not available"),
            capability="notifications.list",
            user_action="Install the phonectl companion APK or Termux:API.",
        )
        if getattr(args, "json", False):
            print(json.dumps(env, indent=2))
        else:
            print(f"phonectl: {env['error']['message']}")
        return 1
    items = provider.list(package=getattr(args, "package", None))
    env = results.ok(capability="notifications.list", data=items)
    if getattr(args, "json", False):
        print(json.dumps(env, indent=2))
    else:
        for n in items:
            print(f"{n['package']}  title={n['title']!r}  can_reply={n['can_reply']}")
    return 0


def _cmd_notifications_wait(args):
    cfg = config.load()
    registry, session, conn = build_runtime(cfg)
    provider = registry.for_capability("observe_notifications")
    if provider is None:
        env = results.err(
            errors.CapabilityUnavailableError("observe_notifications not available"),
            capability="notifications.wait",
            user_action="Install the phonectl companion APK or Termux:API.",
        )
        if getattr(args, "json", False):
            print(json.dumps(env, indent=2))
        else:
            print(f"phonectl: {env['error']['message']}")
        return 1
    match = provider.wait(
        package=getattr(args, "package", None),
        title_contains=getattr(args, "title_contains", None),
        text_contains=getattr(args, "text_contains", None),
        timeout=getattr(args, "timeout", 30.0),
    )
    env = results.ok(capability="notifications.wait", data=match)
    if getattr(args, "json", False):
        print(json.dumps(env, indent=2))
    elif match:
        print(f"{match['package']}  title={match['title']!r}")
    else:
        print("phonectl: no matching notification found (timed out)")
    return 0


def _cmd_notifications_reply(args):
    cfg = config.load()
    registry, session, conn = build_runtime(cfg)
    provider = registry.for_capability("observe_notifications")
    if provider is None:
        env = results.err(
            errors.CapabilityUnavailableError("observe_notifications not available"),
            capability="notifications.reply",
            user_action="Install the phonectl companion APK.",
        )
        if getattr(args, "json", False):
            print(json.dumps(env, indent=2))
        else:
            print(f"phonectl: {env['error']['message']}")
        return 1
    key = args.key
    text = args.text
    env = runtime.run_action(
        "notifications_reply",
        lambda b, s: provider.reply(key, text),
        {"key": key, "text": f"<{len(text)} chars>"},
        build=build_runtime,
        yes=getattr(args, "yes", False),
    )
    if env["ok"]:
        if getattr(args, "json", False):
            print(json.dumps(env, indent=2))
        return 0
    if getattr(args, "json", False):
        print(json.dumps(env, indent=2))
    else:
        print(f"phonectl: {env['error']['message']}")
    return _exit_code(env)


def _cmd_notifications_dismiss(args):
    cfg = config.load()
    registry, session, conn = build_runtime(cfg)
    provider = registry.for_capability("observe_notifications")
    if provider is None:
        env = results.err(
            errors.CapabilityUnavailableError("observe_notifications not available"),
            capability="notifications.dismiss",
            user_action="Install the phonectl companion APK.",
        )
        if getattr(args, "json", False):
            print(json.dumps(env, indent=2))
        else:
            print(f"phonectl: {env['error']['message']}")
        return 1
    key = args.key
    env = runtime.run_action(
        "notifications_dismiss",
        lambda b, s: provider.dismiss(key),
        {"key": key},
        build=build_runtime,
        yes=getattr(args, "yes", False),
    )
    if env["ok"]:
        if getattr(args, "json", False):
            print(json.dumps(env, indent=2))
        return 0
    if getattr(args, "json", False):
        print(json.dumps(env, indent=2))
    else:
        print(f"phonectl: {env['error']['message']}")
    return _exit_code(env)


def _cmd_macro_validate(args):
    from phonectl.macro import loader, schema
    cfg = config.load()
    if not hasattr(args, "path") or args.path is None:
        print("usage: phonectl macro validate <path>")
        return 1
    doc = loader.load(args.path)
    errs = schema.validate(doc)
    env = results.ok(capability="macro.validate", data={"valid": not errs, "errors": errs})
    if getattr(args, "json", False):
        print(json.dumps(env, indent=2))
    else:
        if errs:
            for e in errs:
                print(f"  error: {e}")
            return 1
        print(f"macro valid: {doc.get('name', '?')}")
    return 0


def _cmd_macro_run(args):
    from phonectl.macro import loader, schema
    from phonectl.macro.engine import Engine, CancellationToken
    cfg = config.load()
    doc = loader.load(args.path)
    client = _daemon_client(cfg)
    if client is not None:
        env = client.call("macro_run", {"macro": doc, "yes": bool(getattr(args, "yes", False))})
    else:
        macro = schema.parse(doc)
        eng = Engine(build=build_runtime, cfg=cfg, fn_for=macro_fn_for)
        env = eng.run(macro, yes=bool(getattr(args, "yes", False)))
    if getattr(args, "json", False):
        print(json.dumps(env, indent=2))
    else:
        if env.get("ok"):
            d = env.get("data", {})
            print(f"macro run {d.get('run_id', '?')}: {d.get('outcome', 'ok')} ({d.get('steps_run', 0)} steps)")
        else:
            print(f"macro failed: {env.get('error', {}).get('message', '?')}")
    return 0 if env.get("ok") else 1


def _cmd_macro_status(args):
    cfg = config.load()
    from phonectl.macro import records as _mrec
    runs = _mrec.read(kind="macro_run", limit=10)
    env = results.ok(capability="macro.status", data={"runs": runs})
    if getattr(args, "json", False):
        print(json.dumps(env, indent=2))
    else:
        if not runs:
            print("no macro runs recorded")
        for r in runs:
            print(f"  {r['run_id']} {r.get('macro_name')} {r.get('outcome')}")
    return 0


def _cmd_macro_cancel(args):
    cfg = config.load()
    client = _daemon_client(cfg)
    if client is None:
        print("phonectl: no running daemon (cancel has no effect)")
        return 0
    env = client.call("macro_cancel", {"run_id": args.run_id})
    if getattr(args, "json", False):
        print(json.dumps(env, indent=2))
    else:
        c = env.get("data", {}).get("cancelled", False)
        print("cancelled" if c else "run not found")
    return 0


def _cmd_macro_enable(args):
    from phonectl.macro import loader, registry
    doc = loader.load(args.path)
    registry.enable(doc)
    env = results.ok(capability="macro.enable", data={"name": doc.get("name")})
    if getattr(args, "json", False):
        print(json.dumps(env, indent=2))
    else:
        print(f"macro enabled: {doc.get('name', '?')}")
    return 0


def _cmd_macro_disable(args):
    from phonectl.macro import registry
    registry.disable(args.name)
    env = results.ok(capability="macro.disable", data={"name": args.name})
    if getattr(args, "json", False):
        print(json.dumps(env, indent=2))
    else:
        print(f"macro disabled: {args.name}")
    return 0


def _cmd_macro_list(args):
    from phonectl.macro import registry
    macros = registry.all()
    env = results.ok(capability="macro.list", data={"macros": macros})
    if getattr(args, "json", False):
        print(json.dumps(env, indent=2))
    else:
        if not macros:
            print("no macros registered")
        for m in macros:
            state = "enabled" if m.get("enabled") else "disabled"
            print(f"  {m.get('name', '?')}  [{state}]")
    return 0


def _cmd_autonomy_grant(args):
    import sys
    import time
    from phonectl.macro import autonomy
    cfg = config.load()
    env = _dispatch(
        "autonomy_grant",
        {"macro": args.macro, "max_risk": args.max_risk,
         "scope": getattr(args, "scope", "all"),
         "expires_at": getattr(args, "expires", None)},
        lambda: results.ok(capability="autonomy.grant",
                           data=autonomy.grant(args.macro, max_risk=args.max_risk,
                                               scope=getattr(args, "scope", "all"),
                                               expires_at=getattr(args, "expires", None),
                                               now=time.time())),
        cfg=cfg,
    )
    if getattr(args, "json", False):
        print(json.dumps(env, indent=2))
    else:
        g = env.get("data", {})
        print(f"autonomy granted: {g.get('macro')} max_risk={g.get('max_risk')} id={g.get('id')}")
    return 0


def _cmd_autonomy_revoke(args):
    import time
    from phonectl.macro import autonomy
    cfg = config.load()
    env = _dispatch(
        "autonomy_revoke",
        {"macro": args.macro},
        lambda: (autonomy.revoke(macro=args.macro, now=time.time()),
                 results.ok(capability="autonomy.revoke", data={"revoked": True}))[1],
        cfg=cfg,
    )
    if getattr(args, "json", False):
        print(json.dumps(env, indent=2))
    else:
        print(f"autonomy revoked: {args.macro}")
    return 0


def _cmd_autonomy_list(args):
    import time
    from phonectl.macro import autonomy
    cfg = config.load()
    env = _dispatch(
        "autonomy_list",
        {},
        lambda: results.ok(capability="autonomy.list",
                           data={"grants": autonomy.list_live(now=time.time())}),
        cfg=cfg,
    )
    if getattr(args, "json", False):
        print(json.dumps(env, indent=2))
    else:
        grants = env.get("data", {}).get("grants", [])
        if not grants:
            print("no autonomy grants")
        for g in grants:
            print(f"  {g.get('macro')}  max_risk={g.get('max_risk')}")
    return 0


def _cmd_memory_show(args):
    from phonectl.macro import memory
    cfg = config.load()
    store = getattr(args, "store", None)
    env = _dispatch(
        "memory_show",
        {"store": store} if store else {},
        lambda: results.ok(capability="memory.show",
                           data=memory.read(store) if store else memory.export()),
        cfg=cfg,
    )
    if getattr(args, "json", False):
        print(json.dumps(env, indent=2))
    else:
        print(json.dumps(env.get("data", {}), indent=2))
    return 0


def _cmd_memory_export(args):
    from phonectl.macro import memory
    cfg = config.load()
    env = _dispatch(
        "memory_export",
        {},
        lambda: results.ok(capability="memory.export", data=memory.export()),
        cfg=cfg,
    )
    if getattr(args, "json", False):
        print(json.dumps(env, indent=2))
    else:
        data = env.get("data", {})
        file_arg = getattr(args, "file", None)
        if file_arg:
            import pathlib
            pathlib.Path(file_arg).write_text(json.dumps(data))
            print(f"exported to {file_arg}")
        else:
            print(json.dumps(data, indent=2))
    return 0


def _cmd_memory_delete(args):
    from phonectl.macro import memory
    cfg = config.load()
    store = getattr(args, "store", None)
    env = _dispatch(
        "memory_delete",
        {"store": store} if store else {},
        lambda: (memory.delete(store),
                 results.ok(capability="memory.delete", data={"deleted": True}))[1],
        cfg=cfg,
    )
    if getattr(args, "json", False):
        print(json.dumps(env, indent=2))
    else:
        print(f"memory deleted: {store or 'all'}")
    return 0


def _cmd_daemon(args):
    import signal
    cfg = config.load()
    sub = getattr(args, "daemon_cmd", None)
    if sub == "start":
        from phonectl.daemon.server import DaemonServer
        srv = DaemonServer(cfg, build=build_runtime)
        host, port = srv.bind()
        signal.signal(signal.SIGINT, lambda *a: srv.shutdown())
        print(f"phonectl daemon listening on {host}:{port} (Ctrl-C to stop)")
        try:
            srv.serve_forever()
        finally:
            srv.shutdown()
        return 0
    if sub == "status":
        client = _daemon_client(cfg)
        data = {"running": client is not None}
        if client is not None:
            st = client.call("status", {})
            data.update(st.get("data", {}))
        env = results.ok(capability="daemon.status", data=data)
        if getattr(args, "json", False):
            print(json.dumps(env, indent=2))
        else:
            print(f"daemon running={data['running']}")
        return 0
    if sub == "stop":
        client = _daemon_client(cfg)
        if client is None:
            _daemon_discovery.remove()
            print("phonectl: no running daemon")
            return 0
        client.call("shutdown", {})
        print("phonectl: shutdown signalled")
        return 0
    print("usage: phonectl daemon {start|status|stop}")
    return 1


def _cmd_job(args):
    cfg = config.load()
    client = _daemon_client(cfg)
    if client is None:
        print("phonectl: no running daemon")
        return 1
    import time as _t
    deadline = _t.monotonic() + (cfg.get("act_timeout", 60.0) if getattr(args, "wait", False) else 0.0)
    while True:
        env = client.call("job_poll", {"job_id": args.job_id})
        if not env.get("ok"):
            print(json.dumps(env, indent=2) if getattr(args, "json", False) else f"phonectl: {env['error']['message']}")
            return 1
        status = env["data"]["status"]
        if status in ("done", "error") or not getattr(args, "wait", False) or _t.monotonic() >= deadline:
            break
        _t.sleep(cfg.get("poll_interval", 0.5))
    if getattr(args, "json", False):
        print(json.dumps(env, indent=2))
    else:
        print(f"phonectl: job {args.job_id} status={env['data']['status']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="phonectl")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd")

    def _action_flags(sp):
        sp.add_argument("--yes", action="store_true")
        sp.add_argument("--json", action="store_true")
        sp.add_argument("--request-id", default=None)
        sp.add_argument("--idempotency-key", default=None)
        sp.add_argument("--detach", action="store_true",
                        help="submit the action and print its job id instead of waiting")

    o = sub.add_parser("observe")
    o.add_argument("--screenshot", action="store_true")
    o.add_argument("--screenshot-path", default=None)
    o.add_argument("--json", action="store_true")
    o.add_argument("--tree", action="store_true")
    o.add_argument("--relations", action="store_true")
    o.set_defaults(func=_cmd_observe)

    t = sub.add_parser("tap")
    g = t.add_mutually_exclusive_group(required=True)
    g.add_argument("--index", type=int)
    g.add_argument("--xy", nargs=2, type=int, metavar=("X", "Y"))
    g.add_argument("--selector")
    g.add_argument("--text")
    g.add_argument("--id")
    _action_flags(t)
    t.add_argument("--nth", type=int)
    t.add_argument("--expected-hash")
    t.add_argument("--stale-ok", action="store_true")
    t.set_defaults(func=_cmd_tap)

    ty = sub.add_parser("type")
    ty.add_argument("text")
    _action_flags(ty)
    ty.set_defaults(func=_cmd_type)

    sw = sub.add_parser("swipe")
    sw.add_argument("swipe_args", nargs="+", metavar="DIRECTION_OR_COORDS",
                    help="up|down|left|right  or  x1 y1 x2 y2")
    sw.add_argument("--within", default=None, metavar="i=N")
    sw.add_argument("--distance-pct", dest="distance_pct", type=float, default=0.5)
    _action_flags(sw)
    sw.set_defaults(func=_cmd_swipe)

    lp = sub.add_parser("long-press")
    lp.add_argument("--i", type=int, dest="i", default=None)
    lp.add_argument("--selector", default=None)
    lp.add_argument("--text", default=None)
    lp.add_argument("--x", type=int, default=None)
    lp.add_argument("--y", type=int, default=None)
    lp.add_argument("--duration-ms", dest="duration_ms", type=int, default=1000)
    _action_flags(lp)
    lp.set_defaults(func=_cmd_long_press)

    dt = sub.add_parser("double-tap")
    dt.add_argument("--i", type=int, dest="i", default=None)
    dt.add_argument("--selector", default=None)
    dt.add_argument("--text", default=None)
    dt.add_argument("--x", type=int, default=None)
    dt.add_argument("--y", type=int, default=None)
    dt.add_argument("--interval-ms", dest="interval_ms", type=int, default=100)
    _action_flags(dt)
    dt.set_defaults(func=_cmd_double_tap)

    dr = sub.add_parser("drag")
    dr.add_argument("--x1", type=int, required=True)
    dr.add_argument("--y1", type=int, required=True)
    dr.add_argument("--x2", type=int, required=True)
    dr.add_argument("--y2", type=int, required=True)
    dr.add_argument("--duration-ms", dest="duration_ms", type=int, default=500)
    _action_flags(dr)
    dr.set_defaults(func=_cmd_drag)

    fl = sub.add_parser("fling")
    fl.add_argument("direction", choices=["up", "down", "left", "right"])
    _action_flags(fl)
    fl.set_defaults(func=_cmd_fling)

    sc = sub.add_parser("scroll")
    sc.add_argument("direction", choices=["up", "down", "left", "right"])
    sc.add_argument("--within", default=None, metavar="i=N")
    _action_flags(sc)
    sc.set_defaults(func=_cmd_scroll)

    su2 = sub.add_parser("scroll-until")
    su2.add_argument("--text", default=None)
    su2.add_argument("--selector", default=None)
    su2.add_argument("--direction", default="down",
                     choices=["up", "down", "left", "right"])
    su2.add_argument("--within", default=None, metavar="i=N")
    su2.add_argument("--max", type=int, default=10)
    _action_flags(su2)
    su2.set_defaults(func=_cmd_scroll_until)

    k = sub.add_parser("key")
    k.add_argument("keycode")
    _action_flags(k)
    k.set_defaults(func=_cmd_key)

    la = sub.add_parser("launch")
    la.add_argument("package")
    _action_flags(la)
    la.set_defaults(func=_cmd_launch)

    w = sub.add_parser("wait-for")
    w.add_argument("--text", default=None)
    w.add_argument("--id", default=None)
    w.add_argument("--selector", default=None)
    w.add_argument("--nth", type=int)
    w.add_argument("--timeout", type=float, default=5.0)
    w.set_defaults(func=_cmd_wait_for)

    rc = sub.add_parser("reconnect")
    rc.add_argument("port", nargs="?", default=None)
    rc.set_defaults(func=_cmd_reconnect)

    d = sub.add_parser("doctor")
    d.add_argument("--json", action="store_true")
    d.add_argument("--bundle", default=None, metavar="ZIP")
    d.set_defaults(func=_cmd_doctor)

    su = sub.add_parser("setup")
    su.add_argument("module", nargs="?", default="adb",
                    choices=list(setup_mod.MODULES) + ["all"])
    su.set_defaults(func=_cmd_setup)

    po = sub.add_parser("policy")
    posub = po.add_subparsers(dest="policy_cmd")
    po.set_defaults(func=_cmd_policy)
    pe = posub.add_parser("explain")
    pe.add_argument("--verb", default="tap")
    pe.add_argument("--text")
    pe.add_argument("--id")
    pe.add_argument("--selector")
    pe.add_argument("--index", type=int)
    pe.add_argument("--nth", type=int)
    pe.add_argument("--json", action="store_true")
    pe.set_defaults(func=_cmd_policy)

    au = sub.add_parser("audit")
    ausub = au.add_subparsers(dest="audit_cmd")
    at = ausub.add_parser("tail")
    at.add_argument("--limit", type=int, default=20)
    ausub.add_parser("purge")
    ae = ausub.add_parser("export")
    ae.add_argument("path")
    ae.add_argument("--no-redact", action="store_true")
    au.set_defaults(func=_cmd_audit)

    mcp = sub.add_parser("mcp")
    mcp.set_defaults(func=_cmd_mcp)

    # Emergency kill switch — host-only (Finding 1). `resume` is intentionally not
    # exposed on any agent surface (no MCP tool, no daemon RPC).
    st = sub.add_parser("stop", help="Engage the emergency kill switch (STOP).")
    st.add_argument("--json", action="store_true")
    st.set_defaults(func=_cmd_stop)
    rs = sub.add_parser("resume", help="Clear the kill switch (host-only, human action).")
    rs.add_argument("--json", action="store_true")
    rs.set_defaults(func=_cmd_resume)

    # clipboard subcommand group
    cb = sub.add_parser("clipboard")
    cbsub = cb.add_subparsers(dest="clipboard_cmd")
    cbr = cbsub.add_parser("read")
    cbr.add_argument("--json", action="store_true")
    cbr.set_defaults(func=_cmd_clipboard_read)
    cbw = cbsub.add_parser("write")
    cbw.add_argument("text")
    cbw.add_argument("--yes", action="store_true")
    cbw.add_argument("--json", action="store_true")
    cbw.set_defaults(func=_cmd_clipboard_write)
    cbc = cbsub.add_parser("clear")
    cbc.add_argument("--yes", action="store_true")
    cbc.add_argument("--json", action="store_true")
    cbc.set_defaults(func=_cmd_clipboard_clear)
    cb.set_defaults(func=lambda args: (cb.print_help(), 2)[1])

    # intent subcommand group
    it = sub.add_parser("intent")
    itsub = it.add_subparsers(dest="intent_cmd")
    its = itsub.add_parser("start")
    its.add_argument("--action", default=None)
    its.add_argument("--data", default=None)
    its.add_argument("--component", default=None)
    its.add_argument("--extra", action="append", metavar="K=V", default=[])
    its.add_argument("--yes", action="store_true")
    its.add_argument("--json", action="store_true")
    its.set_defaults(func=_cmd_intent_start)
    itb = itsub.add_parser("broadcast")
    itb.add_argument("action")
    itb.add_argument("--extra", action="append", metavar="K=V", default=[])
    itb.add_argument("--yes", action="store_true")
    itb.add_argument("--json", action="store_true")
    itb.set_defaults(func=_cmd_intent_broadcast)
    it.set_defaults(func=lambda args: (it.print_help(), 2)[1])

    # extract subcommand group
    ex = sub.add_parser("extract")
    exsub = ex.add_subparsers(dest="extract_cmd")
    exl = exsub.add_parser("list")
    exl.add_argument("--container-i", type=int, dest="container_i", default=None)
    exl.add_argument("--json", action="store_true")
    exl.set_defaults(func=_cmd_extract_list)
    exf = exsub.add_parser("form")
    exf.add_argument("--json", action="store_true")
    exf.set_defaults(func=_cmd_extract_form)
    ex.set_defaults(func=lambda args: (ex.print_help(), 2)[1])

    # find command
    fi = sub.add_parser("find")
    fi.add_argument("--text-regex", dest="text_regex", default=None)
    fi.add_argument("--ocr-text", dest="ocr_text", default=None,
                    metavar="REGEX",
                    help="OCR the screen and match region text; fallback when UI tree is empty")
    fi.add_argument("--json", action="store_true")
    fi.set_defaults(func=_cmd_find)

    # get subcommand group
    ge = sub.add_parser("get")
    gesub = ge.add_subparsers(dest="get_cmd")
    gef = gesub.add_parser("focused-field")
    gef.add_argument("--json", action="store_true")
    gef.set_defaults(func=_cmd_get_focused_field)
    getir = gesub.add_parser("text-in-region")
    getir.add_argument("--bounds", nargs=4, type=int, required=True,
                       metavar=("X1", "Y1", "X2", "Y2"))
    getir.add_argument("--json", action="store_true")
    getir.set_defaults(func=_cmd_get_text_in_region)
    ge.set_defaults(func=lambda args: (ge.print_help(), 2)[1])

    # device subcommand group
    dv = sub.add_parser("device")
    dvsub = dv.add_subparsers(dest="device_cmd")
    dvb = dvsub.add_parser("battery")
    dvb.add_argument("--json", action="store_true")
    dvb.set_defaults(func=_cmd_device_battery)
    dvw = dvsub.add_parser("wifi")
    dvw.add_argument("--json", action="store_true")
    dvw.set_defaults(func=_cmd_device_wifi)
    dv.set_defaults(func=lambda args: (dv.print_help(), 2)[1])

    # tts subcommand group
    tt = sub.add_parser("tts")
    ttsub = tt.add_subparsers(dest="tts_cmd")
    tts = ttsub.add_parser("speak")
    tts.add_argument("text")
    tts.add_argument("--language", default=None)
    tts.add_argument("--rate", type=float, default=None)
    tts.add_argument("--json", action="store_true")
    tts.set_defaults(func=_cmd_tts_speak)
    tt.set_defaults(func=lambda args: (tt.print_help(), 2)[1])

    # packages subcommand group
    pk = sub.add_parser("packages")
    pksub = pk.add_subparsers(dest="packages_cmd")
    pkl = pksub.add_parser("list")
    pkl.add_argument("--all", action="store_true")
    pkl.add_argument("--json", action="store_true")
    pkl.set_defaults(func=_cmd_packages_list)
    pkr = pksub.add_parser("resolve")
    pkr.add_argument("package")
    pkr.add_argument("--json", action="store_true")
    pkr.set_defaults(func=_cmd_packages_resolve)
    pkla = pksub.add_parser("launch")
    pkla.add_argument("package")
    pkla.add_argument("--yes", action="store_true")
    pkla.add_argument("--json", action="store_true")
    pkla.set_defaults(func=_cmd_packages_launch)
    pkst = pksub.add_parser("stop")
    pkst.add_argument("package")
    pkst.add_argument("--yes", action="store_true")
    pkst.add_argument("--json", action="store_true")
    pkst.set_defaults(func=_cmd_packages_stop)
    pkcl = pksub.add_parser("clear")
    pkcl.add_argument("package")
    pkcl.add_argument("--yes", action="store_true")
    pkcl.add_argument("--json", action="store_true")
    pkcl.set_defaults(func=_cmd_packages_clear)
    pk.set_defaults(func=lambda args: (pk.print_help(), 2)[1])

    # trust subcommand group
    tr = sub.add_parser("trust")
    trsub = tr.add_subparsers(dest="trust_cmd")
    trs = trsub.add_parser("status")
    trs.add_argument("--json", action="store_true")
    trs.set_defaults(func=_cmd_trust_status)
    tr.set_defaults(func=lambda args: (tr.print_help(), 2)[1])

    # notifications subcommand group
    nt = sub.add_parser("notifications")
    ntsub = nt.add_subparsers(dest="notifications_cmd")
    ntl = ntsub.add_parser("list")
    ntl.add_argument("--package", default=None)
    ntl.add_argument("--json", action="store_true")
    ntl.set_defaults(func=_cmd_notifications_list)
    ntw = ntsub.add_parser("wait")
    ntw.add_argument("--package", default=None)
    ntw.add_argument("--title-contains", dest="title_contains", default=None)
    ntw.add_argument("--text-contains", dest="text_contains", default=None)
    ntw.add_argument("--timeout", type=float, default=30.0)
    ntw.add_argument("--json", action="store_true")
    ntw.set_defaults(func=_cmd_notifications_wait)
    ntr = ntsub.add_parser("reply")
    ntr.add_argument("key")
    ntr.add_argument("text")
    ntr.add_argument("--yes", action="store_true")
    ntr.add_argument("--json", action="store_true")
    ntr.set_defaults(func=_cmd_notifications_reply)
    ntd = ntsub.add_parser("dismiss")
    ntd.add_argument("key")
    ntd.add_argument("--yes", action="store_true")
    ntd.add_argument("--json", action="store_true")
    ntd.set_defaults(func=_cmd_notifications_dismiss)
    nt.set_defaults(func=lambda args: (nt.print_help(), 2)[1])

    # ocr subcommand group (Phase 4.4 — optional, lowest priority)
    oc = sub.add_parser("ocr")
    ocsub = oc.add_subparsers(dest="ocr_cmd")
    ocs = ocsub.add_parser("screen")
    ocs.add_argument("--min-confidence", dest="min_confidence", type=float, default=0.0)
    ocs.add_argument("--json", action="store_true")
    ocs.set_defaults(func=_cmd_ocr_screen)
    oc.set_defaults(func=lambda args: (oc.print_help(), 2)[1])

    # job command (Task 8)
    jb = sub.add_parser("job")
    jb.add_argument("job_id")
    jb.add_argument("--wait", action="store_true", help="block until the job is terminal")
    jb.add_argument("--json", action="store_true")
    jb.set_defaults(func=_cmd_job)

    # macro subcommand group (Phase 6.1)
    mc = sub.add_parser("macro")
    mcsub = mc.add_subparsers(dest="macro_cmd")
    mcv = mcsub.add_parser("validate")
    mcv.add_argument("path")
    mcv.add_argument("--json", action="store_true")
    mcv.set_defaults(func=_cmd_macro_validate)
    mcr = mcsub.add_parser("run")
    mcr.add_argument("path")
    mcr.add_argument("--yes", action="store_true")
    mcr.add_argument("--json", action="store_true")
    mcr.set_defaults(func=_cmd_macro_run)
    mcs = mcsub.add_parser("status")
    mcs.add_argument("--json", action="store_true")
    mcs.set_defaults(func=_cmd_macro_status)
    mcc = mcsub.add_parser("cancel")
    mcc.add_argument("run_id")
    mcc.add_argument("--json", action="store_true")
    mcc.set_defaults(func=_cmd_macro_cancel)
    mce = mcsub.add_parser("enable")
    mce.add_argument("path")
    mce.add_argument("--json", action="store_true")
    mce.set_defaults(func=_cmd_macro_enable)
    mcd = mcsub.add_parser("disable")
    mcd.add_argument("name")
    mcd.add_argument("--json", action="store_true")
    mcd.set_defaults(func=_cmd_macro_disable)
    mcl = mcsub.add_parser("list")
    mcl.add_argument("--json", action="store_true")
    mcl.set_defaults(func=_cmd_macro_list)
    mc.set_defaults(func=_cmd_macro_validate)

    # autonomy subcommand group (Phase 6.3)
    at = sub.add_parser("autonomy")
    atsub = at.add_subparsers(dest="autonomy_cmd")
    atg = atsub.add_parser("grant")
    atg.add_argument("macro")
    atg.add_argument("--max-risk", required=True, dest="max_risk",
                     choices=["low", "medium", "high", "critical"])
    atg.add_argument("--scope", default="all")
    atg.add_argument("--expires", type=float, default=None)
    atg.add_argument("--json", action="store_true")
    atg.set_defaults(func=_cmd_autonomy_grant)
    atr = atsub.add_parser("revoke")
    atr.add_argument("macro")
    atr.add_argument("--json", action="store_true")
    atr.set_defaults(func=_cmd_autonomy_revoke)
    atl = atsub.add_parser("list")
    atl.add_argument("--json", action="store_true")
    atl.set_defaults(func=_cmd_autonomy_list)
    at.set_defaults(func=_cmd_autonomy_list)

    # memory subcommand group (Phase 6.3)
    mm = sub.add_parser("memory")
    mmsub = mm.add_subparsers(dest="memory_cmd")
    mms = mmsub.add_parser("show")
    mms.add_argument("store", nargs="?", default=None)
    mms.add_argument("--json", action="store_true")
    mms.set_defaults(func=_cmd_memory_show)
    mme = mmsub.add_parser("export")
    mme.add_argument("file", nargs="?", default=None)
    mme.add_argument("--json", action="store_true")
    mme.set_defaults(func=_cmd_memory_export)
    mmd = mmsub.add_parser("delete")
    mmd.add_argument("store", nargs="?", default=None)
    mmd.add_argument("--json", action="store_true")
    mmd.set_defaults(func=_cmd_memory_delete)
    mm.set_defaults(func=_cmd_memory_show)

    # daemon subcommand group (Phase 5.1)
    dm = sub.add_parser("daemon")
    dmsub = dm.add_subparsers(dest="daemon_cmd")
    dmsub.add_parser("start").set_defaults(func=_cmd_daemon)
    dmst = dmsub.add_parser("status")
    dmst.add_argument("--json", action="store_true")
    dmst.set_defaults(func=_cmd_daemon)
    dmsub.add_parser("stop").set_defaults(func=_cmd_daemon)
    dm.set_defaults(func=_cmd_daemon)

    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "func", None) is None:
        parser.print_help()
        return 0
    try:
        return args.func(args)
    except errors.PhonectlError as e:
        if getattr(args, "json", False):
            print(json.dumps(results.err(e, **getattr(e, "lock_state", {})), indent=2))
        else:
            print(f"phonectl: {e}")
        return 1
