#!/usr/bin/env bash
# Stop: request inspection when an indirect edit has no later verification.
# A repeated Stop event with stop_hook_active set is allowed to finish.
set -euo pipefail
command -v python3 >/dev/null 2>&1 || exit 0
exec python3 "${BASH_SOURCE[0]%/*}/_verification.py" stop
