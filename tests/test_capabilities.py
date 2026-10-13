"""Offline Harness capability diagnostic integration."""
import os
from pathlib import Path
import subprocess
import shutil
import sys
import unittest

from test_install_upgrade import Base, ROOT
from _platform import BASH, posix, home_env


IDS = (
    "role_discovery", "model_pins_inheritance", "hook_payload_fields",
    "trusted_hook_lifecycle", "tool_rewrites", "completion_events",
    "tool_visibility", "read_only_enforcement", "usage_formats",
)
LOCAL_CORE = ROOT / "hooks/checkpoint/capabilities.py"
sys.path.insert(0, str(LOCAL_CORE.parent))
from capabilities import describe, render_rows


class CapabilityStatusTests(Base):
    def invoke(self, *args, source=ROOT):
        marker = self.root / "host-invoked"
        bin_dir = self.root / "bin"
        bin_dir.mkdir(exist_ok=True)
        for name in ("claude", "codex"):
            path = bin_dir / name
            path.write_text("#!/bin/sh\nprintf invoked > \"$HOST_MARKER\"\n")
            path.chmod(0o755)
        env = {**os.environ, **home_env(self.home),
               "CLAUDE_HOME": str(self.host_home("claude")),
               "CODEX_HOME": str(self.host_home("codex")),
               "XDG_STATE_HOME": str(self.home / "state"),
               "XDG_CONFIG_HOME": str(self.home / "config"),
               "PYTHONDONTWRITEBYTECODE": "1", "HOST_MARKER": str(marker),
               "PATH": str(bin_dir) + os.pathsep + os.environ.get("PATH", "")}
        env.pop("PYTHONPATH", None)
        result = subprocess.run([BASH, posix(source / "install.sh"), *args],
                                env=env, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=20)
        self.assertFalse(marker.exists(), result.stdout + result.stderr)
        return result

    def test_version_syntax_rejected_before_installation(self):
        before = self.tree_state()
        for args in (("--host-version", "2.1.296", "--host", "claude"),
                     ("--status", "--host-version", "2.1.296"),
                     ("--status", "--host", "both", "--host-version", "2.1.296")):
            with self.subTest(args=args):
                result = self.invoke(*args)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertIn("--host-version requires --status and one explicit --host", result.stderr)
        self.assertEqual(self.tree_state(), before)

    def test_status_is_read_only_with_local_catalog(self):
        before = self.tree_state()
        result = self.invoke("--status", "--host", "claude")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertTrue(result.stdout.startswith("Harness status (claude): "))
        self.assertTrue(LOCAL_CORE.is_file(), "shipped local catalog required")
        self.assertEqual(sum(row.startswith("capability ") for row in result.stdout.splitlines()), 10)
        self.assertEqual(self.tree_state(), before)

    def test_default_hosts_keep_versions_separate_and_unknown(self):
        result = self.invoke("--status")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count("Harness status ("), 2)
        version_rows = [row for row in result.stdout.splitlines()
                        if row.startswith("capability version: ")]
        self.assertEqual(len(version_rows), 2)
        self.assertTrue(all("unknown (unavailable)" in row for row in version_rows))

    def test_catalog_rows_with_local_core(self):
        self.assertTrue(LOCAL_CORE.is_file(), "shipped local catalog required")
        for host, version in (("claude", None), ("claude", "2.1.296"),
                              ("claude", "2.1.297"), ("claude", "bad-version"),
                              ("codex", None), ("codex", "0.156.1"),
                              ("codex", "0.156.2"), ("codex", "9" * 33)):
            with self.subTest(host=host, version=version):
                argv = ["--status", "--host", host]
                if version is not None:
                    argv += ["--host-version", version]
                result = self.invoke(*argv)
                self.assertEqual(result.returncode, 0, result.stderr)
                rows = [row for row in result.stdout.splitlines() if row.startswith("capability ")]
                self.assertEqual([row.split(":", 1)[0] for row in rows],
                                 ["capability version", *("capability " + key for key in IDS)])
                self.assertTrue(all("unverified" in row for row in rows), rows)
                self.assertNotIn("bad-version", result.stdout)

    def test_newline_version_is_sanitized_without_echo(self):
        report = describe("claude", "bad\nversion", package="harness")
        self.assertIsNone(report["host_version"])
        self.assertEqual(report["version_source"], "invalid")
        self.assertFalse(report["host_verified"])
        rows = render_rows(report)
        self.assertEqual(len(rows), 10)
        self.assertTrue(all("unverified" in value for _, value in rows))
        self.assertTrue(all("\n" not in value for _, value in rows))
        self.assertNotIn("bad\nversion", "\n".join(value for _, value in rows))

    def test_copied_standalone_tree_installs_its_catalog(self):
        source = self.root / "standalone"
        shutil.copytree(ROOT, source, ignore=shutil.ignore_patterns(".git", "__pycache__"))
        self.assertFalse((source.parent / "phase-b3-router-builder").exists())
        for host in ("claude", "codex"):
            self.run_tree(source, host)
            installed = self.host_home(host) / "harness/hooks/checkpoint/capabilities.py"
            self.assertEqual(installed.read_bytes(), LOCAL_CORE.read_bytes())
            before = self.tree_state()
            result = self.invoke("--status", "--host", host, source=source)
            self.assertEqual(result.returncode, 0, result.stderr)
            rows = [row for row in result.stdout.splitlines() if row.startswith("capability ")]
            self.assertEqual(len(rows), 10)
            self.assertTrue(all("unverified" in row for row in rows))
            self.assertEqual(self.tree_state(), before)

    def test_unavailable_local_catalog_keeps_status_read_only(self):
        source = self.root / "standalone"
        shutil.copytree(ROOT, source, ignore=shutil.ignore_patterns(".git", "__pycache__"))
        catalog = source / "hooks/checkpoint/capabilities.py"
        baseline = self.invoke("--status", "--host", "claude", source=source)
        self.assertEqual(baseline.returncode, 0, baseline.stderr)
        health_rows = [row for row in baseline.stdout.splitlines()
                       if not row.startswith("capability ")]
        before = self.tree_state()
        for contents in (None, "def broken(:\n", "value = 1\n",
                         "def describe(*args, **kwargs):\n    return {}\n"
                         "def render_rows(report):\n    raise KeyError('broken')\n"):
            with self.subTest(contents=contents):
                if contents is None:
                    catalog.unlink()
                else:
                    catalog.write_text(contents)
                result = self.invoke("--status", "--host", "claude", source=source)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stderr, "")
                self.assertEqual([row for row in result.stdout.splitlines()
                                  if not row.startswith("capability ")], health_rows)
                self.assertEqual([row for row in result.stdout.splitlines()
                                  if row.startswith("capability ")],
                                 ["capability version: unavailable (local catalog missing or unreadable)"])
                self.assertEqual(self.tree_state(), before)


if __name__ == "__main__":
    unittest.main()
