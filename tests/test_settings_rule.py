#!/usr/bin/env python3
"""The shared settings rule beside a fake Router 0.3 sibling: exact bytes and mode after every
install and uninstall order, the sibling's empty events and folders, a true no-op rerun, and
0.1 and 0.2 starts.

The fake sibling follows Router's installer rules (lane 3R18): its hook blocks, private
.router-backup-<ns> files and its manifest fields; it uninstalls by the shared rule. No Router
checkout, network or model is used. The 0.1 and 0.2 starts need HARNESS_V01 and HARNESS_V02
from old-trees.sh; a test that needs one fails when it is unset, it never skips.
"""
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
HOSTS = (("claude", "settings.json"), ("codex", "hooks.json"))
PLAIN = b'{\n\t"z": 2,\n\t"a": 1\n}'
EMPTY_STOP = b'{\n\t"z": 2,\n\t"hooks": {\n\t\t"Stop": []\n\t},\n\t"a": 1\n}'
MODE = 0o640
DONE = {"claude": "Harness installed. Restart Claude Code to load the agents, skills, and hooks.\n",
        "codex": ("Harness installed for Codex. Trust new or changed hooks in Codex (/hooks) before they run.\n"
                  "Optional judge session profile: bash install.sh --host codex --judge-permissions\n")}
UNINSTALLED = "Harness uninstalled. User additions and modified files were kept.\n"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canon(value):
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def mode_of(path):
    return stat.S_IMODE(path.lstat().st_mode)


def payload(host):
    """What this checkout installs on a host, as install.sh enumerates it."""
    files = {}
    agents = "agents" if host == "claude" else "codex/agents"
    for folder, destination in (("hooks", "harness/hooks"), ("statusline", "harness/statusline"),
                                (agents, "agents"), ("skills", "skills")):
        for path in sorted((ROOT / folder).rglob("*")):
            relative = path.relative_to(ROOT / folder)
            if any(part in ("__pycache__", ".git", ".DS_Store") for part in relative.parts) or \
                    path.suffix in (".pyc", ".pyo"):
                continue
            if folder == "agents" and relative == Path("SHARED.sha256"):
                continue
            if path.is_file():
                files[str(Path(destination) / relative)] = path.read_bytes()
    return files


