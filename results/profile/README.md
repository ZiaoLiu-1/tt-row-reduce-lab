# Profiler attempt: numerical pass, no event capture

The real profiler-enabled 32×32 custom kernel returned a correct numerical
readback, recorded in `simulator.jsonl` and `raw/`. `profile_log_device.csv`
is the original output: architecture metadata and a 15-column header, with
zero event rows. No measured project-zone intervals are available.

`profiler-validation.json` preserves the first failed check. That checker
expected outdated field names. The corrected checker follows the exact pinned
writer in `tt_metal/impl/profiler/profiler.cpp`; it recognizes the actual header
and marker names. `profiler-revalidation.json` preserves its expected failure:
`profiler CSV has no event rows`. This is a recheck of the original CSV, not a
new device run or a successful profiling result.

To reproduce the recheck without overwriting evidence:

```sh
python3 tools/check_profiler.py results/profile/profile_log_device.csv
```

Exit status 1 is expected for this retained file. Parser regression tests use
synthetic complete intervals and the exact observed header; those fixtures do
not supply missing simulator events. The hooks remain available for another
supported environment. No claim is made that all ttsim versions lack profiling.
