"""Standalone Harness usage CLI, exercised as a separate process."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from _platform import home_env


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "hooks/checkpoint/usage_cli.py"


class UsageCLI(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="harness-usage-cli-")
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.home = self.base / "home"
        self.home.mkdir()
        self.env = {**os.environ, **home_env(self.home),
                    "XDG_STATE_HOME": str(self.base / "state"),
                    "PYTHONDONTWRITEBYTECODE": "1"}
        self.state = self.base / "state/claude-harness/usage"

    def run_cli(self, *args, input=None):
        return subprocess.run([sys.executable, str(CLI), *args], input=input,
                              text=True, capture_output=True, env=self.env,
                              cwd=self.base, timeout=10)

    def test_report_absent_state_and_syntax(self):
        result = self.run_cli("report")
        self.assertEqual((result.returncode, result.stderr), (0, ""), result.stderr)
        self.assertEqual(json.loads(result.stdout)["tasks"], [])
        self.assertFalse(self.state.exists())
        self.assertEqual(self.run_cli("record").returncode, 2)

    def test_stdin_declare_record_replay_and_fresh_process(self):
        declaration = {"host": "claude", "expected_event_ids": ["e1"], "outcome": "returned"}
        result = self.run_cli("declare", "task1", "--file", "-", input=json.dumps(declaration))
        self.assertEqual(result.returncode, 0, result.stderr)
        event = {"adapter": "claude-message", "context": {
            "event_id": "e1", "attempt_id": "a1", "stage": "worker", "outcome": "returned"},
            "payload": {"type": "assistant", "message": {"usage": {
                "input_tokens": 5, "output_tokens": 3, "cache_read_input_tokens": 2,
                "cache_creation_input_tokens": 1}}}}
        for _ in range(2):
            result = self.run_cli("record", "task1", "--file", "-", input=json.dumps(event))
            self.assertEqual(result.returncode, 0, result.stderr)
        report = self.run_cli("report", "task1")
        self.assertEqual(report.returncode, 0, report.stderr)
        self.assertEqual((json.loads(report.stdout)["tasks"][0]["event_count"],
                          json.loads(report.stdout)["tasks"][0]["known_tokens"]), (1, 11))
        self.assertEqual(report.stdout, json.dumps(json.loads(report.stdout),
                                                 sort_keys=True, separators=(",", ":")) + "\n")

    def test_bad_inputs_no_write_and_content_free_errors(self):
        secret = "PRIVATE_PAYLOAD_SENTINEL"
        bad = self.base / "bad.json"
        bad.write_text('{"private":"' + secret + '"}')
        for args, data in [(("declare", "bad", "--file", str(bad)), None),
                           (("declare", "bad", "--file", "-"), "{" + secret),
                           (("declare", "bad", "--file", "-"), " " * 65537)]:
            result = self.run_cli(*args, input=data)
            self.assertEqual(result.returncode, 1)
            self.assertLessEqual(len(result.stderr), 160)
            self.assertNotIn(secret, result.stderr + result.stdout)
            self.assertNotIn(str(bad), result.stderr)
        self.assertFalse(self.state.exists())
        self.assertEqual(list(self.home.iterdir()), [])

    def test_invalid_record_keeps_existing_report_private(self):
        declaration = {"host": "codex", "expected_event_ids": ["e1"], "outcome": "unknown"}
        created = self.run_cli("declare", "task1", "--file", "-", input=json.dumps(declaration))
        self.assertEqual(created.returncode, 0, created.stderr)
        before = self.run_cli("report").stdout
        secret = "PRIVATE_PAYLOAD_SENTINEL"
        invalid = {"adapter": "codex-turn", "context": {
            "event_id": "e1", "attempt_id": "a1", "stage": "worker", "outcome": "unknown"},
            "payload": {"type": "turn.completed", "usage": {"input_tokens": -1},
                        "prompt": secret}}
        result = self.run_cli("record", "task1", "--file", "-", input=json.dumps(invalid))
        self.assertEqual(result.returncode, 1)
        self.assertLessEqual(len(result.stderr), 160)
        self.assertNotIn(secret, result.stderr + result.stdout)
        self.assertEqual(self.run_cli("report").stdout, before)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO requires POSIX")
    def test_fifo_rejected_without_waiting(self):
        fifo = self.base / "pipe"
        os.mkfifo(fifo)
        result = self.run_cli("declare", "task1", "--file", str(fifo))
        self.assertEqual(result.returncode, 1)
        self.assertFalse(self.state.exists())


if __name__ == "__main__":
    unittest.main()
