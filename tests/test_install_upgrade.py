#!/usr/bin/env python3
"""Exact uninstall, created paths, upgrade cleanup, shared-role skew, --purge and VERSION.

Old releases come from old-trees.sh (HARNESS_V01, HARNESS_V02, ROUTER_V02). A test that
needs one and finds it unset fails. Under a python3 older than 3.10 the old releases run
with a newer python3 from PATH, and are skipped only when none exists.
"""

import ast
import atexit
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import types
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from _platform import (BASH, IS_WINDOWS, home_env, posix, posix_modes, python_shim,  # noqa: E402
                       skip_unless_posix_modes)
HOSTS = ("claude", "codex")
SETTINGS = {"claude": "settings.json", "codex": "hooks.json"}
ROLES = ("sweeper", "researcher", "planner", "builder", "builder-in-place",
         "judge", "worker", "test-writer", "docs-writer")
SKEW = "shared roles come from Router 0.2.0 or earlier; upgrade it for the 0.3 roles"
HAND_FORMATTED = (b'{\n\t"zeta":   1,\n\t"alpha": [1,2, 3],\n\t"hooks": {"Stop": []},\n'
                  b'\t"note": "tabs, key order, no trailing newline"\n}')
EXTRAS = {"claude": ("agents/Explore.md", "agents/Plan.md", "agents/general-purpose.md"),
          "codex": ("agents/seat-judge.toml", "agents/seat-builder.toml")}


RELEASED_PYTHON = {}  # shim folders keyed by the installer's actual Python floor


def released_env(env, tree):
    """Use a Python meeting this tree's floor, without reusing a lower-floor shim."""
    if env.get("HOME"):
        env = dict(env, **home_env(env["HOME"]))
    match = re.search(r"sys\.version_info < \((\d+), (\d+)\)", (Path(tree) / "install.sh").read_text())
    floor = tuple(map(int, match.groups())) if match else (3, 9)
    if sys.version_info >= floor and not IS_WINDOWS:
        return env
    if floor not in RELEASED_PYTHON:
        found = sys.executable if sys.version_info >= floor else None
        if found is None:
            search = os.environ.get("PATH", os.defpath)
            names = [f"python3.{minor}" for minor in range(20, floor[1] - 1, -1)] + ["python3", "python", "py"]
            for name in names:
                candidate = shutil.which(name, path=search)
                if not candidate:
                    continue
                args = [candidate] + (["-3"] if name == "py" else [])
                try:
                    result = subprocess.run(args + ["-c", "import sys; print(sys.executable) if sys.version_info >= "
                                                    + repr(floor) + " else sys.exit(1)"],
                                            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10)
                except (OSError, subprocess.TimeoutExpired):
                    continue
                if result.returncode == 0 and result.stdout.strip():
                    found = result.stdout.strip()
                    break
        if found is None:
            raise unittest.SkipTest("the release installer needs Python " + ".".join(map(str, floor))
                                    + " or newer; none is on PATH")
        folder = Path(tempfile.mkdtemp(prefix="harness-released-python-"))
        atexit.register(shutil.rmtree, folder, True)
        python_shim(folder, found)
        RELEASED_PYTHON[floor] = str(folder)
    return dict(env, PATH=RELEASED_PYTHON[floor] + os.pathsep + env.get("PATH", os.defpath))


def old_tree(name):
    value = os.environ.get(name, "")
    if not value or not (Path(value) / "install.sh").is_file():
        raise AssertionError(f"{name} is unset or has no install.sh; source old-trees.sh first")
    return Path(value)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def snapshot(top):
    """Every path below top with its kind, mode and bytes."""
    found = {}
    for path in sorted(Path(top).rglob("*")):
        info = path.lstat()
        kind = "dir" if stat.S_ISDIR(info.st_mode) else "link" if stat.S_ISLNK(info.st_mode) else "file"
        found[str(path.relative_to(top))] = (kind, stat.S_IMODE(info.st_mode),
                                             path.read_bytes() if kind == "file" else None)
    return found


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="harness-upgrade-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "user"
        self.home.mkdir()

    def host_home(self, host):
        return self.home / f".{host}"

    def settings_path(self, host):
        return self.host_home(host) / SETTINGS[host]

    def manifest(self, host):
        return json.loads((self.host_home(host) / "harness/install-manifest.json").read_text())

    def run_tree(self, tree, host, *flags, code=0, home=None):
        home = home or self.home
        env = {**os.environ, "HOME": str(home), "CLAUDE_HOME": str(home / ".claude"),
               "CODEX_HOME": str(home / ".codex")}
        env = released_env(env, tree)
        if IS_WINDOWS and Path(tree).resolve() != ROOT and "--uninstall" in flags:
            # Released installers copy source modes but cannot unlink NTFS read-only files.
            # Keep their historical behavior intact and make only the old fixture writable.
            for folder, _, names in os.walk(home):
                for name in names:
                    os.chmod(os.path.join(folder, name), stat.S_IREAD | stat.S_IWRITE)
        result = subprocess.run([BASH, posix(Path(tree) / "install.sh"), "--host", host, *flags],
                                env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                timeout=60)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        return result

    def run_install(self, host, *flags, code=0):
        return self.run_tree(ROOT, host, *flags, code=code)

    def harness_commands(self, host):
        path = self.settings_path(host)
        if not path.exists():
            return []
        settings = json.loads(path.read_text())
        return [hook.get("command") for groups in settings.get("hooks", {}).values()
                for group in groups for hook in group.get("hooks", [])
                if "/harness/" in str(hook.get("command"))]

    def tree_state(self, top=None):
        top = top or self.home
        state = {}
        for path in sorted(top.rglob("*")):
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                kind = "link:" + os.readlink(path)
            elif stat.S_ISDIR(info.st_mode):
                kind = "dir"
            else:
                kind = sha(path.read_bytes())
            state[str(path.relative_to(top))] = (kind, stat.S_IMODE(info.st_mode), info.st_mtime_ns)
        return state


