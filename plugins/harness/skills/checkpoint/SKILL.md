---
name: checkpoint
description: Record enough verified project progress to resume after a context reset or handoff. Use for sustained tasks, checkpoint reminders, and recovery from compaction.
---

# Checkpoint a task

Find the current project's instructions and inspect its working tree. Use the
checkpoint path shown by the meter, or the checkpoint_path option in the host's
project checkpoint.json. Without an override, use STATE.md at the project root.
Keep the file inside this project and avoid following symbolic links.

Read the checkpoint before planning a resumed task. Confirm its claims against
the files that matter, then reconcile the latest user request with the recorded
next action. A stale checkpoint should lead to a correction, not an assumption
that unfinished work was completed.

For a new record, use the outline at ../../harness/hooks/checkpoint/outline.md
relative to this installed skill. The accompanying guide.md explains what to
preserve. In a source checkout, both files are under ../../hooks/checkpoint/.

Write a compact account of the requested result, next action, working files,
verification evidence, and unresolved obstacles. Preserve the reasoning behind
choices that would be costly to rediscover. Update it at meaningful transitions
and when a context reminder requests a save. An urgent reminder means the
checkpoint needs attention before further work consumes context.

After recovery, read the full checkpoint if the hook supplied only an excerpt.
Never treat the local archive as newer than the live project file without
checking. Report the next action and any missing evidence plainly.
