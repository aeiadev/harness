"""Enabled guard output and unchanged ordinary hook behavior."""
from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from _platform import BASH, posix
GUARD = ROOT / "hooks/guard/danger_cmd_guard.py"
POLICY = json.loads((ROOT / "tests/fixtures/ssh-operations.json").read_text())["policy"]
REMOTE = "ssh deploy@example.test 'cat /srv/site/VERSION'"


class OutputBudget(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve()
        self.project = self.home / "project"
        (self.project / ".harness").mkdir(parents=True)
        self.policy = self.project / ".harness/operations.json"
        self.policy.write_text(json.dumps(POLICY))
        self.env = dict(os.environ, HOME=str(self.home), XDG_CONFIG_HOME=str(self.home / "config"),
                        PYTHONDONTWRITEBYTECODE="1")

    def bind(self, mode):
        cli = ROOT / "hooks/guard/operations_policy_cli.py"
        result = subprocess.run([sys.executable, "-B", str(cli), "bind", "--project", str(self.project),
                                 "--file", str(self.policy), "--mode", mode], stdin=subprocess.DEVNULL,
                                capture_output=True, env=self.env, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)

    def run_guard(self, command, *, codex=False, workdir=None):
        payload = {"tool_name": "functions.exec_command" if codex else "Bash",
                   "tool_input": {"cmd" if codex else "command": command}}
        if codex:
            payload["hook_event_name"] = "PreToolUse"
            if workdir is not None:
                payload["tool_input"]["workdir"] = str(workdir)
        else:
            payload["cwd"] = str(workdir or self.project)
        return subprocess.run([sys.executable, "-B", str(GUARD)], input=json.dumps(payload).encode(),
                              capture_output=True, env=self.env, cwd=self.home if codex else self.project,
                              timeout=5)

    @unittest.skipIf(os.name == "nt", "policy activation is unsupported on Windows")
    def test_report_enforce_and_local_precedence(self):
        self.bind("report")
        self.assertEqual((self.run_guard(REMOTE).returncode, self.run_guard(REMOTE).stderr), (0, b""))
        denied = self.run_guard("ssh deploy@example.test 'rm /srv/site/current'")
        self.assertEqual(denied.returncode, 0)
        self.assertLessEqual(len(denied.stderr), 512)
        self.assertNotIn(b"/srv/site", denied.stderr)
        self.bind("enforce")
        denied = self.run_guard("ssh deploy@example.test 'rm /srv/site/current'")
        self.assertEqual(denied.returncode, 2)
        local = self.run_guard("git push --mirror origin && " + REMOTE)
        self.assertEqual(local.stderr, b"[danger-cmd-guard] BLOCKED: git push --mirror or --prune can delete remote refs. Preview with --dry-run first.\n")

    @unittest.skipIf(os.name == "nt", "policy activation is unsupported on Windows")
    def test_complete_nonrelevant_parity_and_incomplete_mode(self):
        self.bind("report")
        for command in ("git status --short", "echo ssh"):
            result = self.run_guard(command)
            self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b"", b""))
        oversized = "echo " + "x" * 65540
        beyond = oversized + "; ssh deploy@example.test 'rm /srv/site/current'"
        for command in (oversized, beyond, "sh -c"):
            warning = self.run_guard(command)
            self.assertEqual(warning.returncode, 0)
            self.assertTrue(0 < len(warning.stderr) <= 512)
        self.bind("enforce")
        for command in (oversized, beyond, "sh -c"):
            self.assertEqual(self.run_guard(command).returncode, 2)
        self.assertEqual(self.run_guard(REMOTE, codex=True, workdir=self.project).returncode, 0)

    @unittest.skipIf(os.name == "nt", "policy activation is unsupported on Windows")
    def test_invalid_enabled_policy_refuses(self):
        self.bind("enforce")
        self.policy.write_text("{}")
        result = self.run_guard(REMOTE)
        self.assertEqual(result.returncode, 2)
        self.assertLessEqual(len(result.stderr), 512)

    @unittest.skipIf(os.name == "nt", "policy activation is unsupported on Windows")
    def test_report_policy_drift_warns(self):
        self.bind("report")
        self.policy.write_text("{}")
        result = self.run_guard(REMOTE)
        self.assertEqual(result.returncode, 0)
        self.assertIn(b"policy-digest-drift", result.stderr)
        self.assertLessEqual(len(result.stderr), 512)

    def assert_dependency_failure(self, fault):
        hooks = self.home / "relocated/hooks"
        shutil.copytree(ROOT / "hooks", hooks, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        ambient = self.home / "ambient"
        ambient.mkdir()
        marker = self.home / "ambient-imported"
        env = dict(self.env, PYTHONPATH=str(ambient), HARNESS_PYTHON=sys.executable,
                   XDG_STATE_HOME=str(self.home / "state"))
        for dependency in ("ssh_operations", "operations_policy"):
            (ambient / (dependency + ".py")).write_text(
                "from pathlib import Path\nPath(" + repr(str(marker)) + ").touch()\n")
        entries = ([sys.executable, "-B", str(hooks / "guard/danger_cmd_guard.py")],
                   [BASH, posix(hooks / "guard/danger-cmd-guard.sh")])
        local = "git push --mirror origin && " + REMOTE
        local_output = (b"[danger-cmd-guard] BLOCKED: git push --mirror or --prune can delete "
                        b"remote refs. Preview with --dry-run first." + os.linesep.encode())
        for mode in ((None,) if os.name == "nt" else ("report", "enforce")):
            if mode is not None:
                self.bind(mode)
            for dependency in ("ssh_operations", "operations_policy"):
                path = hooks / "guard" / (dependency + ".py")
                original = path.read_bytes()
                permissions = path.stat().st_mode
                if fault == "missing":
                    path.unlink()
                elif fault == "unreadable":
                    # A directory cannot be loaded as Python source on any platform.
                    path.unlink()
                    path.mkdir()
                else:
                    path.write_bytes(fault)
                try:
                    for entry in entries:
                        for command in (REMOTE, local):
                            with self.subTest(mode=mode, dependency=dependency, entry=entry[0], command=command):
                                payload = {"tool_name": "Bash", "cwd": str(self.project),
                                           "tool_input": {"command": command}}
                                result = subprocess.run(entry, input=json.dumps(payload).encode(),
                                                        capture_output=True, env=env, cwd=self.project,
                                                        timeout=5)
                                self.assertEqual(result.returncode, 2, result.stderr)
                                self.assertEqual(result.stdout, b"")
                                self.assertEqual(result.stderr, local_output if command == local else
                                                 b"[danger-cmd-guard] BLOCKED: remote-operations dependency-unavailable" + os.linesep.encode())
                                self.assertLessEqual(len(result.stderr), 512)
                                self.assertFalse(marker.exists(), "borrowed an ambient dependency")
                finally:
                    if path.is_dir():
                        path.rmdir()
                    path.write_bytes(original)
                    path.chmod(permissions)

    def test_missing_dependency_refuses_without_paths_or_fallback(self):
        self.assert_dependency_failure("missing")

    def test_unreadable_dependency_refuses_without_paths_or_fallback(self):
        self.assert_dependency_failure("unreadable")

    def test_syntax_corrupt_dependency_refuses_without_paths_or_fallback(self):
        self.assert_dependency_failure(b"def broken(:\n")

    def test_encoding_corrupt_dependency_refuses_without_paths_or_fallback(self):
        self.assert_dependency_failure(b"\xff\n")

    def test_initialization_failure_refuses_without_paths_or_fallback(self):
        self.assert_dependency_failure(b"raise RuntimeError('private dependency content')\n")

    def test_truncated_dependency_refuses_without_paths_or_fallback(self):
        self.assert_dependency_failure(b"# Incomplete source: required API is absent.\n")

    def test_permission_denied_dependency_in_full_file_entry(self):
        if os.name != "nt":
            self.bind("enforce")
        with mock.patch.object(sys, "path", [str(GUARD.parent)] + sys.path):
            spec = importlib.util.spec_from_file_location("output_budget_guard", GUARD)
            guard = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(guard)
        loader = guard.importlib.util.spec_from_file_location
        for dependency in ("ssh_operations", "operations_policy"):
            def denied(name, path):
                if Path(path).name == dependency + ".py":
                    raise PermissionError(str(path))
                return loader(name, path)
            payload = {"tool_name": "Bash", "cwd": str(self.project),
                       "tool_input": {"command": REMOTE}}
            with self.subTest(dependency=dependency), mock.patch.dict(os.environ, self.env), \
                    mock.patch.object(guard.importlib.util, "spec_from_file_location", side_effect=denied), \
                    mock.patch.object(sys, "stdin", io.TextIOWrapper(io.BytesIO(json.dumps(payload).encode()))), \
                    redirect_stdout(io.StringIO()) as stdout, redirect_stderr(io.StringIO()) as stderr:
                self.assertEqual(guard.main(), 2)
                self.assertEqual(stdout.getvalue(), "")
                self.assertEqual(stderr.getvalue(),
                                 "[danger-cmd-guard] BLOCKED: remote-operations dependency-unavailable\n")


    @unittest.skipUnless(os.name == "nt", "native Windows off/refusal behavior")
    def test_windows_off_and_present_registry_behavior(self):
        for command in ("git status", REMOTE):
            result = self.run_guard(command)
            self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b"", b""))
        registry = self.home / "config/harness/operations-bindings.json"
        registry.parent.mkdir(parents=True)
        registry.write_text('{"schema_version":1,"bindings":[]}')
        result = self.run_guard(REMOTE)
        self.assertEqual(result.returncode, 2)
        self.assertLessEqual(len(result.stderr), 512)
        self.assertNotIn(str(self.home).encode(), result.stderr)
        self.assertEqual(self.run_guard("git status").returncode, 0)


if __name__ == "__main__":
    unittest.main()
