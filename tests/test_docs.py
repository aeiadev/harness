"""Pin the reader-facing 0.3.0 contract without network or user state."""

import importlib.util
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from _platform import BASH, posix  # noqa: E402
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
PLATFORM_ROWS = [
    "| Platform | Requirements | Claude Code | Codex |",
    "| --- | --- | --- | --- |",
    "| Linux | Bash 3.2 or newer, Python 3.9 or newer, Git. | Script install; hooks start through `sh` and `bash`. | Script install; hooks start through `sh` and `bash`. |",
    "| Stock macOS | The system `/bin/bash` 3.2 and `/usr/bin/python3` 3.9 are enough, with Git. | As on Linux. | As on Linux. |",
    "| Native Windows | Git for Windows and Python 3.9 or newer; run `install.sh` in Git Bash. | Claude Code runs the hooks in Git Bash. "
    "| Codex runs the hooks in PowerShell through `command_windows`, with the Python that ran the installer. |",
    "| WSL | The Linux requirements, inside WSL. | As on Linux, installed inside WSL. | As on Linux, installed inside WSL. |",
]
PYTHON_LOOKUP = (
    "The installer and every hook find Python through `hooks/pyrun.sh`: `HARNESS_PYTHON` when set, then the interpreter "
    "recorded in the Harness state folder, then `python3`, `python` and `py -3`. Only Python 3.9 or newer counts, and the "
    "Microsoft Store `python3` stub is skipped. A hook that finds Python records it there when it exits 0, once that state folder exists; "
    "`--dry-run` and `--status` write nothing. Without a usable Python, the installer stops with "
    "`harness: missing requirement: python3 (Python 3.9 or newer)`. A hook prints "
    "`harness: no Python 3.9 or newer found (python3, python, py -3); install one or set HARNESS_PYTHON` on stderr and exits 1, "
    "the danger guard exits 2 so the command stays blocked, and the statusline shows the same line.")
HOME_LINE = ("On native Windows, Harness takes the home from `HOME` before Windows Python's `USERPROFILE` lookup, "
             "so Git Bash, the hosts and Harness agree on it.")
PAIRING = "Router 0.5.0 pairs with Harness 0.5.0. Native Windows support starts at 0.3.1."
LIMITS = [
    "- On native Windows, POSIX owner checks and file modes such as 0600 and 0700 are not enforced; use Windows account "
    "and folder permissions to control access. The file modes named elsewhere in this README apply to POSIX systems.",
    "- Harness installs regular files and creates no symbolic links, so it needs no symlink permission. Where symlinks "
    "are not allowed, Router installs copies instead, and its upgrade and uninstall handle those copies.",
    "- Codex on native Windows runs the hooks through PowerShell with the Python that ran the installer. If that Python "
    "is moved or removed, a hook prints `harness: Python missing; rerun install.sh`; run `install.sh` again.",
    "- The danger guard reads POSIX shell syntax; it does not inspect PowerShell commands.",
    "- The Microsoft Store `python3` stub is never used; with no other Python 3.9 or newer, the missing-Python message above applies.",
    "- The manual plugin statusline command calls `python3` directly, not through the launcher, so on native Windows it "
    "needs a real `python3` on `PATH`.",
    "- On native Windows, use file names without control characters, and keep installation, state and backup paths "
    "below the 260-character MAX_PATH limit with room for the files beneath them; longer paths are not supported.",
    "- An install made on native Windows by Router or Harness 0.2 is not a supported starting point. Upgrade both tools "
    "to 0.3.1 before uninstalling either one there: a 0.2 uninstaller predates Windows support and can misread the "
    "current sibling's manifest paths.",
]
TEST_NOTES = (
    "On native Windows, some checks skip and print why: POSIX owner and mode assertions and chmod-based unreadable-file "
    "fixtures, FIFO fixtures (Windows Python has no `os.mkfifo`), symlink assertions when the account cannot create "
    "symlinks, and fixtures that need control-character file names or paths past 260 characters. The POSIX-API "
    "simulation self-check runs only under `PYTHONPATH=tests/win_sim`. Tests that deny access with mode 000 also skip "
    "under root. Upgrade tests from the 0.1 and 0.2 releases run those releases' installers, which need Python 3.10 "
    "or newer; without one, only those cases skip, as on stock macOS.")
