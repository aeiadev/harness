#!/usr/bin/env python3
"""Explicit, standalone offline usage commands for Harness."""

import sys
sys.dont_write_bytecode = True

import json
import os
from pathlib import Path
import stat

from storage import local_cache
from compat import open_read


LIMIT = 65536


class CLIError(ValueError):
    pass


def arguments(argv):
    if len(argv) in (1, 2) and argv[0] == "report":
        return "report", argv[1] if len(argv) == 2 else None, None
    if (len(argv) == 4 and argv[0] in ("declare", "record")
            and argv[1] and argv[2] == "--file" and argv[3]):
        return argv[0], argv[1], argv[3]
    raise CLIError("usage: usage_cli.py {declare|record} TASK_ID --file PATH | report [TASK_ID]")


def read_json(path):
    try:
        if path == "-":
            raw = sys.stdin.buffer.read(LIMIT + 1)
        else:
            with open_read(path) as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise CLIError("input must be a regular file")
                raw = stream.read(LIMIT + 1)
        if len(raw) > LIMIT:
            raise CLIError("JSON input exceeds 64 KiB")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise CLIError("JSON input must be an object")
        return value
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        if isinstance(exc, CLIError):
            raise
        raise CLIError("cannot read valid JSON input") from None


def main(argv=None):
    try:
        action, task_id, file_path = arguments(sys.argv[1:] if argv is None else argv)
    except CLIError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    try:
        # This sibling is vendored byte-for-byte from the shared stdlib-only core.
        import usage
    except ImportError:
        print("usage: core unavailable", file=sys.stderr)
        return 1

    try:
        state_dir = Path(local_cache()) / "usage"
        if action == "report":
            result = usage.get_report(task_id, state_dir=state_dir)
        else:
            value = read_json(file_path)
            if action == "declare":
                result = usage.declare_task(task_id, value, state_dir=state_dir)
            else:
                if set(value) != {"adapter", "context", "payload"}:
                    raise CLIError("record requires adapter, context, and payload")
                event = usage.normalize_event(value["adapter"], value["payload"], value["context"])
                result = usage.record_event(task_id, event, state_dir=state_dir)
        sys.stdout.buffer.write((json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"))
        return 0
    except (CLIError, usage.UsageError, OSError, ValueError, RecursionError):
        print("usage: invalid input or unavailable storage", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
