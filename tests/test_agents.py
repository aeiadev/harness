#!/usr/bin/env python3
"""Check public role definitions and installer behavior without network or model calls."""

import copy
import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_MODELS = {
    "sweeper": "haiku",
    "researcher": "sonnet",
    "planner": "sonnet",
    "builder": "sonnet",
    "builder-in-place": "sonnet",
    "judge": "opus",
    "worker": "sonnet",
    "test-writer": "sonnet",
    "docs-writer": "sonnet",
}
READ_ONLY_ROLES = {"sweeper", "researcher", "planner", "judge"}
READ_ONLY_TOOLS = {"Read", "Grep", "Glob", "Bash", "WebSearch", "WebFetch"}
ALL_TOOLS = READ_ONLY_TOOLS | {"Write", "Edit"}
EXPECTED_TURNS = {"sweeper": 30, "researcher": 40, "planner": 40,
                  "judge": 40, "builder": 80, "builder-in-place": 80,
                  "worker": 80, "test-writer": 80, "docs-writer": 80}
RETURN_LIMITS = {"sweeper": 3000, "researcher": 5000, "planner": 5000,
                 "judge": 1500, "builder": 1500, "builder-in-place": 1500,
                 "worker": 5000, "test-writer": 1500, "docs-writer": 1500}


def parse_agent(path):
    """Parse the scalar YAML frontmatter subset used by these agent files."""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        raise ValueError(f"{path.name}: missing opening frontmatter delimiter")
    try:
        end = lines.index("---", 1)
    except ValueError as error:
        raise ValueError(f"{path.name}: missing closing frontmatter delimiter") from error
    metadata = {}
    for number, line in enumerate(lines[1:end], start=2):
        match = re.fullmatch(r"([A-Za-z][A-Za-z0-9_-]*): (\S.*)", line)
        if not match:
            raise ValueError(f"{path.name}:{number}: expected a nonempty scalar field")
        key, value = match.groups()
        if key in metadata:
            raise ValueError(f"{path.name}:{number}: duplicate frontmatter key {key}")
        # These definitions use plain YAML scalars. Reject syntax requiring a YAML
        # parser instead of silently accepting an invalid or ambiguous document.
        if ": " in value or " #" in value or value[0] in "[]{}&*!|>'\"%@`":
            raise ValueError(f"{path.name}:{number}: unsupported plain scalar syntax")
        metadata[key] = value
    return metadata, "\n".join(lines[end + 1 :])


def parse_codex(path):
    """Validate generated string/boolean TOML tables without dependencies on 3.10."""
    fields = {}
    table = fields
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        if line.startswith("["):
            if not re.fullmatch(r"\[[A-Za-z0-9_.-]+\]", line):
                raise ValueError(f"{path.name}: unsupported table header")
            table = fields
            for name in line[1:-1].split("."):
                table = table.setdefault(name, {})
            continue
        key, value = line.split(" = ", 1)
        key = json.loads(key) if key.startswith('"') else key
        if key in table:
            raise ValueError(f"{path.name}: duplicate key {key}")
        table[key] = json.loads(value)
        if not isinstance(table[key], (str, bool)):
            raise ValueError(f"{path.name}: unsupported value for {key}")
    if importlib.util.find_spec("tomllib"):
        import tomllib
        if tomllib.loads(path.read_text(encoding="utf-8")) != fields:
            raise ValueError(f"{path.name}: TOML does not match generated subset")
    return fields


class AgentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.files = sorted((ROOT / "agents").glob("*.md"))

    def test_exactly_nine_agent_files(self):
        self.assertEqual(
            {path.name for path in self.files},
            {name + ".md" for name in EXPECTED_MODELS},
            "agents/ must contain exactly the nine requested role markdown files",
        )

    def test_valid_frontmatter_and_unique_names(self):
        names = []
        for path in self.files:
            with self.subTest(agent=path.name):
                metadata, body = parse_agent(path)
                self.assertTrue(
                    {"name", "description", "tools", "model"} <= metadata.keys(),
                    f"{path.name}: name, description, tools, and model are required",
                )
                name = metadata["name"]
                names.append(name)
                self.assertEqual(name, path.stem, "agent name must match its filename")
                self.assertEqual(metadata["model"], EXPECTED_MODELS[name])
                self.assertEqual(metadata.get("maxTurns"), str(EXPECTED_TURNS[name]))
                self.assertNotRegex(metadata["description"], r"(?i)router|seat-")
                self.assertGreater(len(metadata["description"].split()), 8)
                tools = [item.strip() for item in metadata["tools"].split(",")]
                self.assertTrue(all(tools), "tools must not include empty entries")
                self.assertEqual(len(tools), len(set(tools)), "tool names must be unique")
                self.assertTrue(set(tools) <= ALL_TOOLS, "agent has an unexpected tool")
                for section in ("## Job", "## Must not", "## Return"):
                    self.assertIn(section, body, f"{path.name}: missing {section}")
                self.assertIn(f"{RETURN_LIMITS[name]} characters", body)
                self.assertRegex(body, r"(?is)longer material.*file.*name.*return")
                if name in {"builder", "builder-in-place", "worker", "test-writer", "docs-writer"}:
                    for label in ("CHANGED:", "BAR:", "OUTPUT:", "NOT DONE:", "OPEN:"):
                        self.assertIn(label, body)
                if name == "judge":
                    self.assertRegex(body, r"(?i)numbered findings")
                    self.assertIn("Start the first line with exactly one of PASS, SEND_BACK, or NOT DONE", body)
                self.assertNotIn(chr(0x2014), body, "agent text must not contain em dashes")
        self.assertEqual(len(names), len(set(names)), "agent names must be unique")

    def test_codex_roles_match_names_bodies_and_tiers(self):
        codex_files = sorted((ROOT / "codex" / "agents").glob("*.toml"))
        self.assertEqual({path.stem for path in codex_files}, set(EXPECTED_MODELS))
        tiers = {"haiku": ("gpt-6-luna", "low"), "sonnet": ("gpt-6-sol", "medium"), "opus": ("gpt-6-astra", "high")}
        for path in codex_files:
            with self.subTest(role=path.stem):
                fields = parse_codex(path)
                metadata, body = parse_agent(ROOT / "agents" / (path.stem + ".md"))
                self.assertEqual(fields["name"], metadata["name"])
                self.assertEqual(fields["description"], metadata["description"])
                base = body.strip() + "\n"
                instructions = fields["developer_instructions"]
                self.assertTrue(instructions.startswith(base), "TOML must start with the Markdown body")
                tail = instructions[len(base):]
                if path.stem in {"judge", "sweeper", "researcher", "planner", "test-writer"}:
                    self.assertTrue(tail.startswith("\n## Codex\n\n"), f"{path.name}: Codex note missing")
                    self.assertIn("Codex", tail)
                else:
                    self.assertEqual(tail, "", f"{path.name}: unexpected text after the Markdown body")
                self.assertEqual((fields["model"], fields["model_reasoning_effort"]), tiers[metadata["model"]])
                self.assertNotIn("tools", fields, "Codex has no Claude-style tool allowlist")

    def test_codex_roles_do_not_set_permissions(self):
        for path in sorted((ROOT / "codex" / "agents").glob("*.toml")):
            with self.subTest(role=path.name):
                fields = parse_codex(path)
                for key in ("sandbox_mode", "default_permissions", "permissions"):
                    self.assertNotIn(
                        key, fields.keys(),
                        f"{path.name}: {key} is ignored in Codex role files; "
                        "restrictions must come from the parent session",
                    )

    def test_judge_permissions_allow_temp_writes_and_protect_workspace(self):
        role = parse_codex(ROOT / "codex/agents/judge.toml")
        fragment = parse_codex(ROOT / "codex/judge-permissions.toml")
        self.assertEqual(fragment["default_permissions"], "harness-judge")
        self.assertEqual(set(role), {
            "name", "description", "developer_instructions", "model", "model_reasoning_effort",
        }, "role permissions are ignored; the parent session must select the profile")
        self.assertEqual(set(fragment["permissions"]), {"harness-judge"})
        profile = fragment["permissions"]["harness-judge"]
        self.assertEqual(profile["filesystem"], {
            ":root": "read", ":workspace_roots": "read",
            ":tmpdir": "write", ":slash_tmp": "write",
        }, "judge must grant temporary writes without making the source workspace writable")
        self.assertEqual(profile["network"], {"enabled": False})
        for config in (role, fragment):
            self.assertNotIn("sandbox_mode", config, "do not mix legacy and named permissions")
            self.assertNotIn("sandbox_workspace_write", config)
        readme = (ROOT / "README.md").read_text()
        self.assertIn("role instructions alone do not", readme)
        self.assertIn("enforces workspace protection", readme)
        for text in (role["developer_instructions"], readme):
            self.assertIn("inherit", text)
            self.assertIn("dedicated verification session", text)
            self.assertIn("SEND_BACK", text)

    def test_codex_generator_detects_drift(self):
        result = subprocess.run(
            ["python3", str(ROOT / "codex" / "generate_agents.py"), "--check"],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_read_only_roles_have_no_edit_tools(self):
        for name in READ_ONLY_ROLES:
            with self.subTest(agent=name):
                metadata, body = parse_agent(ROOT / "agents" / f"{name}.md")
                tools = {item.strip() for item in metadata["tools"].split(",")}
                self.assertTrue(tools <= READ_ONLY_TOOLS, f"{name} has a mutation tool")
                self.assertIn("read-only", body.lower())

    def test_judge_is_fresh_opus_and_reruns_the_bar(self):
        metadata, body = parse_agent(ROOT / "agents" / "judge.md")
        self.assertEqual(metadata["model"], "opus", "judge must use opus")
        self.assertIn("fresh context", body.lower())
        self.assertRegex(body, r"(?i)rerun the supplied check yourself")
        self.assertIn("BAR", body)
        self.assertIn("exactly one of PASS, SEND_BACK, or NOT DONE", body)
        self.assertIn("without editing the project", body)

    def test_builder_isolation_contract(self):
        metadata, body = parse_agent(ROOT / "agents" / "builder.md")
        self.assertEqual(metadata.get("isolation"), "worktree")
        self.assertIn("own git worktree", body)
        metadata, body = parse_agent(ROOT / "agents" / "builder-in-place.md")
        self.assertNotIn("isolation", metadata, "in-place builder must use the current directory")
        self.assertIn("current directory", body)

    def test_frontmatter_parser_rejects_malformed_fields(self):
        # Exercise the validator with in-memory fixtures, without third-party YAML.
        class Fixture:
            name = "fixture.md"

            def __init__(self, content):
                self.content = content

            def read_text(self, encoding):
                return self.content

        invalid = [
            "name: missing-delimiters",
            "---\nname: no-ending\n",
            "---\nname: duplicate\nname: duplicate\n---\n",
            "---\nname:\n---\n",
            "---\ndescription: invalid: scalar\n---\n",
        ]
        for text in invalid:
            with self.subTest(frontmatter=text):
                with self.assertRaises(ValueError):
                    parse_agent(Fixture(text))


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="harness-install-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "distribution"
        self.source.mkdir()
        shutil.copy2(ROOT / "install.sh", self.source / "install.sh")
        shutil.copy2(ROOT / "VERSION", self.source / "VERSION")
        (self.source / "examples").mkdir()
        shutil.copy2(ROOT / "examples" / "settings.example.json", self.source / "examples" / "settings.example.json")
        # Fixtures test packaging independently from runtime hook behavior.
        for relative in (
            "hooks/context/inject-project-context.sh",
            "hooks/guard/danger-cmd-guard.sh",
            "hooks/helpers/auto-format.sh",
            "hooks/helpers/verify-patch-landed-auto.sh",
            "hooks/helpers/stop-check-verify-patch.sh",
            "hooks/checkpoint/dispatch.py",
            "hooks/checkpoint/storage.py",
            "hooks/checkpoint/journal.py",
            "hooks/checkpoint/policy.json",
            "hooks/checkpoint/outline.md",
            "hooks/checkpoint/guide.md",
            "statusline/statusline.py",
            "statusline/spend_hook.py",
            "agents/sweeper.md",
            "skills/checkpoint/SKILL.md",
        ):
            path = self.source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"fixture: {relative}\n", encoding="utf-8")
        self.user_home = self.root / "user"
        self.user_home.mkdir()
        self.claude_home = self.user_home / ".claude"
        self.env = {**os.environ, "HOME": str(self.user_home)}
        self.env.pop("CLAUDE_HOME", None)
        self.env.pop("CODEX_HOME", None)

    def run_install(self, *flags, success=True, env=None):
        result = subprocess.run(
            ["bash", str(self.source / "install.sh"), "--host", "claude", *flags],
            env=env or self.env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, "installer unexpectedly succeeded")
        return result

    def write_settings(self, settings):
        self.claude_home.mkdir(parents=True, exist_ok=True)
        (self.claude_home / "settings.json").write_text(json.dumps(settings), encoding="utf-8")

    def settings(self):
        return json.loads((self.claude_home / "settings.json").read_text(encoding="utf-8"))

    def snapshot(self):
        return {
            str(path.relative_to(self.root)): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in self.root.rglob("*")
            if path.is_file()
        }

    def test_fresh_install_wires_every_example_hook(self):
        self.run_install()
        expected = json.loads((self.source / "examples" / "settings.example.json").read_text())
        self.assertEqual(self.settings(), expected)
        self.assertTrue((self.claude_home / "harness/hooks/guard/danger-cmd-guard.sh").is_file())
        self.assertTrue((self.claude_home / "harness/statusline/statusline.py").is_file())
        self.assertTrue((self.claude_home / "agents/sweeper.md").is_file())
        self.assertTrue((self.claude_home / "skills/checkpoint/SKILL.md").is_file())
        commands = [hook["command"] for groups in expected["hooks"].values() for group in groups for hook in group["hooks"]]
        commands.append(expected["statusLine"]["command"])
        for command in commands:
            script = shlex.split(command)[1].replace("$HOME", str(self.user_home), 1)
            self.assertTrue(Path(script).is_file(), f"wired executable is missing: {script}")

    def test_preserves_user_settings_hooks_and_statusline(self):
        initial = {
            "permissions": {"allow": ["Read"]},
            "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo user-hook"}]}]},
            "statusLine": {"type": "command", "command": "echo user-status"},
        }
        self.write_settings(initial)
        self.run_install()
        installed = self.settings()
        self.assertEqual(installed["permissions"], initial["permissions"])
        self.assertEqual(installed["statusLine"], initial["statusLine"])
        self.assertEqual(installed["hooks"]["PreToolUse"][0], initial["hooks"]["PreToolUse"][0])
        backups = list(self.claude_home.glob("settings.json.harness-backup-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(json.loads(backups[0].read_text()), initial)
        self.run_install("--uninstall")
        self.assertEqual(self.settings(), initial)

    def test_second_install_is_byte_and_timestamp_idempotent(self):
        self.write_settings({"theme": "dark"})
        self.run_install()
        before = self.snapshot()
        self.run_install()
        self.assertEqual(self.snapshot(), before)

    def test_dry_run_makes_no_files_or_directories(self):
        before = self.snapshot()
        self.run_install("--dry-run")
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.claude_home.exists())

    def test_dry_run_does_not_change_existing_install(self):
        self.run_install()
        before = self.snapshot()
        self.run_install("--dry-run", "--statusline")
        self.run_install("--dry-run", "--uninstall")
        self.assertEqual(self.snapshot(), before)

    def test_statusline_override_is_explicit_and_restored(self):
        previous = {"type": "command", "command": "echo custom", "padding": 1}
        self.write_settings({"statusLine": previous})
        self.run_install()
        self.assertEqual(self.settings()["statusLine"], previous)
        self.run_install("--statusline")
        self.assertIn("harness/statusline/statusline.py", self.settings()["statusLine"]["command"])
        self.run_install("--statusline")
        self.run_install("--uninstall")
        self.assertEqual(self.settings(), {"statusLine": previous})

    def test_custom_home_is_used_and_shell_quoted(self):
        self.claude_home = self.root / "custom home ' dollar $ value"
        env = {**self.env, "CLAUDE_HOME": str(self.claude_home)}
        self.run_install(env=env)
        command = self.settings()["statusLine"]["command"]
        self.assertEqual(shlex.split(command), ["python3", str(self.claude_home / "harness/statusline/statusline.py")])
        self.assertFalse((self.user_home / ".claude").exists())
        self.run_install("--uninstall", env=env)
        self.assertFalse((self.claude_home / "settings.json").exists(), "uninstall left the settings file it created")

    def test_statusline_replacement_restores_latest_user_value(self):
        first = {"type": "command", "command": "echo first"}
        latest = {"type": "command", "command": "echo latest"}
        self.write_settings({"statusLine": first})
        self.run_install("--statusline")
        settings = self.settings()
        settings["statusLine"] = latest
        self.write_settings(settings)
        self.run_install("--statusline")
        self.run_install("--uninstall")
        self.assertEqual(self.settings(), {"statusLine": latest})

    def test_preexisting_empty_hook_groups_are_preserved(self):
        initial = {"hooks": {"PostToolUse": [{"hooks": []}]}}
        self.write_settings(initial)
        self.run_install()
        self.run_install("--uninstall")
        self.assertEqual(self.settings(), initial)

    def test_uninstall_removes_only_event_lists_emptied_by_harness(self):
        self.write_settings({"hooks": {"PreToolUse": [], "OtherEvent": []}, "user": "keep"})
        self.run_install()
        host_home = self.codex_home if isinstance(self, CodexInstallTests) else self.claude_home
        manifest = json.loads((host_home / "harness/install-manifest.json").read_text())
        self.assertEqual(set(manifest["existing_empty_events"]), {"PreToolUse", "OtherEvent"})
        installed = self.settings()
        installed["hooks"]["PostCompact"] = []  # User emptied this after install.
        installed["hooks"]["PostToolUse"][0]["hooks"].append(
            {"type": "command", "command": "echo other-tool"}
        )
        self.write_settings(installed)
        self.run_install("--uninstall")
        self.assertEqual(self.settings(), {
            "hooks": {
                "PreToolUse": [],
                "OtherEvent": [],
                "PostCompact": [],
                "PostToolUse": [{"hooks": [{"type": "command", "command": "echo other-tool"}]}],
            },
            "user": "keep",
        })

    def test_uninstall_removes_event_whose_original_other_hook_was_later_removed(self):
        other = {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo other-tool"}]}
        self.write_settings({"hooks": {"PreToolUse": [other]}})
        self.run_install()
        installed = self.settings()
        host_home = self.codex_home if isinstance(self, CodexInstallTests) else self.claude_home
        manifest = json.loads((host_home / "harness/install-manifest.json").read_text())
        self.assertNotIn("PreToolUse", manifest["existing_empty_events"],
                         "an event with another hook at install must not count as empty")
        installed["hooks"]["PreToolUse"].remove(other)
        self.write_settings(installed)
        self.run_install("--uninstall")
        self.assertEqual(self.settings(), {"hooks": {}},
                         "uninstall left an empty event whose original hook was removed")

    def test_uninstall_keeps_other_hook_present_at_install(self):
        other = {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo other-tool"}]}
        initial = {"hooks": {"PreToolUse": [other]}}
        self.write_settings(initial)
        self.run_install()
        self.run_install("--uninstall")
        self.assertEqual(self.settings(), initial)

    def test_upgrade_legacy_event_manifest_does_not_keep_nonempty_event(self):
        other = {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo other-tool"}]}
        self.write_settings({"hooks": {"PreToolUse": [other]}})
        self.run_install()
        host_home = self.codex_home if isinstance(self, CodexInstallTests) else self.claude_home
        manifest_path = host_home / "harness/install-manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest.pop("existing_empty_events")
        manifest["existing_events"] = ["PreToolUse"]
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        self.run_install()
        upgraded = json.loads(manifest_path.read_text())
        self.assertNotIn("PreToolUse", upgraded["existing_empty_events"])
        installed = self.settings()
        installed["hooks"]["PreToolUse"].remove(other)
        self.write_settings(installed)
        self.run_install("--uninstall")
        self.assertEqual(self.settings(), {"hooks": {}},
                         "legacy manifest left an event whose original hook was removed")

    def test_examples_point_to_router_defaults(self):
        for name in ("CLAUDE.md.example", "AGENTS.md.example"):
            with self.subTest(name=name):
                example = (ROOT / "examples" / name).read_text(encoding="utf-8")
                self.assertIn("--with-defaults", example,
                              f"{name} must point readers to Router delegation defaults")

    def check_retained_runtime(self, kind, custom_home):
        self.user_home = self.root / f"user-{kind}-{custom_home}"
        self.user_home.mkdir()
        self.claude_home = self.root / f"custom '{kind}' $ home" if custom_home else self.user_home / ".claude"
        self.env["HOME"] = str(self.user_home)
        env = {**self.env, **({"CLAUDE_HOME": str(self.claude_home)} if custom_home else {})}
        if kind == "preexisting":
            path = self.claude_home / "harness/hooks/guard/danger-cmd-guard.sh"
            command = f"bash {shlex.quote(str(path))}" if custom_home else 'bash "$HOME/.claude/harness/hooks/guard/danger-cmd-guard.sh"'
            self.write_settings({"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": command, "timeout": 25}]}]}})
        self.run_install(env=env)
        settings = self.settings()
        if kind == "hook":
            settings["hooks"]["PreToolUse"][0]["hooks"][0]["timeout"] = 25
        elif kind == "statusline":
            settings["statusLine"]["padding"] = 1
        self.write_settings(settings)
        retained = copy.deepcopy(settings["statusLine"] if kind == "statusline" else settings["hooks"]["PreToolUse"])
        result = self.run_install("--uninstall", env=env)
        actual = self.settings()["statusLine"] if kind == "statusline" else self.settings()["hooks"]["PreToolUse"]
        self.assertEqual(actual, retained, "customized settings must be preserved")
        for relative in ("hooks/guard/danger-cmd-guard.sh", "hooks/checkpoint/dispatch.py", "statusline/statusline.py"):
            self.assertTrue((self.claude_home / "harness" / relative).is_file(), f"retained command needs runtime file: {relative}")
        self.assertIn("Keeping runtime", result.stdout)
        self.assertTrue((self.claude_home / "harness/install-manifest.json").is_file(), "partial uninstall must retain ownership for later cleanup")
        self.write_settings({})
        self.run_install("--uninstall", env=env)
        self.assertFalse((self.claude_home / "harness/hooks/guard/danger-cmd-guard.sh").exists())
        self.assertFalse((self.claude_home / "harness/install-manifest.json").exists())

    def test_uninstall_keeps_runtime_for_customized_hook(self):
        for custom_home in (False, True):
            with self.subTest(custom_home=custom_home):
                self.check_retained_runtime("hook", custom_home)

    def test_uninstall_keeps_runtime_for_customized_statusline(self):
        for custom_home in (False, True):
            with self.subTest(custom_home=custom_home):
                self.check_retained_runtime("statusline", custom_home)

    def test_uninstall_keeps_runtime_for_preexisting_matching_hook(self):
        for custom_home in (False, True):
            with self.subTest(custom_home=custom_home):
                self.check_retained_runtime("preexisting", custom_home)

    def test_user_file_collision_fails_before_any_change(self):
        path = self.claude_home / "agents/sweeper.md"
        path.parent.mkdir(parents=True)
        path.write_text("user agent\n")
        before = self.snapshot()
        result = self.run_install(success=False)
        self.assertIn("refusing to overwrite", result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_modified_owned_file_blocks_upgrade_without_writes(self):
        self.run_install()
        path = self.claude_home / "skills/checkpoint/SKILL.md"
        path.write_text("user adaptation\n")
        before = self.snapshot()
        self.run_install(success=False)
        self.assertEqual(self.snapshot(), before)

    def test_unchanged_owned_file_can_be_upgraded(self):
        self.run_install()
        (self.source / "skills/checkpoint/SKILL.md").write_text("new version\n")
        self.run_install()
        self.assertEqual((self.claude_home / "skills/checkpoint/SKILL.md").read_text(), "new version\n")
        self.run_install("--uninstall")
        self.assertFalse((self.claude_home / "skills/checkpoint/SKILL.md").exists())

    def test_uninstall_retains_changes_and_user_additions(self):
        self.run_install()
        changed = self.claude_home / "agents/sweeper.md"
        changed.write_text("customized\n")
        added = self.claude_home / "harness/user.txt"
        added.write_text("keep me\n")
        settings = self.settings()
        settings["hooks"]["PostToolUse"][0]["hooks"].append({"type": "command", "command": "echo keep"})
        settings["statusLine"] = {"type": "command", "command": "echo new-status"}
        self.write_settings(settings)
        self.run_install("--uninstall")
        self.assertEqual(changed.read_text(), "customized\n")
        self.assertEqual(added.read_text(), "keep me\n")
        self.assertEqual(self.settings()["hooks"], {"PostToolUse": [{"hooks": [{"type": "command", "command": "echo keep"}]}]})
        self.assertEqual(self.settings()["statusLine"], {"type": "command", "command": "echo new-status"})
        self.assertFalse((self.claude_home / "harness/install-manifest.json").exists())
        self.run_install("--uninstall")

    def test_matching_preexisting_hooks_remain_unowned_but_shared_role_is_claimed(self):
        expected = json.loads((self.source / "examples" / "settings.example.json").read_text())
        initial = {"hooks": {"PreToolUse": copy.deepcopy(expected["hooks"]["PreToolUse"])}}
        initial["hooks"]["PreToolUse"][0]["hooks"][0]["timeout"] = 25
        self.write_settings(initial)
        path = self.claude_home / "agents/sweeper.md"
        path.parent.mkdir()
        shutil.copy2(self.source / "agents/sweeper.md", path)
        self.run_install()
        self.assertEqual(len(self.settings()["hooks"]["PreToolUse"]), 1)
        self.run_install("--uninstall")
        self.assertEqual(self.settings(), initial)
        self.assertFalse(path.exists())

    def test_invalid_settings_fail_without_writes(self):
        self.claude_home.mkdir()
        (self.claude_home / "settings.json").write_text("{broken")
        before = self.snapshot()
        self.run_install(success=False)
        self.assertEqual(self.snapshot(), before)

    def test_invalid_hook_shape_fails_without_writes(self):
        self.write_settings({"hooks": {"PreToolUse": {}}})
        before = self.snapshot()
        result = self.run_install(success=False)
        self.assertIn("hooks.PreToolUse must be an array", result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_missing_wired_script_fails_before_any_change(self):
        (self.source / "hooks/context/inject-project-context.sh").unlink()
        before = self.snapshot()
        result = self.run_install(success=False)
        self.assertIn("missing installation requirement: hooks/context/inject-project-context.sh", result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_preexisting_empty_manifest_is_not_overwritten(self):
        manifest = self.claude_home / "harness/install-manifest.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text("{}")
        before = self.snapshot()
        result = self.run_install(success=False)
        self.assertIn("unsupported install manifest version", result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_symlink_destination_is_rejected(self):
        self.claude_home.mkdir()
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        (self.claude_home / "agents").symlink_to(elsewhere, target_is_directory=True)
        before = self.snapshot()
        result = self.run_install(success=False)
        self.assertIn("symbolic link", result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_missing_python_names_requirement(self):
        no_python = self.root / "empty-path"
        no_python.mkdir()
        result = subprocess.run(
            [shutil.which("bash"), str(self.source / "install.sh")],
            env={**self.env, "PATH": str(no_python)},
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing requirement: python3", result.stderr)


class CodexInstallTests(unittest.TestCase):
    snapshot = InstallTests.snapshot
    test_uninstall_removes_only_event_lists_emptied_by_harness = InstallTests.test_uninstall_removes_only_event_lists_emptied_by_harness
    test_uninstall_removes_event_whose_original_other_hook_was_later_removed = InstallTests.test_uninstall_removes_event_whose_original_other_hook_was_later_removed
    test_uninstall_keeps_other_hook_present_at_install = InstallTests.test_uninstall_keeps_other_hook_present_at_install
    test_upgrade_legacy_event_manifest_does_not_keep_nonempty_event = InstallTests.test_upgrade_legacy_event_manifest_does_not_keep_nonempty_event

    def setUp(self):
        InstallTests.setUp(self)
        shutil.copytree(ROOT / "codex", self.source / "codex")
        self.codex_home = self.user_home / ".codex"

    def run_install(self, *flags, host="codex", success=True, env=None):
        command = ["bash", str(self.source / "install.sh")]
        if host is not None:
            command += ["--host", host]
        result = subprocess.run(
            command + list(flags), env=env or self.env,
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=15,
        )
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, "installer unexpectedly succeeded")
        return result

    def write_settings(self, settings):
        self.codex_home.mkdir(parents=True, exist_ok=True)
        (self.codex_home / "hooks.json").write_text(json.dumps(settings), encoding="utf-8")

    def settings(self):
        return json.loads((self.codex_home / "hooks.json").read_text(encoding="utf-8"))

    def write_config(self, contents):
        self.codex_home.mkdir(parents=True, exist_ok=True)
        target = self.codex_home / "config.toml"
        target.write_bytes(contents)
        return target

    def test_judge_profile_is_offered_but_not_enabled_by_default(self):
        initial = b'default_permissions = "personal"\n'
        target = self.write_config(initial)
        result = self.run_install()
        self.assertIn("--judge-permissions", result.stdout)
        self.assertEqual(target.read_bytes(), initial)
        self.assertFalse(list(self.codex_home.glob("config.toml.harness-backup-*")))
        self.run_install("--uninstall")
        self.assertEqual(target.read_bytes(), initial)

    def test_judge_profile_fresh_merge_repeat_and_uninstall(self):
        self.run_install("--judge-permissions")
        target = self.codex_home / "config.toml"
        config = parse_codex(target)
        self.assertNotIn("default_permissions", config, "do not select a global default profile")
        fragment = parse_codex(ROOT / "codex/judge-permissions.toml")
        self.assertEqual(config["permissions"], fragment["permissions"])
        before = self.snapshot()
        self.run_install("--judge-permissions")
        self.run_install()
        self.assertEqual(self.snapshot(), before, "repeat install must not rewrite or duplicate the profile")
        self.run_install("--uninstall")
        self.assertFalse(target.exists(), "uninstall must remove a config created only for the profile")

    def test_judge_profile_preserves_other_profile_and_restores_exact_bytes(self):
        initial = (b'# Preserve comments and line endings.\r\n'
                   b'default_permissions = "personal"\r\n\r\n[permissions]\r\n'
                   b'[permissions.personal.filesystem]\r\n":root" = "read"')
        target = self.write_config(initial)
        self.run_install("--judge-permissions")
        merged = parse_codex(target)
        self.assertEqual(merged["default_permissions"], "personal")
        self.assertEqual(merged["permissions"]["personal"], {"filesystem": {":root": "read"}})
        self.assertIn("harness-judge", merged["permissions"])
        self.assertTrue(target.read_bytes().startswith(initial))
        backups = list(self.codex_home.glob("config.toml.harness-backup-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), initial)
        self.assertEqual(backups[0].stat().st_mode & 0o777, 0o600)
        self.run_install("--uninstall")
        self.assertEqual(target.read_bytes(), initial)

    def test_judge_profile_never_claims_existing_profile(self):
        for initial in (
            b'[permissions.harness-judge.filesystem]\n":root" = "write"\n',
            b'[permissions."harness-judge".filesystem]\n":root" = "write"\n',
            b'permissions = { harness-judge = { filesystem = { ":root" = "write" } } }\n',
        ):
            with self.subTest(config=initial):
                target = self.write_config(initial)
                self.run_install("--judge-permissions")
                self.assertEqual(target.read_bytes(), initial, "existing profile must never be overwritten")
                self.run_install("--uninstall")
                self.assertEqual(target.read_bytes(), initial, "uninstall must not remove an unowned profile")

    def test_judge_uninstall_preserves_user_config_additions(self):
        initial = b'model = "user-choice"\n'
        target = self.write_config(initial)
        self.run_install("--judge-permissions")
        addition = b'\n[user_settings]\nkeep = true\n'
        target.write_bytes(target.read_bytes() + addition)
        self.run_install("--uninstall")
        self.assertEqual(target.read_bytes(), initial + addition)

    def test_judge_uninstall_keeps_modified_profile(self):
        self.run_install("--judge-permissions")
        target = self.codex_home / "config.toml"
        modified = target.read_bytes().replace(b'enabled = false', b'enabled = true')
        target.write_bytes(modified)
        self.run_install("--uninstall")
        self.assertEqual(target.read_bytes(), modified)

    def test_judge_profile_can_be_reinstalled_after_partial_uninstall(self):
        self.run_install("--judge-permissions")
        settings = self.settings()
        settings["hooks"]["SessionStart"][0]["hooks"][0]["timeout"] = 30
        self.write_settings(settings)
        self.run_install("--uninstall")
        manifest_path = self.codex_home / "harness/install-manifest.json"
        self.assertTrue(manifest_path.exists(), "edited hook should retain the runtime and manifest")
        self.assertNotIn("judge_permissions", json.loads(manifest_path.read_text()),
                         "removing the owned profile must also clear its ownership")
        self.assertFalse((self.codex_home / "config.toml").exists())
        self.run_install("--judge-permissions")
        config = parse_codex(self.codex_home / "config.toml")
        self.assertIn("harness-judge", config["permissions"])

    def test_judge_profile_dry_runs_never_write(self):
        target = self.write_config(b'model = "user-choice"\n')
        before = self.snapshot()
        self.run_install("--judge-permissions", "--dry-run")
        self.assertEqual(self.snapshot(), before)
        self.run_install("--judge-permissions")
        before = self.snapshot()
        self.run_install("--uninstall", "--dry-run")
        self.assertEqual(self.snapshot(), before)
        self.assertIn(b'harness-judge', target.read_bytes())

    def test_judge_profile_refuses_symlink_config(self):
        outside = self.root / "unrelated.toml"
        outside.write_bytes(b'model = "user-choice"\n')
        self.codex_home.mkdir()
        (self.codex_home / "config.toml").symlink_to(outside)
        before = self.snapshot()
        result = self.run_install("--judge-permissions", success=False)
        self.assertIn("symbolic link", result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_codex_merge_backup_repeat_and_restore(self):
        initial = {"user": "kept", "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo user-hook"}]}]}}
        self.write_settings(initial)
        result = self.run_install()
        self.assertIn("/hooks", result.stdout)
        self.assertIn("Trust", result.stdout)
        self.assertEqual(self.settings()["hooks"]["PreToolUse"][0], initial["hooks"]["PreToolUse"][0])
        self.assertNotIn("statusLine", self.settings())
        self.assertEqual(len(list((self.codex_home / "agents").glob("*.toml"))), 9)
        self.assertFalse(self.claude_home.exists())
        backups = list(self.codex_home.glob("hooks.json.harness-backup-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(json.loads(backups[0].read_text()), initial)
        before = self.snapshot()
        self.run_install()
        self.assertEqual(self.snapshot(), before)
        self.run_install("--uninstall")
        self.assertEqual(self.settings(), initial)
        self.assertFalse((self.codex_home / "harness").exists())
        self.assertFalse((self.codex_home / "agents").exists())

    def test_default_detection_and_explicit_both(self):
        # A private PATH provides required utilities without discovering any
        # locally installed model CLI. Neither CLI is ever executed.
        path = self.root / "bin"
        path.mkdir()
        for name in ("python3", "dirname", "bash"):
            (path / name).symlink_to(shutil.which(name))
        env = {**self.env, "PATH": str(path)}
        result = self.run_install(host=None, env=env, success=False)
        self.assertIn("no host found", result.stderr)
        self.codex_home.mkdir()
        self.run_install(host=None, env=env)
        self.assertTrue((self.codex_home / "hooks.json").is_file())
        self.assertFalse(self.claude_home.exists())
        self.run_install(host="both", env=env)
        self.assertTrue((self.claude_home / "settings.json").is_file())
        self.run_install("--uninstall", host="both", env=env)
        self.assertFalse((self.codex_home / "hooks.json").exists(), "uninstall left the hooks.json it created")
        self.assertFalse((self.claude_home / "settings.json").exists(), "uninstall left the settings.json it created")
        self.assertTrue(self.codex_home.is_dir(), "uninstall removed a host home that existed before")

    def test_custom_codex_home_is_shell_quoted_and_restored(self):
        self.codex_home = self.root / "custom home ' dollar $ value"
        env = {**self.env, "CODEX_HOME": str(self.codex_home)}
        self.run_install(env=env)
        for groups in self.settings()["hooks"].values():
            for group in groups:
                for entry in group["hooks"]:
                    words = shlex.split(entry["command"])
                    self.assertTrue(words[1].startswith(str(self.codex_home / "harness") + "/"))
                    self.assertTrue(Path(words[1]).is_file())
        self.assertFalse((self.user_home / ".codex").exists())
        self.run_install("--uninstall", env=env)
        self.assertFalse((self.codex_home / "hooks.json").exists(), "uninstall left the hooks.json it created")

    def test_modified_codex_role_is_preserved(self):
        self.run_install()
        role = self.codex_home / "agents" / "sweeper.toml"
        role.write_text('name = "custom"\n')
        self.run_install("--uninstall")
        self.assertEqual(role.read_text(), 'name = "custom"\n')

    def test_codex_invalid_json_and_dry_run_do_not_write(self):
        self.codex_home.mkdir()
        target = self.codex_home / "hooks.json"
        target.write_text("{broken")
        before = self.snapshot()
        self.run_install(success=False)
        self.assertEqual(self.snapshot(), before)
        target.write_text("{}\n")
        before = self.snapshot()
        self.run_install("--dry-run")
        self.assertEqual(self.snapshot(), before)

    def test_codex_hook_matchers_cover_actual_tool_names(self):
        template = json.loads((ROOT / "codex" / "hooks.json").read_text())
        guard_groups = template["hooks"]["PreToolUse"]
        for name in ("Bash", "exec_command", "shell_command", "functions.exec_command"):
            self.assertTrue(any(re.fullmatch(group["matcher"], name) for group in guard_groups), name)
        self.assertFalse(any(re.fullmatch(group["matcher"], "Read") for group in guard_groups))


if __name__ == "__main__":
    unittest.main(verbosity=2)