GUARD_BINDING = ("It also does not follow a value bound by `read`, `printf -v` or a for-loop variable and later run "
                 "by a shell.")
STATUSLINE_RULE = "The live statusline is Claude-only. A script install sets it when no `statusLine` exists."
PAIRS_031 = ("Pairs with Router 0.3.1. An older Router that installed different shared roles keeps them, and the "
             "installer prints one notice to upgrade it.")
RECIPE = ("That file comes from the script install (`install.sh`); a plugin-only install does not create it, "
          "so this command needs the script install too.")
RECIPE_031 = ("- The README says the manual plugin statusline command runs a file that only the script install "
              "(`install.sh`) creates; a plugin-only install does not create it.")
STATUSLINE_031 = ("- A script install sets the Claude statusline when none exists, without `--statusline`; the flag only "
                  "replaces an existing one. An upgrade moves a statusline an older Harness wrote to the launcher form.")
LIMITS_031 = ("- Limits on native Windows: POSIX owner checks and file modes such as 0600 are not enforced; file names "
              "with control characters and paths past the 260-character MAX_PATH are not supported; the danger guard "
              "reads POSIX shell syntax, not PowerShell. Support there starts at 0.3.1: upgrade both Router and Harness "
              "to 0.3.1 before uninstalling.")
RELEASE_STEPS = [
    "1. Set VERSION to X.Y.Z and start CHANGELOG.md with `## X.Y.Z`; its first line starts with `Pairs with Router X.Y.Z.`",
    "2. Check the release text with `scripts/release-notes.sh X.Y.Z`.",
    "3. Commit, then push Router main and then Harness main, before any tag:",
    "4. Tag each repository, Router first, and push the tag:",
    "5. Publish the GitHub Release with:",
]
RELEASE_CODE = ["git push origin main", "git tag vX.Y.Z", "git push origin vX.Y.Z",
                'gh release create vX.Y.Z --title "Harness X.Y.Z" --notes "$(scripts/release-notes.sh X.Y.Z)"']
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
                result = subprocess.run([BASH, posix(ROOT / "install.sh"), *command.split()[2:]], cwd=ROOT, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_custom_install_commands_profile(self):
        text = squash(read("docs/custom-install.md"))
        for phrase in ("bash install.sh --profile commands --host claude --dry-run > plan.json",
                       "bash install.sh --profile commands --host claude --plan plan.json",
                       "`--host both` is refused", "`~/.local/bin/claude-harness`", "`~/.local/bin/codex-harness`",
                       "`~/.claude/harness-commands`", "`~/.codex/harness-commands`",
                       "`$XDG_STATE_HOME/harness-commands/<host>`", "`HARNESS_COMMANDS_STATE`",
                       "`$XDG_STATE_HOME/claude-harness`", "registers no hook",
                       "never adopted", "byte-identical", "`scripts/commands_install.py`",
                       "missing installation requirement", "does not establish live enforcement"):
            self.assertIn(phrase, text)
        match = re.search(r"Only ((?:`[a-z]+`(?:, | and ))+`[a-z]+`) are available", text)
        self.assertIsNotNone(match)
        spec = importlib.util.spec_from_file_location("docs_command_runtime", ROOT / "hooks/checkpoint/command_runtime.py")
        runtime = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runtime)
        self.assertEqual(re.findall(r"`([a-z]+)`", match.group(1)), list(runtime.COMMANDS + runtime.META))
        self.assertNotIn("\u2014", read("docs/custom-install.md"))

    def test_remote_policy_and_qualification_truth(self):
        readme = squash(read("README.md"))
        for phrase in ("Full Router and Harness suites ran natively on Linux, macOS and Windows with Python 3.9 and 3.13 (six cells), with 0 failures and every skip named and reviewed", "Windows skips the POSIX-only tests", "required SSH integration test ran on Linux only", "PyYAML parity tests ran on Linux with Python 3.9 and 3.13 with PyYAML installed", "Live-host and installed-runtime qualification, and paid model or connector pilots, have not been run",
                       "off until an owner binds", "install never binds one", "registers no hook",
                       "SSH_FIXTURE_REQUIRED=1", "[commands-only installation](docs/custom-install.md)"):
            self.assertIn(phrase, readme)
        self.assertNotIn("remote commands sent as quoted text to `ssh`", readme)
        contributing = squash(read("CONTRIBUTING.md"))
        self.assertIn("The exception is opt-in", contributing)
        self.assertIn("SSH_FIXTURE_REQUIRED=1", contributing)
        self.assertNotRegex(readme, r"(?i)coming (soon|next)")
        version = (ROOT / "VERSION").read_text().strip()
        self.assertEqual(version, "0.5.0")
        for name in ("README.md", "CHANGELOG.md", "CONTRIBUTING.md", "docs/ssh-policy.md", "docs/operations.md", "docs/evaluation.md"):
            self.assertNotIn("\u2014", read(name), name)

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
                       "then, on Claude only, the last statusline window seen if it is 10 minutes old or less"):
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
        current = text.split("## 0.5.0\n", 1)[1].split("\n## 0.4.1", 1)[0]
        self.assertEqual(current.strip().splitlines()[0], "Pairs with Router 0.5.0.")
        self.assertNotIn("tagged or published", current)
        for phrase in ("Full Router and Harness suites ran natively on Linux, macOS and Windows with Python 3.9 and 3.13 (six cells), with 0 failures and every skip named and reviewed", "Windows skips the POSIX-only tests", "required SSH integration test ran on Linux only", "The PyYAML parity tests ran on Linux with Python 3.9 and 3.13 with PyYAML installed", "Live-host, installed-runtime and paid-pilot qualification have not been run",
                       "`install.sh --profile commands`", "registers no hook", "`ssh check`"):
            self.assertIn(phrase, current)
        self.assertNotRegex(current, r"(?i)coming|next release|will add")
        previous = text.split("## 0.4.1\n", 1)[1].split("\n## 0.4.0", 1)[0]
        self.assertEqual(previous.strip().splitlines()[0], "Pairs with Router 0.4.1.")
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

    def test_platform_table_and_requirements(self):
        install = section(read("README.md"), "## Install")
        lines = install.splitlines()
        rows = [line for line in lines if line.startswith("|")]
        self.assertEqual(rows[:len(PLATFORM_ROWS)], PLATFORM_ROWS)
        self.assertEqual(sum(line.startswith("| Platform |") for line in read("README.md").splitlines()), 1)
        self.assertIn("Use Bash, Python 3.9 or newer, and Git", install)
        self.assertEqual(re.findall(r"Python 3\.1[0-9] or newer", squash(read("README.md"))), ["Python 3.10 or newer"])  # the test note
        self.assertIn(PYTHON_LOOKUP, squash(install))
        self.assertIn(HOME_LINE, squash(install))
        launcher = (ROOT / "hooks/pyrun.sh").read_text(encoding="utf-8")
        installer = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn('message="$tool: no Python 3.9 or newer found (python3, python, py -3); install one or set $name"', launcher)
        self.assertIn("*/WindowsApps/*", launcher)
        self.assertIn("harness: missing requirement: python3 (Python 3.9 or newer)", installer)
        self.assertIn("'harness: Python missing; rerun install.sh'", installer)
        self.assertNotRegex(installer, r"symlink_to\(|os\.symlink\(")

    def test_platform_limits(self):
        limits = section(read("README.md"), "### Platform limits")
        self.assertIn(PAIRING, limits.splitlines())
        readme = squash(read("README.md"))
        self.assertIn("Download or clone the [Harness 0.5.0 release](https://github.com/aeiadev/harness/releases/tag/v0.5.0), then run from its directory:", readme)
        self.assertNotIn("tagged or published", readme)
        self.assertNotIn("releases/tag/v0.4.1", readme)
        self.assertIn("From the matching 0.5.0 checkout", read("docs/operations.md"))
        self.assertIn("From the matching 0.5.0 checkout", read("docs/evaluation.md"))
        self.assertEqual([line for line in limits.splitlines() if line.startswith("- ")], LIMITS)
        self.assertIn(TEST_NOTES, squash(section(read("README.md"), "## Test")))
        self.assertIn("python3 tests/run_all.py", section(read("README.md"), "## Test"))

    def test_guard_and_statusline_rules(self):
        text = squash(read("README.md"))
        self.assertIn("printf octal escapes such as `\\174`, or a here-string built from printf output. " + GUARD_BINDING, text)
        self.assertIn(STATUSLINE_RULE, text)
        self.assertIn(STATUSLINE + " " + RECIPE, squash(section(read("README.md"), "## Install")))
        self.assertFalse(any("statusline" in path.name.lower() for path in (ROOT / "plugins").rglob("*")))
        self.assertIn(RECIPE_031, read("CHANGELOG.md").split("## 0.3.1\n", 1)[1].split("\n## 0.3.0", 1)[0].splitlines())
        source = (ROOT / "codex/permissions.py").read_text(encoding="utf-8")
        self.assertIn("contents never enter that metadata. Python 3.9 and 3.10 conservatively decline multiline", source.splitlines())
        self.assertNotRegex(source, r"Python 3\.10 (?:has|conservatively declines)")
        self.assertIn("except ImportError:  # Python 3.9 and 3.10 have no standard TOML parser.", source.splitlines())

    def test_changelog_031(self):
        text = read("CHANGELOG.md")
        release = text.split("## 0.3.1\n", 1)[1].split("\n## 0.3.0", 1)[0]
        lines = release.strip("\n").splitlines()
        self.assertEqual(lines[0], PAIRS_031)
        self.assertEqual(re.findall(r"(?m)^### (\w+)", release), ["Fixed", "Platforms", "Changed"])
        bullets = [line for line in lines if line.startswith("- ")]
        self.assertEqual(len(bullets), len(set(bullets)))
        self.assertEqual(len(bullets), len([line for line in lines if line.strip() and not line.startswith(("#", "Pairs"))]))
        self.assertNotRegex(release, r"\b(?:lane|[0-9]{1,2}[A-Z][0-9A-Za-z-]*|wave)\b")
        parts = {part.split("\n", 1)[0]: part for part in re.split(r"(?m)^### ", release)[1:]}
        self.assertIn(STATUSLINE_031, parts["Changed"].splitlines())
        self.assertIn(LIMITS_031, parts["Platforms"].splitlines())
        installer = (ROOT / "install.sh").read_text(encoding="utf-8")
        for flag in re.findall(r"--[a-z][a-z-]+", release):
            self.assertIn(flag, installer)

    def test_contributing_release_steps(self):
        text = read("CONTRIBUTING.md")
        releasing = text.split("## Releasing\n", 1)[1]
        self.assertIn("Router and Harness release together at the same version X.Y.Z, the same day, Router first.", releasing.splitlines())
        steps = [line for line in releasing.splitlines() if re.match(r"\d+\. ", line)]
        self.assertEqual(steps, RELEASE_STEPS)
        code = [line.strip() for block in re.findall(r"(?ms)^   ```bash\n(.*?)^   ```", releasing) for line in block.splitlines()]
        self.assertEqual(code, RELEASE_CODE)
        order = [releasing.index(item) for item in RELEASE_STEPS[2:3] + RELEASE_CODE[:1] + RELEASE_STEPS[3:4] + RELEASE_CODE[1:]]
        self.assertEqual(order, sorted(order))


if __name__ == "__main__":
    unittest.main()
