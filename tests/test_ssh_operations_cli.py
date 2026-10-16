"""Offline SSH check contract."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from _platform import can_symlink
CLI = ROOT / "hooks/guard/ssh_operations_cli.py"
POLICY = json.loads((ROOT / "tests/fixtures/ssh-operations.json").read_text())["policy"]


class CheckCli(unittest.TestCase):
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
        bind = ROOT / "hooks/guard/operations_policy_cli.py"
        result = subprocess.run([sys.executable, "-B", str(bind), "bind", "--project", str(self.project),
                                 "--file", str(self.policy), "--mode", "enforce"], stdin=subprocess.DEVNULL,
                                capture_output=True, env=self.env, timeout=5)
        if os.name == "nt":
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertEqual(result.stderr.strip(), b"unsupported-platform")
        else:
            self.assertEqual(result.returncode, 0, result.stderr)

    def call(self, command, *args, input_data=None):
        data = input_data if input_data is not None else json.dumps({"schema_version": 1,
                                      "command": command, "project_root": str(self.project)}).encode()
        return subprocess.run([sys.executable, "-B", str(CLI), *args], input=data,
                              capture_output=True, env=self.env, timeout=5)

    @unittest.skipIf(os.name == "nt", "policy activation is unsupported on Windows")
    def test_bound_decisions_and_stdin(self):
        for command, code, verdict in (("git status", 0, "ALLOW"),
                ("ssh deploy@example.test 'cat /srv/site/VERSION'", 0, "ALLOW"),
                ("ssh deploy@example.test 'rm /srv/site/current'", 3, "BLOCK"),
                ("ssh deploy@example.test 'cat $FILE'", 4, "UNKNOWN")):
            with self.subTest(command=command):
                result = self.call(command, "check", "--file", "-")
                self.assertEqual(result.returncode, code, result.stderr)
                self.assertEqual(json.loads(result.stdout)["verdict"], verdict)

    def test_preview_verdicts_are_offline_and_do_not_activate(self):
        registry = self.home / "config/harness/operations-bindings.json"
        before = registry.read_bytes() if registry.exists() else None
        request = self.home / "preview.json"
        for command, code in (("git status", 0),
                              ("ssh deploy@example.test 'cat /srv/site/VERSION'", 0),
                              ("ssh deploy@example.test 'rm /srv/site/current'", 3),
                              ("ssh deploy@example.test 'cat $FILE'", 4)):
            request.write_text(json.dumps({"schema_version": 1, "command": command,
                                          "project_root": str(self.project)}))
            for path in ("-", str(request)):
                with self.subTest(command=command, source=path):
                    result = self.call(command, "check", "--file", path, "--policy", str(self.policy))
                    self.assertEqual(result.returncode, code, result.stderr)
                    self.assertEqual(json.loads(result.stdout)["binding"], "unbound-preview")
        self.assertEqual(registry.read_bytes() if registry.exists() else None, before)

    def test_preview_and_bad_input(self):
        result = self.call("git status", "check", "--file", "-", "--policy", str(self.policy))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("unbound", result.stdout.decode())
        self.assertEqual(self.call("x", "check", "--file", "-", input_data=b"{}").returncode, 1)
        self.assertEqual(self.call("x", "check").returncode, 2)
        request = self.home / "request.json"
        request.write_text(json.dumps({"schema_version": 1, "command": "git status",
                                       "project_root": str(self.project)}))
        preview = ("--policy", str(self.policy))
        self.assertEqual(self.call("x", "check", "--file", str(request), *preview, input_data=b"").returncode, 0)
        if can_symlink():
            link = self.home / "link.json"
            link.symlink_to(request)
            self.assertEqual(self.call("x", "check", "--file", str(link), *preview, input_data=b"").returncode, 1)
        else:
            print("SKIP symlink assertion: this user cannot create symlinks", file=sys.stderr)
        self.assertEqual(self.call("x", "check", "--file", "-",
                                   input_data=b" " * (65536 + 8193)).returncode, 1)
        fifo = self.home / "fifo"
        if hasattr(os, "mkfifo"):
            os.mkfifo(fifo)
            self.assertEqual(self.call("x", "check", "--file", str(fifo)).returncode, 1)

    @unittest.skipUnless(os.name == "nt", "native Windows file namespace")
    def test_windows_nonfile_names_refuse_before_filesystem_access(self):
        import importlib.util
        from unittest.mock import patch
        spec = importlib.util.spec_from_file_location("offline_preview", CLI)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for name in ("NUL", "CON.txt", "COM1", "LPT¹", "C:relative.json",
                     "C:/file:stream", "//server/share/file", "//./pipe/unused",
                     "//?/C:/file", "C:/file.", "C:/file/"):
            with self.subTest(name=name), patch.object(os, "lstat", side_effect=AssertionError("unexpected filesystem access")), \
                    patch.object(os, "open", side_effect=AssertionError("unexpected file open")):
                with self.assertRaises(ValueError):
                    module.file_bytes(name, 16384, "input", None)


if __name__ == "__main__":
    unittest.main()
