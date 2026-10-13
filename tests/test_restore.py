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
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if os.name == "nt":  # Fixtures are LF UTF-8 on every platform, as a Linux or macOS checkout writes them.
    Path.write_text = lambda self, data, encoding=None, errors=None, newline=None: self.write_bytes(
        data.encode(encoding or "utf-8", errors or "strict"))
    Path.read_text = lambda self, encoding=None, errors=None: self.read_bytes().decode(
        encoding or "utf-8", errors or "strict")


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
        self.environment["HARNESS_PROJECT_ROOT"] = str(self.project)
        self.document = self.project / "STATE.md"
        # State sits outside the long name so its deeper paths stay under Windows' 260 characters.
        self.environment["XDG_STATE_HOME"] = str(self.base / f"state-{name[:8]}{len(name)}")

    def policy(self, **values):
        directory = self.project / ".claude"
        directory.mkdir(exist_ok=True)
        (directory / "checkpoint.json").write_text(json.dumps(values))

    def restore(self, source="compact", host="claude", environment=None, **fields):
        payload = {"session_id": "restore-test", "cwd": str(self.project),
                   "source": source, **fields}
        result = subprocess.run([sys.executable, str(ROOT / "hooks/checkpoint/dispatch.py"),
                                 "restore", "--host=" + host], input=json.dumps(payload),
                                text=True, encoding="utf-8", capture_output=True, cwd=self.project,
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

    def test_later_critical_sections_survive_oversized_early_paragraph(self):
        self.document.write_text("## Status\n" + "old filler " * 2500 + "\n" +
                                 "".join(f"## Note {number}\nold\n" for number in range(300)) +
                                 "## Objective\nShip the current fix.\n"
                                 "## Constraints\nKeep local changes.\n"
                                 "## Next move\nRun the focused regression.\n"
                                 "## Proof\nSee tests/test_restore.py.\n"
                                 "## Failures\nOne unresolved failure remains.\n")
        for host in ("claude", "codex"):
            with self.subTest(host=host):
                text = self.restore(host=host)
                self.assertLessEqual(len(text.encode()), 4000)
                for phrase in ("Run the focused regression.", "Ship the current fix.",
                               "Keep local changes.", "tests/test_restore.py",
                               "One unresolved failure remains."):
                    self.assertIn(phrase, text)

    def test_repeated_sections_do_not_crowd_out_current_facts(self):
        self.document.write_text(
            "## Status\n" + "old filler " * 2500 + "\n" +
            "".join(f"## Objective\nOld objective {number}\n" for number in range(12)) +
            "## Next move\nCURRENT NEXT ACTION\n"
            "## Constraints\nPRESERVE USER WORK\n"
            "## Proof\nCURRENT PROOF POINTER\n"
            "## Failures\nUNRESOLVED FAILURE\n", encoding="utf-8")
        for host in ("claude", "codex"):
            with self.subTest(host=host):
                text = self.restore(host=host)
                self.assertLessEqual(len(text.encode()), 4000)
                for phrase in ("CURRENT NEXT ACTION", "PRESERVE USER WORK",
                               "CURRENT PROOF POINTER", "UNRESOLVED FAILURE",
                               "Old objective 11"):
                    self.assertIn(phrase, text)

    def test_distinct_critical_sections_are_not_capped_as_old_notes(self):
        names = ("Next move", "Next action", "Next step", "Objective", "Aligned",
                 "Constraints", "Requirements", "In flight", "Failures",
                 "Unresolved failures", "Blockers", "Tests", "Verification",
                 "Proof", "Dead ends")
        self.document.write_text("## Status\n" + "old filler " * 2500 + "\n" +
                                 "".join(f"## {name}\nCritical fact {number}.\n"
                                         for number, name in enumerate(names)))
        text = self.restore()
        self.assertLessEqual(len(text.encode()), 4000)
        for number in range(len(names)):
            self.assertIn(f"Critical fact {number}.", text)

    def test_late_action_after_megabyte_paragraph(self):
        self.document.write_text("## Status\n" + "界" * 400000 +
                                 "\n## Next move\nRun the late check.\n", encoding="utf-8")
        for host in ("claude", "codex"):
            with self.subTest(host=host):
                text = self.restore(host=host)
                self.assertIn("Run the late check.", text)
                self.assertNotIn("�", text)
                self.assertLessEqual(len(text.encode()), 4000)

    def test_excerpt_reader_limits_each_read(self):
        sys.path.insert(0, str(ROOT / "hooks/checkpoint"))
        import state_excerpt
        self.document.write_text("## Status\n" + "x" * (2 * 1024 * 1024) +
                                 "\n## Next move\nRun the late check.\n")
        original = state_excerpt.guarded_read
        with mock.patch.object(state_excerpt, "guarded_read", wraps=original) as read:
            raw, complete = state_excerpt.read_state(self.document)
        self.assertFalse(complete)
        self.assertIn(b"Run the late check.", raw)
        self.assertEqual(len(read.call_args_list), 2)
        self.assertTrue(all(call.args[1] <= state_excerpt.READ_LIMIT // 2
                            for call in read.call_args_list))
        self.assertLessEqual(len(raw), state_excerpt.READ_LIMIT + 2)

    def shown_path(self, cap):
        return str(self.document).encode()[:min(300, cap // 6)].decode("utf-8", "ignore")

    def lead(self, cap=4000):
        return (f"Checkpoint reference: {self.shown_path(cap)}\n"
                "Use this saved record to resume; reconcile it with the latest request.\n\n")

    def notice(self, cap=1000):
        return f"\n[Excerpt only. Read the complete checkpoint at {self.shown_path(cap)}.]\n"

    def wide(self):
        """Move the document to a path past the 166-byte cut (1000 // 6), whatever TMPDIR is, so the
        layout has fixed widths: a 261-byte lead and a 217-byte notice leave 522 bytes for sections.
        About 190 bytes long, under Windows' 260-character path limit."""
        self.relocate("d" * max(1, 190 - len(os.fsencode(self.base / "project/STATE.md"))))
        self.assertEqual(len(self.shown_path(1000).encode()), 166, self.document)
        self.assertEqual((len(self.lead(1000).encode()), len(self.notice(1000).encode())), (261, 217))

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
        self.wide()
        line = "a" * 2990 + "END"
        for blanks in ("\n", "\n\n\n", "  \n\t\n\n"):
            with self.subTest(blanks=blanks):
                text = self.proof("## Proof\n" + line + "\n" + blanks + "## Objective\nO\n")
                # 522 less 22 header bytes and one 2-byte separator: 249 a section, so the line's
                # last 248 bytes and a newline that the join drops.
                self.assertEqual(text, self.lead(1000) + "## Objective\nO\n\n## Proof\n" +
                                 "a" * 245 + "END" + self.notice())

    def test_crlf_oversized_newest_line_leaves_no_carriage_return(self):
        self.wide()
        self.policy(restore_bytes=1000)
        self.document.write_bytes(b"## Proof\r\n" + b"a" * 2990 + b"END\r\n\r\n")
        text = self.restore()
        # 522 less the 10-byte header: 512, so the line's last 511 bytes and a newline.
        self.assertEqual(text, self.lead(1000) + "## Proof\r\n" + "a" * 508 + "END" + self.notice())

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
        self.wide()
        self.policy(restore_bytes=1000)
        self.document.write_text("# Title\n## Next move\nB\n## Proof\n" +
                                 "".join(f"proof {n:03d}\n" for n in range(200)))
        text = self.restore()
        # 522 less 22 header bytes and two 2-byte separators: 165 a section, so the newest
        # sixteen 10-byte proof lines.
        kept = "\n".join(f"proof {n:03d}" for n in range(184, 200))
        self.assertEqual(text, self.lead(1000) + "# Title\n\n## Next move\nB\n\n## Proof\n" + kept + self.notice())
        self.assertLessEqual(len(text.encode()), 1000)

    def test_excerpt_keeps_the_newest_lines_at_any_path_length(self):
        lines = "".join(f"proof {n:03d}\n" for n in range(200))
        # Document paths from short to past the 166-byte cut, whatever TMPDIR is; the longest
        # is sized from TMPDIR to about 190 bytes, under Windows' 260-character path limit.
        longest = max(1, 190 - len(os.fsencode(self.base / "project/STATE.md")))
        counts = []
        for name in sorted({"short", "d" * 40, "d" * 120, "d" * longest}, key=len):
            with self.subTest(name_bytes=len(name)):
                self.relocate(name)
                self.policy(restore_bytes=1000)
                self.document.write_text("# Title\n## Next move\nB\n## Proof\n" + lines)
                text = self.restore()
                head = self.lead(1000) + "# Title\n\n## Next move\nB\n\n## Proof\n"
                self.assertTrue(text.startswith(head), text[:200])
                self.assertTrue(text.endswith(self.notice()), text[-200:])
                rows = text[len(head):-len(self.notice())].split("\n")
                self.assertTrue(0 < len(rows) < 200, len(rows))
                self.assertEqual(rows, [f"proof {n:03d}" for n in range(200 - len(rows), 200)])
                self.assertLessEqual(len(text.encode()), 1000)
                if len(self.shown_path(1000).encode()) == 166:
                    self.assertEqual(len(rows), 16)  # The fixed widths of test_excerpt_layout_is_exact.
                counts.append(len(rows))
        self.assertEqual(counts, sorted(counts, reverse=True), "a longer path never leaves more room")

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
        self.git("init", "--quiet")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "fixture" + chr(64) + "example.invalid")
        (self.project / "tracked").write_text("first")
        self.git("add", "tracked")
        self.git("commit", "--quiet", "-m", "fixture")
        # Git keeps each branch as a file under .git/logs/refs/heads; Windows stops paths at 260.
        half = min(150, (250 - len(str(self.project / ".git/logs/refs/heads"))) // 2)
        self.git("checkout", "--quiet", "-b", "b" * half + "/" + "c" * half)
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
