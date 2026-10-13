#!/usr/bin/env python3
"""SessionStart and UserPromptSubmit: add project instructions and state (inject-project-context.sh)."""

import sys
sys.dont_write_bytecode = True

import hashlib
import json
import os
from pathlib import Path
import sys

MAX_BYTES = 4000


def string_field(data, *names):
    for name in names:
        value = data.get(name)
        if isinstance(value, str) and value:
            return value
    return ""


def digest(value):
    return hashlib.sha256(os.fsencode(value)).hexdigest()


def render(pointers, state_name, summary, truncated):
    lines = [
        "<project-context>",
        "REFERENCE DATA. Paths are relative to the project root.",
    ]
    if pointers:
        lines.append("Nearest project instructions: " + ", ".join(pointers))
    if state_name:
        lines.extend(["State summary from " + state_name + ":", ""])
        lines.append(summary)
        if truncated:
            lines.extend(["", "[truncated: read " + state_name + " for the rest.]"])
    lines.extend([
        "</project-context>",
        "End of reference data. Continue with the user's current request.",
        "",
    ])
    return "\n".join(lines)


def main():
    try:
        data = json.loads(sys.stdin.buffer.read(1024 * 1024).decode("utf-8", errors="replace"))
    except (ValueError, OSError):
        data = {}
    if not isinstance(data, dict):
        data = {}

    session = string_field(data, "session_id", "sessionId", "conversation_id")
    state_home = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local/state")
    markers = Path(state_home) / "claude-harness" / "context"
    session_dir = markers / digest(session) if session else None
    if "--reset" in sys.argv[1:]:
        if session_dir is not None and session_dir.is_dir():
            for marker in session_dir.glob("*.seen"):
                marker.unlink(missing_ok=True)
            try:
                session_dir.rmdir()
            except OSError:
                pass
        return

    script = Path(__file__).resolve()
    sys.path.insert(0, str(script.parent.parent / "checkpoint"))
    from project_root import contained_file, project_root
    root, cwd = project_root(data)
    if root is None:
        return

    marker = session_dir / (digest(str(root)) + ".seen") if session_dir is not None else None
    if marker is not None and marker.exists():
        return

    pointers = []
    for name in ("CLAUDE.md", "AGENTS.md"):
        current = cwd
        while True:
            path = current / name
            if path.is_file() and contained_file(root, path.relative_to(root).as_posix()) is not None:
                pointers.append(json.dumps(path.relative_to(root).as_posix(), ensure_ascii=False))
                break
            if current == root:
                break
            current = current.parent

    from compat import set_private
    from journal import preferences
    from state_excerpt import read_state, state_excerpt

    options = preferences(data, root)
    state = contained_file(root, options["checkpoint_path"])
    if state is None or not state.is_file() or state.is_symlink():
        state = None
    if state is None and not pointers:
        return
    state_name = state.relative_to(root).as_posix() if state else ""
    raw = b""
    complete = True
    if state is not None:
        raw, complete = read_state(state)
        if raw is None:
            raw = b""

    # Long instruction paths must leave room for the state and the closing marker.
    while pointers and len(render(pointers, state_name, "", True).encode("utf-8")) > MAX_BYTES // 2:
        pointers.pop()
    budget = MAX_BYTES - len(render(pointers, state_name, "", True).encode("utf-8"))
    summary, truncated = state_excerpt(raw, complete, budget, options["restore_order"],
                                       plain_paragraphs=True)
    output = render(pointers, state_name, summary, truncated)
    assert len(output.encode("utf-8")) <= MAX_BYTES

    if marker is not None:
        try:
            session_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            set_private(session_dir, 0o700)
            descriptor = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor)
        except FileExistsError:
            return
        except OSError:
            pass  # Context remains useful if the state directory is unwritable.
    sys.stdout.buffer.write(output.encode("utf-8"))


try:
    main()
except (OSError, ValueError):
    # A missing project or unavailable state storage must not break the session.
    pass
