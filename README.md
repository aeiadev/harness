# Harness

For developers running long Claude Code or Codex CLI sessions who want safer shell commands, context that survives compaction, and cost visibility. Harness provides hooks, skills, and nine role agents for both hosts.
Harness loads project context, checks shell commands, keeps a written checkpoint
through compaction, and shows session cost. It is maintained by AEIA.

## The parts

| Part | Job |
| --- | --- |
| Context hook | Injects project instructions and state once per session, with output capped at 4,000 bytes (under 4 KB). |
| Command guard | Blocks known destructive shell commands and unbounded waits before execution. |
| Checkpoint hooks | Ask for checkpoints as context fills, save compaction summaries, and hand state back after compaction. |
| Helper hooks | Remind the agent to verify indirect edits; run installed formatters only when explicitly enabled. |
| Spend | Claude's live statusline shows context and cost. Codex hooks write a spend side file and occasional messages. |
| Skills | Load a focused procedure when a task calls for it. |
| Role agents | Take a scoped brief, do one kind of work, and return a short result. |

During a session, the context hook supplies the starting point. Skills guide the
work, and roles let a coordinator delegate a search, change, test, or review.
The guard runs before shell commands. Helpers and the context meter run after
tools. At compaction, the state hooks preserve the written checkpoint. A fresh
judge checks the result against the brief before it counts as complete.

Router is a separate repository that plugs into this setup. Router chooses where
a job goes; Harness provides the hooks, skills, and roles the job runs with.
Harness works without Router.

## Install

Use Bash, Python 3.10 or newer, and Git for project discovery and worktree roles. The tests also need
the `timeout` command. Hook scripts use Python's standard library and run locally.
Download or clone a reviewed release, then run from its directory:

```bash
bash install.sh --dry-run
bash install.sh
bash install.sh --host codex
bash install.sh --host both
```

Use `--host claude`, `--host codex`, or `--host both` to select a host. Without
`--host`, the installer selects hosts with a configuration directory, an explicit
home override, or an executable on `PATH`. If none are found, choose a host explicitly.

For Claude, the installer copies hooks and the statusline to `~/.claude/harness/`, roles to
`~/.claude/agents/`, and skills to `~/.claude/skills/`. It creates `settings.json`
when missing, backs up an existing file before changing it, and merges the hook
entries without removing your existing entries. Repeating an installation does
not duplicate hooks. Existing role or skill files with conflicting contents are
preserved and reported.

For Codex, it copies the same runtime scripts to `~/.codex/harness/`, nine
TOML roles to the global `~/.codex/agents/`, and skills to `~/.codex/skills/`.
It backs up and merges `~/.codex/hooks.json`,
preserving existing hook entries. Set `CODEX_HOME` to change this destination.
Review and trust the new hooks in Codex with `/hooks` before they can run.
Changing a hook script requires trusting it again. The installer does not
change trust settings or run either CLI.

The live statusline is Claude-only. An existing `statusLine` is kept unless you
explicitly select Harness:

```bash
bash install.sh --statusline
```

Use `CLAUDE_HOME` to install into a different configuration directory. This is an
installer setting; configure Claude Code to use the same directory separately.

```bash
CLAUDE_HOME="$HOME/.config/claude" bash install.sh
```

To preview or remove the installation:

```bash
bash install.sh --dry-run --uninstall
bash install.sh --uninstall
```

Uninstall removes the hook entries and unchanged files recorded by the installer.
It preserves user additions and changed files. If customized or pre-existing
settings still invoke Harness, it keeps and reports the runtime files they need.
Remove those references and run `--uninstall` again to finish cleanup.
When Harness replaced a previous statusline, uninstall restores that value if the installed statusline is still
active. Review reported leftovers before deleting anything by hand.

## Quick start

1. Add the short instructions in `examples/CLAUDE.md.example` to your global
   `CLAUDE.md`, or `examples/AGENTS.md.example` to your Codex `AGENTS.md`.
   The installer does not change instruction files.
2. In your project, load the `checkpoint` skill and create a `STATE.md` checkpoint from
   hooks/checkpoint/outline.md. Keep current requirements and the next action there.
