# Project evidence ledger

Updated during implementation, 2026-09-09 UTC / 2026-09-08 Toronto.

The project is agent-assisted. Ziao's independent implementation, reconstruction
and oral understanding have not been assessed. Upstream LLK algorithms, Metal
runtime, ttsim and reference kernels remain Tenstorrent's work; adaptations and
project additions are identified in [source-notes.md](source-notes.md). This
file does not update the workspace's canonical resume evidence or mastery.

## Verified scope at this handoff

- The CPU contract has a real Apple Clang 17 C++20 strict-warning
  ASan/UBSan run: 12 test groups, 84 shape/pattern cases and 255,025 assertions.
  Twelve CPU CLI matrix records and ten invalid CLI rejections were also run.
  Durable raw logs, compiler details and summary reside in
  [results/cpu/](../results/cpu/). These establish C0 only.
- The Python evidence-tool tests were actually run with
  `python3 -m unittest discover -s tests -p 'test_tools.py' -v`.
  They use explicit temporary fixtures, including process failures and invalid
  numerical/layout data. Fixture output is not simulator evidence.
- C1 host link passed; the real binary rejected all 14 invalid-input cases
  before device creation (`results/c1-rejections/`). Remote CTest passed 3/3.
  Official simulator smoke passed, as recorded in `results/build/`.
- Custom C2 device JIT and C3 full custom-kernel ttsim execution passed: 12
  required shapes plus same-process different-input reuse, 13 processes and
  14 readbacks covering 521 logical output rows. Raw BF16 values, numerical
  checks, padding and source/binary identity are retained in
  [results/summary.json](../results/summary.json) and its adjacent raw logs.
- The profiler-enabled smoke was numerically correct but returned a header-only
  CSV. Profiling capture is not verified. No Tenstorrent silicon is available;
  C4 is outside this delivery.

The exact state, tested source commit, private repository URL and durable
result paths are recorded in [STATE.md](../STATE.md) after actual execution.
No result count in this ledger should be promoted without its matching raw log
and source identity. Failed attempts remain evidence of a limitation, not a pass.

## Reproduction and source identity

The CPU tests can be reproduced with the direct compiler command in the README;
the CMake path is also supplied there but must be distinguished from the command
actually used. The simulator matrix command is:

```sh
python3 tools/run_matrix.py \
  --binary "$TT_METAL_HOME/build_Release/row-reduce-lab/tt_row_reduce_metal" \
  --case all --timeout 120 --output-dir results/rerun-matrix
```

The runner binds source commit plus file digest manifest, executable SHA-256,
Metal commit, simulator version/asset/SHA, descriptor SHA, shape, pattern, seed,
input/output hashes, raw logs and exit codes. It checks full output tiles,
numerical thresholds and changed-input reuse, and refuses existing evidence
paths. `summary.json` claims complete C3 only for all required shapes and the
two-execution repeat process. Environment setup/toolchain capture belongs in
`environment.json`; the runner also saves per-run identity under its result path.

## Conservative wording candidates

**Now supported at project level, subject to personal-ownership review:** “Built a small TT-Metalium learning
project for row-wise reduction, using C++ host orchestration and
reader/compute/writer kernels; checked results in the official Tenstorrent
simulator against a quantized CPU reference across 12 shapes and a repeated
execution case.” Personal ownership and ability to explain the selected
code points require a separate review.

Neither wording supports independent authorship of all low-level code,
production optimization, professional kernel expertise, multi-node execution,
real-device performance, upstream acceptance or automatic personal mastery.
