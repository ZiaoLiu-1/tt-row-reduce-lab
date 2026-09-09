# Linux ARM64 environment

This records the 2026-09-09 UTC environment checkpoint. The tested host is
Ubuntu 22.04 on aarch64, with four logical CPUs and about 23.4 GiB RAM. The
isolated prefix provides Clang 20.1.8, GCC 12 C++20 headers and libstdc++, CMake
4.0.2, Ninja 1.11.1, and a Python venv. A real `std::span` / `std::bit_cast`
probe compiled, linked, and ran; its loader dependencies were inspected.
The project Metal target subsequently compiled and linked (C1); the official
simulator example returned 21. Custom-kernel execution remains pending.
See [STATE.md](../STATE.md) and [build logs](../results/build/) for the separate
environment, build and execution outcomes.

All commands below run in a separate Linux project checkout. They extract
packages into a user prefix and do not use sudo, install a kernel driver,
modify services, or change a system compiler. The existing Lima environment is
not involved. Source and simulator versions are recorded in
[upstream.lock](../upstream.lock).

## Prepare the prefix and pinned source

From this repository's root on the Linux host:

```bash
export RR_PROJECT="$PWD"
export RR_ENV_ROOT="$HOME/accelerator-projects-20260908/tt-row-reduce-lab"
export BUILD_PURPOSE='row reduction native dependencies'
bash "$RR_PROJECT/tools/with-heavy-lock.sh" \
  python3 "$RR_PROJECT/tools/bootstrap_jammy_arm64.py" --root "$RR_ENV_ROOT"
source "$RR_PROJECT/tools/activate_linux_prefix.sh"
```

The bootstrap reads the LLVM Jammy ARM64 package index, checks each archive's
SHA-256, and extracts `.deb` files with `dpkg-deb -x`. Downloads use a `.part`
file until verification succeeds. GCC 12 headers/runtime and native libraries
such as hwloc, NUMA, TBB, and Capstone also stay in this prefix. An initially listed TBB auxiliary package was unavailable for this architecture
and removed from the requested package list. The script checks for at
least 3 GB free before this dependency stage; this is not a guarantee that the
source checkout and build will fit. Inspect `df -h "$RR_ENV_ROOT"` between stages.

The host lacked Python `ensurepip`. The working workaround, already included in
the bootstrap, uses its existing pip to populate an empty venv:

```bash
python3 -m venv --without-pip "$RR_ENV_ROOT/venv"
python3 -m pip --python "$RR_ENV_ROOT/venv/bin/python" \
  --cache-dir "$RR_ENV_ROOT/downloads/pip-cache" install \
  pip cmake==4.0.2 ninja==1.11.1.4 pyyaml jinja2 loguru
```

The first setup invocation used pip's default cache setting; it did not
explicitly redirect that cache. No existing user cache was inspected or cleaned.
The reproducible recipe now specifies its own download cache, and activation
also redirects `TMPDIR` into the isolated environment.

This requires a host pip that supports `--python`. No system `python3-venv`
package was installed. CMake and Ninja are pinned in this recipe; Ubuntu/LLVM
package selection uses the available package indexes, so archive hashes and
actual compiler/runtime identities must be retained with a new run.