class FakeRouter:
    """Router as Harness meets it. package=None writes a 0.2 manifest and 0.2 backups (no record,
    backups keep the settings mode); old="0.1" also leaves out had_hooks and existing_empty_events;
    record_sha=False writes the earlier 0.3 record (existed and backup only)."""

    FILES = {"router/bin/router": b"#!/bin/sh\n", "hooks/router/fake.py": b"print('fake router')\n",
             "skills/dispatch/SKILL.md": b"# dispatch\n"}
    EVENTS = ("PreToolUse", "Stop")

    def __init__(self, home, filename, package=VERSION, record_sha=True, old="0.2"):
        self.home, self.filename, self.package, self.record_sha, self.old = home, filename, package, record_sha, old
        self.settings = home / filename
        self.manifest_path = home / "router/install-manifest.json"
        self.block = {"matcher": "*", "hooks": [{"type": "command", "command": f"python3 {home}/hooks/router/fake.py"}]}

    def write(self, path, data, mode=None):
        """Router write_bytes: the given mode, else the mode of the file it replaces."""
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix=".router-", dir=path.parent)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
        if mode is not None:
            os.chmod(name, mode)
        elif path.exists():
            os.chmod(name, mode_of(path))
        os.replace(name, path)

    def save(self, value, original):
        if value == original and self.settings.exists():
            return None
        backup = None
        if self.settings.exists():
            backup = self.settings.with_name(f"{self.filename}.router-backup-{time.time_ns()}")
            shutil.copy2(self.settings, backup)
            if self.package is not None:
                os.chmod(backup, 0o600)
        self.write(self.settings, canon(value))
        return backup

    def sibling(self):
        path = self.home / "harness/install-manifest.json"
        return json.loads(path.read_text()) if path.is_file() else {}

    def sibling_original(self, sibling):
        """Ruling R-B: the sibling's record whatever it says; its backup only when it checks."""
        if "settings_original_existed" not in sibling:
            return None
        existed, name = sibling["settings_original_existed"], sibling.get("settings_original_backup")
        if existed is False or existed is None:
            return {"existed": existed, "backup": None, "sha256": None, "mode": None}
        if existed is not True or not isinstance(name, str) or not (self.home / name).is_file():
            return None
        data = (self.home / name).read_bytes()
        if sibling.get("settings_original_sha256") not in (None, digest(data)):
            return None
        copied = self.settings.with_name(f"{self.filename}.router-backup-{time.time_ns()}")
        self.write(copied, data, 0o600)
        return {"existed": True, "backup": copied.name, "sha256": digest(data),
                "mode": sibling.get("settings_original_mode")}

    def install(self):
        manifest = json.loads(self.manifest_path.read_text()) if self.manifest_path.exists() else {}
        sibling = self.sibling()
        original_bytes = self.settings.read_bytes() if self.settings.exists() else None
        original = json.loads(original_bytes) if original_bytes is not None else {}
        value = copy.deepcopy(original)
        hooks = value.setdefault("hooks", {})
        entries = list(manifest.get("hooks", []))
        for event in self.EVENTS:
            blocks = hooks.setdefault(event, [])
            if self.block not in blocks:
                blocks.append(copy.deepcopy(self.block))
                entries.append({"event": event, "block": copy.deepcopy(self.block)})
        created, held = list(manifest.get("created_dirs", [])), set()
        for relative in [*self.FILES, self.filename, "router/install-manifest.json"]:
            for folder in [str(parent) for parent in reversed(Path(relative).parents) if str(parent) != "."]:
                held.add(folder)
                if not (self.home / folder).exists() and folder not in created:
                    created.append(folder)
        if self.package is not None:
            created += [item for item in sibling.get("created_dirs", []) if item in held and item not in created]
        record = None
        if self.package is not None and "settings_original_sha256" not in manifest:
            record = self.sibling_original(sibling)
            if record is None and manifest and "package" not in manifest:
                # Upgraded from its own 0.1 or 0.2: its oldest backup, else unknown (its hooks are in).
                own = sorted(self.home.glob(f"{self.filename}.router-backup-*"),
                             key=lambda path: int(path.name.rsplit("-", 1)[1]))
                record = ({"existed": True, "backup": own[0].name, "sha256": digest(own[0].read_bytes()),
                           "mode": mode_of(own[0])} if own else
                          {"existed": None, "backup": None, "sha256": None, "mode": None})
            if record is None:
                record = ({"existed": True, "backup": None, "sha256": digest(original_bytes),
                           "mode": mode_of(self.settings), "current": True} if original_bytes is not None else
                          {"existed": False, "backup": None, "sha256": None, "mode": None})
        wrote = value != original or not self.settings.exists()
        backup = self.save(value, original)
        if record is not None and record.get("current") and backup is not None:
            record["backup"] = backup.name
        present = original.get("hooks", {})
        empty = manifest.get("existing_empty_events")
        if empty is None:
            empty = [event for event, blocks in present.items() if blocks == []]
            empty += [event for event in sibling.get("existing_empty_events", [])
                      if event in present and event not in empty]
        result = {"version": 1, "files": {relative: {"sha256": digest(data)} for relative, data in self.FILES.items()},
                  "hooks": entries,
                  "had_hooks": manifest["had_hooks"] if "had_hooks" in manifest else (
                      "hooks" in original and sibling.get("had_hooks") is not False),
                  "existing_empty_events": empty}
        if self.package is None and self.old == "0.1":
            del result["had_hooks"], result["existing_empty_events"]
        if self.package is not None:
            result.update(package=self.package, source="/fake/router", created_dirs=created,
                          installed_at=manifest.get("installed_at", datetime.now(timezone.utc).isoformat()))
            for key in ("existed", "backup", "sha256", "mode"):
                name = "settings_original_" + key
                if record is not None and (self.record_sha or key in ("existed", "backup")):
                    result[name] = record[key]
                elif name in manifest:
                    result[name] = manifest[name]
            written = digest(self.settings.read_bytes()) if wrote else manifest.get("settings_written_sha256")
            if written is not None:
                result["settings_written_sha256"] = written
        for relative, data in self.FILES.items():
            (self.home / relative).parent.mkdir(parents=True, exist_ok=True)
            (self.home / relative).write_bytes(data)
        self.write(self.manifest_path, canon(result))

    def uninstall(self):
        """The shared rule: the verified original when it equals what is left, else removal or canon."""
        manifest = json.loads(self.manifest_path.read_text())
        original = json.loads(self.settings.read_bytes()) if self.settings.exists() else {}
        value = copy.deepcopy(original)
        hooks = value.get("hooks", {})
        for entry in manifest["hooks"]:
            blocks = hooks.get(entry["event"], [])
            if entry["block"] in blocks:
                blocks.remove(entry["block"])
                if not blocks and entry["event"] not in manifest.get("existing_empty_events", []):
                    hooks.pop(entry["event"])
        if not hooks and not manifest.get("had_hooks", True):
            value.pop("hooks", None)
        name = manifest.get("settings_original_backup")
        data = (self.home / name).read_bytes() if isinstance(name, str) and (self.home / name).is_file() else None
        if data is not None and manifest.get("settings_original_sha256") == digest(data) and \
                canon(json.loads(data)) == canon(value):
            self.write(self.settings, data, manifest.get("settings_original_mode"))
        elif value in ({}, {"hooks": {}}) and manifest.get("settings_original_existed") is not True:
            self.settings.unlink(missing_ok=True)
        else:
            self.save(value, original)
        for relative, fingerprint in manifest["files"].items():
            path = self.home / relative
            if path.is_file() and {"sha256": digest(path.read_bytes())} == fingerprint:
                path.unlink()
        self.manifest_path.unlink()
        for relative in sorted(set(manifest.get("created_dirs", [])), key=lambda item: len(Path(item).parts),
                               reverse=True):
            try:
                (self.home / relative).rmdir()
            except OSError:
                pass


