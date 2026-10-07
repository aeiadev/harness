#!/usr/bin/env python3
"""Host event adapter for checkpoint reminders, copies, and recovery."""

import json
import sys

from journal import Notebook
from storage import utf8_prefix


def handle():
    try:
        event = json.loads(sys.stdin.buffer.read(1024 * 1024))
    except (ValueError, RecursionError):
        return
    if not isinstance(event, dict) or not event or event.get("agent_id"):
        return
    action = sys.argv[1] if len(sys.argv) >= 2 else ""
    for argument in sys.argv[2:]:
        if argument in {"--host=codex", "--host=claude"}:
            event["harness_host"] = argument.partition("=")[2]
    if action not in {"check", "snapshot", "archive", "restore"}:
        return
    if action == "restore" and event.get("source") != "compact":
        return
    journal = Notebook(event)
    message = None
    if action == "check":
        message = journal.check()
    elif action in {"snapshot", "archive"}:
        journal.archive("before" if action == "snapshot" else "after")
    else:
        message = journal.restore()
    if message:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "SessionStart" if action == "restore" else "PostToolUse",
            "additionalContext": utf8_prefix(message, 4000)}}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        handle()
    except Exception:  # a checkpoint failure must never block the session
        pass
