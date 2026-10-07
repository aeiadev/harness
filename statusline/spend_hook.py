#!/usr/bin/env python3
"""Stop/PostToolUse: persist spend and emit an occasional Codex systemMessage."""

import json
import os
from pathlib import Path
import sys
import time


def main():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks" / "checkpoint"))
    import storage
    import spend

    data = storage.object_value(json.load(sys.stdin))
    key = storage.conversation_tag(data)
    if not key or data.get("agent_id"):
        return
    amount, estimated = spend.codex_spend(data)
    text = spend.segment(amount)
    directory = storage.locked_directory(storage.local_cache() / "spend")
    storage.replace_private(directory / (key + ".txt"), (text + "\n").encode("utf-8"))
    marker = directory / (key + ".json")
    raw, _ = storage.guarded_read(marker, 65536)
    try:
        marks = json.loads(raw) if raw else {}
        marks = marks if isinstance(marks, dict) else {}
    except (ValueError, RecursionError):
        marks = {}
    now = time.time()
    interval = max(1, storage.quantity(os.environ.get("HARNESS_SPEND_MESSAGE_SECONDS"), 300))
    last = storage.quantity(marks.get("message_at"), 0)
    if now - last >= interval:
        qualifier = " (configured-rate estimate)" if estimated else ""
        if amount is None:
            qualifier = " (cost unavailable; supply cost or configure HARNESS_CODEX_RATES)"
        print(json.dumps({"systemMessage": "[harness] spend " + text + qualifier}))
        marks["message_at"] = now
    marks.update(segment=text, estimated=estimated)
    storage.replace_private(marker, json.dumps(marks).encode("utf-8"))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