class RuleBase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="harness-rule-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.homes = {"claude": self.base / "home/.claude", "codex": self.base / "home/.codex"}
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith(("ROUTER_", "CLAUDE_", "CODEX_", "XDG_"))}
        self.env.update(HOME=str(self.base / "home"), CLAUDE_HOME=str(self.homes["claude"]),
                        CODEX_HOME=str(self.homes["codex"]), XDG_CONFIG_HOME=str(self.base / "config"),
                        XDG_STATE_HOME=str(self.base / "state"), PYTHONDONTWRITEBYTECODE="1")
        self.router = {host: FakeRouter(self.homes[host], filename) for host, filename in HOSTS}

    def settings(self, host):
        return self.homes[host] / dict(HOSTS)[host]

    def seed(self, data=PLAIN, mode=MODE):
        for host, _ in HOSTS:
            self.homes[host].mkdir(parents=True, exist_ok=True)
            self.settings(host).write_bytes(data)
            self.settings(host).chmod(mode)

    def backups(self, host, kind="harness"):
        return set(self.homes[host].glob(f"{dict(HOSTS)[host]}.{kind}-backup-*"))

    def harness(self, *options, tree=ROOT, hosts=("both",)):
        out = ""
        for host in hosts:
            result = subprocess.run(["bash", str(tree / "install.sh"), "--host", host, *options], env=self.env,
                                    text=True, capture_output=True, stdin=subprocess.DEVNULL, timeout=120)
            self.assertEqual((result.returncode, result.stderr), (0, ""), result.stdout + result.stderr)
            out += result.stdout
        return out

    def manifest(self, host):
        return json.loads((self.homes[host] / "harness/install-manifest.json").read_text())

    def writes(self, host):
        """How many shipped files an install run writes now."""
        return sum(1 for relative, data in payload(host).items()
                   if not ((self.homes[host] / relative).is_file() and (self.homes[host] / relative).read_bytes() == data))

    def installed(self, host, count, original=None, backup=None, removed=()):
        lines = f"Installing {count} files into {self.homes[host]}.\n"
        lines += f"Backed up original settings: {original}\n" if original else ""
        lines += f"Backup: {backup}\n" if backup else ""
        lines += "".join(f"Removed no-longer-shipped: {relative}\n" for relative in removed)
        return lines + DONE[host]

    def install_checked(self):
        """Run this checkout for both hosts and assert every printed line; returns the new backups."""
        counts = {host: self.writes(host) for host, _ in HOSTS}
        before = {host: self.backups(host) for host, _ in HOSTS}
        old = {host: (self.manifest(host)["files"] if (self.homes[host] / "harness/install-manifest.json").exists()
                      else {}) for host, _ in HOSTS}
        out = self.harness()
        expected, new = "", {}
        for host, _ in HOSTS:
            new[host] = self.backups(host) - before[host]
            # Two new backups: the copy of the recorded original came first, then the pre-write one.
            original = (self.homes[host] / self.manifest(host)["settings_original_backup"]
                        if len(new[host]) == 2 else None)
            rest = sorted(new[host] - {original})
            self.assertLessEqual(len(rest), 1, host)
            removed = [relative for relative in old[host] if relative not in self.manifest(host)["files"]
                       and not (self.homes[host] / relative).exists()]
            expected += self.installed(host, counts[host], original, rest[0] if rest else None, removed)
        self.assertEqual(out, expected)
        return new

    def assert_record(self, host, existed, data, mode, backup=None):
        manifest = self.manifest(host)
        record = {key: manifest.get(key, "absent") for key in (
            "settings_original_existed", "settings_original_sha256", "settings_original_mode")}
        self.assertEqual(record, {"settings_original_existed": existed,
                                  "settings_original_sha256": None if data is None else digest(data),
                                  "settings_original_mode": mode}, host)
        name = manifest.get("settings_original_backup", "absent")
        if data is None or existed is None:
            self.assertIsNone(name, host)
            return None
        path = self.homes[host] / name
        self.assertEqual((path.read_bytes(), oct(mode_of(path))), (data, oct(0o600)), host)
        if backup is not None:
            self.assertEqual(path, backup, host)
        return path

    def assert_original(self, data, mode):
        for host, _ in HOSTS:
            path = self.settings(host)
            if data is None:
                self.assertFalse(path.exists() or path.is_symlink(), f"{host}: settings left behind")
            else:
                self.assertEqual((path.read_bytes(), oct(mode_of(path))), (data, oct(mode)), host)

    def uninstall_harness(self, last, absent=None):
        """Harness --uninstall with every printed line asserted. last: no Router left in settings."""
        counts = {host: len(self.manifest(host)["files"]) for host, _ in HOSTS}
        records = {host: self.manifest(host) for host, _ in HOSTS}
        before = {host: self.backups(host) for host, _ in HOSTS}
        out = self.harness("--uninstall")
        expected = ""
        for host, filename in HOSTS:
            expected += f"Removing {counts[host]} unchanged files and owned settings entries.\n"
            new = sorted(self.backups(host) - before[host])
            if last and records[host].get("settings_original_existed") is not True and not self.settings(host).exists():
                reason = "absent before install" if records[host]["settings_original_existed"] is False else \
                    "original unknown and nothing left"
                expected += f"Removing {filename} ({reason})\n"
                self.assertEqual(new, [], host)
            elif last and absent is None:
                expected += f"Restoring {filename} from {self.homes[host] / records[host]['settings_original_backup']}\n"
                self.assertEqual(new, [], host)
            else:
                self.assertEqual(len(new), 1, host)
                expected += f"Backup: {new[0]}\n"
            expected += UNINSTALLED
        self.assertEqual(out, expected)

    def uninstall(self, order):
        for product in order:
            if product == "harness":
                self.uninstall_harness(last=not self.router["claude"].manifest_path.exists())
            else:
                for fake in self.router.values():
                    fake.uninstall()

    def folders(self):
        return {host: sorted(str(Path(base).relative_to(home)) for base, _, _ in os.walk(home))
                for host, home in self.homes.items()}

    def unreadable(self, path):
        if os.geteuid() == 0:
            self.skipTest("root reads a mode 000 file")
        path.chmod(0)
        self.addCleanup(path.chmod, 0o600)

    def old_tree(self, name):
        value = os.environ.get(name)
        self.assertTrue(value, f"{name} is unset; source old-trees.sh before running this test")
        tree = Path(value)
        self.assertTrue((tree / "install.sh").is_file(), f"{name} has no install.sh: {tree}")
        return tree


