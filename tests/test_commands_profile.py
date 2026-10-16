#!/usr/bin/env python3
"""Harness commands-only profile: restricted entry, isolated state and scoped lifecycle.

Every test uses a disposable HOME, XDG state/config and TMPDIR, closed stdin and a timeout.
Nothing here starts a host, connects to a network or calls a model.
"""
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import sysconfig
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _platform import (BASH, IS_WINDOWS, canonical_temp_root, home_env, posix, python_shim,  # noqa: E402
                       run, skip_unless_symlinks)

ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / "bin/harness-commands"
INSTALL = ROOT / "install.sh"
ALLOWED = ("operations", "observe", "usage", "evaluate", "ssh", "policy",
           "status", "doctor", "help", "version")
REFUSED = ("on", "off", "auto", "ladder", "config", "routing", "install", "hooks",
           "statusline", "checkpoint", "receipt")
PROJECT_MODULES = ("compat", "operations", "observation", "evaluation", "usage", "storage",
                   "journal", "operations_cli", "observation_cli", "evaluation_cli", "usage_cli",
                   "operations_policy", "ssh_operations", "ssh_operations_cli",
                   "operations_policy_cli", "shell_syntax")
POLICY = json.loads((ROOT / "tests/fixtures/ssh-operations.json").read_text(encoding="utf-8"))["policy"]
DECLARATION = {"host": "claude", "expected_event_ids": ["e1"], "outcome": "returned"}
OWNED_PREFIXES = (".claude/harness-commands", ".codex/harness-commands",
                  ".claude/.harness-commands", ".codex/.harness-commands",
                  ".local", ".harness-commands-", "state/harness-commands")


def clean_env(home, base, **extra):
    """Disposable HOME/XDG/TMPDIR; host-home and state overrides from the caller never leak in."""
    env = {key: value for key, value in os.environ.items()
           if key not in ("CLAUDE_HOME", "CODEX_HOME", "HARNESS_COMMANDS_STATE", "PYTHONPATH",
                          "XDG_STATE_HOME", "XDG_CONFIG_HOME", "COMMANDS_INSTALL_CRASH_AT")}
    env.update(home_env(home))
    env.update({"XDG_STATE_HOME": str(home / "state"), "XDG_CONFIG_HOME": str(home / "config"),
                "TMPDIR": str(base), "PYTHONDONTWRITEBYTECODE": "1"})
    env.update(extra)
    return env


def snapshot(top):
    """Relative path -> kind, mode and bytes or link text, without following links."""
    found = {}
    for folder, dirs, names in os.walk(top):
        for name in dirs + names:
            path = Path(folder) / name
            info = path.lstat()
            relative = path.relative_to(top).as_posix()
            if stat.S_ISLNK(info.st_mode):
                found[relative] = ("link", os.readlink(path))
            elif stat.S_ISDIR(info.st_mode):
                found[relative] = ("dir", stat.S_IMODE(info.st_mode))
            else:
                found[relative] = ("file", stat.S_IMODE(info.st_mode), path.read_bytes())
    return found


