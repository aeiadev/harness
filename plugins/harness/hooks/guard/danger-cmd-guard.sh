#!/usr/bin/env bash
# PreToolUse shell hook: exit 2 with a visible reason for dangerous commands.
set -euo pipefail
hook_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# pyrun.sh finds Python 3.9+ (never the Store stub); with none it exits 2, so the command stays blocked.
exec "$BASH" "$hook_dir/../pyrun.sh" harness block guard/danger_cmd_guard.py
