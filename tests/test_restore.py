#!/usr/bin/env python3
"""Restore ordering and bounded context for both hosts."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]


class RestoreContract(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        # Resolve TMPDIR links (macOS /var is /private/var) and stop Git at the fixture,
        # so the restored path and facts line never depend on where TMPDIR points.
        self.base = Path(temporary.name).resolve()
        self.environment = dict(os.environ, HOME=str(self.base),
                                GIT_CEILING_DIRECTORIES=str(self.base),
                                PYTHONDONTWRITEBYTECODE="1")
        self.environment.pop("CLAUDE_PROJECT_DIR", None)
        self.relocate("fixture")

    def relocate(self, name):
        """Use a fresh project and state under base; the name sets the path length."""
        self.project = self.base / name / "project"
        self.project.mkdir(parents=True)
        (self.project / ".git").mkdir()
        self.document = self.project / "STATE.md"
        self.environment["XDG_STATE_HOME"] = str(self.base / name / "state")

    def policy(self, **values):
        directory = self.project / ".claude"
        directory.mkdir(exist_ok=True)
        (directory / "checkpoint.json").write_text(json.dumps(values))

    def restore(self, source="compact", host="claude", environment=None, **fields):
        payload = {"session_id": "restore-test", "cwd": str(self.project),
                   "source": source, **fields}
        result = subprocess.run([sys.executable, str(ROOT / "hooks/checkpoint/dispatch.py"),
                                 "restore", "--host=" + host], input=json.dumps(payload),
                                text=True, capture_output=True, cwd=self.project,
                                env=environment or self.environment, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return (json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
                if result.stdout else "")

    def test_order_tail_and_cap(self):
        self.policy(restore_bytes=1000)
        self.document.write_text("## Proof\nold proof\n" + "middle entry\n" * 1000 +
                                 "newest proof\n" + "## In flight\nRunning edit\n" +
                                 "## Objective\nFinish restore\n" +
                                 "## Next move\nRun checks\n" +
                                 "## Other\nLast section\n")
        text = self.restore()
        self.assertLessEqual(len(text.encode()), 1000)
        self.assertLess(text.index("## Next move"), text.index("## Objective"))
        self.assertLess(text.index("## Objective"), text.index("## In flight"))
        self.assertLess(text.index("## In flight"), text.index("## Proof"))
        self.assertIn("newest proof", text)
        self.assertNotIn("old proof", text)
        self.assertIn("## Other", text)

    def shown_path(self, cap):
        return str(self.document).encode()[:min(300, cap // 6)].decode("utf-8", "ignore")

    def lead(self, cap=4000):
        return (f"Checkpoint reference: {self.shown_path(cap)}\n"
                "Use this saved record to resume; reconcile it with the latest request.\n\n")

    def notice(self, cap=1000):
        return f"\n[Excerpt only. Read the complete checkpoint at {self.shown_path(cap)}.]\n"

    def share(self, headers, sections, cap=1000):
        """Equal content share per section, computed from the inputs alone."""
        fixed = len((self.lead(cap) + self.notice(cap) + headers).encode()) + 2 * (sections - 1)
        return (cap - fixed) // sections

    def proof(self, text, restore_bytes=1000):
        self.policy(restore_bytes=restore_bytes)
        self.document.write_text(text)
        return self.restore()

    def test_oversized_newest_line_keeps_its_tail(self):
        line = "HEAD-OF-PROOF " + "x" * 2970 + " END-OF-PROOF"
        text = self.proof("## Proof\n" + line + "\n")
        self.assertLessEqual(len(text.encode()), 1000)
        body = text.split("## Proof\n", 1)[1].split("\n[Excerpt only", 1)[0]
        self.assertTrue(body.endswith("END-OF-PROOF") or body.endswith("END-OF-PROOF\n"), body[-40:])
        self.assertGreater(len(body), 100)
        self.assertNotIn("HEAD-OF-PROOF", text)

    def test_oversized_newest_line_with_trailing_blank_lines_keeps_its_tail(self):
        line = "a" * 2990 + "END"
        for blanks in ("\n", "\n\n\n", "  \n\t\n\n"):
            with self.subTest(blanks=blanks):
                text = self.proof("## Proof\n" + line + "\n" + blanks + "## Objective\nO\n")
                share = self.share("## Objective\n## Proof\n", 2)
                kept = "a" * (share - 4) + "END\n"
                self.assertEqual(text, self.lead(1000) + "## Objective\nO\n\n## Proof\n" +
                                 kept.rstrip("\n") + self.notice())

    def test_crlf_oversized_newest_line_leaves_no_carriage_return(self):
        self.policy(restore_bytes=1000)
        self.document.write_bytes(b"## Proof\r\n" + b"a" * 2990 + b"END\r\n\r\n")
        text = self.restore()
        kept = "a" * (self.share("## Proof\r\n", 1) - 4) + "END"
        self.assertEqual(text, self.lead(1000) + "## Proof\r\n" + kept + self.notice())

    def test_oversized_multibyte_line_stays_valid(self):
        text = self.proof("## Proof\n" + "界" * 2000 + "\n")
        self.assertNotIn("�", text)
        self.assertLessEqual(len(text.encode()), 1000)
        self.assertIn("界界界", text)

    def test_oversized_newest_line_beats_a_fitting_old_line(self):
        text = self.proof("## Proof\nold fitting line\n" + "n" * 3000 + "NEWEST-END\n")
        self.assertNotIn("old fitting line", text)
        self.assertIn("NEWEST-END", text)

    def test_complete_layout_with_preamble_is_exact(self):
        self.document.write_text("# Title\n\n## Objective\nA\n\n## Next move\nB\n")
        self.assertEqual(self.restore(), self.lead() + "# Title\n\n## Next move\nB\n\n## Objective\nA\n")

    def test_complete_layout_without_preamble_is_exact(self):
        self.document.write_text("## Objective\nA\n## Other\nC\n## Next move\nB\n## In flight\nD\n")
        self.assertEqual(self.restore(), self.lead() + "## Next move\nB\n\n## Objective\nA\n\n"
                         "## In flight\nD\n\n## Other\nC\n")

    def test_preamble_stays_first_whatever_the_order(self):
        self.policy(restore_order=["Next move"])
        self.document.write_text("# Title\n## Objective\nA\n## Next move\nB\n")
        self.assertEqual(self.restore(), self.lead() + "# Title\n\n## Next move\nB\n\n## Objective\nA\n")

    def test_excerpt_layout_is_exact(self):
        lines = "".join(f"proof {n:03d}\n" for n in range(200))
        # Document paths from short to past the 166-byte cut, whatever TMPDIR is.
        for name in ("short", "d" * 40, "d" * 120, "d" * 200):
            with self.subTest(name_bytes=len(name)):
                self.relocate(name)
                self.policy(restore_bytes=1000)
                self.document.write_text("# Title\n## Next move\nB\n## Proof\n" + lines)
                text = self.restore()
                count = self.share("## Next move\n## Proof\n", 3) // len("proof 000\n")
                self.assertTrue(0 < count < 200, count)
                kept = "".join(f"proof {n:03d}\n" for n in range(200 - count, 200))
                self.assertEqual(text, self.lead(1000) + "# Title\n\n## Next move\nB\n\n## Proof\n" +
                                 kept.rstrip("\n") + self.notice())
                self.assertLessEqual(len(text.encode()), 1000)

    def test_oversized_preamble_keeps_its_first_lines(self):
        text = self.proof("# Title line\n" + "preamble filler\n" * 300 + "PREAMBLE-LAST\n" +
                          "## Next move\nB\n")
        self.assertTrue(text.startswith(self.lead(1000) + "# Title line\npreamble filler\n"))
        self.assertNotIn("PREAMBLE-LAST", text)
        self.assertIn("\n\n## Next move\nB", text)

    def test_override_validation_and_clamp(self):
        self.document.write_text("## Objective\nA\n## Next move\nB\n## In flight\nC\n")
        self.policy(restore_order=["In flight", "Next move"])
        text = self.restore()
        self.assertLess(text.index("## In flight"), text.index("## Next move"))
        self.assertLess(text.index("## Next move"), text.index("## Objective"))
        self.assertEqual(text.count("## Objective"), 1)
        self.policy(restore_order="In flight")
        text = self.restore()
        self.assertLess(text.index("## Next move"), text.index("## Objective"))
        self.assertLess(text.index("## Objective"), text.index("## In flight"))
        self.policy(restore_bytes=20)
        self.document.write_text("## Proof\n" + "long line\n" * 3000)
        self.assertLessEqual(len(self.restore().encode()), 1000)
        self.policy(restore_bytes=50000)
        self.assertLessEqual(len(self.restore().encode()), 4000)

    def test_identical_custom_sections_are_preserved(self):
        self.document.write_text("## Note\nSame text\n## Note\nSame text\n")
        self.assertEqual(self.restore().count("## Note"), 2)

    def git(self, *args):
        result = subprocess.run([shutil.which("git"), "-c", "core.hooksPath=/dev/null", *args], cwd=self.project,
                                stdin=subprocess.DEVNULL, capture_output=True,
                                text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def test_git_facts_are_bounded_and_use_document_age(self):
        (self.project / ".git").rmdir()
        self.git("init", "--quiet")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "fixture" + chr(64) + "example.invalid")
        (self.project / "tracked").write_text("first")
        self.git("add", "tracked")
        self.git("commit", "--quiet", "-m", "fixture")
        self.git("checkout", "--quiet", "-b", "b" * 150 + "/" + "c" * 150)
        (self.project / "tracked").write_text("changed")
        self.document.write_text("## Next move\nRun checks\n")
        os.utime(self.document, (time.time() - 3 * 3600, time.time() - 3 * 3600))
        text = self.restore()
        facts = next(line for line in text.splitlines() if line.startswith("Git: "))
        self.assertLessEqual(len(facts.encode()), 200)
        self.assertIn("dirty=2", facts)
        self.assertIn("STATE.md age=3h", facts)
        self.assertLess(text.index("Git: "), text.index("## Next move") if "## Next move" in text else len(text))

    def make_repo(self):
        (self.project / ".git").rmdir()
        self.git("init", "--quiet")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "fixture" + chr(64) + "example.invalid")
        (self.project / "tracked").write_text("first")
        self.git("add", "tracked")
        self.git("commit", "--quiet", "-m", "fixture")
        (self.project / ".git" / "info" / "exclude").write_text("STATE.md\n")

    def facts_line(self):
        self.document.write_text("## Next move\nRun checks\n")
        os.utime(self.document, (time.time() - 120, time.time() - 120))
        return [line for line in self.restore().splitlines() if line.startswith("Git: ")]

    def test_detached_head_keeps_the_facts_line(self):
        self.make_repo()
        self.git("checkout", "--quiet", "--detach")
        (self.project / "tracked").write_text("changed")
        lines = self.facts_line()
        self.assertEqual(len(lines), 1)
        self.assertRegex(lines[0], r"^Git: detached HEAD=[0-9a-f]{7,} dirty=1 STATE\.md age=[0-9]+[mhd]$")

    def test_branch_name_is_still_shown(self):
        self.make_repo()
        self.git("checkout", "--quiet", "-b", "feature/x")
        (self.project / "tracked").write_text("changed")
        lines = self.facts_line()
        self.assertEqual(len(lines), 1)
        self.assertRegex(lines[0], r"^Git: feature/x HEAD=[0-9a-f]{7,} dirty=1 STATE\.md age=[0-9]+[mhd]$")

    def test_repo_without_a_commit_has_no_facts_line(self):
        (self.project / ".git").rmdir()
        self.git("init", "--quiet")
        self.assertEqual(self.facts_line(), [])

    def test_facts_are_absent_outside_git_and_on_timeout(self):
        self.document.write_text("## Next move\nRun checks\n")
        self.assertNotIn("Git: ", self.restore())
        stub = self.base / "bin"
        stub.mkdir()
        fake = stub / "git"
        fake.write_text("#!/bin/sh\nsleep 3\n")
        fake.chmod(0o755)
        start = time.monotonic()
        text = self.restore(environment={**self.environment,
                                         "PATH": str(stub) + os.pathsep + os.environ["PATH"]})
        self.assertLess(time.monotonic() - start, 2)
        self.assertNotIn("Git: ", text)
        self.assertIn("Run checks", text)

    def test_resume_cache_warning_is_claude_only_and_strictly_gated(self):
        self.document.write_text("## Next move\nRun checks\n")
        warning = self.restore(source="resume", prompt_cache_likely_expired=True,
                               context_tokens=123456)
        self.assertIn("Prompt cache likely expired", warning)
        self.assertIn("123456", warning)
        self.assertNotIn("Run checks", warning)
        self.assertLessEqual(len(warning.encode()), 160)
        for fields in ({}, {"prompt_cache_likely_expired": False},
                       {"prompt_cache_likely_expired": "true"}):
            self.assertEqual(self.restore(source="resume", **fields), "")
        self.assertEqual(self.restore(source="resume", host="codex",
                                      prompt_cache_likely_expired=True), "")
        self.assertNotIn("Prompt cache", self.restore(source="compact",
                                                       prompt_cache_likely_expired=True))

    def test_settings_example_has_resume_matcher(self):
        settings = json.loads((ROOT / "examples/settings.example.json").read_text())
        resume = [group for group in settings["hooks"]["SessionStart"]
                  if group.get("matcher") == "resume"]
        self.assertEqual(len(resume), 1)
        self.assertTrue(any("dispatch.py" in hook["command"] and
                            "restore --host=claude" in hook["command"]
                            for hook in resume[0]["hooks"]))


if __name__ == "__main__":
    unittest.main()
