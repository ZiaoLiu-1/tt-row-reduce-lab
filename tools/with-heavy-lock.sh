#!/usr/bin/env bash
set -euo pipefail
base="${ACCELERATOR_BUILD_BASE:-$HOME/accelerator-projects-20260908}"
lock="$base/.heavy-build.lock"
mkdir -p "$base"
if ! mkdir "$lock" 2>/dev/null; then
  echo 'Shared heavy-build lock is occupied; no operation started.' >&2
  cat "$lock/owner" >&2 2>/dev/null || true
  exit 75
fi
owner="tt-row-reduce-lab:$$:$(date -u +%Y%m%dT%H%M%SZ)"
printf '%s\npurpose=%s\n' "$owner" "${BUILD_PURPOSE:-narrow Metal setup/build}" > "$lock/owner"
release() {
  if [[ -f "$lock/owner" && "$(head -n 1 "$lock/owner")" == "$owner" ]]; then
    rm "$lock/owner"
    rmdir "$lock"
  fi
}
trap release EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
"$@"
