#!/usr/bin/env python3
"""Offline checklist entry points and inspection category contracts."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from _platform import BASH, posix, home_env  # noqa: E402

EXPECTED = (
    "Inspection: Inspect the actual changed files or diff and confirm the intended edits landed.\n"
    "Acceptance: Check requirements against BAR evidence and record verifier identity and provenance. "
    "Inspection or a reported PASS alone is not independent acceptance.\n"
    "External readback: Observe the resulting external state without replaying the mutation; "
    "record evidence, not-applicable, or unknown.\n"
)


def tree_state(root):
    result = {}
    for path in root.rglob("*"):
        info = path.lstat()
        value = ("link", os.readlink(path)) if path.is_symlink() else (
            ("file", path.read_bytes()) if path.is_file() else ("dir",))
        result[str(path.relative_to(root))] = (value, info.st_mode, info.st_mtime_ns)
    return result


class ChecklistTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="harness-checklist-")
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.env = {**os.environ, **home_env(self.home),
                    "CLAUDE_HOME": str(self.home / ".claude"),
                    "CODEX_HOME": str(self.home / ".codex"),
                    "XDG_CONFIG_HOME": str(self.home / ".config"),
                    "XDG_STATE_HOME": str(self.home / ".state"),
                    "PYTHONDONTWRITEBYTECODE": "1"}

    def check_command(self, path):
        before = tree_state(self.home)
        result = subprocess.run([sys.executable, "-B", str(path)], stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, env=self.env, cwd=self.home, timeout=10)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, EXPECTED, ""))
        bad = subprocess.run([sys.executable, "-B", str(path), "--unknown"], stdin=subprocess.DEVNULL,
                             capture_output=True, text=True, env=self.env, cwd=self.home, timeout=10)
        self.assertEqual((bad.returncode, bad.stdout), (2, ""))
        self.assertLessEqual(len(bad.stderr.encode("utf-8")), 120)
        self.assertEqual(before, tree_state(self.home))

    def test_fresh_module_without_siblings(self):
        source = ROOT / "hooks/checkpoint/completion.py"
        isolated = self.home / "isolated"
        isolated.mkdir()
        target = isolated / "completion.py"
        shutil.copyfile(source, target)
        self.check_command(target)

    def test_script_install_copies_checkpoint_recursively(self):
        result = subprocess.run([BASH, posix(ROOT / "install.sh"), "--host", "both"],
                                stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                env=self.env, cwd=self.home, timeout=40)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        source = (ROOT / "hooks/checkpoint/completion.py").read_bytes()
        for host in ("claude", "codex"):
            target = self.home / f".{host}/harness/hooks/checkpoint/completion.py"
            self.assertEqual(target.read_bytes(), source)
            self.check_command(target)

    def test_plugin_entry_point_is_standalone(self):
        plugin = ROOT / "plugins/harness/hooks/checkpoint/completion.py"
        self.assertEqual(plugin.read_bytes(), (ROOT / "hooks/checkpoint/completion.py").read_bytes())
        isolated = self.home / "plugin-only"
        isolated.mkdir()
        target = isolated / "completion.py"
        shutil.copyfile(plugin, target)
        self.check_command(target)

    def test_readme_explains_distinct_outcomes_and_explicit_command(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("## Completion checklist", readme)
        self.assertIn("python3 hooks/checkpoint/completion.py", readme)
        self.assertIn("python3 <host-home>/harness/hooks/checkpoint/completion.py", readme)
        self.assertIn("python3 <plugin-root>/hooks/checkpoint/completion.py", readme)
        self.assertIn(EXPECTED, readme)
        self.assertIn("does not establish\nindependent acceptance or external readback", readme)
        self.assertIn("checkpoint restore does not print the full checklist", readme)


if __name__ == "__main__":
    unittest.main(verbosity=2)
