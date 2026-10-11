#!/usr/bin/env bash
# PostToolUse: remind the assistant to inspect the result of indirect edits.
set -euo pipefail
here=${BASH_SOURCE[0]%/*}
[[ $here != "${BASH_SOURCE[0]}" ]] || here=.
# pyrun.sh finds Python 3.9+ (never the Store stub); with none it says so on stderr and exits 1.
exec "$BASH" "$here/../pyrun.sh" harness warn helpers/_verification.py post
