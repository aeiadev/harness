# Changelog

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