class ExactUninstallTests(Base):
    def test_hand_formatted_settings_restore_exact_bytes(self):
        for host in HOSTS:
            with self.subTest(host=host):
                path = self.settings_path(host)
                path.parent.mkdir(parents=True)
                path.write_bytes(HAND_FORMATTED)
                self.run_install(host)
                self.assertNotEqual(path.read_bytes(), HAND_FORMATTED)
                manifest = self.manifest(host)
                self.assertIs(manifest["settings_original_existed"], True)
                self.assertNotIn("/", manifest["settings_original_backup"])
                self.assertEqual(manifest["settings_written_sha256"], sha(path.read_bytes()))
                self.assertNotIn("tabs, key order", json.dumps(manifest), "settings text copied into the manifest")
                self.run_install(host)
                self.run_install(host, "--uninstall")
                self.assertEqual(path.read_bytes(), HAND_FORMATTED)

    def test_edit_between_install_and_uninstall_survives(self):
        for host in HOSTS:
            with self.subTest(host=host):
                path = self.settings_path(host)
                path.parent.mkdir(parents=True)
                path.write_bytes(HAND_FORMATTED)
                self.run_install(host)
                edited = json.loads(path.read_text())
                edited["userEdit"] = "keep me"
                path.write_text(json.dumps(edited))
                self.run_install(host, "--uninstall")
                result = json.loads(path.read_text())
                self.assertEqual(result, {**json.loads(HAND_FORMATTED), "userEdit": "keep me"})
                self.assertEqual(self.harness_commands(host), [])

    def test_v01_upgrade_then_uninstall_keeps_user_empty_stop(self):
        tree = old_tree("HARNESS_V01")
        initial = {"hooks": {"Stop": []}, "keep": 1}
        for host in HOSTS:
            with self.subTest(host=host):
                path = self.settings_path(host)
                path.parent.mkdir(parents=True)
                path.write_text(json.dumps(initial))
                self.run_tree(tree, host)
                (old,) = self.host_home(host).glob(SETTINGS[host] + ".harness-backup-*")
                self.run_install(host)
                manifest = self.manifest(host)
                # 0.1 named no backup: the oldest one holds the settings before Harness, and it is 0600.
                self.assertEqual((manifest["settings_original_backup"], manifest["settings_original_sha256"]),
                                 (old.name, sha(json.dumps(initial).encode())))
                modes = posix_modes(self, "the 0o600 settings mode recorded and restored")
                if modes:
                    self.assertEqual(manifest["settings_original_mode"], 0o600)
                self.run_install(host, "--uninstall")
                self.assertEqual(path.read_bytes(), json.dumps(initial).encode())
                if modes:
                    self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_v02_upgrade_then_uninstall_is_surgical(self):
        tree = old_tree("HARNESS_V02")
        for host in HOSTS:
            with self.subTest(host=host):
                path = self.settings_path(host)
                path.parent.mkdir(parents=True)
                path.write_text(json.dumps({"keep": 1}))
                self.run_tree(tree, host)
                self.assertNotIn("settings_written_sha256", self.manifest(host))
                (old,) = self.host_home(host).glob(SETTINGS[host] + ".harness-backup-*")
                # The user drops one Harness event, so this checkout rewrites the file.
                settings = json.loads(path.read_text())
                settings["hooks"].pop(sorted(settings["hooks"])[0])
                path.write_text(json.dumps(settings))
                output = self.run_install(host).stdout
                self.assertIn("Backup:", output)
                manifest = self.manifest(host)
                # Never Harness's own file: the oldest backup, which holds the settings before 0.2.
                self.assertEqual((manifest["settings_original_existed"], manifest["settings_original_backup"],
                                  manifest["settings_original_sha256"]),
                                 (True, old.name, sha(b'{"keep": 1}')), "an upgrade recorded Harness's own file as the original")
                self.run_install(host, "--uninstall")
                self.assertEqual(self.harness_commands(host), [], "uninstall restored a file with 0.2 hooks")
                self.assertEqual(path.read_bytes(), b'{"keep": 1}')
                if posix_modes(self, "the 0o600 settings mode restored"):
                    self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)


