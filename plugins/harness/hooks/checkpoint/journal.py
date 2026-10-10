"""A session binds once to a checkpoint and keeps its receipts outside Git."""

import json
import os
from pathlib import Path
import re
import uuid

from storage import (conversation_tag, fingerprint, guarded_read, json_object,
                     local_cache, object_value, quantity, replace_private,
                     rollout_window, uses_codex, utf8_prefix)


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
    defaults = {"checkpoint_path": "STATE.md", "window_tokens": 280000,
                "repeat_after": 12000, "restore_bytes": 4000,
                "restore_order": ["Next move", "Objective", "In flight"]}
    sources = [Path(__file__).with_name("policy.json")]
    base = base or project_anchor(event)
    if base:
        sources.append(base / (".codex" if uses_codex(event) else ".claude") / "checkpoint.json")
    result = dict(defaults)
    for source in sources:
        for key, value in json_object(source).items():
            if key in defaults or key in ("remind_at", "urgent_at"):
                result[key] = value
    for key in defaults.keys() - {"checkpoint_path", "restore_order"}:
        result[key] = int(quantity(result[key], defaults[key]))
    order = result["restore_order"]
    if (not isinstance(order, list) or not order or not all(isinstance(item, str) and item.strip()
                                                 for item in order)):
        result["restore_order"] = defaults["restore_order"]
    for key in ("remind_at", "urgent_at"):
        if key in result:
            result[key] = quantity(result[key])
            if not result[key] or result[key] <= 0:
                result.pop(key)
    if not result["window_tokens"]:
        result["window_tokens"] = defaults["window_tokens"]
    # Explicit thresholds are checked once, against the real window, in window_and_thresholds().
    result["repeat_after"] = max(1, result["repeat_after"])
    result["restore_bytes"] = max(1000, min(4000, result["restore_bytes"]))
    if contained_file(base or Path.cwd(), result["checkpoint_path"]) is None:
        result["checkpoint_path"] = defaults["checkpoint_path"]
    return result


def window_and_thresholds(event, options):
    context = object_value(event.get("context_window"))
    live = quantity(context.get("context_window_size"))
    if live and live > 0:
        window, source = int(live), "statusline"
    else:
        rollout = rollout_window(event) if uses_codex(event) else None
        if rollout:
            window, source = rollout, "rollout"
        else:
            from pressure import last_statusline_window
            remembered = last_statusline_window(event) if not uses_codex(event) else None
            window, source = ((remembered, "statusline") if remembered else
                              (options["window_tokens"], "policy"))
    remind = options.get("remind_at", window * .65)
    urgent = options.get("urgent_at", window * .85)
    if "remind_at" in options and "urgent_at" not in options and urgent <= remind < window:
        urgent = window
    # Two explicit thresholds still need an ordered interval inside the live window.
    if not 0 < remind < urgent <= window or ("urgent_at" in options and urgent == window):
        remind, urgent = window * .65, window * .85
    return window, source, remind, urgent


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
        window, source, remind, urgent = window_and_thresholds(self.event, self.options)
        response = self.event.get("tool_response")
        if response is not None and used:
            used += len(json.dumps(response, ensure_ascii=False).encode()) // 4
        from pressure import write
        write(self.event, used, window, source, remind, urgent)
        previous = quantity(self.cursor.get("usage"), 0)
        if used < previous // 2:
            self.cursor.pop("notice", None)
        self.cursor["usage"] = used
        note = object_value(self.cursor.get("notice"))
        raw, _ = self.content()
        signature = fingerprint(raw) if raw is not None else None
        text = None
        if used >= urgent:
            repeat = (signature == note.get("revision")
                      and used >= quantity(note.get("at"), 0) + self.options["repeat_after"])
            if note.get("level") != "urgent" or repeat:
                text = (f"Context meter: {used} tokens. Save a checkpoint at {self.document} "
                        "before starting another operation. Include the next action, what is In flight, and unresolved checks.")
                self.cursor["notice"] = {"level": "urgent", "at": used, "revision": signature}
        elif used >= remind and not note:
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
        from facts import free_facts
        lead += free_facts(self.anchor, self.document)
        if raw is None:
            return utf8_prefix(lead + "No readable checkpoint exists. Establish the next step before proceeding.", cap)
        body = raw.decode("utf-8", errors="replace")
        sections = [part for part in re.split(r"(?im)(?=^## )", body) if part.strip()]
        preamble = []
        if sections and not sections[0].startswith("## "):
            preamble = [sections.pop(0)]
        def heading(part):
            first = part.splitlines()[0]
            return first[3:].strip().casefold() if first.startswith("## ") else ""
        priorities = [name.casefold() for name in self.options["restore_order"]]
        indices = []
        for name in priorities:
            indices.extend(index for index, part in enumerate(sections)
                           if heading(part) == name and index not in indices)
        indices.extend(index for index in range(len(sections)) if index not in indices)
        ordered = preamble + [sections[index] for index in indices]
        body = "\n\n".join(part.rstrip("\n") for part in ordered) + "\n" if ordered else ""
        if complete and len((lead + body).encode()) <= cap:
            return lead + body
        tail = f"\n[Excerpt only. Read the complete checkpoint at {path}.]\n"
        budget = cap - len((lead + tail).encode())
        headers = [part.split("\n", 1)[0] + "\n" if part.startswith("## ") else ""
                   for part in ordered]
        content_budget = max(0, budget - sum(len(h.encode()) for h in headers)
                             - 2 * max(0, len(ordered) - 1))
        share = content_budget // max(1, len(ordered))
        pieces = []
        for number, (part, header) in enumerate(zip(ordered, headers)):
            content = part[len(header):]
            if len(content.encode()) <= share:
                excerpt = content
            elif preamble and number == 0:
                excerpt = ""
                for line in content.splitlines(keepends=True):
                    if len((excerpt + line).encode()) > share:
                        break
                    excerpt += line
                if not excerpt:
                    excerpt = utf8_prefix(content, share)
            else:
                lines = content.splitlines(keepends=True)
                while lines and not lines[-1].strip():
                    lines.pop()
                newest = []
                remaining = share
                for line in reversed(lines):
                    if len(line.encode()) > remaining:
                        break
                    newest.append(line)
                    remaining -= len(line.encode())
                excerpt = "".join(reversed(newest))
                if not excerpt and lines and share >= 2:
                    kept = lines[-1].rstrip("\r\n").encode()[-(share - 1):]
                    excerpt = kept.decode("utf-8", "ignore") + "\n"
            pieces.append((header + excerpt).rstrip("\n"))
        return utf8_prefix(lead + "\n\n".join(pieces), cap - len(tail.encode())) + tail