3. Start a new host session so the installed hooks and roles are loaded. In
   Codex, trust the hooks through `/hooks`.
4. Give a role a brief with a concrete check. Give the judge the same brief and
   the resulting checkout in fresh context.

```text
TASK: Add validation for an empty title.
FILES: src/title.py and tests/test_title.py in this repository.
BAR: python3 tests/test_title.py
RETURN: CHANGED / BAR / OUTPUT / NOT DONE / OPEN
```

Use `builder` for an isolated Git worktree or `builder-in-place` for a bounded
change in the current directory. The caller chooses the role and supplies its
scope. Installing Harness does not automatically dispatch tasks to agents.

| Role | Job | Claude | Codex model / effort |
| --- | --- | --- | --- |
| `sweeper` | Find, list, and count existing material without edits. | haiku | gpt-6-luna / low |
| `researcher` | Gather evidence for a named decision without edits. | sonnet | gpt-6-sol / medium |
| `planner` | Critique a design and return an implementation plan without edits. | sonnet | gpt-6-sol / medium |
| `builder` | Make one bounded change in its own worktree. | sonnet | gpt-6-sol / medium |
| `builder-in-place` | Make one bounded change in the current directory. | sonnet | gpt-6-sol / medium |
| `judge` | Re-run the check in fresh context and return PASS or SEND_BACK. | opus | gpt-6-astra / high |
| `worker` | Complete a scoped task with several steps. | sonnet | gpt-6-sol / medium |
| `test-writer` | Write failing tests for a named behavior. | sonnet | gpt-6-sol / medium |
| `docs-writer` | Write README and documentation from the code. | sonnet | gpt-6-sol / medium |

These model tiers are editable defaults. Choose models available to your account.
The Codex roles use `developer_instructions` from the matching `agents/*.md`
body. Run `python3 codex/generate_agents.py` after editing source roles;
`python3 codex/generate_agents.py --check` checks for drift. Tests assert that
names, instructions, and model tiers remain in sync.

To use a role in Claude Code, ask the coordinator to use the role and provide its brief. For example: “Use the `sweeper` role to find every checkpoint configuration file, make no edits, and return the paths.” In Codex, request the named role in the session instructions, for example: “Use the `builder-in-place` role. TASK: Reject an empty title. FILES: src/title.py tests/test_title.py. BAR: timeout 60 python3 tests/test_title.py. RETURN: changed files and BAR output.” Codex receives the role instructions from the installed TOML file. Its requested read-only behavior is not enforced by that file; use the parent session permission profile when enforcement matters.
Claude Code tool allowlists still apply to the Markdown roles. On Codex,
read-only behavior for `planner`, `researcher`, `sweeper`, and `judge` is requested
by the role's instructions, not enforced. It is enforced only if the parent
session starts with a restricted permission profile. A Codex role file cannot
enforce read-only. The child inherits the parent session's sandbox, and Claude
tool allowlists do not apply to Codex.
Verified on Codex 0.156.1: role-level `sandbox_mode`, `default_permissions`, and
`[permissions]` are ignored. Use the parent-session profile in the judge recipe
below when sandbox enforcement is required.

### Codex judge

The judge is instructed to leave source files unchanged on both hosts. Claude
restricts its direct editing tools through the role allowlist, while Bash still
uses the session permissions. On Codex, the role instructions alone do not
enforce this restriction. The dedicated parent session in the recipe below
enforces workspace protection through its permission profile,
[`codex/judge-permissions.toml`](codex/judge-permissions.toml), allows test artifacts
under the system temporary directories, keeps workspace roots read-only, and
disables command network access. Role files cannot set permissions on Codex 0.156.
This was verified on Codex 0.156.1: the judge inherits the parent session's
permissions, so select the profile when starting that session.

From this Harness checkout, install the global roles and opt into the profile
merge, then start a dedicated verification session with this runnable brief:

```bash
bash install.sh --host codex --judge-permissions
export TMPDIR=/tmp
codex -c default_permissions=harness-judge \
  -c "agents.judge.config_file=\"${CODEX_HOME:-$HOME/.codex}/agents/judge.toml\"" \
  'Use the judge role. TASK: Verify generated Codex roles. FILES: agents/* and codex/*.py and codex/agents/* and codex/judge-permissions.toml. BAR: timeout 30 python3 codex/generate_agents.py --check </dev/null. REQUIREMENT: Generated roles and the session profile match their canonical defaults. RETURN: VERDICT / BAR / FINDINGS / REQUIREMENTS.'
```

`--judge-permissions` backs up an existing `~/.codex/config.toml` before adding
the profile. It preserves any existing `harness-judge` profile, even one with
different permissions. Review that existing profile before selecting it.
Repeated installs do not duplicate the profile. Uninstall removes only the
unchanged profile the installer added and preserves other configuration.
The installer does not change the global `default_permissions` setting.
`CODEX_HOME` also applies to this configuration file.
Unsupported TOML layouts are left unchanged with a message. This includes inline
permission containers and, on Python 3.10, multiline values. For those layouts,
merge the named profile tables manually without changing `default_permissions`.

Never pass `-s` with `-c default_permissions=harness-judge`: `-s` overrides the
permission profile. Project-level `.codex/agents/` is not loaded under
`codex exec` on Codex 0.156.1. Installation uses global `~/.codex/agents/`, and
the command above also registers the judge explicitly with
`-c agents.judge.config_file=...`.

For another project, use its source repository as the session workspace and give
the judge that project's brief and BAR. Set `TMPDIR` outside the source repository
before starting the session. If tests write artifacts, supply a disposable
verification copy and keep it, caches, and output in a temporary directory outside
all workspace roots. Do not add that directory as a workspace root. If effective
permissions cannot protect the source or run the BAR, the judge returns SEND_BACK.
Offline tests check the configuration and role synchronization without launching
either host CLI.

| Skill | Load when |
| --- | --- |
| `checkpoint` | Starting, checkpointing, or recovering a long task. |
| `verify-patch-landed` | A scripted or indirect edit needs proof that it changed the file. |
| `project-handoff` | Resuming work or preparing a checkpoint for another session. |
| `tdd` | Implementing a behavior through a failing test, a small fix, and cleanup. |
| `skill-index` | Choosing among this repository's skills and roles. |

## Configuration

`examples/settings.example.json` contains Claude hook wiring.
`codex/hooks.json` contains Codex wiring, using `$HOME/.codex/harness/` paths.
The installer adjusts these templates for `CLAUDE_HOME` and `CODEX_HOME`.
Keep your own hooks alongside the Harness entries.

Both hosts run the same hook scripts. Shell payloads are normalized before
inspection, including Codex `Bash` and native `exec_command` / `shell_command`
calls. Codex `PreCompact` snapshots the checkpoint, `PostCompact` saves the
compaction summary, and `SessionStart` with `source=compact` restores the
configured checkpoint (`checkpoint_path`, default `STATE.md`).
Injected project context and recovered checkpoints are limited to 4,000 UTF-8
bytes on both hosts, strictly under 4 KB (4,096 bytes). Larger checkpoints include a
pointer to the full file. Summary and snapshot files remain in local runtime
storage; they are not added to the project. PostCompact saves a summary when
Codex exposes its plaintext. Some remote compactions expose only encrypted
history; in that case Harness still saves the checkpoint snapshot and restores
the checkpoint, but cannot archive the unavailable summary text.

| Environment variable | Default | Purpose |
| --- | --- | --- |
| `HOME` | Your home directory | Base for the default installation and runtime state. |
| `CLAUDE_HOME` | `$HOME/.claude` | Installer destination, also used for uninstall. |
| `CODEX_HOME` | `$HOME/.codex` | Codex installation and uninstall destination. |
| `XDG_STATE_HOME` | `$HOME/.local/state` | Base for runtime files under `claude-harness`. |
| `CLAUDE_PROJECT_DIR` | Unset | Project fallback when the hook payload has no `cwd`; process working directory is the final fallback. |
| `HARNESS_AUTO_FORMAT` | Unset (off) | Set exactly `1` in the host environment to enable installed formatters for trusted projects. |
| `HARNESS_SPEND_WARN` | Unset | Optional USD threshold for highlighting the session cost. |
| `HARNESS_CODEX_RATES` | Unset | JSON mapping model names to `input`, `cached_input`, and `output` USD rates per million tokens. |
| `HARNESS_SPEND_MESSAGE_SECONDS` | `300` | Minimum interval between Codex spend messages. |