class RecordTheOriginal(RuleBase):
    """Fix A1, record: the first source that exists, on the first 0.3 run only."""

    def test_alone_records_the_current_bytes(self):
        self.seed()
        new = self.install_checked()
        for host, _ in HOSTS:
            self.assert_record(host, True, PLAIN, MODE, backup=next(iter(new[host])))
        self.install_checked()
        self.uninstall_harness(last=True)
        self.assert_original(PLAIN, MODE)

    def test_sibling_record_is_copied_and_restored(self):
        for record_sha in (True, False):
            with self.subTest(record_sha=record_sha):
                self.setUp()
                self.router = {host: FakeRouter(self.homes[host], name, record_sha=record_sha) for host, name in HOSTS}
                self.seed()
                for fake in self.router.values():
                    fake.install()
                written = {host: self.settings(host).read_bytes() for host, _ in HOSTS}
                counts = {host: self.writes(host) for host, _ in HOSTS}
                out = self.harness()
                expected = ""
                for host, _ in HOSTS:
                    new = self.backups(host)
                    self.assertEqual(sorted(path.read_bytes() for path in new), sorted([PLAIN, written[host]]), host)
                    copy_path = next(path for path in new if path.read_bytes() == PLAIN)
                    other = next(path for path in new if path != copy_path)
                    self.assert_record(host, True, PLAIN, MODE if record_sha else None, backup=copy_path)
                    expected += self.installed(host, counts[host], copy_path, other)
                self.assertEqual(out, expected)
                self.uninstall(("router", "harness"))
                for host, _ in HOSTS:
                    self.assertEqual(self.settings(host).read_bytes(), PLAIN, host)
                if record_sha:
                    self.assert_original(PLAIN, MODE)

    def test_sibling_backup_failing_its_hash_is_not_taken(self):
        self.seed()
        for host, fake in self.router.items():
            fake.install()
            backup = self.homes[host] / json.loads(fake.manifest_path.read_text())["settings_original_backup"]
            backup.write_bytes(b'{"tampered": 1}')
        current = {host: self.settings(host).read_bytes() for host, _ in HOSTS}
        new = self.install_checked()
        for host, _ in HOSTS:
            self.assert_record(host, True, current[host], MODE, backup=next(iter(new[host])))

    def test_unreadable_sibling_backup_is_no_source(self):
        self.seed()
        for host, fake in self.router.items():
            fake.install()
            self.unreadable(self.homes[host] / json.loads(fake.manifest_path.read_text())["settings_original_backup"])
        current = {host: self.settings(host).read_bytes() for host, _ in HOSTS}
        new = self.install_checked()
        for host, _ in HOSTS:
            self.assert_record(host, True, current[host], MODE, backup=next(iter(new[host])))

    def test_unreadable_old_backup_is_no_source(self):
        self.seed()
        self.harness(tree=self.old_tree("HARNESS_V02"), hosts=("claude", "codex"))
        current = {}
        for host, _ in HOSTS:
            (old,) = self.backups(host)
            self.unreadable(old)
            # A readable later backup is not tried: the unreadable oldest one falls to the current bytes.
            later = self.homes[host] / f"{dict(HOSTS)[host]}.harness-backup-later"
            later.write_bytes(b'{"later": 1}')
            later.chmod(0o600)
            os.utime(later, ns=(old.lstat().st_mtime_ns + 1_000_000_000,) * 2)
            current[host] = self.settings(host).read_bytes()
        new = self.install_checked()
        for host, _ in HOSTS:
            # The current bytes keep a copy only when this run rewrites the file (0.2 Codex hooks match).
            new[host] -= {self.homes[host] / f"{dict(HOSTS)[host]}.harness-backup-later"}
            self.assertLessEqual(len(new[host]), 1, host)
            kept = next(iter(new[host])).name if new[host] else None
            if kept:
                self.assertEqual(sorted(new[host])[0].read_bytes(), current[host], host)
            record = {key: self.manifest(host)[key] for key in (
                "settings_original_existed", "settings_original_backup", "settings_original_sha256",
                "settings_original_mode")}
            self.assertEqual(record, {"settings_original_existed": True, "settings_original_backup": kept,
                                      "settings_original_sha256": digest(current[host]),
                                      "settings_original_mode": 0o600}, host)

    def test_sibling_that_created_the_file_records_it_absent(self):
        for fake in self.router.values():
            fake.install()
        self.install_checked()
        for host, _ in HOSTS:
            self.assert_record(host, False, None, None)
        self.uninstall(("router", "harness"))
        self.assert_original(None, None)

    def check_old_harness_start(self, name):
        self.seed()
        tree = self.old_tree(name)
        self.harness(tree=tree, hosts=("claude", "codex"))
        old = {host: self.backups(host) for host, _ in HOSTS}
        for host, _ in HOSTS:
            self.assertEqual(len(old[host]), 1, f"{name} left no settings backup for {host}")
            backup = next(iter(old[host]))
            self.assertEqual((backup.read_bytes(), mode_of(backup)), (PLAIN, 0o600), host)
            self.assertEqual(mode_of(self.settings(host)), 0o600, f"{name} did not write settings 0600")
        self.install_checked()
        for host, _ in HOSTS:
            # Harness 0.x went first: its own backup is the original, and it is 0600.
            self.assert_record(host, True, PLAIN, 0o600, backup=next(iter(old[host])))
        self.uninstall_harness(last=True)
        self.assert_original(PLAIN, 0o600)

    def test_harness_v01_start_records_its_backup(self):
        self.check_old_harness_start("HARNESS_V01")

    def test_harness_v02_start_records_its_backup(self):
        self.check_old_harness_start("HARNESS_V02")

    def test_harness_v02_start_without_backup_is_unknown_and_removed(self):
        self.harness(tree=self.old_tree("HARNESS_V02"), hosts=("claude", "codex"))
        self.assertEqual([self.backups(host) for host, _ in HOSTS], [set(), set()])
        self.install_checked()
        for host, _ in HOSTS:
            self.assert_record(host, None, None, None)
        self.uninstall_harness(last=True)
        self.assert_original(None, None)

    def test_router_v02_start_takes_its_earliest_backup_by_name(self):
        self.router = {host: FakeRouter(self.homes[host], name, package=None) for host, name in HOSTS}
        self.seed()
        for host, fake in self.router.items():
            fake.install()
            (first,) = self.backups(host, "router")
            number = int(first.name.rsplit("-", 1)[1])
            later = first.with_name(f"{dict(HOSTS)[host]}.router-backup-{number + 1}")
            later.write_bytes(b'{"later": 1}')
            mine = self.homes[host] / f"{dict(HOSTS)[host]}.harness-backup-decoy"
            mine.write_bytes(b'{"decoy": 1}')
            os.utime(mine, ns=(number + 2, number + 2))
        old = {host: self.backups(host) for host, _ in HOSTS}
        counts = {host: self.writes(host) for host, _ in HOSTS}
        out = self.harness()
        expected = ""
        for host, _ in HOSTS:
            new = self.backups(host) - old[host]
            copies = [path for path in new if path.read_bytes() == PLAIN]
            self.assertEqual(len(copies), 1, f"{host}: no copy of the oldest Router backup")
            other = next(path for path in new if path != copies[0])
            self.assert_record(host, True, PLAIN, MODE, backup=copies[0])
            expected += self.installed(host, counts[host], copies[0], other)
        self.assertEqual(out, expected)
        # Router upgrades to 0.3 and takes Harness's verified record; then both leave.
        self.router = {host: FakeRouter(self.homes[host], name) for host, name in HOSTS}
        for fake in self.router.values():
            fake.install()
        self.uninstall(("router", "harness"))
        self.assert_original(PLAIN, MODE)


