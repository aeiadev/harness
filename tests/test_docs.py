"""Pin the reader-facing 0.3.0 contract without network or user state."""

import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCS = Path(os.environ.get("HARNESS_DOCS_ROOT", ROOT))
STATUSLINE = ("A plugin cannot set the main statusline. With a script install, `bash install.sh --statusline` sets it. "
              "With a plugin install, add this command as the `statusLine.command` in Claude settings by hand: "
              '`python3 "$HOME/.claude/harness/statusline/statusline.py"`.')
PURGE = ("Alone, `--purge` lists and removes only Harness's own backups; with `--uninstall`, it uninstalls first and then purges. "
         "`--dry-run` lists changes without writing.")
PRESSURE = ("The pressure file for Router is `$XDG_STATE_HOME/claude-harness/pressure/<session-hash>.json`, "
            "or `$HOME/.local/state` when XDG_STATE_HOME is unset or relative. Harness hashes "
            "the session ID, writes the current window, percent, level, and host, and replaces "
            "the file privately with 0600 mode. Router reads this signal for context pressure nudges.")
RESTORE_LINE = ("- Restore original settings bytes and mode without group or other write when a backup matches exactly, "
                "otherwise in standard JSON layout, in either install or uninstall order with Router 0.3.0; reruns after Router edits settings make no change.")
ROLES_LINE = ("- Require TASK and RETURN for the sweeper, researcher, and planner roles, and all four fields "
              "(TASK, FILES, BAR, RETURN) for judge and the other shared roles; keep the same brief rule in Codex TOML files.")
ROUTER_DIRS = [Path(os.environ["ROUTER_DIR"])] if os.environ.get("ROUTER_DIR") else []
ROUTER_DIRS.append(ROOT.parent / "router")
ROUTER = next((d / "README.md" for d in ROUTER_DIRS if (d / "README.md").exists()), None)
FIELDS = ("TASK", "FILES", "BAR", "RETURN")


def read(name):
    return (DOCS / name).read_text(encoding="utf-8")


def block(text):
    match = re.findall(r"<!-- shared:begin -->.*?<!-- shared:end -->", text, re.S)
    if len(match) != 1:
        raise AssertionError(f"shared block count: {len(match)}")
    return match[0]


def squash(text):
    return " ".join(text.split())


def section(text, heading):
    """Text of one heading's section with every shared block cut out."""
    text = re.sub(r"<!-- shared:begin -->.*?<!-- shared:end -->", "", text, flags=re.S)
    level = len(heading) - len(heading.lstrip("#"))
    match = re.search(rf"(?m)^{re.escape(heading)}\n(.*?)(?=^#{{1,{level}}} |\Z)", text, re.S)
    if match is None:
        raise AssertionError(f"missing section {heading}")
    return match.group(1)


def fenced(text, heading):
    match = re.search(rf"(?m)^#### {re.escape(heading)}\n.*?^```text\n(.*?)^```", text, re.S | re.M)
    if match is None:
        raise AssertionError(f"missing {heading} brief")
    return match.group(1)


def fields(brief, read_set=False):
    found = []
    for line in brief.splitlines():
        match = re.fullmatch(r"\s*((?:TASK|FILES|BAR|RETURN)(?=\s)|(?i:TASK|FILES|BAR|RETURN)(?=:))[:\s]+(.+)", line)
        if match is not None:
            found.append((match.group(1).upper(), match.group(2).strip()))
    names = [name for name, _ in found]
    required = ["TASK", "RETURN"] if read_set else list(FIELDS)
    for name in required:
        if names.count(name) != 1:
            raise AssertionError(f"{name} count in brief: {names}")
    if names != [name for name in FIELDS if name in names]:
        raise AssertionError(f"field order or duplicate: {names}")
    if any(value == "" for _, value in found):
        raise AssertionError("empty brief field")
    for name, value in found:
        if name == "BAR" and re.match(r"^timeout [1-9][0-9]*\s", value) is None:
            raise AssertionError(f"BAR lacks timeout: {value}")


