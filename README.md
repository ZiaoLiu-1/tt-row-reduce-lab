# TT Row Reduce Lab

A small TT-Metalium row-sum learning project: C++ host orchestration, one Wormhole
Tensix node, and reader/compute/writer kernels. Inputs and outputs are BF16;
the host checks device readback against a reference computed from the quantized
input bits. The scope is `1 ≤ M,N ≤ 128`, with explicit padding and tile layout.

**Current evidence: C0 CPU contract tests have run; custom-kernel ttsim validation
is pending.** Building the host, JIT compilation and simulator execution are
separate milestones in [STATE.md](STATE.md). There is no Tenstorrent card and no
hardware performance result. The commands below are the reproducible interface;
only saved, source-bound results establish which steps have passed.

## Start with the CPU contract

C++20 and CMake 3.24+ are sufficient for the CPU portion:

```sh
cmake -S . -B build_cpu -DROW_REDUCE_SANITIZERS=ON
cmake --build build_cpu --parallel 1
ctest --test-dir build_cpu --output-on-failure
./build_cpu/row_reduce_cpu --rows 33 --cols 33 --pattern decimals --seed 109
python3 -m unittest discover -s tests -p 'test_tools.py' -v
```

On a system without CMake, the contract tests also build directly:

```sh
mkdir -p build_cpu
clang++ -std=c++20 -O1 -g -Wall -Wextra -Wpedantic -Werror \
  -fsanitize=address,undefined -fno-omit-frame-pointer -Iinclude \
  tests/cpu_tests.cpp -o build_cpu/row_reduce_cpu_tests
./build_cpu/row_reduce_cpu_tests
```

The CPU CLI labels its output `C0/cpu_reference`. It does not load Metal or run a
device kernel. Evidence-tool regression fixtures are also CPU tests.

## Build the Metal host on Linux

Use the exact commits and release digests in [upstream.lock](upstream.lock).
The pinned Metal tree requires Clang 20, working C++20 libstdc++ headers/runtime,
CMake, Ninja and its native dependencies. On Ubuntu 22.04, the intended
libstdc++ dependency is GCC 12; the compiler executable alone is insufficient.
The small [integration tool](tools/integrate_metal.py) adds this target to the
pinned upstream checkout while rejecting unrelated top-level CMake edits.

The actual user-prefix bootstrap and compiler checks are documented in
[docs/environment.md](docs/environment.md).

With the checkout and toolchain ready:

```sh
export TT_METAL_HOME=/absolute/path/to/pinned/tt-metal
python3 tools/fetch_simulator.py --metal "$TT_METAL_HOME" --output /absolute/path/to/simulator
export TT_METAL_SIMULATOR=/absolute/path/to/simulator/libttsim_wh_aarch64.so
export TT_METAL_SLOW_DISPATCH_MODE=1
export TT_METAL_DISABLE_SFPLOADMACRO=1
export TT_METAL_CACHE=/absolute/path/to/isolated/project-kernel-cache
bash tools/configure_metal.sh
```

For Linux x86_64, select `libttsim_wh.so`. The fetch tool copies the pinned
Wormhole descriptor next to the library and verifies its release SHA-256. The
configure script selects the upstream ARM64 toolchain on `aarch64` and builds
only `tt_row_reduce_metal`, with one build job by default. Shared environments
must use [with-heavy-lock.sh](tools/with-heavy-lock.sh) for downloads and builds.
The runner requires an absolute `TT_METAL_CACHE` and preserves that environment
value, keeping JIT writes out of a shared default home cache.

## Run and retain the evidence

Commit the project source before capturing a result. Start with one 32×32 smoke
and calibrate the timeout from its actual process time:

```sh
python3 tools/run_matrix.py \
  --binary "$TT_METAL_HOME/build_Release/row-reduce-lab/tt_row_reduce_metal" \
  --case smoke --timeout 300 --output-dir results/smoke
python3 tools/run_matrix.py \
  --binary "$TT_METAL_HOME/build_Release/row-reduce-lab/tt_row_reduce_metal" \
  --case all --timeout 300 --output-dir results
```

`--case all` launches 13 processes serially: the 12 required shapes and one
33×33 process that reuses its mesh, tensors and workload for two different
inputs. It verifies all 14 outputs. A full successful matrix is the C3 gate.
Use a fresh output directory for every attempt; existing evidence is never
overwritten. `--list` shows the exact shapes, patterns and seeds without running.

The runner writes `environment.json`, `simulator.jsonl`, `summary.json` and
`raw/*.stdout.log` / `raw/*.stderr.log`. Records bind project source, binary,
Metal commit, simulator release/asset SHA, input and output bytes, command,
exit status and elapsed wall time. The checker independently regenerates the
input, decodes the output tile layout and recomputes the numerical budget.
Timeout, unsupported environment, crash, protocol error and numerical failure
remain visible. A partial run cannot produce a complete-matrix C3 summary.

Profiler capture, once the simulator run works:

```sh
bash tools/profile.sh \
  "$TT_METAL_HOME/build_Release/row-reduce-lab/tt_row_reduce_metal" \
  results/profile 300
```

This requires a newly created CSV and complete reader/compute/writer scopes.
Missing profiler output fails validation. Simulator wall time and raw profiler
counters describe software simulation only; neither is silicon latency,
bandwidth or speedup.

## Read the implementation

- [Design and numerical contract](docs/design.md): layout, dataflow, ownership and validation.
- [中文五段关键代码导读](docs/explain.md): a short 33×33 demonstration and explanation prompts.
- [Pinned sources and attribution](docs/source-notes.md), [LICENSE](LICENSE), [NOTICE](NOTICE).
- [Evidence and personal-understanding boundary](docs/resume-evidence.md).

The project supports a single node. Multi-node reduction, other operators and
silicon tuning are outside this version's implemented contract.