class Rulings(RuleBase):
    """Coordinator rulings R-A, R-B, R-C (W6-settings-rulings.md)."""

    def test_r_a_no_record_uninstall_derives_the_original(self):
        tree = self.old_tree("HARNESS_V02")
        for start in (PLAIN, None):
            with self.subTest(original=start):
                self.setUp()
                if start is not None:
                    self.seed(start)
                self.harness(tree=tree, hosts=("claude", "codex"))
                old = {host: sorted(self.backups(host)) for host, _ in HOSTS}
                counts = {host: len(self.manifest(host)["files"]) for host, _ in HOSTS}
                out = self.harness("--uninstall")
                expected = ""
                for host, filename in HOSTS:
                    expected += f"Removing {counts[host]} unchanged files and owned settings entries.\n"
                    if start is not None:  # A backup shows the file existed: the oldest one holds what is left.
                        expected += f"Restoring {filename} from {old[host][0]}\n"
                    else:  # No fact shows a file before 0.2: unknown, and nothing is left.
                        expected += f"Removing {filename} (original unknown and nothing left)\n"
                    expected += UNINSTALLED
                self.assertEqual(out, expected)
                # Harness 0.2 went first, so the restored file stays 0600 (the limit sentence).
                self.assert_original(start, 0o600)

    def test_r_b_sibling_unknown_record_is_inherited(self):
        # Fresh home, Router 0.2, Router upgraded to 0.3 (unknown), then Harness 0.3.
        self.router = {host: FakeRouter(self.homes[host], name, package=None) for host, name in HOSTS}
        for fake in self.router.values():
            fake.install()
        self.router = {host: FakeRouter(self.homes[host], name) for host, name in HOSTS}
        for host, fake in self.router.items():
            fake.install()
            self.assertIsNone(json.loads(fake.manifest_path.read_text())["settings_original_existed"], host)
        self.install_checked()
        for host, _ in HOSTS:
            self.assert_record(host, None, None, None)
        self.uninstall(("router", "harness"))
        self.assert_original(None, None)

    def test_r_b_mirror_harness_unknown_then_router(self):
        self.harness(tree=self.old_tree("HARNESS_V02"), hosts=("claude", "codex"))
        self.install_checked()
        for fake in self.router.values():
            fake.install()
        for host, fake in self.router.items():
            self.assertIsNone(json.loads(fake.manifest_path.read_text())["settings_original_existed"], host)
        self.uninstall(("harness", "router"))
        self.assert_original(None, None)

    def test_r_c_had_hooks_from_a_router_v01_sibling_backup(self):
        self.router = {host: FakeRouter(self.homes[host], name, package=None, old="0.1") for host, name in HOSTS}
        self.seed()
        for fake in self.router.values():
            fake.install()
        counts = {host: self.writes(host) for host, _ in HOSTS}
        out = self.harness()
        expected = ""
        for host, _ in HOSTS:
            new = self.backups(host)
            copies = [path for path in new if path.read_bytes() == PLAIN]
            self.assertEqual(len(copies), 1, host)
            expected += self.installed(host, counts[host], copies[0], next(path for path in new if path != copies[0]))
            self.assertIs(self.manifest(host)["had_hooks"], False, host)
            self.assert_record(host, True, PLAIN, MODE, backup=copies[0])
        self.assertEqual(out, expected)
        self.router = {host: FakeRouter(self.homes[host], name) for host, name in HOSTS}
        for fake in self.router.values():
            fake.install()
        self.uninstall(("router", "harness"))
        self.assert_original(PLAIN, MODE)

    def test_r_c_empty_events_from_a_router_v01_sibling_backup(self):
        self.router = {host: FakeRouter(self.homes[host], name, package=None, old="0.1") for host, name in HOSTS}
        self.seed(EMPTY_STOP)
        for fake in self.router.values():
            fake.install()
        for host, fake in self.router.items():
            self.assertNotIn("existing_empty_events", json.loads(fake.manifest_path.read_text()), host)
        self.harness()
        for host, _ in HOSTS:
            self.assertEqual(self.manifest(host)["existing_empty_events"], ["Stop"], host)
        self.router = {host: FakeRouter(self.homes[host], name) for host, name in HOSTS}
        for fake in self.router.values():
            fake.install()
        self.uninstall(("router", "harness"))
        self.assert_original(EMPTY_STOP, MODE)


