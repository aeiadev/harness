"""Real opt-in SSH proof. Required Linux gate fails if fixture is unavailable."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import subprocess
import tempfile
import unittest

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hooks/checkpoint"))
sys.path.insert(0, str(ROOT / "hooks/guard"))
from observation import assess

spec = importlib.util.spec_from_file_location("ssh_disposable", ROOT / "tests/fixtures/ssh-disposable/run.py")
fixture_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture_module)
SSHFixture = fixture_module.SSHFixture
PAYLOAD, TARGET = fixture_module.PAYLOAD, fixture_module.TARGET


def digest(data):
    return hashlib.sha256(data).hexdigest()


def expected():
    return {"schema_version": 1, "task_id": "ssh_fixture", "target_id": "site_version",
            "revision_id": "rev_1", "requirements": {"content": digest(PAYLOAD)}}


def observed(data, revision="rev_1"):
    return {"schema_version": 1, "read_status": "ok", "task_id": "ssh_fixture",
            "target_id": "site_version", "revision_id": revision,
            "evidence_id": "actual_ssh_readback", "verifier_id": "observer_account",
            "requirements": {"content": digest(data)}}


class OfflineSSHFixtureCleanup(unittest.TestCase):
    def fixture(self, outcomes):
        fixture = SSHFixture(engine="controlled-engine")
        calls = []
        pending = iter(outcomes)

        def engine(*args, **kwargs):
            calls.append((args, kwargs))
            if args[:2] == ("container", "inspect"):
                return subprocess.CompletedProcess(args, 125, stdout=b"")
            expected, outcome = next(pending)
            self.assertEqual(args[:len(expected)], expected)
            self.assertLessEqual(kwargs.get("timeout", 40), 40)
            if isinstance(outcome, BaseException):
                raise outcome
            return subprocess.CompletedProcess(args, outcome,
                                               stdout=b'[{"Id":"image"}]' if args[:2] == ("image", "inspect") else b"")

        fixture._engine = engine
        return fixture, calls

    def test_failed_removal_and_inspection_do_not_clear_ownership(self):
        fixture, calls = self.fixture([(("rm", "--force"), 125),
                                       (("rm", "--force"), 0)])
        fixture.started = True
        with self.assertRaises(fixture_module.FixtureUnavailable):
            fixture.close()
        self.assertTrue(fixture.started)
        fixture.close()
        fixture.close()
        self.assertFalse(fixture.started)
        self.assertEqual([args[0] for args, _ in calls].count("rm"), 2)
        self.assertTrue(all(args[-1] == fixture.name for args, _ in calls))

    def test_removal_exception_keeps_retry_possible(self):
        fixture, calls = self.fixture([(("rm", "--force"), OSError("engine unavailable")),
                                       (("rm", "--force"), 0)])
        fixture.started = True
        with self.assertRaises(OSError):
            fixture.close()
        self.assertTrue(fixture.started)
        fixture.close()
        fixture.close()
        self.assertFalse(fixture.started)
        self.assertEqual(len(calls), 2)

    def test_failed_launch_cleans_exact_name(self):
        fixture, calls = self.fixture([(("image", "inspect"), 0),
                                       (("run",), fixture_module.FixtureUnavailable("launch failed")),
                                       (("rm", "--force"), 0)])
        with self.assertRaises(fixture_module.FixtureUnavailable):
            fixture.__enter__()
        self.assertFalse(fixture.started)
        self.assertEqual(calls[-1][0], ("rm", "--force", fixture.name))
        fixture.close()
        self.assertEqual(len(calls), 3)

    def test_launch_timeout_with_failed_cleanup_retains_retry(self):
        fixture, calls = self.fixture([(("image", "inspect"), 0),
                                       (("run",), subprocess.TimeoutExpired("run", 40)),
                                       (("rm", "--force"), 125),
                                       (("rm", "--force"), 0)])
        with self.assertRaises(fixture_module.FixtureUnavailable):
            fixture.__enter__()
        self.assertTrue(fixture.started)
        fixture.close()
        self.assertFalse(fixture.started)
        self.assertTrue(all(args[-1] == fixture.name for args, _ in calls if args[0] == "rm"))


class FixturePins(unittest.TestCase):
    def test_image_installs_exactly_the_locked_versions(self):
        # Every installed package is pinned and recorded in the lock that the fixture checks
        # against dpkg. The OpenSSH binaries come from one source package and must share one
        # version, or apt cannot satisfy their exact inter-dependencies.
        here = ROOT / "tests/fixtures/ssh-disposable"
        lock = json.loads((here / "fixture-lock.json").read_text(encoding="utf-8"))
        text = (here / "Dockerfile").read_text(encoding="utf-8")
        self.assertEqual(text.splitlines()[0], "FROM " + lock["base_image"])
        installs = re.findall(r"apt-get install ([^&]*)&&", text)
        self.assertEqual(len(installs), 1, installs)
        names = [word for word in installs[0].split() if not word.startswith("-")]
        self.assertTrue(all("=" in word for word in names), names)
        pins = dict(word.split("=", 1) for word in names)
        self.assertEqual(len(pins), len(names), names)
        self.assertEqual(pins, lock["packages"])
        openssh = {name: version for name, version in pins.items() if name.startswith("openssh-")}
        self.assertEqual(sorted(openssh), ["openssh-client", "openssh-server", "openssh-sftp-server"])
        self.assertEqual(len(set(openssh.values())), 1, openssh)


class RealSSHObservation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        enabled = os.environ.get("SSH_FIXTURE_REQUIRED") == "1" or os.environ.get("SSH_FIXTURE_RUN") == "1"
        if not enabled:
            raise unittest.SkipTest("real SSH integration unrun; set SSH_FIXTURE_REQUIRED=1 for mandatory gate")

    def fixture(self):
        return SSHFixture(os.environ.get("SSH_FIXTURE_ENGINE"),
                          os.environ.get("SSH_FIXTURE_IMAGE", fixture_module.DEFAULT_IMAGE))

    def test_actual_readback_server_denial_and_report_never_replays(self):
        with self.fixture() as server:
            before = server.execute(server.ssh("observer", "cat " + TARGET))
            self.assertEqual(before.returncode, 0)
            self.assertNotEqual(before.stdout, PAYLOAD)
            result = server.smoke()
            self.assertTrue(result["passed"], result)
            actual = server.execute(server.ssh("observer", "cat " + TARGET))
            self.assertEqual(actual.returncode, 0)
            self.assertEqual(actual.stdout, PAYLOAD)
            denied = server.execute(server.ssh("observer", "cat /srv/site/worker-only"))
            self.assertNotEqual(denied.returncode, 0)
            self.assertEqual(denied.stdout, b"")
            unavailable = server.execute(server.ssh("observer", "cat " + TARGET).replace("-p 22 ", "-p 23 ", 1))
            self.assertEqual(unavailable.returncode, 255)
            mutations = server.mutations
            cases = [
                (observed(actual.stdout), "PASS"),
                (observed(before.stdout, "rev_0"), "FAIL"),
                (observed(b"mismatched fixture bytes"), "FAIL"),
                ({"schema_version": 1, "read_status": "denied"}, "UNKNOWN"),
                ({"schema_version": 1, "read_status": "unavailable"}, "UNKNOWN"),
            ]
            for proof, verdict in cases:
                with self.subTest(verdict=verdict, read_status=proof["read_status"]):
                    report = assess(expected(), proof)
                    self.assertEqual(report["reported_verdict"], verdict)
                    self.assertEqual(report["mode"], "report-only")
                    self.assertFalse(report["gates_mutation"])
                    self.assertFalse(report["host_verified"])
                    self.assertEqual(server.mutations, mutations)
            self.assertEqual(server.execute(server.ssh("observer", "cat " + TARGET)).stdout, PAYLOAD)

    def test_guard_allows_deployment_and_denies_before_dispatch(self):
        with tempfile.TemporaryDirectory(prefix="ssh-hook-") as temporary, self.fixture() as server:
            home = Path(temporary)
            project = home / "project"
            (project / ".harness").mkdir(parents=True)
            raw = json.dumps(server.policy()).encode()
            (project / ".harness/operations.json").write_bytes(raw)
            registry = home / "config/harness/operations-bindings.json"
            registry.parent.mkdir(parents=True, mode=0o700)
            registry.write_text(json.dumps({"schema_version": 1, "bindings": [{
                "project_root": str(project), "policy_path": ".harness/operations.json",
                "sha256": digest(raw), "mode": "enforce"}]}))
            registry.chmod(0o600)
            env = dict(os.environ, HOME=str(home), XDG_CONFIG_HOME=str(home / "config"),
                       XDG_STATE_HOME=str(home / "state"), HARNESS_PYTHON=sys.executable,
                       PYTHONDONTWRITEBYTECODE="1")
            env.pop("PYTHONPATH", None)

            def guarded(command, wanted):
                for tool, key in (("Bash", "command"), ("functions.exec_command", "cmd")):
                    payload = {"tool_name": tool, "tool_input": {key: command},
                               "cwd": str(project)}
                    result = subprocess.run(["sh", str(ROOT / "hooks/pyrun.sh"), "harness",
                                             "block", "guard/danger_cmd_guard.py"],
                                            input=json.dumps(payload).encode(), capture_output=True,
                                            env=env, cwd=home, timeout=10)
                    self.assertEqual(result.returncode, wanted, (tool, result.stderr))
                    self.assertEqual(result.stdout, b"")
                    if wanted == 0:
                        self.assertEqual(result.stderr, b"")
                    else:
                        self.assertTrue(result.stderr)
                        self.assertLessEqual(len(result.stderr), 512)
                        self.assertNotIn(str(home).encode(), result.stderr)
                return result.returncode

            for command, mutation in ((server.upload(), True),
                    (server.ssh("deploy", "mv /srv/site/releases/artifact.txt " + TARGET), True),
                    (server.ssh("observer", "cat " + TARGET), False),
                    ("rsync -a /tmp/tree/ deploy@127.0.0.1:/srv/site/releases/tree/", True)):
                guarded(command, 0)
                self.assertEqual(server.execute(command, mutation=mutation).returncode, 0)
            before = server.mutations
            prohibited = server.ssh("deploy", "rm " + TARGET)
            # Dispatch is contingent on the actual hook exit, with a fixture-only
            # enrolled policy. It must reject before a remote process is started.
            if guarded(prohibited, 2) == 0:
                server.execute(prohibited, mutation=True)
            self.assertEqual(server.mutations, before)
            self.assertEqual(server.execute(server.ssh("observer", "cat " + TARGET)).stdout, PAYLOAD)


if __name__ == "__main__":
    unittest.main()
