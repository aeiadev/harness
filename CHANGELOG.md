# Changelog

## 0.3.2

Pairs with Router 0.3.2.

### Changed

- Align the paired version with Router 0.3.2 for shared roles and CI resolution.

## 0.3.1

Pairs with Router 0.3.1. An older Router that installed different shared roles keeps them, and the installer prints one notice to upgrade it.

### Fixed

- Treat Windows permission bits consistently on Python 3.13, which exposes `fchmod` but cannot enforce private POSIX modes.
- Fall back from incomplete UNC `XDG_STATE_HOME` values consistently in the launcher and Python runtime across Python 3.9 and 3.13.

- Write the pressure file, checkpoint pin and reminder state when HOME or XDG_STATE_HOME sits behind a symbolic link (macOS `/var`, a linked home folder); a link inside Harness's own state folder is still refused.
- Block a shell that runs a variable the same command line set to destructive code, such as `sh -c "$x"` after `x=$(echo ...)`, through `sh -c`, `eval` or `$x` as the command; `sh -c "$cmd"` with nothing destructive in sight still runs.
- Check relative paths after a `cd` the guard cannot follow (`cd -` with no earlier cd, `cd "$DIR"`, `cd $(...)`) as if the shell were in the home, so `cd - && rm -rf .ssh` is blocked; `git rm -r` is checked like `rm -r`, from its `git -C` directory.
- Drop setuid, setgid and sticky bits when a rewrite, or an uninstall restore with no recorded mode, keeps the existing mode of `settings.json` or `hooks.json`, as Router does; the read and write bits stay as they were.
- Write Codex `config.toml` with mode 0600 on POSIX systems when `--judge-permissions` or uninstall changes it, not with the file's earlier mode, so it stays private.
- Find leaked bytecode when Python writes it under a `pycache_prefix`, as macOS `/usr/bin/python3` does, instead of beside the source.
- Inject project context once per session even where `chmod` is unavailable: the hook records its marker through the portability helper.
- Name a missing installer file with `/` on Windows too (`missing installation requirement: hooks/context/...`), as the installer's other messages do.

### Platforms

