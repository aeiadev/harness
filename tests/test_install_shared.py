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


class SharedInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="shared-role-install-")
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)

    def run_install(self, host, *flags):
        env = {**os.environ, "HOME": str(self.home),
               "CLAUDE_HOME": str(self.home / ".claude"),
               "CODEX_HOME": str(self.home / ".codex")}
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
                    self.run_install(host, "--uninstall")
                    settings = json.loads(settings_path.read_text())
                    self.assertEqual("hooks" in settings, expected)
                    if router_path.exists():
                        router_path.unlink()
                    if settings_path.exists():
                        settings_path.unlink()

    def test_legacy_manifest_keeps_preexisting_empty_hooks_key(self):
        for host in ("claude", "codex"):
            with self.subTest(host=host):
                host_home = self.home / f".{host}"
                settings_path = host_home / ("settings.json" if host == "claude" else "hooks.json")
                manifest_path = host_home / "harness/install-manifest.json"
                host_home.mkdir(parents=True, exist_ok=True)
                settings_path.write_text('{"hooks": {}}')
                manifest_path.parent.mkdir(parents=True, exist_ok=True)
                manifest_path.write_text(json.dumps({"version": 1, "files": {}, "hooks": {}}))
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
