"""Strict, explicit project policy binding. Standard library only; no hook subprocesses."""

import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import stat
import secrets

POLICY_LIMIT = 16 * 1024
REGISTRY_LIMIT = 64 * 1024
ROOT_SCAN_LIMIT = 256
POLICY_RELATIVE = ".harness/operations.json"
_ID = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*\Z")
_HOST = re.compile(r"[A-Za-z0-9][A-Za-z0-9.:-]*\Z")
_USER = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_OPERATIONS = {"ssh": {"read", "create-dir", "rename", "remove"},
               "scp": {"upload"}, "rsync": {"upload", "delete", "remove"}}
_spec = importlib.util.spec_from_file_location("harness_compat", Path(__file__).parent.parent / "checkpoint/compat.py")
compat = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(compat)
_NOFOLLOW = compat.NOFOLLOW
_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_NONBLOCK = compat.NONBLOCK


class PolicyError(ValueError):
    def __init__(self, error_id):
        self.error_id = error_id
        super().__init__(error_id)


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise PolicyError("policy-invalid")
        result[key] = value
    return result


def _finite_float(text):
    value = float(text)
    if not math.isfinite(value):
        raise ValueError("nonfinite number")
    return value


def _json_bytes(data, limit, error_id):
    if type(data) is not bytes:
        raise PolicyError(error_id)
    if len(data) > limit:
        raise PolicyError("policy-size-limit" if error_id == "policy-invalid" else "registry-size-limit")
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=_unique_pairs,
                          parse_float=_finite_float,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except PolicyError:
        raise PolicyError(error_id) from None
    except (UnicodeError, ValueError, TypeError, RecursionError):
        raise PolicyError(error_id) from None


def validate_policy(policy):
    """Validate and return the H1 schema-1 policy; raises PolicyError."""
    def bad():
        raise PolicyError("policy-invalid")
    if type(policy) is not dict or set(policy) != {"schema_version", "destinations", "rules"} or type(policy["schema_version"]) is not int or policy["schema_version"] != 1:
        bad()
    destinations, rules = policy["destinations"], policy["rules"]
    if type(destinations) is not dict or not destinations or type(rules) is not list or not rules:
        bad()
    for label, dest in destinations.items():
        if type(label) is not str or not _ID.fullmatch(label) or type(dest) is not dict or set(dest) != {"host", "user", "port"}:
            bad()
        if type(dest["host"]) is not str or not _HOST.fullmatch(dest["host"]) or type(dest["user"]) is not str or not _USER.fullmatch(dest["user"]):
            bad()
        if type(dest["port"]) is not int or not 1 <= dest["port"] <= 65535:
            bad()
    seen = set()
    for rule in rules:
        if type(rule) is not dict or type(rule.get("id")) is not str or not _ID.fullmatch(rule["id"]) or rule["id"] in seen:
            bad()
        seen.add(rule["id"])
        transport = rule.get("transport")
        destination = rule.get("destination")
        if type(rule.get("decision")) is not str or rule["decision"] not in ("allow", "deny") or type(transport) is not str or transport not in _OPERATIONS or type(destination) is not str or destination not in destinations:
            bad()
        shape = set(rule) - {"id", "decision", "transport", "destination"}
        if shape not in ({"argv"}, {"remote_root"}, {"operation", "remote_root"}):
            bad()
        if "argv" in rule:
            argv = rule["argv"]
            if transport != "ssh" or type(argv) is not list or not argv or any(type(x) is not str or not x for x in argv):
                bad()
        if "remote_root" in rule:
            root = rule["remote_root"]
            if type(root) is not str or not root.startswith("/") or root == "/" or any(x in ("", ".", "..") for x in root[1:].split("/")) or any(x in root for x in "~$*?[]\\"):
                bad()
        if "operation" in rule and (type(rule["operation"]) is not str or rule["operation"] not in _OPERATIONS[transport]):
            bad()
        if transport == "ssh" and ((shape == {"remote_root"}) or (rule["decision"] == "allow" and shape != {"argv"})):
            bad()
    return policy


def strict_load(data):
    """Parse bounded UTF-8 policy bytes with duplicate-key rejection."""
    return validate_policy(_json_bytes(data, POLICY_LIMIT, "policy-invalid"))


def _absolute_spelling(path):
    # abspath collapses '..' before the no-follow walk can validate the
    # preceding component. Preserve kernel traversal order, including '..'.
    return os.fspath(path) if os.path.isabs(path) else os.path.join(os.getcwd(), path)


