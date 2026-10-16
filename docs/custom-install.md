# Commands-only installation

The default `bash install.sh` is unchanged: it installs Harness roles, skills
and hooks for the hosts it finds. The commands profile is a separate opt-in for
bespoke hosts that keep their own settings, hooks, roles, skills and
instructions. It installs only Harness's offline commands.

## Install

Plan for one explicit host, review the JSON, then apply exactly that plan:

```bash
bash install.sh --profile commands --host claude --dry-run > plan.json
bash install.sh --profile commands --host claude --plan plan.json
```

Use `codex` instead of `claude` for the other host. `--host both` is refused for
this profile, and so is a call without exactly one action. The dry run leaves
HOME, host settings, hooks, roles, instructions and command state unchanged; it
removes its temporary payload before returning. Apply recreates the exact saved
inventory at the same path, so keep the saved plan and use the same `TMPDIR`.
The recreated payload remains available for interruption recovery. A changed
payload or preimage requires a new plan.

`--status`, `--rollback`, `--recover` and `--uninstall` are profile-scoped and
take the same explicit host. Rollback restores the latest committed preimage.
Uninstall removes only unchanged owned files and preserves command data and
custom host settings. A pending transaction requires `--recover` first.

## Paths

| Host | Owned runtime | Launcher | Default command state |
| --- | --- | --- | --- |
| Claude | `~/.claude/harness-commands` | `~/.local/bin/claude-harness` | `$XDG_STATE_HOME/harness-commands/claude` |
| Codex | `~/.codex/harness-commands` | `~/.local/bin/codex-harness` | `$XDG_STATE_HOME/harness-commands/codex` |

`CLAUDE_HOME` and `CODEX_HOME` move the runtime with the host home, as for the
full install. Command state is `$XDG_STATE_HOME/harness-commands/<host>`, or
`~/.local/state/harness-commands/<host>` when `XDG_STATE_HOME` is unset;
`HARNESS_COMMANDS_STATE` (an absolute path) overrides it for one invocation. The
full profile's `$XDG_STATE_HOME/claude-harness` store, host-home variables and
project variables never select this store. Installer journals live under
`$XDG_STATE_HOME/harness-commands-installer/<host>`.

An existing `~/.local/bin/claude-harness` or `~/.local/bin/codex-harness` that
this installer did not create, whether a file or a link, is a conflict: planning
refuses before any write and the launcher is never adopted. So is a linked
runtime directory. Another product's or host's launcher and state are never
borrowed.

## Commands

Only `operations`, `observe`, `usage`, `evaluate`, `ssh`, `policy`, `status`, `doctor`, `help` and `version` are available.
Any other name, such as a hook, routing or configuration switch, exits 2
before a project module, manifest or state is read. `operations check`,
`observe`, `evaluate` and `usage` keep their existing schemas and exit codes.
`ssh check` and `policy bind|show|unbind` are loaded only when called; policy
bindings use the owner registry `$XDG_CONFIG_HOME/harness/operations-bindings.json`.

The profile registers no hook. Binding a policy here does not establish live
enforcement: interception needs an active full Harness guard or a separately
reviewed custom adapter. `status` and `doctor` print JSON naming the
commands-only profile, host, version, source revision, manifest digest and
state path, with `registered_hooks` and `policy_interception` false and host
availability and MCP qualification `unknown`.

The installed launcher runs without the source checkout. Its shell header picks
Python 3.9 or newer from `python3`, `python` or `py -3` and runs it isolated.
Before loading any command module it checks every packaged file against
`manifest.json`; a missing, linked or changed file stops every command with
exit 1, and modules are never borrowed from `PYTHONPATH` or another install.

## Packaging

The payload is a fixed list of tracked runtime files: the launcher, its
dispatcher, the operations, observation, evaluation and usage modules and the
SSH check and policy modules. Ignored, private or linked files are never
copied; a linked or missing input stops the dry run. `source_revision` is
`sha256:` plus the digest of the packaged inventory and VERSION, so a release
archive without `.git` is identified too.

The shared lifecycle helper `scripts/commands_install.py` is maintained in
Router and kept byte-identical here; see Router's `docs/custom-install.md` for
its plan, journal, lock and recovery rules. Harness passes `--product harness`
and one host per call.

The full install now checks, before any write, that every module its shell guard
names is present beside it: `shell_syntax.py`, `wait-loop-guard.py` and the
remote guard's `ssh_operations.py` and `operations_policy.py`. A missing one stops
with `missing installation requirement: hooks/guard/<name>`. The full install
still copies everything under `hooks/`, the inert dispatcher included, but creates
no commands launcher, runtime or state. The Claude Code plugin does not ship the
dispatcher.

These are static and installer tests on a disposable home. They are not live
host qualification, connector qualification or proof of enforcement.
