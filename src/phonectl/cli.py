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
from phonectl.session import Session
from phonectl.connection import Connection, GUIDANCE


def _make_backend(cfg) -> AdbBackend:
    return AdbBackend(serial=cfg.get("serial"))


def build_runtime(cfg, backend=None):
    backend = backend or _make_backend(cfg)
    session = Session()
    conn = Connection(backend, cfg)
    return backend, session, conn


def _emit(snap) -> None:
    print(json.dumps(snap, indent=2))


def _cmd_observe(args):
    cfg = config.load()
    backend, session, conn = build_runtime(cfg)
    conn.ensure()
    snap = observer.observe(backend, session, screenshot=args.screenshot,
                            snap_path=args.screenshot_path, tree=args.tree,
                            relations=args.relations)
    if getattr(args, "json", False):
        print(json.dumps(results.ok(capability="ui.observe", provider="adb", data=snap),
                         indent=2))
    else:
        _emit(snap)
    return 0


def _do_action(args, verb, fn, target):
    env = runtime.run_action(
        verb,
        fn,
        target,
        build=build_runtime,
        yes=getattr(args, "yes", False),
        request_id=getattr(args, "request_id", None),
        idempotency_key=getattr(args, "idempotency_key", None),
    )
    if env["ok"]:
        if getattr(args, "json", False):
            print(json.dumps(env, indent=2))
        elif env.get("dry_run"):
            print(f"phonectl: dry-run {verb} {target} (not executed)")
        else:
            _emit(env["data"])
        return 0
    code = env["error"]["code"]
    if getattr(args, "json", False):
        print(json.dumps(env, indent=2))
    else:
        print(f"phonectl: {env['error']['message']}")
    return {"stopped": 2, "confirmation_required": 3}.get(code, 1)


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


def _cmd_swipe(args):
    x1, y1, x2, y2 = args.coords
    return _do_action(args, "swipe", lambda b, s: actuator.swipe(b, s, x1, y1, x2, y2),
                      {"coords": args.coords})


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
    if getattr(args, "bundle", None):
        path = diagnostics.bundle(args.bundle, backend, cfg)
        print(f"phonectl: diagnostics bundle written to {path}")
        return 0
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


def _cmd_mcp(args):
    from phonectl import mcp_server

    try:
        mcp_server.serve()
        return 0
    except errors.CapabilityUnavailableError as e:
        print(f"phonectl: {e}")
        return 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="phonectl")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd")

    def _action_flags(sp):
        sp.add_argument("--yes", action="store_true")
        sp.add_argument("--json", action="store_true")
        sp.add_argument("--request-id", default=None)
        sp.add_argument("--idempotency-key", default=None)

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
    sw.add_argument("coords", nargs=4, type=int, metavar=("X1", "Y1", "X2", "Y2"))
    _action_flags(sw)
    sw.set_defaults(func=_cmd_swipe)

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
