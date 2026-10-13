"""Resolve the project shared by context injection and checkpoint recovery."""

import os
from pathlib import Path
import subprocess


def selected_cwd(event):
    workspace = event.get("workspace")
    choices = (event.get("cwd"), event.get("project_dir"),
               workspace.get("current_dir") if isinstance(workspace, dict) else None,
               os.environ.get("CLAUDE_PROJECT_DIR"), os.getcwd())
    for choice in choices:
        if isinstance(choice, str) and choice.strip():
            path = Path(choice).resolve()
            if path.is_dir():
                return path
    return None


def permitted(root):
    root = root.resolve()
    return root != Path.home().resolve() and root != Path(root.anchor)


def declared_root():
    value = os.environ.get("HARNESS_PROJECT_ROOT")
    if not isinstance(value, str) or not value.strip():
        return None
    path = Path(value)
    if not path.is_absolute() or not path.is_dir():
        return None
    root = path.resolve()
    return root if permitted(root) else None


def git_root(cwd):
    try:
        result = subprocess.run(["git", "-C", str(cwd), "rev-parse", "--show-toplevel"],
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, timeout=.7, check=True)
        value = os.fsdecode(result.stdout).strip()
        root = Path(value).resolve()
        if not value or not root.is_dir() or not permitted(root):
            return None
        cwd.relative_to(root)
        return root
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def project_root(event):
    cwd = selected_cwd(event)
    if cwd is None:
        return None, None
    root = git_root(cwd)
    if root is None:
        declared = declared_root()
        if declared is not None:
            try:
                cwd.relative_to(declared)
                root = declared
            except ValueError:
                pass
    return root, cwd


def valid_pin(base):
    if not isinstance(base, str) or not base.strip():
        return None
    path = Path(base)
    if not path.is_absolute() or not path.is_dir():
        return None
    path = path.resolve()
    if not permitted(path):
        return None
    return path if declared_root() == path or git_root(path) == path else None


def contained_file(base, relative):
    if not isinstance(relative, str) or not relative or len(relative.encode()) > 512:
        return None
    part = Path(relative)
    if part.is_absolute() or ".." in part.parts:
        return None
    base = base.resolve()
    candidate = base / part
    current = base
    for piece in part.parts:
        current = current / piece
        if current.is_symlink():
            return None
    try:
        candidate.resolve().relative_to(base)
    except ValueError:
        return None
    return candidate
