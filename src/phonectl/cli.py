from __future__ import annotations

import argparse
import json
from phonectl import __version__, config, audit, observer, actuator
from phonectl.adb_backend import AdbBackend
from phonectl.session import Session
from phonectl.connection import Connection


def _make_backend(cfg) -> AdbBackend:
    return AdbBackend(serial=cfg.get("serial"))


def build_runtime(cfg, backend=None):
    backend = backend or _make_backend(cfg)
    session = Session()
    conn = Connection(backend, cfg)
    return backend, session, conn


def _emit(snap) -> None:
    print(json.dumps(snap, indent=2))


def _guard_action(cfg) -> int | None:
    if audit.kill_switch_active():
        print("phonectl: action refused (kill switch STOP present)")
        return 2
    return None


def _cmd_observe(args):
    cfg = config.load()
    backend, session, conn = build_runtime(cfg)
    conn.ensure()
    _emit(observer.observe(backend, session, screenshot=args.screenshot,
                           snap_path=args.screenshot_path))
    return 0


def _do_action(args, verb, fn, target):
    cfg = config.load()
    blocked = _guard_action(cfg)
    if blocked is not None:
        return blocked
    mode = config.get_mode(cfg)
    if mode == "confirm" and not args.yes:
        print(f"phonectl: {verb} {target} requires --yes in confirm mode")
        return 3
    backend, session, conn = build_runtime(cfg)
    conn.ensure()
    if mode == "dry-run":
        observer.observe(backend, session)
        print(f"phonectl: dry-run {verb} {target} (not executed)")
        return 0
    snap = fn(backend, session)
    audit.log_action(verb, target, snap)
    _emit(snap)
    return 0


def _cmd_tap(args):
    if args.index is not None:
        return _do_action(args, "tap", lambda b, s: actuator.tap(b, s, i=args.index),
                          {"i": args.index})
    x, y = args.xy
    return _do_action(args, "tap", lambda b, s: actuator.tap(b, s, x=x, y=y),
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
    if args.text is None and args.id is None:
        print("phonectl: wait-for requires --text or --id")
        return 2
    cfg = config.load()
    backend, session, conn = build_runtime(cfg)
    conn.ensure()
    snap = actuator.wait_for(backend, session, text=args.text, id=args.id,
                             timeout=args.timeout)
    if snap is None:
        print("phonectl: wait-for timed out")
        return 1
    _emit(snap)
    return 0


def _cmd_doctor(args):
    cfg = config.load()
    backend, session, conn = build_runtime(cfg)
    try:
        conn.ensure()
    except ConnectionError as e:
        print(str(e))
        return 1
    print(f"phonectl: connected (serial={backend.serial}, state={backend.get_state()})")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="phonectl")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd")

    o = sub.add_parser("observe")
    o.add_argument("--screenshot", action="store_true")
    o.add_argument("--screenshot-path", default=None)
    o.set_defaults(func=_cmd_observe)

    t = sub.add_parser("tap")
    g = t.add_mutually_exclusive_group(required=True)
    g.add_argument("--index", type=int)
    g.add_argument("--xy", nargs=2, type=int, metavar=("X", "Y"))
    t.add_argument("--yes", action="store_true")
    t.set_defaults(func=_cmd_tap)

    ty = sub.add_parser("type")
    ty.add_argument("text")
    ty.add_argument("--yes", action="store_true")
    ty.set_defaults(func=_cmd_type)

    sw = sub.add_parser("swipe")
    sw.add_argument("coords", nargs=4, type=int, metavar=("X1", "Y1", "X2", "Y2"))
    sw.add_argument("--yes", action="store_true")
    sw.set_defaults(func=_cmd_swipe)

    k = sub.add_parser("key")
    k.add_argument("keycode")
    k.add_argument("--yes", action="store_true")
    k.set_defaults(func=_cmd_key)

    la = sub.add_parser("launch")
    la.add_argument("package")
    la.add_argument("--yes", action="store_true")
    la.set_defaults(func=_cmd_launch)

    w = sub.add_parser("wait-for")
    w.add_argument("--text", default=None)
    w.add_argument("--id", default=None)
    w.add_argument("--timeout", type=float, default=5.0)
    w.set_defaults(func=_cmd_wait_for)

    d = sub.add_parser("doctor")
    d.set_defaults(func=_cmd_doctor)
    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "func", None) is None:
        parser.print_help()
        return 0
    return args.func(args)
