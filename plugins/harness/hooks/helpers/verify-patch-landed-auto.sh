#!/usr/bin/env bash
# PostToolUse: remind the assistant to inspect the result of indirect edits.
set -euo pipefail
command -v python3 >/dev/null 2>&1 || exit 0
exec python3 "${BASH_SOURCE[0]%/*}/_verification.py" post
