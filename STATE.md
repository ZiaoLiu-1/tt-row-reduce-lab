# Project state

Updated: 2026-09-09 UTC (2026-09-08 Toronto).

Goal is active. The goal tool created this task's unbudgeted goal on 2026-09-09: implement, verify custom-kernel ttsim C3 execution, document and deliver a private `ZiaoLiu-1/tt-row-reduce-lab` repository by 2026-09-09 evening Toronto.

Current validation: C0 CPU contract, C1 real host link, custom C2 device JIT and C3 full simulator matrix verified. Profiling produced a header-only CSV, so no successful profiling claim is made. This directory initially contained only AGENTS.md. The root coordination task owns workspace status, evidence and learning records. No hardware is available; no silicon performance claim is possible.

Execution: pin Metal `89e1256c982a5b4739d173bcc446c8c748a44b40` and ttsim `v1.10.6`; single-node BF16 row reduction and the 12-shape matrix are complete. The shared heavy-build lock has been released after execution. Final work is evidence review and private delivery. Private routing stays outside Git.

Authorship: project implementation is agent-assisted. Ziao's independent reconstruction and oral understanding are not yet assessed.

## Environment checkpoint (2026-09-09 UTC)

Pinned source and all three recursive submodules were obtained; checkout footprint was 881 MiB. The isolated Linux ARM64 Ubuntu 22.04 environment now has Clang 20.1.8, GCC 12 C++20 headers and libstdc++ runtime, CMake 4.0.2, Ninja 1.11.1 and a project venv. A real std::span/std::bit_cast host probe compiled, linked and ran; loader identity was checked. The initial missing TBB auxiliary package and missing Python ensurepip were resolved within the project prefix/venv, without sudo or system modification.

Official configure completed with Python bindings and multihost distributed compute disabled. It still fetched tool-only dependencies (including Emscripten) during configuration; this was observed rather than assumed away. About 5.1 GiB remained when the narrow `metal_example_add_2_integers_in_riscv` target began (873 build steps). Build runs at parallelism 1 under the shared lock. This checkpoint is only environment preparation, not C1/C2/C3 proof of this project's kernel.

Actual-build correction: inspecting CMake's detected implicit includes revealed that upstream resolves the compiler's real path, bypassing the temporary wrapper. The standalone toolchain probe used GCC 12 correctly, but the first configure had detected GCC 11 headers. That initial build was deliberately interrupted and retained in its own log. Reconfigure now supplies `--gcc-toolchain=<prefix>/usr` explicitly in C/C++ flags and refreshes CMake compiler detection. Parallelism increased from 1 to 2 only after observing >20 GiB available RAM and individual compile RSS under 300 MB; two CPUs remain for existing workloads.

## C0 result

Apple Clang 17 compiled the CPU targets with C++20 strict warnings and ASan/UBSan. All 12 test groups passed (84 shape/pattern combinations, 255025 assertions); 12 CPU matrix records and 10 expected CLI rejections passed validation. Durable raw evidence is in `results/cpu/`. CPU matrix `actual` values are deliberately rounded oracle outputs used to exercise the comparator; they are not device outputs and contribute no C3 cases. Root coordination independently checked RNE, face coordinates, column-zero counterexamples and source hashes without a blocking finding.

## Private repository checkpoint

Created and read back `https://github.com/ZiaoLiu-1/tt-row-reduce-lab` with visibility `PRIVATE`, then pushed the initial source/C0 snapshot `aee2dea`. C1/C2/C3 were pending at that checkpoint; repository availability is not execution evidence. The Metal build embeds its configure-time project commit; the runner also records actual source files and binary SHA-256.

Build dependency correction: bundled UMD compilation stopped at `hwloc/autogen/config.h` because Ubuntu's architecture-specific headers reside in the prefix's `usr/include/aarch64-linux-gnu`. Added that explicit system include directory to both compiler flag sets and regenerated/rebuilt the same narrow target. The prior failing log is preserved. No upstream source or system installation was changed to work around the error.

