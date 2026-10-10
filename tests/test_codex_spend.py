#!/usr/bin/env python3
"""Offline Codex spend checks using synthetic hook and rollout payloads."""

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]


class SpendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="harness-spend-test-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.home = self.base / "home"
        self.home.mkdir()
        self.env = dict(os.environ, HOME=str(self.home), XDG_STATE_HOME=str(self.base / "xdg"),
                        PYTHONDONTWRITEBYTECODE="1")
        for key in ("HARNESS_CODEX_RATES", "HARNESS_SPEND_MESSAGE_SECONDS", "HARNESS_SPEND_WARN"):
            self.env.pop(key, None)
        self.transcript = self.base / "rollout.jsonl"
        self.payload = {"session_id": "spend-fixture", "turn_id": "fixture-turn",
                        "model": "gpt-6-sol", "cwd": str(self.base),
                        "transcript_path": str(self.transcript), "hook_event_name": "Stop"}
        self.key = hashlib.sha256(self.payload["session_id"].encode()).hexdigest()
        self.directory = self.base / "xdg/claude-harness/spend"

    def run_hook(self, payload=None, env=None):
        result = subprocess.run([sys.executable, str(REPO / "statusline/spend_hook.py")],
                                input=json.dumps(payload if payload is not None else self.payload),
                                capture_output=True, text=True, env=env or self.env,
                                cwd=self.base, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout) if result.stdout else None

    def side_text(self):
        return (self.directory / (self.key + ".txt")).read_text().strip()

    def side_file(self):
        return self.directory / (self.key + ".txt")

    def rates(self, **extra):
        rates = {"gpt-6-sol": {"input": 2, "cached_input": 1, "output": 4}}
        rates.update(extra)
        return dict(self.env, HARNESS_CODEX_RATES=json.dumps(rates))

    def rollout(self, usages, model="gpt-6-sol"):
        entries = [{"type": "turn_context", "payload": {"model": model}}]
        for incoming, cached, outgoing in usages:
            entries.append({"type": "event_msg", "payload": {"type": "token_count", "info": {
                "total_token_usage": {"input_tokens": incoming, "cached_input_tokens": cached,
                                      "output_tokens": outgoing}}}})
        self.transcript.write_text("\n".join(json.dumps(entry) for entry in entries) + "\n")

    def test_missing_prices_never_invent_spend(self):
        self.rollout([(1000000, 0, 1000000)])
        message = self.run_hook()
        self.assertEqual(self.side_text(), "2000.0k tok")
        self.assertIn("2000.0k tok", message["systemMessage"])

    def test_unpriced_turn_shows_tokens_and_incomplete_usage_does_not(self):
        self.run_hook({**self.payload, "type": "turn.completed", "usage": {
            "input_tokens": 12000, "cached_input_tokens": 0, "output_tokens": 300}},
            env=self.rates())
        self.assertEqual(self.side_text(), "$0.03")
        self.run_hook({**self.payload, "type": "turn.completed", "model": "unknown-model",
                       "usage": {"input_tokens": 12000, "cached_input_tokens": 0,
                                 "output_tokens": 300}}, env=self.rates())
        self.assertEqual(self.side_text(), "12.3k tok")
        self.run_hook({**self.payload, "type": "turn.completed", "model": "unknown-model",
                       "usage": {"input_tokens": 12000}}, env=self.rates())
        self.assertFalse(self.side_file().exists())

    def test_incomplete_usage_removes_stale_spend_and_reports_exact_message(self):
        self.run_hook({**self.payload, "cost": {"total_cost_usd": 1.5}})
        self.assertEqual(self.side_text(), "$1.50")
        marker = self.directory / (self.key + ".json")
        marks = json.loads(marker.read_text())
        marks["message_at"] = 0
        marker.write_text(json.dumps(marks))
        message = self.run_hook({**self.payload, "type": "turn.completed",
                                 "usage": {"input_tokens": 100}})
        self.assertFalse(self.side_file().exists())
        self.assertIsNotNone(re.fullmatch(
            r"\[harness\] spend unavailable: supply cost or configure HARNESS_CODEX_RATES",
            message["systemMessage"]))
        self.assertEqual(json.loads(marker.read_text())["segment"], "")

    def test_reported_cost_matches_statusline_segment(self):
        data = {**self.payload, "cost": {"total_cost_usd": 1.234}}
        self.run_hook(data)
        self.assertEqual(self.side_text(), "$1.23")
        result = subprocess.run([sys.executable, str(REPO / "statusline/statusline.py")],
                                input=json.dumps(data), text=True, capture_output=True,
                                env=self.env, cwd=self.base, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip().split(" | ")[-1], self.side_text())

    def test_cumulative_usage_is_not_double_counted_and_cache_is_subtracted(self):
        self.rollout([(1000000, 250000, 100000), (1000000, 250000, 100000),
                      (2000000, 500000, 200000)])
        message = self.run_hook(env=self.rates())
        self.assertEqual(self.side_text(), "$4.30")
        self.assertIn("configured-rate estimate", message["systemMessage"])

    def test_model_changes_are_priced_per_delta(self):
        self.rollout([(1000000, 0, 0)])
        with self.transcript.open("a") as stream:
            for entry in [{"type": "turn_context", "payload": {"model": "gpt-6-luna"}},
                          {"type": "event_msg", "payload": {"type": "token_count", "info": {
                              "total_token_usage": {"input_tokens": 2000000,
                                  "cached_input_tokens": 0, "output_tokens": 0}}}}]:
                stream.write(json.dumps(entry) + "\n")
        self.run_hook(env=self.rates(**{"gpt-6-luna": {"input": 1, "cached_input": 0.5, "output": 2}}))
        self.assertEqual(self.side_text(), "$3.00")

    def test_turn_completed_json_usage(self):
        self.transcript.write_text("\n".join(json.dumps({"type": "turn.completed", "usage": {
            "input_tokens": 1000000, "cached_input_tokens": 0, "output_tokens": 0}})
            for _ in range(2)) + "\n")
        self.run_hook(env=self.rates())
        self.assertEqual(self.side_text(), "$4.00")

    def test_direct_turn_completed_payload(self):
        self.run_hook({**self.payload, "type": "turn.completed", "usage": {
            "input_tokens": 1000000, "cached_input_tokens": 0, "output_tokens": 0}}, env=self.rates())
        self.assertEqual(self.side_text(), "$2.00")

    def test_messages_are_throttled_across_stop_and_posttooluse(self):
        data = {**self.payload, "cost": {"total_cost_usd": 1}}
        self.assertIsNotNone(self.run_hook(data))
        self.assertIsNone(self.run_hook({**data, "hook_event_name": "PostToolUse"}))
        marker = self.directory / (self.key + ".json")
        marks = json.loads(marker.read_text())
        marks["message_at"] = 0
        marker.write_text(json.dumps(marks))
        self.assertIsNotNone(self.run_hook(data))

    def test_side_file_is_private_and_ignores_color_threshold(self):
        self.run_hook({**self.payload, "cost": {"total_cost_usd": 3}},
                      env=dict(self.env, HARNESS_SPEND_WARN="1"))
        self.assertEqual(self.side_text(), "$3.00")
        self.assertEqual(self.directory.stat().st_mode & 0o777, 0o700)
        for path in self.directory.iterdir():
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_bad_rates_usage_or_unknown_model_are_unavailable(self):
        for rates in ("{bad", "[]", '{"gpt-6-sol": {"input": 1}}',
                      '{"gpt-6-sol":{"input":NaN,"cached_input":1,"output":1}}'):
            with self.subTest(rates=rates):
                self.rollout([(1000000, 0, 0)])
                self.run_hook(env=dict(self.env, HARNESS_CODEX_RATES=rates))
                self.assertEqual(self.side_text(), "1000.0k tok")
        self.rollout([(1000000, 0, 0)], model="unknown-model")
        self.run_hook(env=self.rates())
        self.assertEqual(self.side_text(), "1000.0k tok")
        self.rollout([(1, 2, 0)])
        self.run_hook(env=self.rates())
        self.assertFalse(self.side_file().exists())

    def test_large_partial_transcript_does_not_understate_cost(self):
        self.rollout([(1000000, 0, 0)])
        content = self.transcript.read_text()
        self.transcript.write_text("x" * 4194305 + "\n" + content)
        self.run_hook(env=self.rates())
        self.assertFalse(self.side_file().exists())

    def test_malformed_history_is_unavailable_but_model_free_usage_shows_tokens(self):
        self.rollout([(1000000, 0, 0)])
        with self.transcript.open("a") as stream:
            stream.write("{malformed\n")
        self.run_hook(env=self.rates())
        self.assertFalse(self.side_file().exists())
        self.rollout([(1000000, 0, 0)])
        lines = self.transcript.read_text().splitlines()
        self.transcript.write_text("\n".join(lines[1:]) + "\n")
        self.run_hook(env=self.rates())
        self.assertEqual(self.side_text(), "1000.0k tok")

    def test_fifo_and_symlink_transcripts_do_not_hang(self):
        os.mkfifo(self.transcript)
        self.run_hook(env=self.rates())
        self.assertFalse(self.side_file().exists())
        self.transcript.unlink()
        self.transcript.symlink_to(self.base / "absent")
        self.run_hook(env=self.rates())
        self.assertFalse(self.side_file().exists())

    def test_missing_session_or_child_never_overwrites_parent_spend(self):
        self.assertIsNone(self.run_hook({"model": "gpt-6-sol"}))
        self.assertIsNone(self.run_hook({**self.payload, "agent_id": "child"}))
        self.assertFalse(self.directory.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
