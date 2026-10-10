#!/usr/bin/env python3
"""Local, standard-library checks for CI and public policy files."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/ci.yml"
SECURITY = [
    "Only the latest release is supported.",
    "Report a vulnerability privately through GitHub\x27s private vulnerability reporting on this repository. If that is not available, open an issue that says a security report is waiting and leave the details out.",
    "These tools read command text and install files under your Claude Code and Codex homes. The danger guard is a best-effort check of common command forms, not a sandbox.",
    "No response time is promised.",
]
CONTRIBUTING = [
    "Run the checks in .github/workflows/ci.yml; none of them calls a model or the network.",
    "Python 3.10 or newer, standard library only.",
    "Keep a change small and add a test that fails without it.",
]


GITLEAKS = ('docker run --rm -v "$PWD:/repo" -w /repo zricethezav/gitleaks:v8.18.4 '
            'detect --source . --no-banner')
OPERATOR_END = r"(?:==|!=|&&|\|\||!|<|>)\s*$"
NETWORK_WORDS = r"\b(?:pip3?|apt(?:-get)?|brew|curl|wget|npm|npx|docker|git clone|git fetch|git pull)\b"


BANNED = r"\b(?:guarantee\w*|sla|within 24|within 48|always secure)\b"


def split_jobs(text):
    """Map job name to its block lines, by indentation under the top-level jobs: key."""
    jobs, current, in_jobs = {}, None, False
    for line in text.splitlines():
        if re.match(r"^jobs:\s*$", line):
            in_jobs = True
            continue
        if not in_jobs:
            continue
        if line.strip() and not line.startswith(" "):
            break
        found = re.match(r"^  ([\w-]+):\s*$", line)
        if found:
            current = found.group(1)
            jobs[current] = []
        elif current is not None:
            jobs[current].append(line)
    return jobs


def run_lines(block):
    """Yield (line, in_run) for the lines of a job block; in_run is true inside run: scripts."""
    run_indent = None
    for line in block:
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())
        if run_indent is not None and stripped and indent <= run_indent:
            run_indent = None
        started = stripped.lstrip("- ").startswith("run:")
        yield line, run_indent is not None or started
        if started:
            run_indent = indent


def split_steps(block):
    """Return step blocks at the workflow's six-space list indentation."""
    steps = []
    for line in block:
        if re.match(r"^      - ", line):
            steps.append([])
        if steps:
            steps[-1].append(line)
    return steps


