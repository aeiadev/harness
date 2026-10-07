#!/usr/bin/env python3
"""Standalone helper checks with fake formatters and a temporary home."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hooks/checkpoint"))
import storage
import journal


class HelperTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.bin = self.home / "bin"
        self.bin.mkdir()
        self.bin.joinpath("python3").symlink_to(shutil.which("python3"))
        self.env = {**os.environ, "HOME": str(self.home), "PATH": str(self.bin),
                    "XDG_STATE_HOME": str(self.home / "state")}
        self.bash = shutil.which("bash")
        self.log = self.home / "formatter.json"
        self.env["FORMATTER_LOG"] = str(self.log)
        self.env["HARNESS_AUTO_FORMAT"] = "1"

    def run_hook(self, hook, event):
        result = subprocess.run([self.bash, str(ROOT / "hooks/helpers" / hook)],
                                input=json.dumps(event), text=True, capture_output=True,
                                env=self.env, cwd=self.home, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "", result.stderr)
        return result.stdout

    def formatter(self, name, code=0):
        path = self.bin / name
        path.write_text("#!" + shutil.which("python3") + "\n"
                        "import json, os, sys\n"
                        "with open(os.environ['FORMATTER_LOG'], 'w') as stream:\n"
                        "    json.dump(sys.argv, stream)\n"
                        f"sys.exit({code})\n")
        path.chmod(0o755)

    def test_formatter_missing_is_silent(self):
        file = self.home / "sample.py"
        file.write_text("x=1\n")
        self.assertEqual(self.run_hook("auto-format.sh", {
            "tool_name": "Write", "tool_input": {"file_path": str(file)}}), "")
        self.assertFalse(self.log.exists())

    def test_formatting_requires_exact_environment_opt_in(self):
        self.formatter("prettier")
        target = self.home / "page.js"
        target.write_text("const value=1")
        event = {"tool_name": "Write", "tool_input": {"file_path": str(target)}}
        for setting in (None, "", "0", "true", "yes"):
            self.env.pop("HARNESS_AUTO_FORMAT", None)
            if setting is not None:
                self.env["HARNESS_AUTO_FORMAT"] = setting
            self.assertEqual(self.run_hook("auto-format.sh", event), "")
            self.assertFalse(self.log.exists(), "Disabled formatting executed repository tooling")
        self.env["HARNESS_AUTO_FORMAT"] = "1"
        self.run_hook("auto-format.sh", event)
        self.assertTrue(self.log.is_file(), "Explicit enablement did not run the formatter")

    def test_formatter_path_arguments_and_precedence(self):
        self.formatter("ruff")
        self.formatter("black")
        file = self.home / "a space.py"
        file.write_text("x=1\n")
        output = self.run_hook("auto-format.sh", {"tool_name": "Edit", "cwd": str(self.home),
                               "tool_input": {"file_path": file.name}})
        self.assertEqual(output, "")
        args = json.loads(self.log.read_text())
        self.assertEqual(Path(args[0]).name, "ruff")
        self.assertEqual(args[1:], ["format", "--", str(file)])

    def test_formatter_failure_is_silent(self):
        self.formatter("black", 1)
        file = self.home / "sample.py"
        file.touch()
        self.assertEqual(self.run_hook("auto-format.sh", {
            "tool_name": "MultiEdit", "tool_input": {"file_path": str(file)}}), "")
        self.assertTrue(self.log.exists())

    def test_formatter_unsupported_tool_and_file(self):
        self.formatter("ruff")
        file = self.home / "sample.py"
        file.touch()
        self.run_hook("auto-format.sh", {"tool_name": "Read", "tool_input": {"file_path": str(file)}})
        self.assertFalse(self.log.exists())
        file = self.home / "sample.unknown"
        file.touch()
        self.run_hook("auto-format.sh", {"tool_name": "Write", "tool_input": {"file_path": str(file)}})
        self.assertFalse(self.log.exists())

    def test_immediate_reminder(self):
        data = self.run_hook("verify-patch-landed-auto.sh", {
            "tool_name": "Bash", "tool_input": {"command": "sed -i s/old/new/ sample.txt"}})
        context = json.loads(data)["hookSpecificOutput"]
        self.assertEqual(context["hookEventName"], "PostToolUse")
        self.assertIn("Re-read", context["additionalContext"])
        self.assertEqual(self.run_hook("verify-patch-landed-auto.sh", {
            "tool_name": "Bash", "tool_input": {"command": "git diff"}}), "")

    def test_codex_immediate_reminder(self):
        for name in ("Bash", "exec_command", "functions.shell_command"):
            key = "cmd" if name == "exec_command" else "command"
            output = self.run_hook("verify-patch-landed-auto.sh", {
                "turn_id": "turn", "tool_name": name,
                "tool_input": {key: "sed -i s/old/new/ sample.txt"}})
            self.assertIn("Re-read", json.loads(output)["hookSpecificOutput"]["additionalContext"])

    def test_codex_patch_formatter(self):
        self.formatter("ruff")
        file = self.home / "a space.py"
        file.write_text("x=1\n")
        patch = "*** Begin Patch\n*** Update File: old.py\n*** Move to: a space.py\n@@\n-x=0\n+x=1\n*** End Patch"
        for data in (patch, {"patch": patch}, {"input": patch}):
            self.run_hook("auto-format.sh", {"tool_name": "apply_patch", "cwd": str(self.home),
                                            "tool_input": data, "turn_id": "turn"})
            self.assertEqual(json.loads(self.log.read_text())[1:], ["format", "--", str(file)])
            self.log.unlink()

    def test_codex_stop_requires_successful_inspection(self):
        path = self.home / "rollout.jsonl"
        entries = [{"type": "event_msg", "payload": {"type": "task_started"}}]

        def action(identifier, command, code):
            entries.extend([
                {"type": "response_item", "payload": {"type": "function_call", "call_id": identifier,
                    "name": "exec_command", "arguments": json.dumps({"cmd": command})}},
                {"type": "response_item", "payload": {"type": "function_call_output", "call_id": identifier,
                    "output": json.dumps({"exit_code": code})}},
            ])

        def stop():
            path.write_text("\n".join(json.dumps(entry) for entry in entries))
            return subprocess.run([self.bash, str(ROOT / "hooks/helpers/stop-check-verify-patch.sh")],
                input=json.dumps({"transcript_path": str(path), "turn_id": "turn"}),
                text=True, capture_output=True, env=self.env, timeout=5)

        action("edit", "sed -i s/old/new/ sample.txt", 0)
        result = stop()
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("no later successful inspection", result.stderr)
        action("failed-check", "git diff", 1)
        self.assertEqual(stop().returncode, 2)
        entries[-1]["payload"]["output"] = (
            "Process exited with code 1\nFinal output:\nProcess exited with code 0\n")
        self.assertEqual(stop().returncode, 2, "Command stdout spoofed the tool exit status")
        action("check", "git diff", 0)
        self.assertEqual(stop().returncode, 0)
        entries[-1]["payload"]["output"] = "Process exited with code 0\nFinal output:\nverified\n"
        self.assertEqual(stop().returncode, 0)

    def test_multiline_rewrite_reminder(self):
        output = self.run_hook("verify-patch-landed-auto.sh", {
            "tool_name": "Bash", "tool_input": {
                "command": "python3 - <<'PY'\nimport ast\nast.parse(source)\nPY"}})
        self.assertIn("Python rewrite", json.loads(output)["hookSpecificOutput"]["additionalContext"])

    def test_helpers_without_python_are_silent(self):
        self.bin.joinpath("python3").unlink()
        for hook in ("auto-format.sh", "stop-check-verify-patch.sh", "verify-patch-landed-auto.sh"):
            with self.subTest(hook=hook):
                self.assertEqual(self.run_hook(hook, {}), "")

    def test_echoed_edits_do_not_trigger(self):
        for command in ("echo sed -i pattern sample.txt", "echo 'sed -i s/old/new/ sample.txt'",
                        "printf '%s' 'git diff; sed -i s/old/new/ sample.txt'",
                        "cat <<'EOF'\nsed -i s/old/new/ sample.txt\nEOF"):
            with self.subTest(command=command):
                self.assertEqual(self.run_hook("verify-patch-landed-auto.sh", {
                    "tool_name": "Bash", "tool_input": {"command": command}}), "")

    def transcript(self, verify=False, error=False, check_command=None):
        entries = [{"type": "user", "message": {"role": "user", "content": "Change the file"}}]
        actions = [("edit", "Bash", {"command": "sed -i s/old/new/ sample.txt"})]
        if verify:
            actions.append(("check", "Bash", {"command": check_command}) if check_command is not None
                           else ("check", "Read", {"file_path": "sample.txt"}))
        for identifier, name, data in actions:
            entries.append({"type": "assistant", "message": {"content": [{
                "type": "tool_use", "id": identifier, "name": name, "input": data}]}})
            entries.append({"type": "user", "message": {"content": [{
                "type": "tool_result", "tool_use_id": identifier,
                "is_error": error and identifier == "check", "content": "result"}]}})
        path = self.home / "transcript.jsonl"
        path.write_text("\n".join(json.dumps(item) for item in entries))
        return path

    def test_stop_requests_unverified_patch(self):
        data = self.run_hook("stop-check-verify-patch.sh", {"transcript_path": str(self.transcript())})
        self.assertEqual(json.loads(data)["decision"], "block")

    def test_state_and_stop_agree_on_payload_host(self):
        (self.home / ".git").mkdir()
        for host in ("claude", "codex"):
            directory = self.home / ("." + host)
            directory.mkdir()
            (directory / "checkpoint.json").write_text(json.dumps({"checkpoint_path": host + ".md"}))
        cases = (
            ({}, False),
            ({"model": "claude-sonnet-4-6"}, False),
            ({"model": None}, False),
            ({"model": {"name": "gpt-example"}}, False),
            ({"turn_id": "fixture-turn"}, True),
            ({"thread_id": "fixture-thread"}, True),
            ({"model": "gpt-6-sol"}, True),
            ({"type": "turn.completed"}, True),
        )
        transcript = self.transcript()
        for fields, codex in cases:
            with self.subTest(fields=fields):
                payload = {"cwd": str(self.home), "hook_event_name": "Stop",
                           "transcript_path": str(transcript), "stop_hook_active": False, **fields}
                self.assertEqual(storage.uses_codex(payload), codex)
                host = "codex" if codex else "claude"
                self.assertEqual(journal.preferences(payload)["checkpoint_path"], host + ".md")
                result = subprocess.run(
                    [self.bash, str(ROOT / "hooks/helpers/stop-check-verify-patch.sh")],
                    input=json.dumps(payload), text=True, capture_output=True,
                    env=self.env, cwd=self.home, timeout=5,
                )
                self.assertEqual(result.returncode, 2 if codex else 0, result.stderr)
                if codex:
                    self.assertEqual(result.stdout, "")
                    self.assertIn("no later successful inspection", result.stderr)
                else:
                    self.assertEqual(result.stderr, "")
                    self.assertEqual(json.loads(result.stdout)["decision"], "block")

    def test_stop_accepts_successful_read_only(self):
        self.assertEqual(self.run_hook("stop-check-verify-patch.sh", {
            "transcript_path": str(self.transcript(verify=True))}), "")
        self.assertEqual(json.loads(self.run_hook("stop-check-verify-patch.sh", {
            "transcript_path": str(self.transcript(verify=True, error=True))}))["decision"], "block")

    def test_echoed_inspections_do_not_verify(self):
        for command in ("echo git diff", "echo 'git diff'", "printf '%s' 'cat sample.txt'",
                        "printf '%s' <<'EOF'\ngit diff\nEOF"):
            with self.subTest(command=command):
                output = self.run_hook("stop-check-verify-patch.sh", {
                    "transcript_path": str(self.transcript(verify=True, check_command=command))})
                self.assertEqual(json.loads(output)["decision"], "block")
        self.assertEqual(self.run_hook("stop-check-verify-patch.sh", {
            "transcript_path": str(self.transcript(verify=True, check_command="git diff"))}), "")

    def test_stop_recursion_and_missing_transcript(self):
        self.assertEqual(self.run_hook("stop-check-verify-patch.sh", {
            "transcript_path": str(self.transcript()), "stop_hook_active": True}), "")
        self.assertEqual(self.run_hook("stop-check-verify-patch.sh", {
            "transcript_path": str(self.home / "absent")}), "")

    def test_stop_ignores_previous_turn(self):
        path = self.transcript()
        with path.open("a") as stream:
            stream.write("\n" + json.dumps({"type": "user", "message": {
                "role": "user", "content": "Start another task"}}))
        self.assertEqual(self.run_hook("stop-check-verify-patch.sh", {"transcript_path": str(path)}), "")

    def test_malformed_payloads(self):
        for hook in ("auto-format.sh", "stop-check-verify-patch.sh", "verify-patch-landed-auto.sh"):
            for payload in ({}, [], {"tool_name": "Bash", "tool_input": None}):
                with self.subTest(hook=hook, payload=payload):
                    self.assertEqual(self.run_hook(hook, payload), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
