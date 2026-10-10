#!/usr/bin/env python3
"""Shared role ownership across Harness and Router install orders."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
ROLES = ("sweeper", "researcher", "planner", "builder", "builder-in-place",
         "judge", "worker", "test-writer", "docs-writer")
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
DONE = {"claude": "Harness installed. Restart Claude Code to load the agents, skills, and hooks.\n",
        "codex": ("Harness installed for Codex. Trust new or changed hooks in Codex (/hooks) before they run.\n"
                  "Optional judge session profile: bash install.sh --host codex --judge-permissions\n")}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def old_role(host, name):
    """A shared role as Harness 0.2 shipped it (HARNESS_V02 from old-trees.sh; unset fails, never skips)."""
    tree = os.environ.get("HARNESS_V02", "")
    if not tree or not (Path(tree) / "install.sh").is_file():
        raise AssertionError("HARNESS_V02 is unset or has no install.sh; source old-trees.sh first")
    return (Path(tree) / ("agents" if host == "claude" else "codex/agents") / Path(name).name).read_bytes()


class SharedInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="shared-role-install-")
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)

    def run_install(self, host, *flags):
        env = {**os.environ, "HOME": str(self.home),
               "CLAUDE_HOME": str(self.home / ".claude"),
               "CODEX_HOME": str(self.home / ".codex"),
               "XDG_CONFIG_HOME": str(self.home / ".config"), "XDG_STATE_HOME": str(self.home / ".state")}
        result = subprocess.run(["bash", str(ROOT / "install.sh"), "--host", host, *flags],
                                env=env, stdin=subprocess.DEVNULL, capture_output=True,
                                text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def role_paths(self, host):
        extension = ".md" if host == "claude" else ".toml"
        return [f"agents/{role}{extension}" for role in ROLES]

    def router_manifest(self, host, paths):
        host_home = self.home / f".{host}"
        target = host_home / "router/install-manifest.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        files = {name: {"sha256": hashlib.sha256((host_home / name).read_bytes()).hexdigest()}
                 for name in paths}
        target.write_text(json.dumps({"version": 1, "files": files, "hooks": []}), encoding="utf-8")

    def skewed_state(self, host):
        """Harness 0.3 after a skewed run: 0.2-era role bytes on disk, claimed with their hashes."""
        self.run_install(host)
        top = self.home / f".{host}"
        path = top / "harness/install-manifest.json"
        manifest = json.loads(path.read_text())
        for name in self.role_paths(host):
            (top / name).write_bytes(old_role(host, name))
            manifest["files"][name] = sha(old_role(host, name))
        path.write_text(json.dumps(manifest, indent=2) + "\n")
        return top

    def claim(self, host, package):
        """A Router manifest claiming every shared role with the bytes on disk."""
        top = self.home / f".{host}"
        value = {"version": 1, "files": {name: {"sha256": sha((top / name).read_bytes())}
                                         for name in self.role_paths(host)}, "hooks": []}
        if package is not None:
            value["package"] = package
        (top / "router").mkdir(exist_ok=True)
        (top / "router/install-manifest.json").write_text(json.dumps(value))

    def shipped(self, host, name):
        return (ROOT / ("agents" if host == "claude" else "codex/agents") / Path(name).name).read_bytes()

    def test_same_version_claim_on_old_role_bytes_is_released(self):
        for host in ("claude", "codex"):
            with self.subTest(host=host):
                top = self.skewed_state(host)
                self.claim(host, VERSION)
                output = self.run_install(host)
                self.assertEqual(output, f"Installing {len(ROLES)} files into {top}.\n" + DONE[host])
                files = json.loads((top / "harness/install-manifest.json").read_text())["files"]
                for name in self.role_paths(host):
                    self.assertEqual((top / name).read_bytes(), self.shipped(host, name), name)
                    self.assertEqual(files[name], sha(self.shipped(host, name)), name)

    def test_older_sibling_or_edited_role_is_left_with_the_notice(self):
        for host in ("claude", "codex"):
            for case in ("older", "edited"):
                with self.subTest(host=host, case=case):
                    top = self.skewed_state(host)
                    self.claim(host, None if case == "older" else VERSION)
                    if case == "edited":
                        for name in self.role_paths(host):
                            (top / name).write_bytes(b"user edit\n")
                    before = {name: (top / name).read_bytes() for name in self.role_paths(host)}
                    output = self.run_install(host)
                    version = "0.2.0 or earlier" if case == "older" else VERSION
                    self.assertEqual(output, f"shared roles come from Router {version}; upgrade it for the 0.3 roles\n"
                                             f"Installing 0 files into {top}.\n" + DONE[host])
                    files = json.loads((top / "harness/install-manifest.json").read_text())["files"]
                    for name, data in before.items():
                        self.assertEqual((top / name).read_bytes(), data, name)
                        self.assertEqual(files[name], sha(data), name)
                    (top / "router/install-manifest.json").unlink()

    def test_sibling_gone_uninstall_removes_a_role_holding_the_shipped_bytes(self):
        for host in ("claude", "codex"):
            with self.subTest(host=host):
                top = self.skewed_state(host)
                # Router 0.3 rewrote the roles to the shipped bytes, then left; Harness's record is stale.
                for name in self.role_paths(host):
                    (top / name).write_bytes(self.shipped(host, name))
                count = len(json.loads((top / "harness/install-manifest.json").read_text())["files"])
                settings = "settings.json" if host == "claude" else "hooks.json"
                output = self.run_install(host, "--uninstall")
                self.assertEqual(output, f"Removing {count} unchanged files and owned settings entries.\n"
                                         f"Removing {settings} (absent before install)\n"
                                         "Harness uninstalled. User additions and modified files were kept.\n")
                for name in self.role_paths(host):
                    self.assertFalse((top / name).exists(), name)

    def test_version_stamp(self):
        from datetime import datetime, timezone
        self.assertEqual((ROOT / "VERSION").read_text().strip(), "0.3.0")
        for host in ("claude", "codex"):
            with self.subTest(host=host):
                self.run_install(host)
                path = self.home / f".{host}/harness/install-manifest.json"
                for _ in range(2):
                    manifest = json.loads(path.read_text())
                    self.assertIs(type(manifest["version"]), int)
                    self.assertEqual(manifest["version"], 1)
                    self.assertEqual(manifest["package"], "0.3.0")
                    self.assertEqual(manifest["source"], str(ROOT))
                    self.assertIs(type(manifest["installed_at"]), str)
                    self.assertEqual(datetime.fromisoformat(manifest["installed_at"]).utcoffset(), timezone.utc.utcoffset(None))
                    self.run_install(host)

    def test_reinstall_leaves_manifest_bytes_and_mtime(self):
        import time
        for host in ("claude", "codex"):
            with self.subTest(host=host):
                self.run_install(host)
                path = self.home / f".{host}/harness/install-manifest.json"
                before, mtime = path.read_bytes(), path.stat().st_mtime_ns
                time.sleep(0.05)
                self.run_install(host)
                self.assertEqual(path.read_bytes(), before)
                self.assertEqual(path.stat().st_mtime_ns, mtime)

    def test_both_preflights_second_host_before_writing(self):
        claude = self.home / ".claude"
        claude.mkdir()
        (claude / "sentinel").write_bytes(b"unchanged")
        codex = self.home / ".codex"
        (codex / "agents").mkdir(parents=True)
        (codex / "agents/sweeper.toml").write_bytes(b"user role")

        def tree_hash(root):
            entries = []
            for path in sorted(root.rglob("*")):
                entries.append((str(path.relative_to(root)), "dir" if path.is_dir() else hashlib.sha256(path.read_bytes()).hexdigest()))
            return entries

        before = tree_hash(claude)
        env = {**os.environ, "HOME": str(self.home), "CLAUDE_HOME": str(claude), "CODEX_HOME": str(codex)}
        result = subprocess.run(["bash", str(ROOT / "install.sh"), "--host", "both"], env=env,
                                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("refusing to overwrite", result.stderr)
        self.assertEqual(tree_hash(claude), before)

    def test_bad_json_and_manifest_name_file_once(self):
        for host in ("claude", "codex"):
            for case in ("bad_json", "nonobject", "bad_manifest", "unreadable"):
                with self.subTest(host=host, case=case):
                    host_home = self.home / f".{host}"
                    settings = host_home / ("settings.json" if host == "claude" else "hooks.json")
                    manifest = host_home / "harness/install-manifest.json"
                    settings.parent.mkdir(exist_ok=True)
                    if settings.is_dir():
                        settings.rmdir()
                    elif settings.exists():
                        settings.unlink()
                    if manifest.exists():
                        manifest.unlink()

                    if case == "bad_json":
                        settings.write_text("{bad")
                    elif case == "nonobject":
                        settings.write_text("[]")
                    elif case == "bad_manifest":
                        settings.write_text("{}")
                        manifest.parent.mkdir(parents=True, exist_ok=True)
                        manifest.write_text('{"version": 1, "files": [], "hooks": {}}')
                    else:
                        settings.mkdir()
                    path = manifest if case == "bad_manifest" else settings
                    env = {**os.environ, "HOME": str(self.home), "CLAUDE_HOME": str(self.home / ".claude"),
                           "CODEX_HOME": str(self.home / ".codex")}
                    result = subprocess.run(["bash", str(ROOT / "install.sh"), "--host", host], env=env,
                                            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
                    self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                    self.assertEqual(len(result.stderr.splitlines()), 1, result.stderr)
                    self.assertIn(str(path), result.stderr)
                    self.assertNotIn("Traceback", result.stderr)
                    if settings.is_dir():
                        settings.rmdir()
                    else:
                        settings.unlink()
                    if manifest.exists():
                        manifest.unlink()

    def test_uninstall_removes_bytecode_cache_files_and_directories(self):
        for host in ("claude", "codex"):
            with self.subTest(host=host):
                self.run_install(host)
                runtime = self.home / f".{host}/harness"
                cache = runtime / "hooks/checkpoint/__pycache__"
                cache.mkdir()
                (cache / "dispatch.cpython-312.pyc").write_bytes(b"cache")
                (runtime / "hooks/checkpoint/old.pyo").write_bytes(b"cache")
                self.run_install(host, "--uninstall")
                self.assertFalse(cache.exists())
                self.assertFalse((runtime / "hooks/checkpoint/old.pyo").exists())

    def test_first_install_inherits_router_hook_key_ownership(self):
        for host in ("claude", "codex"):
            for sibling_state, initial_hooks, expected in (
                ("false", {}, False),
                ("missing", {}, True),
                ("invalid", {}, True),
                ("malformed_false", {}, True),
                ("keyless", {}, True),
                ("true", {}, True),
                ("false", None, False),
            ):
                with self.subTest(host=host, sibling=sibling_state, initial_hooks=initial_hooks):
                    host_home = self.home / f".{host}"
                    settings_path = host_home / ("settings.json" if host == "claude" else "hooks.json")
                    manifest_path = host_home / "harness/install-manifest.json"
                    router_path = host_home / "router/install-manifest.json"
                    host_home.mkdir(parents=True, exist_ok=True)
                    if initial_hooks is not None:
                        settings_path.write_text(json.dumps({"hooks": initial_hooks}))
                    if sibling_state != "missing":
                        router_path.parent.mkdir(parents=True, exist_ok=True)
                        payload = {"version": 1, "files": {}, "hooks": []}
                        if sibling_state in ("false", "malformed_false", "true"):
                            payload["had_hooks"] = sibling_state == "true"
                        if sibling_state == "malformed_false":
                            del payload["hooks"]
                        router_path.write_text("{broken" if sibling_state == "invalid" else json.dumps(payload))
                    self.run_install(host)
                    manifest = json.loads(manifest_path.read_text())
                    self.assertEqual(manifest["had_hooks"], expected)
                    self.run_install(host)
                    self.assertEqual(json.loads(manifest_path.read_text())["had_hooks"], expected)
                    if initial_hooks is not None:
                        # A reformat after install makes uninstall take the surgical path that reads had_hooks.
                        settings_path.write_text(json.dumps(json.loads(settings_path.read_text()), indent=4))
                    self.run_install(host, "--uninstall")
                    if initial_hooks is None:
                        self.assertFalse(settings_path.exists(), "uninstall left the settings file it created")
                    else:
                        settings = json.loads(settings_path.read_text())
                        self.assertEqual("hooks" in settings, expected)
                    if router_path.exists():
                        router_path.unlink()
                    if settings_path.exists():
                        settings_path.unlink()
                    # Each case starts clean: an old backup beside a 0.2-style sibling is a 0.1/0.2 original.
                    for backup in host_home.glob(settings_path.name + ".harness-backup-*"):
                        backup.unlink()

    def test_legacy_manifest_keeps_preexisting_empty_hooks_key(self):
        for host in ("claude", "codex"):
            with self.subTest(host=host):
                host_home = self.home / f".{host}"
                settings_path = host_home / ("settings.json" if host == "claude" else "hooks.json")
                manifest_path = host_home / "harness/install-manifest.json"
                host_home.mkdir(parents=True, exist_ok=True)
                settings_path.write_text('{"hooks": {}}')
                manifest_path.parent.mkdir(parents=True, exist_ok=True)
                # Every 0.1 and 0.2 manifest records had_hooks; this seed held a "hooks" key.
                manifest_path.write_text(json.dumps({"version": 1, "files": {}, "hooks": {}, "had_hooks": True}))
                self.run_install(host, "--uninstall")
                self.assertEqual(json.loads(settings_path.read_text()), {"hooks": {}})

    def test_router_claim_keeps_every_shared_role(self):
        for host in ("claude", "codex"):
            with self.subTest(host=host):
                self.run_install(host)
                paths = self.role_paths(host)
                self.router_manifest(host, paths)
                output = self.run_install(host, "--uninstall")
                for name in paths:
                    self.assertTrue((self.home / f".{host}" / name).is_file(), name)
                    self.assertIn(f"Keeping shared role file: {name}", output)

    def test_without_router_manifest_removes_every_shared_role(self):
        for host in ("claude", "codex"):
            with self.subTest(host=host):
                self.run_install(host)
                self.run_install(host, "--uninstall")
                for name in self.role_paths(host):
                    self.assertFalse((self.home / f".{host}" / name).exists(), name)

    def test_invalid_router_manifest_keeps_every_shared_role(self):
        for host in ("claude", "codex"):
            with self.subTest(host=host):
                self.run_install(host)
                target = self.home / f".{host}" / "router/install-manifest.json"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text('{broken', encoding="utf-8")
                output = self.run_install(host, "--uninstall")
                for name in self.role_paths(host):
                    self.assertTrue((self.home / f".{host}" / name).is_file(), name)
                self.assertIn("invalid Router manifest", output)

    def test_identical_preexisting_role_is_claimed(self):
        for host in ("claude", "codex"):
            with self.subTest(host=host):
                name = self.role_paths(host)[0]
                target = self.home / f".{host}" / name
                target.parent.mkdir(parents=True, exist_ok=True)
                source = ROOT / ("agents" if host == "claude" else "codex/agents") / target.name
                target.write_bytes(source.read_bytes())
                self.run_install(host)
                manifest = json.loads((self.home / f".{host}" / "harness/install-manifest.json").read_text())
                self.assertIn(name, manifest["files"])
                self.run_install(host, "--uninstall")
                self.assertFalse(target.exists())

    def test_identical_preexisting_nonshared_files_are_not_claimed(self):
        for host in ("claude", "codex"):
            with self.subTest(host=host):
                host_home = self.home / f".{host}"
                role = self.role_paths(host)[0]
                source_dir = "agents" if host == "claude" else "codex/agents"
                paths = {
                    role: ROOT / source_dir / Path(role).name,
                    "skills/checkpoint/SKILL.md": ROOT / "skills/checkpoint/SKILL.md",
                    "harness/hooks/context/inject-project-context.sh": ROOT / "hooks/context/inject-project-context.sh",
                }
                for name, source in paths.items():
                    target = host_home / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(source.read_bytes())
                self.run_install(host)
                manifest = json.loads((host_home / "harness/install-manifest.json").read_text())
                self.assertIn(role, manifest["files"], "identical shared role must be claimed")
                for name in paths:
                    if name != role:
                        self.assertNotIn(name, manifest["files"], f"identical non-shared file was claimed: {name}")
                self.run_install(host, "--uninstall")
                self.assertFalse((host_home / role).exists(), "claimed shared role must be removed")
                for name in paths:
                    if name != role:
                        self.assertTrue((host_home / name).is_file(), f"unowned file was removed: {name}")

    def test_checksum_is_repo_only_and_legacy_copy_is_removed_if_unchanged(self):
        for host in ("claude", "codex"):
            with self.subTest(host=host):
                host_home = self.home / f".{host}"
                checksum = host_home / "agents/SHARED.sha256"
                self.run_install(host)
                self.assertFalse(checksum.exists(), "repository checksum must not be installed")
                manifest_path = host_home / "harness/install-manifest.json"
                manifest = json.loads(manifest_path.read_text())
                self.assertNotIn("agents/SHARED.sha256", manifest["files"])
                checksum.write_bytes((ROOT / "agents/SHARED.sha256").read_bytes())
                manifest["files"]["agents/SHARED.sha256"] = hashlib.sha256(checksum.read_bytes()).hexdigest()
                manifest_path.write_text(json.dumps(manifest))
                self.run_install(host, "--uninstall")
                self.assertFalse(checksum.exists(), "unchanged legacy checksum must be removed")

    def test_modified_legacy_checksum_survives_uninstall(self):
        for host in ("claude", "codex"):
            with self.subTest(host=host):
                host_home = self.home / f".{host}"
                self.run_install(host)
                checksum = host_home / "agents/SHARED.sha256"
                original = (ROOT / "agents/SHARED.sha256").read_bytes()
                checksum.write_bytes(original + b"user edit\n")
                manifest_path = host_home / "harness/install-manifest.json"
                manifest = json.loads(manifest_path.read_text())
                manifest["files"]["agents/SHARED.sha256"] = hashlib.sha256(original).hexdigest()
                manifest_path.write_text(json.dumps(manifest))
                self.run_install(host, "--uninstall")
                self.assertEqual(checksum.read_bytes(), original + b"user edit\n")


if __name__ == "__main__":
    unittest.main()
