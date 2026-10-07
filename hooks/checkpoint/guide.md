# A useful record for the next session

Keep one project checkpoint. STATE.md is the default; a project can select a
different relative path in its host's checkpoint.json. The meter prints the
chosen path. A session keeps that selection even if a tool changes directory.

Update the record when completed work changes what should happen next: after
checking a change, accepting a requirement, discovering a blocker, or preparing
to hand off. Include enough evidence to distinguish verified work from plans.
The outline is a starting point. Adapt its sections to the project.

When context gets tight, prioritize the information needed to restart safely:

1. The next executable action, including anything that prevents it.
2. The requested outcome and the constraints that still apply.
3. The checkout and files that contain unfinished work.
4. Acceptance evidence, especially failures that need attention.
5. Decisions or rejected approaches whose rationale would otherwise be lost.
6. Links to supporting material that is too large for the checkpoint.

Keep credentials and full conversation logs out of this file. Replace stale
claims with current observations, and explain changes that affect the plan.
The checkpoint is a working record, not permission to ignore a newer request.

Hooks ask the agent to save; they cannot make the agent comply. Before compaction
they copy the existing checkpoint to local storage. After compaction they retain
any available plaintext summary and present a bounded checkpoint excerpt.
Read the full document whenever the excerpt is incomplete. An unreadable file
produces a recovery notice instead of invented progress.

Defaults live in policy.json next to this guide. Project overrides live in
.claude/checkpoint.json or .codex/checkpoint.json. The journal and archives use
the operating system's local state directory, separate from project files.
