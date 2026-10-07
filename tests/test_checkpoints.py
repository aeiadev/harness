#!/usr/bin/env python3
"""Exercise checkpoint behavior through the same commands the hosts receive."""

import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class CheckpointContract(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.home = self.base / "home"
        self.project = self.base / "project"
        self.home.mkdir()
        self.project.mkdir()
        (self.project / ".git").mkdir()
        self.environment = dict(os.environ, HOME=str(self.home),
                                XDG_STATE_HOME=str(self.base / "local"),
                                PYTHONDONTWRITEBYTECODE="1")
        self.environment.pop("CLAUDE_PROJECT_DIR", None)
        self.log = self.base / "events.jsonl"
        self.event = {"session_id": "fixture", "cwd": str(self.project),
                      "transcript_path": str(self.log)}
        self.document = self.project / "STATE.md"
        self.document.write_text("## Next move\nCheck the title validator.\n")
        for host in ("claude", "codex"):
            self.configure(host)

    def configure(self, host="claude", **overrides):
        directory = self.project / ("." + host)
        directory.mkdir(exist_ok=True)
        data = {"remind_at": 100, "urgent_at": 200, "window_tokens": 300,
                "repeat_after": 50, **overrides}
        (directory / "checkpoint.json").write_text(json.dumps(data))

    def invoke(self, event_name, host="claude", fields=None, raw=None, environment=None):
        path = ROOT / ("codex/hooks.json" if host == "codex" else "examples/settings.example.json")
        wiring = json.loads(path.read_text())["hooks"][event_name]
        commands = [item["command"] for group in wiring for item in group["hooks"]
                    if "/checkpoint/" in item["command"]]
        self.assertEqual(len(commands), 1, "Host needs one checkpoint handler for this event")
        words = shlex.split(commands[0])
        words[0] = sys.executable
        words[1] = str(ROOT / words[1].split("/harness/", 1)[1])
        payload = {**self.event, **({"model": "gpt-example"} if host == "codex" else {}), **(fields or {})}
        outcome = subprocess.run(words, input=raw if raw is not None else json.dumps(payload),
                                 text=True, capture_output=True, cwd=self.project,
                                 env=environment or self.environment, timeout=5)
        self.assertEqual(outcome.returncode, 0, outcome.stderr)
        self.assertEqual(outcome.stderr, "")
        if not outcome.stdout:
            return ""
        envelope = json.loads(outcome.stdout)["hookSpecificOutput"]
        self.assertEqual(envelope["hookEventName"], event_name)
        return envelope["additionalContext"]

    def usage(self, amount, host="claude"):
        item = {"type": "assistant", "message": {"usage": {"input_tokens": amount}}}
        if host == "codex":
            item = {"type": "event_msg", "payload": {"type": "token_count", "info": {
                "total_token_usage": {"input_tokens": 999999},
                "last_token_usage": {"input_tokens": amount, "cached_input_tokens": 40}}}}
        self.log.write_text(json.dumps(item))

    def recover(self, **kwargs):
        return self.invoke("SessionStart", fields={"source": "compact", **kwargs})

    def artifacts(self, filename):
        return list((self.base / "local").rglob(filename))

    def test_reminders_repeat_until_checkpoint_changes(self):
        self.usage(99)
        self.assertEqual(self.invoke("PostToolUse"), "")
        self.usage(100)
        self.assertIn(str(self.document), self.invoke("PostToolUse"))
        self.assertEqual(self.invoke("PostToolUse"), "")
        self.usage(200)
        self.assertIn("before starting", self.invoke("PostToolUse"))
        self.usage(249)
        self.assertEqual(self.invoke("PostToolUse"), "")
        self.usage(250)
        self.assertTrue(self.invoke("PostToolUse"))
        self.document.write_text("The validator passed. Next: inspect the diff.")
        self.usage(300)
        self.assertEqual(self.invoke("PostToolUse"), "")

    def test_both_hosts_recover_and_rearm_reminders(self):
        for host in ("claude", "codex"):
            with self.subTest(host=host):
                self.usage(200, host)
                self.assertTrue(self.invoke("PostToolUse", host))
                text = self.invoke("SessionStart", host, {"source": "compact"})
                self.assertIn(self.document.read_text(), text)
                self.usage(100, host)
                self.assertTrue(self.invoke("PostToolUse", host))

    def test_wiring_selects_host_without_model_or_turn_metadata(self):
        self.configure("codex", checkpoint_path="selected.md")
        (self.project / "selected.md").write_text("The configured Codex checkpoint")
        recovered = self.invoke("SessionStart", "codex", {"source": "compact", "model": None})
        self.assertIn("configured Codex checkpoint", recovered)

    def test_cached_usage_excludes_child_transcript_records(self):
        entries = [
            {"type": "assistant", "message": {"usage": {
                "input_tokens": 30, "cache_creation_input_tokens": 30, "cache_read_input_tokens": 40}}},
            {"type": "assistant", "isSidechain": True, "message": {"usage": {"input_tokens": 900}}},
        ]
        self.log.write_text("\n".join(json.dumps(item) for item in entries))
        self.assertIn("next useful stopping point", self.invoke("PostToolUse"))

    def test_large_transcript_does_not_reuse_an_out_of_range_summary(self):
        item = {"type": "compacted", "payload": {"message": "Outdated summary"}}
        self.log.write_text(json.dumps(item) + "\n" + "x" * 4194305 + "\n")
        self.invoke("PostCompact", "codex")
        self.assertEqual(self.artifacts("summary.txt"), [])
        self.assertEqual(len(self.artifacts("checkpoint.md")), 1)

    def test_untrusted_label_stays_in_local_storage(self):
        self.usage(100)
        self.invoke("PostToolUse", fields={"session_id": "../../escape"})
        self.assertFalse((self.base / "escape").exists())
        self.assertFalse((self.project / "escape").exists())
        self.assertTrue(self.invoke("PostToolUse", fields={"session_id": "different"}))

    def test_other_session_events_are_silent(self):
        for source in ("clear", "startup", "resume", ""):
            self.assertEqual(self.invoke("SessionStart", fields={"source": source}), "")

    def test_pin_retains_project_when_tool_changes_directory(self):
        self.usage(100)
        self.invoke("PostToolUse")
        other = self.base / "other"
        other.mkdir()
        (other / "STATE.md").write_text("Unrelated record")
        self.assertIn("title validator", self.recover(cwd=str(other)))

    def test_subdirectory_wins_over_environment_fallback(self):
        nested = self.project / "src"
        nested.mkdir()
        self.assertIn("title validator", self.invoke("SessionStart", fields={
            "source": "compact", "cwd": str(nested)}, environment={
            **self.environment, "CLAUDE_PROJECT_DIR": str(self.home)}))

    def test_child_and_distinct_session_do_not_consume_each_others_notice(self):
        self.usage(100)
        self.assertEqual(self.invoke("PostToolUse", fields={"agent_id": "child"}), "")
        self.assertTrue(self.invoke("PostToolUse"))
        self.assertTrue(self.invoke("PostToolUse", fields={"session_id": "separate"}))

    def test_response_estimate_can_trigger_save(self):
        self.usage(90)
        self.assertTrue(self.invoke("PostToolUse", fields={"tool_response": "x" * 100}))

    def test_configured_document_is_selected_for_each_host(self):
        for host in ("claude", "codex"):
            self.configure(host, checkpoint_path=host + ".md")
            (self.project / (host + ".md")).write_text("Selected for " + host)
            text = self.invoke("SessionStart", host, {"source": "compact"})
            self.assertIn("Selected for " + host, text)

    def test_traversal_and_external_paths_cannot_supply_checkpoint_text(self):
        outside = self.base / "external.md"
        outside.write_text("External material")
        for choice in (str(outside), "../external.md"):
            self.configure(checkpoint_path=choice)
            self.assertNotIn("External material", self.recover())
        self.document.unlink()
        self.document.symlink_to(outside)
        self.assertIn("No readable checkpoint", self.recover())

    def test_fifo_and_directory_are_not_read_as_documents(self):
        self.document.unlink()
        os.mkfifo(self.document)
        self.assertIn("No readable checkpoint", self.recover())
        self.document.unlink()
        self.document.mkdir()
        self.assertIn("No readable checkpoint", self.recover())

    def test_home_directory_does_not_supply_a_shared_checkpoint(self):
        (self.home / "STATE.md").write_text("Shared home material")
        recovered = self.recover(cwd=str(self.home))
        self.assertNotIn("Shared home material", recovered)
        self.assertIn("No readable checkpoint", recovered)

    def test_restore_byte_ceiling_and_unicode_boundaries(self):
        for size in (3600, 3999, 4000, 4095, 4096, 6000):
            self.document.write_text("界" * (size // 3))
            for host in ("claude", "codex"):
                with self.subTest(size=size, host=host):
                    text = self.invoke("SessionStart", host, {"source": "compact"})
                    self.assertLessEqual(len(text.encode()), 4000)
                    self.assertNotIn("\ufffd", text)
                    self.assertTrue(self.document.read_text() in text or "Excerpt only" in text)

    def test_excerpt_preserves_custom_headings(self):
        self.configure(restore_bytes=1000)
        headings = ["First action", "Untested work", "Why this approach"]
        self.document.write_text("\n".join("## " + h + "\n" + "detail\n" * 600 for h in headings))
        text = self.recover()
        self.assertLessEqual(len(text.encode()), 1000)
        for heading in headings:
            self.assertIn("## " + heading, text)

    def test_snapshots_and_available_summaries_are_private_and_collision_free(self):
        self.invoke("PreCompact")
        for _ in range(2):
            self.invoke("PostCompact", fields={"compact_summary": "The next check is ready."})
        snapshots = self.artifacts("checkpoint.md")
        self.assertEqual(len(snapshots), 3)
        self.assertTrue(all(p.read_bytes() == self.document.read_bytes() for p in snapshots))
        self.assertEqual(len(self.artifacts("summary.txt")), 2)
        for file in (self.base / "local/claude-harness").rglob("*"):
            self.assertEqual(file.stat().st_mode & 0o777, 0o700 if file.is_dir() else 0o600)
        self.assertEqual(list(self.project.glob("**/receipt.json")), [])
        for receipt in self.artifacts("receipt.json"):
            self.assertEqual(json.loads(receipt.read_text())["sha256"],
                             hashlib.sha256(self.document.read_bytes()).hexdigest())

    def test_latest_rollout_summary_wins_and_encrypted_summary_is_not_invented(self):
        records = [{"type": "compacted", "payload": {"message": value}}
                   for value in ("Obsolete", "Current summary")]
        self.log.write_text("\n".join(json.dumps(item) for item in records))
        self.invoke("PostCompact", "codex")
        self.assertEqual([p.read_text() for p in self.artifacts("summary.txt")], ["Current summary"])
        records.append({"type": "compacted", "payload": {"message": "", "encrypted_content": "opaque"}})
        self.log.write_text("\n".join(json.dumps(item) for item in records))
        self.invoke("PostCompact", "codex")
        self.assertEqual(len(self.artifacts("summary.txt")), 1)
        self.assertEqual(len(self.artifacts("checkpoint.md")), 2)

    def test_unusable_storage_does_not_prevent_recovery(self):
        blocked = self.base / "blocked"
        blocked.write_text("occupied")
        environment = {**self.environment, "XDG_STATE_HOME": str(blocked)}
        self.usage(100)
        self.assertTrue(self.invoke("PostToolUse", environment=environment))
        self.assertIn("title validator", self.invoke("SessionStart", fields={"source": "compact"},
                                                      environment=environment))

    def test_invalid_inputs_and_invalid_options_do_not_break_host(self):
        for name in ("PostToolUse", "PreCompact", "PostCompact", "SessionStart"):
            for raw in ("", "{", "[]", "null"):
                self.assertEqual(self.invoke(name, raw=raw), "")
        self.configure(remind_at=None, urgent_at=[], window_tokens="bad", restore_bytes=float("inf"),
                       checkpoint_path={"bad": True})
        self.usage(190000)
        self.assertTrue(self.invoke("PostToolUse"))

    def test_transcript_links_and_special_files_are_not_followed(self):
        source = self.base / "source"
        source.write_text(json.dumps({"type": "assistant", "message": {"usage": {"input_tokens": 100}}}))
        self.log.symlink_to(source)
        self.assertEqual(self.invoke("PostToolUse"), "")
        self.log.unlink()
        os.mkfifo(self.log)
        self.assertEqual(self.invoke("PostToolUse"), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