def validate(text):
    jobs = split_jobs(text)
    for name in ("test", "together", "secrets"):
        assert name in jobs, name + " job"
    assert re.search(r"^env:\n  ROUTER_REPOSITORY: aeiadev/router$", text, re.M), "Router repository"
    test, together, secrets = ("\n".join(jobs[n]) for n in ("test", "together", "secrets"))
    assert re.search(r"^      matrix:\n        os: \[ubuntu-latest, macos-latest\]$", test, re.M), "OS matrix"
    assert re.search(r'^        python-version: \["3.10", "3.13"\]$', test, re.M), "Python matrix"
    assert test.count("fetch-depth: 0") >= 2, "full checkout history"
    plugin_steps = split_steps(jobs["test"])
    expected_plugin = (("Plugin drift", "python3 scripts/gen_plugin.py --check"),
                       ("Plugin shared-roles", "diff -r plugins/shared-roles router/plugins/shared-roles"),
                       ("Plugin validate", "if command -v claude >/dev/null; then claude plugin validate .; claude plugin validate plugins/harness; claude plugin validate plugins/shared-roles; fi"))
    python_steps = [step for step in plugin_steps if any(line.strip() == "- name: Python tests" for line in step)]
    assert len(python_steps) == 1, "Python tests step"
    assert [line.rstrip() for line in python_steps[0]] == [
        "      - name: Python tests", "        env:", "          ROUTER_DIR: router",
        '        run: for t in tests/test_*.py; do python3 "$t" || exit 1; done'], "Python tests ROUTER_DIR"
    shared = [i for i, step in enumerate(plugin_steps) if any(line.strip() == "- name: Shared hashes" for line in step)]
    assert len(shared) == 1, "Shared hashes step"
    indexes = []
    for name, command in expected_plugin:
        matching = [i for i, step in enumerate(plugin_steps)
                    if any(line.strip() == "- name: " + name for line in step)]
        assert len(matching) == 1, name + " step"
        index = matching[0]
        run = [line.strip()[5:] for line in plugin_steps[index] if line.strip().startswith("run: ")]
        assert run == [command], name + " command"
        indexes.append(index)
    assert shared[0] < indexes[0] < indexes[1] < indexes[2], "Plugin steps order"
    assert "repository: ${{ env.ROUTER_REPOSITORY }}" in test, "Router checkout in test"
    for name, sha in (("ROUTER_V01", "36acbaf"), ("ROUTER_V02", "4214545"),
                      ("HARNESS_V01", "063388b"), ("HARNESS_V02", "3b80d6f")):
        assert re.search(r"^\s+" + name + r"=", test, re.M) and sha in test, name + " archive"
        assert 'echo "' + name + "=$" + name + '" >> "$GITHUB_ENV"' in test, name + " export"
    for command in ("for t in tests/test_*.py", "for t in tests/test_*.sh",
                    "bash tests/fresh-user.sh", "bash tests/fresh-user-codex.sh",
                    "python3 codex/generate_agents.py --check", "shasum -a 256 -c SHARED.sha256"):
        assert command in test, command
    for job, block in jobs.items():
        for line, in_run in run_lines(block):
            if in_run and re.search(NETWORK_WORDS, line):
                assert job == "secrets" and re.fullmatch(r"\s*run: " + re.escape(GITLEAKS), line), \
                    "network use in " + job + " job: " + line.strip()
    for line in jobs["test"]:
        found = re.search(r"uses: (\S+)@", line)
        assert not found or found.group(1) in ("actions/checkout", "actions/setup-python"), line
    assert re.search(r"uses: actions/checkout@\S+\n\s+with:\n(?:\s+[\w-]+: .*\n)*?"
                     r"\s+repository: \$\{\{ env\.ROUTER_REPOSITORY \}\}\n", together + "\n"), \
        "together job checks out Router"
    together_steps = split_steps(jobs["together"])
    runner = [step for step in together_steps if any(re.fullmatch(r"\s*run: bash router/tests/together\.sh", line)
                                                    for line in step)]
    assert len(runner) == 1, "together command"
    assert any(re.fullmatch(r"\s*HARNESS_DIR: \$\{\{ github\.workspace \}\}", line) for line in runner[0]), \
        "together HARNESS_DIR"
    lines = [l.strip() for l in jobs["secrets"]]
    assert any(re.fullmatch(r"run: " + re.escape(GITLEAKS), l) for l in lines), "gitleaks command"
    assert "gitleaks/gitleaks-action" not in text, "licence-key action"
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("uses:"):
            assert re.search(r"uses: \S+@\S+", stripped), "unpinned action: " + stripped
        if stripped.startswith("if:"):
            expression = stripped[3:].strip()
            assert expression and not re.search(OPERATOR_END, expression), "incomplete if: " + line
            assert expression.count("'") % 2 == 0 and expression.count('"') % 2 == 0, "quotes: " + line
        assert line.count("${{") == line.count("}}"), "unbalanced expression: " + line
        for expression in re.findall(r"\$\{\{(.*?)\}\}", line):
            assert expression.strip() and not re.search(OPERATOR_END, expression), "incomplete expression: " + line
            assert expression.count("'") % 2 == 0 and expression.count('"') % 2 == 0, "expression quotes: " + line
    for block in jobs.values():
        for line, in_run in run_lines(block):
            if in_run:
                for token in re.findall(r"(?<![\w/])(?:tests|scripts|codex|agents)/[\w.*-]+(?:/[\w.*-]+)*", line.strip()):
                    if "*" not in token:
                        assert (ROOT / token).exists(), "missing run path: " + token


