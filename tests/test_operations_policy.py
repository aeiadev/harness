import hashlib
import json
import os
from pathlib import Path
from unittest.mock import patch
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks" / "guard"))
import operations_policy as op

SAMPLE = json.loads((Path(__file__).with_name("fixtures") / "ssh-operations.json").read_text())["policy"]


class PolicyFixture:
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.project = self.base / "site"
        (self.project / ".harness").mkdir(parents=True)
        self.policy = self.project / ".harness" / "operations.json"
        self.policy.write_text(json.dumps(SAMPLE))
        self.config = self.base / "config"
        self.env = {"XDG_CONFIG_HOME": str(self.config), "HOME": str(self.base)}

    def bind(self, mode="enforce"):
        return op.bind_project(str(self.project), str(self.policy), mode, environ=self.env)

    def resolve(self, cwd=None):
        return op.resolve_binding(str(cwd or self.project), environ=self.env)


@unittest.skipIf(os.name == "nt", "policy activation requires POSIX filesystem capabilities")
class PolicyTests(PolicyFixture, unittest.TestCase):
    def test_regular_file_terminal_directory_components_are_not_erased(self):
        for mode in ("report", "enforce"):
            self.bind(mode)
            registry = self.config / "harness/operations-bindings.json"
            before = (registry.read_bytes(), registry.stat().st_ino)
            for suffix in ("/", "/.", "/./", "//", "/../operations.json"):
                with self.subTest(mode=mode, suffix=suffix):
                    with self.assertRaises(op.PolicyError):
                        op._regular_bytes(str(self.policy) + suffix, op.POLICY_LIMIT, "policy")
                    with self.assertRaises(op.PolicyError):
                        op.bind_project(str(self.project), str(self.policy) + suffix,
                                        mode, environ=self.env)
                    self.assertEqual((registry.read_bytes(), registry.stat().st_ino), before)

    def test_empty_project_does_not_mean_current_directory(self):
        for mode in ("report", "enforce"):
            self.bind(mode)
            registry = self.config / "harness/operations-bindings.json"
            before = (registry.read_bytes(), registry.stat().st_ino)
            with patch.object(op.os, "getcwd", return_value=str(self.project)):
                with self.assertRaises(op.PolicyError):
                    op.bind_project("", str(self.policy), mode, environ=self.env)
                with self.assertRaises(op.PolicyError):
                    op.unbind_project("", environ=self.env)
            self.assertEqual((registry.read_bytes(), registry.stat().st_ino), before)

    def test_missing_config_parent_component_cannot_hide_existing_registry(self):
        self.bind()
        registry = self.config / "harness/operations-bindings.json"
        for content in (b"{", b'{"schema_version":1,"bindings":[],"padding":"' +
                        b"x" * (op.REGISTRY_LIMIT - len(b'{"schema_version":1,"bindings":[],"padding":""}')) + b'"}'):
            registry.write_bytes(content)
            for mode in ("report", "enforce"):
                absent = self.base / "new-config-parent"
                env = dict(self.env, XDG_CONFIG_HOME=str(absent) + "/../config")
                before = sorted(self.base.rglob("*"))
                with self.subTest(mode=mode, size=len(content)):
                    with self.assertRaises(op.PolicyError):
                        op.bind_project(str(self.project), str(self.policy), mode, environ=env)
                    self.assertEqual(sorted(self.base.rglob("*")), before)
                    self.assertEqual(registry.read_bytes(), content)

    def test_missing_config_prefix_validates_entire_uncreated_tail(self):
        for mode in ("report", "enforce"):
            for kind in ("file", "fifo", "symlink", "long-name", "long-path"):
                with tempfile.TemporaryDirectory(dir=self.base) as folder:
                    case = Path(folder)
                    absent, bad = case / "new", case / "bad"
                    if kind == "file":
                        bad.write_bytes(b"unrelated")
                    elif kind == "fifo":
                        os.mkfifo(bad)
                    elif kind == "symlink":
                        bad.symlink_to(case / "missing")
                    if kind == "long-name":
                        raw = str(absent / ("x" * (os.pathconf(case, "PC_NAME_MAX") + 1)))
                    elif kind == "long-path":
                        raw = str(absent) + ("/" + "x" * 120) * (os.pathconf(case, "PC_PATH_MAX") // 120 + 1)
                    else:
                        raw = str(absent) + "/../bad/../config"
                    env = dict(self.env, XDG_CONFIG_HOME=raw)
                    before = sorted(case.rglob("*"))
                    with self.subTest(mode=mode, kind=kind):
                        with self.assertRaises(op.PolicyError):
                            op.bind_project(str(self.project), str(self.policy), mode, environ=env)
                        self.assertEqual(sorted(case.rglob("*")), before)

    def test_registry_writer_reserves_lock_and_temporary_name_limits(self):
        actual_pathconf = op.os.pathconf
        for mode in ("report", "enforce"):
            for existing in ("missing", "directory", "registry"):
                for limit in (24, 25, 30, 44, 45, 64, 255):
                    with tempfile.TemporaryDirectory(dir=self.base) as folder:
                        config = Path(folder) / "config"
                        env = dict(self.env, XDG_CONFIG_HOME=str(config))
                        if existing == "directory":
                            (config / "harness").mkdir(parents=True, mode=0o700)
                        elif existing == "registry":
                            op.bind_project(str(self.project), str(self.policy), mode, environ=env)
                        before = sorted(Path(folder).rglob("*"))
                        contents = {p: p.read_bytes() for p in before if p.is_file()}
                        def bounds(path, name):
                            return limit if name == "PC_NAME_MAX" else actual_pathconf(path, name)
                        with self.subTest(mode=mode, existing=existing, limit=limit), patch.object(op.os, "pathconf", side_effect=bounds):
                            if limit < 45:
                                with self.assertRaises(op.PolicyError):
                                    op.bind_project(str(self.project), str(self.policy), mode, environ=env)
                                self.assertEqual(sorted(Path(folder).rglob("*")), before)
                                self.assertEqual({p: p.read_bytes() for p in contents}, contents)
                            else:
                                op.bind_project(str(self.project), str(self.policy), mode, environ=env)
                                self.assertEqual(op.resolve_binding(str(self.project), environ=env)["mode"], mode)

    def test_registry_roots_must_be_kernel_path_spellings(self):
        self.bind()
        registry = self.config / "harness/operations-bindings.json"
        original = json.loads(registry.read_bytes())
        for mode in ("report", "enforce"):
            for character in (chr(0), chr(0xd800), chr(0xdc00)):
                for field in ("project_root", "binding_key", "stored_with_key"):
                    doc = json.loads(json.dumps(original))
                    record = doc["bindings"][0]
                    record.pop("binding_key", None)
                    record["mode"] = mode
                    if field == "stored_with_key":
                        record["binding_key"] = str(self.project)
                        record["project_root"] = str(self.project) + character
                    else:
                        record[field] = str(self.project) + character
                    raw = json.dumps(doc).encode()
                    registry.write_bytes(raw)
                    before = (raw, registry.stat().st_ino)
                    with self.subTest(mode=mode, character=ord(character), field=field):
                        result = self.resolve()
                        self.assertEqual((result["mode"], result["error_id"]),
                                         ("unknown", "registry-invalid"))
                        with self.assertRaises(op.PolicyError):
                            op.load_registry(self.env)
                        with self.assertRaises(op.PolicyError):
                            self.bind(mode)
                        self.assertEqual((registry.read_bytes(), registry.stat().st_ino), before)

    def test_filesystem_limit_errors_are_bounded_policy_errors(self):
        self.bind()
        registry = self.config / "harness/operations-bindings.json"
        before = (registry.read_bytes(), registry.stat().st_ino)
        for mode in ("report", "enforce"):
            for action in (lambda: self.bind(mode),
                           lambda: op.unbind_project(str(self.project), environ=self.env)):
                with self.subTest(mode=mode), patch.object(op.os, "pathconf", side_effect=ValueError("unsupported name")):
                    with self.assertRaises(op.PolicyError):
                        action()
                    self.assertEqual((registry.read_bytes(), registry.stat().st_ino), before)

    def test_valid_and_drift(self):
        self.bind()
        result = self.resolve()
        self.assertEqual((result["mode"], result["error_id"]), ("enforce", None))
        self.assertEqual(result["policy"], SAMPLE)
        self.policy.write_text(self.policy.read_text() + " ")
        self.assertEqual(self.resolve()["error_id"], "policy-digest-drift")

    def test_missing_symlink_fifo_and_ancestor(self):
        self.bind("report")
        self.policy.unlink()
        self.assertEqual(self.resolve()["error_id"], "missing-policy")
        self.policy.symlink_to(self.base / "target")
        self.assertEqual(self.resolve()["error_id"], "policy-symlink")
        self.policy.unlink()
        os.mkfifo(self.policy)
        self.assertEqual(self.resolve()["error_id"], "policy-fifo")
        self.policy.unlink()
        (self.project / ".harness").rmdir()
        (self.project / ".harness").symlink_to(self.base)
        self.assertEqual(self.resolve()["error_id"], "policy-ancestor-escape")

    def test_roots_and_registry_corruption(self):
        self.bind()
        nested = self.project / "nested"
        (nested / ".git").mkdir(parents=True)
        self.assertEqual(self.resolve(nested)["mode"], "off")
        self.assertEqual(self.resolve(self.base)["mode"], "off")
        alias = self.base / "alias"
        alias.symlink_to(self.project)
        self.assertEqual(self.resolve(alias)["mode"], "enforce")
        registry = self.config / "harness" / "operations-bindings.json"
        registry.write_bytes(b"{")
        self.assertEqual(self.resolve()["mode"], "unknown")
        self.assertEqual(self.resolve()["error_id"], "registry-invalid")

    def test_limits(self):
        with self.assertRaises(op.PolicyError):
            op.strict_load(b" " * 16385)
        self.bind()
        registry = self.config / "harness" / "operations-bindings.json"
        registry.write_bytes(b" " * 65537)
        self.assertEqual(self.resolve()["error_id"], "registry-size-limit")

    def test_registry_duplicate_keys_and_private_mode(self):
        self.bind()
        registry = self.config / "harness" / "operations-bindings.json"
        registry.write_bytes(b'{"schema_version":1,"schema_version":1,"bindings":[]}')
        self.assertEqual(self.resolve()["error_id"], "registry-invalid")
        registry.write_text('{"schema_version":1,"bindings":[]}')
        registry.chmod(0o644)
        self.assertEqual(self.resolve()["error_id"], "registry-private-mode")

    def test_wrong_project_is_recoverable(self):
        self.bind("report")
        registry = self.config / "harness" / "operations-bindings.json"
        stored = json.loads(registry.read_text())
        stored["bindings"][0]["project_root"] = str(self.base / "other")
        registry.write_text(json.dumps(stored))
        result = self.resolve()
        self.assertEqual((result["mode"], result["error_id"]), ("report", "wrong-project"))

    def test_enrolled_non_git_and_no_inheritance(self):
        self.bind()
        child = self.project / "subdir"
        child.mkdir()
        self.assertEqual(self.resolve(child)["mode"], "enforce")
        (child / ".git").write_text("gitdir: /tmp/other")
        self.assertEqual(self.resolve(child)["mode"], "off")

    def test_invalid_rebind_preserves_existing_bytes(self):
        self.bind()
        registry = self.config / "harness" / "operations-bindings.json"
        before = registry.read_bytes()
        self.policy.write_bytes(b'{"schema_version":1,}')
        with self.assertRaises(op.PolicyError):
            self.bind("report")
        self.assertEqual(registry.read_bytes(), before)

    def test_accepted_registry_without_binding_key(self):
        self.bind("report")
        registry = self.config / "harness" / "operations-bindings.json"
        doc = json.loads(registry.read_bytes())
        doc["bindings"][0].pop("binding_key")
        registry.write_text(json.dumps(doc))
        self.assertEqual((self.resolve()["mode"], self.resolve()["error_id"]), ("report", None))
        self.bind("enforce")
        self.assertEqual(len(json.loads(registry.read_bytes())["bindings"]), 1)
        op.unbind_project(str(self.project), environ=self.env)
        self.assertEqual(json.loads(registry.read_bytes())["bindings"], [])

    def test_rejected_registry_does_not_create_lock(self):
        registry = self.config / "harness" / "operations-bindings.json"
        registry.parent.mkdir(parents=True, mode=0o700)
        registry.write_bytes(b"{")
        with self.assertRaises(op.PolicyError):
            self.bind()
        self.assertFalse((registry.parent / ".operations-bindings.lock").exists())

    def test_fifo_lock_refused(self):
        self.bind()
        lock = self.config / "harness" / ".operations-bindings.lock"
        lock.unlink()
        os.mkfifo(lock)
        with self.assertRaises(op.PolicyError):
            self.bind()
        self.assertTrue(lock.is_fifo())

    def test_registry_parent_swap_refused(self):
        self.bind("report")
        registry = self.config / "harness" / "operations-bindings.json"
        moved = self.base / "moved"
        original = op._registry_dir
        def swap(env):
            value = original(env)
            registry.parent.rename(moved)
            registry.parent.mkdir(mode=0o700)
            return value
        with patch.object(op, "_registry_dir", swap), self.assertRaises(op.PolicyError):
            self.bind("enforce")
        self.assertEqual(json.loads((moved / registry.name).read_bytes())["bindings"][0]["mode"], "report")
        self.assertFalse(registry.exists())

    def test_inaccessible_registry_namespace_is_not_absence(self):
        for mode in ("report", "enforce"):
            self.bind(mode)
            registry = self.config / "harness" / "operations-bindings.json"
            raw = registry.read_bytes()
            for present in (True, False):
                if not present:
                    registry.unlink()
                for parent in (registry.parent, self.config):
                    with self.subTest(mode=mode, present=present, parent=parent.name):
                        parent.chmod(0)
                        try:
                            result = self.resolve()
                            self.assertEqual(result["mode"], "unknown")
                            self.assertIsNone(result["policy"])
                            self.assertIsNotNone(result["error_id"])
                            with self.assertRaises(op.PolicyError):
                                op.load_registry(self.env)
                        finally:
                            parent.chmod(0o700)
                if not present:
                    registry.write_bytes(raw)
                    registry.chmod(0o600)

    def test_non_directory_registry_ancestors_are_not_absence(self):
        for depth in (0, 1, 2):
            for kind in ("regular", "fifo", "symlink"):
                with self.subTest(depth=depth, kind=kind):
                    base = self.base / (kind + str(depth))
                    parts = [base, base / "config", base / "config" / "harness"]
                    ancestor = parts[depth]
                    ancestor.parent.mkdir(parents=True, exist_ok=True)
                    if kind == "regular":
                        ancestor.write_bytes(b"not a directory")
                    elif kind == "fifo":
                        os.mkfifo(ancestor)
                    else:
                        ancestor.symlink_to(self.base / "absent")
                    env = dict(self.env, XDG_CONFIG_HOME=str(parts[1]))
                    result = op.resolve_binding(str(self.project), environ=env)
                    self.assertEqual(result["mode"], "unknown")
                    self.assertEqual(result["error_id"], "registry-ancestor-escape")

    def test_oversized_prospective_mutations_leave_inventory_unchanged(self):
        self.bind("report")
        registry = self.config / "harness" / "operations-bindings.json"
        lock = registry.parent / ".operations-bindings.lock"
        stored = json.loads(registry.read_bytes())
        other = self.base / "other"
        (other / ".harness").mkdir(parents=True)
        other_policy = other / ".harness" / "operations.json"
        other_policy.write_bytes(self.policy.read_bytes())
        # Compact input fits, but preserving this user data in output does not.
        stored["user_note"] = [0] * 12000
        raw = json.dumps(stored, separators=(",", ":")).encode()
        self.assertLess(len(raw), op.REGISTRY_LIMIT)
        def inventory():
            return sorted((str(p.relative_to(self.base)), p.stat().st_mode,
                           hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None)
                          for p in self.base.rglob("*"))
        for has_lock in (True, False):
            if not has_lock:
                lock.unlink()
            for action in (lambda: self.bind("enforce"),
                           lambda: op.bind_project(str(other), str(other_policy), "report", environ=self.env),
                           lambda: op.unbind_project(str(self.project), environ=self.env)):
                registry.write_bytes(raw)
                before = inventory()
                with self.subTest(has_lock=has_lock, action=action), self.assertRaises(op.PolicyError) as caught:
                    action()
                self.assertEqual(caught.exception.error_id, "registry-size-limit")
                self.assertEqual(inventory(), before)

    def test_project_spelling_validated_before_canonicalization(self):
        bad = self.base / "bad"
        outside = self.base / "outside/child"
        outside.mkdir(parents=True)
        (outside.parent / "site").mkdir()
        raw = str(bad) + "/../site"
        registry = self.config / "harness/operations-bindings.json"
        for kind in ("file", "fifo", "dangling", "missing", "alias"):
            if kind == "file":
                bad.write_text("unrelated")
            elif kind == "fifo":
                os.mkfifo(bad)
            elif kind == "dangling":
                bad.symlink_to(self.base / "missing")
            elif kind == "alias":
                bad.symlink_to(outside, target_is_directory=True)
            try:
                for mode in ("report", "enforce"):
                    for action in (lambda: op.bind_project(raw, str(self.policy), mode, environ=self.env),
                                   lambda: op.unbind_project(raw, environ=self.env)):
                        self.bind(mode)
                        before = registry.read_bytes()
                        inode = registry.stat().st_ino
                        with self.subTest(kind=kind, mode=mode, action=action):
                            with self.assertRaises(op.PolicyError):
                                action()
                            self.assertEqual(registry.read_bytes(), before)
                            self.assertEqual(registry.stat().st_ino, inode)
            finally:
                if kind != "missing":
                    bad.unlink()

    def test_file_spelling_cannot_select_a_different_policy(self):
        outside = self.base / "outside/child"
        outside.mkdir(parents=True)
        (outside.parent / ".harness").mkdir()
        (outside.parent / ".harness/operations.json").write_bytes(self.policy.read_bytes())
        alias = self.project / "alias"
        alias.symlink_to(outside, target_is_directory=True)
        raw_file = str(alias) + "/../.harness/operations.json"
        for mode in ("report", "enforce"):
            with self.subTest(mode=mode):
                with self.assertRaises(op.PolicyError):
                    op.bind_project(str(self.project), raw_file, mode, environ=self.env)
                self.assertFalse(self.config.exists())

    def test_valid_project_and_policy_parent_components_remain_supported(self):
        child = self.project / "child"
        child.mkdir()
        raw_project = str(child) + "/.."
        raw_file = str(child) + "/../.harness/operations.json"
        entry = op.bind_project(raw_project, raw_file, "enforce", environ=self.env)
        self.assertEqual(entry["project_root"], str(self.project))
        self.assertEqual(self.resolve()["policy"], SAMPLE)
        self.assertEqual(op.unbind_project(raw_project, environ=self.env)["bindings"], [])

    def test_raw_config_ancestors_cannot_hide_before_parent_components(self):
        damaged = self.base / "damaged"
        env = dict(self.env, XDG_CONFIG_HOME=str(damaged) + "/../config")
        for kind in ("file", "fifo", "dangling-link", "unreadable"):
            if kind == "file":
                damaged.write_text("unrelated")
            elif kind == "fifo":
                os.mkfifo(damaged)
            elif kind == "dangling-link":
                damaged.symlink_to(self.base / "missing")
            else:
                damaged.mkdir(mode=0)
            try:
                with self.subTest(kind=kind):
                    actual = op.resolve_binding(str(self.project), environ=env)
                    self.assertEqual(actual["mode"], "unknown")
                    self.assertIsNone(actual["policy"])
                    self.assertEqual(actual["error_id"], "registry-ancestor-escape")
            finally:
                if kind == "unreadable":
                    damaged.chmod(0o700)
                    damaged.rmdir()
                else:
                    damaged.unlink()
        self.assertFalse(self.config.exists())

    def test_symlink_parent_components_cannot_relocate_registry_into_project(self):
        outside = self.base / "outside/leaf"
        outside.mkdir(parents=True)
        alias = self.project / "alias"
        alias.symlink_to(outside, target_is_directory=True)
        env = dict(self.env, XDG_CONFIG_HOME=str(alias) + "/../config")
        for mode in ("report", "enforce"):
            with self.subTest(mode=mode), self.assertRaises(op.PolicyError):
                op.bind_project(str(self.project), str(self.policy), mode, environ=env)
            self.assertFalse((self.project / "config").exists())
            self.assertFalse((outside.parent / "config").exists())

    def test_valid_directory_parent_components_remain_supported(self):
        valid = self.base / "valid"
        valid.mkdir()
        env = dict(self.env, XDG_CONFIG_HOME=str(valid) + "/../config")
        for mode in ("report", "enforce"):
            op.bind_project(str(self.project), str(self.policy), mode, environ=env)
            actual = op.resolve_binding(str(self.project), environ=env)
            self.assertEqual((actual["mode"], actual["error_id"]), (mode, None))
            self.assertEqual(actual["policy"], SAMPLE)

    def test_ancestor_symlink_introduced_while_locking_refuses_mutation(self):
        import fcntl
        original_flock = fcntl.flock
        moved = self.base / "moved-config"
        registry = self.config / "harness/operations-bindings.json"
        def replace_ancestor(fd, flags):
            self.config.rename(moved)
            self.config.symlink_to(moved, target_is_directory=True)
            return original_flock(fd, flags)
        for action in (lambda: self.bind("enforce"),
                       lambda: op.unbind_project(str(self.project), environ=self.env)):
            self.bind("report")
            before = registry.read_bytes()
            inode = registry.stat().st_ino
            try:
                with self.subTest(action=action), patch.object(fcntl, "flock", replace_ancestor):
                    with self.assertRaises(op.PolicyError) as caught:
                        action()
                    self.assertEqual(caught.exception.error_id, "registry-ancestor-escape")
                    self.assertEqual(registry.read_bytes(), before)
                    self.assertEqual(registry.stat().st_ino, inode)
                    self.assertEqual(sorted(p.name for p in registry.parent.iterdir()),
                                     [".operations-bindings.lock", "operations-bindings.json"])
            finally:
                if self.config.is_symlink():
                    self.config.unlink()
                    moved.rename(self.config)

    def test_closest_enrollment_wins_before_distant_ancestor_scan(self):
        inner = self.project.joinpath(*(["i"] * 180))
        (inner / ".harness").mkdir(parents=True)
        inner_policy = inner / ".harness/operations.json"
        inner_policy.write_bytes(self.policy.read_bytes())
        cwd = inner.joinpath(*(["c"] * 100))
        cwd.mkdir(parents=True)
        # Recursive rmtree holds one directory descriptor per level on macOS.
        # Remove only this known fixture, deepest first, before ordinary cleanup.
        def cleanup_deep_tree():
            inner_policy.unlink()
            (inner / ".harness").rmdir()
            current = cwd
            while current != self.project:
                current.rmdir()
                current = current.parent
        self.addCleanup(cleanup_deep_tree)
        for outer_mode, inner_mode in (("report", "enforce"), ("enforce", "report")):
            self.bind(outer_mode)
            op.bind_project(str(inner), str(inner_policy), inner_mode, environ=self.env)
            with self.subTest(outer=outer_mode, inner=inner_mode):
                actual = self.resolve(cwd)
                self.assertEqual(actual, {"mode": inner_mode, "policy": SAMPLE,
                                          "project_root": str(inner), "error_id": None})

    def test_unreadable_nested_roots_never_inherit_policy(self):
        nested = self.project / "nested"
        nested.mkdir()
        marker = nested / ".git"
        for mode in ("report", "enforce"):
            self.bind(mode)
            for kind in ("directory", "worktree", "absent"):
                if kind == "directory":
                    marker.mkdir()
                elif kind == "worktree":
                    marker.write_text("gitdir: /synthetic")
                nested.chmod(0)
                try:
                    with self.subTest(mode=mode, kind=kind):
                        result = self.resolve(nested)
                        self.assertEqual(result["mode"], mode)
                        self.assertIsNone(result["policy"])
                        self.assertEqual(result["error_id"], "root-scan-error")
                finally:
                    nested.chmod(0o700)
                    if kind == "directory":
                        marker.rmdir()
                    elif kind == "worktree":
                        marker.unlink()

    def test_overflow_registry_numbers_refuse_without_mutation(self):
        self.bind()
        registry = self.config / "harness" / "operations-bindings.json"
        original = registry.read_bytes().rstrip()
        (registry.parent / ".operations-bindings.lock").unlink()
        for value in (b"1e999", b"-1e999", b"[1e999]", b'{"nested":-1e999}'):
            raw = original[:-1] + b',"user_note":' + value + b'}'
            registry.write_bytes(raw)
            before = registry.stat()
            with self.subTest(value=value):
                self.assertEqual(self.resolve()["error_id"], "registry-invalid")
                for action in (lambda: self.bind("report"),
                               lambda: op.unbind_project(str(self.project), environ=self.env)):
                    with self.assertRaises(op.PolicyError) as caught:
                        action()
                    self.assertEqual(caught.exception.error_id, "registry-invalid")
                self.assertEqual(registry.read_bytes(), raw)
                self.assertEqual((registry.stat().st_ino, registry.stat().st_mtime_ns),
                                 (before.st_ino, before.st_mtime_ns))
                self.assertEqual([p.name for p in registry.parent.iterdir()], [registry.name])

    def test_unbinding_absent_entry_preserves_namespace_and_inode(self):
        def inventory():
            return sorted((str(p.relative_to(self.base)), p.stat().st_ino,
                           p.stat().st_mode, p.stat().st_mtime_ns,
                           p.read_bytes() if p.is_file() else None)
                          for p in self.base.rglob("*"))
        before = inventory()
        self.assertEqual(op.unbind_project(str(self.project), environ=self.env),
                         {"schema_version": 1, "bindings": []})
        self.assertEqual(inventory(), before)
        self.bind()
        other = self.base / "other"
        other.mkdir()
        registry = self.config / "harness" / "operations-bindings.json"
        (registry.parent / ".operations-bindings.lock").unlink()
        before = inventory()
        result = op.unbind_project(str(other), environ=self.env)
        self.assertEqual(result, json.loads(registry.read_bytes()))
        self.assertEqual(inventory(), before)

    def test_mutation_size_revalidated_against_concurrent_registry(self):
        self.bind("report")
        registry = self.config / "harness" / "operations-bindings.json"
        stored = json.loads(registry.read_bytes())
        stored["user_note"] = [0] * 12000
        concurrent = json.dumps(stored, separators=(",", ":")).encode()
        original = op._registry_dir
        def concurrent_update(env):
            registry.write_bytes(concurrent)
            return original(env)
        with patch.object(op, "_registry_dir", concurrent_update), self.assertRaises(op.PolicyError) as caught:
            self.bind("enforce")
        self.assertEqual(caught.exception.error_id, "registry-size-limit")
        self.assertEqual(registry.read_bytes(), concurrent)
        self.assertEqual(sorted(p.name for p in registry.parent.iterdir()),
                         [".operations-bindings.lock", "operations-bindings.json"])


class PortablePolicyTests(PolicyFixture, unittest.TestCase):
    def test_missing_registry_is_off(self):
        self.assertEqual(self.resolve(), {"mode": "off", "policy": None,
                                          "project_root": None, "error_id": None})

    def test_bad_policy_shapes(self):
        for raw in (b'{"schema_version":1,"schema_version":1}', b'{"schema_version":1,}',
                    json.dumps(dict(SAMPLE, schema_version=2)).encode(),
                    json.dumps(dict(SAMPLE, rules=[dict(SAMPLE["rules"][0], decision="permit")])).encode()):
            with self.subTest(raw=raw[:30]):
                with self.assertRaises(op.PolicyError):
                    op.strict_load(raw)
        bad = json.loads(json.dumps(SAMPLE))
        bad["rules"].append(dict(bad["rules"][0]))
        with self.assertRaises(op.PolicyError):
            op.validate_policy(bad)

    def test_unhashable_rule_values_have_bounded_error(self):
        with self.assertRaises(op.PolicyError) as caught:
            op.strict_load("not bytes")
        self.assertEqual(caught.exception.error_id, "policy-invalid")
        for field, value in (("transport", []), ("destination", {})):
            bad = json.loads(json.dumps(SAMPLE))
            bad["rules"][0][field] = value
            with self.subTest(field=field):
                with self.assertRaises(op.PolicyError) as caught:
                    op.strict_load(json.dumps(bad).encode())
                self.assertEqual(caught.exception.error_id, "policy-invalid")

    def test_missing_activation_without_dirfd_capability(self):
        original = os.open
        def unsupported(path, flags, *args, **kwargs):
            if kwargs.get("dir_fd") is not None:
                raise NotImplementedError("dir_fd")
            return original(path, flags, *args, **kwargs)
        with patch.object(os, "open", unsupported):
            self.assertEqual(self.resolve()["mode"], "off")

    def test_absence_at_each_namespace_level_without_posix_open(self):
        for created in (self.base, self.config, self.config / "harness"):
            created.mkdir(exist_ok=True, mode=0o700)
            before = sorted(self.base.rglob("*"))
            with self.subTest(level=created.name), patch.object(os, "open", side_effect=NotImplementedError):
                self.assertEqual(self.resolve()["mode"], "off")
            self.assertEqual(sorted(self.base.rglob("*")), before)


@unittest.skipUnless(os.name == "nt", "native Windows refusal contract")
class WindowsPolicyTests(PolicyFixture, unittest.TestCase):
    def test_activation_refuses_both_modes_without_writes(self):
        before = sorted(self.base.rglob("*"))
        for mode in ("report", "enforce"):
            with self.subTest(mode=mode), self.assertRaises(op.PolicyError) as caught:
                self.bind(mode)
            self.assertEqual(caught.exception.error_id, "unsupported-platform")
            self.assertEqual(sorted(self.base.rglob("*")), before)

    def test_existing_registry_is_unknown_and_unchanged(self):
        registry = self.config / "harness/operations-bindings.json"
        registry.parent.mkdir(parents=True)
        raw = b'{"schema_version":1,"bindings":[]}'
        registry.write_bytes(raw)
        self.assertEqual(self.resolve(), {"mode": "unknown", "policy": None,
                                         "project_root": None, "error_id": "unsupported-platform"})
        with self.assertRaises(op.PolicyError) as caught:
            op.load_registry(self.env)
        self.assertEqual(caught.exception.error_id, "unsupported-platform")
        self.assertEqual(registry.read_bytes(), raw)


if __name__ == "__main__":
    unittest.main()