Checkpoint defaults are installed in `harness/hooks/checkpoint/policy.json`.
Override them at the Git root in `.claude/checkpoint.json` or
`.codex/checkpoint.json` for the corresponding host:

```json
{
  "checkpoint_path": "STATE.md",
  "remind_at": 180000,
  "urgent_at": 240000,
  "window_tokens": 280000,
  "repeat_after": 12000,
  "restore_bytes": 4000
}
```

The three token thresholds must increase in that order. `repeat_after` sets the
additional token usage before another urgent reminder if the checkpoint has not
changed. `checkpoint_path` must be a relative path inside the project, without
symbolic links. `restore_bytes` is clamped to 1,000 through 4,000 bytes, including
the recovery notice and file pointer. A session retains its checkpoint location
when tools change directory. Sessions started in the home directory without a
project use a private draft location reported by the meter.

Before compaction, both hosts copy the current file. After compaction, available
plaintext summaries are stored with checkpoint copies and small receipts under
`$XDG_STATE_HOME/claude-harness/checkpoints/` (or the default local state path).
Hooks request a save but cannot guarantee the agent updates the project file.

Claude session cost comes from `cost.total_cost_usd` in its statusline JSON.
Codex has no command statusline, and its built-in cost items are Enterprise-only.
Its `Stop` and `PostToolUse` spend hook writes the same `$0.00` or `$--` segment
to `$XDG_STATE_HOME/claude-harness/spend/<session-hash>.txt` (with the usual
`$HOME/.local/state` fallback). The hook also emits a throttled `systemMessage`.
Reported cost is used when available. Otherwise, complete rollout token usage
and `HARNESS_CODEX_RATES` produce an estimate. Without usable usage and rates,
the segment is `$--`. No rates are bundled, and estimates are not billing data.
The display does not query a billing service or enforce a spending limit.

## Adapt it

Edit descriptions to control when a skill or role is chosen. Keep each role's
scope, exclusions, and return shape explicit. Change the `model` field if your
available models differ. Keep the judge independent of the implementation
conversation and give it a check it can run locally.

Keep durable project facts in `STATE.md`; avoid credentials, transcripts, and
private environment values. Read the `checkpoint` skill for checkpoint and compaction
rules. The project-handoff helper reports Git state without changing the tree.

The shell guard recognizes known command patterns. It is a guardrail, not a
sandbox or a substitute for host permissions.

Automatic formatting is off by default. Set `HARNESS_AUTO_FORMAT=1` in the host
process environment only for repositories whose tooling you trust. After edits,
the hook uses executables already on `PATH`: `ruff format` (or `black`) for
Python, `gofmt` for Go, `rustfmt` for Rust, `phpcbf` for PHP, and `shfmt` for shell
files. It runs `prettier --write` for JavaScript, TypeScript, JSON, CSS, SCSS,
HTML, Markdown, and YAML. Formatters can load repository configuration, execute
plugins, and change files. The hook performs no downloads and invokes no package
runner. Unset the variable, or set any value other than `1`, to disable it.

## Test

All repository tests run offline, use temporary directories where needed, and
make no model calls:

```bash
timeout 900 bash -c 'for t in tests/test_*.py; do python3 "$t" || exit 1; done && for t in tests/test_*.sh; do bash "$t" || exit 1; done && bash tests/fresh-user.sh && bash tests/fresh-user-codex.sh'
```

`tests/fresh-user.sh` installs into an empty temporary home, checks every hook
entry, exercises the installed guard and context injector, verifies cost output,
and checks the installed files for absolute home-directory paths.
`tests/fresh-user-codex.sh` uses temporary `HOME` and `CODEX_HOME` directories,
checks the Codex install and hook behavior, and verifies merge and uninstall
preserve existing hooks. It never starts Codex or calls a model.

MIT licensed. See [LICENSE](LICENSE).
