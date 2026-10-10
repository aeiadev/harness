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
import ast
from datetime import datetime, timezone
import copy
import errno
import hashlib
import contextlib
import io
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import sys
import tempfile
import subprocess

if sys.version_info < (3, 10):
    sys.exit("harness: missing requirement: python3 3.10 or newer")

source = Path(sys.argv.pop(1))
parser = argparse.ArgumentParser(description="Install Harness roles, skills, and hooks.")
parser.add_argument("--dry-run", action="store_true", help="show changes without writing files")
parser.add_argument("--statusline", action="store_true", help="replace an existing statusLine")
parser.add_argument("--uninstall", action="store_true", help="remove unchanged files and wiring installed by Harness")
parser.add_argument("--host", choices=("claude", "codex", "both"), help="install for one host or both (default: hosts present)")
parser.add_argument("--judge-permissions", action="store_true", help="merge the optional Codex judge session profile into config.toml")
parser.add_argument("--purge", action="store_true", help="remove Harness's own backups (after --uninstall when both are given)")
parser.add_argument("--status", action="store_true", help="report installed Harness state without writing")
args = parser.parse_args()
if args.status and (args.uninstall or args.purge or args.dry_run or args.judge_permissions):
    parser.error("--status cannot be combined with --uninstall, --purge, --dry-run, or --judge-permissions")
version_path = source / "VERSION"
try:
    package_version = version_path.read_text(encoding="utf-8").strip()
except FileNotFoundError:
    sys.exit(f"harness: {version_path}: missing")
except (OSError, UnicodeError) as error:
    sys.exit(f"harness: {version_path}: unreadable ({getattr(error, 'strerror', None) or error})")
if not package_version:
    sys.exit(f"harness: {version_path}: empty")
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
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as error:
        fail(f"{path}: {error}")
    if not isinstance(value, dict):
        fail(f"{path} must contain a JSON object")
    return value


def validate_manifest(value, path):
    if type(value.get("version")) is not int or value["version"] != 1:
        fail(f"{path}: unsupported install manifest version")
    valid = (
        isinstance(value.get("files", {}), dict)
        and all(isinstance(key, str) and isinstance(item, str) for key, item in value.get("files", {}).items())
        and isinstance(value.get("hooks", {}), dict)
        and isinstance(value.get("existing_empty_events", []), list)
        and isinstance(value.get("existing_events", []), list)
        and isinstance(value.get("had_hooks", True), bool)
        and (value.get("statusline") is None or isinstance(value["statusline"], dict))
        and type(value.get("settings_original_existed", True)) in (bool, type(None))
        and (value.get("settings_original_mode") is None or
             (type(value["settings_original_mode"]) is int and 0 <= value["settings_original_mode"] <= 0o777))
        and (value.get("settings_original_sha256") is None or (
            isinstance(value["settings_original_sha256"], str)
            and re.fullmatch(r"[0-9a-f]{64}", value["settings_original_sha256"]) is not None))
        and (value.get("settings_original_backup") is None or (
            isinstance(value["settings_original_backup"], str)
            and Path(value["settings_original_backup"]).name == value["settings_original_backup"]
            and value["settings_original_backup"] not in ("", ".", "..")))
        and (value.get("settings_written_sha256") is None or (
            isinstance(value["settings_written_sha256"], str)
            and re.fullmatch(r"[0-9a-f]{64}", value["settings_written_sha256"]) is not None))
        and isinstance(value.get("created_dirs", []), list)
        and all(isinstance(item, str) and item and not Path(item).is_absolute() and ".." not in Path(item).parts
                for item in value.get("created_dirs", []))
    )
    if not valid:
        fail(f"{path}: invalid install manifest")
    for event, groups in value.get("hooks", {}).items():
        if not isinstance(event, str) or not isinstance(groups, list):
            fail(f"{path}: invalid install manifest hooks")
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list) or not all(isinstance(hook, dict) for hook in group["hooks"]):
                fail(f"{path}: invalid install manifest hooks")
    line = value.get("statusline")
    if line is not None and not all(key in line for key in ("installed", "had_previous", "previous")):
        fail(f"{path}: invalid install manifest statusline")


def digest(data):
    return hashlib.sha256(data).hexdigest()


SHARED_ROLES = frozenset(
    f"agents/{name}{extension}"
    for name in ("sweeper", "researcher", "planner", "builder", "builder-in-place",
                 "judge", "worker", "test-writer", "docs-writer")
    for extension in (".md", ".toml")
)