class WorkflowTests(unittest.TestCase):
    def test_workflow(self):
        self.assertTrue(WORKFLOW.is_file())
        validate(WORKFLOW.read_text())

    def test_validator_rejects_broken_copies(self):
        text = WORKFLOW.read_text()
        together_checkout = ("      - uses: actions/checkout@v4\n        with:\n"
                             "          repository: ${{ env.ROUTER_REPOSITORY }}\n"
                             "          path: router\n      - name: Together")
        self.assertEqual(text.count(together_checkout), 1)
        plugin_line = "      - name: Plugin drift\n        run: python3 scripts/gen_plugin.py --check\n"
        plugin_broken = (text.replace(plugin_line, ""),
                         text.replace("python3 scripts/gen_plugin.py --check", "python3 scripts/gen_plugin.py"),
                         text.replace("python3 scripts/gen_plugin.py --check", "python3 scripts/gen_plugin.py --check || true"),
                         text.replace(plugin_line, "").replace("  together:", "  together:\n" + plugin_line, 1),
                         text.replace("claude plugin validate plugins/shared-roles; fi",
                                      "pip install x; claude plugin validate plugins/shared-roles; fi"))
        for broken in (text.replace("        env:\n          ROUTER_DIR: router\n", "", 1),
                       text.replace("ROUTER_DIR: router", "ROUTER_DIR: ../router", 1)):
            with self.subTest(router=broken[:70]), self.assertRaises(AssertionError) as raised:
                validate(broken)
            self.assertEqual(str(raised.exception), "Python tests ROUTER_DIR")
        for broken in plugin_broken:
            with self.subTest(plugin=broken[:70]), self.assertRaises(AssertionError) as raised:
                validate(broken)
            self.assertRegex(str(raised.exception), r"^(Plugin drift step|Plugin drift command|Plugin validate command)$")
        for broken in (text.replace("macos-latest", "", 1),
                       re.sub(r"^  together:.*?(?=^  secrets:)", "", text, flags=re.M | re.S),
                       text + "\n      - name: Broken\n        run: tests/nope.sh\n",
                       text.replace(together_checkout, "      - name: Together"),
                       text.replace("--source .", "--source /repo"),
                       text.replace('-w /repo ', ""),
                       text.replace("run: python3 codex/generate_agents.py --check",
                                    "run: pip install x && python3 codex/generate_agents.py --check"),
                       text.replace("          HARNESS_DIR: ${{ github.workspace }}\n        run: bash router/tests/together.sh",
                                    "        run: bash router/tests/together.sh\n      - name: Misplaced env\n"
                                    "        env:\n          HARNESS_DIR: ${{ github.workspace }}"),
                       text.replace("run: bash router/tests/together.sh",
                                    "run: bash router/tests/together.sh\n      - name: Network install\n"
                                    "        run: pip install x && bash router/tests/together.sh"),
                       text.replace("run: docker run --rm", "run: curl x\n      - name: Gitleaks again\n        run: docker run --rm"),
                       text.replace("path: router\n          fetch-depth: 0",
                                    "path: ${{ 'router }}\n          fetch-depth: 0", 1),
                       text.replace("path: router\n          fetch-depth: 0",
                                    "path: ${{ env.X == }}\n          fetch-depth: 0", 1)):
            with self.subTest(broken=broken[:80]), self.assertRaises(AssertionError):
                validate(broken)

    def test_banned_words_match_whole_words_only(self):
        self.assertIsNone(re.search(BANNED, "we translate and slate text".lower()))
        self.assertIsNotNone(re.search(BANNED, "no sla here".lower()))
        self.assertIsNotNone(re.search(BANNED, "We guarantee it".lower()))

    def test_policies(self):
        for filename, required in (("SECURITY.md", SECURITY), ("CONTRIBUTING.md", CONTRIBUTING)):
            with self.subTest(filename=filename):
                path = ROOT / filename
                self.assertTrue(path.is_file())
                lines = path.read_text().splitlines()
                self.assertLessEqual(len(lines), 40)
                for item in required:
                    self.assertIn(item, lines)
                content = path.read_text()
                self.assertNotIn("\u2014", content)
                self.assertNotRegex(content.lower(), BANNED)


if __name__ == "__main__":
    unittest.main(verbosity=2)
