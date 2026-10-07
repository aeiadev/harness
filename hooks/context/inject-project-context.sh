#!/usr/bin/env bash
# Session context is reference data. Reset the session's markers on clear/compact.
# Runtime requirements: Bash, Python 3.10+, and Git.
set -euo pipefail

python3 - "$0" "$@" 3<&0 <<'PY'
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
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


def render(pointers, state_name, paragraphs, truncated):
    lines = [
        "<project-context>",
        "REFERENCE DATA. Paths are relative to the Git project root.",
    ]
    if pointers:
        lines.append("Nearest project instructions: " + ", ".join(pointers))
    if state_name:
        lines.extend(["State summary from " + state_name + ":", ""])
        lines.append("\n\n".join(paragraphs))
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
        with os.fdopen(3, encoding="utf-8", errors="replace") as stream:
            data = json.loads(stream.read(1024 * 1024))
    except (ValueError, OSError):
        data = {}
    if not isinstance(data, dict):
        data = {}

    session = string_field(data, "session_id", "sessionId", "conversation_id")
    state_home = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local/state")
    markers = Path(state_home) / "claude-harness" / "context"
    session_dir = markers / digest(session) if session else None
    if "--reset" in sys.argv[2:]:
        if session_dir is not None and session_dir.is_dir():
            for marker in session_dir.glob("*.seen"):
                marker.unlink(missing_ok=True)
            try:
                session_dir.rmdir()
            except OSError:
                pass
        return

    workspace = data.get("workspace")
    workspace_cwd = string_field(workspace, "current_dir") if isinstance(workspace, dict) else ""
    candidate = (string_field(data, "cwd", "project_dir") or workspace_cwd
                 or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    cwd = Path(candidate).resolve()
    if not cwd.is_dir():
        return
    try:
        result = subprocess.run(
            ["git", "-C", str(cwd), "rev-parse", "--show-toplevel"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=2, check=True,
        )
        root = Path(os.fsdecode(result.stdout).strip()).resolve()
        cwd.relative_to(root)
    except (OSError, ValueError, subprocess.SubprocessError):
        return

    marker = session_dir / (digest(str(root)) + ".seen") if session_dir is not None else None
    if marker is not None and marker.exists():
        return

    pointers = []
    for name in ("CLAUDE.md", "AGENTS.md"):
        current = cwd
        while True:
            path = current / name
            if path.is_file():
                pointers.append(json.dumps(path.relative_to(root).as_posix(), ensure_ascii=False))
                break
            if current == root:
                break
            current = current.parent

    script = Path(sys.argv[1]).resolve()
    sys.path.insert(0, str(script.parent.parent / "checkpoint"))
    from journal import contained_file, preferences

    options = preferences(data, root)
    state = contained_file(root, options["checkpoint_path"])
    if state is None or not state.is_file() or state.is_symlink():
        state = None
    if state is None and not pointers:
        return
    state_name = state.relative_to(root).as_posix() if state else ""
    paragraphs = []
    complete = True
    if state is not None:
        try:
            with state.open("rb") as stream:
                raw = stream.read(MAX_BYTES + 1)
            complete = len(raw) <= MAX_BYTES
            body = raw.decode("utf-8", errors="replace").replace("\r\n", "\n")
            paragraphs = re.split(r"\n[ \t]*\n", body)
            # The final paragraph may continue beyond the bounded read.
            if not complete:
                paragraphs.pop()
            paragraphs = [paragraph.strip() for paragraph in paragraphs if paragraph.strip()]
        except OSError:
            complete = False

    # Long instruction paths must leave room for the state and the closing marker.
    while pointers and len(render(pointers, state_name, [], True).encode("utf-8")) > MAX_BYTES // 2:
        pointers.pop()
    shown = []
    for paragraph in paragraphs:
        candidate_paragraphs = shown + [paragraph]
        if len(render(pointers, state_name, candidate_paragraphs, True).encode("utf-8")) > MAX_BYTES:
            break
        shown = candidate_paragraphs
    truncated = not complete or len(shown) != len(paragraphs)
    output = render(pointers, state_name, shown, truncated)
    assert len(output.encode("utf-8")) <= MAX_BYTES

    if marker is not None:
        try:
            session_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(session_dir, 0o700)
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
PY
