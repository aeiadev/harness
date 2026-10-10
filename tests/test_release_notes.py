#!/usr/bin/env python3
"""Release notes extraction with literal version matching and exact errors."""
import re
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/release-notes.sh"


class ReleaseNotesTests(unittest.TestCase):
    def call(self, *args):
        return subprocess.run(["bash", str(SCRIPT), *map(str, args)],
                              text=True, capture_output=True, timeout=10)

    def assert_message(self, actual, expected):
        self.assertIsNotNone(re.fullmatch(re.escape(expected), actual))

    def test_fixture_sections_and_literal_versions(self):
        with tempfile.TemporaryDirectory(prefix="release-notes-") as temp:
            changelog = Path(temp) / "CHANGELOG.md"
            changelog.write_text("# Changes\n\n## 1.0\n\nFirst\n\n"
                                 "## 0.2.0\n\nMiddle\nMore\n\n"
                                 "## 0.2\nShort\n"
                                 "## .*\n\nLiteral\n\n"
                                 "## 0.1\n\nLast\n\n")
            for version, expected in (("1.0", "First\n"),
                                      ("0.2.0", "Middle\nMore\n"),
                                      ("0.2", "Short\n"),
                                      (".*", "Literal\n"),
                                      ("0.1", "Last\n")):
                with self.subTest(version=version):
                    result = self.call(version, changelog)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout, expected)
                    self.assertEqual(result.stderr, "")
            missing = self.call("absent", changelog)
            self.assertEqual(missing.returncode, 1)
            self.assertEqual(missing.stdout, "")
            self.assert_message(missing.stderr, "release-notes: no section for absent\n")
            absent_file = Path(temp) / "missing.md"
            result = self.call("1.0", absent_file)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, "")
            self.assert_message(result.stderr, f"release-notes: {absent_file}: cannot read\n")

    def test_backslash_in_version_is_literal(self):
        with tempfile.TemporaryDirectory(prefix="release-notes-") as temp:
            changelog = Path(temp) / "CHANGELOG.md"
            changelog.write_text("## a\\tb\n\nTab text\n\n## a\tb\n\nReal tab\n\n## c\\\\d\n\nDouble\n")
            for version, expected in (("a\\tb", "Tab text\n"), ("a\tb", "Real tab\n"),
                                      ("c\\\\d", "Double\n")):
                with self.subTest(version=version):
                    result = self.call(version, changelog)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout, expected)

    def test_usage(self):
        result = self.call()
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assert_message(result.stderr, "usage: release-notes.sh VERSION [CHANGELOG]\n")

    def test_real_changelog_default(self):
        content = (ROOT / "CHANGELOG.md").read_text()
        section = content.split("## 0.2.0\n", 1)[1].split("## 0.1.0", 1)[0]
        expected = section.strip("\n") + "\n"
        result = self.call("0.2.0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, expected)
        self.assertTrue(result.stdout.startswith("Pairs with Router 0.2.0.\n\n### Added"))

    def test_pairs_with_line_leads_each_release(self):
        for version in ("0.3.0", "0.2.0"):
            with self.subTest(version=version):
                result = self.call(version)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(result.stdout.startswith("Pairs with Router " + version + ".\n\n### "),
                                result.stdout[:80])

    def test_contributing_documents_the_github_release_step(self):
        text = (ROOT / "CONTRIBUTING.md").read_text()
        self.assertIn("## Releasing", text)
        self.assertIn('gh release create vX.Y.Z --title "Harness X.Y.Z" --notes "$(scripts/release-notes.sh X.Y.Z)"', text)
        self.assertIn("same day", text)
        self.assertIn("Router first", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
