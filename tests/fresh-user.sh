#!/usr/bin/env bash
# Exercise the shipped installer and installed commands with an empty home.
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
fresh_home="$(mktemp -d)"
trap 'rm -rf -- "$fresh_home"' EXIT

env HOME="$fresh_home" CLAUDE_HOME="$fresh_home/.claude" \
  XDG_STATE_HOME="$fresh_home/.local/state" \
  python3 - "$repo_root" <<'PY'
import json
import os
from pathlib import Path
import subprocess
import sys


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def run(command, payload=None, cwd=None, expected=0):
    result = subprocess.run(
        command,
        input=json.dumps(payload).encode() if payload is not None else b"",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd,
        timeout=45,
        check=False,
    )
    require(
        result.returncode == expected,
        f"{command[0]} returned {result.returncode}, expected {expected}: "
        + result.stderr.decode(errors="replace")[:1000],
    )
    return result


try:
    repo = Path(sys.argv[1]).resolve()
    home = Path(os.environ["HOME"])
    config = home / ".claude"
    config.mkdir()
    settings_path = config / "settings.json"
    settings_path.write_text("{}\n", encoding="utf-8")
    run(["bash", str(repo / "install.sh"), "--host", "claude"], cwd=repo)
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    require(isinstance(settings, dict), "installed settings must be a JSON object")

    def hook(event, filename, matcher=None, argument=None):
        for group in settings.get("hooks", {}).get(event, []):
            if matcher is not None and group.get("matcher") != matcher:
                continue
            for entry in group.get("hooks", []):
                command = entry.get("command", "")
                if entry.get("type") != "command" or filename not in command:
                    continue
                if argument is not None and argument not in command:
                    continue
                return command
        raise AssertionError(f"missing hook: {event}, {filename}, matcher={matcher}")

    hook("SessionStart", "inject-project-context.sh", "clear", "--reset")
    hook("SessionStart", "dispatch.py", "compact")
    inject = hook("UserPromptSubmit", "inject-project-context.sh")
    guard = hook("PreToolUse", "danger-cmd-guard.sh", "Bash")
    hook("PostToolUse", "dispatch.py")
    hook("PostToolUse", "verify-patch-landed-auto.sh", "Bash")
    hook("PostToolUse", "auto-format.sh", "Write|Edit|MultiEdit")
    hook("PostCompact", "dispatch.py")
    hook("Stop", "stop-check-verify-patch.sh")
    status = settings.get("statusLine", {})
    require(status.get("type") == "command", "statusLine must run a command")
    require("statusline.py" in status.get("command", ""), "missing statusline command")

    project = home / "project"
    project.mkdir()
    run(["git", "init", "--quiet", str(project)], cwd=home)
    os.environ["CLAUDE_PROJECT_DIR"] = str(project)
    blocked = run(
        ["bash", "-c", guard],
        {"tool_name": "Bash", "tool_input": {"command": "rm -rf ~"}, "cwd": str(project)},
        cwd=project,
        expected=2,
    )
    require(blocked.stderr or blocked.stdout, "blocked command must explain the rejection")
    run(
        ["bash", "-c", guard],
        {"tool_name": "Bash", "tool_input": {"command": "ls"}, "cwd": str(project)},
        cwd=project,
    )
    (project / "CLAUDE.md").write_text(
        "# Project context\n\n" + ("A paragraph of project context. " * 20 + "\n\n") * 100,
        encoding="utf-8",
    )
    (project / "STATE.md").write_text(
        "Written: fixture\n\n" + ("Current project checkpoint. " * 20 + "\n\n") * 100,
        encoding="utf-8",
    )
    context = run(
        ["bash", "-c", inject],
        {"session_id": "fresh-user", "cwd": str(project), "hook_event_name": "UserPromptSubmit"},
        cwd=project,
    ).stdout
    require(context.strip(), "context injector produced no project context")
    require(len(context) <= 4000, f"context output exceeds 4096 bytes: {len(context)}")
    for expected in (b"<project-context>", b"CLAUDE.md", b"State summary from STATE.md", b"Current project checkpoint."):
        require(expected in context, f"context output is missing expected project data: {expected!r}")

    cost = run(
        ["bash", "-c", status["command"]],
        {
            "cwd": str(project),
            "model": {"display_name": "Example"},
            "cost": {"total_cost_usd": 1.23},
            "context_window": {"used_percentage": 25, "context_window_size": 200000},
        },
        cwd=project,
    ).stdout
    require(b"$1.23" in cost, "installed statusline did not display $1.23")

    for relative, minimum in (("agents", 9), ("skills", 5)):
        path = config / relative
        require(path.is_dir(), f"missing installed {relative} directory")
        items = list(path.glob("*.md")) if relative == "agents" else list(path.glob("*/SKILL.md"))
        require(len(items) == minimum, f"installed {relative} count is {len(items)}, expected {minimum}")

    # Check text files only: Python cache files can contain the runtime location.
    forbidden = b"/" + b"home/"
    for path in config.rglob("*"):
        if not path.is_file() or path.suffix == ".pyc":
            continue
        data = path.read_bytes()
        require(forbidden not in data, f"absolute home-directory path in {path.relative_to(config)}")

    print("PASS: fresh-user installation, all hook entries, guard, context cap, cost, and portable paths")
except (AssertionError, OSError, ValueError, subprocess.TimeoutExpired) as error:
    print(f"FAIL: fresh-user: {error}", file=sys.stderr)
    sys.exit(1)
PY
