# Changelog

## 0.2.0

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
