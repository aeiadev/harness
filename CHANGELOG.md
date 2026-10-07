# Changelog

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
