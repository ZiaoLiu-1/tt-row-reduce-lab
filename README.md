# TT Row Reduce Lab

A C++20 program that sums each row of a BF16 matrix using TT-Metalium. A reader,
compute kernel and writer move tiles through one Wormhole Tensix node; the host
checks the output against a CPU reference computed from the uploaded BF16 values.

The program handles matrices from 1×1 to 128×128, including dimensions that do
not fill a tile. It has been tested in the official Tenstorrent simulator across
12 shapes and a repeated run with different input. No hardware performance
measurements are available.

## Try the CPU reference

The reference, layout checks and tests run without TT-Metal. They require a
C++20 compiler, CMake 3.24 or later, and Python 3.10 or later for the tool tests.

```sh
cmake -S . -B build_cpu -DROW_REDUCE_SANITIZERS=ON
cmake --build build_cpu --parallel 1
ctest --test-dir build_cpu --output-on-failure
./build_cpu/row_reduce_cpu --rows 33 --cols 33 --pattern ones --seed 109
```

For this input, each of the 33 row sums is `33`. The CLI emits a JSON record
labelled `C0/cpu_reference`; each `comparison.rows` entry contains the rounded
reference as `actual`, used to exercise the comparator. To check quantization and the error budget, try
`--pattern decimals` in the same command. Device readback comes from the
separate Metal executable.

## How the reduction works

The host quantizes the input to BF16, pads the matrix with zeros and arranges it
into 32×32 tiles. A 33×33 matrix occupies four input tiles. The reader loads
those tiles through the NoC; the compute kernel sums across their width in
FP32 and packs one BF16 output tile per tile-row. The writer stores the result
in DRAM for the host to read back.

Row sums sit in column zero of each output tile. The host checks those sums and
every unused output position, so an incorrect layout or missing write cannot
pass just because the visible vector looks right. The repeat case keeps the
same device objects alive, changes the input, and checks the next result.

See the [design](docs/design.md) for the tile layout, buffer ordering and numerical
budget, or follow a [33×33 example in Chinese](docs/explain.md).

## Run on the simulator

The Metal host requires Linux and the versions pinned in [upstream.lock](upstream.lock).
The tested environment is Ubuntu 22.04 ARM64 with Clang 20 and GCC 12 C++20
headers/runtime. Follow the [environment setup](docs/environment.md), then the
[build and run instructions](docs/running.md). With that environment active:

```sh
python3 tools/run_matrix.py \
  --binary "$TT_METAL_HOME/build_Release/row-reduce-lab/tt_row_reduce_metal" \
  --case smoke --timeout 300 --output-dir results/rerun-smoke
```

The runner saves raw output, numerical checks and source/binary hashes. Use a
fresh output directory for each run. `--case all` runs the full matrix;
`--list` prints the cases without launching anything.

## Results and limits

The [saved simulator run](results/summary.json) contains 14 readbacks covering
521 logical rows. These results belong to source commit
`81efb4fff0f7f2de48819529b83427f31b2714da`; later tool and documentation changes
keep that identity intact. [Validation notes](docs/validation.md) describe the
checks and show how to recheck the saved files locally.

The input range is deliberately small: after BF16 quantization, each value must
be zero or have magnitude between `2^-8` and `1`. The implementation uses one
node. It does not support multi-node reduction or other operators. A profiler
attempt produced correct output but no event rows, so no kernel timings are
reported. Simulator wall time is not a measurement of card performance.

The host setup and kernels adapt Tenstorrent's pinned reduction examples.
[Source notes](docs/source-notes.md) identify those adaptations and project
additions. Code is distributed under [Apache-2.0](LICENSE); third-party
attribution is retained in [NOTICE](NOTICE) and the derived files.
