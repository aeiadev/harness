#!/usr/bin/env bash
# Report local repository facts without changing the working tree.
set -euo pipefail

if ! command -v git >/dev/null 2>&1; then
  printf 'error: missing requirement: git\n' >&2
  exit 2
fi

if (( $# > 1 )); then
  printf 'usage: context-status.sh [path]\n' >&2
  exit 2
fi

requested_path=${1:-$PWD}
if [[ ! -d "$requested_path" ]]; then
  printf 'error: not a directory: %s\n' "$requested_path" >&2
  exit 2
fi

export GIT_OPTIONAL_LOCKS=0
if ! project_root=$(git -C "$requested_path" rev-parse --show-toplevel 2>/dev/null); then
  printf 'error: not inside a Git working tree: %s\n' "$requested_path" >&2
  exit 2
fi

branch=$(git -C "$project_root" symbolic-ref --quiet --short HEAD 2>/dev/null || true)
branch=${branch:-'(detached)'}
head_commit=$(git -C "$project_root" rev-parse --verify HEAD 2>/dev/null || true)
head_commit=${head_commit:-'(unborn)'}
upstream=$(git -C "$project_root" rev-parse --abbrev-ref '@{upstream}' 2>/dev/null || true)

ahead=unknown
behind=unknown
if [[ -n "$upstream" ]]; then
  counts=$(git -C "$project_root" rev-list --left-right --count "$upstream...HEAD" 2>/dev/null || true)
  if [[ "$counts" =~ ^([0-9]+)[[:space:]]+([0-9]+)$ ]]; then
    behind=${BASH_REMATCH[1]}
    ahead=${BASH_REMATCH[2]}
  fi
fi

printf 'root: %s\n' "$project_root"
printf 'branch: %s\n' "$branch"
printf 'commit: %s\n' "$head_commit"
printf 'upstream: %s\n' "${upstream:-none}"
printf 'ahead: %s\n' "$ahead"
printf 'behind: %s\n' "$behind"
if [[ -f "$project_root/STATE.md" ]]; then
  printf 'state_file: STATE.md\n'
else
  printf 'state_file: missing\n'
fi
if [[ -f "$project_root/DECISIONS.md" ]]; then
  printf 'decisions_file: present\n'
else
  printf 'decisions_file: missing\n'
fi
printf 'working_tree:\n'
git -C "$project_root" status --short --branch
