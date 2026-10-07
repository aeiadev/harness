#!/usr/bin/env python3
"""Inspect a Claude Code or Codex shell hook payload without executing it."""

import importlib.util
import json
import os
from pathlib import Path
import pwd
import re
import sys

from shell_syntax import background_groups, command_input, executed_string, segments, substitutions, unwrap

spec = importlib.util.spec_from_file_location("wait_loop_guard", Path(__file__).with_name("wait-loop-guard.py"))
wait_guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wait_guard)


def protected_target(target):
    homes = {os.path.expanduser("~"), os.environ.get("HOME", "")}
    try:
        homes.add(pwd.getpwuid(os.getuid()).pw_dir)
    except KeyError:
        pass
    for prefix in ("${HOME}", "$HOME", "~"):
        if target == prefix or target.startswith(prefix + "/"):
            target = os.path.expanduser("~") + target[len(prefix):]
            break
    # Trailing globs delete every child of the protected directory too.
    target = re.sub(r"/[*?]+$", "/", target)
    target = os.path.normpath(target)
    if target.startswith("//"):
        target = "/" + target.lstrip("/")
    if target == "/":
        return True
    if not os.path.isabs(target):
        return False
    # Removing a home's ancestor also removes the home, including ~/.. .
    return any(os.path.commonpath((target, os.path.normpath(home))) == target
               for home in homes if home and os.path.isabs(home))


def git_action(words):
    args = words[1:]
    while args and args[0].startswith("-"):
        option = args.pop(0)
        if option in {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env"} and args:
            args.pop(0)
    return args


def inspect(command, background=False, depth=0, loop_bounded=False, inherited_elevated=False):
    if depth > 12:
        return "Shell nesting exceeds the inspection limit. Simplify the command."
    for nested in background_groups(command):
        reason = inspect(nested, True, depth + 1, loop_bounded, inherited_elevated)
        if reason:
            return reason
    for nested in substitutions(command):
        reason = inspect(nested, False, depth + 1, loop_bounded, inherited_elevated)
        if reason:
            return reason
    for raw_words, separator in segments(command):
        words, timed, elevated = unwrap(raw_words)
        if not words:
            continue
        name = words[0].rsplit("/", 1)[-1]
        shell_background = separator.strip() == "&"
        directory_setup = name == "cd" and separator.strip() == "&&"
        if (background or shell_background) and not timed and not directory_setup:
            return "Background commands need a positive timeout duration. Use timeout N command."
        if name == "rm":
            if elevated or inherited_elevated:
                return "Privilege-escalated deletion (sudo rm) is blocked. Use a targeted command without sudo."
            options, targets = [], []
            ended = False
            for arg in words[1:]:
                if arg == "--" and not ended:
                    ended = True
                elif not ended and arg.startswith("-"):
                    options.append(arg)
                else:
                    targets.append(arg)
            recursive = any(arg == "--recursive" or (not arg.startswith("--") and re.search("[rR]", arg[1:])) for arg in options)
            if recursive and any(protected_target(target) for target in targets):
                return "Recursive deletion of the filesystem root, a user's home, or its ancestor is blocked. Choose a specific subdirectory."
        if name == "git":
            args = git_action(words)
            if args and args[0] == "reset" and "--hard" in args[1:]:
                return "git reset --hard discards uncommitted work. Save changes or use a non-destructive operation."
            if args and args[0] == "push" and any(arg == "-f" or arg.startswith("--force") or (arg.startswith("-") and not arg.startswith("--") and "f" in arg[1:]) or arg.startswith("+") for arg in args[1:]):
                return "Force pushes rewrite remote history. Use a normal push or a new branch."
        nested = executed_string(words)
        if nested is not None:
            reason = inspect(nested, False, depth + 1, loop_bounded or timed, inherited_elevated or elevated)
            if reason:
                return reason
    reason = None if loop_bounded else wait_guard.unbounded_loop(command)
    if reason:
        return "Unbounded wait loop. Wrap the shell in timeout N or use an advancing counter or clock comparison."
    return None


def main():
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeError):
        return 0
    shell_input = command_input(payload)
    if shell_input is None:
        return 0
    command, background = shell_input
    reason = inspect(command, background)
    if reason:
        print("[danger-cmd-guard] BLOCKED: " + reason, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
