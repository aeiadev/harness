#!/usr/bin/env python3
"""Read-only installer status and uninstall follow-ups."""
import ast
import errno
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from test_install_upgrade import Base, ROOT, SETTINGS, sha, old_tree, EXTRAS


NOT_ROOT = unittest.skipIf(os.geteuid() == 0, "mode 000 directories stay readable for root")


def ceilings_of(script):
    module = ast.parse(Path(script).read_text())
    return next(ast.literal_eval(node.value) for node in module.body if isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == "CEILINGS" for target in node.targets))


def budget_line(label, tokens, ceilings):
    tail = f", ceiling {ceilings[label]}" if label in ceilings else ""
    return f"budget: {label} {tokens} tokens always-on (estimate{tail})"


def line(actual, expected):
    assert re.fullmatch(re.escape(expected), actual) is not None, (actual, expected)


def status_rows(result):
    return [row for row in result.stdout.splitlines() if not row.startswith("plugin: ")]


HOSTILE = "HARNESS_TEST_HOSTILE_PLUGIN_CACHE"


class StatusTests(Base):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in ("CLAUDE_CODE_PLUGIN_CACHE_DIR", "HARNESS_STATUS_BUDGET_TIMEOUT"):
            os.environ.pop(name, None)

    def test_plugin_lines_ignore_the_ambient_cache_dir(self):
        result = self.status()
        self.assertEqual([row for row in result.stdout.splitlines() if row.startswith("plugin: ")],
                         ["plugin: harness not enabled, not cached", "plugin: shared-roles not enabled, not cached"])

    def test_hostile_cache_dir_exported_before_setup_changes_nothing(self):
        hostile = self.root / "hostile-file"
        hostile.write_text("not a directory")
        result = subprocess.run([sys.executable, str(Path(__file__).resolve()),
                                 "StatusTests.test_plugin_lines_ignore_the_ambient_cache_dir"],
                                env={**os.environ, "CLAUDE_CODE_PLUGIN_CACHE_DIR": str(hostile)},
                                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_non_regular_manifest_is_named_plainly(self):
        directory = self.host_home("claude") / "harness"
        directory.mkdir(parents=True)
        manifest = directory / "install-manifest.json"
        manifest.mkdir()
        line(self.lines(self.status())[2], "files: unknown (harness/install-manifest.json/ not readable: not a regular file)")
        manifest.rmdir()
        os.mkfifo(manifest)
        line(self.lines(self.status())[2], "files: unknown (harness/install-manifest.json not readable: not a regular file)")

    def test_control_characters_in_plugin_lines_are_cleaned(self):
        home = self.root / "ho\x07me"
        home.mkdir()
        (home / "settings.json").write_text("not json")
        result = self.status(env={"CLAUDE_HOME": str(home)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"plugin: {str(home / 'settings.json').replace(chr(7), '?')} is not valid JSON",
                      result.stdout.splitlines())
        self.assertNotIn("\x07", result.stdout)

    # Each plugin line that can carry a path or a name read from disk, with a newline and an ESC in it.
    UNKNOWN = ["plugin: harness enabled unknown, not cached", "plugin: shared-roles enabled unknown, not cached"]

    def hostile_home(self):
        home = self.root / "ho\nme\x1b"
        home.mkdir()
        return home

    @staticmethod
    def shown(path):
        return str(path).replace("\n", "?").replace("\x1b", "?")[:200]

    def plugin_lines(self, home, tree=ROOT):
        result = self.status(tree=tree, env={"CLAUDE_HOME": str(home)})
        self.assertEqual((result.returncode, result.stderr), (0, ""), result.stdout)
        self.assertNotIn("\x1b", result.stdout)
        return [row for row in result.stdout.splitlines() if row.startswith("plugin: ")]

    def scratch_tree(self):
        tree = self.root / "tree"
        shutil.copytree(ROOT, tree, symlinks=True,
                        ignore=shutil.ignore_patterns(".git", "plugins", "tests", "__pycache__", "*.pyc"))
        return tree

    def settings_line(self, content):
        home = self.hostile_home()
        settings = home / "settings.json"
        settings.write_text(content)
        return self.shown(settings), self.plugin_lines(home)

    def test_settings_that_is_not_an_object_is_cleaned(self):
        path, rows = self.settings_line("[]")
        self.assertEqual(rows, [f"plugin: {path} is not a JSON object", *self.UNKNOWN])

    def test_enabled_plugins_that_is_not_an_object_is_cleaned(self):
        path, rows = self.settings_line('{"enabledPlugins": []}')
        self.assertEqual(rows, [f"plugin: {path} enabledPlugins is not an object", *self.UNKNOWN])

    def test_enabled_plugins_flag_that_is_not_a_bool_is_cleaned(self):
        path, rows = self.settings_line('{"enabledPlugins": {"harness@harness": "yes"}}')
        self.assertEqual(rows, [f'plugin: {path} enabledPlugins["harness@harness"] is not true or false', *self.UNKNOWN])

    def test_settings_nested_too_deeply_is_cleaned(self):
        path, rows = self.settings_line("[" * 100000)
        self.assertEqual(rows, [f"plugin: {path} is nested too deeply", *self.UNKNOWN])

    def test_current_cached_version_skips_names_with_control_characters(self):
        home = self.hostile_home()
        version = (ROOT / "VERSION").read_text().strip()
        for plugin, name in (("harness", version), ("harness", "0.1\x1b"), ("harness", "0.2\nx"),
                             ("shared-roles", "0.2.0"), ("shared-roles", "0.1\x1b")):
            (home / "plugins/cache/harness" / plugin / name).mkdir(parents=True)
        self.assertEqual(self.plugin_lines(home), [f"plugin: harness not enabled, cached {version} (current)",
                                                   f"plugin: shared-roles not enabled, cached 0.2.0 (stale, checkout {version})"])

    def test_stale_cached_version_cleans_the_checkout_version(self):
        tree = self.scratch_tree()
        (tree / "VERSION").write_text("9.9\n\x1b9\n")
        home = self.hostile_home()
        for name in ("0.2.0", "0.1\x1b"):
            (home / "plugins/cache/harness/harness" / name).mkdir(parents=True)
        self.assertEqual(self.plugin_lines(home, tree), ["plugin: harness not enabled, cached 0.2.0 (stale, checkout 9.9??9)",
                                                         "plugin: shared-roles not enabled, not cached"])

    def test_doubled_roles_line_cleans_the_agents_path_and_role_names(self):
        tree = self.scratch_tree()
        pins = tree / "agents/SHARED.sha256"
        pins.write_text(pins.read_text() + "0" * 64 + "  ro\x1ble.md\n")
        home = self.hostile_home()
        (home / "settings.json").write_text(json.dumps({"enabledPlugins": {"shared-roles@harness": True}}))
        (home / "agents").mkdir()
        for name in ("builder.md", "ro\x1ble.md"):
            (home / "agents" / name).write_text("role")
        self.assertEqual(self.plugin_lines(home, tree),
                         ["plugin: harness not enabled, not cached", "plugin: shared-roles enabled, not cached",
                          f"plugin: roles in both {self.shown(home / 'agents')} and shared-roles: builder, ro?le "
                          "(doubled descriptions cost tokens every turn)"])

    @NOT_ROOT
    def test_search_only_ancestor_is_not_blamed_for_a_deeper_error(self):
        home = self.host_home("claude")
        ancestor = home / "plugins"
        deep = ancestor / "cache/harness/harness"
        deep.mkdir(parents=True)
        deep.chmod(0o100)
        self.addCleanup(deep.chmod, 0o755)
        ancestor.chmod(0o111)
        self.addCleanup(ancestor.chmod, 0o755)
        with self.assertRaises(FileNotFoundError):
            os.stat(ancestor / ".harness-status-probe")  # The ancestor can be searched...
        with self.assertRaises(PermissionError):
            os.listdir(ancestor)  # ...but not read.
        try:
            os.listdir(deep)
        except OSError as error:
            self.assertNotIsInstance(error, FileNotFoundError)
            reason = error.strerror
        else:
            self.fail("the OS did not deny reading the deeper directory")
        result = self.status()
        self.assertEqual((result.returncode, result.stderr), (0, ""), result.stdout)
        rows = [row for row in result.stdout.splitlines() if row.startswith("plugin: ")]
        self.assertEqual(rows, [f"plugin: plugins/cache/harness/harness/ not readable: {reason}",
                                "plugin: harness not enabled, cached unknown",
                                "plugin: shared-roles not enabled, not cached"])

    def test_prompt_hooks_do_not_count_and_object_hooks_are_invalid(self):
        for host in ("claude", "codex"):
            with self.subTest(host=host):
                template = json.loads((ROOT / ("examples/settings.example.json" if host == "claude" else "codex/hooks.json")).read_text())
                total = sum(len(group["hooks"]) for groups in template["hooks"].values() for group in groups)
                first = next(iter(template["hooks"].values()))[0]["hooks"]
                first.extend(({"type": "prompt", "prompt": "hello"}, {"type": "agent"}))
                settings = self.settings_path(host)
                settings.parent.mkdir(parents=True, exist_ok=True)
                settings.write_text(json.dumps(template))
                line(self.lines(self.status(host), host)[6], f"hooks: {total} of {total} registered")
                settings.write_text('{"hooks":{"PreToolUse":[{"hooks":{}}]}}')
                line(self.lines(self.status(host), host)[6], f"hooks: unknown ({settings.name} is not valid JSON)")

    def status(self, host="claude", tree=ROOT, env=None):
        environment = {**os.environ, "HOME": str(self.home),
                       "CLAUDE_HOME": str(self.host_home("claude")),
                       "CODEX_HOME": str(self.host_home("codex")),
                       "XDG_CONFIG_HOME": str(self.home / ".config"),
                       "XDG_STATE_HOME": str(self.home / ".local/state"), **(env or {})}
        return subprocess.run(["bash", str(Path(tree) / "install.sh"), "--status", "--host", host],
                              env=environment, stdin=subprocess.DEVNULL, capture_output=True,
                              text=True, timeout=20)

    def lines(self, result, host="claude"):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        lines = status_rows(result)
        line(lines[0], f"Harness status ({host}): {self.host_home(host)}")
        self.assertEqual(len(lines), 9, lines)
        return lines

    def test_fresh_is_read_only(self):
        before = self.tree_state()
        lines = self.lines(self.status())
        line(lines[1], "version: not installed, checkout " + (ROOT / "VERSION").read_text().strip())
        line(lines[2], "files: none installed")
        line(lines[3], "leftovers: none")
        line(lines[4], "sibling: Router not installed")
        line(lines[5], "skew: none")
        template = json.loads((ROOT / "examples/settings.example.json").read_text())
        total = sum(len(group["hooks"]) for groups in template["hooks"].values() for group in groups)
        line(lines[6], f"hooks: 0 of {total} registered")
        line(lines[7], "last hook write: none yet")
        line(lines[8], budget_line("tree", 0, ceilings_of(ROOT / "scripts/budget.py")))
        self.assertEqual(self.tree_state(), before)
        self.assertFalse((self.home / ".local").exists())

    def test_current_read_only(self):
        for host in ("claude", "codex"):
            with self.subTest(host=host):
                self.run_install(host)
                before = self.tree_state()
                lines = self.lines(self.status(host), host)
                version = (ROOT / "VERSION").read_text().strip()
                line(lines[1], f"version: installed {version}, checkout {version} (current)")
                line(lines[2], f"files: {len(self.manifest(host)['files'])} current")
                line(lines[3], "leftovers: none")
                line(lines[4], "sibling: Router not installed")
                line(lines[5], "skew: none")
                self.assertEqual(self.tree_state(), before)

    def test_stale_and_edited_read_only(self):
        self.run_install("claude")
        files = self.manifest("claude")["files"]
        names = list(files)
        stale, edited, missing = names[:3]
        checkout = self.copy_checkout()
        checkout_file = checkout / stale.replace("harness/", "", 1)
        checkout_file.write_bytes(b"new checkout bytes")
        (self.host_home("claude") / edited).write_bytes(b"user edit")
        (self.host_home("claude") / missing).unlink()
        before = self.tree_state()
        result = self.status(tree=checkout)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        rows = status_rows(result)
        line(rows[2], f"files: 1 stale, 1 edited, 1 missing, {len(files)-3} current")
        line(rows[3], f"  stale: {stale}")
        line(rows[4], f"  edited: {edited}")
        line(rows[5], f"  missing: {missing}")
        self.assertEqual(self.tree_state(), before)

    def copy_checkout(self):
        checkout = self.root / "checkout"
        checkout.mkdir()
        for folder in ("hooks", "statusline", "agents", "skills", "codex", "examples", "scripts"):
            shutil.copytree(ROOT / folder, checkout / folder, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        for name in ("install.sh", "VERSION"):
            shutil.copy2(ROOT / name, checkout / name)
        return checkout

    def test_bad_settings_and_manifest_are_reported(self):
        top = self.host_home("claude")
        top.mkdir()
        settings = self.settings_path("claude")
        settings.write_text("{")
        lines = self.lines(self.status())
        line(lines[6], f"hooks: unknown ({settings.name} is not valid JSON)")
        settings.write_text("[" * 100000)
        lines = self.lines(self.status())
        line(lines[6], f"hooks: unknown ({settings.name} is nested too deeply)")
        manifest = top / "harness/install-manifest.json"
        manifest.parent.mkdir()
        manifest.write_text("{")
        lines = self.lines(self.status())
        line(lines[1], "version: installed unknown, checkout " + (ROOT / "VERSION").read_text().strip())
        line(lines[2], "files: unknown (install manifest unreadable)")
        manifest.write_text(json.dumps({"version": 1, "files": {"../outside": "0" * 64}, "hooks": {}}))
        lines = self.lines(self.status())
        line(lines[2], "files: unknown (install manifest unreadable)")

    @NOT_ROOT
    def test_unreadable_paths_and_control_characters(self):
        self.run_install("claude")
        manifest_path = self.host_home("claude") / "harness/install-manifest.json"
        data = self.manifest("claude")
        data["package"] = "bad\nversion\x01"
        manifest_path.write_text(json.dumps(data))
        rows = status_rows(self.status())
        line(rows[1], "version: installed bad?version?, checkout " + (ROOT / "VERSION").read_text().strip() + " (run install.sh to update)")
        manifest_path.chmod(0)
        rows = status_rows(self.status())
        line(rows[1], "version: installed unknown, checkout " + (ROOT / "VERSION").read_text().strip())
        manifest_path.chmod(0o600)
        harness_dir = self.host_home("claude") / "harness"
        harness_dir.chmod(0)
        result = self.status()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        harness_dir.chmod(0o755)
        state = self.home / ".local/state/claude-harness"
        state.mkdir(parents=True)
        state.chmod(0)
        rows = status_rows(self.status())
        line(rows[-2], f"last hook write: unknown (.local/state/claude-harness/ not readable: {os.strerror(errno.EACCES)})")
        state.chmod(0o755)

    def test_hook_write_and_sibling(self):
        self.run_install("claude")
        state = self.home / ".local/state/claude-harness"
        (state / "checkpoints").mkdir(parents=True)
        file = state / "checkpoints" / "latest.json"
        file.write_text("{}")
        os.utime(file, (1000000000, 1000000000))
        rows = status_rows(self.status())
        line(rows[-2], "last hook write: 2001-09-09T01:46:40Z (checkpoints/latest.json)")
        router = self.host_home("claude") / "router/install-manifest.json"
        router.parent.mkdir()
        router.write_text("{")
        rows = status_rows(self.status())
        line(rows[4], "sibling: Router unknown")
        line(rows[5], "skew: none")

    def test_router_02_skew(self):
        router = old_tree("ROUTER_V02")
        self.run_tree(router, "claude")
        self.run_install("claude")
        rows = status_rows(self.status())
        self.assertEqual(sum(re.fullmatch(re.escape("sibling: Router 0.2.0 or earlier installed"), row) is not None for row in rows), 1)
        self.assertEqual(sum(re.fullmatch(re.escape("skew: shared roles come from Router 0.2.0 or earlier; upgrade it for the 0.3 roles"), row) is not None for row in rows), 1)

    def test_version_and_hooks(self):
        self.run_install("claude")
        rows = self.lines(self.status())
        template = json.loads((ROOT / "examples/settings.example.json").read_text())
        total = sum(len(group["hooks"]) for groups in template["hooks"].values() for group in groups)
        line(rows[6], f"hooks: {total} of {total} registered")
        manifest_path = self.host_home("claude") / "harness/install-manifest.json"
        manifest = self.manifest("claude")
        manifest["package"] = "0.0.0"
        manifest_path.write_text(json.dumps(manifest))
        rows = self.lines(self.status())
        line(rows[1], "version: installed 0.0.0, checkout " + (ROOT / "VERSION").read_text().strip() + " (run install.sh to update)")
        settings = self.settings_path("claude")
        settings.chmod(0)
        rows = self.lines(self.status())
        line(rows[6], "hooks: unknown (settings.json is not readable)")
        settings.chmod(0o600)

    def test_budget_success_and_failures(self):
        checkout = self.copy_checkout()
        script = checkout / "scripts/budget.py"
        direct = subprocess.run([sys.executable, str(script), "--home", str(self.host_home("claude")),
                                 "--host", "claude", "--json"], stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(direct.returncode, 0, direct.stderr)
        row = json.loads(direct.stdout)["trees"][0]
        ceilings = ceilings_of(script)
        line(status_rows(self.status(tree=checkout))[-1], budget_line(row["label"], row["tokens"], ceilings))
        script.write_text(script.read_text().replace('"together": 900}', '"together": 900, "tree": 123}'))
        self.assertEqual(ceilings_of(script)["tree"], 123)
        line(status_rows(self.status(tree=checkout))[-1], "budget: tree 0 tokens always-on (estimate, ceiling 123)")
        script.write_text(script.read_text().replace('"harness": 600', '"harness": 601'))
        line(status_rows(self.status(tree=checkout))[-1], "budget: tree 0 tokens always-on (estimate, ceiling 123)")
        script.unlink()
        line(status_rows(self.status(tree=checkout))[-1], "budget: unavailable (scripts/budget.py is missing)")
        for body in ("import sys; sys.exit(3)", "import sys; sys.exit('msg')",
                     "import os; print('partial', flush=True); os._exit(3)",
                     "import time; time.sleep(1)", "print('not json')"):
            script.write_text('CEILINGS = {"router": 650, "harness": 600, "together": 900}\n' + body + "\n")
            result = self.status(tree=checkout, env={"HARNESS_STATUS_BUDGET_TIMEOUT": "0.1"})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
            line(status_rows(result)[-1], "budget: unavailable (scripts/budget.py failed)")

    def test_old_leftovers(self):
        tree = old_tree("HARNESS_V02")
        self.run_tree(tree, "claude")
        top = self.host_home("claude")
        manifest = self.manifest("claude")
        recorded = EXTRAS["claude"][0]
        (top / recorded).write_bytes(b"recorded old file")
        manifest["files"][recorded] = sha(b"recorded old file")
        (top / "harness/install-manifest.json").write_text(json.dumps(manifest))
        extra = next(name for name in EXTRAS["claude"] if name not in manifest["files"])
        (top / extra).parent.mkdir(parents=True, exist_ok=True)
        (top / extra).write_text("old")
        rows = status_rows(self.status())
        line(rows[1], "version: installed 0.2.0 or earlier, checkout " + (ROOT / "VERSION").read_text().strip())
        block = [row for row in rows if row.startswith("  ") and "(" in row and row.endswith(")") and
                 ("remove it" in row)]
        self.assertEqual(len(block), 2, rows)
        self.assertEqual(sum(re.fullmatch(r"leftovers: 2", row) is not None for row in rows), 1, rows)
        self.assertTrue(any(re.fullmatch(re.escape(f"  {recorded} (installed by an earlier release; run install.sh to remove it)"), row) for row in rows))
        self.assertTrue(any(re.fullmatch(re.escape(f"  {extra} (not tracked; remove it by hand)"), row) for row in rows))

    def copy_checkout_files(self):
        names = set()
        for folder, destination in (("hooks", "harness/hooks"), ("statusline", "harness/statusline"),
                                    ("agents", "agents"), ("skills", "skills")):
            names.update(str(Path(destination) / path.relative_to(ROOT / folder))
                         for path in (ROOT / folder).rglob("*") if path.is_file() and path.name != "SHARED.sha256")
        return names

    def test_conflicting_flags(self):
        for flag in ("--dry-run", "--uninstall", "--purge", "--judge-permissions"):
            result = subprocess.run(["bash", str(ROOT / "install.sh"), "--status", "--host", "claude", flag],
                                    stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 2)

    def test_uninstall_restores_original_settings_mode(self):
        for host in ("claude", "codex"):
            for mode in (0o644, 0o600):
                with self.subTest(host=host, mode=mode):
                    top = self.host_home(host)
                    if top.exists():
                        shutil.rmtree(top)
                    top.mkdir()
                    path = self.settings_path(host)
                    original = b'{\n  "user": "value"\n}\n'
                    path.write_bytes(original)
                    path.chmod(mode)
                    self.run_install(host)
                    self.assertEqual(self.manifest(host)["settings_original_mode"], mode)
                    self.run_install(host)
                    self.run_install(host, "--uninstall")
                    self.assertEqual(path.read_bytes(), original)
                    self.assertEqual(stat.S_IMODE(path.stat().st_mode), mode)

    def test_old_and_invalid_settings_mode(self):
        host = "claude"
        path = self.settings_path(host)
        path.parent.mkdir()
        path.write_bytes(b"{}")
        path.chmod(0o644)
        self.run_install(host)
        manifest_path = self.host_home(host) / "harness/install-manifest.json"
        manifest = self.manifest(host)
        manifest.pop("settings_original_mode")
        manifest_path.write_text(json.dumps(manifest))
        self.run_install(host, "--uninstall")
        line("mode: " + oct(stat.S_IMODE(path.stat().st_mode)), "mode: 0o644")
        self.run_install(host)
        manifest = self.manifest(host)
        for bad in ("644", 0o1000):
            manifest["settings_original_mode"] = bad
            manifest_path.write_text(json.dumps(manifest))
            result = self.run_install(host, "--uninstall", code=1)
            line(result.stderr.strip(), f"harness: {manifest_path}: invalid install manifest")

    def test_uninstall_reports_linked_files(self):
        for parent in ("agents", "harness/hooks"):
            with self.subTest(parent=parent):
                top = self.host_home("claude")
                if top.exists():
                    shutil.rmtree(top)
                self.run_install("claude")
                names = [name for name in self.manifest("claude")["files"] if name.startswith(parent + "/")]
                self.assertTrue(names)
                outside = self.root / ("outside-" + parent.replace("/", "-"))
                shutil.copytree(top / parent, outside)
                shutil.rmtree(top / parent)
                (top / parent).symlink_to(outside, target_is_directory=True)
                before = self.tree_state(outside)
                dry = self.run_install("claude", "--uninstall", "--dry-run").stdout.splitlines()
                for name in names:
                    self.assertEqual(sum(re.fullmatch(re.escape(f"Would leave in place under a symbolic link: {name}"), row) is not None for row in dry), 1)
                output = self.run_install("claude", "--uninstall").stdout.splitlines()
                for name in names:
                    self.assertEqual(sum(re.fullmatch(re.escape(f"Left in place under a symbolic link: {name}"), row) is not None for row in output), 1)
                self.assertEqual(self.tree_state(outside), before)

    def test_normal_uninstall_has_no_link_line(self):
        self.run_install("claude")
        rows = self.run_install("claude", "--uninstall").stdout.splitlines()
        self.assertFalse(any(re.fullmatch(r"(?:Would leave|Left) in place under a symbolic link: .*", row) for row in rows))


    HOOKS_BAD = "hooks: unknown (settings.json is not valid JSON)"

    def test_any_json_shape_in_settings(self):
        shapes = ["[1]", "[]", "null", '"x"', "7", '{"hooks": []}', '{"hooks": null}',
                  '{"hooks": {"PreToolUse": {}}}', '{"hooks": {"PreToolUse": [1]}}',
                  '{"hooks": {"PreToolUse": [{}]}}', '{"hooks": {"PreToolUse": [{"hooks": "x"}]}}',
                  '{"hooks": {"PreToolUse": [{"hooks": [1]}]}}',
                  '{"hooks": {"PreToolUse": [{"hooks": [{"command": 7}]}]}}',
                  '{"hooks": {"PreToolUse": [{"hooks": [{"command": null}]}]}}']
        settings = self.settings_path("claude")
        settings.parent.mkdir()
        for text in shapes:
            with self.subTest(shape=text):
                settings.write_text(text)
                result = self.status()
                self.assertNotIn("Traceback", result.stderr)
                lines = self.lines(result)
                line(lines[6], self.HOOKS_BAD)
                line(lines[7], "last hook write: none yet")

    def test_clean_caps_long_values(self):
        self.run_install("claude")
        path = self.host_home("claude") / "harness/install-manifest.json"
        data = self.manifest("claude")
        data["package"] = "a" * 300
        path.write_text(json.dumps(data))
        rows = status_rows(self.status())
        line(rows[1], "version: installed " + "a" * 200 + ", checkout " + (ROOT / "VERSION").read_text().strip() +
             " (run install.sh to update)")

    def test_empty_state_directory_is_none_yet(self):
        (self.home / ".local/state/claude-harness/checkpoints").mkdir(parents=True)
        rows = self.lines(self.status())
        line(rows[7], "last hook write: none yet")

    def test_file_in_place_of_state_cache_names_the_real_error(self):
        cache = self.home / ".local/state/claude-harness"
        cache.parent.mkdir(parents=True)
        cache.write_text("not a directory")
        line(self.lines(self.status())[7],
             f"last hook write: unknown (.local/state/claude-harness not readable: {os.strerror(errno.ENOTDIR)})")

    def test_state_directory_fallbacks(self):
        cache = self.home / ".local/state/claude-harness"
        cache.parent.mkdir(parents=True)
        if os.geteuid() == 0:
            self.skipTest("mode 000 directories stay readable for root")
        cache.mkdir()
        cache.chmod(0)
        self.addCleanup(cache.chmod, 0o755)
        line(self.lines(self.status())[7], f"last hook write: unknown (.local/state/claude-harness/ not readable: {os.strerror(errno.EACCES)})")
        cache.chmod(0o755)
        outer = self.home / "xdg-parent"
        (outer / "state").mkdir(parents=True)
        outer.chmod(0)
        self.addCleanup(outer.chmod, 0o755)
        line(self.lines(self.status(env={"XDG_STATE_HOME": str(outer / "state")}))[7],
             f"last hook write: unknown (xdg-parent/ not readable: {os.strerror(errno.EACCES)})")

    @NOT_ROOT
    def test_state_cache_lacking_read_or_search_reports_the_os_error(self):
        state = self.home / "xdg-parent" / "state"
        cache = state / "claude-harness"
        cache.mkdir(parents=True)
        for directory, mode, operation in ((cache, 0o100, os.listdir), (cache, 0o400, None), (state, 0o400, None)):
            with self.subTest(directory=directory, mode=oct(mode)):
                directory.chmod(mode)
                try:
                    try:
                        (operation or (lambda path: os.stat(path / ".harness-status-probe")))(directory)
                    except OSError as error:
                        self.assertNotIsInstance(error, FileNotFoundError)
                        expected = error.strerror
                    else:
                        self.fail("the OS did not deny the requested operation")
                    target = str(directory.relative_to(self.home)) + "/"
                    line(self.lines(self.status(env={"XDG_STATE_HOME": str(state)}))[7],
                         f"last hook write: unknown ({target} not readable: {expected})")
                finally:
                    directory.chmod(0o755)

    @NOT_ROOT
    def test_ancestor_with_search_but_no_read_still_prints_the_timestamp(self):
        ancestor = self.home / "ancestor"
        cache = ancestor / "state" / "claude-harness"
        cache.mkdir(parents=True)
        (cache / "f.json").write_text("{}")
        os.utime(cache / "f.json", (1000000000, 1000000000))
        ancestor.chmod(0o111)
        self.addCleanup(ancestor.chmod, 0o755)
        line(self.lines(self.status(env={"XDG_STATE_HOME": str(ancestor / "state")}))[7],
             "last hook write: 2001-09-09T01:46:40Z (f.json)")

    def test_state_errors_come_from_the_operation_not_from_mode_bits(self):
        # The OS call is made to fail with EIO while the mode would give EACCES (or nothing): the line must carry EIO.
        eio = os.strerror(errno.EIO)
        state = self.home / "xdg-parent" / "state"
        cache = state / "claude-harness"
        cache.mkdir(parents=True)
        cases = (("cache read", cache, 0o300, "listdir", cache, "xdg-parent/state/claude-harness/"),
                 ("cache search", cache, 0o600, "stat", cache / ".harness-status-probe", "xdg-parent/state/claude-harness/"),
                 ("parent search", state, 0o600, "stat", state / ".harness-status-probe", "xdg-parent/state/"))
        for label, directory, mode, name, target, shown in cases:
            with self.subTest(label):
                directory.chmod(mode)
                self.addCleanup(directory.chmod, 0o755)
                fed = ("import errno, os\nreal = os.%s\ntarget = %r\n"
                       "def fed(path, *args, **kwargs):\n"
                       "    if os.fspath(path) == target:\n"
                       "        raise OSError(errno.EIO, os.strerror(errno.EIO), target)\n"
                       "    return real(path, *args, **kwargs)\n"
                       "os.%s = fed\n") % (name, str(target), name)
                result = self.status_in_process(fed, env={"XDG_STATE_HOME": str(state)})
                line(self.lines(result)[7], f"last hook write: unknown ({shown} not readable: {eio})")
                directory.chmod(0o755)

    @NOT_ROOT
    def test_unreadable_subdirectory_is_named(self):
        cache = self.home / ".local/state/claude-harness"
        (cache / "checkpoints").mkdir(parents=True)
        (cache / "checkpoints/old.json").write_text("{}")
        os.utime(cache / "checkpoints/old.json", (1000000000, 1000000000))
        (cache / "pressure").mkdir()
        (cache / "pressure").chmod(0o400)
        self.addCleanup((cache / "pressure").chmod, 0o755)
        rows = self.lines(self.status())
        line(rows[7], f"last hook write: unknown (pressure/ not readable: {os.strerror(errno.EACCES)})")

    @NOT_ROOT
    def test_long_unreadable_state_path_is_cleaned(self):
        cache = self.home / ".local/state/claude-harness"
        directory = cache / ("p" * 205)
        directory.mkdir(parents=True)
        directory.chmod(0o400)
        self.addCleanup(directory.chmod, 0o755)
        rows = self.lines(self.status())
        line(rows[7], f"last hook write: unknown ({'p' * 200}/ not readable: {os.strerror(errno.EACCES)})")

    def test_long_state_file_path_is_cleaned(self):
        cache = self.home / ".local/state/claude-harness"
        relative = Path("checkpoints") / ("p" * 180) / ("f" * 30 + ".json")
        path = cache / relative
        path.parent.mkdir(parents=True)
        path.write_text("{}")
        os.utime(path, (1000000000, 1000000000))
        rows = self.lines(self.status())
        line(rows[7], f"last hook write: 2001-09-09T01:46:40Z ({str(relative)[:200]})")

    @NOT_ROOT
    def test_unreadable_harness_directory(self):
        self.run_install("claude")
        directory = self.host_home("claude") / "harness"
        directory.chmod(0)
        self.addCleanup(directory.chmod, 0o755)
        rows = self.lines(self.status())
        line(rows[1], "version: installed unknown, checkout " + (ROOT / "VERSION").read_text().strip())
        line(rows[2], f"files: unknown (harness/ not readable: {os.strerror(errno.EACCES)})")

    @NOT_ROOT
    def test_unreadable_agents_directory_is_named(self):
        self.run_install("claude")
        directory = self.host_home("claude") / "agents"
        directory.chmod(0)
        self.addCleanup(directory.chmod, 0o755)
        rows = self.lines(self.status())
        line(rows[1], "version: installed " + (ROOT / "VERSION").read_text().strip() + ", checkout " +
             (ROOT / "VERSION").read_text().strip() + " (current)")
        line(rows[2], f"files: unknown (agents/ not readable: {os.strerror(errno.EACCES)})")

    def status_in_process(self, prelude, host="claude", env=None):
        """Run the program inside install.sh in one python3 process, after prelude has patched it."""
        runner = ("import sys\nfrom pathlib import Path\ninstall, host = sys.argv[1:]\n"
                  "program = Path(install).read_text().split(\"<<'PY'\\n\", 1)[1].rsplit(\"\\nPY\\n\", 1)[0]\n"
                  "sys.argv = ['-', str(Path(install).parent), '--status', '--host', host]\n"
                  + prelude + "\nexec(compile(program, install, 'exec'), {'__name__': '__main__'})\n")
        environment = {**os.environ, "HOME": str(self.home), "CLAUDE_HOME": str(self.host_home("claude")),
                       "CODEX_HOME": str(self.host_home("codex")), "XDG_CONFIG_HOME": str(self.home / ".config"),
                       "XDG_STATE_HOME": str(self.home / ".local/state"), **(env or {})}
        return subprocess.run([sys.executable, "-c", runner, str(ROOT / "install.sh"), host], env=environment,
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=20)

    def test_files_loop_reports_the_os_error_on_any_python(self):
        # Fed directly: os.lstat raises EACCES below agents/, as a mode-000 agents/ makes the OS do.
        # This runs as root too, and does not depend on how this Python's pathlib treats errors.
        self.run_install("claude")
        expected = f"files: unknown (agents/ not readable: {os.strerror(errno.EACCES)})"
        fed = ("import errno, os\nagents = os.path.join(os.environ['CLAUDE_HOME'], 'agents')\nreal_lstat = os.lstat\n"
               "def lstat(path, *args, **kwargs):\n"
               "    if os.fspath(path).startswith(agents + os.sep):\n"
               "        raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), agents)\n"
               "    return real_lstat(path, *args, **kwargs)\n"
               "os.lstat = lstat\n")
        with self.subTest("os.lstat fed EACCES"):
            line(self.lines(self.status_in_process(fed))[2], expected)
        if os.geteuid() == 0:
            self.skipTest("mode 000 directories stay readable for root")
        # A real mode-000 agents/ under the pathlib of Python 3.12 and later, whose exists() and
        # is_symlink() may return False on an error instead of raising it.
        swallow = ("import pathlib\nfor name in ('exists', 'is_symlink', 'is_file', 'is_dir'):\n"
                   "    def quiet(self, *args, _real=getattr(pathlib.Path, name), **kwargs):\n"
                   "        try:\n            return _real(self, *args, **kwargs)\n"
                   "        except OSError:\n            return False\n"
                   "    setattr(pathlib.Path, name, quiet)\n")
        directory = self.host_home("claude") / "agents"
        directory.chmod(0)
        self.addCleanup(directory.chmod, 0o755)
        with self.subTest("mode 000 agents/, pathlib that returns False on errors"):
            line(self.lines(self.status_in_process(swallow))[2], expected)


if __name__ == "__main__":
    unittest.main()
