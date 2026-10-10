#!/usr/bin/env python3
"""Check the nine canonical roles: C1 brief rule, Codex-only text, budgets, and 19 pinned hashes."""

import hashlib
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / "agents"
CODEX = ROOT.parent / "codex" / "agents"
ROLES = {"sweeper", "researcher", "planner", "builder", "builder-in-place",
         "judge", "worker", "test-writer", "docs-writer"}
READ_SET = {"sweeper", "researcher", "planner"}
CODEX_NOTE_ROLES = {"judge", "sweeper", "researcher", "planner", "test-writer"}
FULL_SENTENCE = 'Before any work, check the brief has exactly one nonempty TASK, FILES, BAR, and RETURN, in that order. A field is a line starting with its name in capitals followed by a colon or a space, or its name in any case followed by a colon. If any field is missing, duplicated, empty, or out of order, return NOT DONE naming the problem and do nothing else.'
READ_SENTENCE = 'Before any work, check the brief has exactly one nonempty TASK and RETURN. FILES and BAR are optional, at most once each. Fields keep the order TASK, FILES, BAR, RETURN. A field is a line starting with its name in capitals followed by a colon or a space, or its name in any case followed by a colon. If the brief breaks this, return NOT DONE naming the problem and do nothing else.'
OLD_BODY_BYTES = {"builder-in-place": 2024, "builder": 1984, "docs-writer": 1601, "judge": 3624,
                  "planner": 1855, "researcher": 1863, "sweeper": 1784, "test-writer": 2048,
                  "worker": 1621}
DESCRIPTION_CEILING = 1300


def split_role(role):
    text = (ROOT / f"{role}.md").read_text(encoding="utf-8")
    match = re.fullmatch(r"---\n(.*?)\n---\n(.*)", text, re.DOTALL)
    fields = dict(line.split(": ", 1) for line in match[1].splitlines())
    return fields, match[2]


def job_paragraph(role):
    return split_role(role)[1].split("## Job", 1)[1].strip().split("\n\n", 1)[0]


class SharedRolesTests(unittest.TestCase):
    def test_each_role_carries_its_c1_sentence_exactly(self):
        for role in ROLES:
            with self.subTest(role=role):
                want = READ_SENTENCE if role in READ_SET else FULL_SENTENCE
                self.assertEqual(job_paragraph(role), want)
                body = split_role(role)[1]
                self.assertNotIn("Free-text notes may follow", body)
                if role in {"builder", "builder-in-place"}:
                    self.assertIn("never share an editing checkout with another active worker", body)

    def test_markdown_roles_hold_no_codex_text_and_tomls_hold_it(self):
        for role in ROLES:
            with self.subTest(role=role):
                self.assertNotIn("Codex", (ROOT / f"{role}.md").read_text(encoding="utf-8"))
                toml = (CODEX / f"{role}.toml").read_text(encoding="utf-8")
                if role in CODEX_NOTE_ROLES:
                    self.assertIn("Codex", toml)
                else:
                    self.assertNotIn("Codex", toml)

    def test_judge_return_wording(self):
        body = split_role("judge")[1].split("## Return", 1)[1]
        for token in ("PASS", "SEND_BACK", "NOT DONE"):
            self.assertIn(token, body.split("Then", 1)[0], f"verdict token {token} missing from the first-line rule")
        self.assertIn("numbered findings", body)
        self.assertIn('one per line starting "1."', body)
        self.assertIn("REPRODUCED or REASONED", body)
        self.assertIn("follow the verdict line, never precede it", body)

    def test_description_and_body_budgets(self):
        total = 0
        for role in ROLES:
            fields, body = split_role(role)
            total += len(role.encode()) + len(fields["description"].encode())
            self.assertLessEqual(len(body.encode()), OLD_BODY_BYTES[role] + 150, f"{role} body grew too much")
        self.assertLessEqual(total, DESCRIPTION_CEILING, "name+description bytes over the C8 ceiling")

    def test_manifest_names_and_hashes(self):
        lines = (ROOT / "SHARED.sha256").read_text(encoding="utf-8").splitlines()
        entries = {}
        for line in lines:
            match = re.fullmatch(r"([0-9a-f]{64})  ((?:\.\./codex/agents/[A-Za-z0-9-]+\.toml|\.\./scripts/budget\.py|[A-Za-z0-9-]+\.md))", line)
            self.assertIsNotNone(match, f"invalid sha256sum line: {line!r}")
            digest, name = match.groups()
            self.assertNotIn(name, entries, f"duplicate manifest entry: {name}")
            entries[name] = digest
        want = {f"{role}.md" for role in ROLES} | {f"../codex/agents/{role}.toml" for role in ROLES} | {"../scripts/budget.py"}
        self.assertEqual(set(entries), want, "manifest must list exactly the nine .md, nine .toml roles and scripts/budget.py")
        for name, expected in entries.items():
            with self.subTest(role=name):
                actual = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                self.assertEqual(actual, expected, f"{name} drifted from SHARED.sha256")


if __name__ == "__main__":
    unittest.main()
