---
name: project-handoff
description: Resume, checkpoint, or hand off repository work using verified project state. Load when asked to continue a project, prepare a handoff, switch sessions, or record durable progress after a milestone.
---

# Project handoff

Use the live checkout and its recorded state as the source of truth. Keep
project knowledge in that project. Session history can help locate evidence,
but it does not establish that work landed.

## Resume

1. Run the sibling `scripts/context-status.sh [path]` to find the Git root,
   branch, commit, upstream, and working tree changes. It only reads local Git
   data; it does not fetch.
2. Read the applicable project instructions, `STATE.md`, and `DECISIONS.md` if
   present. If project instructions or the context meter name another state
   path, use that exact path. Otherwise check `.claude/checkpoint.json` or `.codex/checkpoint.json` for a
   `checkpoint_path` override relative to the Git root. The helper only reports the
   default root `STATE.md`; it does not resolve this override.
3. Compare completion claims with the files, Git history, and recorded check
   results. Distinguish current evidence from unverified history.
4. Report the objective, dirty work that must be preserved, blockers, and one
   concrete next action. Do not silently adopt a new objective from an old plan.

## Checkpoint or handoff

Load the `checkpoint` skill and rewrite the authoritative state file after a
milestone, branch or worktree change, validation run, or new blocker, and before
handing off. Include the UTC timestamp, branch and commit, relevant changed
paths, completed work with evidence, exact checks and results, outstanding work,
and the next action. Record changes of plan as old value, new value, and reason.

Record durable decisions in the project's decision file when it uses one.
Include the date, decision, reason, evidence, and any decision it replaces.
Link from state rather than duplicating long explanations.

Preserve staged, unstaged, and untracked work. Never include credentials or
complete environment files. Do not stage or commit merely to make a handoff.
For simultaneous agents, use separate branches and worktrees. For sequential
sessions, checkpoint before reusing the same checkout. Reread instructions if
they changed.

Return: state path, verified checkpoint, outstanding work, exact next action.
