# Building and running

Run the following commands from the project root. The CPU executable is useful
for inspecting quantization and layout. The Metal executable runs the three
device kernels and returns their output.

## CPU build

```sh
cmake -S . -B build_cpu -DROW_REDUCE_SANITIZERS=ON
cmake --build build_cpu --parallel 1
ctest --test-dir build_cpu --output-on-failure
./build_cpu/row_reduce_cpu --rows 33 --cols 33 --pattern decimals --seed 109
```

CMake registers the C++ contract, matrix and invalid-shape tests. Run the Python
tool tests separately:

```sh
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

Without CMake, the C++ tests can be compiled directly with Clang:

```sh
mkdir -p build_cpu
clang++ -std=c++20 -O1 -g -Wall -Wextra -Wpedantic -Werror \
  -fsanitize=address,undefined -fno-omit-frame-pointer -Iinclude \
  tests/cpu_tests.cpp -o build_cpu/row_reduce_cpu_tests
./build_cpu/row_reduce_cpu_tests
```

## Metal build

Prepare the pinned checkout and native dependencies using the
[Linux setup guide](environment.md). If you use the ARM64 activation script,
it sets the variables below. Otherwise, set them to your own absolute paths:

```sh
export TT_METAL_HOME=/absolute/path/to/pinned/tt-metal
export TT_METAL_SIMULATOR=/absolute/path/to/simulator/libttsim_wh_aarch64.so
export TT_METAL_CACHE=/absolute/path/to/isolated/kernel-cache
export TT_METAL_SLOW_DISPATCH_MODE=1
export TT_METAL_DISABLE_SFPLOADMACRO=1
bash tools/with-heavy-lock.sh \
  python3 tools/fetch_simulator.py --metal "$TT_METAL_HOME" \
  --output /absolute/path/to/simulator
bash tools/with-heavy-lock.sh bash tools/configure_metal.sh
```

For Linux x86_64, use `libttsim_wh.so`. The fetch tool verifies the release
SHA-256 and copies the Wormhole descriptor beside the library. The integration
tool adds this project's CMake target to the pinned upstream checkout and
refuses unrelated edits to its top-level CMake file. Configuration may download
dependencies; the build itself selects only `tt_row_reduce_metal` and defaults
to one job.

## Simulator matrix

Commit the project source, then configure and build it before recording a
simulator run. CMake embeds the revision at configuration time; a commit made
after building does not update the executable. Start with the 32×32 smoke
case, then choose a per-process timeout with enough margin for the observed run:

```sh
python3 tools/run_matrix.py \
  --binary "$TT_METAL_HOME/build_Release/row-reduce-lab/tt_row_reduce_metal" \
  --case smoke --timeout 300 --output-dir results/rerun-smoke
python3 tools/run_matrix.py \
  --binary "$TT_METAL_HOME/build_Release/row-reduce-lab/tt_row_reduce_metal" \
  --case all --timeout 120 --output-dir results/rerun-matrix
```

The retained smoke took about 2.22 seconds, and the full matrix used a
120-second timeout. These observations include software simulation overhead;
a different host or a cold cache may need a longer timeout.

`--case all` runs 13 processes serially: 12 shapes and a separate 33×33 case
that reuses the mesh, tensors and workload for two different inputs. Each
output directory contains `environment.json`, `simulator.jsonl`, `summary.json`
and raw stdout/stderr logs. The checker regenerates input bytes, decodes every
output tile and recomputes the fixed error budget. A partial run cannot be
labelled a complete matrix.

The linked host can also check invalid input without starting the simulator:

```sh
python3 tools/run_rejections.py \
  --binary "$TT_METAL_HOME/build_Release/row-reduce-lab/tt_row_reduce_metal" \
  --output-dir results/c1-rejections-rerun --timeout 15
```

This saves the input files, raw logs and structured rejection results. An exit
code alone does not pass: each rejection must identify the pre-device path.

## Profiler capture

```sh
bash tools/profile.sh \
  "$TT_METAL_HOME/build_Release/row-reduce-lab/tt_row_reduce_metal" \
  results/rerun-profile 120
```

The script requires a newly generated CSV and complete reader, compute and
writer intervals. The [retained attempt](../results/profile/README.md) produced
only a header and fails this check. Profiler counters from ttsim describe
simulated execution; they do not establish silicon latency or bandwidth.

## Common setup errors

- **Existing result files:** choose another output directory. The runners
  refuse to replace earlier records.
- **Missing or mismatched simulator:** run `fetch_simulator.py` on the target
  Linux host and use the architecture-specific library from `upstream.lock`.
- **Missing `TT_METAL_CACHE`:** set it to an absolute directory dedicated to
  this checkout, or source the ARM64 activation script.
- **C++20 headers not found:** check the selected GCC headers as well as the
  Clang executable. The [compiler notes](environment.md#compiler-selection)
  cover the user-prefix configuration.
- **Heavy-build lock occupied:** wait for the other build to finish and retry.
  The wrapper prints the recorded owner and does not start the command.