- Lower the Python floor to 3.9 and keep Bash 3.2 working, so the stock macOS `/bin/bash` and `/usr/bin/python3` can install and run Harness; a test parses every shipped module with the 3.9 grammar and rejects `X | Y` annotations without `from __future__ import annotations`.
- Start hooks and the statusline through `hooks/pyrun.sh`, a POSIX sh launcher shared with Router. It finds a real Python 3.9 or newer (`$HARNESS_PYTHON`, the cached interpreter, then `python3`, `python`, `py -3`; never the Windows Store stub) and sets UTF-8. The shell helpers search in the same order, and the project-context and auto-format Python now live in `.py` files beside their scripts.
- Without a usable Python, hooks are no longer silent: helpers print one line on stderr and exit 1, the danger guard exits 2 (the command stays blocked), and the statusline shows the message. The installer stops with one missing-requirement line.
- Run on native Windows through Git for Windows: Claude Code runs the hooks in Git Bash, and Codex hooks also get a PowerShell `command_windows` pinned to the installer's Python, which prints `harness: Python missing; rerun install.sh` when that Python is gone.
- Install on native Windows (Git Bash): the installer takes the home from `HOME` (Windows Python's own lookup reads `USERPROFILE` only), writes quoted absolute `C:/` hook paths, skips `chmod` where NTFS has no mode bits, records manifest paths with `/` so Router and Harness agree on shared roles, and compares paths without case.
- Run the hooks on native Windows Python: `hooks/checkpoint/compat.py` holds every POSIX-only call (read flags, owner check, modes, atomic replace, home lookup), so no hook imports `pwd` or needs `os.O_NOFOLLOW`, `os.O_NONBLOCK` or `os.geteuid`; a static test reads the Python syntax tree of every other shipped file to keep them out.
- Keep the danger guard blocking on Windows: `/c/Users/x`, `C:\Users\x` and `C:/Users/x` are the same home, compared without case.
- Read hook input as UTF-8 bytes, so a non-ASCII payload no longer depends on the Windows code page.
- Keep a Windows checkout LF: `.gitattributes` makes every text file LF in a `core.autocrlf=true` clone, so `agents/SHARED.sha256` verifies there. `install.sh` stops on a CRLF copy before anything else runs, with one line naming the fix (`git rm -r --cached -q . && git reset --hard -q`, or re-download).
- Read settings and manifests that start with a UTF-8 byte order mark, in the installer and `--status`; the installer still writes them LF without one.
- Run the test suite on native Windows: `tests/_platform.py` (shared with Router) gives tests bash by full path, never the WSL `bash.exe`, POSIX paths for bash, a `python3` shim script instead of a symlinked interpreter, and a timeout that kills the whole process tree.
- Tests find Python through `hooks/pyrun.sh` or the running interpreter, and checks that need POSIX modes, FIFOs, control-character file names or long paths print their reason and skip.
- Limits on native Windows: POSIX owner checks and file modes such as 0600 are not enforced; file names with control characters and paths past the 260-character MAX_PATH are not supported; the danger guard reads POSIX shell syntax, not PowerShell. Support there starts at 0.3.1: upgrade both Router and Harness to 0.3.1 before uninstalling.

### Changed

- Settings register the danger guard as `sh "$HOME/.claude/harness/hooks/pyrun.sh" harness block guard/danger_cmd_guard.py`.
- A script install sets the Claude statusline when none exists, without `--statusline`; the flag only replaces an existing one. An upgrade moves a statusline an older Harness wrote to the launcher form.
- Run CI on ubuntu, macOS and Windows (Git Bash) at Python 3.9 and 3.13, with fail-fast off, so one failing job no longer cancels the rest.
- Run every test file through `tests/run_all.py` in CI, from CONTRIBUTING.md and from the README: one line per file, a per-file timeout, the output of each failure and a final list of every failure; the same file ships in Router and Harness.
- Build the 0.1 and 0.2 release trees from commit SHAs in both the test and together jobs, so `together` gets ROUTER_V01, ROUTER_V02, HARNESS_V01 and HARNESS_V02 too.
- Pick the Router commit for the test job (shared-roles diff, Router comparisons) and the together job with `scripts/router-ref.sh resolve`: the ROUTER_REF repository variable when set, else the Router `v<VERSION>` tag, else Router main once its VERSION is the same or newer.
- Fetch Router again every 20 seconds for up to 10 minutes while its release is still being pushed, so CI no longer goes red because Harness was pushed first; ROUTER_REPOSITORY overrides the repository.
- CONTRIBUTING.md states the Python 3.9 floor, how to run the whole suite, and the release steps as a numbered list: both tools at the same version, mains pushed before tags, Router first.
- Tests compare resolved temporary paths and pin restore budgets with fixed numbers, the checkpoint skill's Resume, checkpoint and hand off triggers, and the installer's output lines.
- The README says the manual plugin statusline command runs a file that only the script install (`install.sh`) creates; a plugin-only install does not create it.
- The README names each platform with its requirements and limits, what the guard still passes (`echo -e` escapes, printf octal escapes, here-strings built from printf, and values bound by `read`, `printf -v` or a for-loop variable), and that the remembered statusline window counts while it is 10 minutes old or less.

## 0.3.0

Pairs with Router 0.3.0.

### Added

- Add window-relative checkpoint reminders and a private context pressure file for Router.
- Default checkpoint reminders to 65% and 85% of the live context window; explicit `remind_at` and `urgent_at` stay fixed token counts.
- Add read-only `install.sh --status` with versions, hook registration, files, token budget, last hook write, and plugin state.
- Add `--purge` to remove Harness backups alone or after `--uninstall`; use `--dry-run` to preview.
- Add generated Claude Code plugins for Harness and shared roles; script-installed hooks take precedence.
- Add a cache-hit percent segment to the Claude statusline.
- Add a Claude-only warning for expired resume caches when the host supplies that signal.
- Add CI checks, a release-notes script, and SECURITY and CONTRIBUTING guides.
- Add a VERSION stamp to the install manifest and preflight both hosts before installation.

### Changed

- Restore checkpoint sections in priority order with recent lines, Git facts, and In flight work after compaction.
- Require TASK and RETURN for the sweeper, researcher, and planner roles, and all four fields (TASK, FILES, BAR, RETURN) for judge and the other shared roles; keep the same brief rule in Codex TOML files.
- Keep Router-claimed roles unchanged on install; move shared roles to 0.3.0 bytes after both tools are upgraded.
- Upgrade cleanly by removing unchanged files no longer shipped while keeping edited files and listing both.
- Restore original settings bytes and mode without group or other write when a backup matches exactly, otherwise in standard JSON layout, in either install or uninstall order with Router 0.3.0; reruns after Router edits settings make no change.
- Show Codex token counts when price data is unavailable and leave the spend segment empty when usage is incomplete.
- Stop hook entry scripts from writing bytecode.

### Fixed

- Block destructive Git, filesystem, download-to-shell, and unsafe background shell commands on both hosts, including disguised downloads and protected paths after directory changes.
- Keep the newest checkpoint line tail and detached HEAD facts during restore.
- Remove the empty folders a 0.1 or 0.2 Harness or Router install made when the last tool leaves.
- Keep an existing settings file's mode when Harness rewrites it; a new settings file is private.

Upgrade notes: run both installers from their 0.3.0 checkouts. A 0.2 installer run after a 0.3 sibling refuses; once both are upgraded, the first tool's next run quietly updates its own shared-role record.

## 0.2.0

Pairs with Router 0.2.0.

### Added

- Pin the nine shared Markdown roles with a SHA-256 manifest and a drift test.
- Document Router's automatic routing modes and optional delegation defaults.

### Changed

- Share Router's original hooks-key ownership on first install and preserve uncertain legacy keys on uninstall.
- Keep event lists that were empty when Harness was installed and keep other tools' hooks; remove event keys emptied by Harness even if another hook occupied them at install. When upgrading from an earlier 0.2 install, an event list left empty after Harness removes its hooks may be dropped; a list that still holds hooks is never removed.
- Keep example instructions to the role reference, checkpoint rule, safety lines, and Router delegation pointer.
- Validate TASK, FILES, BAR, and RETURN in every shared role before work starts.
- Claim identical shared role files with Router 0.2 and retain them while Router uses them.
- Keep the repository-only role checksum out of agent installations.
- Limit test-writer and docs-writer returns to 1500 characters.
- Share one role return contract and turn limits across Harness and Router.
- Regenerate Codex TOML roles from the canonical Markdown files.

## 0.1.0

- Support Claude Code and OpenAI Codex CLI with shared hook scripts and host selection.
- Add nine synchronized Codex TOML roles with editable model and reasoning defaults.
- Codex role files carry only supported keys. Read-only behavior is requested
  by role instructions and enforced only if the parent session starts with a
  restricted permission profile, which the child inherits.
- Preserve Codex checkpoints across PreCompact, PostCompact, and compact session starts.
- Add a Codex spend side file and occasional messages; the live statusline remains Claude-only.
- Back up and merge Codex hook wiring, preserve user entries on uninstall, and explain hook trust.
- Block unbounded C-style waits, recursive deletion above home, and removal through a sudo shell.
- Exercise Codex payloads and installation offline with a fresh-user test.
- Add project context injection with a strict 4,000-byte cap and command guards.
- Provide a checkpoint journal, bounded recovery excerpts, and a project checkpoint skill.
- Include edit verification reminders and formatting disabled until explicitly enabled.
- Display context use and session cost in the statusline.
- Add nine scoped role agents and five skills.
- Add an installer that preserves existing settings, with dry-run and uninstall.
- Include complete hook wiring, global instructions, and fresh-user checks.
