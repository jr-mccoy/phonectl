import argparse
from phonectl import __version__


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="phonectl")
    p.add_argument("--version", action="version", version=__version__)
    p.set_defaults(func=None)
    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "func", None) is None:
        parser.print_help()
        return 0
    return args.func(args)
