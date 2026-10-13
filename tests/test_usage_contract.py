"""Independent synthetic usage contract fixtures across Harness processes."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from _platform import home_env


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "hooks/checkpoint/usage_cli.py"
DISPATCH = ROOT / "hooks/checkpoint/dispatch.py"


class ContractFixture(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="harness-usage-contract-")
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.home = self.base / "home"
        self.home.mkdir()
        self.env = {**os.environ, **home_env(self.home),
                    "XDG_STATE_HOME": str(self.base / "state"),
                    "PYTHONDONTWRITEBYTECODE": "1"}
        self.project = self.base / "project"
        self.project.mkdir()
        self.env["HARNESS_PROJECT_ROOT"] = str(self.project)
        (self.project / "STATE.md").write_text("## Next move\nReview usage fixture.\n")

    def cli(self, *args, value=None):
        run = subprocess.run([sys.executable, str(CLI), *args],
                             input=None if value is None else json.dumps(value),
                             text=True, capture_output=True, env=self.env,
                             cwd=self.project, timeout=10)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(run.stderr, "")
        return json.loads(run.stdout)

    def declare(self, task, host, ids, outcome):
        self.cli("declare", task, "--file", "-", value={
            "host": host, "expected_event_ids": ids, "outcome": outcome})

    def event(self, task, adapter, event_id, attempt, stage, outcome, payload):
        self.cli("record", task, "--file", "-", value={
            "adapter": adapter, "context": {"event_id": event_id, "attempt_id": attempt,
            "stage": stage, "outcome": outcome}, "payload": payload})

    def test_attempts_missing_judge_totals_and_compact_restore(self):
        self.declare("first", "claude", ["first_e"], "returned")
        self.event("first", "claude-message", "first_e", "first_a", "worker", "returned",
                   {"type": "assistant", "message": {"usage": {"input_tokens": 5,
                    "output_tokens": 3, "cache_read_input_tokens": 2,
                    "cache_creation_input_tokens": 1}}})
        self.declare("retry", "codex", ["fail1", "fail2", "retry_e", "judge_e"], "returned")
        for eid, attempt, stage, outcome, inp, out in (
                ("fail1", "a1", "worker", "failed", 4, 1),
                ("fail2", "a2", "worker", "failed", 5, 2),
                ("retry_e", "a3", "worker", "returned", 10, 3),
                ("judge_e", "judge_a", "judge", "returned", 1, 1)):
            self.event("retry", "codex-turn", eid, attempt, stage, outcome,
                       {"type": "turn.completed", "usage": {"input_tokens": inp,
                        "output_tokens": out, "cached_input_tokens": min(inp, 2),
                        "reasoning_output_tokens": min(out, 1)}})
        self.declare("abandoned", "codex", None, "abandoned")
        self.event("abandoned", "missing", "missing_e", "abandoned_a", "worker",
                   "abandoned", {})

        before = self.cli("report")
        tasks = {task["task_id"]: task for task in before["tasks"]}
        self.assertEqual((before["attempted_tasks"], before["independently_accepted_tasks"],
                          before["acceptance_state"], before["tokens_per_independently_accepted_task"]),
                         (3, 0, "unverified", None))
        self.assertEqual((before["observed_cost_usd"], before["cost_missing_reason"],
                          before["estimated"]), (None, "not_collected", False))
        self.assertEqual((tasks["first"]["known_tokens"], tasks["retry"]["known_tokens"],
                          tasks["abandoned"]["known_tokens"]), (11, 27, None))
        self.assertEqual((tasks["first"]["attempt_count"], tasks["retry"]["attempt_count"],
                          tasks["abandoned"]["attempt_count"]), (1, 4, 1))
        self.assertEqual({k: v for k, v in tasks["retry"]["outcome_counts"].items() if v},
                         {"failed": 2, "returned": 2})
        self.assertEqual({k: v for k, v in tasks["retry"]["stage_counts"].items() if v},
                         {"judge": 1, "worker": 3})
        self.assertEqual(tasks["retry"]["inventory_state"], "complete")
        self.assertTrue(tasks["retry"]["tokens_complete"])
        self.assertTrue(tasks["first"]["tokens_complete"])
        self.assertEqual(tasks["abandoned"]["inventory_state"], "unknown")
        self.assertFalse(tasks["abandoned"]["tokens_complete"])
        self.assertEqual(tasks["abandoned"]["missing_fields"]["input_tokens"], 1)
        self.assertEqual((before["by_host"]["claude"]["known_tokens"],
                          before["by_host"]["codex"]["known_tokens"]), (11, 27))
        self.assertEqual((before["by_host"]["claude"]["attempted_tasks"],
                          before["by_host"]["codex"]["attempted_tasks"]), (1, 2))
        self.assertEqual((before["by_host"]["claude"]["event_count"],
                          before["by_host"]["codex"]["event_count"]), (1, 5))
        self.assertEqual((before["by_host"]["claude"]["attempt_count"],
                          before["by_host"]["codex"]["attempt_count"]), (1, 5))
        self.assertEqual((before["by_host"]["claude"]["complete_tasks"],
                          before["by_host"]["codex"]["complete_tasks"],
                          before["by_host"]["codex"]["incomplete_tasks"]), (1, 1, 1))
        self.assertEqual(self.cli("report"), before, "fresh process changed the accounting")

        event = {"session_id": "usage-fixture", "cwd": str(self.project),
                 "source": "compact", "harness_host": "codex"}
        for action in ("snapshot", "archive", "restore"):
            run = subprocess.run([sys.executable, str(DISPATCH), action, "--host=codex"],
                                 input=json.dumps(event), text=True, capture_output=True,
                                 cwd=self.project, env=self.env, timeout=10)
            self.assertEqual(run.returncode, 0, run.stderr)
            if action == "restore":
                self.assertIn("Review usage fixture", run.stdout)
        self.assertEqual(self.cli("report"), before, "compact/restore changed usage or dedup")
        self.event("first", "claude-message", "first_e", "first_a", "worker", "returned",
                   {"type": "assistant", "message": {"usage": {"input_tokens": 5,
                    "output_tokens": 3, "cache_read_input_tokens": 2,
                    "cache_creation_input_tokens": 1}}})
        self.assertEqual(self.cli("report"), before, "replay after restore recounted tokens")


if __name__ == "__main__":
    unittest.main()
