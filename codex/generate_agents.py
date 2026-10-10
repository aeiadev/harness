#!/usr/bin/env python3
"""Generate editable Codex role defaults from the canonical Markdown roles."""

import argparse
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
TIERS = {
    "haiku": ("gpt-6-luna", "low"),
    "sonnet": ("gpt-6-sol", "medium"),
    "opus": ("gpt-6-astra", "high"),
}
JUDGE_PERMISSIONS = {
    "default_permissions": "harness-judge",
    "permissions": {
        "harness-judge": {
            "filesystem": {
                ":root": "read",
                ":tmpdir": "write",
                ":slash_tmp": "write",
                ":workspace_roots": "read",
            },
            "network": {"enabled": False},
        },
    },
}


CODEX_NOTES = {
    'judge': (
        'On Codex, read-only behavior is requested by these instructions, not enforced.\n'
        'It is enforced only if the parent session starts with a restricted permission\n'
        'profile. A Codex role file cannot enforce read-only; the judge inherits the parent\n'
        "session's sandbox. This was verified on Codex 0.156.1.\n"
        '\n'
        'For Codex, the caller must select the `harness-judge` permission profile from\n'
        '`codex/judge-permissions.toml` in a dedicated verification session before spawning\n'
        'the judge with `codex -c default_permissions=harness-judge`. Never combine this\n'
        'profile with `-s`, which overrides it.\n'
        'The profile grants temporary writes, keeps workspace roots read-only, and disables\n'
        'network access. Keep the source repository as the session workspace and set\n'
        '`TMPDIR` outside it before starting the session. A disposable copy must be outside\n'
        'every workspace root. If effective permissions do not protect\n'
        'the source or cannot run the BAR, return SEND_BACK with the blocked requirement.'
    ),
    'planner': (
        'On Codex, read-only behavior is requested by these instructions, not enforced.\n'
        'It is enforced only if the parent session starts with a restricted permission\n'
        'profile. A Codex role file cannot enforce read-only; the child inherits the parent\n'
        "session's sandbox. See the README's Codex judge recipe for a restricted parent\n"
        'session.'
    ),
    'researcher': (
        'On Codex, read-only behavior is requested by these instructions, not enforced.\n'
        'It is enforced only if the parent session starts with a restricted permission\n'
        'profile. A Codex role file cannot enforce read-only; the child inherits the parent\n'
        "session's sandbox. See the README's Codex judge recipe for a restricted parent\n"
        'session.'
    ),
    'sweeper': (
        'On Codex, read-only behavior is requested by these instructions, not enforced.\n'
        'It is enforced only if the parent session starts with a restricted permission\n'
        'profile. A Codex role file cannot enforce read-only; the child inherits the parent\n'
        "session's sandbox. See the README's Codex judge recipe for a restricted parent\n"
        'session.'
    ),
    'test-writer': (
        'On Codex, these instructions request that production code remain read-only; they\n'
        'do not enforce that restriction. It is enforced only if the parent session starts\n'
        'with a permission profile that protects those files. A Codex role file cannot\n'
        "enforce read-only; the child inherits the parent session's sandbox. See the\n"
        "README's Codex judge recipe for a restricted parent session."
    ),
}


def codex_note(name):
    """Codex-only guidance lives here, never in the Markdown bodies."""
    note = CODEX_NOTES.get(name)
    return "\n## Codex\n\n" + note + "\n" if note else ""


def role_values(path):
    """Read the plain scalar frontmatter used by agents/*.md."""
    text = path.read_text(encoding="utf-8")
    match = re.fullmatch(r"---\n(.*?)\n---\n(.*)", text, re.DOTALL)
    if not match:
        raise ValueError(f"{path.name}: expected Markdown frontmatter")
    metadata = {}
    for line in match[1].splitlines():
        field = re.fullmatch(r"([A-Za-z][A-Za-z0-9_-]*): (\S.*)", line)
        if not field or field[1] in metadata:
            raise ValueError(f"{path.name}: invalid or duplicate frontmatter field")
        metadata[field[1]] = field[2]
    model, effort = TIERS[metadata["model"]]
    if metadata["name"] != path.stem:
        raise ValueError(f"{path.name}: role name must match the filename")
    values = {
        "name": metadata["name"],
        "description": metadata["description"],
        "developer_instructions": match[2].strip() + "\n" + codex_note(metadata["name"]),
        "model": model,
        "model_reasoning_effort": effort,
    }
    # A Codex role file cannot enforce read-only. Children inherit the parent
    # session's sandbox; permission profiles belong in session configuration.
    return values


def render(values):
    # JSON strings and booleans are valid TOML in this small generated subset.
    lines = ["# Generated by codex/generate_agents.py; edit installed defaults as needed."]

    def table(items, path=()):
        for key, value in items.items():
            if not isinstance(value, dict):
                name = key if re.fullmatch(r"[A-Za-z0-9_-]+", key) else json.dumps(key)
                lines.append(f"{name} = {json.dumps(value, ensure_ascii=False)}")
        for key, value in items.items():
            if isinstance(value, dict):
                child = (*path, key)
                lines.extend(("", "[" + ".".join(child) + "]"))
                table(value, child)

    table(values)
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if generated role files have drifted")
    args = parser.parse_args()
    destination = ROOT / "codex" / "agents"
    wanted = {path.stem + ".toml": render(role_values(path)) for path in sorted((ROOT / "agents").glob("*.md"))}
    profile_path = ROOT / "codex" / "judge-permissions.toml"
    profile_text = render(JUDGE_PERMISSIONS)
    if args.check:
        actual = {path.name: path.read_text(encoding="utf-8") for path in destination.glob("*.toml")}
        if actual != wanted or not profile_path.is_file() or profile_path.read_text(encoding="utf-8") != profile_text:
            sys.exit("Codex roles are out of sync; run python3 codex/generate_agents.py")
        print(f"PASS: {len(wanted)} Codex roles and judge permissions match the canonical defaults")
        return
    destination.mkdir(parents=True, exist_ok=True)
    extras = {path.name for path in destination.glob("*.toml")} - wanted.keys()
    if extras:
        sys.exit("Unexpected Codex role files: " + ", ".join(sorted(extras)))
    for filename, text in wanted.items():
        (destination / filename).write_text(text, encoding="utf-8")
    profile_path.write_text(profile_text, encoding="utf-8")


if __name__ == "__main__":
    main()
