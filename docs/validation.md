# Validation

The saved results were collected on 2026-09-09 UTC using the versions in
[upstream.lock](../upstream.lock). The Metal host and kernels were compiled from
commit `81efb4fff0f7f2de48819529b83427f31b2714da`. Their retained host binary has
SHA-256 `e9b87650d2a51fa1f694b1e09b5b0bb5d9a3837bc3e1da2e26773d8cbcb5d5b8`.
Later changes to tooling and documentation do not change those recorded inputs.

## What was checked

The [CPU tests](../results/cpu/) ran with Apple Clang 17, strict C++20 warnings
and ASan/UBSan. They cover quantization, face boundaries, layout round-trips,
the numerical comparator and invalid inputs across the supported shapes and
patterns. The CPU CLI's `actual` values are rounded reference values used to
exercise the comparator; they are not device output.

The Linux ARM64 Metal target compiled and linked. Its
[input-rejection run](../results/c1-rejections/summary.json) rejected all 14
invalid inputs before device creation. The [build logs](../results/build/)
retain both the successful build and earlier dependency/configuration failures.

The [simulator matrix](../results/summary.json) passed all 12 shapes plus a
same-process repeat with different input: 13 processes, 14 readbacks and 521
logical rows. Every readback was compared against the quantized reference;
unused output columns and padded rows were checked for zero. Raw BF16 output,
input hashes, source identities and logs are saved beside the summary.

The result schema calls these stages `C0` (CPU checks), `C1` (host build),
`C2` (device JIT) and `C3` (full simulator execution). Hardware execution would
be `C4`; it has not been performed. Python test fixtures exercise the recorders
and checkers and are not simulator runs.

## Recheck the saved run

From the repository root:

```sh
python3 results/audit/verify_c3.py results --source-root "$PWD" \
  --binary results/binaries/tt_row_reduce_metal.aarch64.elf
```

This checker uses exact rational BF16 arithmetic and a separate face-layout
decoder. It verifies the saved rows, logs, source bytes and binary hash without
running the executable. Historical source bytes are resolved from Git when
they differ from the working tree. Its [saved report](../results/audit/independent-c3.json)
states the remaining limits, including the lack of an independent rebuild and
the absence of raw input DRAM upload bytes.

The [JIT manifests](../results/jit/README.md) identify the retained compiled
ELFs, including firmware and the official smoke example. Their file counts
are not counts of distinct project kernels. The host ELF depends on the
recorded Linux ARM64 runtime libraries and is not a portable release binary.

## Timing and profiler limits

The profiler-enabled smoke returned correct output, but the CSV contains no
event rows. The [original attempt and recheck](../results/profile/README.md)
are retained; no reader/compute/writer timings were obtained.

`wall_seconds` includes process startup, JIT/cache effects, simulation and
teardown. `execute_wall_seconds` covers enqueue through `Finish` and excludes
transfers. Both describe software simulation in these records. They do not
measure hardware latency, bandwidth or speedup.
