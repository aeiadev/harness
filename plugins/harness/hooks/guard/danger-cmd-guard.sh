#!/usr/bin/env bash
# PreToolUse shell hook: exit 2 with a visible reason for dangerous commands.
set -euo pipefail
hook_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$hook_dir/danger_cmd_guard.py"
