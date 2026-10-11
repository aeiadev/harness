#!/usr/bin/env python3
"""An interpreter cached for the current installer must not satisfy an older release's higher floor."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import test_install_upgrade as upgrade
import test_settings_rule as settings_rule


class ReleasedPython(unittest.TestCase):
    def test_current_floor_cache_cannot_leak_into_legacy_install(self):
        with tempfile.TemporaryDirectory() as directory:
            tree = Path(directory)
            script = tree / "install.sh"
            with patch.object(upgrade, "IS_WINDOWS", True), \
                    patch.object(upgrade.sys, "version_info", (3, 9)), \
                    patch.object(upgrade, "RELEASED_PYTHON", type(upgrade.RELEASED_PYTHON)()), \
                    patch.object(upgrade, "python_shim"), \
                    patch.object(upgrade.tempfile, "mkdtemp", return_value=str(tree / "shim")), \
                    patch.object(upgrade.atexit, "register"), \
                    patch.object(upgrade.shutil, "which", return_value=None):
                script.write_text("if sys.version_info < (3, 9): pass\n")
                upgrade.released_env({"PATH": ""}, tree)
                script.write_text("if sys.version_info < (3, 10): pass\n")
                with self.assertRaises(unittest.SkipTest):
                    upgrade.released_env({"PATH": ""}, tree)

    def test_settings_rule_current_floor_cache_cannot_leak_into_legacy_install(self):
        with tempfile.TemporaryDirectory() as directory:
            tree = Path(directory)
            script = tree / "install.sh"
            with patch.object(settings_rule, "IS_WINDOWS", True), \
                    patch.object(settings_rule.sys, "version_info", (3, 9)), \
                    patch.object(settings_rule, "RELEASED_PYTHON", type(settings_rule.RELEASED_PYTHON)()), \
                    patch.object(settings_rule, "python_shim"), \
                    patch.object(settings_rule.tempfile, "mkdtemp", return_value=str(tree / "shim")), \
                    patch.object(settings_rule.atexit, "register"), \
                    patch.object(settings_rule.shutil, "which", return_value=None):
                script.write_text("if sys.version_info < (3, 9): pass\n")
                settings_rule.released_env({"PATH": ""}, tree)
                script.write_text("if sys.version_info < (3, 10): pass\n")
                with self.assertRaises(unittest.SkipTest):
                    settings_rule.released_env({"PATH": ""}, tree)

    def test_settings_rule_probes_python_exe_before_selecting_legacy_shim(self):
        with tempfile.TemporaryDirectory() as directory:
            tree = Path(directory)
            (tree / "install.sh").write_text("if sys.version_info < (3, 10): pass\n")

            def which(name, path):
                return {"python3.10": "old.exe", "python": "new.exe"}.get(name)

            def probe(args, **kwargs):
                return subprocess.CompletedProcess(args, 1 if args[0] == "old.exe" else 0,
                                                   "" if args[0] == "old.exe" else "new.exe\n", "")

            with patch.object(settings_rule, "IS_WINDOWS", True), \
                    patch.object(settings_rule.sys, "version_info", (3, 9)), \
                    patch.object(settings_rule, "RELEASED_PYTHON", {}), \
                    patch.object(settings_rule, "python_shim") as writer, \
                    patch.object(settings_rule.tempfile, "mkdtemp", return_value=str(tree / "shim")), \
                    patch.object(settings_rule.atexit, "register"), \
                    patch.object(settings_rule.shutil, "which", side_effect=which), \
                    patch.object(settings_rule.subprocess, "run", side_effect=probe):
                env = settings_rule.released_env({"PATH": "original"}, tree)
                self.assertEqual(env["PATH"], str(tree / "shim") + settings_rule.os.pathsep + "original")
                writer.assert_called_once_with(tree / "shim", "new.exe")


if __name__ == "__main__":
    unittest.main()
