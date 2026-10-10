#!/usr/bin/env python3
"""Inspect a Claude Code or Codex shell hook payload without executing it."""

import sys
sys.dont_write_bytecode = True

import fnmatch
import importlib.util
import json
import os
from pathlib import Path
import pwd
import re
import sys

from shell_syntax import background_groups, command_input, command_segments, executed_string, literal_echoed_code, payload_host, substitutions, unwrap

spec = importlib.util.spec_from_file_location("wait_loop_guard", Path(__file__).with_name("wait-loop-guard.py"))
wait_guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wait_guard)


def home_dirs():
    homes = {"/root", os.path.expanduser("~"), os.environ.get("HOME", "")}
    try:
        homes.add(pwd.getpwuid(os.getuid()).pw_dir)
    except KeyError:
        pass
    return {os.path.normpath(home) for home in homes if home and os.path.isabs(home)}


def normalize_target(target):
    for prefix in ("${HOME}", "$HOME", "~"):
        if target == prefix or target.startswith(prefix + "/"):
            target = os.path.expanduser("~") + target[len(prefix):]
            break
    # Trailing globs delete every child of the protected directory too.
    target = re.sub(r"/[*?]+$", "/", target)
    target = os.path.normpath(target)
    if target.startswith("//"):
        target = "/" + target.lstrip("/")
    return target


def resolve(target, cwd):
    """Join a relative path to the directory an earlier cd or pushd entered, when known."""
    if cwd and not os.path.isabs(normalize_target(target)):
        return os.path.normpath(os.path.join(cwd, target))
    return target


def literal_directory(target, cwd):
    """Resolve a cd target written literally; anything expanded at run time is unknown (None)."""
    rest = next((target[len(prefix):] for prefix in ("${HOME}", "$HOME")
                 if target == prefix or target.startswith(prefix + "/")), target)
    if "$" in rest or "`" in target:
        return None
    target = normalize_target(target)
    if os.path.isabs(target):
        return target
    return os.path.normpath(os.path.join(cwd, target)) if cwd else None


def change_directory(name, args, cwd, stack, previous):
    """Follow cd, pushd and popd, including the previous cd directory."""
    args = list(args)
    if name == "cd":
        while args and re.fullmatch(r"-[LPe@]+", args[0]):
            args.pop(0)
        if args[:1] == ["--"]:
            args.pop(0)
        if args == ["-"]:
            return previous, stack, cwd if cwd and previous else None
        if len(args) > 1:
            return None, stack, None
        target = literal_directory(args[0] if args else "~", cwd)
        return target, stack, cwd if cwd and target else None
    stay = False
    while args and args[0] == "-n":
        stay = True  # -n edits the stack without changing directory.
        args.pop(0)
    if args[:1] == ["--"]:
        args.pop(0)
    # Like cd, a pushd or popd that changes directory sets the previous one (OLDPWD) to
    # where it left, known only if both ends are known.
    if name == "pushd":
        if not args and not stay and stack:  # A bare pushd swaps the top two directories.
            target = stack[-1]
            return target, stack[:-1] + [cwd], cwd if cwd and target else None
        if len(args) != 1 or re.fullmatch(r"[-+]\d*", args[0]):  # rotate, OLDPWD or unseen stack
            return (cwd if stay else None), [], previous if stay else None
        target = literal_directory(args[0], cwd)
        if stay:
            return cwd, stack + [target], previous
        return target, stack + [cwd], cwd if cwd and target else None
    if args:
        return (cwd if stay else None), [], previous if stay else None
    if stay:
        return cwd, stack[:-1], previous
    target = stack[-1] if stack else None
    return target, stack[:-1], cwd if cwd and target else None


def protected_target(target):
    target = normalize_target(target)
    if target == "/":
        return True
    if not os.path.isabs(target):
        return False
    # Removing a home's ancestor also removes the home, including ~/.. .
    return any(os.path.commonpath((target, home)) == target for home in home_dirs())


def protected_directory(target):
    """A .git directory anywhere, or a home's .ssh, .claude or .codex; not their children."""
    parent, base = os.path.split(normalize_target(target))

    def named(name):
        # A dot glob such as .git* or ~/.ss* also expands to the directory.
        return base == name or (base.startswith(".") and re.search(r"[*?[]", base) is not None
                                and fnmatch.fnmatchcase(name, base))

    if named(".git"):
        return True
    return os.path.isabs(parent) and os.path.normpath(parent) in home_dirs() and any(
        named(name) for name in (".ssh", ".claude", ".codex"))


