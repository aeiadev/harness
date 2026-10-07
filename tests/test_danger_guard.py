#!/usr/bin/env python3
"""Standalone subprocess tests. Fixture commands are never executed."""

import json
import os
from pathlib import Path
import pwd
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hooks" / "guard" / "danger-cmd-guard.sh"


class DangerGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="harness-guard-")
        self.addCleanup(self.temp.cleanup)
        self.env = dict(os.environ, HOME=self.temp.name, XDG_STATE_HOME=self.temp.name + "/state", PYTHONDONTWRITEBYTECODE="1")

    def call(self, command, background=False, tool="Bash", host="claude"):
        payload = {"tool_name": tool, "tool_input": {"command": command, "run_in_background": background}}
        if host == "codex":
            payload.update(hook_event_name="PreToolUse", turn_id="fixture-turn", permission_mode="default")
        if tool in {"exec_command", "functions.exec_command", "shell_command", "functions.shell_command"}:
            payload["tool_input"]["cmd"] = payload["tool_input"].pop("command")
        return subprocess.run(["bash", str(HOOK)], input=json.dumps(payload), text=True, capture_output=True, env=self.env, cwd=self.temp.name, timeout=5)

    def assert_decision(self, command, blocked, **kwargs):
        result = self.call(command, **kwargs)
        self.assertEqual(result.returncode, 2 if blocked else 0, f"command={command!r}; stderr={result.stderr!r}")
        self.assertEqual(result.stdout, "", "Guard stdout must remain empty")
        if blocked:
            self.assertIn("BLOCKED:", result.stderr)
            self.assertGreater(len(result.stderr), 30, "Blocked command needs a useful reason")
        else:
            self.assertEqual(result.stderr, "")

    def test_fixtures(self):
        fixture = Path(__file__).with_name("danger-guard-fixtures.txt")
        for line in fixture.read_text().splitlines():
            if not line or line.startswith("#"):
                continue
            expected, command = line.split("\t", 1)
            command = command.replace("{REAL_HOME}", pwd.getpwuid(os.getuid()).pw_dir).replace("{TEMP_HOME}", self.temp.name)
            for host, tool in (("claude", "Bash"), ("codex", "Bash"),
                               ("codex", "exec_command"), ("codex", "functions.exec_command"),
                               ("codex", "shell_command"), ("codex", "functions.shell_command")):
                with self.subTest(command=command, host=host, tool=tool):
                    self.assert_decision(command, expected == "block", host=host, tool=tool)

    def test_codex_command_alias(self):
        payload = {"tool_name": "shell_command", "tool_input": {"command": "rm -rf /"}, "turn_id": "fixture-turn"}
        result = subprocess.run(["bash", str(HOOK)], input=json.dumps(payload), text=True, capture_output=True,
                                env=self.env, cwd=self.temp.name, timeout=5)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("BLOCKED:", result.stderr)

    def test_multiline_and_heredoc_data(self):
        self.assert_decision("true\ngit reset --hard", True)
        self.assert_decision("sleep 1 &\n", True)
        self.assert_decision("cat <<'EOF'\nrm -rf /\ngit reset --hard\nwhile true; do sleep 1; done\nEOF\n", False)
        self.assert_decision("cat <<'EOF'\nallowed data\nEOF\nrm -rf /", True)
        self.assert_decision("cat <<ONE <<'TWO'\nrm -rf /\nONE\ngit reset --hard\nTWO\n", False)

    def test_background_payload(self):
        self.assert_decision("sleep 1", True, background=True)
        self.assert_decision("cd ./repo", True, background=True)
        self.assert_decision("timeout 10 sleep 1", False, background=True)
        self.assert_decision("timeout --signal TERM 10 sleep 1", False, background=True)
        self.assert_decision("cd ./repo && timeout 10 sleep 1", False, background=True)
        self.assert_decision("timeout 0 sleep 1", True, background=True)
        self.assert_decision("timeout 10 true; sleep 1", True, background=True)

    def test_background_brace_groups(self):
        for command in ("{ sleep 1; } &", "{ sleep 1; } 2>/dev/null &",
                        "{ sleep 1; } >/dev/null 2>&1 &", "{\nsleep 1\n} &\n",
                        "{ timeout 10 sleep 1; sleep 1; } &"):
            with self.subTest(command=command):
                self.assert_decision(command, True)
        for command in ("{ timeout 10 sleep 1; } &", "{ timeout 10 sleep 1; } 2>/dev/null &",
                        "{ timeout 10 sleep 1; } >/dev/null 2>&1 &", "echo '{ sleep 1; } &'",
                        "timeout 10 echo '{literal}' &", "timeout 10 echo ${HOME} &"):
            with self.subTest(command=command):
                self.assert_decision(command, False)

    def test_unrelated_and_incomplete_payloads(self):
        self.assert_decision("rm -rf /", False, tool="Read")
        for raw in ("", "{bad", "null", "[]", "{}", '{"tool_name":"Bash","tool_input":null}',
                    '{"tool_name":"exec_command","tool_input":{"cmd":[]}}',
                    '{"tool_name":"functions.exec_command","tool_input":{"cmd":null}}'):
            result = subprocess.run(["bash", str(HOOK)], input=raw, text=True, capture_output=True, env=self.env, cwd=self.temp.name, timeout=5)
            self.assertEqual(result.returncode, 0, f"payload={raw!r}; stderr={result.stderr!r}")
            self.assertEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
