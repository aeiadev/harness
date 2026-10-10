#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 1 ]; then
    printf '%s\n' 'usage: release-notes.sh VERSION [CHANGELOG]' >&2
    exit 2
fi

version=$1
changelog=${2:-"$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/CHANGELOG.md"}
if [ ! -f "$changelog" ] || [ ! -r "$changelog" ]; then
    printf 'release-notes: %s: cannot read\n' "$changelog" >&2
    exit 1
fi

if ! notes=$(RELEASE_NOTES_VERSION=$version awk '
    BEGIN { heading = "## " ENVIRON["RELEASE_NOTES_VERSION"] }
    $0 == heading { found = 1; active = 1; next }
    active && /^## / { exit }
    active {
        if ($0 ~ /^[[:space:]]*$/) {
            if (started) blanks++
        } else {
            while (blanks-- > 0) print ""
            blanks = 0
            print
            started = 1
        }
    }
    END { if (!found) exit 1 }
' "$changelog"); then
    printf 'release-notes: no section for %s\n' "$version" >&2
    exit 1
fi

if [ -n "$notes" ]; then
    printf '%s\n' "$notes"
fi