def git_action(words):
    args = words[1:]
    directory = None
    while args and args[0].startswith("-"):
        option = args.pop(0)
        if option in {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env"} and args:
            value = args.pop(0)
            if option == "-C":
                directory = value
    return args, directory


def split_options(args, valued_short="", valued_long=()):
    """Return short option letters, long option names, and operands."""
    short, long, operands = set(), set(), []
    index, ended = 0, False
    while index < len(args):
        arg = args[index]
        index += 1
        if ended or arg == "-" or not arg.startswith("-"):
            operands.append(arg)
        elif arg == "--":
            ended = True
        elif arg.startswith("--"):
            name, has_value, _ = arg[2:].partition("=")
            long.add(name)
            if not has_value and name in valued_long:
                index += 1
        else:
            for position, letter in enumerate(arg[1:], 1):
                short.add(letter)
                if letter in valued_short:
                    index += position == len(arg) - 1
                    break
    return short, long, operands


def has_long(names, option):
    # Git accepts unambiguous prefixes such as --forc or --del.
    return any(len(name) >= 3 and option.startswith(name) for name in names)


def whole_tree(pathspec):
    normalized = os.path.normpath(pathspec)
    return (normalized == "." or all(part == ".." for part in normalized.split("/"))
            or pathspec in {":/", ":/.", "*", ":(top)"})


def explicit_file(pathspec, cwd=None):
    """True for a pathspec naming one file: not ., .., a directory, a glob or a magic pathspec."""
    if whole_tree(pathspec) or pathspec.startswith(":") or re.search(r"[*?[]", pathspec):
        return False
    if pathspec.rsplit("/", 1)[-1] in {"", ".", ".."}:
        return False
    # Text cannot tell src from a file, so ask the filesystem (a stat, nothing runs).
    return not os.path.isdir(resolve(normalize_target(pathspec), cwd))


def explicit_files(long, operands, cwd=None):
    """--ours or --theirs also resets unstaged edits to files with no conflict, so name files one by one."""
    return not has_long(long, "pathspec-from-file") and all(explicit_file(arg, cwd) for arg in operands)


def device_write(arg):
    if not arg.startswith("of="):
        return False
    path = os.path.normpath(arg[3:])
    if path.startswith("//"):
        path = "/" + path.lstrip("/")
    # Pseudo devices that discard or forward output are not disks.
    if path in {"/dev/null", "/dev/stdout", "/dev/stderr", "/dev/shm"} or path.startswith(("/dev/fd/", "/dev/shm/")):
        return False
    return path.startswith("/dev/")


def world_writable(mode):
    if re.fullmatch(r"[0-7]{1,4}", mode):
        return bool(int(mode[-1]) & 2)
    for clause in mode.split(","):
        who = re.match(r"[ugoa]*", clause)[0]
        if "a" in who or "o" in who:
            if any(op in "+=" and "w" in perms for op, perms in re.findall(r"([-+=])([rwxXst]*)", clause[len(who):])):
                return True
    return False


def world_writable_tree(args, cwd=None):
    short, long, operands = split_options(args)
    if not ("R" in short or has_long(long, "recursive")) or len(operands) < 2:
        return False
    return world_writable(operands[0]) and any(protected_target(resolve(target, cwd)) for target in operands[1:])


def find_deletes_tree(args, cwd=None):
    index = 0
    while index < len(args) and (args[index] in {"-H", "-L", "-P", "-D"} or args[index].startswith("-O")):
        index += 2 if args[index] == "-D" else 1
    starts = []
    while index < len(args) and not args[index].startswith("-") and args[index] not in {"(", "!", ","}:
        starts.append(args[index])
        index += 1
    starts = [resolve(start, cwd) for start in starts or ["."]]  # find with no start path searches .
    return "-delete" in args[index:] and any(protected_target(start) or protected_directory(start) for start in starts)


BACKGROUND_REASON = "Background commands need a positive timeout duration."
HOST_ADVICE = {
    "claude": " The & form needs timeout N command. To start a dev server or another long task, use the Bash tool's run_in_background instead.",
    "codex": " Use timeout N command (Codex has no run_in_background).",
}
SHELL_READERS = {"sh", "bash", "zsh", "dash", "ksh", "source", "."}
DOWNLOADERS = {"curl", "wget"}
DOWNLOAD_REASON = "Running a download straight in a shell (curl or wget into sh or bash) is blocked. Save the script, read it, then run it."


def command_name(words):
    return words[0].rsplit("/", 1)[-1] if words else ""


def downloads(text, depth=0):
    """True when shell text runs curl or wget as a command."""
    if depth > 12:
        return True
    for raw_words, _, _ in command_segments(text):
        words = unwrap(raw_words)[0]
        nested = executed_string(words)
        if command_name(words) in DOWNLOADERS or (nested is not None and downloads(nested, depth + 1)):
            return True
    return any(downloads(body, depth + 1) for body in substitutions(text))


def runs_download(word):
    return any(downloads(body) for body in substitutions(word))


def download_piped_to_shell(command):
    """Follow pipes, including into and out of groups, from curl or wget to a shell."""
    levels = [{"pipe": False, "group": False, "fed": False}]
    feeding = False
    for raw_words, separator, _ in command_segments(command):
        words = unwrap(raw_words)[0]
        level = levels[-1]
        if command_name(words) in SHELL_READERS and (feeding or level["fed"]):
            return True
        if command_name(words) in DOWNLOADERS or any(runs_download(word) for word in words):
            level["pipe"] = level["group"] = True
        operator = separator.strip()
        if operator in {"(", "{"}:
            levels.append({"pipe": False, "group": False, "fed": feeding or level["fed"]})
            feeding = False
        elif operator in {")", "}"} and len(levels) > 1:
            child = levels.pop()
            levels[-1]["pipe"] |= child["group"]
            levels[-1]["group"] |= child["group"]
            feeding = False
        elif operator in {"|", "|&"}:
            feeding = level["pipe"]
        else:
            level["pipe"] = feeding = False
    return False


def git_destructive(args, cwd=None):
    action, rest = args[0], args[1:]
    if action == "clean":
        short, long, _ = split_options(rest, "e", ("exclude",))
        if ("f" in short or has_long(long, "force")) and not ("n" in short or has_long(long, "dry-run")):
            return "git clean -f deletes untracked files for good. Preview with git clean -n or remove specific paths."
    if action == "checkout":
        short, long, operands = split_options(rest, "bB", ("orphan", "conflict", "pathspec-from-file"))
        resolving = has_long(long, "ours") or has_long(long, "theirs")
        if "f" in short or has_long(long, "force") or (not resolving and any(whole_tree(arg) for arg in operands)) or (
                resolving and not explicit_files(long, operands, cwd)):
            return "Discarding every working-tree change (git checkout . or -f) is blocked. Name specific files or stash first."
    if action == "restore":
        short, long, operands = split_options(rest, "s", ("source", "conflict", "pathspec-from-file"))
        worktree = "W" in short or has_long(long, "worktree") or not ("S" in short or has_long(long, "staged"))
        resolving = has_long(long, "ours") or has_long(long, "theirs")
        if worktree and ((not resolving and any(whole_tree(arg) for arg in operands))
                         or (resolving and not explicit_files(long, operands, cwd))):
            return "Discarding every working-tree change (git restore .) is blocked. Name specific files or stash first."
    if action == "branch":
        short, long, _ = split_options(rest)
        if "D" in short or (("d" in short or has_long(long, "delete")) and ("f" in short or has_long(long, "force"))):
            return "Force-deleting a branch (git branch -D) can lose unmerged commits. Merge first and use git branch -d."
    if action == "push":
        short, long, operands = split_options(rest, "o", ("push-option", "repo", "receive-pack", "exec"))
        if "d" in short or has_long(long, "delete") or any(arg.startswith(":") and len(arg) > 1 for arg in operands):
            return "Deleting a remote ref (git push --delete or :ref) is blocked. Ask the owner to delete it."
        if (has_long(long, "mirror") or has_long(long, "prune")) and not ("n" in short or has_long(long, "dry-run")):
            return "git push --mirror or --prune can delete remote refs. Preview with --dry-run first."
    return None


def inspect(command, background=False, depth=0, loop_bounded=False, inherited_elevated=False, cwd=None,
            lookup_cwd=None):
    if depth > 12:
        return "Shell nesting exceeds the inspection limit. Simplify the command."
    for nested in background_groups(command):
        reason = inspect(nested, True, depth + 1, loop_bounded, inherited_elevated, cwd, lookup_cwd)
        if reason:
            return reason
    for nested in substitutions(command):
        reason = inspect(nested, False, depth + 1, loop_bounded, inherited_elevated, cwd, lookup_cwd)
        if reason:
            return reason
    if download_piped_to_shell(command):
        return DOWNLOAD_REASON
    # cwd follows literal cd, pushd and popd; an inner sh -c or bash -c starts where its caller is.
    dirstack = []
    previous = None
    cwd_stack = []
    for raw_words, separator, redirects in command_segments(command):
        words, timed, elevated = unwrap(raw_words)
        if not words:
            if separator == "(":
                cwd_stack.append((cwd, dirstack, previous))
            elif separator == ")" and cwd_stack:
                cwd, dirstack, previous = cwd_stack.pop()
            continue
        name = command_name(words)
        if runs_download(words[0]):
            return DOWNLOAD_REASON
        if name in SHELL_READERS and (any(word.startswith("<(") and runs_download(word) for word in words[1:])
                                      or any(operator in {"<", "<<<"} and runs_download(target) for operator, target in redirects)):
            return DOWNLOAD_REASON
        shell_background = separator.strip() == "&"
        directory_setup = name == "cd" and separator.strip() == "&&"
        if (background or shell_background) and not timed and not directory_setup:
            return BACKGROUND_REASON
        if name in {"cd", "pushd", "popd"}:
            cwd, dirstack, previous = change_directory(name, words[1:], cwd, dirstack, previous)
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
            resolved = [resolve(target, cwd) for target in targets]
            if recursive and any(protected_target(target) for target in resolved):
                return "Recursive deletion of the filesystem root, a user's home, or its ancestor is blocked. Choose a specific subdirectory."
            if recursive and any(protected_directory(target) for target in resolved):
                return "Recursive deletion of a .git directory or of ~/.ssh, ~/.claude or ~/.codex is blocked. Remove a specific path inside it instead."
        if name == "dd" and any(device_write(arg) for arg in words[1:]):
            return "dd to a device (of=/dev/...) overwrites a disk. Write to a regular file instead."
        if name == "mkfs" or name.startswith("mkfs."):
            return "mkfs formats a device and erases its data. Run it yourself outside the agent if you mean it."
        if name == "chmod" and world_writable_tree(words[1:], cwd):
            return "Recursive world-writable chmod on the filesystem root, a user's home, or its ancestor is blocked. Choose a specific directory and a narrower mode."
        if name == "find" and find_deletes_tree(words[1:], cwd):
            return "find -delete from the filesystem root, a user's home, or its ancestor is blocked. Start from a specific subdirectory."
        if name == "git":
            args, git_dir = git_action(words)
            if args and args[0] == "reset" and "--hard" in args[1:]:
                return "git reset --hard discards uncommitted work. Save changes or use a non-destructive operation."
            if args and args[0] == "push" and any(arg == "-f" or arg.startswith("--force") or (arg.startswith("-") and not arg.startswith("--") and "f" in arg[1:]) or arg.startswith("+") for arg in args[1:]):
                return "Force pushes rewrite remote history. Use a normal push or a new branch."
            git_cwd = literal_directory(git_dir, cwd or lookup_cwd) if git_dir else (cwd or lookup_cwd)
            reason = git_destructive(args, git_cwd) if args else None
            if reason:
                return reason
        nested = executed_string(words)
        if nested is not None:
            echoed = literal_echoed_code(nested)
            if echoed is not None:
                reason = inspect(echoed, False, depth + 1, loop_bounded or timed,
                                 inherited_elevated or elevated, cwd, lookup_cwd)
                if reason:
                    return reason
            reason = inspect(nested, False, depth + 1, loop_bounded or timed,
                             inherited_elevated or elevated, cwd, lookup_cwd)
            if reason:
                return reason
        if separator == "(":
            cwd_stack.append((cwd, dirstack, previous))
        elif separator == ")" and cwd_stack:
            cwd, dirstack, previous = cwd_stack.pop()
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
    host = payload_host(payload)
    # Claude tracks its run_in_background tasks, so only the timeout rule is
    # skipped there; the command is still inspected. Codex has no such option.
    payload_cwd = payload.get("cwd") if isinstance(payload, dict) else None
    if not isinstance(payload_cwd, str) or not os.path.isabs(payload_cwd) or not os.path.isdir(payload_cwd):
        payload_cwd = None
    reason = inspect(command, background and host != "claude", lookup_cwd=payload_cwd)
    if reason == BACKGROUND_REASON:
        reason += HOST_ADVICE[host]
    if reason:
        print("[danger-cmd-guard] BLOCKED: " + reason, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
