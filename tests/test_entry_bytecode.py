#!/usr/bin/env python3
"""Configured hook entry points must not leave Python bytecode behind."""
import ast
import re
import json
import tempfile
import os
from pathlib import Path
import subprocess
import shutil
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
KNOWN = {
    Path("hooks/checkpoint/dispatch.py"),
    Path("hooks/guard/danger_cmd_guard.py"),
    Path("hooks/helpers/_verification.py"),
    Path("statusline/statusline.py"),
    Path("statusline/spend_hook.py"),
}


def configured_entries(root=ROOT):
    entries = set()
    for config in ("examples/settings.example.json", "codex/hooks.json"):
        settings = json.loads((root / config).read_text())
        commands = []
        for matchers in settings.get("hooks", {}).values():
            for matcher in matchers:
                commands.extend(hook["command"] for hook in matcher.get("hooks", []))
        if "statusLine" in settings:
            commands.append(settings["statusLine"]["command"])
        for command in commands:
            found = re.search(r"(?:hooks|statusline)/[\w/-]+\.py", command)
            if found:
                entries.add(Path(found.group()))
                continue
            found = re.search(r"(?:hooks|statusline)/[\w/-]+\.sh", command)
            if not found:
                continue
            shell = root / found.group()
            source = shell.read_text()
            for line in source.splitlines():
                if re.search(r"\bexec python3\b", line):
                    script = re.search(r"([\w-]+\.py)\b", line)
                    if script:
                        entries.add(shell.parent.relative_to(root) / script.group(1))
    return entries


def has_bytecode_line(root, entry):
    body = ast.parse((root / entry).read_text()).body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]
    if len(body) < 2:
        return False
    first, assignment = body[0], body[1]
    return (isinstance(first, ast.Import) and [a.name for a in first.names] == ["sys"]
            and isinstance(assignment, ast.Assign) and len(assignment.targets) == 1
            and isinstance(assignment.targets[0], ast.Attribute)
            and assignment.targets[0].attr == "dont_write_bytecode"
            and isinstance(assignment.targets[0].value, ast.Name)
            and assignment.targets[0].value.id == "sys"
            and isinstance(assignment.value, ast.Constant)
            and assignment.value.value is True)


def missing_bytecode_line(root, entries):
    return sorted(e for e in entries if not has_bytecode_line(root, e))


def cache_leaks(root, entries):
    """Run each entry from a temp copy; return entries that failed or left a cache."""
    bad = []
    with tempfile.TemporaryDirectory(prefix="harness-bytecode-") as temp:
        base = Path(temp)
        for dirname in ("hooks", "statusline"):
            shutil.copytree(root / dirname, base / dirname,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        home = base / "home"
        home.mkdir()
        state = base / "state"
        state.mkdir()
        env = dict(os.environ, HOME=str(home), XDG_STATE_HOME=str(state))
        env.pop("PYTHONDONTWRITEBYTECODE", None)
        for entry in sorted(entries):
            before = set(base.rglob("__pycache__"))
            result = subprocess.run([sys.executable, str(base / entry)], input="{}",
                                    capture_output=True, text=True, env=env,
                                    cwd=base, timeout=10)
            if result.returncode != 0 or set(base.rglob("__pycache__")) != before:
                bad.append(entry)
    return bad


def run_no_cache_check(root):
    return cache_leaks(root, configured_entries(root))


class EntryBytecodeTests(unittest.TestCase):
    def test_every_configured_python_entry_disables_bytecode_first(self):
        entries = configured_entries()
        self.assertTrue(entries)
        self.assertTrue(KNOWN <= entries)
        self.assertEqual(missing_bytecode_line(ROOT, entries), [])

    def test_entries_run_without_creating_caches(self):
        self.assertEqual(run_no_cache_check(ROOT), [])

    def test_new_configured_hook_without_bytecode_line_goes_red(self):
        with tempfile.TemporaryDirectory(prefix="harness-synth-") as temp:
            base = Path(temp)
            for dirname in ("hooks", "statusline", "examples", "codex"):
                shutil.copytree(ROOT / dirname, base / dirname,
                                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            synth = base / "hooks/newhook"
            synth.mkdir()
            (synth / "helper.py").write_text("VALUE = 1\n")
            (synth / "newhook.py").write_text(
                '"""Synthetic hook."""\nimport sys\nsys.path.insert(0, sys.path[0])\n'
                "import helper\n")
            config = base / "examples/settings.example.json"
            settings = json.loads(config.read_text())
            settings["hooks"]["Stop"].append({"hooks": [{
                "type": "command",
                "command": "python3 $HOME/.claude/hooks/newhook/newhook.py"}]})
            config.write_text(json.dumps(settings))
            entries = configured_entries(base)
            self.assertIn(Path("hooks/newhook/newhook.py"), entries)
            self.assertIn(Path("hooks/newhook/newhook.py"), missing_bytecode_line(base, entries))
            self.assertEqual(run_no_cache_check(base), [Path("hooks/newhook/newhook.py")])
            self.assertEqual(missing_bytecode_line(ROOT, configured_entries()), [])
            self.assertEqual(cache_leaks(base, entries - {Path("hooks/newhook/newhook.py")}), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
