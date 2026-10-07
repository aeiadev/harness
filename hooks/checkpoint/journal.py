"""A session binds once to a checkpoint and keeps its receipts outside Git."""

import json
import os
from pathlib import Path
import re
import uuid

from storage import (conversation_tag, fingerprint, guarded_read, json_object,
                     local_cache, object_value, quantity, replace_private,
                     uses_codex, utf8_prefix)


def project_anchor(event):
    candidate = (event.get("cwd") or event.get("project_dir")
                 or object_value(event.get("workspace")).get("current_dir")
                 or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    if not isinstance(candidate, str):
        return None
    current = Path(candidate).resolve()
    if not current.is_dir():
        return None
    for directory in (current, *current.parents):
        if (directory / ".git").exists():
            return directory
    return None if current == Path.home().resolve() else current


def contained_file(base, relative):
    if not isinstance(relative, str) or not relative or len(relative.encode()) > 512:
        return None
    part = Path(relative)
    if part.is_absolute() or ".." in part.parts:
        return None
    candidate = base / part
    if candidate.is_symlink() or any(p.is_symlink() for p in candidate.parents if p != base.parent):
        return None
    return candidate if candidate.resolve().is_relative_to(base.resolve()) else None


def preferences(event, base=None):
    defaults = {"checkpoint_path": "STATE.md", "remind_at": 180000,
                "urgent_at": 240000, "window_tokens": 280000,
                "repeat_after": 12000, "restore_bytes": 4000}
    sources = [Path(__file__).with_name("policy.json")]
    base = base or project_anchor(event)
    if base:
        sources.append(base / (".codex" if uses_codex(event) else ".claude") / "checkpoint.json")
    result = dict(defaults)
    for source in sources:
        for key, value in json_object(source).items():
            if key in defaults:
                result[key] = value
    for key in defaults.keys() - {"checkpoint_path"}:
        result[key] = int(quantity(result[key], defaults[key]))
    if not 0 < result["remind_at"] < result["urgent_at"] < result["window_tokens"]:
        for key in ("remind_at", "urgent_at", "window_tokens"):
            result[key] = defaults[key]
    result["repeat_after"] = max(1, result["repeat_after"])
    result["restore_bytes"] = max(1000, min(4000, result["restore_bytes"]))
    if contained_file(base or Path.cwd(), result["checkpoint_path"]) is None:
        result["checkpoint_path"] = defaults["checkpoint_path"]
    return result


class Notebook:
    def __init__(self, event):
        self.event = event
        self.identity = conversation_tag(event)
        host = "codex" if uses_codex(event) else "claude"
        self.directory = local_cache() / "checkpoints" / host / (self.identity or "anonymous")
        self.receipt = self.directory / "cursor.json"
        self.cursor = json_object(self.receipt) if self.identity else {}
        self.anchor = project_anchor(event)
        self.options = preferences(event, self.anchor)
        self.document = None
        binding = object_value(self.cursor.get("binding"))
        if isinstance(binding.get("directory"), str):
            base = Path(binding["directory"])
            if base.is_absolute():
                self.document = contained_file(base, binding.get("document"))
                if self.document:
                    self.anchor = base
                    self.options = preferences(event, base)
        if self.document is None:
            self.anchor = self.anchor or self.directory / "draft"
            self.document = contained_file(self.anchor, self.options["checkpoint_path"])
        if self.document is None:
            # Keep the path visible, but guarded reads will reject unsafe links.
            self.document = self.anchor / "STATE.md"
        self.cursor["binding"] = {"directory": str(self.anchor),
                                  "document": str(self.document.relative_to(self.anchor))}

    def persist(self):
        if self.identity:
            try:
                replace_private(self.receipt, json.dumps(self.cursor).encode())
            except OSError:
                pass

    def content(self):
        if contained_file(self.anchor, str(self.document.relative_to(self.anchor))) is None:
            return None, False
        return guarded_read(self.document, 1024 * 1024)

    def check(self):
        from storage import occupied_tokens
        used = occupied_tokens(self.event)
        response = self.event.get("tool_response")
        if response is not None and used:
            used += len(json.dumps(response, ensure_ascii=False).encode()) // 4
        previous = quantity(self.cursor.get("usage"), 0)
        if used < previous // 2:
            self.cursor.pop("notice", None)
        self.cursor["usage"] = used
        note = object_value(self.cursor.get("notice"))
        raw, _ = self.content()
        signature = fingerprint(raw) if raw is not None else None
        text = None
        if used >= self.options["urgent_at"]:
            repeat = (signature == note.get("revision")
                      and used >= quantity(note.get("at"), 0) + self.options["repeat_after"])
            if note.get("level") != "urgent" or repeat:
                text = (f"Context meter: {used} tokens. Save a checkpoint at {self.document} "
                        "before starting another operation. Include the next action and unresolved checks.")
                self.cursor["notice"] = {"level": "urgent", "at": used, "revision": signature}
        elif used >= self.options["remind_at"] and not note:
            text = (f"Context meter: {used} tokens. Refresh {self.document} "
                    "at the next useful stopping point.")
            self.cursor["notice"] = {"level": "routine", "at": used, "revision": signature}
        self.persist()
        return text

    def archive(self, phase):
        from storage import log_records
        raw, complete = self.content()
        summary = self.event.get("compact_summary")
        summary = summary if isinstance(summary, str) else None
        if phase == "after" and summary is None:
            records, _ = log_records(self.event.get("transcript_path"))
            for record in records:
                if record.get("type") == "compacted":
                    summary = object_value(record.get("payload")).get("message")
                    summary = summary if isinstance(summary, str) else None
        if raw is None and not summary:
            return
        destination = self.directory / "archive" / uuid.uuid4().hex
        if raw is not None:
            replace_private(destination / "checkpoint.md", raw)
        if summary and phase == "after":
            replace_private(destination / "summary.txt", utf8_prefix(summary, 1024 * 1024).encode())
        receipt = {"phase": phase, "complete": complete, "checkpoint": str(self.document),
                   "sha256": fingerprint(raw) if raw is not None else None,
                   "has_summary": bool(summary) and phase == "after"}
        replace_private(destination / "receipt.json", json.dumps(receipt).encode())
        self.persist()

    def restore(self):
        self.cursor.pop("notice", None)
        self.cursor.pop("usage", None)
        self.persist()
        raw, complete = self.content()
        cap = self.options["restore_bytes"]
        path = utf8_prefix(str(self.document), min(300, cap // 6))
        lead = f"Checkpoint reference: {path}\nUse this saved record to resume; reconcile it with the latest request.\n\n"
        if raw is None:
            return utf8_prefix(lead + "No readable checkpoint exists. Establish the next step before proceeding.", cap)
        body = raw.decode("utf-8", errors="replace")
        if complete and len((lead + body).encode()) <= cap:
            return lead + body
        tail = f"\n[Excerpt only. Read the complete checkpoint at {path}.]\n"
        budget = cap - len((lead + tail).encode())
        # Divide the space between headings, independent of any template vocabulary.
        chunks = re.split(r"(?m)(?=^## )", body)
        chunks = [chunk for chunk in chunks if chunk.strip()]
        pieces = [utf8_prefix(chunk, max(0, budget // len(chunks) - 1)) for chunk in chunks] if chunks else []
        return utf8_prefix(lead + "\n".join(pieces), cap - len(tail.encode())) + tail
