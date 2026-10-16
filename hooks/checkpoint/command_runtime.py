"""Explicit commands-only Harness dispatcher; a refused name never loads a command module or state."""
import sys
sys.dont_write_bytecode = True

import hashlib
import importlib.util
import json
import os
from pathlib import Path

COMMANDS = ("operations", "observe", "usage", "evaluate", "ssh", "policy")
META = ("status", "doctor", "help", "version")
# The packaged runtime: bin/harness-commands checks this list (plus profile.json) before any import.
RUNTIME_FILES = (
    "bin/harness-commands",
    "hooks/checkpoint/command_runtime.py",
    "hooks/checkpoint/compat.py",
    "hooks/checkpoint/evaluation.py",
    "hooks/checkpoint/evaluation_cli.py",
    "hooks/checkpoint/observation.py",
    "hooks/checkpoint/observation_cli.py",
    "hooks/checkpoint/operations.py",
    "hooks/checkpoint/operations_cli.py",
    "hooks/checkpoint/usage.py",
    "hooks/guard/operations_policy.py",
    "hooks/guard/operations_policy_cli.py",
    "hooks/guard/ssh_operations.py",
    "hooks/guard/ssh_operations_cli.py",
)
ENTRIES = {"operations": "hooks/checkpoint/operations_cli.py", "observe": "hooks/checkpoint/observation_cli.py",
           "evaluate": "hooks/checkpoint/evaluation_cli.py", "ssh": "hooks/guard/ssh_operations_cli.py",
           "policy": "hooks/guard/operations_policy_cli.py"}
LIMIT = 65536
USAGE = "usage: usage {declare|record} TASK_ID --file PATH | report [TASK_ID]"


def _identity(root):
    profile = root / "profile.json"
    manifest = root / "manifest.json"
    if profile.is_file():
        value = json.loads(profile.read_text(encoding="utf-8"))
        if type(value) is not dict or value.get("host") not in ("claude", "codex"):
            raise ValueError("invalid profile identity")
        record = json.loads(manifest.read_text(encoding="utf-8"))
        expected = record["files"]["profile.json"]["sha256"]
        if (hashlib.sha256(profile.read_bytes()).hexdigest() != expected
                or value.get("source_revision") != record["source_revision"]):
            raise ValueError("profile identity drift")
        return value
    # A source checkout may run this entry before installation; an installed runtime has profile.json.
    if manifest.exists() or manifest.is_symlink():
        raise ValueError("profile identity missing")
    version = root / "VERSION"
    return {"host": "claude", "version": version.read_text(encoding="utf-8").strip() if version.is_file()
            else "unknown", "source_revision": "source"}


def state_path(host):
    """Commands data: HARNESS_COMMANDS_STATE, else $XDG_STATE_HOME/harness-commands/<host>.

    The full profile's claude-harness store, host homes and project variables never select it.
    """
    override = os.environ.get("HARNESS_COMMANDS_STATE")
    if override:
        chosen = Path(override).expanduser()
        if not chosen.is_absolute():
            raise ValueError("HARNESS_COMMANDS_STATE must be absolute")
        return chosen
    if os.environ.get("XDG_STATE_HOME"):
        base = Path(os.environ["XDG_STATE_HOME"])
        if not base.is_absolute():
            raise ValueError("XDG_STATE_HOME must be absolute")
    else:
        import compat
        base = compat.home() / ".local/state"
    return base / "harness-commands" / host


def _status(root, identity):
    manifest = root / "manifest.json"
    return {"profile": "commands-only", "product": "harness", "host": identity["host"],
            "version": identity["version"], "source_revision": identity["source_revision"],
            "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest() if manifest.is_file() else None,
            "state": str(state_path(identity["host"])), "registered_hooks": False,
            "host_availability": "unknown", "policy_interception": False,
            "mcp_qualification": "unknown"}


def _load(root, relative):
    path = root / relative
    if path.is_symlink() or not path.is_file():
        raise ImportError("runtime module missing")
    spec = importlib.util.spec_from_file_location("harness_commands_" + path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _read_object(path):
    import stat
    import compat
    if path == "-":
        raw = sys.stdin.buffer.read(LIMIT + 1)
    else:
        with compat.open_read(path) as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError("input must be a regular file")
            raw = stream.read(LIMIT + 1)
    if len(raw) > LIMIT:
        raise ValueError("JSON input exceeds 64 KiB")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("JSON input must be an object")
    return value


def _usage(argv, host):
    """The usage_cli grammar and output, stored only in the commands state."""
    if len(argv) in (1, 2) and argv[0] == "report":
        action, task_id, file_path = "report", argv[1] if len(argv) == 2 else None, None
    elif len(argv) == 4 and argv[0] in ("declare", "record") and argv[1] and argv[2] == "--file" and argv[3]:
        action, task_id, file_path = argv[0], argv[1], argv[3]
    else:
        print(USAGE, file=sys.stderr)
        return 2
    import usage
    try:
        state_dir = state_path(host) / "usage"
        if action == "report":
            result = usage.get_report(task_id, state_dir=state_dir)
        else:
            value = _read_object(file_path)
            if action == "declare":
                result = usage.declare_task(task_id, value, state_dir=state_dir)
            else:
                if set(value) != {"adapter", "context", "payload"}:
                    raise ValueError("record requires adapter, context, and payload")
                event = usage.normalize_event(value["adapter"], value["payload"], value["context"])
                result = usage.record_event(task_id, event, state_dir=state_dir)
    except (usage.UsageError, OSError, UnicodeError, ValueError, TypeError, RecursionError):
        print("usage: invalid input or unavailable storage", file=sys.stderr)
        return 1
    sys.stdout.buffer.write((json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"))
    return 0


def main(argv, *, root):
    argv = list(argv) or ["help"]
    name = argv[0]
    if name not in COMMANDS + META:
        print("harness commands: command unavailable in commands-only profile", file=sys.stderr)
        return 2
    root = Path(root)
    package = str(root / "hooks/checkpoint")
    if sys.path[:1] != [package]:
        sys.path.insert(0, package)
    try:
        identity = _identity(root)
        if name == "help":
            print("harness commands-only: " + ", ".join(sorted(COMMANDS + META)))
            return 0
        if name == "version":
            print(identity["version"])
            return 0
        if name in ("status", "doctor"):
            print(json.dumps(_status(root, identity), sort_keys=True))
            return 0
        if name == "usage":
            return _usage(argv[1:], identity["host"])
        command = _load(root, ENTRIES[name])
    except (ImportError, OSError, UnicodeError, ValueError, KeyError, TypeError, SyntaxError, AttributeError):
        print("harness commands: runtime unavailable or request invalid", file=sys.stderr)
        return 1
    return command.main(argv[1:])
