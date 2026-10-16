"""Offline, bounded SSH policy check. No inspected text is executed."""
import sys
sys.dont_write_bytecode = True

import argparse
import importlib.util
import json
import os
from pathlib import Path, PureWindowsPath
import stat

MAX_REQUEST = 64 * 1024 + 8192


def adjacent(name):
    spec = importlib.util.spec_from_file_location("harness_" + name,
                                                  Path(__file__).with_name(name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def adjacent_compat():
    spec = importlib.util.spec_from_file_location("harness_compat",
                                                  Path(__file__).parent.parent / "checkpoint/compat.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def unique(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("duplicate key")
        out[key] = value
    return out


def file_bytes(path, limit, kind, policy_module):
    if os.name != "nt":
        return policy_module._regular_bytes(path, limit, kind)
    # Offline preview does not enroll a Windows policy. Restrict this reader to
    # ordinary drive paths; devices, network namespaces and alternate data
    # streams are not input files. Existing reparse components are refused.
    if not path or path.endswith(("/", "\\")) or os.path.basename(path) in (".", ".."):
        raise ValueError("regular file required")
    supplied_drive, supplied_tail = os.path.splitdrive(path)
    if supplied_drive and not supplied_tail.startswith(("/", "\\")):
        raise ValueError("drive-relative input")
    reserved = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
    reserved.update(prefix + digit for prefix in ("COM", "LPT") for digit in "123456789¹²³")
    # GetFullPathName (used by abspath) can strip a terminal dot. Reject the
    # supplied spelling before canonicalization can change its meaning.
    raw = PureWindowsPath(path)
    for part in raw.parts[1:] if raw.anchor else raw.parts:
        if part != ".." and (":" in part or part.rstrip(" .") != part
                              or part.split(".", 1)[0].upper() in reserved):
            raise ValueError("ordinary path required")
    target = Path(os.path.abspath(path))
    if len(target.drive) != 2 or not target.drive[0].isalpha() or target.drive[1] != ":":
        raise ValueError("local disk path required")
    if len(target.parts) > 256:
        raise ValueError("path limit")
    current = Path(target.anchor)
    parts = target.parts[1:]
    if not parts:
        raise ValueError("regular file required")
    for part in parts:
        if part != ".." and (":" in part or part.rstrip(" .") != part
                              or part.split(".", 1)[0].upper() in reserved):
            raise ValueError("ordinary path required")
    for index, part in enumerate(parts):
        current = current / part
        before = current.lstat()
        if getattr(before, "st_file_attributes", 0) & 0x400 or stat.S_ISLNK(before.st_mode):
            raise ValueError("reparse input")
        expected = stat.S_ISREG if index == len(parts) - 1 else stat.S_ISDIR
        if not expected(before.st_mode):
            raise ValueError("regular path required")
    fd = os.open(current, os.O_RDONLY | adjacent_compat().NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        opened = os.fstat(stream.fileno())
        if (not stat.S_ISREG(opened.st_mode) or opened.st_size > limit
                or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)):
            raise ValueError("input changed or oversized")
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("input too large")
    return data


def request(path, policy_module):
    if path == "-":
        data = sys.stdin.buffer.read(MAX_REQUEST + 1)
    else:
        data = file_bytes(path, MAX_REQUEST, "input", policy_module)
    if len(data) > MAX_REQUEST:
        raise ValueError("request too large")
    value = json.loads(data.decode("utf-8"), object_pairs_hook=unique)
    if type(value) is not dict or set(value) != {"schema_version", "command", "project_root"}:
        raise ValueError("invalid request")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ValueError("invalid version")
    if type(value["command"]) is not str or type(value["project_root"]) is not str:
        raise ValueError("invalid values")
    if not os.path.isabs(value["project_root"]) or not os.path.isdir(value["project_root"]):
        raise ValueError("invalid project")
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(prog="ssh")
    sub = parser.add_subparsers(dest="action", required=True)
    check = sub.add_parser("check")
    check.add_argument("--file", required=True)
    check.add_argument("--policy")
    args = parser.parse_args(argv)
    try:
        policy_module = adjacent("operations_policy")
        remote = adjacent("ssh_operations")
        value = request(args.file, policy_module)
        root = os.path.realpath(value["project_root"])
        if root != value["project_root"]:
            raise ValueError("noncanonical project")
        preview = args.policy is not None
        if preview:
            data = file_bytes(args.policy, policy_module.POLICY_LIMIT, "policy", policy_module)
            selected = policy_module.strict_load(data)
        else:
            binding = policy_module.resolve_binding(root)
            if binding["mode"] == "off" or binding["error_id"] or binding["project_root"] != root:
                raise ValueError("unavailable binding")
            selected = binding["policy"]
        result = remote.inspect_remote(value["command"], selected, cwd=root)
    except (OSError, ValueError, UnicodeError, RuntimeError, TypeError, ImportError, SyntaxError, AttributeError):
        print("ssh: malformed or unavailable input", file=sys.stderr)
        return 1
    if preview:
        result = dict(result, binding="unbound-preview")
    print(json.dumps(result, sort_keys=True))
    return {"ALLOW": 0, "BLOCK": 3, "UNKNOWN": 4}[result["verdict"]]


if __name__ == "__main__":
    raise SystemExit(main())