def _regular_bytes(path, limit, kind):
    """Walk every component without following links, then read a bounded regular fd."""
    absolute = _absolute_spelling(path)
    parent, name = os.path.split(absolute)
    # Split the final component before Path normalizes trailing '/' or '/.'.
    # A file followed by a directory requirement is never that regular file.
    parts = Path(parent).parts
    directory = os.open(parts[0], os.O_RDONLY | _DIRECTORY | _CLOEXEC)
    try:
        for part in parts[1:]:
            try:
                next_fd = os.open(part, os.O_RDONLY | _DIRECTORY | _NOFOLLOW | _CLOEXEC, dir_fd=directory)
            except FileNotFoundError:
                raise PolicyError("missing-" + kind) from None
            except OSError:
                raise PolicyError(kind + "-ancestor-escape") from None
            os.close(directory)
            directory = next_fd
        return _regular_at(directory, name, limit, kind)
    finally:
        os.close(directory)


def _regular_at(directory, name, limit, kind):
    try:
        info = os.stat(name, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        raise PolicyError("missing-" + kind) from None
    if stat.S_ISLNK(info.st_mode):
        raise PolicyError(kind + "-symlink")
    if not stat.S_ISREG(info.st_mode):
        raise PolicyError(kind + "-fifo")
    if kind == "registry" and os.name != "nt" and (not compat.owned(info) or info.st_mode & 0o077):
        raise PolicyError("registry-private-mode")
    try:
        fd = os.open(name, os.O_RDONLY | _NOFOLLOW | _NONBLOCK | _CLOEXEC, dir_fd=directory)
    except OSError:
        raise PolicyError(kind + "-invalid") from None
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
            raise PolicyError(kind + "-invalid")
        data = os.read(fd, limit + 1)
        if len(data) > limit:
            raise PolicyError(kind + "-size-limit")
        if os.fstat(fd).st_size != len(data):
            raise PolicyError(kind + "-invalid")
        return data
    finally:
        os.close(fd)


def registry_path(environ=None):
    env = os.environ if environ is None else environ
    base = env.get("XDG_CONFIG_HOME") or os.path.join(env.get("HOME") or str(Path.home()), ".config")
    return os.path.join(base, "harness", "operations-bindings.json")


def _inside(path, root):
    try:
        return os.path.commonpath((path, root)) == root
    except ValueError:
        return False


def _registry_root(value):
    if type(value) is not str or "\x00" in value:
        return False
    try:
        os.fsencode(value)
        return os.path.isabs(value) and os.path.normpath(value) == value
    except (ValueError, TypeError):
        return False


def _registry(data):
    registry = _json_bytes(data, REGISTRY_LIMIT, "registry-invalid")
    if type(registry) is not dict or type(registry.get("schema_version")) is not int or registry["schema_version"] != 1 or type(registry.get("bindings")) is not list:
        raise PolicyError("registry-invalid")
    seen = set()
    for entry in registry["bindings"]:
        if type(entry) is not dict or not {"project_root", "policy_path", "sha256", "mode"} <= set(entry):
            raise PolicyError("registry-invalid")
        root = entry.get("binding_key", entry["project_root"])
        stored = entry["project_root"]
        if not _registry_root(root) or root in seen or not _registry_root(stored) or type(entry["policy_path"]) is not str or entry["policy_path"] != POLICY_RELATIVE or type(entry["sha256"]) is not str or not _HASH.fullmatch(entry["sha256"]) or type(entry["mode"]) is not str or entry["mode"] not in ("report", "enforce"):
            raise PolicyError("registry-invalid")
        seen.add(root)
    return registry


def _registry_present(path):
    # Portable absence check, without requiring POSIX directory descriptors.
    # Only ENOENT proves absence; lexists also hides EACCES and ENOTDIR.
    parts = Path(_absolute_spelling(path)).parts
    current = parts[0]
    for index, part in enumerate(parts[1:], start=1):
        current = os.path.join(current, part)
        ancestor = index < len(parts) - 1
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            tail = parts[index:]
            # Creating a missing prefix before '..' can reveal a different,
            # already existing registry that this read did not validate.
            if ".." in tail:
                raise PolicyError("registry-ancestor-escape") from None
            if os.name != "nt":
                try:
                    parent = os.path.dirname(current)
                    name_max = os.pathconf(parent, "PC_NAME_MAX")
                    path_max = os.pathconf(parent, "PC_PATH_MAX")
                    valid = (name_max > 0 and path_max > 0 and
                             all(len(os.fsencode(p)) <= name_max for p in tail) and
                             len(os.fsencode(_absolute_spelling(path))) < path_max)
                except (OSError, ValueError):
                    valid = False
                if not valid:
                    raise PolicyError("registry-ancestor-escape") from None
            return False
        except OSError:
            raise PolicyError("registry-ancestor-escape" if ancestor else "registry-invalid") from None
        if ancestor and not stat.S_ISDIR(info.st_mode):
            raise PolicyError("registry-ancestor-escape")
    return True


def load_registry(environ=None):
    """Return validated registry, or empty schema-1 registry only if absent."""
    path = registry_path(environ)
    try:
        if not _registry_present(path):
            return {"schema_version": 1, "bindings": []}
        if os.name == "nt":
            raise PolicyError("unsupported-platform")
        return _registry(_regular_bytes(path, REGISTRY_LIMIT, "registry"))
    except PolicyError as exc:
        if exc.error_id == "missing-registry":
            return {"schema_version": 1, "bindings": []}
        raise
    except (OSError, NotImplementedError, TypeError):
        raise PolicyError("registry-invalid") from None


def _boundary(cwd, root):
    current = cwd
    scanned = 0
    while current != root:
        scanned += 1
        if scanned > ROOT_SCAN_LIMIT:
            raise PolicyError("root-scan-limit")
        try:
            os.lstat(os.path.join(current, ".git"))
        except FileNotFoundError:
            pass
        except OSError:
            raise PolicyError("root-scan-error") from None
        else:
            return True
        parent = os.path.dirname(current)
        if parent == current:
            return True
        current = parent
    return False


def resolve_binding(cwd, *, environ=None):
    """Return {mode, policy, project_root, error_id}; off means no activation."""
    result = {"mode": "off", "policy": None, "project_root": None, "error_id": None}
    root_cwd = os.path.realpath(cwd)
    path = registry_path(environ)
    try:
        registry = load_registry(environ)
    except PolicyError as exc:
        return dict(result, mode="unknown", error_id=exc.error_id)
    candidates = sorted((e for e in registry["bindings"] if _inside(root_cwd, e.get("binding_key", e["project_root"]))),
                        key=lambda e: len(e.get("binding_key", e["project_root"])), reverse=True)
    matches = []
    for entry in candidates:
        try:
            if not _boundary(root_cwd, entry.get("binding_key", entry["project_root"])):
                matches.append(entry)
                # Candidates are closest first. A more distant binding must
                # not replace this mode if its longer boundary scan fails.
                break
        except PolicyError as exc:
            return dict(result, mode=entry["mode"], project_root=entry.get("binding_key", entry["project_root"]), error_id=exc.error_id)
    if not matches:
        return result
    entry = max(matches, key=lambda e: len(e.get("binding_key", e["project_root"])))
    mode, root = entry["mode"], entry.get("binding_key", entry["project_root"])
    result.update(mode=mode, project_root=root)
    if entry["project_root"] != root:
        result["error_id"] = "wrong-project"
        return result
    if _inside(os.path.realpath(path), root):
        result["error_id"] = "registry-inside-project"
        return result
    if os.name == "nt":
        result["error_id"] = "unsupported-platform"
        return result
    try:
        data = _regular_bytes(os.path.join(root, POLICY_RELATIVE), POLICY_LIMIT, "policy")
        if hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise PolicyError("policy-digest-drift")
        result["policy"] = strict_load(data)
    except PolicyError as exc:
        result["error_id"] = exc.error_id
    except OSError:
        result["error_id"] = "policy-invalid"
    return result


def _canonical_project(project):
    if not project:
        raise PolicyError("invalid-project")
    root = os.path.abspath(project)
    if not os.path.isdir(root) or root != os.path.realpath(root):
        raise PolicyError("invalid-project")
    if len(Path(root).parts) > ROOT_SCAN_LIMIT:
        raise PolicyError("root-scan-limit")
    directory = None
    try:
        directory = os.open(root, os.O_RDONLY | _DIRECTORY | _NOFOLLOW | _CLOEXEC)
        # Validate the original spelling before accepting the canonical key:
        # abspath can hide a symlink or a non-directory component before '..'.
        _check_parent_identity(project, directory)
    except (OSError, NotImplementedError, TypeError, PolicyError):
        raise PolicyError("invalid-project") from None
    finally:
        if directory is not None:
            os.close(directory)
    return root


def _registry_dir(environ):
    path = registry_path(environ)
    parent = os.path.dirname(path)
    base = os.path.dirname(parent)
    # Traverse every existing ancestor using no-follow directory descriptors.
    absolute = _absolute_spelling(base)
    parts = Path(absolute).parts
    directory = os.open(parts[0], os.O_RDONLY | _DIRECTORY | _CLOEXEC)
    try:
        for part in parts[1:] + ("harness",):
            try:
                os.mkdir(part, 0o700, dir_fd=directory)
            except FileExistsError:
                pass
            try:
                next_fd = os.open(part, os.O_RDONLY | _DIRECTORY | _NOFOLLOW | _CLOEXEC, dir_fd=directory)
            except OSError:
                raise PolicyError("registry-ancestor-escape") from None
            os.close(directory)
            directory = next_fd
    except BaseException:
        os.close(directory)
        raise
    if os.name != "nt":
        info = os.fstat(directory)
        if not compat.owned(info) or info.st_mode & 0o077:
            os.close(directory)
            raise PolicyError("registry-private-mode")
    return parent, directory


def _encode_registry(registry):
    try:
        data = (json.dumps(registry, ensure_ascii=True, sort_keys=True, indent=2,
                           allow_nan=False) + "\n").encode()
    except (ValueError, TypeError, RecursionError):
        raise PolicyError("registry-invalid") from None
    if len(data) > REGISTRY_LIMIT:
        raise PolicyError("registry-size-limit")
    return data


def _registry_write_limits(environ):
    path = _absolute_spelling(registry_path(environ))
    parent = os.path.dirname(path)
    existing = parent
    while True:
        try:
            info = os.lstat(existing)
            if not stat.S_ISDIR(info.st_mode):
                raise PolicyError("registry-ancestor-escape")
            break
        except FileNotFoundError:
            shorter = os.path.dirname(existing)
            if shorter == existing:
                raise PolicyError("registry-ancestor-escape") from None
            existing = shorter
    try:
        name_max = os.pathconf(existing, "PC_NAME_MAX")
        path_max = os.pathconf(existing, "PC_PATH_MAX")
    except (OSError, ValueError, NotImplementedError):
        raise PolicyError("registry-ancestor-escape") from None
    names = (os.path.basename(path), ".operations-bindings.lock",
             ".operations-bindings-" + "0" * 24)
    if (name_max <= 0 or path_max <= 0 or
            any(len(os.fsencode(name)) > name_max or
                len(os.fsencode(os.path.join(parent, name))) >= path_max for name in names)):
        raise PolicyError("registry-ancestor-escape")


def _mutate(environ, change):
    # Validate the prospective mutation before creating directories or a lock.
    # This snapshot is only a preflight: reload and revalidate under the lock.
    registry = load_registry(environ)
    changed = change(registry)
    # None means this operation has nothing to change. Linearize the no-op at
    # this validated read without creating a namespace or lock.
    if changed is None:
        return registry
    _encode_registry(changed)
    if os.name != "nt":
        _registry_write_limits(environ)
        # Owner commands run without threads. Preserve the caller's mask and
        # refuse before creating a namespace or an unusable coordination lock.
        current = os.umask(0o777)
        os.umask(current)
        if current & 0o700:
            raise PolicyError("registry-private-mode")
    parent, parent_fd = _registry_dir(environ)
    flags = os.O_CREAT | os.O_RDWR | _NOFOLLOW | _NONBLOCK | _CLOEXEC
    try:
        _check_parent_identity(parent, parent_fd)
        try:
            prior_lock = os.stat(".operations-bindings.lock", dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            prior_lock = None
        if prior_lock is not None and (not stat.S_ISREG(prior_lock.st_mode) or prior_lock.st_mode & 0o077 or not compat.owned(prior_lock)):
            raise PolicyError("registry-invalid")
        lock = os.open(".operations-bindings.lock", flags, 0o600, dir_fd=parent_fd)
        opened_lock = os.fstat(lock)
        if not stat.S_ISREG(opened_lock.st_mode) or (prior_lock is not None and (opened_lock.st_dev, opened_lock.st_ino) != (prior_lock.st_dev, prior_lock.st_ino)):
            os.close(lock)
            raise PolicyError("registry-invalid")
    except BaseException:
        os.close(parent_fd)
        raise
    try:
        if os.name != "nt":
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX)
        _check_parent_identity(parent, parent_fd)
        try:
            registry_before_bytes = _regular_at(parent_fd, "operations-bindings.json", REGISTRY_LIMIT, "registry")
            registry = _registry(registry_before_bytes)
        except PolicyError as exc:
            if exc.error_id != "missing-registry":
                raise
            registry_before_bytes = None
            registry = {"schema_version": 1, "bindings": []}
        changed = change(registry)
        if changed is None:
            return registry
        data = _encode_registry(changed)
        temp = ".operations-bindings-" + secrets.token_hex(12)
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _CLOEXEC, 0o600, dir_fd=parent_fd)
        try:
            compat.set_private(fd, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            # The target was read under lock; refuse a symlink introduced before replace.
            _check_parent_identity(parent, parent_fd)
            try:
                current = _regular_at(parent_fd, "operations-bindings.json", REGISTRY_LIMIT, "registry")
            except PolicyError as exc:
                if exc.error_id != "missing-registry":
                    raise
                current = None
            if current != registry_before_bytes:
                raise PolicyError("registry-invalid")
            _check_parent_identity(parent, parent_fd)
            os.replace(temp, "operations-bindings.json", src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            os.fsync(parent_fd)
            _check_parent_identity(parent, parent_fd)
        finally:
            try:
                os.unlink(temp, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
        return changed
    finally:
        os.close(lock)
        os.close(parent_fd)


def _check_parent_identity(parent, directory):
    walked = None
    try:
        # A leaf-only lstat follows ancestor symlinks. Walk the lexical namespace
        # again so a renamed ancestor linked back to the pinned inode is refused.
        parts = Path(_absolute_spelling(parent)).parts
        flags = os.O_RDONLY | _DIRECTORY | _NOFOLLOW | _CLOEXEC
        walked = os.open(parts[0], flags)
        for part in parts[1:]:
            next_fd = os.open(part, flags, dir_fd=walked)
            os.close(walked)
            walked = next_fd
        named = os.fstat(walked)
        opened = os.fstat(directory)
    except (OSError, NotImplementedError, TypeError):
        raise PolicyError("registry-ancestor-escape") from None
    finally:
        if walked is not None:
            os.close(walked)
    if not stat.S_ISDIR(named.st_mode) or (named.st_dev, named.st_ino) != (opened.st_dev, opened.st_ino):
        raise PolicyError("registry-ancestor-escape")


def bind_project(project, file, mode, *, environ=None):
    """Explicitly bind canonical ROOT to its validated policy bytes."""
    if mode not in ("report", "enforce"):
        raise PolicyError("invalid-mode")
    if os.name == "nt":
        raise PolicyError("unsupported-platform")
    root = _canonical_project(project)
    expected = os.path.join(root, POLICY_RELATIVE)
    if os.path.abspath(file) != expected:
        raise PolicyError("invalid-policy-path")
    if _inside(os.path.realpath(registry_path(environ)), root):
        raise PolicyError("registry-inside-project")
    try:
        data = _regular_bytes(file, POLICY_LIMIT, "policy")
    except OSError:
        raise PolicyError("policy-invalid") from None
    strict_load(data)
    entry = {"binding_key": root, "project_root": root, "policy_path": POLICY_RELATIVE,
             "sha256": hashlib.sha256(data).hexdigest(), "mode": mode}
    def change(registry):
        # Recheck policy at mutation time; invalid updates leave prior binding intact.
        current = _regular_bytes(file, POLICY_LIMIT, "policy")
        if current != data:
            raise PolicyError("policy-digest-drift")
        registry["bindings"] = [e for e in registry["bindings"] if e.get("binding_key", e["project_root"]) != root] + [entry]
        return registry
    try:
        _mutate(environ, change)
    except (OSError, NotImplementedError, TypeError):
        raise PolicyError("registry-invalid") from None
    return entry


def unbind_project(project, *, environ=None):
    root = _canonical_project(project)
    def change(registry):
        retained = [e for e in registry["bindings"] if e.get("binding_key", e["project_root"]) != root]
        if len(retained) == len(registry["bindings"]):
            return None
        registry["bindings"] = retained
        return registry
    try:
        return _mutate(environ, change)
    except (OSError, NotImplementedError, TypeError):
        raise PolicyError("registry-invalid") from None