class CreatedPathTests(Base):
    def test_fresh_home_leaves_no_settings_or_created_dirs(self):
        for host in HOSTS:
            with self.subTest(host=host):
                self.run_install(host)
                self.assertTrue(self.settings_path(host).is_file())
                self.assertIn("agents", self.manifest(host)["created_dirs"])
                self.run_install(host, "--uninstall")
                self.assertTrue(self.host_home(host).is_dir(), "the host home must stay")
                self.assertEqual(sorted(p.name for p in self.host_home(host).iterdir()), [])

    def test_preexisting_empty_dirs_and_empty_settings_survive(self):
        for host in HOSTS:
            for edit in (False, True):
                with self.subTest(host=host, edit=edit):
                    top = self.host_home(host)
                    if top.exists():
                        shutil.rmtree(top)
                    (top / "skills").mkdir(parents=True)
                    (top / "agents").mkdir()
                    path = self.settings_path(host)
                    path.write_bytes(b"{}")
                    self.run_install(host)
                    self.assertNotIn("skills", self.manifest(host)["created_dirs"])
                    if edit:
                        path.write_text(json.dumps(json.loads(path.read_text()), indent=4))
                    self.run_install(host, "--uninstall")
                    self.assertTrue(path.is_file(), "a pre-existing settings file was deleted")
                    self.assertEqual(json.loads(path.read_text()), {})
                    if not edit:
                        self.assertEqual(path.read_bytes(), b"{}")
                    for name in ("skills", "agents"):
                        self.assertTrue((top / name).is_dir(), f"pre-existing empty {name} was removed")
                    self.assertFalse((top / "harness").exists())

    def test_user_file_in_created_dir_keeps_dir(self):
        for host in HOSTS:
            with self.subTest(host=host):
                self.run_install(host)
                top = self.host_home(host)
                (top / "harness/notes.txt").write_text("mine\n")
                (top / "skills/checkpoint/mine.md").write_text("mine\n")
                self.run_install(host, "--uninstall")
                self.assertEqual((top / "harness/notes.txt").read_text(), "mine\n")
                self.assertEqual((top / "skills/checkpoint/mine.md").read_text(), "mine\n")
                self.assertFalse((top / "harness/install-manifest.json").exists())
                self.assertFalse((top / "harness/hooks").exists())
                self.assertFalse((top / "agents").exists())

    def test_symlinked_created_dir_outside_home_survives(self):
        for host in HOSTS:
            with self.subTest(host=host):
                self.run_install(host)
                top = self.host_home(host)
                shutil.rmtree(top / "skills")
                outside = self.root / f"outside-{host}"
                (outside / "checkpoint").mkdir(parents=True)
                (top / "skills").symlink_to(outside, target_is_directory=True)
                output = self.run_install(host, "--uninstall").stdout
                self.assertTrue((outside / "checkpoint").is_dir(), "an outside directory was removed")
                self.assertIn("skills", "\n".join(line for line in output.splitlines() if "link" in line))
                self.assertFalse((top / "agents").exists())

    def test_v02_upgrade_uninstall_prunes_emptied_harness_dirs(self):
        tree = old_tree("HARNESS_V02")
        for host in HOSTS:
            with self.subTest(host=host):
                self.run_tree(tree, host)
                self.run_install(host)
                self.assertIn("skills", self.manifest(host)["created_dirs"])
                self.run_install(host, "--uninstall")
                for name in ("agents", "skills", "harness"):
                    self.assertFalse((self.host_home(host) / name).exists(), f"left emptied {name}")
                self.assertTrue(self.host_home(host).is_dir())


