"""Small, bounded data operations for local hook processes."""

import hashlib
import json
import math
import os
from pathlib import Path
import stat
import tempfile


def quantity(value, fallback=None):
    if isinstance(value, bool):
        return fallback
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) and parsed >= 0 else fallback
    except (ValueError, TypeError, OverflowError):
        return fallback


def object_value(value):
    return value if isinstance(value, dict) else {}


def fingerprint(value):
    return hashlib.sha256(value if isinstance(value, bytes) else str(value).encode()).hexdigest()


def conversation_tag(event):
    for field in ("session_id", "sessionId", "conversation_id", "thread_id"):
        value = event.get(field)
        if isinstance(value, str) and value:
            return fingerprint(value)
    return None


def uses_codex(event):
    if event.get("harness_host") in ("claude", "codex"):
        return event["harness_host"] == "codex"
    model = event.get("model")
    return bool(event.get("turn_id") or event.get("thread_id")
                or event.get("type") == "turn.completed"
                or isinstance(model, str) and model.startswith(("gpt-", "o1", "o3", "o4", "codex")))


def local_cache():
    chosen = os.environ.get("XDG_STATE_HOME", "")
    base = Path(chosen) if chosen and Path(chosen).is_absolute() else Path.home() / ".local/state"
    return base / "claude-harness"


def guarded_read(path, ceiling, tail=False):
    """Return regular-file bytes and whether the entire file fitted."""
    if not isinstance(path, (str, Path)) or not str(path):
        return None, False
    try:
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW), "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                return None, False
            clipped = info.st_size > ceiling
            if tail and clipped:
                stream.seek(-ceiling, os.SEEK_END)
            raw = stream.read(ceiling + (0 if tail else 1))
            complete = not clipped and len(raw) <= ceiling
            if tail and clipped:
                raw = raw.partition(b"\n")[2]
            return raw[:ceiling], complete
    except (OSError, ValueError):
        return None, False


def json_object(path):
    raw, complete = guarded_read(path, 65536)
    try:
        return object_value(json.loads(raw)) if raw and complete else {}
    except (ValueError, RecursionError):
        return {}


def locked_directory(path):
    """Create private directories without following symbolic links."""
    path = Path(path).absolute()
    if path.is_symlink():
        raise OSError("linked storage directory")
    if not path.exists():
        locked_directory(path.parent)
        path.mkdir(mode=0o700, exist_ok=True)
    if not path.is_dir() or any(p.is_symlink() for p in path.parents):
        raise OSError("unsafe storage directory")
    return path


def replace_private(path, content):
    directory = locked_directory(path.parent)
    if path.is_symlink() or path.exists() and not path.is_file():
        raise OSError("unsafe storage target")
    descriptor, temporary = tempfile.mkstemp(dir=directory, prefix=".pending-")
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def log_records(path):
    raw, complete = guarded_read(path, 4 * 1024 * 1024, tail=True)
    records = []
    for line in (raw or b"").splitlines():
        try:
            entry = json.loads(line)
            if isinstance(entry, dict):
                records.append(entry)
        except (ValueError, RecursionError):
            if line.strip():
                complete = False
    return records, complete


def occupied_tokens(event):
    context = object_value(event.get("context_window"))
    latest = object_value(context.get("current_usage"))
    if latest:
        return int(sum(quantity(latest.get(k), 0) for k in (
            "input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")))
    amount = 0
    records, _ = log_records(event.get("transcript_path"))
    for record in records:
        if record.get("isSidechain") or record.get("agent_id"):
            continue
        packet = object_value(record.get("payload"))
        if record.get("type") == "compacted" or record.get("subtype") == "compact_boundary":
            amount = 0
        if record.get("type") == "assistant":
            usage = object_value(object_value(record.get("message")).get("usage"))
            if usage:
                amount = sum(quantity(usage.get(k), 0) for k in (
                    "input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
        elif packet.get("type") == "token_count":
            usage = object_value(object_value(packet.get("info")).get("last_token_usage"))
            amount = quantity(usage.get("input_tokens"), amount)
    return int(amount)


def rollout_window(event):
    raw, _ = guarded_read(event.get("transcript_path"), 4 * 1024 * 1024, tail=True)
    for line in reversed((raw or b"").splitlines()):
        try:
            record = object_value(json.loads(line))
        except (ValueError, RecursionError):
            continue
        payload = object_value(record.get("payload"))
        value = quantity(payload.get("model_context_window"))
        if value and value > 0:
            return int(value)
    return None


def utf8_prefix(text, allowance):
    return text.encode("utf-8")[:max(0, allowance)].decode("utf-8", errors="ignore")
