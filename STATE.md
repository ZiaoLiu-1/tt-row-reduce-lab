# Project state

Updated: 2026-09-09 UTC (2026-09-08 Toronto).

Goal is active. The goal tool created this task's unbudgeted goal on 2026-09-09: implement, verify custom-kernel ttsim C3 execution, document and deliver a private `ZiaoLiu-1/tt-row-reduce-lab` repository by 2026-09-09 evening Toronto.

Current validation: C0 CPU contract verified; C1 host link, C2 device JIT and C3 simulator execution pending. This directory initially contained only AGENTS.md. The root coordination task owns workspace status, evidence and learning records. No hardware is available; no silicon performance claim is possible.

Execution: pin Metal `89e1256c982a5b4739d173bcc446c8c748a44b40` and ttsim `v1.10.6`; first complete single-node BF16 row reduction and the 12-shape matrix. Remote environment preflight and narrow toolchain bootstrap are the immediate work. The task is the sole full-Metal environment coordinator and must use the shared heavy-build lock. Private routing stays outside Git.

Authorship: project implementation is agent-assisted. Ziao's independent reconstruction and oral understanding are not yet assessed.

## Environment checkpoint (2026-09-09 UTC)

Pinned source and all three recursive submodules were obtained; checkout footprint was 881 MiB. The isolated Linux ARM64 Ubuntu 22.04 environment now has Clang 20.1.8, GCC 12 C++20 headers and libstdc++ runtime, CMake 4.0.2, Ninja 1.11.1 and a project venv. A real std::span/std::bit_cast host probe compiled, linked and ran; loader identity was checked. The initial missing TBB auxiliary package and missing Python ensurepip were resolved within the project prefix/venv, without sudo or system modification.

Official configure completed with Python bindings and multihost distributed compute disabled. It still fetched tool-only dependencies (including Emscripten) during configuration; this was observed rather than assumed away. About 5.1 GiB remained when the narrow `metal_example_add_2_integers_in_riscv` target began (873 build steps). Build runs at parallelism 1 under the shared lock. This checkpoint is only environment preparation, not C1/C2/C3 proof of this project's kernel.

Actual-build correction: inspecting CMake's detected implicit includes revealed that upstream resolves the compiler's real path, bypassing the temporary wrapper. The standalone toolchain probe used GCC 12 correctly, but the first configure had detected GCC 11 headers. That initial build was deliberately interrupted and retained in its own log. Reconfigure now supplies `--gcc-toolchain=<prefix>/usr` explicitly in C/C++ flags and refreshes CMake compiler detection. Parallelism increased from 1 to 2 only after observing >20 GiB available RAM and individual compile RSS under 300 MB; two CPUs remain for existing workloads.

## C0 result

Apple Clang 17 compiled the CPU targets with C++20 strict warnings and ASan/UBSan. All 12 test groups passed (84 shape/pattern combinations, 255025 assertions); 12 CPU matrix records and 10 expected CLI rejections passed validation. Durable raw evidence is in `results/cpu/`. CPU matrix `actual` values are deliberately rounded oracle outputs used to exercise the comparator; they are not device outputs and contribute no C3 cases. Root coordination independently checked RNE, face coordinates, column-zero counterexamples and source hashes without a blocking finding.