class UpgradeCleanupTests(Base):
    @staticmethod
    def shipped_source(relative, host):
        """Map a destination to the shipped file without consulting the installer."""
        for prefix, folder in (("harness/hooks/", "hooks"),
                               ("harness/statusline/", "statusline"),
                               ("agents/", "agents" if host == "claude" else "codex/agents"),
                               ("skills/", "skills")):
            if relative.startswith(prefix):
                return ROOT / folder / relative[len(prefix):]
        raise AssertionError(f"unexpected installed payload path: {relative}")

    def extended_tree(self, name):
        tree = self.root / f"tree-{name}"
        if not tree.exists():
            shutil.copytree(old_tree(name), tree)
            for host, extras in EXTRAS.items():
                folder = tree / ("agents" if host == "claude" else "codex/agents")
                for relative in extras:
                    (folder / Path(relative).name).write_text(f"old {relative}\n")
                    (folder / Path(relative).name).chmod(0o444)
        return tree

    def check_upgrade(self, name):
        tree = self.extended_tree(name)
        for host in HOSTS:
            with self.subTest(host=host, tree=name):
                self.run_tree(tree, host)
                top = self.host_home(host)
                for relative in EXTRAS[host]:
                    self.assertTrue((top / relative).is_file())
                guide = "harness/hooks/checkpoint/guide.md"
                read_only = "harness/hooks/guard/wait-loop-guard.py"
                self.assertFalse((top / "router/install-manifest.json").exists(),
                                 "read-only repair fixture must have no sibling claim")
                owned = {relative.replace("\\", "/"): expected
                         for relative, expected in self.manifest(host)["files"].items()}
                for relative in (guide, read_only):
                    source = self.shipped_source(relative, host)
                    self.assertIn(relative, owned)
                    self.assertEqual((top / relative).read_bytes(), source.read_bytes(),
                                     f"{relative} must be retained with equal bytes")
                modes = posix_modes(self, "private legacy preservation and read-only repair")
                if modes:
                    (top / guide).chmod(0o600)
                    (top / read_only).chmod(0o444)
                before = {relative: ((top / relative).read_bytes(), stat.S_IMODE((top / relative).stat().st_mode))
                          for relative in owned}
                if modes:
                    self.assertEqual(before[guide][1], 0o600)
                    self.assertEqual(before[read_only][1], 0o444)
                    self.assertTrue(before[guide][1] & stat.S_IWUSR)
                    self.assertFalse(before[read_only][1] & stat.S_IWUSR)
                output = self.run_install(host).stdout
                for relative in EXTRAS[host]:
                    self.assertFalse((top / relative).exists(), f"left no-longer-shipped {relative}")
                    self.assertIn(f"Removed no-longer-shipped: {relative}", output)
                    self.assertNotIn(relative, self.manifest(host)["files"])
                new_files, replaced_files = [], []
                for relative in self.manifest(host)["files"]:
                    source = self.shipped_source(relative, host)
                    self.assertTrue(source.is_file(), relative)
                    shipped = source.read_bytes()
                    current = (top / relative).read_bytes()
                    self.assertEqual(current, shipped, relative)
                    old = before.get(relative)
                    if old is not None and old[0] == shipped and (not modes or old[1] & stat.S_IWUSR):
                        self.assertEqual(current, old[0], f"retained bytes changed: {relative}")
                        if modes:
                            self.assertEqual(stat.S_IMODE((top / relative).stat().st_mode), old[1],
                                             f"retained owner-writable mode changed: {relative}")
                    else:
                        if old is None:
                            new_files.append(relative)
                        elif old[0] != shipped:
                            replaced_files.append(relative)
                        if modes:
                            expected = 0o755 if source.stat().st_mode & 0o111 else 0o644
                            self.assertEqual(stat.S_IMODE((top / relative).stat().st_mode), expected,
                                             f"new, replaced, or owned read-only file: {relative}")
                self.assertTrue(new_files, "fixture must exercise a new payload write")
                self.assertTrue(replaced_files, "fixture must exercise a replaced payload write")
                self.assertIn(guide, self.manifest(host)["files"])
                self.assertIn(read_only, self.manifest(host)["files"])
                self.run_install(host, "--uninstall")
                self.assertFalse((top / "harness/install-manifest.json").exists())

    def test_v01_upgrade_removes_unshipped_files(self):
        self.check_upgrade("HARNESS_V01")

    def test_v02_upgrade_removes_unshipped_files(self):
        self.check_upgrade("HARNESS_V02")

    @skip_unless_posix_modes("old archive extraction and upgrade under 0o077 and 0o022 umasks")
    def test_upgrade_with_masked_old_archive(self):
        case = os.environ.get("HARNESS_MODE_CASE")
        if case is None:
            for mask in (0o077, 0o022):
                for name in ("HARNESS_V01", "HARNESS_V02"):
                    with self.subTest(mask=oct(mask), tree=name):
                        env = dict(os.environ, HARNESS_MODE_CASE=f"{mask:o}:{name}")
                        result = subprocess.run([sys.executable, __file__,
                                                 "UpgradeCleanupTests.test_upgrade_with_masked_old_archive"],
                                                env=env, stdin=subprocess.DEVNULL, capture_output=True,
                                                text=True, timeout=240)
                        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return
        mask_text, name = case.split(":", 1)
        mask = int(mask_text, 8)
        self.assertIn(mask, (0o077, 0o022))
        self.assertIn(name, ("HARNESS_V01", "HARNESS_V02"))
        os.umask(mask)  # This process creates the archive, extracts it, installs, and upgrades.
        archive = self.root / "old.tar"
        with tarfile.open(archive, "w") as tar:
            def git_modes(info):
                info.mode = (0o755 if info.mode & 0o111 else 0o644) if info.isfile() else 0o755
                return info
            tar.add(old_tree(name), arcname=".", filter=git_modes)
        extracted = self.root / "extracted"
        extracted.mkdir()
        subprocess.run([BASH, "-c", 'tar -xf "$1" -C "$2"', "tar", str(archive), str(extracted)],
                       check=True, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
        guide_mode = stat.S_IMODE((extracted / "hooks/checkpoint/guide.md").stat().st_mode)
        self.assertEqual(guide_mode, 0o600 if mask == 0o077 else 0o644)
        os.environ[name] = str(extracted)
        self.check_upgrade(name)
        if mask == 0o077:
            fresh = self.root / "fresh"
            fresh.mkdir()
            self.run_tree(ROOT, "claude", home=fresh)
            installed = fresh / ".claude/harness/hooks/checkpoint/guide.md"
            self.assertEqual(installed.read_bytes(), (ROOT / "hooks/checkpoint/guide.md").read_bytes())
            self.assertEqual(stat.S_IMODE(installed.stat().st_mode), 0o644)

    def test_edited_old_file_survives_and_is_reported(self):
        tree = self.extended_tree("HARNESS_V01")
        for host in HOSTS:
            with self.subTest(host=host):
                self.run_tree(tree, host)
                edited, unchanged = (self.host_home(host) / name for name in EXTRAS[host][:2])
                edited.chmod(0o644)
                edited.write_text("user edit\n")
                output = self.run_install(host).stdout
                self.assertEqual(edited.read_text(), "user edit\n")
                self.assertIn(f"Keeping modified old file: {EXTRAS[host][0]}", output)
                self.assertFalse(unchanged.exists())

    def test_dry_run_lists_and_writes_nothing(self):
        tree = self.extended_tree("HARNESS_V02")
        for host in HOSTS:
            with self.subTest(host=host):
                self.run_tree(tree, host)
                before = self.tree_state()
                output = self.run_install(host, "--dry-run").stdout
                self.assertEqual(self.tree_state(), before)
                for relative in EXTRAS[host]:
                    self.assertIn(f"Would remove no-longer-shipped: {relative}", output)


class OldInstallFolderTests(Base):
    """D-P1: a 0.1 or 0.2 manifest has no created_dirs, so its folders are those its files live in.

    Known limit: a user folder of the same name that existed empty before the old install is
    removed when both tools leave.
    """

    def fresh_home(self, host):
        self.host_home(host).mkdir()
        return snapshot(self.host_home(host))

    def test_sibling_router_old_manifest_folders_are_inherited(self):
        router = old_tree("ROUTER_V02")
        for host in HOSTS:
            with self.subTest(host=host):
                before = self.fresh_home(host)
                self.run_tree(router, host)
                self.assertNotIn("created_dirs", json.loads(
                    (self.host_home(host) / "router/install-manifest.json").read_text()))
                self.run_install(host)
                self.assertIn("skills", self.manifest(host)["created_dirs"])
                self.run_tree(router, host, "--uninstall")
                self.run_install(host, "--uninstall", "--purge")
                backups = list(self.host_home(host).glob("*.router-backup-*"))  # Router 0.2 never purges.
                self.assertEqual(len(backups), 1)
                backups[0].unlink()
                self.assertEqual(snapshot(self.host_home(host)), before)

    def test_sibling_router_old_manifest_keeps_a_folder_it_does_not_share(self):
        router = old_tree("ROUTER_V02")
        for host in HOSTS:
            with self.subTest(host=host):
                self.fresh_home(host)
                self.run_tree(router, host)
                self.run_install(host)
                created = self.manifest(host)["created_dirs"]
                self.assertNotIn("router", created)
                self.assertNotIn("hooks", created)
                self.run_tree(router, host, "--uninstall")
                self.run_install(host, "--uninstall")

    def test_own_upgrade_from_old_install_derives_its_folders(self):
        for name in ("HARNESS_V01", "HARNESS_V02"):
            tree = old_tree(name)
            for host in HOSTS:
                with self.subTest(tree=name, host=host):
                    shutil.rmtree(self.host_home(host), ignore_errors=True)
                    before = self.fresh_home(host)
                    self.run_tree(tree, host)
                    self.run_install(host)
                    created = self.manifest(host)["created_dirs"]
                    self.assertIn("skills", created)
                    self.assertIn("harness", created)
                    self.run_install(host, "--uninstall", "--purge")
                    self.assertEqual(snapshot(self.host_home(host)), before)

    def test_old_folder_that_is_not_empty_is_kept(self):
        tree = old_tree("HARNESS_V02")
        for host in HOSTS:
            with self.subTest(host=host):
                self.run_tree(tree, host)
                self.run_install(host)
                mine = self.host_home(host) / "skills/mine.txt"
                mine.write_text("user\n")
                self.run_install(host, "--uninstall")
                self.assertEqual(mine.read_text(), "user\n")


class SettingsModeTests(Base):
    """D-P2: a rewrite keeps an existing settings file's mode; a new one is 0600, as Router 0.3."""

    @skip_unless_posix_modes("settings modes 0o644, 0o640 and 0o600 kept across rewrites")
    def test_rewrites_keep_the_existing_mode(self):
        for host in HOSTS:
            for mode in (0o644, 0o640, 0o600):
                with self.subTest(host=host, mode=oct(mode)):
                    shutil.rmtree(self.host_home(host), ignore_errors=True)
                    self.host_home(host).mkdir()
                    path = self.settings_path(host)
                    path.write_bytes(HAND_FORMATTED)
                    path.chmod(mode)
                    self.run_install(host)
                    self.assertEqual(stat.S_IMODE(path.stat().st_mode), mode)
                    self.run_install(host, "--statusline")
                    self.run_install(host)
                    self.assertEqual(stat.S_IMODE(path.stat().st_mode), mode)
                    self.run_install(host, "--uninstall")
                    self.assertEqual(path.read_bytes(), HAND_FORMATTED)
                    self.assertEqual(stat.S_IMODE(path.stat().st_mode), mode)

    @skip_unless_posix_modes("a settings mode 0o644 kept after an edit")
    def test_a_rewrite_by_a_second_run_after_an_edit_keeps_the_mode(self):
        for host in HOSTS:
            with self.subTest(host=host):
                path = self.settings_path(host)
                path.parent.mkdir()
                path.write_bytes(b'{"a": 1}\n')
                path.chmod(0o644)
                self.run_install(host)
                settings = json.loads(path.read_text())
                settings["hooks"] = {}
                path.write_text(json.dumps(settings))
                path.chmod(0o644)
                self.run_install(host)
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)
                shutil.rmtree(path.parent)

    def test_kept_mode_drops_setuid_setgid_and_sticky(self):
        # The policy itself, with a stand-in stat result, so a host without POSIX modes checks it too.
        body = (ROOT / "install.sh").read_text(encoding="utf-8").split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
        function = next(node for node in ast.parse(body).body
                        if isinstance(node, ast.FunctionDef) and node.name == "settings_mode")

        class Settings:
            def __init__(self, mode):
                self.mode = mode

            def stat(self):
                if self.mode is None:
                    raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), "settings.json")
                return types.SimpleNamespace(st_mode=stat.S_IFREG | self.mode)

        cases = ((0o4640, 0o640), (0o2640, 0o640), (0o1640, 0o640), (0o7777, 0o777),
                 (0o644, 0o644), (0o640, 0o640), (None, 0o600))
        for given, expected in cases:
            with self.subTest(mode=None if given is None else oct(given)):
                scope = {"stat": stat, "settings_path": Settings(given)}
                exec(compile(ast.Module(body=[function], type_ignores=[]), "install.sh", "exec"), scope)
                self.assertEqual(oct(scope["settings_mode"]()), oct(expected))

    @skip_unless_posix_modes("setuid, setgid and sticky bits dropped from a kept settings mode")
    def test_rewrites_drop_setuid_setgid_and_sticky(self):
        for host in HOSTS:
            for mode in (0o4640, 0o2640, 0o1640, 0o6640):
                with self.subTest(host=host, mode=oct(mode)):
                    shutil.rmtree(self.host_home(host), ignore_errors=True)
                    self.host_home(host).mkdir()
                    path = self.settings_path(host)
                    path.write_bytes(HAND_FORMATTED)
                    try:
                        path.chmod(mode)
                    except OSError as error:
                        self.skipTest(f"cannot set {oct(mode)} on a file here: {error.strerror}")
                    if stat.S_IMODE(path.stat().st_mode) != mode:
                        self.skipTest(f"the OS did not keep {oct(mode)} on a file")
                    self.run_install(host)  # the install write
                    self.assertEqual(oct(stat.S_IMODE(path.stat().st_mode)), oct(0o640))
                    settings = json.loads(path.read_text())
                    settings["user"] = 1  # not the original any more: uninstall merges and rewrites
                    path.write_text(json.dumps(settings))
                    path.chmod(mode)
                    self.run_install(host, "--uninstall")
                    self.assertEqual(json.loads(path.read_text()).get("user"), 1)
                    self.assertEqual(oct(stat.S_IMODE(path.stat().st_mode)), oct(0o640))

    def test_restore_without_a_recorded_mode_drops_setuid_setgid_and_sticky(self):
        # The uninstall restore fallback, with a stand-in settings file, so every OS checks it.
        body = (ROOT / "install.sh").read_text(encoding="utf-8").split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
        function = next((node for node in ast.parse(body).body
                         if isinstance(node, ast.FunctionDef) and node.name == "restore_mode"), None)
        self.assertIsNotNone(function, "install.sh has no restore_mode()")

        class Settings:
            def __init__(self, mode):
                self.mode = mode

            def exists(self):
                return self.mode is not None

            def stat(self):
                if self.mode is None:
                    raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), "settings.json")
                return types.SimpleNamespace(st_mode=stat.S_IFREG | self.mode)

        cases = ((None, 0o4640, 0o640), (None, 0o2640, 0o640), (None, 0o1640, 0o640), (None, 0o7777, 0o777),
                 (None, 0o644, 0o644), (None, None, 0o600), (0o600, 0o4640, 0o600), (0o644, None, 0o644))
        for wanted, current, expected in cases:
            with self.subTest(wanted=wanted and oct(wanted), current=current and oct(current)):
                scope = {"stat": stat, "settings_path": Settings(current)}
                exec(compile(ast.Module(body=[function], type_ignores=[]), "install.sh", "exec"), scope)
                self.assertEqual(oct(scope["restore_mode"](wanted)), oct(expected))

    @skip_unless_posix_modes("setuid dropped when uninstall restores a settings file with no recorded mode")
    def test_uninstall_restore_drops_setuid(self):
        for host in HOSTS:
            with self.subTest(host=host):
                shutil.rmtree(self.host_home(host), ignore_errors=True)
                self.host_home(host).mkdir()
                path = self.settings_path(host)
                path.write_bytes(HAND_FORMATTED)
                try:
                    path.chmod(0o4640)
                except OSError as error:
                    self.skipTest(f"cannot set 0o4640 on a file here: {error.strerror}")
                if stat.S_IMODE(path.stat().st_mode) != 0o4640:
                    self.skipTest("the OS did not keep 0o4640 on a file")
                self.run_install(host)
                self.assertIsNone(self.manifest(host).get("settings_original_mode"))  # no mode recorded
                path.chmod(0o4640)
                self.run_install(host, "--uninstall")
                self.assertEqual(path.read_bytes(), HAND_FORMATTED)  # the restore, not a merge
                self.assertEqual(oct(stat.S_IMODE(path.stat().st_mode)), oct(0o640))

    @skip_unless_posix_modes("a new settings file is 0o600")
    def test_new_settings_file_is_private(self):
        for host in HOSTS:
            with self.subTest(host=host):
                self.run_install(host)
                self.assertEqual(stat.S_IMODE(self.settings_path(host).stat().st_mode), 0o600)
                shutil.rmtree(self.host_home(host))

    def test_router_v02_first_then_harness_uninstalled_first_restores_bytes_and_mode(self):
        router = old_tree("ROUTER_V02")
        # Router 0.2 writes text in the platform's native newline convention.
        original = (json.dumps({"zeta": 1, "alpha": [1, 2, 3]}, indent=2) + "\n").replace("\n", os.linesep).encode()
        for host in HOSTS:
            with self.subTest(host=host):
                shutil.rmtree(self.host_home(host), ignore_errors=True)
                self.host_home(host).mkdir()
                path = self.settings_path(host)
                path.write_bytes(original)
                path.chmod(0o644)
                self.run_tree(router, host)
                self.run_install(host)
                self.run_install(host, "--uninstall")
                self.run_tree(router, host, "--uninstall")
                self.assertEqual(path.read_bytes(), original)
                if posix_modes(self, "restored settings mode is 0o644"):
                    self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)


