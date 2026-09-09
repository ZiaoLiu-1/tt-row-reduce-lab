#!/usr/bin/env bash
# Capture one real simulator run, then require a newly created profiler CSV.
set -euo pipefail
if [[ $# -lt 2 || $# -gt 3 ]]; then
    echo "usage: tools/profile.sh /absolute/path/to/tt_row_reduce /new/output/directory [timeout_seconds]" >&2
    exit 2
fi
: "${TT_METAL_HOME:?set TT_METAL_HOME to the pinned checkout}"
: "${TT_METAL_SIMULATOR:?set TT_METAL_SIMULATOR to the pinned official Wormhole .so}"
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
profiler_csv="${TT_METAL_HOME}/generated/profiler/.logs/profile_log_device.csv"
if [[ -e "${profiler_csv}" ]]; then
    echo "Refusing to reuse an existing profiler CSV: ${profiler_csv}" >&2
    echo "Archive it explicitly before starting this capture." >&2
    exit 2
fi
if [[ -n "${TT_METAL_WATCHER:-}" || -n "${TT_METAL_DPRINT_CORES:-}" ]]; then
    echo "Disable TT_METAL_WATCHER and TT_METAL_DPRINT_CORES for this profiler capture." >&2
    exit 2
fi
python3 "${project_root}/tools/run_matrix.py" --binary "$1" --output-dir "$2" \
    --case smoke --profile --timeout "${3:-300}"
if [[ -f "${profiler_csv}" ]]; then
    cp -- "${profiler_csv}" "$2/profile_log_device.csv"
fi
python3 "${project_root}/tools/check_profiler.py" "$2/profile_log_device.csv" --output "$2/profiler-validation.json"