A follow-up reconfigure exposed a CMake toolchain cache issue: the pinned ARM64 toolchain repeatedly assigns bare compiler names with CACHE INTERNAL, causing CMake 4.0.2 to discard prior cache settings in this user-prefix setup (and re-enable MPI by default). Added a project-owned toolchain wrapper that includes the exact upstream file, then fixes both compiler paths to the extracted prefix. This preserves the upstream source and compiler semantics while stabilizing reconfigure. The failed MPI configure is retained separately; MPI is still intentionally disabled.

## C1 and official simulator checkpoint

The real `tt_row_reduce_metal` host and official smoke target compiled and linked from project source `81efb4fff0f7f2de48819529b83427f31b2714da`; custom binary SHA-256 is `e9b87650d2a51fa1f694b1e09b5b0bb5d9a3837bc3e1da2e26773d8cbcb5d5b8`. Remote CTest passed 3/3. Fourteen actual Metal CLI invalid-input cases all returned exit 2 with `before_device=true`; raw evidence is `results/c1-rejections/`.

Official `add_2_integers_in_riscv` returned 21 and exit 0 on the hash-verified pinned ttsim asset. This proves the environment works, not that this project kernel runs. Its 2.37 s process wall time includes software/JIT overhead and is not a silicon metric. All nine setup/build/smoke logs, including failed attempts, are retained verbatim in `results/build/`; `environment.json` captures the actual toolchain and simulator identity. The initial priority heavy-build phase ended and Runtime was notified to use the next shared-lock opportunity.

During the build, the same remote filesystem changed from 45 GiB total to 146 GiB total, with about 106 GiB free. This task did not perform the resize. GitHub Actions has been explicitly disabled and read back as `enabled=false`; Actions run count was 0. Validation is local/authorized remote execution only.

## Custom C3 result (2026-09-09 02:17 UTC)

All 12 required shapes and the 33×33 same-process, different-input repeat passed: 13 serial processes, 14 readbacks, 521 logical output rows. The saved matrix summary declares `matrix_complete=true`, `validation_stage=C3` and `sources_and_binary_unchanged=true`. Numerical tolerances were not widened. All output-column and padded-row checks passed. `results/summary.json`, `results/simulator.jsonl`, `results/environment.json` and `results/raw/` retain the complete result and raw logs.

The compiled source and executed checkout were both `81efb4fff0f7f2de48819529b83427f31b2714da`; host binary SHA-256 is `e9b87650d2a51fa1f694b1e09b5b0bb5d9a3837bc3e1da2e26773d8cbcb5d5b8`. `source_dirty=true` reflects captured evidence files; all 27 source-manifest files match that retained Git commit. Later tooling/documentation commits do not rewrite this identity. The retained host binary and actual JIT ELF bytes were copied back and matched against the recorded hashes.

The initial custom smoke took 2.2245 s process wall time; the complete matrix used a 120 s per-process timeout and totaled 14.4750 s. These are software execution observations, not silicon performance. The JIT cache contained 61 ELF artifacts after the matrix and 84 after the profiler attempt, including firmware, the official smoke and XIP forms—not counts of unique project kernels.

An independent standard-library Python audit used exact rational arithmetic to recompute BF16 row sums, error budgets, face decoding and padding. It passed all 521 rows, all 26 raw-log hashes, all 27 source hashes and the retained host binary SHA. It audits saved artifacts and does not constitute a second independent execution or rebuild.

The profiler-enabled custom smoke returned correct numerical output. Its actual CSV had only metadata and the pinned 15-column header, with no event rows. The original checker rejected an outdated header expectation; that failure is retained. The parser now matches the observed pinned writer, including `ZONE_START`/`ZONE_END` and optional trace identifiers; all 16 evidence-tool regression tests passed. Revalidation of the same original CSV correctly fails with `profiler CSV has no event rows` in `results/profile/profiler-revalidation.json`. No reader/compute/writer interval or silicon metric is claimed.
