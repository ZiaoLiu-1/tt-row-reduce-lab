# Retained JIT identity

`manifest.json` records the 61 actual ELF files present after the successful
complete custom-kernel matrix. `manifest-after-profile.json` records the 84
files present after the profiler-enabled smoke. Every retained file under
`elfs/` was copied from the isolated cache and checked against these SHA-256
and byte-length records.

These totals include runtime firmware, the earlier official smoke, custom
kernel specializations, and XIP forms. They are not counts of distinct project
kernels. The relative paths preserve the actual cache identities. The manifest
and ELF files establish compiled artifact identity; C3 correctness is established
separately by the adjacent matrix and raw readback records.

The compiler cache is specific to the pinned Metal/toolchain configuration.
These artifacts are retained for review, not installed automatically by any
reproduction command. Upstream licensing and adaptations are described in
the repository's `NOTICE`, `LICENSE` and `docs/source-notes.md`.
