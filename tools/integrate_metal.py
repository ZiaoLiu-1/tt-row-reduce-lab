#!/usr/bin/env python3
"""Append a small, idempotent project target to an otherwise clean pinned checkout."""
import argparse
import pathlib
import subprocess

PIN = '89e1256c982a5b4739d173bcc446c8c748a44b40'
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('metal', type=pathlib.Path)
p.add_argument('--project', type=pathlib.Path, default=pathlib.Path(__file__).resolve().parents[1])
a = p.parse_args()
metal, project = a.metal.resolve(), a.project.resolve()
if subprocess.check_output(['git', '-C', str(metal), 'rev-parse', 'HEAD'], text=True).strip() != PIN:
    p.error('TT-Metal HEAD must equal upstream.lock; refusing an unpinned integration')
cmake = metal / 'CMakeLists.txt'
original = subprocess.check_output(['git', '-C', str(metal), 'show', 'HEAD:CMakeLists.txt'], text=True)
marker = '# TT_ROW_REDUCE_LAB integration (tools/integrate_metal.py)'
current = cmake.read_text()
# Bracket quoting accepts spaces without shell evaluation. Refuse closing delimiters.
if ']==]' in str(project):
    p.error('unsupported path characters')
append = f'''\n{marker}
set(ROW_REDUCE_BUILD_METAL ON CACHE BOOL "Build pinned Metal host" FORCE)
add_subdirectory([==[{project}]==] ${{CMAKE_BINARY_DIR}}/row-reduce-lab)
'''
if current not in (original, original + append):
    p.error('upstream CMakeLists has unrelated or differently configured edits; refusing to replace them')
cmake.write_text(original + append)
print(f'Integrated tt_row_reduce_metal into pinned TT-Metal checkout ({PIN}).')
