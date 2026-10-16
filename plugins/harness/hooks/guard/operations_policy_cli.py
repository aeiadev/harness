"""Noninteractive owner binding commands for the remote operations policy."""

import argparse
import json
from pathlib import Path
import sys

# Adjacent, explicit local module; never resolve through ambient PYTHONPATH.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import operations_policy as policy


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise policy.PolicyError("invalid-arguments")


def main(argv=None):
    parser = Parser(prog="policy", add_help=True)
    sub = parser.add_subparsers(dest="action", required=True, parser_class=Parser)
    bind = sub.add_parser("bind")
    bind.add_argument("--project", required=True)
    bind.add_argument("--file", required=True)
    bind.add_argument("--mode", choices=("report", "enforce"), required=True)
    sub.add_parser("show")
    unbind = sub.add_parser("unbind")
    unbind.add_argument("--project", required=True)
    try:
        args = parser.parse_args(argv)
        if args.action == "bind":
            policy.bind_project(args.project, args.file, args.mode)
            print("bound")
        elif args.action == "unbind":
            policy.unbind_project(args.project)
            print("unbound")
        else:
            print(json.dumps(policy.load_registry(), sort_keys=True, indent=2))
        return 0
    except policy.PolicyError as exc:
        print(exc.error_id, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
