#!/usr/bin/env bash
# Stop: request inspection when an indirect edit has no later verification.
# A repeated Stop event with stop_hook_active set is allowed to finish.
set -euo pipefail
here=${BASH_SOURCE[0]%/*}
[[ $here != "${BASH_SOURCE[0]}" ]] || here=.
# pyrun.sh finds Python 3.9+ (never the Store stub); with none it says so on stderr and exits 1.
exec "$BASH" "$here/../pyrun.sh" harness warn helpers/_verification.py stop
