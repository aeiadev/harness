#!/usr/bin/env python3
"""Display context occupancy, the latest checkpoint reminder, and session cost."""

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks/checkpoint"))
import journal
import storage
import spend


def display():
    payload = json.load(sys.stdin)
    if not isinstance(payload, dict):
        return
    settings = journal.preferences(payload)
    window = storage.object_value(payload.get("context_window"))
    percent = storage.quantity(window.get("used_percentage"))
    used = storage.occupied_tokens(payload)
    capacity = storage.quantity(window.get("context_window_size"))
    if percent is None and window.get("current_usage") and capacity:
        percent = used * 100 / capacity
    context = f"ctx {min(percent, 100):.0f}%" if percent is not None else (
        f"ctx {used // 1000}k/{settings['window_tokens'] // 1000}k" if used else "ctx --")
    record = journal.Notebook(payload).cursor
    level = storage.object_value(record.get("notice")).get("level", "ready")
    cost = spend.segment(spend.reported_spend(payload), color=True)
    print(f"{context} | checkpoint {level} | {cost}")


if __name__ == "__main__":
    try:
        display()
    except Exception:  # a statusline failure prints nothing instead of a traceback
        pass
