#!/usr/bin/env bash
# Run from any directory. Fixtures and hook state always live in a temporary HOME.
set -euo pipefail

test_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
# pyrun.sh picks the interpreter the hooks use: python3, python or py -3, never the Store stub.
python=$("$BASH" "$test_root/hooks/pyrun.sh" --find harness)
"$python" - "$test_root/hooks/context/inject-project-context.sh" <<'PY'
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

HOOK = Path(sys.argv.pop()).resolve()
sys.path.insert(0, str(HOOK.parents[2] / "tests"))
from _platform import BASH, home_env, posix


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="harness-context-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.home = self.base / "home"
        self.home.mkdir()
        self.env = {
            **home_env(self.home),
            "XDG_STATE_HOME": str(self.base / "state"),
            "PATH": os.environ["PATH"],
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
        }
        self.repo = self.make_repo("project")
        (self.repo / "STATE.md").write_text("# State\n\nCurrent checkpoint.\n", encoding="utf-8")

    def make_repo(self, name):
        path = self.base / name
        path.mkdir()
        subprocess.run(["git", "init", "-q", str(path)], stdin=subprocess.DEVNULL,
                       capture_output=True, env=self.env, check=True, timeout=5)
        return path

    def call(self, *, cwd=None, session="first", reset=False, raw=None, env=None):
        payload = {"cwd": str(cwd or self.repo)}
        if session is not None:
            payload["session_id"] = session
        command = [BASH, posix(HOOK)] + (["--reset"] if reset else [])
        result = subprocess.run(command, input=raw if raw is not None else json.dumps(payload).encode(),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=self.base,
                                env=env or self.env, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        self.assertEqual(result.stderr, b"", "Hook must not report incidental errors")
        self.assertLessEqual(len(result.stdout), 4000, "Injected bytes exceed the public ceiling")
        return result.stdout.decode("utf-8", errors="strict")

    def test_once_per_session_project_even_when_state_changes(self):
        first = self.call()
        self.assertIn("Current checkpoint.", first)
        self.assertIn("</project-context>", first)
        self.assertTrue(first.endswith("Continue with the user's current request.\n"))
        self.assertEqual(self.call(), "", "A second prompt reinjected context")
        nested = self.repo / "nested"
        nested.mkdir()
        self.assertEqual(self.call(cwd=nested), "", "Nested cwd did not share the project marker")
        (self.repo / "STATE.md").write_text("Updated checkpoint.\n", encoding="utf-8")
        self.assertEqual(self.call(), "", "Changed state must wait for a reset or new session")
        self.assertIn("Updated checkpoint.", self.call(session="second"))
        other = self.make_repo("other")
        (other / "STATE.md").write_text("Other project.\n", encoding="utf-8")
        self.assertIn("Other project.", self.call(cwd=other))

    def test_reset_restores_only_the_requested_session(self):
        self.call()
        self.call(session="second")
        self.assertEqual(self.call(reset=True), "", "Reset itself must stay silent")
        self.assertIn("Current checkpoint.", self.call())
        self.assertEqual(self.call(session="second"), "", "Reset affected an unrelated session")
        self.assertEqual(self.call(reset=True, session=None), "")
        self.assertEqual(self.call(), "", "Missing-session reset cleared a known session")

    def test_nearest_instruction_pointers_do_not_leave_project(self):
        nested = self.repo / "nested" / "deep"
        nested.mkdir(parents=True)
        (self.base / "AGENTS.md").write_text("Outside instructions.\n", encoding="utf-8")
        (self.repo / "CLAUDE.md").write_text("Root instructions.\n", encoding="utf-8")
        (self.repo / "nested" / "CLAUDE.md").write_text("Nearest instructions.\n", encoding="utf-8")
        (self.repo / "AGENTS.md").write_text("Project instructions.\n", encoding="utf-8")
        output = self.call(cwd=nested)
        self.assertIn('"nested/CLAUDE.md"', output)
        self.assertIn('"AGENTS.md"', output)
        self.assertNotIn('"CLAUDE.md"', output)
        self.assertNotIn("Outside instructions", output)
        self.assertNotIn("../AGENTS.md", output)
        self.assertIn("Current checkpoint.", output)

    def test_declared_non_git_and_malformed_cwd_fallback(self):
        plain = self.base / "plain"
        plain.mkdir()
        (plain / "STATE.md").write_text("Declared plain state")
        self.assertEqual(self.call(cwd=plain, session=None), "")
        declared = {**self.env, "HARNESS_PROJECT_ROOT": str(plain)}
        self.assertIn("Declared plain state", self.call(cwd=plain, session=None, env=declared))
        sibling = self.base / "sibling"
        sibling.mkdir()
        (sibling / "STATE.md").write_text("Sibling state")
        self.assertEqual(self.call(cwd=sibling, session=None, env=declared), "")
        self.assertEqual(self.call(cwd=sibling, session=None,
                                   env={**self.env, "HARNESS_PROJECT_ROOT": str(Path(self.base.anchor))}), "")
        payload = {"cwd": ["bad"], "project_dir": str(self.repo), "session_id": "fallback"}
        self.assertIn("Current checkpoint.", self.call(raw=json.dumps(payload).encode()))
        payload = {"cwd": " ", "workspace": {"current_dir": str(self.repo)},
                   "session_id": "workspace"}
        self.assertIn("Current checkpoint.", self.call(raw=json.dumps(payload).encode()))

    def test_home_git_and_symlinked_files_are_excluded(self):
        subprocess.run(["git", "init", "-q", str(self.home)], stdin=subprocess.DEVNULL,
                       capture_output=True, env=self.env, check=True, timeout=5)
        (self.home / "STATE.md").write_text("HOME STATE")
        self.assertEqual(self.call(cwd=self.home, session=None), "")
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "STATE.md").write_text("ESCAPED STATE")
        (outside / "CLAUDE.md").write_text("ESCAPED INSTRUCTIONS")
        try:
            (self.repo / "CLAUDE.md").symlink_to(outside / "CLAUDE.md")
            (self.repo / "STATE.md").unlink()
            (self.repo / "STATE.md").symlink_to(outside / "STATE.md")
        except (OSError, NotImplementedError):
            self.skipTest("this user cannot create symlinks")
        self.assertEqual(self.call(session=None), "")

    def test_git_worktree_and_canonical_symlink_root(self):
        env = {**self.env, "GIT_AUTHOR_NAME": "Fixture Author", "GIT_AUTHOR_EMAIL": "fixture.invalid",
               "GIT_COMMITTER_NAME": "Fixture Committer", "GIT_COMMITTER_EMAIL": "fixture.invalid"}
        subprocess.run(["git", "-C", str(self.repo), "add", "STATE.md"],
                       stdin=subprocess.DEVNULL, capture_output=True, env=env, check=True, timeout=5)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", "fixture"],
                       stdin=subprocess.DEVNULL, capture_output=True, env=env, check=True, timeout=5)
        tree = self.base / "worktree"
        subprocess.run(["git", "-C", str(self.repo), "worktree", "add", "--detach", str(tree)],
                       stdin=subprocess.DEVNULL, capture_output=True, env=env, check=True, timeout=5)
        self.assertIn("Current checkpoint.", self.call(cwd=tree, session=None))
        linked = self.base / "linked-project"
        try:
            linked.symlink_to(self.repo, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("this user cannot create symlinks")
        self.assertIn("Current checkpoint.", self.call(cwd=linked, session=None))

    def test_large_state_has_hard_cap_and_complete_paragraphs(self):
        paragraphs = ["Paragraph %d: %s END-%d" % (number, "x" * 350, number) for number in range(90)]
        (self.repo / "STATE.md").write_text("\n\n".join(paragraphs) + "\n", encoding="utf-8")
        output = self.call()
        self.assertGreater(len(output.encode("utf-8")), 3000, "Summary discarded useful fitting paragraphs")
        self.assertIn("[truncated: read STATE.md for the rest.]", output)
        self.assertIn("</project-context>", output)
        for paragraph in paragraphs:
            if paragraph.split(":")[0] + ":" in output:
                self.assertIn(paragraph, output, "Summary split a paragraph")

    def test_context_larger_than_cap_is_strictly_under_four_kilobytes(self):
        paragraphs = ["Paragraph %d: %s" % (number, "x" * 100) for number in range(60)]
        (self.repo / "STATE.md").write_text("\n\n".join(paragraphs), encoding="utf-8")
        output = self.call(session=None)
        self.assertGreater(len(output.encode("utf-8")), 3900)
        self.assertLessEqual(len(output.encode("utf-8")), 4000)

    def test_context_just_under_cap_passes_through_whole(self):
        prefix = "# State\n\n"
        closing = "\n"
        body = "x" * 3700
        expected = prefix + body + closing
        (self.repo / "STATE.md").write_text(expected, encoding="utf-8")
        output = self.call(session=None)
        self.assertIn(expected, output)

    def test_configured_checkpoint_path_supplies_session_context(self):
        selected = self.repo / "checkpoints" / "selected.md"
        selected.parent.mkdir()
        selected.write_text("Configured checkpoint.", encoding="utf-8")
        (self.repo / ".claude").mkdir()
        (self.repo / ".claude" / "checkpoint.json").write_text(
            json.dumps({"checkpoint_path": "checkpoints/selected.md"}), encoding="utf-8")
        (self.repo / "STATE.md").write_text("Default checkpoint.", encoding="utf-8")
        output = self.call(session=None)
        self.assertIn("State summary from checkpoints/selected.md", output)
        self.assertIn("Configured checkpoint.", output)
        self.assertNotIn("Default checkpoint.", output)

    def test_nested_configured_checkpoint_path_is_project_relative(self):
        selected = self.repo / "docs" / "notes" / "STATE.md"
        selected.parent.mkdir(parents=True)
        selected.write_text("Nested checkpoint.", encoding="utf-8")
        (self.repo / ".claude").mkdir()
        (self.repo / ".claude" / "checkpoint.json").write_text(
            json.dumps({"checkpoint_path": "docs/notes/STATE.md"}), encoding="utf-8")
        output = self.call(session=None)
        self.assertIn("State summary from docs/notes/STATE.md", output)
        self.assertNotIn("State summary from STATE.md", output)

    def test_utf8_cap_preserves_complete_unicode_paragraphs(self):
        paragraphs = ["Paragraph %d: %s END-%d" % (number, "🌱é" * 100, number) for number in range(40)]
        (self.repo / "STATE.md").write_text("\n\n".join(paragraphs), encoding="utf-8")
        output = self.call()
        self.assertIn(paragraphs[0], output)
        self.assertNotIn("�", output, "Summary contains a broken Unicode character")
        self.assertIn("[truncated:", output)
        for paragraph in paragraphs:
            if paragraph.split(":")[0] + ":" in output:
                self.assertIn(paragraph, output, "Unicode paragraph was split")

    def test_single_oversized_paragraph_is_omitted(self):
        (self.repo / "STATE.md").write_text("START-" + "x" * 20000 + "-END", encoding="utf-8")
        output = self.call()
        self.assertNotIn("START-", output, "Oversized paragraph was partially included")
        self.assertIn("[truncated: read STATE.md for the rest.]", output)
        self.assertIn("</project-context>", output)

    def test_later_sections_survive_oversized_early_paragraph_on_both_hosts(self):
        (self.repo / "STATE.md").write_text(
            "## Status\n" + "old filler " * 2500 + "\n\n"
            "## Objective\nShip the current fix.\n\n"
            "## Constraints\nKeep local changes.\n\n"
            "## Next move\nRun the focused regression.\n\n"
            "## Proof\nSee tests/test_restore.py.\n\n"
            "## Failures\nOne unresolved failure remains.\n", encoding="utf-8")
        for host in ("claude", "codex"):
            with self.subTest(host=host):
                payload = {"cwd": str(self.repo), "session_id": host, "model":
                           "gpt-6-sol" if host == "codex" else "claude-sonnet"}
                output = self.call(raw=json.dumps(payload).encode())
                for phrase in ("Run the focused regression.", "Ship the current fix.",
                               "Keep local changes.", "tests/test_restore.py",
                               "One unresolved failure remains."):
                    self.assertIn(phrase, output)
                self.assertNotIn("�", output)

    def test_repeated_sections_do_not_crowd_out_current_facts(self):
        (self.repo / "STATE.md").write_text(
            "## Status\n" + "old filler " * 2500 + "\n" +
            "".join(f"## Objective\nOld objective {number}\n" for number in range(12)) +
            "## Next move\nCURRENT NEXT ACTION\n"
            "## Constraints\nPRESERVE USER WORK\n"
            "## Proof\nCURRENT PROOF POINTER\n"
            "## Failures\nUNRESOLVED FAILURE\n", encoding="utf-8")
        for host in ("claude", "codex"):
            with self.subTest(host=host):
                payload = {"cwd": str(self.repo), "session_id": host, "model":
                           "gpt-6-sol" if host == "codex" else "claude-sonnet"}
                output = self.call(raw=json.dumps(payload).encode())
                for phrase in ("CURRENT NEXT ACTION", "PRESERVE USER WORK",
                               "CURRENT PROOF POINTER", "UNRESOLVED FAILURE",
                               "Old objective 11"):
                    self.assertIn(phrase, output)

    def test_late_action_after_megabyte_paragraph(self):
        (self.repo / "STATE.md").write_text(
            "## Status\n" + "界" * 400000 + "\n## Next move\nRun the late check.\n",
            encoding="utf-8")
        output = self.call(session=None)
        self.assertIn("Run the late check.", output)
        self.assertNotIn("�", output)

    def test_unrelated_markdown_is_not_checkpoint_input(self):
        (self.repo / "notes.md").write_text("Unrelated material.")
        (self.repo / "STATE.md").unlink()
        self.assertEqual(self.call(session=None), "")

    def test_exact_byte_boundary_with_unicode(self):
        for count in (3700, 3999, 4000, 4095, 4096, 4200):
            with self.subTest(bytes=count):
                body = "a" * (count - 3) + "界"
                (self.repo / "STATE.md").write_text(body, encoding="utf-8")
                result = self.call(session=None)
                self.assertLess(len(result.encode()), 4096)
                self.assertIn("</project-context>", result)
                self.assertTrue(body in result or "[truncated:" in result)

    def test_instructions_work_without_a_state_file(self):
        (self.repo / "STATE.md").unlink()
        (self.repo / "AGENTS.md").write_text("Project rules.\n", encoding="utf-8")
        output = self.call()
        self.assertIn('"AGENTS.md"', output)
        self.assertNotIn("State summary", output)

    def test_absent_session_does_not_deduplicate(self):
        self.assertIn("Current checkpoint.", self.call(session=None))
        self.assertIn("Current checkpoint.", self.call(session=None))

    def test_temp_home_default_storage_and_safe_marker_names(self):
        env = dict(self.env)
        env.pop("XDG_STATE_HOME")
        self.call(env=env, session="../../escape ; wildcard*")
        markers = list((self.home / ".local/state/claude-harness/context").glob("*/*.seen"))
        self.assertEqual(len(markers), 1, "Expected one marker under the temporary HOME")
        self.assertRegex(markers[0].name, r"^[0-9a-f]{64}\.seen$")
        self.assertRegex(markers[0].parent.name, r"^[0-9a-f]{64}$")
        self.assertEqual(self.call(env=env, session="../../escape ; wildcard*"), "")

    def test_concurrent_calls_inject_once(self):
        with ThreadPoolExecutor(max_workers=4) as workers:
            outputs = list(workers.map(lambda _: self.call(), range(4)))
        self.assertEqual(sum(bool(output) for output in outputs), 1, "Concurrent calls duplicated context")

    def test_invalid_payloads_and_nonproject_paths_are_silent(self):
        for raw in (b"", b"not json", b"null", b"[]", b'{"cwd":null,"session_id":null}'):
            with self.subTest(payload=raw):
                self.assertEqual(self.call(raw=raw), "")
        self.assertEqual(self.call(cwd=self.base), "", "Nonproject directory injected context")
        self.assertEqual(self.call(cwd=self.base / "absent"), "")


unittest.main(verbosity=2)
PY
