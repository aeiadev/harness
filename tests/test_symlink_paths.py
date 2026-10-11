#!/usr/bin/env python3
"""State is written and read back when HOME or XDG_STATE_HOME sits behind a symlink.

macOS keeps temporary folders under /var, a link to /private/var; some Linux systems
link /home or ~/.local elsewhere. Only a link inside Harness's own state folder is refused.
"""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
DISPATCH = ROOT / "hooks/checkpoint/dispatch.py"
SESSION = "symlinked-fixture"
sys.path.insert(0, str(ROOT / "tests"))
from _platform import home_env  # noqa: E402


class SymlinkedStatePaths(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="harness-symlink-")
        self.addCleanup(temporary.cleanup)
        self.real = Path(temporary.name).resolve() / "real"
        self.link = self.real.parent / "link"
        self.real.mkdir()
        self.link.symlink_to(self.real, target_is_directory=True)
        for name in ("home", "state", "outside"):
            (self.real / name).mkdir()
        self.project = self.real / "project"
        (self.project / ".git").mkdir(parents=True)
        (self.project / ".claude").mkdir()
        (self.project / ".claude/checkpoint.json").write_text(json.dumps(
            {"remind_at": 100, "urgent_at": 200, "window_tokens": 300, "repeat_after": 50}))
        (self.project / "STATE.md").write_text("## Next move\nCheck the title validator.\n")
        self.log = self.real / "events.jsonl"
        self.log.write_text(json.dumps(
            {"type": "assistant", "message": {"usage": {"input_tokens": 100}}}))
        self.other = self.real / "other"
        self.other.mkdir()
        (self.other / "STATE.md").write_text("Unrelated record")

    def environment(self, home, state=None):
        # Windows Python finds the home (and so the state folder) through USERPROFILE, not HOME.
        env = dict(os.environ, **home_env(home), PYTHONDONTWRITEBYTECODE="1")
        env.pop("CLAUDE_PROJECT_DIR", None)
        env.pop("XDG_STATE_HOME", None)
        if state is not None:
            env["XDG_STATE_HOME"] = str(state)
        return env

    def invoke(self, action, env, **fields):
        event = {"session_id": SESSION, "cwd": str(self.project),
                 "transcript_path": str(self.log), **fields}
        outcome = subprocess.run([sys.executable, str(DISPATCH), action, "--host=claude"],
                                 input=json.dumps(event), text=True, capture_output=True,
                                 cwd=self.project, env=env, timeout=10)
        self.assertEqual((outcome.returncode, outcome.stderr), (0, ""))
        if not outcome.stdout:
            return ""
        return json.loads(outcome.stdout)["hookSpecificOutput"]["additionalContext"]

    def assert_state_round_trip(self, env, cache):
        """Pressure file, reminder notice and project pin are written and read back."""
        first = self.invoke("check", env)
        self.assertIn("Refresh", first)
        self.assertIn(str(self.project / "STATE.md"), first)
        pressure = cache / "pressure" / (hashlib.sha256(SESSION.encode()).hexdigest()[:16] + ".json")
        record = json.loads(pressure.read_text())
        self.assertEqual((record["used"], record["window"], record["level"]), (100, 300, "remind"))
        cursor = cache / "checkpoints/claude" / hashlib.sha256(SESSION.encode()).hexdigest() / "cursor.json"
        saved = json.loads(cursor.read_text())
        self.assertEqual(saved["notice"]["level"], "routine")
        self.assertEqual(saved["binding"]["directory"], str(self.project))
        self.assertEqual(self.invoke("check", env), "", "a read-back notice must not repeat")
        self.assertIn("title validator", self.invoke("restore", env, source="compact",
                                                       cwd=str(self.other)))

    def test_home_behind_a_symlink(self):
        env = self.environment(self.link / "home")
        self.assert_state_round_trip(env, self.link / "home/.local/state/claude-harness")
        self.assertTrue((self.real / "home/.local/state/claude-harness/pressure").is_dir())

    def test_xdg_state_home_behind_a_symlink(self):
        env = self.environment(self.real / "home", self.link / "state")
        self.assert_state_round_trip(env, self.link / "state/claude-harness")
        self.assertTrue((self.real / "state/claude-harness/pressure").is_dir())
        self.assertFalse((self.real / "home/.local").exists())

    def test_link_inside_harness_state_is_still_refused(self):
        outside = self.real / "outside"
        for linked in ("claude-harness", "claude-harness/pressure"):
            with self.subTest(linked=linked):
                state = self.real / ("state-" + linked.replace("/", "-"))
                (state / linked).parent.mkdir(parents=True, exist_ok=True)
                (state / linked).symlink_to(outside, target_is_directory=True)
                env = self.environment(self.real / "home", self.link / state.name)
                self.assertIn("Refresh", self.invoke("check", env))
                self.assertEqual(list(outside.rglob("*")), [], "state must not follow a link")


if __name__ == "__main__":
    unittest.main(verbosity=2)
