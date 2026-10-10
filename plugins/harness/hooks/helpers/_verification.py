#!/usr/bin/env python3
"""Best-effort reminders for indirect edits; never run transcript commands."""

import sys
sys.dont_write_bytecode = True

import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "guard"))
from shell_syntax import command_input, executed_string, segments, unwrap

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "checkpoint"))
import storage


def command_words(command, depth=0):
    """Inspect command positions, not words printed by echo or printf."""
    if not isinstance(command, str) or depth > 8:
        return
    for words, _ in segments(command):
        words, _, _ = unwrap(words)
        if not words:
            continue
        name = Path(words[0]).name
        nested = executed_string(words)
        if nested is not None:
            yield from command_words(nested, depth + 1)
        else:
            yield name, words[1:]


def indirect_edit(command):
    if not isinstance(command, str):
        return None
    for name, args in command_words(command):
        if name in {"sg", "ast-grep"} and any(arg == "-r" or arg.startswith("--rewrite") for arg in args):
            return "structural rewrite"
        if name in {"sed", "perl", "awk", "gawk"} and any(
            arg.startswith("--in-place") or re.match(r"^-[a-zA-Z]*i", arg) for arg in args
        ):
            return "in-place edit"
        if re.fullmatch(r"python[0-9.]*", name) and re.search(r"ast\.parse|codemod|libcst", command):
            return "Python rewrite"
        if name == "ed" and "<<" in command:
            return "editor script"
    return None


def inspection(command):
    for name, args in command_words(command):
        if name in {"cat", "head", "tail", "rg", "grep", "sha256sum"} and args:
            return True
        if name == "sed" and "-n" in args:
            return True
        if name == "git":
            while args and args[0].startswith("-"):
                option = args.pop(0)
                if option in {"-C", "-c", "--git-dir", "--work-tree"} and args:
                    args.pop(0)
            if args and args[0] in {"diff", "show"}:
                return True
    return False


def pending_edit(transcript):
    """Pair tool uses with successful results; an attempted read is not proof.

    Checks are advisory: a successful subsequent Read or shell inspection clears
    the reminder. This does not assert the semantic correctness of an edit.
    Only the current user turn is considered, so old work cannot block Stop.
    """
    pending = None
    tool_uses = {}
    try:
        with Path(transcript).open(encoding="utf-8") as stream:
            for line in stream:
                try:
                    entry = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if not isinstance(entry, dict):
                    continue
                # Normalize Codex rollout items to the same tool-use/result
                # representation consumed below. Never execute transcript text.
                item = entry.get("payload")
                if entry.get("type") == "event_msg" and isinstance(item, dict):
                    if item.get("type") in {"user_message", "task_started"}:
                        pending, tool_uses = None, {}
                    continue
                if entry.get("type") == "response_item" and isinstance(item, dict):
                    kind = item.get("type")
                    if kind == "message" and item.get("role") == "user":
                        pending, tool_uses = None, {}
                        continue
                    if kind == "function_call":
                        try:
                            arguments = json.loads(item.get("arguments", "{}"))
                        except (ValueError, TypeError):
                            continue
                        entry = {"message": {"content": [{"type": "tool_use",
                            "id": item.get("call_id"), "name": item.get("name"),
                            "input": arguments}]}}
                    elif kind == "function_call_output":
                        output = item.get("output")
                        if isinstance(output, str):
                            try:
                                result = json.loads(output)
                            except ValueError:
                                result = None
                            success = (isinstance(result, dict) and
                                       type(result.get("exit_code")) is int and result["exit_code"] == 0)
                            if result is None:
                                header = re.split(r"(?m)^(?:Final output|Output):\s*\n", output, maxsplit=1)[0]
                                success = bool(re.search(
                                    r"(?m)^(?:Process exited with code 0|Process exit code: 0)\s*$", header))
                        else:
                            success = (isinstance(output, dict) and
                                       type(output.get("exit_code")) is int and output["exit_code"] == 0)
                        entry = {"message": {"content": [{"type": "tool_result",
                            "tool_use_id": item.get("call_id"), "is_error": not success}]}}
                message = entry.get("message")
                if not isinstance(message, dict):
                    continue
                content = message.get("content", [])
                if isinstance(content, str):
                    if message.get("role", entry.get("type")) == "user":
                        pending, tool_uses = None, {}
                    continue
                if not isinstance(content, list):
                    continue
                if message.get("role", entry.get("type")) == "user" and any(
                    isinstance(block, dict) and block.get("type") == "text"
                    for block in content
                ):
                    pending, tool_uses = None, {}
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    kind = block.get("type")
                    if kind == "tool_use":
                        identifier = block.get("id")
                        if isinstance(identifier, str):
                            tool_uses[identifier] = block
                    elif kind == "tool_result":
                        identifier = block.get("tool_use_id")
                        if not isinstance(identifier, str):
                            continue
                        use = tool_uses.pop(identifier, {})
                        if block.get("is_error"):
                            continue
                        data = use.get("input", {})
                        if not isinstance(data, dict):
                            continue
                        shell = command_input({"tool_name": use.get("name"), "tool_input": data})
                        if shell is not None:
                            command, _ = shell
                            edit = indirect_edit(command)
                            if edit:
                                pending = edit
                            elif inspection(command):
                                pending = None
                        elif use.get("name") == "Read":
                            pending = None
    except (OSError, UnicodeError, ValueError):
        return None
    return pending


def main():
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        return
    if not isinstance(payload, dict):
        return
    mode = sys.argv[1] if len(sys.argv) > 1 else "post"
    if mode == "post":
        shell = command_input(payload)
        if shell is None:
            return
        edit = indirect_edit(shell[0])
        if edit:
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": (
                    f"An {edit} was detected. Re-read the target file or inspect "
                    "its diff and confirm the expected change before claiming completion."
                ),
            }}))
    elif mode == "stop" and not payload.get("stop_hook_active"):
        transcript = payload.get("transcript_path")
        if not isinstance(transcript, str) or not transcript:
            return
        edit = pending_edit(transcript)
        if edit:
            reason = (
                f"The latest {edit} has no later successful inspection in this turn. "
                "Read the changed file or inspect its diff to verify the patch landed."
            )
            if storage.uses_codex(payload):
                print(reason, file=sys.stderr)
                raise SystemExit(2)
            print(json.dumps({"decision": "block", "reason": reason}))


if __name__ == "__main__":
    main()
