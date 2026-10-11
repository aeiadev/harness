#!/usr/bin/env python3
"""Upgrades replace unchanged owned pre-launcher hooks while preserving user edits."""
import ast
import copy
import json
import os
from pathlib import Path, PureWindowsPath
import shlex
import subprocess
import sys
import tempfile
import types
import unittest

sys.dont_write_bytecode = True
from _platform import BASH, home_env, posix

ROOT = Path(__file__).resolve().parents[1]
OLD_STATUS_LINE = {"type": "command", "command": 'python3 "$HOME/.claude/harness/statusline/statusline.py"'}


def entries(hooks):
    return [hook for groups in hooks.values() for group in groups for hook in group["hooks"]]


class LauncherUpgrade(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="harness upgrade's ")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith(("HARNESS_", "ROUTER_", "CLAUDE_", "CODEX_", "XDG_"))}
        self.env.update(home_env(self.home))
        self.env.update(CLAUDE_HOME=str(self.home / ".claude"), CODEX_HOME=str(self.home / ".codex"),
                        XDG_STATE_HOME=str(self.home / "state"), PYTHONDONTWRITEBYTECODE="1")

    def install(self, host, *args):
        result = subprocess.run([BASH, posix(ROOT / "install.sh"), "--host", host, *args],
                                env=self.env, stdin=subprocess.DEVNULL, text=True,
                                capture_output=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def legacy(self, host):
        self.install(host)
        folder = self.home / f".{host}"
        settings = folder / ("settings.json" if host == "claude" else "hooks.json")
        manifest_path = folder / "harness/install-manifest.json"
        value = json.loads(settings.read_text())
        manifest = json.loads(manifest_path.read_text())
        expected = copy.deepcopy(value["hooks"])
        old = copy.deepcopy(expected)
        for hook in entries(old):
            words = shlex.split(hook["command"])
            if words[0] == "sh" and words[2] == "harness":
                relative = words[4]
                if relative == "guard/danger_cmd_guard.py":
                    command = ["bash", (folder / "harness/hooks/guard/danger-cmd-guard.sh").as_posix()]
                else:
                    command = ["python3", (folder / "harness/hooks" / relative).as_posix(), *words[5:]]
                hook["command"] = " ".join(shlex.quote(word) for word in command)
            hook.pop("command_windows", None)
        value["hooks"] = old
        manifest["hooks"] = copy.deepcopy(old)
        settings.write_bytes((json.dumps(value) + "\n").encode())
        manifest_path.write_bytes((json.dumps(manifest) + "\n").encode())
        return settings, manifest_path, value, expected

    def test_upgrade_replaces_unchanged_owned_hooks_once(self):
        for host in ("claude", "codex"):
            with self.subTest(host=host):
                settings, manifest_path, old, expected = self.legacy(host)
                self.assertTrue(any(hook["command"].startswith("python3 ") for hook in entries(old["hooks"])))
                self.install(host)
                current = json.loads(settings.read_text())["hooks"]
                self.assertCountEqual(entries(current), entries(expected))
                self.assertCountEqual(entries(json.loads(manifest_path.read_text())["hooks"]), entries(expected))
                before = (settings.read_bytes(), manifest_path.read_bytes())
                self.install(host)
                self.assertEqual((settings.read_bytes(), manifest_path.read_bytes()), before)

    def test_upgrade_preserves_edited_and_unowned_hooks(self):
        for host in ("claude", "codex"):
            with self.subTest(host=host):
                settings, manifest_path, old, expected = self.legacy(host)
                edited = next(hook for hook in entries(old["hooks"]) if hook["command"].startswith("python3 "))
                edited["command"] += " --user-option"
                user = {"type": "command", "command": "printf 'user hook'", "timeout": 7}
                old["hooks"]["SessionStart"].append({"matcher": "startup", "hooks": [user]})
                settings.write_bytes((json.dumps(old) + "\n").encode())
                self.install(host)
                self.assertCountEqual(entries(json.loads(settings.read_text())["hooks"]),
                                      [*entries(expected), edited, user])
                self.install(host, "--uninstall")
                self.assertCountEqual(entries(json.loads(settings.read_text())["hooks"]), [edited, user])

    def test_preserved_commands_keep_their_runtime_files_on_uninstall(self):
        for host, field in (("claude", "command"), ("codex", "command"), ("codex", "command_windows")):
            with self.subTest(host=host, field=field):
                self.install(host)
                folder = self.home / f".{host}"
                settings = folder / ("settings.json" if host == "claude" else "hooks.json")
                value = json.loads(settings.read_text())
                hook = next(hook for hook in entries(value["hooks"])
                            if "guard/danger_cmd_guard.py" in hook["command"])
                if field == "command_windows":
                    hook["command"] = "printf 'user hook'"
                    target = (folder / "harness/hooks/guard/danger_cmd_guard.py").as_posix().replace("'", "''")
                    hook[field] = f"& python '{target}' # user edited"
                else:
                    hook[field] += " # user edited"
                self.write_json(settings, value)
                runtime = {path: path.read_bytes() for top in ("hooks", "statusline")
                           for path in (folder / "harness" / top).rglob("*") if path.is_file()}
                self.assertTrue(runtime)
                self.install(host, "--uninstall")
                self.assertIn(hook, entries(json.loads(settings.read_text())["hooks"]))
                for path, content in runtime.items():
                    self.assertTrue(path.is_file(), path.name)
                    self.assertEqual(path.read_bytes(), content)
                # Remove the retained reference and finish this subcase cleanly.
                self.write_json(settings, {"hooks": {}})
                self.install(host, "--uninstall")

    def test_preserved_statusline_keeps_runtime_files_on_uninstall(self):
        self.install("claude")
        folder = self.home / ".claude"
        settings = folder / "settings.json"
        value = json.loads(settings.read_text())
        value["statusLine"]["command"] += " # user edited"
        self.write_json(settings, value)
        runtime = {path: path.read_bytes() for top in ("hooks", "statusline")
                   for path in (folder / "harness" / top).rglob("*") if path.is_file()}
        self.install("claude", "--uninstall")
        self.assertEqual(json.loads(settings.read_text())["statusLine"], value["statusLine"])
        for path, content in runtime.items():
            self.assertTrue(path.is_file(), path.name)
            self.assertEqual(path.read_bytes(), content)

    def test_windows_runtime_references_accept_slashes_case_and_native_commands(self):
        body = (ROOT / "install.sh").read_text().split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
        function = next(node for node in ast.parse(body).body
                        if isinstance(node, ast.FunctionDef) and node.name == "references_runtime")
        home = PureWindowsPath("C:/Users/Example's Profile/.codex")
        scope = {"home": home, "host": "codex", "shlex": shlex, "os": types.SimpleNamespace(name="nt")}
        exec(compile(ast.Module(body=[function], type_ignores=[]), "install.sh", "exec"), scope)
        check = scope["references_runtime"]
        target = home / "harness/hooks/guard/danger_cmd_guard.py"
        commands = ["python " + shlex.quote(target.as_posix()),
                    "& python '" + str(target).replace("'", "''") + "'",
                    "& python '" + target.as_posix().swapcase().replace("'", "''") + "'"]
        for command in commands:
            for field in ("command", "command_windows"):
                with self.subTest(command=command, field=field):
                    self.assertTrue(check({"hooks": {"PreToolUse": [{"hooks": [{field: command}]}]}}))
                    self.assertTrue(check({"statusLine": {field: command}}))
        self.assertFalse(check({"statusLine": {"command_windows": "& python 'C:/other/tool.py'"}}))
        scope.update(home=self.home, os=os)
        target = (self.home / "harness/statusline/statusline.py").as_posix()
        escaped = "".join("\\" + char if char in " '\\" else char for char in target)
        self.assertTrue(check({"statusLine": {"command": "python " + escaped}}))

    def write_json(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((json.dumps(value) + "\n").encode())

    def assert_statusline_survives(self, settings, expected):
        for args in ((), (), ("--uninstall",)):
            self.install("claude", *args)
            self.assertEqual(json.loads(settings.read_text())["statusLine"], expected)

    def test_unowned_legacy_statusline_survives_install_and_uninstall(self):
        settings = self.home / ".claude/settings.json"
        self.write_json(settings, {"statusLine": OLD_STATUS_LINE})
        self.assert_statusline_survives(settings, OLD_STATUS_LINE)

    def test_edited_statusline_matching_legacy_command_survives(self):
        self.install("claude")
        settings = self.home / ".claude/settings.json"
        value = json.loads(settings.read_text())
        value["statusLine"] = OLD_STATUS_LINE
        self.write_json(settings, value)
        self.assert_statusline_survives(settings, OLD_STATUS_LINE)

    def test_owned_legacy_statusline_preserves_previous_value_on_uninstall(self):
        user_line = {"type": "command", "command": "printf 'user status'", "padding": 3}
        settings = self.home / ".claude/settings.json"
        self.write_json(settings, {"statusLine": user_line})
        self.install("claude", "--statusline")
        manifest_path = self.home / ".claude/harness/install-manifest.json"
        value = json.loads(settings.read_text())
        wanted = value["statusLine"]
        manifest = json.loads(manifest_path.read_text())
        value["statusLine"] = OLD_STATUS_LINE
        manifest["statusline"]["installed"] = OLD_STATUS_LINE
        self.write_json(settings, value)
        self.write_json(manifest_path, manifest)
        self.install("claude")
        self.assertEqual(json.loads(settings.read_text())["statusLine"], wanted)
        record = json.loads(manifest_path.read_text())["statusline"]
        self.assertEqual(record, {"installed": wanted, "had_previous": True, "previous": user_line})
        before = (settings.read_bytes(), manifest_path.read_bytes())
        self.install("claude")
        self.assertEqual((settings.read_bytes(), manifest_path.read_bytes()), before)
        self.install("claude", "--uninstall")
        self.assertEqual(json.loads(settings.read_text())["statusLine"], user_line)

    def test_explicit_statusline_override_restores_unowned_legacy_value(self):
        settings = self.home / ".claude/settings.json"
        self.write_json(settings, {"statusLine": OLD_STATUS_LINE})
        self.install("claude", "--statusline")
        self.assertNotEqual(json.loads(settings.read_text())["statusLine"], OLD_STATUS_LINE)
        self.install("claude", "--uninstall")
        self.assertEqual(json.loads(settings.read_text())["statusLine"], OLD_STATUS_LINE)

    def test_owned_legacy_statusline_without_previous_is_removed_on_uninstall(self):
        self.install("claude")
        settings = self.home / ".claude/settings.json"
        manifest_path = self.home / ".claude/harness/install-manifest.json"
        value = json.loads(settings.read_text())
        wanted = value["statusLine"]
        manifest = json.loads(manifest_path.read_text())
        value["statusLine"] = OLD_STATUS_LINE
        manifest["statusline"]["installed"] = OLD_STATUS_LINE
        self.write_json(settings, value)
        self.write_json(manifest_path, manifest)
        self.install("claude")
        self.assertEqual(json.loads(settings.read_text())["statusLine"], wanted)
        self.install("claude", "--uninstall")
        self.assertNotIn("statusLine", json.loads(settings.read_text()) if settings.exists() else {})


if __name__ == "__main__":
    unittest.main()
