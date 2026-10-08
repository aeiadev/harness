#!/usr/bin/env python3
"""Check that the nine canonical Markdown roles match the pinned byte hashes."""

import hashlib
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / "agents"
ROLES = {"sweeper", "researcher", "planner", "builder", "builder-in-place",
         "judge", "worker", "test-writer", "docs-writer"}


class SharedRolesTests(unittest.TestCase):
    def test_each_role_validates_its_brief_before_work(self):
        for role in ROLES:
            with self.subTest(role=role):
                body = (ROOT / f"{role}.md").read_text(encoding="utf-8").split("## Job", 1)[1].split("## Must not", 1)[0]
                for phrase in ("Before any work", "exactly one nonempty TASK, FILES, BAR, and RETURN", "in that order", "Free-text notes may follow", "missing, duplicated, or empty", "NOT DONE", "do nothing else"):
                    self.assertIn(phrase, body)
                if role in {"builder", "builder-in-place"}:
                    self.assertIn("never share an editing checkout with another active worker", body)

    def test_manifest_names_and_hashes(self):
        lines = (ROOT / "SHARED.sha256").read_text(encoding="utf-8").splitlines()
        entries = {}
        for line in lines:
            match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9-]+\.md)", line)
            self.assertIsNotNone(match, f"invalid sha256sum line: {line!r}")
            digest, name = match.groups()
            self.assertNotIn(name, entries, f"duplicate manifest entry: {name}")
            entries[name] = digest
        self.assertEqual(set(entries), {f"{role}.md" for role in ROLES},
                         "manifest must list exactly the nine canonical roles")
        for name, expected in entries.items():
            with self.subTest(role=name):
                actual = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                self.assertEqual(actual, expected, f"{name} drifted from SHARED.sha256")


if __name__ == "__main__":
    unittest.main()