class SharedRoleSkewTests(Base):
    def roles(self, host):
        extension = ".md" if host == "claude" else ".toml"
        return [f"agents/{role}{extension}" for role in ROLES]

    def role_bytes(self, host):
        return {name: (self.host_home(host) / name).read_bytes() for name in self.roles(host)}

    def test_router_v02_first_then_harness(self):
        router = old_tree("ROUTER_V02")
        for host in HOSTS:
            with self.subTest(host=host):
                self.run_tree(router, host)
                before = self.role_bytes(host)
                result = self.run_install(host)
                self.assertEqual((result.stdout + result.stderr).count(SKEW), 1, result.stdout)
                self.assertEqual(self.role_bytes(host), before)
                files = self.manifest(host)["files"]
                for name, data in before.items():
                    self.assertEqual(files[name], sha(data))
                self.run_install(host)
                self.assertEqual(self.role_bytes(host), before)
                self.run_install(host, "--uninstall")
                self.assertEqual(self.role_bytes(host), before)

    def test_harness_first_then_router_v02_manifest_appears(self):
        router = old_tree("ROUTER_V02")
        scratch = self.root / "router-home"
        scratch.mkdir()
        for host in HOSTS:
            with self.subTest(host=host):
                self.run_install(host)
                self.run_tree(router, host, home=scratch)
                top = self.host_home(host)
                claimed = {}
                for name in self.roles(host):
                    data = (scratch / f".{host}" / name).read_bytes()
                    (top / name).write_bytes(data)
                    claimed[name] = {"sha256": sha(data)}
                sibling = top / "router/install-manifest.json"
                sibling.parent.mkdir(parents=True)
                sibling.write_text(json.dumps({"version": 1, "files": claimed, "hooks": []}))
                before = self.role_bytes(host)
                result = self.run_install(host)
                self.assertEqual(result.stdout.count(SKEW), 1, result.stdout)
                self.assertEqual(self.role_bytes(host), before)
                sibling.unlink()
                self.run_install(host)
                for name in self.roles(host):
                    source = ROOT / ("agents" if host == "claude" else "codex/agents") / Path(name).name
                    self.assertEqual((top / name).read_bytes(), source.read_bytes(), "role not updated after the claim dropped")
                sibling.write_text(json.dumps({"version": 1, "files": {
                    name: {"sha256": sha((top / name).read_bytes())} for name in self.roles(host)}, "hooks": []}))
                self.run_install(host, "--uninstall")
                for name in self.roles(host):
                    self.assertTrue((top / name).is_file(), f"uninstall removed Router-claimed {name}")