def runtime_module():
    spec = importlib.util.spec_from_file_location("harness_command_runtime_test",
                                                  ROOT / "hooks/checkpoint/command_runtime.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def copy_source(destination):
    shutil.copytree(ROOT, destination, ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"))
    return destination


class EntryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="harness-commands-entry-")
        self.addCleanup(temp.cleanup)
        self.base = canonical_temp_root(temp)
        self.home = self.base / "home"
        self.home.mkdir()
        self.env = clean_env(self.home, self.base, HARNESS_COMMANDS_STATE=str(self.base / "explicit"))

    def entry(self, *args, entry=ENTRY, env=None, input=None):
        return run([sys.executable, "-B", str(entry), *args], timeout=20, env=env or self.env,
                   cwd=self.base, input=input, capture_output=True, text=True,
                   **({} if input is not None else {"stdin": subprocess.DEVNULL}))

    def test_refused_controls_load_nothing_and_create_no_state(self):
        for command in REFUSED:
            with self.subTest(command=command):
                result = self.entry(command)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("unavailable in commands-only profile", result.stderr)
                self.assertEqual(result.stdout, "")
        self.assertEqual(list(self.home.iterdir()), [])
        self.assertFalse((self.base / "explicit").exists())
        probe = ("import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); "
                 "import command_runtime; "
                 "codes = [command_runtime.main([name], root=Path(sys.argv[2])) for name in sys.argv[4:]]; "
                 "print(sorted(set(codes)), sorted(set(sys.argv[3].split(',')) & set(sys.modules)))")
        result = run([sys.executable, "-B", "-c", probe, str(ROOT / "hooks/checkpoint"), str(ROOT),
                      ",".join(PROJECT_MODULES), *REFUSED], timeout=20, env=self.env,
                     stdin=subprocess.DEVNULL, capture_output=True, text=True)
        self.assertEqual(result.stdout.strip(), "[2] []", result.stderr)
        self.assertEqual(list(self.home.iterdir()), [])

    def test_status_is_truthful_and_isolated_from_full_profile_state(self):
        env = clean_env(self.home, self.base)
        for name in ("status", "doctor"):
            with self.subTest(command=name):
                result = self.entry(name, env=env)
                self.assertEqual(result.returncode, 0, result.stderr)
                data = json.loads(result.stdout)
                self.assertEqual((data["profile"], data["product"], data["host"]),
                                 ("commands-only", "harness", "claude"))
                self.assertIs(data["registered_hooks"], False)
                self.assertIs(data["policy_interception"], False)
                self.assertEqual(data["host_availability"], "unknown")
                self.assertEqual(data["mcp_qualification"], "unknown")
                self.assertEqual(data["state"], str(self.home / "state/harness-commands/claude"))
                self.assertNotIn("claude-harness", result.stdout)
        explicit = json.loads(self.entry("status").stdout)
        self.assertEqual(explicit["state"], str(self.base / "explicit"))
        relative = self.entry("status", env=dict(env, HARNESS_COMMANDS_STATE="relative/state"))
        self.assertEqual(relative.returncode, 1)
        self.assertEqual(list(self.home.iterdir()), [])
        self.assertFalse((self.base / "explicit").exists())
        version = self.entry("version", env=env)
        self.assertEqual(version.stdout.strip(), (ROOT / "VERSION").read_text().strip())
        helped = self.entry("help", env=env)
        self.assertEqual(helped.returncode, 0, helped.stderr)
        for name in ALLOWED:
            self.assertIn(name, helped.stdout)

    def test_missing_runtime_or_dependency_never_loads_ambient_replacement(self):
        for missing, ambient_name, args in (
                ("hooks/checkpoint/command_runtime.py", "command_runtime", ("status",)),
                ("hooks/guard/ssh_operations.py", "ssh_operations", ("ssh", "check", "--file", "-")),
                ("hooks/checkpoint/usage.py", "usage", ("usage", "report"))):
            with self.subTest(missing=missing):
                source = self.base / ("source-" + ambient_name)
                for folder in ("bin", "hooks"):
                    shutil.copytree(ROOT / folder, source / folder,
                                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
                shutil.copy2(ROOT / "VERSION", source / "VERSION")
                (source / missing).unlink()
                ambient = self.base / ("ambient-" + ambient_name)
                ambient.mkdir()
                marker = self.base / ("ran-" + ambient_name)
                (ambient / (ambient_name + ".py")).write_text(
                    "from pathlib import Path\nPath(" + repr(str(marker)) + ").write_text('yes')\n"
                    "raise SystemExit(42)\n")
                env = dict(self.env, PYTHONPATH=str(ambient))
                result = self.entry(*args, entry=source / "bin/harness-commands", env=env, input="{}")
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn("runtime unavailable", result.stderr)
                self.assertFalse(marker.exists())
                refused = self.entry("auto", entry=source / "bin/harness-commands", env=env)
                self.assertEqual(refused.returncode, 2, refused.stderr)
        self.assertEqual(list(self.home.iterdir()), [])
        self.assertFalse((self.base / "explicit").exists())

    def test_runtime_inventory_is_the_complete_local_import_closure(self):
        inventory = runtime_module().RUNTIME_FILES
        launcher = ast.parse(ENTRY.read_text(encoding="utf-8"))
        declared = next(node.value for node in launcher.body if isinstance(node, ast.Assign)
                        and getattr(node.targets[0], "id", None) == "RUNTIME_FILES")
        self.assertEqual(tuple(ast.literal_eval(declared)), tuple(inventory))
        local = {path.stem: path.relative_to(ROOT).as_posix()
                 for folder in ("hooks/checkpoint", "hooks/guard") for path in (ROOT / folder).glob("*.py")}
        stdlib = {os.path.normcase(os.path.realpath(sysconfig.get_paths()[key]))
                  for key in ("stdlib", "platstdlib")}
        # Some Windows layouts serve the stdlib from pythonXY.zip beside the Lib folder.
        archive = "python%d%d.zip" % sys.version_info[:2]
        stdlib |= {os.path.normcase(os.path.realpath(os.path.join(prefix, archive)))
                   for prefix in {sys.base_prefix, sys.base_exec_prefix}}
        platform_only = {"fcntl", "msvcrt", "pwd", "winreg", "_winapi", "termios"}
        for relative in inventory:
            tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
            names = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names.update(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    names.add(node.module.split(".")[0])
                elif (isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant)
                      and isinstance(node.args[0].value, str)
                      and getattr(node.func, "id", getattr(node.func, "attr", None))
                      in ("adjacent", "import_module", "__import__", "with_name")):
                    names.add(node.args[0].value.rsplit(".py", 1)[0])  # loaders by explicit name
            for name in sorted(names):
                with self.subTest(module=relative, imports=name):
                    if name in local:
                        self.assertIn(local[name], inventory)
                        continue
                    if name in platform_only or name in sys.builtin_module_names:
                        continue
                    spec = importlib.util.find_spec(name)
                    self.assertIsNotNone(spec, name)
                    origin = os.path.normcase(os.path.realpath(spec.origin or ""))
                    self.assertTrue(spec.origin in ("built-in", "frozen")
                                    or any(origin.startswith(base + os.sep) for base in stdlib), name)
        self.assertNotIn("hooks/checkpoint/storage.py", inventory)
        self.assertNotIn("hooks/checkpoint/usage_cli.py", inventory)


class InstallTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="harness-commands-install-")
        self.addCleanup(temp.cleanup)
        self.base = canonical_temp_root(temp)
        self.home = self.base / "home"
        self.home.mkdir()
        shims = self.base / "python-shim"
        shims.mkdir()
        python_shim(shims)
        self.install_path = INSTALL
        self.env = clean_env(self.home, self.base,
                             PATH=str(shims) + os.pathsep + os.environ.get("PATH", os.defpath))

    def install(self, *args, ok=True, profile=True):
        prefix = ["--profile", "commands"] if profile else []
        result = run([BASH, posix(self.install_path), *prefix, *args], timeout=90, env=self.env,
                     cwd=self.base, stdin=subprocess.DEVNULL, capture_output=True, text=True)
        if ok:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout)
        return result

    def apply(self, host):
        plan = self.install("--host", host, "--dry-run")
        saved = self.base / (host + ".json")
        saved.write_text(plan.stdout)
        return self.install("--host", host, "--plan", str(saved))

    def command(self, host, *args, env=None, input=None):
        launcher = self.home / ".local/bin" / (host + "-harness")
        entry = [sys.executable, "-B", str(launcher)] if IS_WINDOWS else [str(launcher)]
        return run([*entry, *args], timeout=20, env={**self.env, **(env or {})}, cwd=self.base,
                   input=input, capture_output=True, text=True,
                   **({} if input is not None else {"stdin": subprocess.DEVNULL}))

    def payloads(self):
        return sorted(path.name for path in self.base.iterdir()
                      if path.name.startswith("harness-commands-payload-"))

    def test_dry_run_is_write_free_and_both_or_no_action_is_refused(self):
        for args in (("--host", "both", "--dry-run"), ("--host", "claude"),
                     ("--host", "claude", "--dry-run", "--status"), ("--dry-run",),
                     ("--host", "claude", "--dry-run", "--statusline")):
            with self.subTest(args=args):
                self.install(*args, ok=False)
                self.assertEqual(list(self.home.iterdir()), [])
        for spelling in (("--profile", "commands"), ("--profile=commands",)):
            with self.subTest(spelling=spelling):
                planned = self.install(*spelling, "--host", "claude", "--dry-run", profile=False)
                plan = json.loads(planned.stdout)
                self.assertEqual((plan["product"], plan["host"]), ("harness", "claude"))
                self.assertTrue(plan["source_revision"].startswith("sha256:"))
                self.assertFalse(Path(plan["payload"]).exists())
                self.assertEqual(sorted(plan["payload_inventory"]), sorted(
                    ["bin", "hooks", "hooks/checkpoint", "hooks/guard", "manifest.json",
                     "profile.json"] + list(runtime_module().RUNTIME_FILES)))
                self.assertEqual(list(self.home.iterdir()), [])
                self.assertEqual(self.payloads(), [])

    @unittest.skipIf(IS_WINDOWS, "FIFO input is POSIX only")
    def test_saved_plan_fifo_refuses_before_payload_or_home_writes(self):
        fifo = self.base / "plan.fifo"
        os.mkfifo(fifo)
        self.install("--host", "claude", "--plan", str(fifo), ok=False)
        self.assertEqual(list(self.home.iterdir()), [])
        self.assertEqual(self.payloads(), [])

    def test_symlinked_or_missing_runtime_input_is_refused_before_payload(self):
        source = copy_source(self.base / "source")
        self.install_path = source / "install.sh"
        private = self.base / "private.py"
        private.write_text("SECRET = 'private'\n")
        (source / "hooks/checkpoint/usage.py").unlink()
        try:
            (source / "hooks/checkpoint/usage.py").symlink_to(private)
        except OSError:
            pass  # Without symlink permission the input is simply missing.
        result = self.install("--host", "claude", "--dry-run", ok=False)
        self.assertNotIn("private", result.stdout)
        self.assertEqual(list(self.home.iterdir()), [])
        self.assertEqual(self.payloads(), [])

    @skip_unless_symlinks("the commands launcher is a symbolic link")
    def test_both_hosts_preserve_bespoke_configuration_and_isolate_state(self):
        outside = self.base / "outside-skill"
        outside.mkdir()
        (outside / "SKILL.md").write_text("linked skill\n")
        bespoke = {
            ".claude/settings.json": json.dumps({"model": "custom", "hooks": {"PreToolUse": [
                {"matcher": "Bash", "hooks": [{"type": "command", "command": "python3 ~/.claude/hooks/mine.py"}]}]}}),
            ".claude/CLAUDE.md": "custom instructions\n",
            ".claude/hooks/mine.py": "print('custom monitor')\n",
            ".claude/agents/custom.md": "---\nname: custom\ntools: Read\n---\nCustom role.\n",
            ".claude/skills/custom/SKILL.md": "custom skill\n",
            ".codex/config.toml": "model = \"custom\"\n",
            ".codex/AGENTS.md": "custom codex instructions\n",
            ".codex/hooks.json": json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "true"}]}]}}),
            ".codex/agents/custom.toml": "name = \"custom\"\n",
            ".codex/skills/custom/SKILL.md": "custom codex skill\n",
            ".local/bin/other-tool": "#!/bin/sh\necho other\n",
            "state/claude-harness/usage/keep.txt": "full-profile state\n",
            "config/harness/keep.txt": "owner config\n",
        }
        for relative, text in bespoke.items():
            path = self.home / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        (self.home / ".claude/skills/linked").symlink_to(outside, target_is_directory=True)
        (self.home / ".codex/skills/linked").symlink_to(outside, target_is_directory=True)
        (self.home / ".claude/agents/alias.md").symlink_to(self.home / ".claude/agents/custom.md")
        before = snapshot(self.home)
        for host in ("claude", "codex"):
            self.apply(host)
            status = self.command(host, "status")
            self.assertEqual(status.returncode, 0, status.stderr)
            data = json.loads(status.stdout)
            self.assertEqual((data["profile"], data["product"], data["host"]),
                             ("commands-only", "harness", host))
            self.assertIs(data["registered_hooks"], False)
            self.assertIs(data["policy_interception"], False)
            self.assertEqual(data["host_availability"], "unknown")
            self.assertEqual(data["state"], str(self.home / "state/harness-commands" / host))
            runtime = self.home / ("." + host) / "harness-commands"
            manifest = json.loads((runtime / "manifest.json").read_text())
            self.assertEqual(data["source_revision"], manifest["source_revision"])
            self.assertEqual(data["manifest_sha256"],
                             hashlib.sha256((runtime / "manifest.json").read_bytes()).hexdigest())
            self.assertEqual(self.command(host, "help").returncode, 0)
            for allowed in ALLOWED:
                self.assertNotIn("unavailable in commands-only", self.command(host, allowed).stderr)
            for refused in REFUSED:
                self.assertEqual(self.command(host, refused).returncode, 2)
            self.assertFalse(any(path.is_symlink() for path in runtime.rglob("*")))
            self.assertFalse((runtime / "hooks/checkpoint/storage.py").exists())
            self.assertFalse((runtime / "hooks/checkpoint/dispatch.py").exists())
        after = snapshot(self.home)
        for relative, value in before.items():
            self.assertEqual(after.get(relative), value, relative)
        for relative in set(after) - set(before):
            self.assertTrue(relative.startswith(OWNED_PREFIXES), relative)
        self.assertEqual(sorted((self.home / "state/claude-harness").rglob("*")),
                         [self.home / "state/claude-harness/usage",
                          self.home / "state/claude-harness/usage/keep.txt"])
        for host in ("claude", "codex"):
            self.install("--host", host, "--uninstall")
            self.assertFalse((self.home / ".local/bin" / (host + "-harness")).exists())
        final = snapshot(self.home)
        for relative, value in before.items():
            self.assertEqual(final.get(relative), value, relative)
        for relative in set(final) - set(before):
            self.assertTrue(relative.startswith(("state/harness-commands", ".local")), relative)

    @skip_unless_symlinks("the commands launcher is a symbolic link")
    def test_installed_launchers_survive_removed_source_checkout(self):
        checkout = copy_source(self.base / "source-copy")
        self.install_path = checkout / "install.sh"
        for host in ("claude", "codex"):
            self.apply(host)
        shutil.rmtree(checkout)
        self.assertFalse(checkout.exists())
        from test_evaluation import fixture
        for host in ("claude", "codex"):
            with self.subTest(host=host):
                result = self.command(host, "status")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)["host"], host)
                observed = self.command(host, "observe", "--file", "-", input=json.dumps({
                    "expected": {"schema_version": 1, "task_id": "task", "target_id": "target",
                                 "revision_id": "rev", "requirements": {"title": "a" * 64}},
                    "observed": {"schema_version": 1, "read_status": "denied"}}))
                self.assertEqual(observed.returncode, 0, observed.stderr)
                self.assertEqual(json.loads(observed.stdout)["reported_verdict"], "UNKNOWN")
                preview = self.base / "preview.json"
                preview.write_text(json.dumps(POLICY))
                checked = self.command(host, "ssh", "check", "--file", "-", "--policy", str(preview),
                                       input=json.dumps({"schema_version": 1, "command": "git status",
                                                         "project_root": str(self.base.resolve())}))
                self.assertEqual(checked.returncode, 0, checked.stderr)
                self.assertEqual(json.loads(checked.stdout)["binding"], "unbound-preview")
                evaluated = self.command(host, "evaluate", "--file", "-", input=json.dumps(fixture()))
                self.assertEqual(evaluated.returncode, 0, evaluated.stderr)
                self.assertEqual(json.loads(evaluated.stdout)["decision"], "inconclusive")

    def test_archive_without_git_can_plan_and_apply(self):
        archive = copy_source(self.base / "archive")
        self.assertFalse((archive / ".git").exists())
        self.install_path = archive / "install.sh"
        planned = self.install("--host", "claude", "--dry-run")
        plan = json.loads(planned.stdout)
        self.assertTrue(plan["source_revision"].startswith("sha256:"))
        saved = self.base / "archive-plan.json"
        saved.write_text(planned.stdout)
        self.install("--host", "claude", "--plan", str(saved))
        profile = json.loads((self.home / ".claude/harness-commands/profile.json").read_text())
        self.assertEqual(profile["source_revision"], plan["source_revision"])
        self.assertEqual(profile["host"], "claude")

    @skip_unless_symlinks("the commands launcher is a symbolic link")
    def test_missing_installed_dependency_refuses_every_allowed_command(self):
        self.apply("claude")
        runtime = self.home / ".claude/harness-commands"
        for relative, name in (("hooks/guard/operations_policy.py", "operations_policy"),
                               ("hooks/checkpoint/compat.py", "compat")):
            with self.subTest(missing=relative):
                missing = runtime / relative
                original = missing.read_bytes()
                missing.unlink()
                ambient = self.base / ("ambient-" + name)
                ambient.mkdir()
                marker = self.base / ("ran-" + name)
                (ambient / (name + ".py")).write_text(
                    "from pathlib import Path\nPath(" + repr(str(marker)) + ").write_text('yes')\n")
                try:
                    for command in ALLOWED:
                        result = self.command("claude", command, env={"PYTHONPATH": str(ambient)})
                        self.assertEqual(result.returncode, 1, command + result.stderr)
                        self.assertFalse(marker.exists())
                finally:
                    missing.write_bytes(original)
                    missing.chmod(0o600)
        self.assertEqual(self.command("claude", "version").returncode, 0)

    @skip_unless_symlinks("the commands launcher is a symbolic link")
    def test_interrupted_apply_recovers_without_adopting_runtime(self):
        plan = self.install("--host", "codex", "--dry-run")
        saved = self.base / "interrupted.json"
        saved.write_text(plan.stdout)
        self.env["COMMANDS_INSTALL_CRASH_AT"] = "after_install"
        self.install("--host", "codex", "--plan", str(saved), ok=False)
        del self.env["COMMANDS_INSTALL_CRASH_AT"]
        self.install("--host", "codex", "--dry-run", ok=False)  # pending transaction first
        recovered = json.loads(self.install("--host", "codex", "--recover").stdout)
        self.assertTrue(recovered["recovered"])
        self.assertFalse((self.home / ".local/bin/codex-harness").exists())
        self.assertFalse((self.home / ".codex/harness-commands").exists())
        self.apply("codex")
        self.assertEqual(self.command("codex", "version").returncode, 0)

    @skip_unless_symlinks("the commands launcher is a symbolic link")
    def test_private_launcher_conflict_drift_and_scoped_uninstall(self):
        launcher = self.home / ".local/bin/claude-harness"
        launcher.parent.mkdir(parents=True)
        private = self.base / "private-harness"
        private.write_text("#!/bin/sh\necho private\n")
        for form in ("file", "link"):
            with self.subTest(form=form):
                if form == "file":
                    launcher.write_text("private launcher")
                else:
                    launcher.symlink_to(private)
                before = snapshot(self.home)
                self.install("--host", "claude", "--dry-run", ok=False)
                self.assertEqual(snapshot(self.home), before)
                self.assertFalse((self.home / ".claude/harness-commands").exists())
                launcher.unlink()
        self.apply("claude")
        data = self.home / "state/harness-commands/claude/keep.json"
        data.parent.mkdir(parents=True)
        data.write_text("user data")
        runtime = self.home / ".claude/harness-commands"
        (runtime / "user.txt").write_text("drift")
        self.install("--host", "claude", "--uninstall", ok=False)
        self.assertTrue((runtime / "user.txt").exists())
        self.assertEqual(self.command("claude", "version").returncode, 0)
        (runtime / "user.txt").unlink()
        self.install("--host", "claude", "--uninstall")
        self.assertEqual(data.read_text(), "user data")
        self.assertFalse(launcher.exists())
        self.assertFalse(runtime.exists())
        self.assertEqual(private.read_text(), "#!/bin/sh\necho private\n")

    @skip_unless_symlinks("profile target symlink refusal")
    def test_symlinked_profile_target_is_not_adopted(self):
        outside = self.base / "outside"
        outside.mkdir()
        marker = outside / "keep.txt"
        marker.write_text("owned by user")
        host = self.home / ".claude"
        host.mkdir()
        (host / "harness-commands").symlink_to(outside, target_is_directory=True)
        self.install("--host", "claude", "--dry-run", ok=False)
        self.assertEqual(marker.read_text(), "owned by user")
        self.assertEqual(sorted(path.name for path in outside.iterdir()), ["keep.txt"])
        self.assertTrue((host / "harness-commands").is_symlink())

    @skip_unless_symlinks("the commands launcher is a symbolic link")
    def test_usage_store_and_override_ignore_full_profile_and_host_variables(self):
        self.apply("claude")
        hostile = {"CLAUDE_HOME": str(self.base / "other-claude"),
                   "CODEX_HOME": str(self.base / "other-codex"),
                   "HARNESS_PROJECT_ROOT": str(self.base / "other-project"),
                   "CLAUDE_PROJECT_DIR": str(self.base / "other-project")}
        declared = self.command("claude", "usage", "declare", "task1", "--file", "-",
                                env=hostile, input=json.dumps(DECLARATION))
        self.assertEqual(declared.returncode, 0, declared.stderr)
        self.assertTrue((self.home / "state/harness-commands/claude/usage/usage.sqlite3").is_file())
        self.assertFalse((self.home / "state/claude-harness").exists())
        for name in ("other-claude", "other-codex", "other-project"):
            self.assertFalse((self.base / name).exists())
        cli = run([sys.executable, "-B", str(ROOT / "hooks/checkpoint/usage_cli.py"), "declare", "task1",
                   "--file", "-"], timeout=20, input=json.dumps(DECLARATION), capture_output=True,
                  text=True, env=clean_env(self.base / "cli-home", self.base), cwd=self.base)
        self.assertEqual(cli.returncode, 0, cli.stderr)
        self.assertEqual(declared.stdout, cli.stdout)
        report = self.command("claude", "usage", "report", "task1", env=hostile)
        self.assertEqual(report.returncode, 0, report.stderr)
        override = self.base / "explicit"
        result = self.command("claude", "usage", "declare", "task2", "--file", "-",
                              env={**hostile, "HARNESS_COMMANDS_STATE": str(override)},
                              input=json.dumps(DECLARATION))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((override / "usage/usage.sqlite3").is_file())
        relative = self.command("claude", "usage", "report",
                                env={"HARNESS_COMMANDS_STATE": "relative-state"})
        self.assertEqual(relative.returncode, 1)
        self.assertFalse((self.base / "relative-state").exists())
        bad = self.command("claude", "usage", "declare")
        self.assertEqual(bad.returncode, 2)
        self.assertFalse((self.home / "state/claude-harness").exists())

    @unittest.skipIf(IS_WINDOWS, "policy activation is unsupported on Windows")
    @skip_unless_symlinks("the commands launcher is a symbolic link")
    def test_policy_and_ssh_load_on_demand_without_interception(self):
        self.apply("claude")
        project = (self.base / "project").resolve()
        (project / ".harness").mkdir(parents=True)
        policy = project / ".harness/operations.json"
        policy.write_text(json.dumps(POLICY))
        bound = self.command("claude", "policy", "bind", "--project", str(project), "--file", str(policy),
                             "--mode", "enforce")
        self.assertEqual(bound.returncode, 0, bound.stderr)
        self.assertTrue((self.home / "config/harness/operations-bindings.json").is_file())
        for command, code, verdict in (("git status", 0, "ALLOW"),
                                       ("ssh deploy@example.test 'cat /srv/site/VERSION'", 0, "ALLOW"),
                                       ("ssh deploy@example.test 'rm /srv/site/current'", 3, "BLOCK"),
                                       ("ssh deploy@example.test 'cat $FILE'", 4, "UNKNOWN")):
            with self.subTest(command=command):
                result = self.command("claude", "ssh", "check", "--file", "-", input=json.dumps(
                    {"schema_version": 1, "command": command, "project_root": str(project)}))
                self.assertEqual(result.returncode, code, result.stderr)
                self.assertEqual(json.loads(result.stdout)["verdict"], verdict)
        status = json.loads(self.command("claude", "status").stdout)
        self.assertIs(status["policy_interception"], False)
        self.assertIs(status["registered_hooks"], False)
        self.assertFalse((self.home / ".claude/settings.json").exists())
        shown = self.command("claude", "policy", "show")
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertEqual(self.command("claude", "policy", "unbind", "--project", str(project)).returncode, 0)
        self.assertEqual(self.command("claude", "policy", "bogus").returncode, 2)

    @skip_unless_symlinks("the commands launcher is a symbolic link")
    def test_repeat_apply_recover_rollback_and_full_install_coexist(self):
        self.apply("claude")
        repeated = json.loads(self.apply("claude").stdout)
        self.assertTrue(repeated["idempotent"])
        same = json.loads(self.install("--host", "claude", "--plan", str(self.base / "claude.json")).stdout)
        self.assertTrue(same["idempotent"])
        status = json.loads(self.install("--host", "claude", "--status").stdout)
        self.assertEqual((status["product"], status["host"]), ("harness", "claude"))
        self.assertFalse(json.loads(self.install("--host", "claude", "--recover").stdout)["recovered"])
        runtime = self.home / ".claude/harness-commands"
        owned = snapshot(runtime)
        self.install("--host", "claude", profile=False)
        settings = json.loads((self.home / ".claude/settings.json").read_text())
        entries = [(event, group.get("matcher"), hook["command"]) for event, groups in settings["hooks"].items()
                   for group in groups for hook in group["hooks"]]
        self.assertFalse([entry for entry in entries if "harness-commands" in entry[2].replace(self.base.as_posix(), "")])
        self.assertEqual(len(entries), len(set(entries)))
        self.assertNotIn("command_runtime", json.dumps(settings))
        self.assertEqual(snapshot(runtime), owned)
        self.install("--host", "claude", "--uninstall", profile=False)
        self.assertEqual(snapshot(runtime), owned)
        self.assertEqual(self.command("claude", "version").returncode, 0)
        self.install("--host", "claude", "--rollback")
        self.assertFalse((self.home / ".local/bin/claude-harness").exists())
        self.assertFalse(runtime.exists())

    def test_shared_helper_matches_router_byte_for_byte(self):
        router = os.environ.get("ROUTER_DIR")
        if not router or not (Path(router) / "scripts/commands_install.py").is_file():
            print("SKIP shared helper parity: ROUTER_DIR has no scripts/commands_install.py", file=sys.stderr)
            raise unittest.SkipTest("ROUTER_DIR has no scripts/commands_install.py")
        self.assertEqual((ROOT / "scripts/commands_install.py").read_bytes(),
                         (Path(router) / "scripts/commands_install.py").read_bytes())


if __name__ == "__main__":
    unittest.main()
