"""Private, bounded context pressure signal shared with local readers."""

import hashlib
import json
import time

from storage import guarded_read, local_cache, object_value, quantity, replace_private


def path_for(event):
    session = event.get("session_id")
    if not isinstance(session, str) or not session:
        return None
    name = hashlib.sha256(session.encode()).hexdigest()[:16] + ".json"
    return local_cache() / "pressure" / name


def previous(event):
    path = path_for(event)
    if path is None:
        return {}
    raw, complete = guarded_read(path, 4096)
    try:
        return object_value(json.loads(raw)) if raw and complete else {}
    except (ValueError, RecursionError):
        return {}


def last_statusline_window(event):
    record = previous(event)
    if (record.get("schema") == 1 and record.get("host") == "claude"
            and record.get("window_source") == "statusline"
            and time.time() - quantity(record.get("ts"), 0) <= 600):
        window = quantity(record.get("window"))
        return int(window) if window and window > 0 else None
    return None


def write(event, used, window, source, remind, urgent):
    path = path_for(event)
    if path is None or not window or window <= 0:
        return
    percent = used * 100 / window
    level = "urgent" if used >= urgent else "remind" if used >= remind else "ok"
    old = previous(event)
    old_percent = quantity(old.get("percent"))
    if (old.get("schema") == 1 and old.get("level") == level
            and old_percent is not None and abs(percent - old_percent) < 1
            and old.get("window") == window and old.get("window_source") == source):
        return
    record = {"schema": 1, "host": "codex" if event.get("harness_host") == "codex" else "claude",
              "ts": time.time(), "used": int(used), "window": int(window),
              "window_source": source, "percent": percent, "level": level}
    try:
        replace_private(path, json.dumps(record, separators=(",", ":")).encode())
    except OSError:
        pass