class PurgeAndVersionTests(Base):
    def make_backups(self):
        for host in HOSTS:
            path = self.settings_path(host)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(HAND_FORMATTED)
        (self.host_home("codex") / "config.toml").write_bytes(b'model = "example"\n')
        self.run_install("claude")
        self.run_install("codex", "--judge-permissions")
        return sorted(self.home.rglob("*.harness-backup-*"))

    @skip_unless_posix_modes("backups are 0o600")
    def test_backups_are_private(self):
        backups = self.make_backups()
        names = {path.name.split(".harness-backup-")[0] for path in backups}
        self.assertEqual(names, {"settings.json", "hooks.json", "config.toml"})
        for path in backups:
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600, path.name)

    def test_without_purge_backups_are_kept(self):
        backups = self.make_backups()
        for host in HOSTS:
            self.run_install(host, "--uninstall")
        for path in backups:
            self.assertTrue(path.is_file(), f"backup removed without --purge: {path}")
        self.assertEqual(self.settings_path("claude").read_bytes(), HAND_FORMATTED)

    def test_purge_removes_and_reports_only_backups(self):
        backups = self.make_backups()
        outside = self.root / "outside-backup"
        outside.write_text("not ours\n")
        link = self.host_home("claude") / "settings.json.harness-backup-link"
        link.symlink_to(outside)
        before = self.tree_state()
        dry = self.run_install("claude", "--purge", "--dry-run").stdout
        self.assertEqual(self.tree_state(), before)
        self.assertIn("Would remove backup:", dry)
        installed = self.settings_path("claude").read_bytes()
        output = self.run_install("claude", "--purge").stdout
        claude_backups = [path for path in backups if path.parent == self.host_home("claude")]
        for path in claude_backups:
            self.assertIn(f"Removed backup: {path}", output)
            self.assertFalse(path.exists())
        self.assertIn(f"Removed {len(claude_backups)} backup", output)
        self.assertEqual(self.settings_path("claude").read_bytes(), installed, "--purge alone changed settings")
        self.assertTrue((self.host_home("claude") / "harness/install-manifest.json").is_file())
        self.assertTrue(link.is_symlink())
        self.assertEqual(outside.read_text(), "not ours\n")
        output = self.run_install("codex", "--uninstall", "--purge").stdout
        self.assertEqual(self.settings_path("codex").read_bytes(), HAND_FORMATTED)
        self.assertEqual(list(self.host_home("codex").glob("*.harness-backup-*")), [])
        self.assertIn("Removed backup:", output)

    def test_version_missing_unreadable_or_empty_fails_in_one_line(self):
        dist = self.root / "dist"
        shutil.copytree(ROOT, dist, ignore=shutil.ignore_patterns(".git", "__pycache__", "tests"))
        version = dist / "VERSION"
        for case in ("missing", "unreadable", "empty"):
            if case == "unreadable" and not posix_modes(self, "a mode 000 VERSION"):
                continue
            with self.subTest(case=case):
                if version.exists():
                    version.chmod(0o644)
                    version.unlink()
                if case == "unreadable":
                    version.write_text("0.3.0\n")
                    version.chmod(0)
                elif case == "empty":
                    version.write_text("\n")
                result = self.run_tree(dist, "claude", code=1)
                self.assertEqual(len(result.stderr.splitlines()), 1, result.stderr)
                self.assertTrue(result.stderr.startswith(f"harness: {version}: "), result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertFalse(self.host_home("claude").exists())
        version.chmod(0o644)


if __name__ == "__main__":
    unittest.main()