def router_manifest():
    """Return a valid Router manifest, or None when it is unsafe to trust."""
    sibling = home / "router" / "install-manifest.json"
    try:
        if sibling.is_symlink() or not sibling.is_file():
            return None
        value = json.loads(sibling.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("version") != 1 or not isinstance(value.get("files"), dict) or not isinstance(value.get("hooks"), list):
            return None
        for relative, fingerprint in value["files"].items():
            if not isinstance(relative, str) or not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
                return None
            if not isinstance(fingerprint, dict) or len(fingerprint) != 1:
                return None
            if "sha256" in fingerprint:
                if not isinstance(fingerprint["sha256"], str) or re.fullmatch(r"[0-9a-f]{64}", fingerprint["sha256"]) is None:
                    return None
            elif "link" in fingerprint:
                if not isinstance(fingerprint["link"], str):
                    return None
            else:
                return None
        return value
    except (OSError, UnicodeError, ValueError, TypeError, RecursionError):
        return None


def router_shared_files():
    """Return Router's claimed paths, or None when its manifest is unsafe to trust."""
    sibling = home / "router" / "install-manifest.json"
    if not sibling.exists() and not sibling.is_symlink():
        return set()
    value = router_manifest()
    return set(value["files"]) if value is not None else None


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
    """The one settings layout both installers write (canon)."""
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def file_mode(value):
    """A settings mode to record or restore: never with group or other write."""
    return value & ~0o022 if type(value) is int and 0 <= value <= 0o777 else None


def plain_name(value):
    return isinstance(value, str) and Path(value).name == value and value not in ("", ".", "..")


def regular_file(path):
    try:
        return stat.S_ISREG(path.lstat().st_mode)
    except OSError:
        return False


def copy_original(data):
    """Keep the original settings in a private backup of Harness's own."""
    fd, name = tempfile.mkstemp(prefix=f"{settings_path.name}.harness-backup-", dir=home)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
    os.chmod(name, 0o600)
    print(f"Backed up original settings: {name}")
    return Path(name).name


def sibling_original(sibling):
    """Router's recorded original, whatever it says (ruling R-B): absent, unknown, or its backup
    when that is still the bytes it recorded. No record, or a backup failing the check: None."""
    if "settings_original_existed" not in sibling:
        return None
    existed, name = sibling["settings_original_existed"], sibling.get("settings_original_backup")
    if existed is False or existed is None:
        return {"existed": existed, "backup": None, "sha256": None, "mode": None}
    if existed is not True or not plain_name(name) or not regular_file(home / name):
        return None
    try:
        data = (home / name).read_bytes()
    except OSError:
        return None  # An unreadable backup is no source here.
    recorded = sibling.get("settings_original_sha256")  # Absent in earlier Router 0.3 manifests.
    if recorded is not None and recorded != digest(data):
        return None
    # Copied to an own backup once the run is past --dry-run.
    return {"existed": True, "backup": None, "sha256": digest(data),
            "mode": file_mode(sibling.get("settings_original_mode")), "copy": data}


def oldest_backup():
    """The oldest settings backup either tool left in the host home, as (name, own), or None:
    Router's by the time in its name, Harness's by modification time."""
    found = []
    try:
        for path in home.iterdir():
            number = re.fullmatch(re.escape(settings_path.name) + r"\.router-backup-([0-9]+)", path.name)
            if number and regular_file(path):
                found.append((int(number[1]), path.name, False))
            elif path.name.startswith(settings_path.name + ".harness-backup-") and regular_file(path):
                found.append((path.lstat().st_mtime_ns, path.name, True))
    except OSError:
        return None
    return min(found)[1:] if found else None


def legacy_original(manifest, sibling):
    """With a 0.1 or 0.2 install present, the oldest settings backup either tool left; with none,
    unknown when an old install's hooks are recorded, since the file's bytes are then not the original."""
    old_harness = manifest_path.exists() and "package" not in manifest
    old_sibling = bool(sibling) and ("package" not in sibling or "settings_original_existed" not in sibling)
    if not (old_harness or old_sibling):
        return None
    oldest = oldest_backup()
    if oldest is None:
        touched = old_harness and bool(manifest.get("hooks")) or old_sibling and bool(sibling.get("hooks"))
        return {"existed": None, "backup": None, "sha256": None, "mode": None} if touched else None
    name, own = oldest
    try:
        data = (home / name).read_bytes()
    except OSError:
        return None  # An unreadable backup is no source here; the next-oldest is not tried.
    return {"existed": True, "backup": name if own else None, "sha256": digest(data),
            "mode": file_mode(stat.S_IMODE((home / name).lstat().st_mode)), "baseline": data,
            **({} if own else {"copy": data})}


def record_original(manifest, sibling, current):
    """Fix the original settings once, on the first 0.3 run: the first source that exists."""
    found = sibling_original(sibling) or legacy_original(manifest, sibling)
    if found is not None:
        return found
    if current is None:
        return {"existed": False, "backup": None, "sha256": None, "mode": None}
    return {"existed": True, "backup": None, "sha256": digest(current),
            "mode": file_mode(stat.S_IMODE(settings_path.stat().st_mode)), "current": True}


def shipped(relative):
    """The digest of a shared role as this checkout ships it for this host, or None."""
    path = source / ("agents" if host == "claude" else "codex/agents") / Path(relative).name
    return digest(path.read_bytes()) if relative in SHARED_ROLES and path.is_file() else None


def install_mode(mode):
    """Installed files are 0644, or 0755 when executable; never a read-only source mode."""
    return 0o755 if mode & 0o111 else 0o644


def backup_file(path):
    """Copy path to a private backup in the host home and return the backup's file name."""
    if path.exists():
        fd, name = tempfile.mkstemp(prefix=f"{path.name}.harness-backup-", dir=home)
        os.close(fd)
        os.chmod(name, 0o600)
        shutil.copyfile(path, name)
        os.chmod(name, 0o600)
        print(f"Backup: {name}")
        return Path(name).name
    return None


def backup_settings(settings, original):
    if settings != original:
        return backup_file(settings_path)
    return None


def linked_component(relative):
    """Return the first symbolic link below the host home on the way to relative, if any."""
    current = home
    for part in Path(relative).parts:
        current = current / part
        if current.is_symlink():
            return current
    return None


def remove_owned(current, owned_groups):
    """Remove owned hooks from one event's group list in place; return True when any was removed."""
    removed_from_event = False
    for owned in owned_groups:
        for group in list(current):
            if group_metadata(group) != group_metadata(owned):
                continue
            removed_owned_hook = False
            for hook in owned["hooks"]:
                if hook in group["hooks"]:
                    group["hooks"].remove(hook)
                    removed_owned_hook = True
                    removed_from_event = True
            if removed_owned_hook and not group["hooks"]:
                current.remove(group)
    return removed_from_event


def prune_dirs(relatives):
    """Remove empty directories, deepest first, that stay under the host home without links."""
    try:
        root = home.resolve()
    except OSError:
        return
    for relative in sorted(set(relatives), key=lambda item: len(Path(item).parts), reverse=True):
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts or not path.parts or path == Path("."):
            continue
        link = linked_component(relative)
        if link is not None:
            print(f"Skipping linked directory: {relative} (symbolic link at {link})")
            continue
        target = home / path
        if not target.is_dir():
            continue
        try:
            resolved = target.resolve()
        except OSError:
            continue
        if resolved == root or root not in resolved.parents:
            print(f"Skipping directory outside the host home: {relative}")
            continue
        try:
            target.rmdir()
        except OSError:
            pass


def purge():
    """Remove Harness's own backups directly in the host home: regular files only, no links."""
    count = 0
    if home.is_dir() and not home.is_symlink():
        for path in sorted(home.iterdir()):
            if not path.name.startswith(("settings.json.harness-backup-", "hooks.json.harness-backup-",
                                         "config.toml.harness-backup-")):
                continue
            try:
                regular = stat.S_ISREG(path.lstat().st_mode)
            except OSError:
                regular = False
            if not regular:
                continue
            if not args.dry_run:
                path.unlink()
            print(f"{'Would remove' if args.dry_run else 'Removed'} backup: {path}")
            count += 1
    print(f"{'Would remove' if args.dry_run else 'Removed'} {count} backup(s) from {home}.")


def settings_mode():
    """An existing settings file keeps its mode; a new one is private, as Router 0.3 writes it."""
    try:
        return stat.S_IMODE(settings_path.stat().st_mode)
    except OSError:
        return 0o600


def folders_of(files):
    """Every folder below the host home that is a parent of a listed file."""
    return list(dict.fromkeys(str(parent) for relative in files
                              for parent in Path(relative).parents if parent != Path(".")))


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
        write_bytes(settings_path, encoded(settings), settings_mode())


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


def clean(value):
    """Keep file-controlled values within one short output line."""
    value = str(value)
    return "".join(character if character.isprintable() and character not in "\r\n" else "?"
                   for character in value)[:200]


def status_probe_search(directory):
    """Ask the OS to traverse a child without writing anything."""
    try:
        os.stat(directory / ".harness-status-probe")
    except FileNotFoundError:
        pass


def status_json(path):
    try:
        info = os.stat(path)
        if path.is_symlink() or not stat.S_ISREG(info.st_mode):
            plain = OSError()
            plain.strerror, plain.filename = "not a regular file", str(path)
            return None, "unreadable", plain
        value = json.loads(path.read_text(encoding="utf-8"))
        return value, None, None
    except FileNotFoundError:
        return None, "missing", None
    except RecursionError:
        return None, "nested too deeply", None
    except (ValueError, UnicodeError):
        return None, "not valid JSON", None
    except OSError as error:
        return None, "unreadable", error


def unreadable_reason(error, base=None):
    """Name an unreadable path relative to its state cache or home."""
    reason = clean(getattr(error, "strerror", None) or error)
    target = getattr(error, "filename", None)
    try:
        path = Path(target)
        if path.name == ".harness-status-probe":
            path = path.parent
        for parent in reversed(path.parents):
            if parent == home or home in parent.parents:
                try:
                    status_probe_search(parent)
                except OSError:
                    path = parent
                    break
        root = (base if base is not None and base in path.parents else home if path == home or home in path.parents
                else Path.home() if path == Path.home() or Path.home() in path.parents else None)
        relative = path.relative_to(root) if root is not None else path
        try:
            suffix = "/" if stat.S_ISDIR(os.lstat(path).st_mode) else ""
        except OSError:
            suffix = ""
        return f"{clean(relative)}{suffix} not readable: {reason}"
    except (TypeError, ValueError, OSError):
        return "install manifest unreadable"


def status_payload():
    payload = {}
    for folder, destination in (("hooks", "harness/hooks"), ("statusline", "harness/statusline"),
                                ("agents" if host == "claude" else "codex/agents", "agents"),
                                ("skills", "skills")):
        root = source / folder
        for path in root.rglob("*"):
            relative = path.relative_to(root)
            if any(part in ("__pycache__", ".git", ".DS_Store") for part in relative.parts) or path.suffix in (".pyc", ".pyo"):
                continue
            if folder == "agents" and relative == Path("SHARED.sha256"):
                continue
            if path.is_file():
                payload[str(Path(destination) / relative)] = digest(path.read_bytes())
    return payload


def status_last_hook_write():
    state = Path(os.environ.get("XDG_STATE_HOME", ""))
    if not state.is_absolute():
        state = Path.home() / ".local/state"
    cache = state / "claude-harness"
    try:
        for parent in reversed(cache.parents):
            try:
                os.stat(parent)
            except FileNotFoundError:
                return "last hook write: none yet"
            status_probe_search(parent)
        try:
            info = os.stat(cache)
        except FileNotFoundError:
            return "last hook write: none yet"
        if not stat.S_ISDIR(info.st_mode):
            os.listdir(cache)  # The OS names the real error, such as "Not a directory".
            raise NotADirectoryError(errno.ENOTDIR, os.strerror(errno.ENOTDIR), str(cache))
        os.listdir(cache)
        status_probe_search(cache)
        newest = None
        def scan(directory):
            nonlocal newest
            for name in os.listdir(directory):
                child = directory / name
                info = os.lstat(child)
                if stat.S_ISDIR(info.st_mode):
                    os.listdir(child)
                    status_probe_search(child)
                    scan(child)
                elif stat.S_ISREG(info.st_mode) and (newest is None or info.st_mtime_ns > newest[0]):
                    newest = (info.st_mtime_ns, child)
        scan(cache)
        if newest is None:
            return "last hook write: none yet"
        stamp = datetime.fromtimestamp(newest[0] / 1e9, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        return f"last hook write: {stamp} ({clean(newest[1].relative_to(cache))})"
    except (OSError, ValueError) as error:
        return f"last hook write: unknown ({unreadable_reason(error, cache)})"


def status_budget():
    script = source / "scripts/budget.py"
    if not script.is_file():
        return "budget: unavailable (scripts/budget.py is missing)"
    try:
        tree = ast.parse(script.read_text(encoding="utf-8"))
        ceilings = next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
                        and any(isinstance(target, ast.Name) and target.id == "CEILINGS" for target in node.targets))
        timeout = float(os.environ.get("HARNESS_STATUS_BUDGET_TIMEOUT", "10"))
        result = subprocess.run([sys.executable, str(script), "--home", str(home), "--host", host, "--json"],
                                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout)
        data = json.loads(result.stdout)
        entry = data["trees"][0]
        label, tokens = entry["label"], entry["tokens"]
        if result.returncode or not isinstance(label, str) or type(tokens) is not int or label not in (*ceilings, "tree"):
            raise ValueError("invalid budget")
        ceiling = ceilings.get(label)
        tail = f", ceiling {ceiling}" if type(ceiling) is int else ""
        return f"budget: {clean(label)} {tokens} tokens always-on (estimate{tail})"
    except (OSError, ValueError, TypeError, KeyError, IndexError, StopIteration, SyntaxError,
            subprocess.TimeoutExpired):
        return "budget: unavailable (scripts/budget.py failed)"


def status_plugins():
    """Report Claude marketplace settings and cache without creating files."""
    names = ("harness", "shared-roles")
    enabled = {name: False for name in names}
    settings_problem = None
    try:
        os.stat(settings_path)
    except FileNotFoundError:
        pass
    except OSError as error:
        settings_problem = unreadable_reason(error)
    else:
        try:
            value = json.loads(settings_path.read_bytes().decode("utf-8"))
            if not isinstance(value, dict):
                settings_problem = f"{clean(settings_path)} is not a JSON object"
            else:
                flags = value.get("enabledPlugins", {})
                if not isinstance(flags, dict):
                    settings_problem = f"{clean(settings_path)} enabledPlugins is not an object"
                else:
                    for name in names:
                        key = f"{name}@harness"
                        flag = flags.get(key, False)
                        if not isinstance(flag, bool):
                            settings_problem = f'{clean(settings_path)} enabledPlugins["{key}"] is not true or false'
                            break
                        enabled[name] = flag
        except RecursionError:
            settings_problem = f"{clean(settings_path)} is nested too deeply"
        except (UnicodeError, ValueError):
            settings_problem = f"{clean(settings_path)} is not valid JSON"
        except OSError as error:
            settings_problem = unreadable_reason(error)
    if settings_problem:
        print(f"plugin: {settings_problem}")
    cache_root = Path(os.environ.get("CLAUDE_CODE_PLUGIN_CACHE_DIR") or home / "plugins") / "cache/harness"
    versions = {}
    try:
        os.listdir(cache_root)
        status_probe_search(cache_root)
    except FileNotFoundError:
        versions = {name: [] for name in names}
    except OSError as error:
        print(f"plugin: {unreadable_reason(error)}")
        versions = {name: None for name in names}
    else:
        for name in names:
            directory = cache_root / name
            try:
                os.stat(directory)
                versions[name] = sorted(entry for entry in os.listdir(directory)
                                        if re.fullmatch(r"[0-9A-Za-z.+-]{1,40}", entry)
                                        and stat.S_ISDIR(os.stat(directory / entry, follow_symlinks=False).st_mode))
            except FileNotFoundError:
                versions[name] = []
            except OSError as error:
                print(f"plugin: {unreadable_reason(error)}")
                versions[name] = None
    for name in names:
        state = "enabled unknown" if settings_problem else "enabled" if enabled[name] else "not enabled"
        found = versions[name]
        cache = ("cached unknown" if found is None else "not cached" if not found else
                 f"cached {', '.join(map(clean, found))} (current)" if package_version in found else
                 f"cached {', '.join(map(clean, found))} (stale, checkout {clean(package_version)})")
        text = f"plugin: {name} {state}, {cache}"
        if name == "harness" and os.path.lexists(manifest_path):
            text += "; stands down: script install present"
        print(text)
    if enabled.get("shared-roles") or versions.get("shared-roles"):
        names_from_pin = []
        for line in (source / "agents/SHARED.sha256").read_text().splitlines():
            _, _, name = line.partition("  ")
            if name and "/" not in name:
                names_from_pin.append(name)
        doubled = []
        for name in names_from_pin:
            path = home / "agents" / name
            try:
                if stat.S_ISREG(os.stat(path).st_mode):
                    doubled.append(path.stem)
            except FileNotFoundError:
                pass
            except OSError as error:
                print(f"plugin: {unreadable_reason(error)}")
                break
        if doubled:
            print(f"plugin: roles in both {clean(home / 'agents')} and shared-roles: {', '.join(map(clean, doubled))} "
                  "(doubled descriptions cost tokens every turn)")


def status():
    print(f"Harness status ({host}): {clean(home)}")
    manifest, problem, manifest_error = status_json(manifest_path)
    if problem is None:
        try:
            validate_manifest(manifest, manifest_path)
            if any(not name or Path(name).is_absolute() or ".." in Path(name).parts
                   for name in manifest.get("files", {})):
                problem = "unreadable"
        except (ValueError, TypeError, KeyError, RecursionError):
            problem = "unreadable"
    version = clean(manifest.get("package") or "0.2.0 or earlier") if problem is None else "unknown"
    checkout = clean(package_version)
    if problem == "missing":
        print(f"version: not installed, checkout {checkout}")
    elif problem:
        print(f"version: installed unknown, checkout {checkout}")
    elif version == checkout:
        print(f"version: installed {version}, checkout {checkout} (current)")
    elif version == "0.2.0 or earlier":
        print(f"version: installed {version}, checkout {checkout}")
    else:
        print(f"version: installed {version}, checkout {checkout} (run install.sh to update)")
    files = manifest.get("files", {}) if problem is None else {}
    categories = {"stale": [], "edited": [], "missing": []}
    files_error = None
    try:
        checkout_files = status_payload()
        if problem is None:
            for relative, recorded in files.items():
                path = home / relative
                # os.lstat raises EACCES and the like; Path.exists and is_symlink may return False.
                try:
                    info = os.lstat(path)
                except (FileNotFoundError, NotADirectoryError):
                    info = None
                if (info is not None and stat.S_ISLNK(info.st_mode)) or linked_component(relative) is not None:
                    categories["edited"].append(relative)
                elif info is None:
                    categories["missing"].append(relative)
                elif not stat.S_ISREG(info.st_mode):
                    categories["edited"].append(relative)
                else:
                    current = digest(path.read_bytes())
                    if current != recorded:
                        categories["edited"].append(relative)
                    elif relative in checkout_files and current != checkout_files[relative]:
                        categories["stale"].append(relative)
    except (OSError, ValueError, RecursionError) as error:
        problem = "unreadable"
        files_error = unreadable_reason(error)
    if problem == "missing":
        print("files: none installed")
    elif files_error:
        print(f"files: unknown ({files_error})")
    elif manifest_error:
        print(f"files: unknown ({unreadable_reason(manifest_error)})")
    elif problem:
        print("files: unknown (install manifest unreadable)")
    else:
        counts = [f"{len(categories[key])} {key}" for key in ("stale", "edited", "missing") if categories[key]]
        counts.append(f"{len(files) - sum(map(len, categories.values()))} current")
        print("files: " + ", ".join(counts))
        for kind in ("stale", "edited", "missing"):
            for relative in categories[kind]:
                print(f"  {kind}: {clean(relative)}")
    leftovers = []
    if problem is None:
        leftovers.extend((name, "installed by an earlier release; run install.sh to remove it")
                         for name in files if name not in checkout_files)
    retired = ("agents/Explore.md", "agents/Plan.md", "agents/general-purpose.md") if host == "claude" else (
        "agents/seat-judge.toml", "agents/seat-builder.toml")
    for name in retired:
        try:
            if name not in files and (home / name).exists():
                leftovers.append((name, "not tracked; remove it by hand"))
        except OSError:
            pass
    print("leftovers: none" if not leftovers else f"leftovers: {len(leftovers)}")
    for name, reason in leftovers:
        print(f"  {clean(name)} ({reason})")
    sibling_path = home / "router/install-manifest.json"
    try:
        present = sibling_path.exists() or sibling_path.is_symlink()
    except OSError:
        present = True
    sibling = router_manifest() if present else None
    sibling_version = clean(sibling.get("package") or "0.2.0 or earlier") if sibling else "unknown"
    print("sibling: Router not installed" if not present else
          "sibling: Router unknown" if sibling is None else f"sibling: Router {sibling_version} installed")
    skew = False
    if sibling:
        try:
            for relative in set(sibling["files"]) & SHARED_ROLES:
                target = home / relative
                if relative in checkout_files and target.is_file() and digest(target.read_bytes()) != checkout_files[relative]:
                    skew = True
                    break
        except OSError:
            pass
    print(f"skew: shared roles come from Router {sibling_version}; upgrade it for the 0.3 roles" if skew else "skew: none")
    template = source / ("examples/settings.example.json" if host == "claude" else "codex/hooks.json")
    example, _, _ = status_json(template)
    settings, settings_problem, _ = status_json(settings_path)
    if settings_problem and settings_problem != "missing":
        reason = {"unreadable": "not readable", "not valid JSON": "not valid JSON",
                  "nested too deeply": "nested too deeply"}[settings_problem]
        print(f"hooks: unknown ({settings_path.name} is {reason})")
    else:
        try:
            def commands(data):
                if not isinstance(data, dict) or not isinstance(data.get("hooks", {}), dict):
                    raise ValueError("bad shape")
                found = []
                for groups in data.get("hooks", {}).values():
                    if not isinstance(groups, list):
                        raise ValueError("bad shape")
                    for group in groups:
                        if not isinstance(group, dict) or not isinstance(group["hooks"], list):
                            raise ValueError("bad shape")
                        for hook in group["hooks"]:
                            if not isinstance(hook, dict):
                                raise ValueError("bad shape")
                            if "command" not in hook:
                                continue
                            if not isinstance(hook["command"], str):
                                raise ValueError("bad shape")
                            found.append(hook["command"])
                return found
            expected = commands({} if example is None else example)
            actual = commands({} if settings_problem == "missing" else settings)
            print(f"hooks: {sum(command in actual for command in expected)} of {len(expected)} registered")
        except (TypeError, ValueError, KeyError, RecursionError):
            print(f"hooks: unknown ({settings_path.name} is not valid JSON)")
    print(status_last_hook_write())
    print(status_budget())
    if host == "claude":
        status_plugins()


def main():
    safe_path(settings_path.name)
    safe_path("harness/install-manifest.json")
    original = read_json(settings_path, {})
    validate_settings(original)
    original_bytes = settings_path.read_bytes() if settings_path.is_file() else None
    settings = copy.deepcopy(original)
    manifest = read_json(manifest_path, {})
    if manifest_path.exists():
        validate_manifest(manifest, manifest_path)
    # Only a first install may treat the settings file on disk as the user's original.
    first_install = not manifest_path.exists()
    previous_files = manifest.get("files", {})
    owned_hooks = manifest.get("hooks", {})
    existing_empty_events = manifest.get("existing_empty_events")
    if existing_empty_events is None:
        current_hooks = original.get("hooks", {})
        empty_now = [event for event, groups in current_hooks.items() if groups == []]

        def only_harness(event):
            groups = copy.deepcopy(current_hooks.get(event))
            if not isinstance(groups, list):
                return False
            remove_owned(groups, owned_hooks.get(event, []))
            return groups == []

        # Earlier manifests recorded every existing event. Such an event counts as empty at
        # install when it is empty now or holds only Harness hooks at the time of this upgrade.
        existing_empty_events = ([event for event in manifest["existing_events"] if event in empty_now or only_harness(event)]
                                 if "existing_events" in manifest else empty_now)
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
            if remove_owned(current, groups) and not current and event not in existing_empty_events:
                settings.get("hooks", {}).pop(event, None)
        if not settings.get("hooks") and not manifest.get("had_hooks", True):
            settings.pop("hooks", None)
        line = manifest.get("statusline")
        if line and settings.get("statusLine") == line["installed"]:
            if line["had_previous"]:
                settings["statusLine"] = line["previous"]
            else:
                settings.pop("statusLine", None)
        removable = []
        kept = []
        linked = []
        keep_runtime = references_runtime(settings)
        router_files = router_shared_files() if any(name in SHARED_ROLES for name in previous_files) else set()
        for relative, expected in previous_files.items():
            path = Path(relative)
            link = None if path.is_absolute() or ".." in path.parts else linked_component(relative)
            if link is not None:
                print(f"Skipping linked path: {relative} (symbolic link at {link})")
                linked.append(relative)
                continue
            target = safe_path(relative)
            if keep_runtime and relative.startswith(("harness/hooks/", "harness/statusline/")):
                # Keep the full runtime because scripts import companion modules.
                continue
            if relative in SHARED_ROLES and (router_files is None or relative in router_files):
                reason = "invalid Router manifest" if router_files is None else "Router still uses it"
                print(f"Keeping shared role file: {relative} ({reason}).")
                continue
            current = digest(target.read_bytes()) if target.is_file() else None
            # A role equal to the bytes this checkout ships is unchanged, whoever wrote it last.
            if current is not None and (current == expected or current == shipped(relative)):
                removable.append(target)
            elif target.exists():
                kept.append(relative)
        # The shared rule: the verified original when it holds exactly what is left, else
        # removal when nothing is left of a file that was absent or is unknown, else canon.
        if "settings_original_existed" in manifest:
            existed, backup_name = manifest["settings_original_existed"], manifest.get("settings_original_backup")
            recorded, wanted_mode = manifest.get("settings_original_sha256"), file_mode(manifest.get("settings_original_mode"))
        else:
            # No record (ruling R-A): a 0.1 or 0.2 install uninstalled without an upgrade run. The old
            # manifest's facts or a backup show the file existed; else the original is unknown.
            oldest = oldest_backup()
            existed = True if manifest.get("had_hooks", True) or existing_empty_events or oldest else None
            backup_name, recorded, wanted_mode = (oldest[0] if oldest else None), None, None
        backup = home / backup_name if plain_name(backup_name) else None
        try:
            data = backup.read_bytes() if backup is not None and regular_file(backup) else None
            if data is not None and "settings_original_existed" not in manifest:
                recorded, wanted_mode = digest(data), file_mode(stat.S_IMODE(backup.lstat().st_mode))
        except OSError:
            data = None  # An unreadable backup does not verify.
        try:
            same = (data is not None and recorded == digest(data) and
                    encoded(json.loads(data)) == encoded(settings))
        except (ValueError, UnicodeError, RecursionError):
            same = False
        if same:
            settings_action = "restore"
        elif settings in ({}, {"hooks": {}}) and existed is not True and settings_path.exists():
            settings_action = "remove"
        else:
            settings_action = "merge"
        print(f"{'Would remove' if args.dry_run else 'Removing'} {len(removable)} unchanged files and owned settings entries.")
        for relative in kept:
            print(f"Keeping modified file: {relative}")
        if keep_runtime:
            print("Keeping runtime files: retained settings commands still use Harness hooks or statusLine.")
        if settings_action == "restore":
            print(f"{'Would restore' if args.dry_run else 'Restoring'} {settings_path.name} from {backup}")
        elif settings_action == "remove":
            reason = "absent before install" if existed is False else "original unknown and nothing left"
            print(f"{'Would remove' if args.dry_run else 'Removing'} {settings_path.name} ({reason})")
        if args.dry_run:
            for relative in linked:
                print(f"Would leave in place under a symbolic link: {relative}")
            return
        if settings_action == "restore":
            mode = wanted_mode
            if mode is None:
                mode = stat.S_IMODE(settings_path.stat().st_mode) if settings_path.exists() else 0o600
            write_bytes(settings_path, data, mode)
        elif settings_action == "remove":
            settings_path.unlink()
        else:
            save_settings(settings, original)
        save_config(config_path, config_updated, config_original)
        if keep_runtime and config_updated != config_original:
            manifest.pop("judge_permissions", None)
            write_bytes(manifest_path, encoded(manifest))
        for path in removable:
            path.unlink()
        runtime = home / "harness"
        if runtime.is_dir() and not runtime.is_symlink():
            for root, dirs, names in os.walk(runtime, followlinks=False):
                directory = Path(root)
                for name in names:
                    path = directory / name
                    if path.suffix in (".pyc", ".pyo") and not path.is_symlink():
                        path.unlink()
                for name in list(dirs):
                    path = directory / name
                    if name == "__pycache__" and not path.is_symlink():
                        shutil.rmtree(path)
                        dirs.remove(name)
        if not keep_runtime:
            manifest_path.unlink()
        if "created_dirs" in manifest:
            directories = manifest["created_dirs"]
        else:
            # Upgraded from 0.1 or 0.2: prune only the directories Harness installs into.
            directories = {"harness"} | {str(parent) for relative in previous_files
                                         for parent in Path(relative).parents if parent != Path(".")}
        prune_dirs(directories)
        for relative in linked:
            print(f"Left in place under a symbolic link: {relative}")
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
            if folder == "agents" and relative == Path("SHARED.sha256"):
                continue  # Repository drift check, not an installable agent.
            if path.is_symlink():
                fail(f"distribution contains a symbolic link: {folder}/{relative}")
            if path.is_file():
                payload[str(Path(destination) / relative)] = (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))

    files = dict(previous_files)
    writes = []
    chmods = []
    sibling = router_manifest()
    router_claims = set(sibling["files"]) if sibling is not None else set()
    same_version = sibling is not None and sibling.get("package") == package_version
    skew = False
    for relative, (data, mode) in payload.items():
        target = safe_path(relative)
        wanted = digest(data)
        if target.exists():
            if not target.is_file():
                fail(f"destination is not a file: {relative}")
            current = digest(target.read_bytes())
            if current == wanted:
                if relative in previous_files or relative in SHARED_ROLES:
                    files[relative] = wanted
                if relative in previous_files and relative not in router_claims and not target.stat().st_mode & stat.S_IWUSR:
                    chmods.append((target, install_mode(mode)))  # An older release installed it read-only.
                continue
            claimed = relative in SHARED_ROLES and relative in router_claims
            # A Router claim blocks a rewrite when Router is another version or the role differs
            # from what Router recorded: Harness claims the bytes on disk and says so. A same-version
            # claim on unchanged bytes is no user file.
            released = claimed and same_version and sibling["files"][relative] == {"sha256": current}
            if claimed and not released:
                files[relative] = current
                skew = True
                continue
            if previous_files.get(relative) != current and not released:
                fail(f"refusing to overwrite existing or modified file: {relative}; move it aside before installing")
        files[relative] = wanted
        writes.append((target, data, install_mode(mode)))
    if skew:
        version = sibling.get("package") if isinstance(sibling.get("package"), str) and sibling.get("package") else "0.2.0 or earlier"
        print(f"shared roles come from Router {version}; upgrade it for the 0.3 roles")

    # Upgrade cleanup: an old file this checkout no longer ships goes when it is unchanged.
    retired = []
    for relative, expected in previous_files.items():
        if relative in payload or relative in SHARED_ROLES:
            continue
        files.pop(relative, None)
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts or not path.parts:
            fail("invalid path in install manifest")
        link = linked_component(relative)
        target = home / path
        if link is not None:
            print(f"Keeping linked old file: {relative} (symbolic link at {link})")
        elif target.is_file() and digest(target.read_bytes()) == expected:
            retired.append((target, relative))
            if args.dry_run:
                print(f"Would remove no-longer-shipped: {relative}")
        elif target.exists():
            print(f"Keeping modified old file: {relative}")

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
    facts = sibling or {}
    # The original settings are fixed once, on the first 0.3 run (a record carries the hash).
    record = record_original(manifest, facts, original_bytes) if "settings_original_sha256" not in manifest else None
    try:  # An earlier release's backup is the best evidence of the settings before any tool.
        baseline = json.loads(record["baseline"]) if record and "baseline" in record else None
    except (ValueError, UnicodeError, RecursionError):
        baseline = None
    baseline = baseline if isinstance(baseline, dict) else None
    if "had_hooks" in manifest:
        had_hooks = manifest["had_hooks"]
    elif first_install and not (baseline is not None and "had_hooks" not in facts):
        had_hooks = "hooks" in original and facts.get("had_hooks") is not False
    else:  # A 0.1 manifest without the fact, own or Router's (ruling R-C): the oldest backup tells.
        had_hooks = "hooks" in baseline if baseline is not None else True
    if "existing_empty_events" not in manifest:
        present = original.get("hooks", {})
        # Router recorded the user's empty events before its own hooks filled them.
        theirs = facts.get("existing_empty_events") if isinstance(facts.get("existing_empty_events"), list) else []
        before = ([event for event, groups in baseline["hooks"].items() if groups == []]
                  if baseline is not None and isinstance(baseline.get("hooks"), dict) else [])
        existing_empty_events = existing_empty_events + [
            event for event in dict.fromkeys(item for item in theirs + before if isinstance(item, str))
            if event in present and event not in existing_empty_events]
    next_manifest = {
        "version": 1,
        "package": package_version,
        "installed_at": datetime.now(timezone.utc).isoformat(),
        "source": str(source),
        "files": files,
        "hooks": next_hooks,
        "had_hooks": had_hooks,
        "existing_empty_events": existing_empty_events,
        "statusline": line,
    }
    if judge_permissions is not None:
        next_manifest["judge_permissions"] = judge_permissions
    new_settings = encoded(settings) if settings != original else None
    for key in ("existed", "backup", "sha256", "mode"):
        name = "settings_original_" + key
        if record is not None:
            next_manifest[name] = record[key]
        elif name in manifest:
            next_manifest[name] = manifest[name]
    # Only Harness's own write moves this hash: a sibling's later write is no user edit.
    written = digest(new_settings) if new_settings is not None else manifest.get("settings_written_sha256")
    if written is not None:
        next_manifest["settings_written_sha256"] = written
    # Record each directory this install makes, never the host home itself.
    # A 0.1 or 0.2 manifest has no created_dirs: its install made the folders its files live in.
    created_dirs = list(manifest["created_dirs"] if "created_dirs" in manifest else folders_of(previous_files))
    for target in [path for path, _, _ in writes] + [settings_path, manifest_path]:
        parts = target.relative_to(home).parts
        for depth in range(1, len(parts)):
            relative = str(Path(*parts[:depth]))
            if not (home / relative).exists() and relative not in created_dirs:
                created_dirs.append(relative)
    # A folder Router created that holds a path Harness writes goes when the last tool leaves.
    held = {str(parent) for relative in [*payload, settings_path.name, "harness/install-manifest.json"]
            for parent in Path(relative).parents if parent != Path(".")}
    if "created_dirs" not in facts:  # The sibling is a 0.1 or 0.2 install, or absent.
        theirs = folders_of(facts.get("files", {}))
    else:
        theirs = facts["created_dirs"] if isinstance(facts["created_dirs"], list) else []
    created_dirs += [item for item in dict.fromkeys(item for item in theirs if isinstance(item, str))
                     if item in held and item not in created_dirs]
    next_manifest["created_dirs"] = created_dirs
    print(f"{'Would install' if args.dry_run else 'Installing'} {len(writes)} files into {home}.")
    if args.dry_run:
        print("Would merge hook settings." if settings != original else "Settings are already up to date.")
        return
    home.mkdir(parents=True, exist_ok=True)
    if record is not None and "copy" in record:
        next_manifest["settings_original_backup"] = copy_original(record["copy"])
    backup_name = backup_settings(settings, original)
    if record is not None and record.get("current") and backup_name is not None:
        next_manifest["settings_original_backup"] = backup_name  # The bytes on disk, kept before the write.
    if manifest.get("installed_at") is not None:
        kept = {**next_manifest, "installed_at": manifest["installed_at"]}
        if kept == manifest:
            next_manifest = kept  # Nothing else changed: a true no-op.
    for path, data, mode in writes:
        write_bytes(path, data, mode)
    for path, mode in chmods:
        os.chmod(path, mode)
    for path, relative in retired:
        path.unlink()
        print(f"Removed no-longer-shipped: {relative}")
    if new_settings is not None:
        write_bytes(settings_path, new_settings, settings_mode())
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
    if args.status:
        for host in selected_hosts:
            home = host_homes[host]
            settings_path = home / ("settings.json" if host == "claude" else "hooks.json")
            manifest_path = home / "harness" / "install-manifest.json"
            status()
        sys.exit(0)
    purge_only = args.purge and not args.uninstall
    if len(selected_hosts) == 2 and not args.dry_run and not purge_only:
        args.dry_run = True
        try:
            for host in selected_hosts:
                home = host_homes[host]
                settings_path = home / ("settings.json" if host == "claude" else "hooks.json")
                manifest_path = home / "harness" / "install-manifest.json"
                with contextlib.redirect_stdout(io.StringIO()):
                    main()
        finally:
            args.dry_run = False
    for host in selected_hosts:
        home = host_homes[host]
        settings_path = home / ("settings.json" if host == "claude" else "hooks.json")
        manifest_path = home / "harness" / "install-manifest.json"
        if not purge_only:
            main()
        if args.purge:
            purge()  # After an uninstall, so its exact-bytes restore still finds the backup.
except (OSError, ValueError, KeyError, TypeError) as error:
    sys.exit(f"harness: {error}")
PY
