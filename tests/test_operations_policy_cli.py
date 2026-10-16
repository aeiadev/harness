import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

CLI = Path(__file__).resolve().parents[1] / "hooks" / "guard" / "operations_policy_cli.py"
SAMPLE = json.loads((Path(__file__).with_name("fixtures") / "ssh-operations.json").read_text())["policy"]


class CliFixture:
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.root = self.base / "project"
        (self.root / ".harness").mkdir(parents=True)
        self.policy = self.root / ".harness" / "operations.json"
        self.policy.write_text(json.dumps(SAMPLE))
        self.env = dict(os.environ, HOME=str(self.base), XDG_CONFIG_HOME=str(self.base / "config"), PYTHONDONTWRITEBYTECODE="1")

    def cli(self, *args):
        return subprocess.run([sys.executable, "-B", str(CLI), *args], input=b"", capture_output=True, env=self.env, timeout=5)


@unittest.skipIf(os.name == "nt", "policy activation requires POSIX filesystem capabilities")
class CliTests(CliFixture, unittest.TestCase):
    def test_restrictive_umask_refuses_before_registry_mutation(self):
        def inventory():
            config = self.base / "config"
            return {str(p.relative_to(self.base)): (p.stat().st_mode, p.stat().st_ino,
                    p.read_bytes() if p.is_file() else None)
                    for p in ([config] + sorted(config.rglob("*"))) if p.exists()}
        for existing in (False, True):
            if existing:
                (self.base / "config/harness").mkdir(parents=True, mode=0o700)
            for mask in (0o100, 0o200, 0o400, 0o600, 0o777):
                before = inventory()
                args = [sys.executable, "-B", str(CLI), "bind", "--project", str(self.root),
                        "--file", str(self.policy), "--mode", "report"]
                with self.subTest(existing=existing, mask=oct(mask)):
                    result = subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True,
                                            env=self.env, timeout=5, umask=mask)
                    self.assertEqual(result.returncode, 2)
                    self.assertEqual(result.stdout, b"")
                    self.assertNotIn(str(self.base).encode(), result.stderr)
                    self.assertEqual(inventory(), before)
        for mask in (0o000, 0o022, 0o077):
            args = [sys.executable, "-B", str(CLI), "bind", "--project", str(self.root),
                    "--file", str(self.policy), "--mode", "enforce"]
            result = subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True,
                                    env=self.env, timeout=5, umask=mask)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.cli("unbind", "--project", str(self.root)).returncode, 0)

    def test_empty_project_and_terminal_file_paths_refuse_without_writes(self):
        for project, file in (("", str(self.policy)), (str(self.root), str(self.policy) + "/"),
                              (str(self.root), str(self.policy) + "/.")):
            for mode in ("report", "enforce"):
                args = [sys.executable, "-B", str(CLI), "bind", "--project", project,
                        "--file", file, "--mode", mode]
                with self.subTest(project=project, file=file, mode=mode):
                    result = subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True,
                                            env=self.env, cwd=self.root, timeout=5)
                    self.assertEqual(result.returncode, 2)
                    self.assertEqual(result.stdout, b"")
                    self.assertFalse((self.base / "config").exists())

    def test_bind_show_unbind(self):
        args = ("bind", "--project", str(self.root), "--file", str(self.policy), "--mode", "report")
        self.assertEqual(self.cli(*args).returncode, 0)
        shown = self.cli("show")
        self.assertEqual(shown.returncode, 0)
        self.assertIn(b'"mode": "report"', shown.stdout)
        self.assertEqual(self.cli("unbind", "--project", str(self.root)).returncode, 0)
        self.assertEqual(json.loads(self.cli("show").stdout)["bindings"], [])

    def test_invalid_mutation_preserves_registry(self):
        args = ("bind", "--project", str(self.root), "--file", str(self.policy), "--mode", "enforce")
        self.assertEqual(self.cli(*args).returncode, 0)
        registry = self.base / "config" / "harness" / "operations-bindings.json"
        before = registry.read_bytes()
        self.policy.write_bytes(b'{"schema_version":1,}')
        self.assertNotEqual(self.cli(*args).returncode, 0)
        self.assertEqual(registry.read_bytes(), before)

    def test_unbind_preserves_other_entries_and_data(self):
        other = self.base / "other"
        (other / ".harness").mkdir(parents=True)
        other_file = other / ".harness" / "operations.json"
        other_file.write_text(json.dumps(SAMPLE))
        for root, file in ((self.root, self.policy), (other, other_file)):
            self.assertEqual(self.cli("bind", "--project", str(root), "--file", str(file), "--mode", "report").returncode, 0)
        registry = self.base / "config" / "harness" / "operations-bindings.json"
        stored = json.loads(registry.read_text())
        stored["user_note"] = {"kept": True}
        registry.write_text(json.dumps(stored))
        self.assertEqual(self.cli("unbind", "--project", str(self.root)).returncode, 0)
        remaining = json.loads(registry.read_text())
        self.assertEqual(remaining["user_note"], {"kept": True})
        self.assertEqual([e["project_root"] for e in remaining["bindings"]], [str(other)])

    def test_invalid_rule_type_has_no_traceback_or_path(self):
        invalid = json.loads(json.dumps(SAMPLE))
        invalid["rules"][0]["transport"] = []
        self.policy.write_text(json.dumps(invalid))
        result = self.cli("bind", "--project", str(self.root), "--file", str(self.policy), "--mode", "report")
        self.assertEqual((result.returncode, result.stderr), (2, b"policy-invalid\n"))
        self.assertNotIn(str(self.root).encode(), result.stdout + result.stderr)

    def test_show_refuses_inaccessible_registry_namespace(self):
        registry = self.base / "config" / "harness" / "operations-bindings.json"
        for mode in ("report", "enforce"):
            self.assertEqual(self.cli("bind", "--project", str(self.root), "--file", str(self.policy), "--mode", mode).returncode, 0)
            raw = registry.read_bytes()
            for present in (True, False):
                if not present:
                    registry.unlink()
                registry.parent.chmod(0)
                try:
                    with self.subTest(mode=mode, present=present):
                        result = self.cli("show")
                        self.assertEqual(result.returncode, 2)
                        self.assertEqual(result.stdout, b"")
                        self.assertNotIn(str(self.base).encode(), result.stderr)
                        self.assertNotIn(b"Traceback", result.stderr)
                        self.assertLess(len(result.stderr), 80)
                finally:
                    registry.parent.chmod(0o700)
                if not present:
                    registry.write_bytes(raw)
                    registry.chmod(0o600)

    def test_overflow_metadata_is_rejected_before_success_or_writes(self):
        args = ("bind", "--project", str(self.root), "--file", str(self.policy), "--mode", "report")
        self.assertEqual(self.cli(*args).returncode, 0)
        registry = self.base / "config" / "harness" / "operations-bindings.json"
        raw = registry.read_bytes().rstrip()[:-1] + b',"user_note":1e999}'
        registry.write_bytes(raw)
        (registry.parent / ".operations-bindings.lock").unlink()
        for command in (args, ("show",), ("unbind", "--project", str(self.root))):
            with self.subTest(command=command[0]):
                result = self.cli(*command)
                self.assertEqual((result.returncode, result.stdout, result.stderr),
                                 (2, b"", b"registry-invalid\n"))
                self.assertEqual(registry.read_bytes(), raw)
                self.assertEqual([p.name for p in registry.parent.iterdir()], [registry.name])

    def test_bind_checks_supplied_file_before_parent_component(self):
        damaged = self.root / "ordinary-file"
        damaged.write_text("unrelated")
        raw = str(damaged) + "/../.harness/operations.json"
        result = self.cli("bind", "--project", str(self.root), "--file", raw, "--mode", "enforce")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, b"")
        self.assertNotIn(b"Traceback", result.stderr)
        self.assertNotIn(str(self.base).encode(), result.stderr)
        self.assertFalse((self.base / "config").exists())

    def test_show_checks_ancestor_before_parent_component(self):
        damaged = self.base / "ordinary-file"
        damaged.write_text("unrelated")
        self.env["XDG_CONFIG_HOME"] = str(damaged) + "/../config"
        result = self.cli("show")
        self.assertEqual((result.returncode, result.stdout, result.stderr),
                         (2, b"", b"registry-ancestor-escape\n"))
        self.assertFalse((self.base / "config").exists())

    def test_unbind_unenrolled_project_leaves_configuration_absent(self):
        result = self.cli("unbind", "--project", str(self.root))
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b"unbound\n", b""))
        self.assertFalse((self.base / "config").exists())

    def test_oversized_output_refuses_without_creating_lock(self):
        args = ("bind", "--project", str(self.root), "--file", str(self.policy), "--mode", "report")
        self.assertEqual(self.cli(*args).returncode, 0)
        registry = self.base / "config" / "harness" / "operations-bindings.json"
        stored = json.loads(registry.read_bytes())
        stored["user_note"] = ""
        compact = json.dumps(stored, separators=(",", ":")).encode()
        stored["user_note"] = "x" * (65536 - len(compact))
        raw = json.dumps(stored, separators=(",", ":")).encode()
        self.assertEqual(len(raw), 65536)
        registry.write_bytes(raw)
        (registry.parent / ".operations-bindings.lock").unlink()
        before = sorted(self.base.rglob("*"))
        result = self.cli(*args)
        self.assertEqual((result.returncode, result.stdout, result.stderr),
                         (2, b"", b"registry-size-limit\n"))
        self.assertEqual(registry.read_bytes(), raw)
        self.assertEqual(sorted(self.base.rglob("*")), before)



