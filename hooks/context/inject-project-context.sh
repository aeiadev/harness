#!/usr/bin/env bash
# Session context is reference data. Reset the session's markers on clear/compact.
# Runtime requirements: Bash, Python 3.9+, and Git.
set -euo pipefail

here=${BASH_SOURCE[0]%/*}
[[ $here != "${BASH_SOURCE[0]}" ]] || here=.
# pyrun.sh finds Python 3.9+ (never the Store stub); with none it says so on stderr and exits 1.
exec "$BASH" "$here/../pyrun.sh" harness warn context/inject_project_context.py "$@"