class UninstallRestore(RuleBase):
    """Fix A1, uninstall: the verified original when it equals what is left, else canon or removal."""

    def check_orders(self, data, mode):
        for first, order in ((first, order) for first in ("router", "harness")
                             for order in (("harness", "router"), ("router", "harness"))):
            with self.subTest(install_first=first, uninstall=order):
                self.setUp()
                if data is not None:
                    self.seed(data, mode)
                if first == "router":
                    for fake in self.router.values():
                        fake.install()
                self.install_checked()
                if first == "harness":
                    for fake in self.router.values():
                        fake.install()
                self.uninstall(order)
                self.assert_original(data, mode)

    def test_both_uninstall_orders_give_back_bytes_and_mode(self):
        self.check_orders(PLAIN, MODE)

    def test_original_absent_file_is_removed_in_both_orders(self):
        self.check_orders(None, None)

    def test_recorded_mode_comes_back_without_group_or_other_write(self):
        self.seed(PLAIN, 0o777)
        for fake in self.router.values():
            fake.install()
        self.install_checked()
        for host, _ in HOSTS:
            self.assertEqual(self.manifest(host)["settings_original_mode"], 0o755, host)
        self.uninstall(("router", "harness"))
        self.assert_original(PLAIN, 0o755)

    def test_unreadable_own_backup_does_not_verify(self):
        self.seed()
        self.install_checked()
        for host, _ in HOSTS:
            self.unreadable(self.homes[host] / self.manifest(host)["settings_original_backup"])
        self.uninstall_harness(last=True, absent=True)
        for host, _ in HOSTS:
            path = self.settings(host)
            self.assertEqual((path.read_bytes(), oct(mode_of(path))), (canon({"z": 2, "a": 1}), oct(MODE)), host)

    def test_user_edit_after_install_survives(self):
        self.seed()
        self.install_checked()
        for host, _ in HOSTS:
            value = json.loads(self.settings(host).read_bytes())
            value["later_edit"] = True
            self.settings(host).write_text(json.dumps(value))
        self.uninstall_harness(last=True, absent=True)
        for host, _ in HOSTS:
            path = self.settings(host)
            # D-P2: a rewrite keeps the file's mode (the seeded 0640).
            self.assertEqual((path.read_bytes(), oct(mode_of(path))),
                             (canon({"z": 2, "a": 1, "later_edit": True}), oct(MODE)), host)


