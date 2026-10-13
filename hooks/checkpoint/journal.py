"""A session binds once to a checkpoint and keeps its receipts outside Git."""

import json
import os
from pathlib import Path
import uuid

from project_root import contained_file, project_root, valid_pin
from state_excerpt import READ_LIMIT, read_state, state_excerpt
from storage import (conversation_tag, fingerprint, guarded_read, json_object,
                     local_cache, object_value, quantity, replace_private,
                     rollout_window, uses_codex, utf8_prefix)


def project_anchor(event):
    return project_root(event)[0]


def preferences(event, base=None):
    defaults = {"checkpoint_path": "STATE.md", "window_tokens": 280000,
                "repeat_after": 12000, "restore_bytes": 4000,
                "restore_order": ["Next move", "Objective", "In flight"]}
    sources = [Path(__file__).with_name("policy.json")]
    base = base or project_anchor(event)
    if base:
        config = contained_file(base, (".codex" if uses_codex(event) else ".claude") + "/checkpoint.json")
        if config is not None:
            sources.append(config)
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
        self.document = None
        binding = object_value(self.cursor.get("binding"))
        old = binding.get("directory")
        draft = self.directory / "draft"
        pinned = valid_pin(old)
        if old == str(draft):
            pinned = draft
        if self.anchor is not None and pinned is not None and self.anchor != pinned:
            self.cursor.pop("notice", None)
            self.cursor.pop("usage", None)
        elif self.anchor is None and pinned is not None:
            self.anchor = pinned
        if self.anchor == pinned:
            self.document = contained_file(self.anchor, binding.get("document"))
        if self.document is None:
            self.anchor = self.anchor or draft
        self.options = preferences(event, self.anchor)
        if self.document is None:
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
        return guarded_read(self.document, READ_LIMIT)

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
        safe = contained_file(self.anchor, str(self.document.relative_to(self.anchor)))
        raw, complete = read_state(self.document) if safe is not None else (None, False)
        cap = self.options["restore_bytes"]
        path = utf8_prefix(str(self.document), min(300, cap // 6))
        lead = f"Checkpoint reference: {path}\nUse this saved record to resume; reconcile it with the latest request.\n\n"
        from facts import free_facts
        lead += free_facts(self.anchor, self.document)
        if raw is None:
            return utf8_prefix(lead + "No readable checkpoint exists. Establish the next step before proceeding.", cap)
        tail = f"\n[Excerpt only. Read the complete checkpoint at {path}.]\n"
        full_budget = cap - len(lead.encode())
        # Reserve the notice before formatting, then omit it when everything fits.
        body, truncated = state_excerpt(raw, complete, full_budget,
                                        self.options["restore_order"])
        if not truncated:
            return lead + body
        body, _ = state_excerpt(raw, complete, full_budget - len(tail.encode()),
                                self.options["restore_order"])
        return lead + body + tail
