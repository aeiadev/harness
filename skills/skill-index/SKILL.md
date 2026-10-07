---
name: skill-index
description: Choose a Harness skill or role for the current task. Load when deciding how to resume, verify, test, plan, delegate, or document work; read only the matching skill or role after choosing.
---

# Skill and role index

This index covers only the five skills and nine roles shipped in this repo.
Skills supply a workflow when needed. Roles give an agent one clear job. Read
the chosen skill's `SKILL.md` or role's agent file; do not load the whole set.

## Skills

| Skill | Load when |
| --- | --- |
| `checkpoint` | Keeping `STATE.md` current through checkpoints or compaction. |
| `verify-patch-landed` | Checking that a scripted or multi-file edit reached disk. |
| `project-handoff` | Resuming, checkpointing, or handing off repository work. |
| `tdd` | Implementing a named behavior with a failing test first. |
| `skill-index` | Choosing the relevant skill or role from this table. |

## Roles

| Role | One job | Default tier |
| --- | --- | --- |
| `sweeper` | Find, list, or count facts without edits. | fast |
| `researcher` | Gather evidence for one decision without edits. | balanced |
| `planner` | Critique a design or produce a plan without edits. | balanced |
| `builder` | Make one bounded change in its own Git worktree. | balanced |
| `builder-in-place` | Make one bounded change in the current directory. | balanced |
| `judge` | Rerun a check in fresh context and return PASS or SEND_BACK. | thorough |
| `worker` | Carry one general multi-step task to completion. | balanced |
| `test-writer` | Write failing tests for a named behavior. | balanced |
| `docs-writer` | Write plain README and documentation text from code. | balanced |

The tier labels are host-neutral. Defaults (Claude Code / Codex): fast = haiku / gpt-6-luna low; balanced = sonnet / gpt-6-sol medium; thorough = opus / gpt-6-astra high. Each role file holds its own default; edit it to change the model.

Give a delegated role its objective, allowed files, requirements, acceptance
check, and short return shape. Use separate worktrees for simultaneous edits.
Keep research and review roles read-only. The judge evaluates fresh evidence
against the acceptance check rather than the builder's confidence.