class PortableCliTests(CliFixture, unittest.TestCase):
    def test_absent_registry_show_is_write_free(self):
        before = sorted(self.base.rglob("*"))
        result = self.cli("show")
        self.assertEqual((result.returncode, result.stderr), (0, b""))
        self.assertEqual(json.loads(result.stdout), {"schema_version": 1, "bindings": []})
        self.assertEqual(sorted(self.base.rglob("*")), before)


@unittest.skipUnless(os.name == "nt", "native Windows refusal contract")
class WindowsCliTests(CliFixture, unittest.TestCase):
    def test_activation_refuses_both_modes_without_writes(self):
        before = sorted(self.base.rglob("*"))
        for mode in ("report", "enforce"):
            with self.subTest(mode=mode):
                result = self.cli("bind", "--project", str(self.root), "--file", str(self.policy), "--mode", mode)
                self.assertEqual((result.returncode, result.stdout, result.stderr.replace(b"\r\n", b"\n")),
                                 (2, b"", b"unsupported-platform\n"))
                self.assertEqual(sorted(self.base.rglob("*")), before)

    def test_existing_registry_show_and_unbind_refuse_without_writes(self):
        registry = self.base / "config/harness/operations-bindings.json"
        registry.parent.mkdir(parents=True)
        raw = b'{"schema_version":1,"bindings":[]}'
        registry.write_bytes(raw)
        before = sorted(self.base.rglob("*"))
        for args in (("show",), ("unbind", "--project", str(self.root))):
            result = self.cli(*args)
            self.assertEqual((result.returncode, result.stdout), (2, b""))
            self.assertIn(result.stderr.replace(b"\r\n", b"\n"),
                          (b"unsupported-platform\n", b"invalid-project\n"))
            self.assertEqual(registry.read_bytes(), raw)
            self.assertEqual(sorted(self.base.rglob("*")), before)


if __name__ == "__main__":
    unittest.main()
