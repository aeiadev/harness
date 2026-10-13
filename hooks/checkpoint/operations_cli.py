#!/usr/bin/env python3
"""Standalone, read-only operations brief and declared-tool preflight."""

import sys
sys.dont_write_bytecode = True

import json
import os
import stat

from compat import open_read
from operations import parse_brief, assess_agent

LIMIT = 65536


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 3 or args[:2] != ["check", "--file"] or not args[2]:
        print("usage: operations_cli.py check --file PATH|-", file=sys.stderr)
        return 2
    try:
        if args[2] == "-":
            raw = sys.stdin.buffer.read(LIMIT + 1)
        else:
            with open_read(args[2]) as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise ValueError("regular file required")
                raw = stream.read(LIMIT + 1)
        if len(raw) > LIMIT:
            raise ValueError("input too large")
        request = json.loads(raw, object_pairs_hook=unique_object)
        if type(request) is not dict or set(request) != {
                "brief", "agent", "required_tools", "available_tools"}:
            raise ValueError("invalid envelope")
        parse_brief(request["brief"])
        result = assess_agent(request["agent"], request["required_tools"],
                              request["available_tools"])
        result["brief_valid"] = True
        print(json.dumps(result, sort_keys=True))
        return 3 if result["tool_status"] == "declared_missing" else 0
    except (OSError, ValueError, TypeError, RecursionError):
        print("operations: invalid input or operations profile", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
