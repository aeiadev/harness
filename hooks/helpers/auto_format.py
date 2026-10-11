#!/usr/bin/env python3
"""PostToolUse: run an installed formatter on files the tool just wrote (auto-format.sh)."""

import sys
sys.dont_write_bytecode = True

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

try:
    event = json.loads(sys.stdin.buffer.read())
except (ValueError, OSError):
    sys.exit(0)
if not isinstance(event, dict):
    sys.exit(0)
data = event.get("tool_input", {})
name = event.get("tool_name", "")
if name in {"apply_patch", "functions.apply_patch"}:
    patch = data if isinstance(data, str) else next((data.get(key) for key in ("patch", "input")
            if isinstance(data.get(key), str)), "") if isinstance(data, dict) else ""
    paths = []
    for line in patch.splitlines():
        match = re.fullmatch(r"\*\*\* (?:Add|Update) File: (.+)", line)
        moved = re.fullmatch(r"\*\*\* Move to: (.+)", line)
        if match:
            paths.append(match[1])
        elif moved and paths:
            paths[-1] = moved[1]
elif name in {"Write", "Edit", "MultiEdit"} and isinstance(data, dict) and isinstance(data.get("file_path"), str):
    paths = [data["file_path"]]
else:
    sys.exit(0)
cwd = event.get("cwd")

def format_file(filename):
    path = Path(filename)
    if not path.is_absolute() and isinstance(cwd, str):
        path = Path(cwd) / path
    try:
        path = path.resolve()
        if not path.is_file():
            return
    except (OSError, ValueError, RuntimeError):
        return
    for args in choices.get(path.suffix.lower(), []):
        executable = shutil.which(args[0])
        if executable:
            try:
                subprocess.run([executable, *args[1:], os.fspath(path)],
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=15, check=False)
            except (OSError, subprocess.TimeoutExpired):
                pass
            break

choices = {
    ".py": [("ruff", "format", "--"), ("black", "-q", "--")],
    ".go": [("gofmt", "-w")],
    ".rs": [("rustfmt", "--")],
    ".php": [("phpcbf",)],
    ".sh": [("shfmt", "-w")],
    ".bash": [("shfmt", "-w")],
}
for ext in ("js", "jsx", "ts", "tsx", "json", "css", "scss", "html", "md", "yaml", "yml"):
    choices["." + ext] = [("prettier", "--write", "--log-level", "silent", "--")]
for filename in dict.fromkeys(paths):
    format_file(filename)
