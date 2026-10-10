#!/usr/bin/env python3
"""Standalone status-line checks with no network, transcript, or account required."""

import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]


class StatuslineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="harness-status-test-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.home = self.base / "home"
        self.home.mkdir()
        self.env = dict(os.environ, HOME=str(self.home), XDG_STATE_HOME=str(self.base / "xdg"),
                        PYTHONDONTWRITEBYTECODE="1")
        self.env.pop("CLAUDE_PROJECT_DIR", None)
        self.env.pop("HARNESS_SPEND_WARN", None)

    def render(self, data=None, raw=None, warn=None):
        env = dict(self.env)
        if warn is not None:
            env["HARNESS_SPEND_WARN"] = warn
        result = subprocess.run([sys.executable, str(REPO / "statusline/statusline.py")],
                                input=raw if raw is not None else json.dumps(data or {}),
                                capture_output=True, text=True, env=env, cwd=self.base, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return result.stdout

    def test_live_spend_and_context_percentage(self):
        result = self.render({"cost": {"total_cost_usd": 1.234},
                              "context_window": {"used_percentage": 42}})
        self.assertIn("$1.23", result)
        self.assertIn("ctx 42%", result)
        self.assertNotIn("\033[", result, "spend coloring must be opt-in")

    def test_current_usage_includes_cached_input(self):
        result = self.render({"cost": {"total_cost_usd": 0}, "context_window": {
            "context_window_size": 200000, "current_usage": {
                "input_tokens": 10000, "cache_read_input_tokens": 50000,
                "cache_creation_input_tokens": 20000, "output_tokens": 10000}}})
        self.assertIn("ctx 40%", result)
        self.assertIn("$0.00", result)

    def test_cache_hit_percent_uses_input_only(self):
        result = self.render({"context_window": {"current_usage": {
            "input_tokens": 10000, "cache_creation_input_tokens": 20000,
            "cache_read_input_tokens": 50000, "output_tokens": 80000}}})
        self.assertIn("cache 62%", result)

    def test_cache_hit_absent_for_zero_missing_or_non_numeric_usage(self):
        for usage in ({}, {"input_tokens": 0, "cache_read_input_tokens": 0},
                      {"input_tokens": "bad", "cache_read_input_tokens": 10},
                      {"input_tokens": 10, "cache_read_input_tokens": []}):
            with self.subTest(usage=usage):
                self.assertNotIn("cache ", self.render({"context_window": {
                    "current_usage": usage}}))

    def test_spend_warning_only_above_explicit_threshold(self):
        payload = {"cost": {"total_cost_usd": 1.25}}
        self.assertIn("\033[33m$1.25\033[0m", self.render(payload, warn="1.00"))
        self.assertNotIn("\033[", self.render(payload, warn="1.25"))
        self.assertNotIn("\033[", self.render(payload, warn="2"))
        for value in ("", "bad", "NaN", "inf", "-1"):
            with self.subTest(value=value):
                self.assertNotIn("\033[", self.render(payload, warn=value))

    def test_missing_and_malformed_fields_do_not_crash_or_invent_cost(self):
        for payload in ({}, {"cost": None}, {"cost": []}, {"context_window": []},
                        {"cost": {"total_cost_usd": "bad"}},
                        {"cost": {"total_cost_usd": float("nan")}},
                        {"cost": {"total_cost_usd": float("inf")}},
                        {"cost": {"total_cost_usd": True}},
                        {"context_window": {"used_percentage": {}, "current_usage": False}}):
            with self.subTest(payload=payload):
                result = self.render(payload)
                self.assertIn("$--", result)
                self.assertIn("ctx --", result)

    def test_invalid_json_is_silent(self):
        for raw in ("", "{bad", "null", "[]", "42"):
            with self.subTest(raw=raw):
                self.assertEqual(self.render(raw=raw), "")

    def test_cost_alone_does_not_require_session_or_transcript(self):
        self.assertIn("$3.50", self.render({"cost": {"total_cost_usd": 3.5}}))

    def test_fallback_transcript_context_and_meter_marks(self):
        transcript = self.base / "transcript.jsonl"
        transcript.write_text(json.dumps({"type": "assistant", "message": {"usage": {
            "input_tokens": 250000}}}) + "\n")
        payload = {"session_id": "status-test", "cwd": str(self.base),
                   "transcript_path": str(transcript), "cost": {"total_cost_usd": 0.42}}
        meter = subprocess.run([sys.executable, str(REPO / "hooks/checkpoint/dispatch.py"), "check"],
                               input=json.dumps(payload), text=True, capture_output=True,
                               env=self.env, cwd=self.base, timeout=5)
        self.assertEqual(meter.returncode, 0, meter.stderr)
        self.assertIn("Save a checkpoint", meter.stdout)
        result = self.render(payload)
        self.assertIn("ctx 250k/280k", result)
        self.assertIn("checkpoint urgent", result)
        self.assertIn("$0.42", result)

    def test_payload_context_takes_precedence_over_transcript(self):
        result = self.render({"context_window": {"used_percentage": 0},
                              "transcript_path": "/nonexistent", "cost": {"total_cost_usd": 0}})
        self.assertIn("ctx 0%", result)

    def test_statusline_window_is_remembered_by_checkpoint_hook(self):
        data = {"session_id": "live-window", "cwd": str(self.base),
                "context_window": {"context_window_size": 200000,
                                   "current_usage": {"input_tokens": 100000}}}
        self.render(data)
        path = self.base / "xdg/claude-harness/pressure" / (
            hashlib.sha256(b"live-window").hexdigest()[:16] + ".json")
        self.assertEqual(json.loads(path.read_text())["window_source"], "statusline")
        transcript = self.base / "usage.jsonl"
        transcript.write_text(json.dumps({"type": "assistant", "message": {
            "usage": {"input_tokens": 130000}}}) + "\n")
        result = subprocess.run([sys.executable, str(REPO / "hooks/checkpoint/dispatch.py"),
                                 "check"], input=json.dumps({"session_id": "live-window",
                                 "cwd": str(self.base), "transcript_path": str(transcript)}),
                                text=True, capture_output=True, env=self.env,
                                cwd=self.base, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Refresh", result.stdout)
        self.assertEqual(json.loads(path.read_text())["window_source"], "statusline")


if __name__ == "__main__":
    unittest.main(verbosity=2)