class SiblingFacts(RuleBase):
    """Fixes A2 and A3: the sibling's recorded empty events and created folders."""

    def test_user_empty_stop_survives_harness_after_router(self):
        for order in (("router", "harness"), ("harness", "router")):
            with self.subTest(uninstall=order):
                self.setUp()
                self.seed(EMPTY_STOP)
                for fake in self.router.values():
                    fake.install()
                self.install_checked()
                for host, _ in HOSTS:
                    self.assertIn("Stop", self.manifest(host)["existing_empty_events"], host)
                self.uninstall(order)
                self.assert_original(EMPTY_STOP, MODE)

    def test_folder_the_first_installer_created_goes_with_the_last(self):
        for order in (("router", "harness"), ("harness", "router")):
            for user_file in (False, True):
                with self.subTest(uninstall=order, user_file=user_file):
                    self.setUp()
                    self.seed()
                    before = self.folders()
                    for fake in self.router.values():
                        fake.install()
                    self.install_checked()
                    for host, _ in HOSTS:
                        self.assertIn("skills", self.manifest(host)["created_dirs"], host)
                        if user_file:
                            (self.homes[host] / "skills/mine.md").write_bytes(b"mine\n")
                    self.uninstall(order)
                    self.assert_original(PLAIN, MODE)
                    after = self.folders()
                    for host, _ in HOSTS:
                        self.assertEqual(after[host], sorted(before[host] + (["skills"] if user_file else [])), host)
                        if user_file:
                            self.assertEqual(sorted(os.listdir(self.homes[host] / "skills")), ["mine.md"])


class RerunIsNoOp(RuleBase):
    """Fix B parity: Harness moves its written hash only on its own settings write."""

    def test_rerun_after_a_sibling_settings_write_changes_nothing(self):
        self.seed()
        self.install_checked()
        for fake in self.router.values():
            fake.install()
        state = {host: ((self.homes[host] / "harness/install-manifest.json").read_bytes(),
                        self.settings(host).read_bytes()) for host, _ in HOSTS}
        for _ in range(2):
            self.install_checked()
            for host, _ in HOSTS:
                self.assertEqual(((self.homes[host] / "harness/install-manifest.json").read_bytes(),
                                  self.settings(host).read_bytes()), state[host], host)


if __name__ == "__main__":
    unittest.main()
