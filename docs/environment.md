# Linux ARM64 setup

The recorded build used Ubuntu 22.04 on ARM64, Clang 20.1.8, GCC 12 C++20
headers and libstdc++, CMake 4.0.2 and Ninja 1.11.1. The Metal host, device JIT
and simulator matrix passed in that environment. The versions and commands
below describe that setup; [validation.md](validation.md) links to its results.

The bootstrap extracts packages into a user directory. It needs `dpkg-deb`,
`apt-get`, Python and a host pip with `--python` support. It does not require
sudo or replace the system compiler. Use a separate checkout and environment
for this build.

## Prepare dependencies and source

Run from the project root on the Linux host:

```bash
export RR_PROJECT="$PWD"
export RR_ENV_ROOT="$HOME/accelerator-projects-20260908/tt-row-reduce-lab"
export BUILD_PURPOSE='row reduction native dependencies'
bash "$RR_PROJECT/tools/with-heavy-lock.sh" \
  python3 "$RR_PROJECT/tools/bootstrap_jammy_arm64.py" --root "$RR_ENV_ROOT"
source "$RR_PROJECT/tools/activate_linux_prefix.sh"
```

The bootstrap verifies LLVM archive hashes against the Jammy ARM64 package
index, downloads GCC 12 and native dependencies, and extracts them under
`prefix/`. CMake, Ninja and Python dependencies go into `venv/`. The venv is
created with `--without-pip`, then populated by the host pip; this also works
when the host lacks `ensurepip`.

CMake and Ninja versions are fixed in the script. LLVM and Ubuntu package
selection follows the available package indexes, so capture the actual
compiler/runtime versions with each new build. The bootstrap checks for 3 GB
free before downloading dependencies. Source, configure-time downloads and
compiled output need additional space; inspect `df -h "$RR_ENV_ROOT"` between
stages. The recorded source checkout with recursive submodules occupied
881 MiB, which is not the total build footprint.

Obtain the pinned source in a new, absent `tt-metal` directory:

```bash
export BUILD_PURPOSE='pinned Metal source and submodules'
bash "$RR_PROJECT/tools/with-heavy-lock.sh" bash -c '
  set -euo pipefail
  test ! -e "$TT_METAL_HOME"
  git clone --filter=blob:none --no-checkout \
    https://github.com/tenstorrent/tt-metal.git "$TT_METAL_HOME"
  git -C "$TT_METAL_HOME" fetch --depth 1 origin \
    89e1256c982a5b4739d173bcc446c8c748a44b40
  git -C "$TT_METAL_HOME" checkout --detach \
    89e1256c982a5b4739d173bcc446c8c748a44b40
  git -C "$TT_METAL_HOME" submodule update --init --recursive --depth 1
'
bash "$RR_PROJECT/tools/with-heavy-lock.sh" \
  python3 "$RR_PROJECT/tools/fetch_simulator.py" --metal "$TT_METAL_HOME" \
  --output "$RR_ENV_ROOT/simulator"
```

The fetch tool verifies the simulator hash from [upstream.lock](../upstream.lock)
and copies the pinned Wormhole descriptor beside it. SFPI comes from the
Metal checkout's own version manifest.

## Compiler selection

Clang needs compatible C++20 standard-library headers as well as a linker and
runtime library. The activation script passes
`--gcc-toolchain=$RR_ENV_ROOT/prefix/usr` explicitly in `CFLAGS` and `CXXFLAGS`.
It also adds the prefix's ARM64 include directory for dependencies such as
hwloc, plus library and loader search paths.

These settings address failures encountered during the recorded build. A
compiler wrapper alone was insufficient because upstream CMake resolved the
compiler's real path and selected older GCC 11 headers. The project-owned
[toolchain file](../cmake/user-prefix-toolchain.cmake) includes the pinned
upstream toolchain and then fixes both compiler paths. This also avoids a
CMake 4.0.2 cache reset observed when the upstream file assigned bare compiler
names. The original failed attempts remain in [build logs](../results/build/).

To refresh an already configured tree after correcting compiler selection:

```bash
export BUILD_PURPOSE='refresh compiler detection and configure'
bash "$RR_PROJECT/tools/with-heavy-lock.sh" \
  cmake --fresh -S "$TT_METAL_HOME" -B "$TT_METAL_HOME/build_Release" -G Ninja \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=build_Release \
  -DCMAKE_TOOLCHAIN_FILE="$RR_PROJECT/cmake/user-prefix-toolchain.cmake" \
  -DBUILD_PROGRAMMING_EXAMPLES=ON -DWITH_PYTHON_BINDINGS=OFF \
  -DENABLE_DISTRIBUTED=OFF
```

For the project configure/build:

```bash
export BUILD_PURPOSE='narrow Metal host target'
export RR_BUILD_JOBS=1
bash "$RR_PROJECT/tools/with-heavy-lock.sh" \
  bash "$RR_PROJECT/tools/configure_metal.sh"
```

Configuration downloads dependencies even with Python bindings and distributed
compute disabled. The build selects only `tt_row_reduce_metal`, with one job
by default. The lock wrapper serializes large downloads and builds on a shared
host; all callers must use the same `ACCELERATOR_BUILD_BASE` (by default,
`$HOME/accelerator-projects-20260908`).

## Activate and record the environment

Source `tools/activate_linux_prefix.sh` in each new shell before building or
running. Besides compiler and library paths, it sets:

```text
TT_METAL_HOME       = $RR_ENV_ROOT/tt-metal
TT_METAL_SIMULATOR  = $RR_ENV_ROOT/simulator/libttsim_wh_aarch64.so
TT_METAL_CACHE      = $RR_ENV_ROOT/kernel-cache
XDG_CACHE_HOME      = $RR_ENV_ROOT/xdg-cache
```

It also sets `TT_METAL_SLOW_DISPATCH_MODE=1` and
`TT_METAL_DISABLE_SFPLOADMACRO=1`, and keeps pip downloads and temporary files
inside the environment directory. An explicit `TT_METAL_CACHE` keeps JIT output
separate from other checkouts.

Save a new environment record after activation:

```bash
python3 "$RR_PROJECT/tools/capture_environment.py" \
  --output "$RR_ENV_ROOT/environment-rerun.json"
```

The capture records upstream/submodule revisions, compiler version, C++ include
search, libstdc++ link path and simulator hash. To verify the loaded runtime,
also inspect the loader output of a linked C++20 probe; the compiler's search
path alone does not show which shared library an executable loads. Continue
with the [run instructions](running.md#simulator-matrix).
