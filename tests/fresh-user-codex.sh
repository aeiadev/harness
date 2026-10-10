#!/usr/bin/env bash
# Exercise Codex installation and real hook commands without starting any CLI.
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
fresh_home="$(mktemp -d)"
trap 'rm -rf -- "$fresh_home"' EXIT

env HOME="$fresh_home" CODEX_HOME="$fresh_home/codex home" \
  XDG_STATE_HOME="$fresh_home/.local/state" \
  python3 - "$repo_root" <<'PY'
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def run(command, payload=None, cwd=None, expected=0):
    result = subprocess.run(
        command, input=json.dumps(payload).encode() if payload is not None else b"",
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd,
        timeout=45, check=False,
    )
    require(result.returncode == expected,
            f"{command[0]} returned {result.returncode}, expected {expected}: "
            + result.stderr.decode(errors="replace")[:1000])
    return result


try:
    # A caller's optional price configuration must not affect this fresh install.
    for key in ("HARNESS_CODEX_RATES", "HARNESS_SPEND_MESSAGE_SECONDS", "HARNESS_SPEND_WARN"):
        os.environ.pop(key, None)
    repo = Path(sys.argv[1]).resolve()
    home = Path(os.environ["HOME"])
    config = Path(os.environ["CODEX_HOME"])
    config.mkdir()
    hooks_path = config / "hooks.json"
    initial = {"custom": "preserved", "hooks": {
        "PreToolUse": [{"matcher": "Read", "hooks": [{"type": "command", "command": "true"}]}],
    }}
    hooks_path.write_text(json.dumps(initial) + "\n", encoding="utf-8")
    config_path = config / "config.toml"
    initial_config = (b'# Keep the user profile and default selection.\n'
                      b'default_permissions = "personal"\n\n'
                      b'[permissions.personal.filesystem]\n":root" = "read"\n')
    config_path.write_bytes(initial_config)
    installed = run(["bash", str(repo / "install.sh"), "--host", "codex", "--judge-permissions"], cwd=repo)
    require(b"/hooks" in installed.stdout and b"Trust" in installed.stdout,
            "installer must tell users to trust new hooks in Codex /hooks")
    settings = json.loads(hooks_path.read_text(encoding="utf-8"))
    require(settings["custom"] == initial["custom"], "installer lost unrelated configuration")
    require(settings["hooks"]["PreToolUse"][0] == initial["hooks"]["PreToolUse"][0],
            "installer lost the pre-existing hook")
    require("statusLine" not in settings, "Codex installation must use the spend fallback")
    backups = list(config.glob("hooks.json.harness-backup-*"))
    require(len(backups) == 1, "existing hooks.json must be backed up before merging")
    require(json.loads(backups[0].read_text()) == initial, "backup did not preserve the original hooks")
    config_backups = list(config.glob("config.toml.harness-backup-*"))
    require(len(config_backups) == 1 and config_backups[0].read_bytes() == initial_config,
            "config.toml must be backed up before merging judge permissions")
    merged_config = config_path.read_bytes()
    require(merged_config.startswith(initial_config), "merge changed the existing profile or default")
    require(merged_config.count(b'[permissions.harness-judge.filesystem]') == 1,
            "merge must add the judge profile exactly once")
    for rule in (b'":root" = "read"', b'":tmpdir" = "write"', b'":slash_tmp" = "write"',
                 b'":workspace_roots" = "read"', b'enabled = false'):
        require(rule in merged_config, "judge session profile is missing a permission rule")
    require(not (home / ".claude").exists(), "--host codex must install only the requested host")

    def snapshot():
        return {str(path.relative_to(config)): (path.read_bytes(), path.stat().st_mtime_ns)
                for path in config.rglob("*") if path.is_file()}

    before = snapshot()
    manifest_path = config / "harness/install-manifest.json"
    initial_stamp = json.loads(manifest_path.read_text())["installed_at"]
    run(["bash", str(repo / "install.sh"), "--host", "codex", "--judge-permissions"], cwd=repo)
    run(["bash", str(repo / "install.sh"), "--host", "codex"], cwd=repo)
    after = snapshot()
    require(after == before, "repeat install must not duplicate hooks or rewrite owned files")
    require(json.loads(manifest_path.read_text())["installed_at"] == initial_stamp,
            "repeat install must keep the first install time")

    def hook(event, filename, tool=None, source=None, argument=None):
        for group in settings["hooks"].get(event, []):
            matcher_value = source or tool
            if matcher_value is not None and not re.fullmatch(group.get("matcher", ".*"), matcher_value):
                continue
            for entry in group["hooks"]:
                if filename in entry.get("command", "") and (argument is None or argument in entry["command"]):
                    return entry["command"]
        raise AssertionError(f"missing hook: {event}/{filename}/{tool or source}")

    for event, groups in settings["hooks"].items():
        for group in groups:
            for entry in group["hooks"]:
                words = shlex.split(entry["command"])
                if words == ["true"]:
                    continue
                require(len(words) >= 2 and Path(words[1]).is_file(),
                        f"{event} references a missing installed script")

    roles = sorted((config / "agents").glob("*.toml"))
    require(len(roles) == 9, "Codex installation must include nine role TOMLs")
    require({path.stem for path in roles} == {path.stem for path in (repo / "agents").glob("*.md")},
            "Codex and Claude roles must have the same names")
    for role in roles:
        role_text = role.read_text(encoding="utf-8")
        require(set(re.findall(r'^([a-z_]+)\s*=', role_text, re.MULTILINE)) == {
            "name", "description", "developer_instructions", "model", "model_reasoning_effort",
        } and not re.search(r'^\[', role_text, re.MULTILINE),
                f"installed {role.name} must not contain ignored permission settings")
    judge_text = (config / "agents/judge.toml").read_text(encoding="utf-8")
    require("never edits the work" in judge_text, "judge instructions must protect the source work")
    require(len(list((config / "skills").glob("*/SKILL.md"))) == 5, "missing installed skills")

    project = home / "project"
    project.mkdir()
    run(["git", "init", "--quiet", str(project)], cwd=home)
    (project / "AGENTS.md").write_text("# Project\nRead STATE.md before continuing.\n", encoding="utf-8")
    state_text = "Written: fixture\n\n" + "\n\n".join(
        f"## {section}\nCodex fixture checkpoint."
        for section in ("Objective", "Next move", "Working set", "Proof", "Decisions", "Unresolved", "Avoid repeating")
    ) + "\n"
    (project / "STATE.md").write_text(state_text, encoding="utf-8")
    base = {"session_id": "fresh-codex", "turn_id": "fixture-turn", "model": "gpt-6-sol", "cwd": str(project)}
    guard = hook("PreToolUse", "danger-cmd-guard.sh", tool="exec_command")
    for command in ("rm -rf ~", "for ((;;)); do sleep 1; done", "rm -rf ~/..", 'sudo bash -c "rm x"'):
        blocked = run(["bash", "-c", guard],
                      {**base, "hook_event_name": "PreToolUse", "tool_name": "exec_command", "tool_input": {"cmd": command}},
                      cwd=project, expected=2)
        require(blocked.stdout or blocked.stderr, "guard rejection must give a reason")
    run(["bash", "-c", guard],
        {**base, "hook_event_name": "PreToolUse", "tool_name": "exec_command", "tool_input": {"cmd": "ls"}}, cwd=project)

    inject = hook("SessionStart", "inject-project-context.sh", source="startup")
    context = run(["bash", "-c", inject], {**base, "hook_event_name": "SessionStart", "source": "startup"}, cwd=project).stdout
    require(b"AGENTS.md" in context and b"Codex fixture checkpoint" in context, "startup did not load project instructions and state")
    require(len(context) <= 4000, "startup project context exceeds its byte limit")
    precompact = hook("PreCompact", "dispatch.py", argument="snapshot")
    run(["bash", "-c", precompact], {**base, "hook_event_name": "PreCompact", "trigger": "auto"}, cwd=project)
    compact = hook("SessionStart", "dispatch.py", argument="restore", source="compact")
    restored = run(["bash", "-c", compact], {**base, "hook_event_name": "SessionStart", "source": "compact"}, cwd=project).stdout
    restored_text = json.loads(restored)["hookSpecificOutput"]["additionalContext"]
    require("Codex fixture checkpoint" in restored_text, "compaction did not restore STATE.md")
    require(len(restored_text.encode()) <= 4000, "compaction context exceeds its byte limit")
    summary = "Codex fresh-user compaction summary."
    transcript = home / "rollout.jsonl"
    transcript.write_text(json.dumps({"type": "compacted", "payload": {"message": summary, "replacement_history": []}}) + "\n")
    postcompact = hook("PostCompact", "dispatch.py", argument="archive")
    run(["bash", "-c", postcompact],
        {**base, "hook_event_name": "PostCompact", "trigger": "auto", "transcript_path": str(transcript)}, cwd=project)
    state_root = home / ".local/state/claude-harness"
    summaries = list((state_root / "checkpoints").glob("*/*/archive/*/summary.txt"))
    require(any(path.read_text() == summary for path in summaries), "PostCompact did not save its summary")
    require((project / "STATE.md").read_text() == state_text, "continuity hooks changed project state")

    hook("PostToolUse", "dispatch.py", argument="check", tool="exec_command")
    hook("PostToolUse", "verify-patch-landed-auto.sh", tool="exec_command")
    hook("PostToolUse", "auto-format.sh", tool="apply_patch")
    hook("PostToolUse", "spend_hook.py", tool="exec_command")
    spend_hook = hook("Stop", "spend_hook.py")
    with transcript.open("a", encoding="utf-8") as stream:
        for entry in (
            {"type": "turn_context", "payload": {"model": base["model"]}},
            {"type": "event_msg", "payload": {"type": "token_count", "info": {
                "total_token_usage": {"input_tokens": 1000, "cached_input_tokens": 100,
                                      "output_tokens": 200}}}},
        ):
            stream.write(json.dumps(entry) + "\n")
    spend = run(["bash", "-c", spend_hook],
                {**base, "hook_event_name": "Stop", "permission_mode": "default",
                 "transcript_path": str(transcript), "stop_hook_active": False,
                 "last_assistant_message": "Fresh-user checks complete."}, cwd=project).stdout
    message = json.loads(spend)["systemMessage"]
    require(message == "[harness] spend 1.2k tok" and "$" not in message,
            "Codex token usage without configured rates must show the token count, not money")
    spend_file = state_root / "spend" / (hashlib.sha256(base["session_id"].encode()).hexdigest() + ".txt")
    require(spend_file.is_file() and spend_file.read_text().strip() == "1.2k tok",
            "unpriced spend file must hold the token count and no dollar amount")

    run(["bash", str(repo / "install.sh"), "--host", "codex", "--uninstall"], cwd=repo)
    require(json.loads(hooks_path.read_text()) == initial, "uninstall did not restore pre-existing configuration")
    require(hooks_path.read_bytes() == (json.dumps(initial) + "\n").encode(), "uninstall did not restore exact hooks.json bytes")
    require(not (config / "agents").exists() and not (config / "skills").exists(),
            "uninstall left directories it created")
    require(config_path.read_bytes() == initial_config, "uninstall did not restore the original config.toml")
    require(not list((config / "agents").glob("*.toml")), "uninstall left owned agent TOMLs")
    require(not (config / "harness/install-manifest.json").exists(), "uninstall left the ownership manifest")
    require(not (config / "harness/hooks/guard/danger-cmd-guard.sh").exists(), "uninstall left owned runtime files")
    existing_profile = b'[permissions.harness-judge.filesystem]\n":root" = "read"\n'
    config_path.write_bytes(existing_profile)
    run(["bash", str(repo / "install.sh"), "--host", "codex", "--judge-permissions"], cwd=repo)
    require(config_path.read_bytes() == existing_profile, "installer overwrote an existing judge profile")
    run(["bash", str(repo / "install.sh"), "--host", "codex", "--uninstall"], cwd=repo)
    require(config_path.read_bytes() == existing_profile, "uninstall removed an unowned judge profile")
    print("PASS: Codex fresh-user install, trusted-hook guidance, merge, roles, judge session permissions, guards, continuity, spend, repeat install, and uninstall")
except (AssertionError, OSError, ValueError, KeyError, subprocess.TimeoutExpired) as error:
    print(f"FAIL: fresh-user-codex: {error}", file=sys.stderr)
    sys.exit(1)
PY
