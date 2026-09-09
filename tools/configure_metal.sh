#!/usr/bin/env bash
# Requires Linux, pinned Metal, Clang 20 with working libstdc++ C++20 headers,
# cmake >= 3.24, ninja, and upstream native library dependencies.
# On a shared build host invoke via with-heavy-lock.sh.
set -euo pipefail
: "${TT_METAL_HOME:?Set TT_METAL_HOME to the pinned tt-metal checkout}"
project="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python3 "$project/tools/integrate_metal.py" "$TT_METAL_HOME" --project "$project"
cd "$TT_METAL_HOME"
args=(--configure-only --build-programming-examples --without-python-bindings --without-distributed)
if [[ "$(uname -m)" == aarch64 ]]; then
 if [[ -n "${RR_ENV_ROOT:-}" && -x "$RR_ENV_ROOT/prefix/usr/bin/clang++-20" ]]; then
  args+=(--toolchain-path "$project/cmake/user-prefix-toolchain.cmake")
 else
  args+=(--toolchain-path cmake/aarch64-linux-clang-20-libstdcpp-toolchain.cmake)
 fi
fi
./build_metal.sh "${args[@]}"
cmake --build build_Release --target tt_row_reduce_metal --parallel "${RR_BUILD_JOBS:-1}"
