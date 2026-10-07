#!/usr/bin/env python3
"""Standalone checks of the wait inspector and its text blanking mode."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

HOOK = Path(__file__).resolve().parents[1] / "hooks" / "guard" / "wait-loop-guard.py"


class WaitLoopTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="harness-wait-")
        self.addCleanup(self.temp.cleanup)
        self.env = dict(os.environ, HOME=self.temp.name, PYTHONDONTWRITEBYTECODE="1")

    def call(self, command, *args):
        return subprocess.run(["python3", str(HOOK), *args], input=command, text=True, capture_output=True, cwd=self.temp.name, env=self.env, timeout=5)

    def test_wait_requires_bound(self):
        result = self.call("until test -f ready; do sleep 1; done")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("timeout", result.stdout)

    def test_timeout_and_counter(self):
        for command in ("timeout 10 bash -c 'while true; do sleep 1; done'", "while [ $attempt -lt 5 ]; do ((attempt++)); sleep 1; done", "while (( SECONDS < 10 )); do sleep 1; done"):
            with self.subTest(command=command):
                result = self.call(command)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_quoted_counter_text_is_not_progress(self):
        for command in ("while [ $attempt -lt 5 ]; do echo '((attempt++))'; sleep 1; done", "attempt=0; while [ $attempt -lt 5 ]; do echo attempt+=1; sleep 1; done"):
            with self.subTest(command=command):
                result = self.call(command)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)

    def test_c_style_loop_requires_a_bound(self):
        for command in ("for ((;;)); do sleep 1; done", "for (( ; ; )); do :; done",
                        "for ((attempt=0; attempt<5; attempt--)); do sleep 1; done",
                        "for ((attempt=0; attempt<5; )); do sleep 1; done",
                        "bash -c 'for ((;;)); do sleep 1; done'"):
            with self.subTest(command=command):
                result = self.call(command)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)

    def test_bounded_and_quoted_c_style_loops(self):
        for command in ("for ((attempt=0; attempt<5; attempt++)); do sleep 1; done",
                        "for ((attempt=5; attempt>0; attempt--)); do sleep 1; done",
                        "for ((; SECONDS<10; )); do sleep 1; done",
                        "for ((; 0; )); do sleep 1; done",
                        "timeout 10 bash -c 'for ((;;)); do sleep 1; done'",
                        "echo 'for ((;;)); do sleep 1; done'"):
            with self.subTest(command=command):
                result = self.call(command)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_counter_and_clock_must_advance_toward_exit(self):
        for command in ('while [ "$attempt" -lt 3 ]; do sleep 1; ((attempt--)); done', 'while [ "$SECONDS" -gt 10 ]; do sleep 1; done', 'until [ "$SECONDS" -lt 10 ]; do sleep 1; done'):
            with self.subTest(command=command):
                result = self.call(command)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)

    def test_strip_preserves_code_and_blanks_data(self):
        result = self.call("echo 'git reset --hard'; git status\ncat <<'EOF'\nrm -rf /\nEOF\n", "--strip")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("git status", result.stdout)
        self.assertNotIn("reset", result.stdout)
        self.assertNotIn("rm -rf", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