For a **new**, absent `tt-metal` directory, obtain the fixed checkout under the
same shared lock. Do not run this block over an existing checkout:

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
```

The observed checkout with recursive submodules occupied 881 MiB. SFPI comes
from that checkout's pinned manifest. Place the separately hash-verified
official ARM64 simulator asset at
`$RR_ENV_ROOT/simulator/libttsim_wh_aarch64.so`; copy the pinned
`tt_metal/soc_descriptors/wormhole_b0_80_arch.yaml` alongside it as
`soc_descriptor.yaml`. The expected simulator hash is in `upstream.lock`.
Simulator download and extraction also require the shared lock.

## Compiler selection, configuration, and build

An initial direct compiler probe selected GCC 12 through a wrapper, but upstream
CMake resolved the compiler's real path and bypassed that wrapper. Inspection
of `CMAKE_CXX_IMPLICIT_INCLUDE_DIRECTORIES` then exposed GCC 11 headers. The
initial build was stopped and its log retained. The activation script now
supplies `--gcc-toolchain=$RR_ENV_ROOT/prefix/usr` explicitly in both `CFLAGS`
and `CXXFLAGS`, in addition to the wrapper. It supplies library search paths
and runtime loader paths for the prefix as well. A later bundled UMD build
exposed Ubuntu's separate ARM64 hwloc configuration headers; both compiler
flag sets now also include `-isystem $RR_ENV_ROOT/prefix/usr/include/aarch64-linux-gnu`.
The failing build log is retained.

The project-owned `cmake/user-prefix-toolchain.cmake` includes the pinned
upstream ARM64 toolchain and then fixes both compiler paths to the extracted
prefix. This avoids an observed CMake 4.0.2 cache reset caused by repeated bare
compiler-name assignments. Two consecutive configurations were checked to keep
MPI and Python bindings disabled; the upstream toolchain file remains unchanged.

After correcting an already configured tree, refresh compiler detection; merely
changing `PATH` leaves CMake's old include discovery cached:

```bash
export BUILD_PURPOSE='refresh compiler detection and configure'
bash "$RR_PROJECT/tools/with-heavy-lock.sh" \
  cmake --fresh -S "$TT_METAL_HOME" -B "$TT_METAL_HOME/build_Release" -G Ninja \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=build_Release \
  -DCMAKE_TOOLCHAIN_FILE="$RR_PROJECT/cmake/user-prefix-toolchain.cmake" \
  -DBUILD_PROGRAMMING_EXAMPLES=ON -DWITH_PYTHON_BINDINGS=OFF \
  -DENABLE_DISTRIBUTED=OFF
```

Run configuration under `with-heavy-lock.sh` too: it downloads and unpacks
dependencies. Even with Python bindings and distributed compute disabled,
upstream configure fetched tool dependencies including Emscripten. Avoid
assuming that a narrow target makes configure dependency-free. Its caches and
downloads consumed space before compilation; about 5.1 GiB remained when the
initial narrow upstream smoke build began. This is a dated observation, not
the current free-space reading or a measured maximum build footprint.

The project integration script checks the exact upstream commit and permits
only an unchanged upstream CMake file or its exact previously generated block.
It refuses unrelated edits. For a first project configure/build:

```bash
export BUILD_PURPOSE='narrow Metal host target'
export RR_BUILD_JOBS=1
bash "$RR_PROJECT/tools/with-heavy-lock.sh" \
  bash "$RR_PROJECT/tools/configure_metal.sh"
```

The initial upstream `metal_example_add_2_integers_in_riscv` build used one job.
Only after observing more than 20 GiB available RAM and individual compiler RSS
below 300 MB did the coordinator increase to two jobs, leaving two logical CPUs
for existing workloads. That observation does not justify an unconditional
two-job default or a full TTNN build. `configure_metal.sh` defaults to one job
and builds only `tt_row_reduce_metal` after configuration.

## Isolated caches and evidence

Always source the activation script before build, JIT, or execution. In addition
to the compiler/library paths, it sets:

```text
TT_METAL_HOME       = $RR_ENV_ROOT/tt-metal
TT_METAL_SIMULATOR  = $RR_ENV_ROOT/simulator/libttsim_wh_aarch64.so
TT_METAL_CACHE     = $RR_ENV_ROOT/kernel-cache
XDG_CACHE_HOME      = $RR_ENV_ROOT/xdg-cache
```

The pinned JIT otherwise defaults to a cache under the user's home. Explicit
cache locations keep generated artifacts inside the authorized project area.
The script also sets `TT_METAL_SLOW_DISPATCH_MODE=1` and
`TT_METAL_DISABLE_SFPLOADMACRO=1` for the documented simulator constraints.

Capture the actual environment after activation:

```bash
python3 "$RR_PROJECT/tools/capture_environment.py" \
  --output "$RR_PROJECT/environment.json"
```

The capture requires `TT_METAL_HOME`, creates the output directory, and records
the upstream/submodule identities, compiler version and C++ include search,
resolved libstdc++ link path, simulator asset hash, and selected execution
switches. Preserve the separate linked-probe loader output when establishing
runtime-library identity; a compiler search path alone does not prove which
shared library an executable loads. Raw build, JIT, and execution logs remain
separate evidence levels. CPU verification is in [results/cpu](../results/cpu/);
neither these environment steps nor simulator wall time establish silicon
performance.
