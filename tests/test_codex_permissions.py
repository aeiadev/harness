#!/usr/bin/env python3
"""Exercise reversible profile merges without changing a user's configuration."""

import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("judge_permissions", ROOT / "codex" / "permissions.py")
permissions = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(permissions)
FRAGMENT = (ROOT / "codex" / "judge-permissions.toml").read_bytes()


class PermissionTests(unittest.TestCase):
    def install(self, original=None, owned=None):
        return permissions.plan_install(original, FRAGMENT, owned)

    def test_round_trip_original_bytes(self):
        for original in (None, b"", b"# config", b"# config\n", b"# config\r\n",
                         b'default_permissions = "other"\r\n\r\n[permissions]\r\n'):
            with self.subTest(original=original):
                installed, owned, _ = self.install(original)
                self.assertIsNotNone(owned)
                self.assertEqual(permissions.plan_uninstall(installed, owned)[0], original)

    def test_dry_run_uninstall_message(self):
        installed, owned, _ = self.install()
        _, message = permissions.plan_uninstall(installed, owned, dry_run=True)
        self.assertEqual(message, "Would remove owned judge profile.")

    def test_only_named_profile_is_added(self):
        original = b'default_permissions = "other"\n[permissions]\n[permissions.other.network]\nenabled = true\n'
        installed, owned, _ = self.install(original)
        self.assertTrue(installed.startswith(original))
        self.assertEqual(installed.count(b"[permissions]"), 1)
        self.assertEqual(installed.count(b"default_permissions"), 1)
        self.assertNotIn('default_permissions', owned["block"])
        self.assertIn(b'[permissions.harness-judge.filesystem]', installed)

    def test_repeated_install_is_idempotent(self):
        first, owned, _ = self.install()
        second, repeated, _ = self.install(first, owned)
        self.assertEqual(first, second)
        self.assertEqual(owned, repeated)
        unowned, metadata, _ = self.install(first)
        self.assertEqual(unowned, first)
        self.assertIsNone(metadata)

    def test_existing_profile_is_never_overwritten(self):
        for original in (
            b'[permissions.harness-judge.network]\nenabled = true\n',
            b'["permissions"."harness-judge".network]\nenabled = true\n',
            b"['permissions'.'harness-judge']\nnetwork = { enabled = true }\n",
            b'permissions = { "harness-judge" = { network = { enabled = true } } }\n',
            b'permissions.harness-judge.network.enabled = true\n',
        ):
            with self.subTest(original=original):
                installed, owned, _ = self.install(original)
                self.assertEqual(installed, original)
                self.assertIsNone(owned)

    def test_inline_permissions_container_is_preserved(self):
        original = b'permissions = { other = { network = { enabled = true } } }\n'
        installed, owned, _ = self.install(original)
        self.assertEqual(installed, original)
        self.assertIsNone(owned)

    def test_user_appended_unrelated_config_is_preserved(self):
        for original in (b'model = "example"', b'model = "example"\n'):
            for leading_newline in (b"", b"\n"):
                with self.subTest(original=original, leading_newline=leading_newline):
                    installed, owned, _ = self.install(original)
                    appended = leading_newline + b'[projects."/workspace/example"]\ntrust_level = "trusted"\n'
                    result, _ = permissions.plan_uninstall(installed + appended, owned)
                    boundary = b"" if original.endswith(b"\n") or leading_newline else b"\n\n"
                    self.assertEqual(result, original + boundary + appended)
                    self.assertNotIn(b'harness-judge', result)

    def test_user_modified_or_extended_profile_is_preserved(self):
        installed, owned, _ = self.install()
        for changed in (
            installed.replace(b'enabled = false', b'enabled = true'),
            installed + b'allow_extra = true\n',
            installed + b'[permissions.harness-judge.extra]\nvalue = true\n',
        ):
            with self.subTest(changed=changed):
                self.assertEqual(permissions.plan_uninstall(changed, owned)[0], changed)

    def test_ownership_does_not_store_original_config(self):
        original = b'# private configuration marker\n'
        _, owned, _ = self.install(original)
        self.assertNotIn('private configuration marker', json.dumps(owned))

    def test_invalid_config_and_metadata_are_preserved(self):
        original = b'not valid TOML'
        self.assertEqual(self.install(original)[0], original)
        installed, owned, _ = self.install()
        owned["sha256"] = "bad"
        self.assertEqual(permissions.plan_uninstall(installed, owned)[0], installed)

    def test_parser_error_does_not_echo_config(self):
        _, _, message = self.install(b'private_marker! = true\n')
        self.assertNotIn('private_marker', message)

    def test_python_310_fallback(self):
        with patch.object(permissions, "tomllib", None):
            self.test_round_trip_original_bytes()
            self.test_only_named_profile_is_added()
            self.test_repeated_install_is_idempotent()
            self.test_existing_profile_is_never_overwritten()
            self.test_inline_permissions_container_is_preserved()
            self.test_user_appended_unrelated_config_is_preserved()
            self.test_user_modified_or_extended_profile_is_preserved()
            self.test_ownership_does_not_store_original_config()
            self.test_invalid_config_and_metadata_are_preserved()
            self.test_parser_error_does_not_echo_config()

    def test_python_310_multiline_refusal(self):
        original = b'developer_instructions = """\n[not_a_table]\n"""\n'
        with patch.object(permissions, "tomllib", None):
            installed, owned, _ = self.install(original)
            self.assertEqual(installed, original)
            self.assertIsNone(owned)


if __name__ == "__main__":
    unittest.main()
