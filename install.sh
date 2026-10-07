#!/usr/bin/env bash
set -euo pipefail

if ! command -v python3 >/dev/null 2>&1; then
  printf '%s\n' 'harness: missing requirement: python3 (Python 3.10 or newer)' >&2
  exit 1
fi

SOURCE_DIR=$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
exec python3 - "$SOURCE_DIR" "$@" <<'PY'
"""Install the local distribution without third-party dependencies."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import stat
import sys
import tempfile

if sys.version_info < (3, 10):
    sys.exit("harness: missing requirement: python3 3.10 or newer")

source = Path(sys.argv.pop(1))
parser = argparse.ArgumentParser(description="Install Harness roles, skills, and hooks.")
parser.add_argument("--dry-run", action="store_true", help="show changes without writing files")
parser.add_argument("--statusline", action="store_true", help="replace an existing statusLine")
parser.add_argument("--uninstall", action="store_true", help="remove unchanged files and wiring installed by Harness")
parser.add_argument("--host", choices=("claude", "codex", "both"), help="install for one host or both (default: hosts present)")
parser.add_argument("--judge-permissions", action="store_true", help="merge the optional Codex judge session profile into config.toml")
args = parser.parse_args()
host_homes = {
    host: Path(os.environ.get(f"{host.upper()}_HOME") or str(Path.home() / f".{host}")).expanduser().absolute()
    for host in ("claude", "codex")
}
selected_hosts = (["claude", "codex"] if args.host == "both" else [args.host]) if args.host else [
    host for host, config_home in host_homes.items()
    if os.environ.get(f"{host.upper()}_HOME") or config_home.is_dir() or shutil.which(host)
]
if not selected_hosts:
    sys.exit("harness: no host found; choose --host claude, --host codex, or --host both")
if args.judge_permissions and "codex" not in selected_hosts:
    parser.error("--judge-permissions requires --host codex or --host both")
if len(selected_hosts) == 2 and host_homes["claude"] == host_homes["codex"]:
    sys.exit("harness: CLAUDE_HOME and CODEX_HOME must use different directories")


def fail(message):
    raise ValueError(message)


def read_json(path, fallback):
    if not path.exists():
        return copy.deepcopy(fallback)
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        fail(f"{path.name} must contain a JSON object")
    return value


def digest(data):
    return hashlib.sha256(data).hexdigest()


def safe_path(relative):
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        fail("invalid path in install manifest")
    target = home / path
    # Do not write through links to another installation or user directory.
    for ancestor in (target, *target.parents):
        if ancestor.is_symlink():
            fail(f"refusing symbolic link: {ancestor}")
        if ancestor == home:
            break
    return target


def write_bytes(path, data, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
        os.chmod(name, mode)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def encoded(value):
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def backup_file(path):
    if path.exists():
        fd, name = tempfile.mkstemp(prefix=f"{path.name}.harness-backup-", dir=home)
        os.close(fd)
        shutil.copyfile(path, name)
        os.chmod(name, 0o600)
        print(f"Backup: {name}")


def backup_settings(settings, original):
    if settings != original:
        backup_file(settings_path)


def save_config(path, updated, original):
    if updated != original:
        backup_file(path)
        if updated is None:
            path.unlink()
        else:
            write_bytes(path, updated)


def save_settings(settings, original):
    if settings != original:
        backup_settings(settings, original)
        write_bytes(settings_path, encoded(settings))


def group_metadata(group):
    return {key: value for key, value in group.items() if key != "hooks"}


def has_hook(groups, expected_group, hook):
    return any(
        (group.get("matcher") or "*") == (expected_group.get("matcher") or "*")
        and any(
            item.get("type") == hook.get("type")
            and item.get("command") == hook.get("command")
            for item in group["hooks"]
        )
        for group in groups
    )


def validate_settings(settings):
    hooks = settings.get("hooks", {})
    if not isinstance(hooks, dict):
        fail(f"{settings_path.name} hooks must be an object")
    for event, groups in hooks.items():
        if not isinstance(groups, list):
            fail(f"{settings_path.name} hooks.{event} must be an array")
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                fail(f"{settings_path.name} hooks.{event} contains an invalid hook group")
            if not all(isinstance(hook, dict) for hook in group["hooks"]):
                fail(f"{settings_path.name} hooks.{event} contains an invalid hook")


def references_runtime(settings):
    commands = [hook.get("command") for groups in settings.get("hooks", {}).values() for group in groups for hook in group["hooks"]]
    if isinstance(settings.get("statusLine"), dict):
        commands.append(settings["statusLine"].get("command"))
    prefixes = (
        str(home / "harness") + "/",
        f"$HOME/.{host}/harness/",
        f"${{HOME}}/.{host}/harness/",
        f"~/.{host}/harness/",
        f"${host.upper()}_HOME/harness/",
        "${" + host.upper() + "_HOME}/harness/",
    )
    for command in commands:
        if not isinstance(command, str):
            continue
        try:
            words = shlex.split(command)
        except ValueError:
            words = [command]
        if any(prefix in word for word in words for prefix in prefixes):
            return True
    return False


def main():
    safe_path(settings_path.name)
    safe_path("harness/install-manifest.json")
    original = read_json(settings_path, {})
    validate_settings(original)
    settings = copy.deepcopy(original)
    manifest = read_json(manifest_path, {})
    if manifest_path.exists() and manifest.get("version") != 1:
        fail("unsupported install manifest version")
    previous_files = manifest.get("files", {})
    owned_hooks = manifest.get("hooks", {})
    judge_permissions = manifest.get("judge_permissions")
    config_path = None
    config_original = config_updated = None
    if host == "codex" and (args.judge_permissions or (args.uninstall and judge_permissions)):
        sys.dont_write_bytecode = True
        sys.path.insert(0, str(source / "codex"))
        from permissions import plan_install, plan_uninstall
        config_path = safe_path("config.toml")
        config_original = config_path.read_bytes() if config_path.exists() else None
        if args.uninstall:
            config_updated, message = plan_uninstall(config_original, judge_permissions, args.dry_run)
        else:
            config_updated, judge_permissions, message = plan_install(
                config_original, (source / "codex/judge-permissions.toml").read_bytes(), judge_permissions,
            )
        print(message)

    if args.uninstall:
        if not manifest:
            print("Harness is not installed (no install manifest).")
            return
        for event, groups in owned_hooks.items():
            current = settings.get("hooks", {}).get(event, [])
            for owned in groups:
                for group in list(current):
                    if group_metadata(group) != group_metadata(owned):
                        continue
                    removed_owned_hook = False
                    for hook in owned["hooks"]:
                        if hook in group["hooks"]:
                            group["hooks"].remove(hook)
                            removed_owned_hook = True
                    if removed_owned_hook and not group["hooks"]:
                        current.remove(group)
            if not current and event not in manifest.get("existing_events", []):
                settings.get("hooks", {}).pop(event, None)
        if not settings.get("hooks") and not manifest.get("had_hooks", False):
            settings.pop("hooks", None)
        line = manifest.get("statusline")
        if line and settings.get("statusLine") == line["installed"]:
            if line["had_previous"]:
                settings["statusLine"] = line["previous"]
            else:
                settings.pop("statusLine", None)
        removable = []
        kept = []
        keep_runtime = references_runtime(settings)
        for relative, expected in previous_files.items():
            target = safe_path(relative)
            if keep_runtime and relative.startswith(("harness/hooks/", "harness/statusline/")):
                # Keep the full runtime because scripts import companion modules.
                continue
            if target.is_file() and digest(target.read_bytes()) == expected:
                removable.append(target)
            elif target.exists():
                kept.append(relative)
        print(f"{'Would remove' if args.dry_run else 'Removing'} {len(removable)} unchanged files and owned settings entries.")
        for relative in kept:
            print(f"Keeping modified file: {relative}")
        if keep_runtime:
            print("Keeping runtime files: retained settings commands still use Harness hooks or statusLine.")
        if args.dry_run:
            return
        save_settings(settings, original)
        save_config(config_path, config_updated, config_original)
        if keep_runtime and config_updated != config_original:
            manifest.pop("judge_permissions", None)
            write_bytes(manifest_path, encoded(manifest))
        for path in removable:
            path.unlink()
        if not keep_runtime:
            manifest_path.unlink()
        directories = {path.parent for path in removable}
        if not keep_runtime:
            directories.add(manifest_path.parent)
        for directory in sorted(directories, key=lambda item: len(item.parts), reverse=True):
            while directory != home:
                try:
                    directory.rmdir()
                except OSError:
                    break
                directory = directory.parent
        if keep_runtime:
            print("Harness partially uninstalled. Remove retained runtime references and run --uninstall again to finish.")
        else:
            print("Harness uninstalled. User additions and modified files were kept.")
        return

    template = Path("examples/settings.example.json" if host == "claude" else "codex/hooks.json")
    example = read_json(source / template, {})
    if not example.get("hooks") or (host == "claude" and "statusLine" not in example):
        fail(f"missing installation requirement: {template}")
    validate_settings(example)
    commands = [hook["command"] for groups in example["hooks"].values() for group in groups for hook in group["hooks"]]
    if host == "claude":
        commands.append(example["statusLine"]["command"])
    prefix = f"$HOME/.{host}/harness/"
    for command in commands:
        words = shlex.split(command)
        if len(words) < 2 or words[0] not in ("bash", "python3") or not words[1].startswith(prefix):
            fail(f"invalid hook or statusLine command in {template}")
        relative = Path(words[1][len(prefix):])
        if relative.is_absolute() or ".." in relative.parts or not (source / relative).is_file():
            fail(f"missing installation requirement: {relative}")
    # Fail before installation if the checkpoint runtime or its user guidance is incomplete.
    required_checkpoint_files = (
        "hooks/checkpoint/storage.py", "hooks/checkpoint/journal.py",
        "hooks/checkpoint/policy.json", "hooks/checkpoint/outline.md",
        "hooks/checkpoint/guide.md", "skills/checkpoint/SKILL.md",
    )
    for relative in required_checkpoint_files:
        if not (source / relative).is_file():
            fail(f"missing installation requirement: {relative}")
    default_home = (Path.home() / f".{host}").absolute()
    if home != default_home:
        def replace_paths(value):
            if isinstance(value, dict):
                return {key: replace_paths(item) for key, item in value.items()}
            if isinstance(value, list):
                return [replace_paths(item) for item in value]
            if isinstance(value, str) and '"' + prefix in value:
                before, rest = value.split('"' + prefix, 1)
                relative, after = rest.split('"', 1)
                return before + shlex.quote(str(home / "harness" / relative)) + after
            return value
        example = replace_paths(example)

    payload = {}
    agent_source = "agents" if host == "claude" else "codex/agents"
    for folder, destination in (("hooks", "harness/hooks"), ("statusline", "harness/statusline"), (agent_source, "agents"), ("skills", "skills")):
        root = source / folder
        if not root.is_dir():
            fail(f"missing installation requirement: {folder}/")
        for path in sorted(root.rglob("*")):
            relative = path.relative_to(root)
            if any(part in ("__pycache__", ".git", ".DS_Store") for part in relative.parts) or path.suffix in (".pyc", ".pyo"):
                continue
            if path.is_symlink():
                fail(f"distribution contains a symbolic link: {folder}/{relative}")
            if path.is_file():
                payload[str(Path(destination) / relative)] = (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))

    files = dict(previous_files)
    writes = []
    for relative, (data, mode) in payload.items():
        target = safe_path(relative)
        wanted = digest(data)
        if target.exists():
            if not target.is_file():
                fail(f"destination is not a file: {relative}")
            current = digest(target.read_bytes())
            if current == wanted:
                if relative in previous_files:
                    files[relative] = wanted
                continue
            if previous_files.get(relative) != current:
                fail(f"refusing to overwrite existing or modified file: {relative}; move it aside before installing")
        files[relative] = wanted
        writes.append((target, data, mode))

    merged_hooks = settings.setdefault("hooks", {})
    next_hooks = copy.deepcopy(owned_hooks)
    for event, groups in example["hooks"].items():
        current = merged_hooks.setdefault(event, [])
        for group in groups:
            missing = [hook for hook in group["hooks"] if not has_hook(current, group, hook)]
            if missing:
                added = {**copy.deepcopy(group), "hooks": copy.deepcopy(missing)}
                current.append(added)
                next_hooks.setdefault(event, []).append(copy.deepcopy(added))
    line = manifest.get("statusline")
    if host == "claude":
        wanted_line = example["statusLine"]
        if "statusLine" not in settings or args.statusline:
            if settings.get("statusLine") != wanted_line:
                if line is None or settings.get("statusLine") != line["installed"]:
                    line = {"had_previous": "statusLine" in settings, "previous": settings.get("statusLine"), "installed": wanted_line}
                else:
                    line["installed"] = wanted_line
                settings["statusLine"] = wanted_line
        elif settings["statusLine"] != wanted_line:
            print("Keeping existing statusLine. Use --statusline to replace it.")
    next_manifest = {
        "version": 1,
        "files": files,
        "hooks": next_hooks,
        "had_hooks": manifest.get("had_hooks", "hooks" in original),
        "existing_events": manifest.get("existing_events", list(original.get("hooks", {}))),
        "statusline": line,
    }
    if judge_permissions is not None:
        next_manifest["judge_permissions"] = judge_permissions
    print(f"{'Would install' if args.dry_run else 'Installing'} {len(writes)} files into {home}.")
    if args.dry_run:
        print("Would merge hook settings." if settings != original else "Settings are already up to date.")
        return
    home.mkdir(parents=True, exist_ok=True)
    backup_settings(settings, original)
    for path, data, mode in writes:
        write_bytes(path, data, mode)
    if settings != original:
        write_bytes(settings_path, encoded(settings))
    save_config(config_path, config_updated, config_original)
    if next_manifest != manifest:
        write_bytes(manifest_path, encoded(next_manifest))
    if host == "codex":
        print("Harness installed for Codex. Trust new or changed hooks in Codex (/hooks) before they run.")
        if not args.judge_permissions and judge_permissions is None:
            print("Optional judge session profile: bash install.sh --host codex --judge-permissions")
    else:
        print("Harness installed. Restart Claude Code to load the agents, skills, and hooks.")


try:
    for host in selected_hosts:
        home = host_homes[host]
        settings_path = home / ("settings.json" if host == "claude" else "hooks.json")
        manifest_path = home / "harness" / "install-manifest.json"
        main()
except (OSError, ValueError, KeyError, TypeError) as error:
    sys.exit(f"harness: {error}")
PY
