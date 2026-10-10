#!/usr/bin/env python3
"""Generated Claude Code marketplace and installer plugin status."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
import sys
import subprocess
import re

ROOT = Path(__file__).resolve().parents[1]
VERSION = (ROOT / "VERSION").read_text().strip()
SKIP_ROUTER = "Router checkout not found (set ROUTER_DIR)"
GUARD = re.compile(r'\[ -e "\$\{CLAUDE_HOME:-\$HOME/\.claude\}/harness/install-manifest\.json" \] \|\| '
                   r'(bash|python3) "\$\{CLAUDE_PLUGIN_ROOT\}/hooks/([\w/-]+\.(?:sh|py))"(.*)')
SCRIPT = re.compile(r'(bash|python3) "\$HOME/\.claude/harness/hooks/([\w/-]+\.(?:sh|py))"(.*)')


def router_dir():
    """ROUTER_DIR when set (it must exist), else ./router when present, else None."""
    chosen = os.environ.get("ROUTER_DIR")
    if chosen is not None:
        return Path(chosen), True
    if Path("router").is_dir():
        return Path("router"), False
    return None, False


def tree_hash(root):
    h = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        h.update(str(path.relative_to(root)).encode())
        if path.is_symlink():
            h.update(os.readlink(path).encode())
        elif path.is_file():
            h.update(path.read_bytes())
    return h.hexdigest()


class Scratch(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.home = self.base / "home"
        self.claude = self.base / "claude"
        self.env = {**os.environ, "HOME": str(self.home), "CLAUDE_HOME": str(self.claude),
                    "XDG_CONFIG_HOME": str(self.base / "config"), "XDG_STATE_HOME": str(self.base / "state"),
                    "PYTHONDONTWRITEBYTECODE": "1"}

    def copy_repo(self):
        path = self.base / "repo"
        shutil.copytree(ROOT, path, symlinks=True, ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"))
        return path

    def gen(self, root, *args):
        return subprocess.run([sys.executable, str(root / "scripts/gen_plugin.py"), *args], env=self.env,
                              capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)


class Generated(Scratch):
    def test_drift_repairs_and_idempotence(self):
        repo = self.copy_repo()
        self.assertEqual(self.gen(repo, "--check").returncode, 0)
        cases = (("plugins/shared-roles/agents/judge.md", "changed"),
                 ("plugins/harness/extra", "extra"),
                 ("plugins/harness/hooks/hooks.json", "missing"))
        for relative, kind in cases:
            with self.subTest(kind=kind):
                path = repo / relative
                if kind == "missing":
                    path.unlink()
                else:
                    path.write_bytes(b"changed")
                before = tree_hash(repo)
                result = self.gen(repo, "--check")
                self.assertEqual((result.returncode, result.stdout, result.stderr),
                                 (1, "", f"gen_plugin: drift at {relative} ({kind}); run python3 scripts/gen_plugin.py\n"))
                self.assertEqual(tree_hash(repo), before)
                self.assertEqual(self.gen(repo).returncode, 0)
        (repo / "VERSION").write_text("9.9.9\n")
        self.assertIn("plugins/harness/.claude-plugin/plugin.json (changed)", self.gen(repo, "--check").stderr)
        self.assertEqual(self.gen(repo).returncode, 0)
        role = repo / "plugins/shared-roles/agents/worker.md"
        role.unlink()
        role.symlink_to(repo / "agents/worker.md")
        self.assertIn("worker.md (symlink)", self.gen(repo, "--check").stderr)
        self.assertEqual(self.gen(repo).returncode, 0)
        self.assertEqual(self.gen(repo, "--check").returncode, 0)
        self.assertIn("(0 written, 0 removed)", self.gen(repo).stdout)

    def test_skill_index_names_plugin_roles(self):
        line = ("When Harness is installed as a plugin, the shared roles are `shared-roles:<name>` "
                "(for example `shared-roles:sweeper`).")
        description = ("description: Choose a Harness skill or role for the task. Load when deciding how to resume, "
                       "verify, test, plan, delegate, or document work; read only the matching one.")
        for path in (ROOT / "skills/skill-index/SKILL.md", ROOT / "plugins/harness/skills/skill-index/SKILL.md"):
            text = path.read_text()
            self.assertEqual(text.splitlines().count(line), 1, str(path))
            self.assertEqual(text.split("---")[1].splitlines()[2], description)
        self.assertTrue((ROOT / "agents/sweeper.md").is_file())

    def test_source_role_must_match_shared_pin(self):
        repo = self.copy_repo()
        role = repo / "agents/judge.md"
        role.write_bytes(role.read_bytes() + b"\n")
        before = tree_hash(repo)
        result = self.gen(repo, "--check")
        self.assertEqual((result.returncode, result.stdout), (1, ""))
        self.assertEqual(result.stderr,
                         "gen_plugin: agents/judge.md differs from agents/SHARED.sha256; "
                         "fix the role or its pin first\n")
        self.assertEqual(tree_hash(repo), before)

    def test_manifest_roles_and_no_statusline(self):
        marketplace = json.loads((ROOT / ".claude-plugin/marketplace.json").read_text())
        self.assertEqual(marketplace, {"name": "harness", "owner": {"name": "AEIA", "url": "https://aeia.dev"},
                                       "plugins": [{"name": "harness", "source": "./plugins/harness"},
                                                   {"name": "shared-roles", "source": "./plugins/shared-roles"}]})
        plugin = json.loads((ROOT / "plugins/harness/.claude-plugin/plugin.json").read_text())
        self.assertEqual((plugin["name"], plugin["version"], plugin["homepage"], plugin["repository"], plugin["license"]),
                         ("harness", VERSION, "https://github.com/aeiadev/harness", "https://github.com/aeiadev/harness", "MIT"))
        self.assertEqual({p.name for p in (ROOT / "plugins/harness/skills").iterdir()},
                         {"checkpoint", "project-handoff", "skill-index", "tdd", "verify-patch-landed"})
        self.assertFalse(any("statusline" in str(p).lower() for p in (ROOT / "plugins").rglob("*")))
        for line in (ROOT / "agents/SHARED.sha256").read_text().splitlines():
            digest, name = line.split("  ")
            if "/" in name:
                continue
            data = (ROOT / "plugins/shared-roles/agents" / name).read_bytes()
            self.assertEqual(data, (ROOT / "agents" / name).read_bytes())
            self.assertEqual(hashlib.sha256(data).hexdigest(), digest)

    def test_shared_roles_equal_routers(self):
        router, required = router_dir()
        if router is None:
            self.skipTest(SKIP_ROUTER)
        self.assertTrue((router / "plugins/shared-roles").is_dir(), f"ROUTER_DIR {router} has no plugins/shared-roles")
        self.assertEqual(tree_hash(ROOT / "plugins/shared-roles"), tree_hash(router / "plugins/shared-roles"))

    def test_router_lookup_skips_without_a_checkout_and_fails_on_a_missing_dir(self):
        def run(**extra):
            env = dict(os.environ, HOME=str(self.home), XDG_CONFIG_HOME=str(self.base / "config"), **extra)
            if "ROUTER_DIR" not in extra:
                env.pop("ROUTER_DIR", None)
            return subprocess.run([sys.executable, str(Path(__file__).resolve()), "-v",
                                   "Generated.test_shared_roles_equal_routers"],
                                  cwd=self.base, env=env, capture_output=True, text=True,
                                  stdin=subprocess.DEVNULL, timeout=60)
        self.home.mkdir()
        skipped = run()
        self.assertEqual(skipped.returncode, 0, skipped.stderr)
        self.assertIn(f"skipped '{SKIP_ROUTER}'", skipped.stderr)
        missing = self.base / "no-such-router"
        failed = run(ROUTER_DIR=str(missing))
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn(f"ROUTER_DIR {missing} has no plugins/shared-roles", failed.stderr)

    def test_hook_commands_keep_registration_and_stand_down(self):
        example = json.loads((ROOT / "examples/settings.example.json").read_text())["hooks"]
        plugin = json.loads((ROOT / "plugins/harness/hooks/hooks.json").read_text())["hooks"]
        self.assertEqual(set(plugin), set(example))
        for event in example:
            self.assertEqual(len(plugin[event]), len(example[event]))
            for original, generated in zip(example[event], plugin[event]):
                self.assertEqual({k: v for k, v in generated.items() if k != "hooks"},
                                 {k: v for k, v in original.items() if k != "hooks"})
                for left, right in zip(original["hooks"], generated["hooks"]):
                    m = GUARD.fullmatch(right["command"])
                    self.assertIsNotNone(m, (event, right))
                    self.assertEqual(m.groups(), SCRIPT.fullmatch(left["command"]).groups())
                    self.assertEqual({k: v for k, v in right.items() if k != "command"},
                                     {k: v for k, v in left.items() if k != "command"})

    def test_hook_commands_execute_and_use_plugin_policy(self):
        plugin = self.base / "plugin"
        shutil.copytree(ROOT / "plugins/harness", plugin)
        hooks = json.loads((plugin / "hooks/hooks.json").read_text())["hooks"]
        guard = hooks["PreToolUse"][0]["hooks"][0]["command"]
        dispatch = hooks["PostToolUse"][0]["hooks"][0]["command"]
        env = {**self.env, "CLAUDE_PLUGIN_ROOT": str(plugin)}
        def invoke(command, payload, state):
            return subprocess.run(["sh", "-c", command], input=json.dumps(payload), text=True,
                                  env={**env, "XDG_STATE_HOME": str(self.base / state)},
                                  capture_output=True, timeout=30)
        danger = {"tool_name": "Bash", "tool_input": {"command": "rm -rf /"}}
        # The template's script path is installed under CLAUDE_HOME in a real script install.
        script = self.claude / "harness/hooks/guard"
        script.mkdir(parents=True)
        for path in (ROOT / "hooks/guard").iterdir():
            if path.is_file():
                shutil.copy2(path, script / path.name)
        original = invoke(f'bash "{script / "danger-cmd-guard.sh"}"', danger, "script")
        result = invoke(guard, danger, "plugin")
        self.assertEqual((result.returncode, result.stdout, result.stderr),
                         (original.returncode, original.stdout, original.stderr))
        self.assertEqual(result.returncode, 2)
        event = {"session_id": "plugin-test", "cwd": str(self.base),
                 "context_window": {"current_usage": {"input_tokens": 800}}}
        plugin_policy = plugin / "hooks/checkpoint/policy.json"
        policy = json.loads(plugin_policy.read_text())
        policy["window_tokens"] = 1000
        plugin_policy.write_text(json.dumps(policy))
        script_result = subprocess.run([sys.executable, str(ROOT / "hooks/checkpoint/dispatch.py"), "check", "--host=claude"],
                                       input=json.dumps(event), text=True, env={**env, "XDG_STATE_HOME": str(self.base / "original")},
                                       capture_output=True, timeout=30)
        plugin_result = invoke(dispatch, event, "copy")
        self.assertEqual((script_result.returncode, script_result.stdout), (0, ""))
        self.assertEqual(plugin_result.returncode, 0)
        self.assertIn("Context meter", plugin_result.stdout)
        manifest = self.claude / "harness/install-manifest.json"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text("{}")
        for command, payload in ((guard, danger), (dispatch, event)):
            result = invoke(command, payload, "standing")
            self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))


class Status(Scratch):
    def lines(self):
        before = tree_hash(self.base)
        result = subprocess.run(["bash", str(ROOT / "install.sh"), "--status", "--host", "claude"], env=self.env,
                                capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
        self.assertEqual((result.returncode, result.stderr), (0, ""), result.stdout)
        self.assertEqual(tree_hash(self.base), before)
        return [s for s in result.stdout.splitlines() if s.startswith("plugin: ")]

    def test_not_enabled_and_cached(self):
        self.assertEqual(self.lines(), ["plugin: harness not enabled, not cached", "plugin: shared-roles not enabled, not cached"])
        self.claude.mkdir()
        (self.claude / "settings.json").write_text(json.dumps({"enabledPlugins": {"harness@harness": True,
                                                                        "shared-roles@harness": True}}))
        for plugin, version in (("harness", VERSION), ("shared-roles", "0.2.0")):
            (self.claude / "plugins/cache/harness" / plugin / version).mkdir(parents=True)
        self.assertEqual(self.lines(), [f"plugin: harness enabled, cached {VERSION} (current)",
                                        f"plugin: shared-roles enabled, cached 0.2.0 (stale, checkout {VERSION})"])

    def test_stand_down_and_doubled_roles(self):
        self.claude.mkdir()
        (self.claude / "settings.json").write_text(json.dumps({"enabledPlugins": {"harness@harness": True,
                                                                        "shared-roles@harness": True}}))
        (self.claude / "harness").mkdir()
        (self.claude / "harness/install-manifest.json").write_text("{}")
        roles = self.claude / "agents"
        roles.mkdir()
        for name in ("builder", "worker"):
            (roles / (name + ".md")).write_text("role")
        self.assertEqual(self.lines(), ["plugin: harness enabled, not cached; stands down: script install present",
                                        "plugin: shared-roles enabled, not cached",
                                        f"plugin: roles in both {roles} and shared-roles: builder, worker "
                                        "(doubled descriptions cost tokens every turn)"])

    def test_malformed_settings(self):
        self.claude.mkdir()
        settings = self.claude / "settings.json"
        cases = (("{", "is not valid JSON"), ("[]", "is not a JSON object"),
                 ('{"enabledPlugins": []}', "enabledPlugins is not an object"),
                 ('{"enabledPlugins": {"harness@harness": "yes"}}',
                  'enabledPlugins["harness@harness"] is not true or false'))
        for content, reason in cases:
            with self.subTest(content=content):
                settings.write_text(content)
                self.assertEqual(self.lines(), [f"plugin: {settings} {reason}",
                                                "plugin: harness enabled unknown, not cached",
                                                "plugin: shared-roles enabled unknown, not cached"])
        settings.unlink()
        settings.mkdir()
        self.assertEqual(self.lines(), ["plugin: settings.json/ not readable: Is a directory",
                                        "plugin: harness enabled unknown, not cached",
                                        "plugin: shared-roles enabled unknown, not cached"])

    def test_bad_cache_path_names_os_error(self):
        self.claude.mkdir()
        cache = self.claude / "plugins/cache/harness"
        cache.parent.mkdir(parents=True)
        cache.write_text("not a directory")
        self.assertEqual(self.lines(), ["plugin: plugins/cache/harness not readable: Not a directory",
                                        "plugin: harness not enabled, cached unknown",
                                        "plugin: shared-roles not enabled, cached unknown"])

    def test_cache_directory_missing_read_or_search_names_os_error(self):
        if os.geteuid() == 0:
            self.skipTest("root can traverse mode-restricted directories")
        cache = self.claude / "plugins/cache/harness"
        cache.mkdir(parents=True)
        for mode in (0o100, 0o400):
            with self.subTest(mode=oct(mode)):
                before = tree_hash(self.base)
                cache.chmod(mode)
                try:
                    try:
                        if mode == 0o100:
                            os.listdir(cache)
                        else:
                            os.stat(cache / ".harness-status-probe")
                    except OSError as error:
                        self.assertNotIsInstance(error, FileNotFoundError)
                        reason = error.strerror
                    else:
                        self.fail("the OS did not deny the requested operation")
                    result = subprocess.run(["bash", str(ROOT / "install.sh"), "--status", "--host", "claude"],
                                            env=self.env, capture_output=True, text=True,
                                            stdin=subprocess.DEVNULL, timeout=60)
                    self.assertEqual((result.returncode, result.stderr), (0, ""))
                    plugin_lines = [row for row in result.stdout.splitlines() if row.startswith("plugin: ")]
                    self.assertEqual(plugin_lines,
                                     [f"plugin: plugins/cache/harness/ not readable: {reason}",
                                      "plugin: harness not enabled, cached unknown",
                                      "plugin: shared-roles not enabled, cached unknown"])
                finally:
                    cache.chmod(0o755)
                self.assertEqual(tree_hash(self.base), before)


if __name__ == "__main__":
    unittest.main()
