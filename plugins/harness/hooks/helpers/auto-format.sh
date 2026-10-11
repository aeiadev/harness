#!/usr/bin/env bash
# Explicitly enabled formatting can execute repository configuration and plugins.
# No downloads or package runners. Missing tools and unsupported files are no-ops.
set -euo pipefail
[[ "${HARNESS_AUTO_FORMAT:-}" == "1" ]] || exit 0
here=${BASH_SOURCE[0]%/*}
[[ $here != "${BASH_SOURCE[0]}" ]] || here=.
# pyrun.sh finds Python 3.9+ (never the Store stub); with none it says so on stderr and exits 1.
exec "$BASH" "$here/../pyrun.sh" harness warn helpers/auto_format.py
