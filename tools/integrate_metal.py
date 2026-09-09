#!/usr/bin/env python3
"""Append a small, idempotent project target to an otherwise clean pinned checkout."""

import argparse
from pathlib import Path
import subprocess

PIN = "89e1256c982a5b4739d173bcc446c8c748a44b40"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("metal", type=Path)
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    metal = args.metal.resolve()
    project = args.project.resolve()
    commit = subprocess.check_output(["git", "-C", str(metal), "rev-parse", "HEAD"], text=True).strip()
    if commit != PIN:
        parser.error("TT-Metal HEAD must equal upstream.lock; refusing an unpinned integration")

    cmake = metal / "CMakeLists.txt"
    original = subprocess.check_output(["git", "-C", str(metal), "show", "HEAD:CMakeLists.txt"], text=True)
    marker = "# TT_ROW_REDUCE_LAB integration (tools/integrate_metal.py)"
    current = cmake.read_text()
    # Bracket quoting accepts spaces without shell evaluation. Refuse closing delimiters.
    if "]==]" in str(project):
        parser.error("unsupported path characters")
    append = f'''\n{marker}
set(ROW_REDUCE_BUILD_METAL ON CACHE BOOL "Build pinned Metal host" FORCE)
add_subdirectory([==[{project}]==] ${{CMAKE_BINARY_DIR}}/row-reduce-lab)
'''
    if current not in (original, original + append):
        parser.error("upstream CMakeLists has unrelated or differently configured edits; refusing to replace them")
    cmake.write_text(original + append)
    print(f"Integrated tt_row_reduce_metal into pinned TT-Metal checkout ({PIN}).")


if __name__ == "__main__":
    main()