class Docs(unittest.TestCase):
    def test_result_first(self):
        text = read("README.md")
        self.assertEqual(text.splitlines()[0], "# Harness")
        first = text.split("# Harness", 1)[1].lstrip().split("\n\n", 1)[0]
        for word in ("hook", "SQLite", "install.sh", "manifest"):
            self.assertNotIn(word, first)
        for phrase in ("long sessions", "plan", "next move", "compaction", "real context window", "destructive shell commands", "nine roles", "checked brief"):
            self.assertIn(phrase, first)
        self.assertLess(text.index("## The parts"), text.index("## Install"))

    def test_shared_and_plugin(self):
        text = read("README.md")
        if ROUTER is not None:
            self.assertEqual(block(text), block(ROUTER.read_text(encoding="utf-8")))
        else:
            print("SKIP Router shared block comparison: set ROUTER_DIR or place Router at ../router")
            block(text)
        self.assertLess(text.index("## Install"), text.index("<!-- shared:begin -->"))
        self.assertLess(text.index("<!-- shared:end -->"), text.index("## Quick start"))
        self.assertIn("[Router](https://github.com/aeiadev/router)", text)
        for phrase in ("/plugin marketplace add aeiadev/harness", "/plugin install harness@harness", "/plugin install shared-roles@harness", '"enabledPlugins": {"harness@harness": true}', "A script install wins over the plugin", ):
            self.assertIn(phrase, section(text, "## Install"))
        self.assertIn(STATUSLINE, squash(section(text, "## Install")))
        for target in re.findall(r"(?<!!)\[[^]]+\]\(([^)]+)\)", text):
            if "://" not in target and not target.startswith("#"):
                self.assertEqual((DOCS / target.split("#", 1)[0]).exists(), True, target)

    def test_briefs_and_example_budget(self):
        text = read("README.md")
        for name in ("README.md", "examples/CLAUDE.md.example", "examples/AGENTS.md.example"):
            for line in read(name).splitlines():
                if line.startswith("BAR:"):
                    self.assertRegex(line, r"^BAR: timeout [1-9][0-9]*\s")
        for brief in re.findall(r"```text\n(.*?)^```", text, re.S | re.M):
            if re.search(r"(?m)^TASK:", brief):
                fields(brief, read_set="FILES:" not in brief)
        for role in ("Sweeper", "Researcher", "Planner"):
            fields(fenced(text, role), read_set=True)
        for role in ("Builder", "Judge"):
            fields(fenced(text, role))
        for name in ("CLAUDE", "AGENTS"):
            example = read(f"examples/{name}.md.example")
            self.assertLessEqual(len(example.splitlines()), 20)
            for role in ("sweeper", "researcher", "planner", "builder", "builder-in-place", "worker", "test-writer", "docs-writer", "judge"):
                self.assertEqual(len(re.findall(rf"(?m)^- {role}: ", example)), 1, role)
        self.assertIn("BAR: timeout 60 python3 tests/test_title.py", text)

    def test_operations_and_limits(self):
        text = read("README.md")
        install, config, limits = section(text, "## Install"), section(text, "## Configuration"), section(text, "### Host limits")
        for phrase in ("0600", "kept after uninstall", "--purge", "--uninstall", "--dry-run", "last hook write", "token budget", "damaged settings", "Router-claimed roles are never rewritten"):
            self.assertIn(phrase, install)
        self.assertIn(PURGE, squash(install))
        self.assertIn(PRESSURE, squash(config))
        self.assertIn("When Codex usage is incomplete, the hook writes no spend file and the segment shows nothing instead of `$--`", squash(config))
        for phrase in ("rollout window", "resume-cache warning", "Cache-hit percent", "spend hook", "run_in_background", "npm run dev &", "role files on both hosts"):
            self.assertIn(phrase, limits)
        for name in ("Restore after compaction", "Window-relative reminders", "Resume-cache warning", "Cache-hit percent", "Codex token fallback", "Shell guard", "Background exemption", "Brief rule"):
            self.assertEqual(len(re.findall(rf"(?m)^\| {re.escape(name)} \|", text)), 1, name)
        commands = re.findall(r"```bash\n(.*?)```", text, re.S)
        safe = [line for group in commands for line in group.splitlines() if line in ("bash install.sh --status", "bash install.sh --dry-run --purge")]
        self.assertEqual(safe, ["bash install.sh --status", "bash install.sh --dry-run --purge"])
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, **{key: str(Path(tmp) / key.lower()) for key in ("HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "CLAUDE_HOME", "CODEX_HOME")}}
            for command in safe:
                result = subprocess.run(["bash", str(ROOT / "install.sh"), *command.split()[2:]], cwd=ROOT, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_reminder_thresholds_documented(self):
        config = squash(section(read("README.md"), "## Configuration"))
        first = re.search(r"```json\n(.*?)```", section(read("README.md"), "## Configuration"), re.S).group(1)
        self.assertNotIn("remind_at", first)
        self.assertNotIn("urgent_at", first)
        for phrase in ("65% and 85% of the live context window", "Claude statusline", "rollout window on Codex",
                       "last statusline window", "`window_tokens` is only the fallback",
                       "turns off the live-window default for that threshold",
                       "fall back to 65% and 85%"):
            self.assertIn(phrase, config)
        self.assertNotIn("must increase in that order", config)
        for phrase in ("`remind_at` alone is at or above 85% of the window and below the window, the urgent threshold becomes the window",
                       "Explicit values must satisfy 0 < `remind_at` < `urgent_at` < the window",
                       "only the urgent value that `remind_at` alone produces may equal the window",
                       "then, on Claude only, the last statusline window seen if it is under 10 minutes old"):
            self.assertIn(phrase, config)
        self.assertNotIn("<= the window", config)

    def test_restore_order_documented(self):
        config = squash(section(read("README.md"), "## Configuration"))
        self.assertIn("`restore_order` lists the checkpoint section headings to restore first, in order, "
                      "ignoring case; any text before the first `##` heading is restored first, and other sections follow "
                      'in file order. The default is `["Next move", "Objective", "In flight"]`, and a value that is not a list, '
                      "an empty list, or a list with a blank or non-text item falls back to it.", config)

    def test_changelog_defaults(self):
        text = read("CHANGELOG.md")
        release = text.split("## 0.3.0\n", 1)[1].split("\n## 0.2.0", 1)[0]
        self.assertIn("- Default checkpoint reminders to 65% and 85% of the live context window; explicit `remind_at` and `urgent_at` stay fixed token counts.",
                      release.splitlines())

    def test_changelog(self):
        text = read("CHANGELOG.md")
        headings = re.findall(r"(?m)^## (\d+\.\d+\.\d+)$", text)
        self.assertEqual(headings[0], (ROOT / "VERSION").read_text().strip())
        release = text.split("## 0.3.0\n", 1)[1].split("\n## 0.2.0", 1)[0]
        self.assertEqual(re.findall(r"(?m)^### (\w+)", release), ["Added", "Changed", "Fixed"])
        self.assertIn("Upgrade notes", release)
        lines = release.splitlines()
        self.assertEqual(lines.count(RESTORE_LINE), 1)
        self.assertEqual(lines.count(ROLES_LINE), 1)
        parts = re.split(r"(?m)^### ", release)
        added = next(p for p in parts if p.startswith("Added"))
        fixed = next(p for p in parts if p.startswith("Fixed"))
        self.assertIn("- Add a cache-hit percent segment to the Claude statusline.", added.splitlines())
        self.assertIn("- Add a Claude-only warning for expired resume caches when the host supplies that signal.", added.splitlines())
        self.assertNotIn("resume cache", fixed)
        installer = (ROOT / "install.sh").read_text()
        for flag in re.findall(r"--[a-z][a-z-]+", release):
            self.assertIn(flag, installer)
        self.assertLessEqual(max(map(len, text.splitlines())), 400)
        self.assertNotIn("\u2014", text)


if __name__ == "__main__":
    unittest.main()
