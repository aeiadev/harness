#!/usr/bin/env python3
"""Display context occupancy, the latest checkpoint reminder, and session cost."""

import sys
sys.dont_write_bytecode = True

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks/checkpoint"))
import journal
import pressure
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
    if capacity and capacity > 0:
        _, _, remind, urgent = journal.window_and_thresholds(payload, settings)
        pressure.write(payload, used, int(capacity), "statusline", remind, urgent)
    if percent is None and window.get("current_usage") and capacity:
        percent = used * 100 / capacity
    context = f"ctx {min(percent, 100):.0f}%" if percent is not None else (
        f"ctx {used // 1000}k/{settings['window_tokens'] // 1000}k" if used else "ctx --")
    usage = storage.object_value(window.get("current_usage"))
    cache = ""
    if usage:
        keys = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
        values = [storage.quantity(usage.get(key, 0)) for key in keys]
        if all(value is not None for value in values) and sum(values) > 0:
            cache = f" | cache {values[2] * 100 / sum(values):.0f}%"
    record = journal.Notebook(payload).cursor
    level = storage.object_value(record.get("notice")).get("level", "ready")
    cost = spend.segment(spend.reported_spend(payload), color=True)
    print(f"{context}{cache} | checkpoint {level} | {cost}")


if __name__ == "__main__":
    try:
        display()
    except Exception:  # a statusline failure prints nothing instead of a traceback
        pass
