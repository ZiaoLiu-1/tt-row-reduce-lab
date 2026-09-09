#!/usr/bin/env python3
"""Record only non-secret build and execution identity (never dump the environment)."""
import argparse
import datetime
import hashlib
import json
import os
import pathlib
import platform
import shutil
import shlex
import subprocess
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--output', required=True, type=pathlib.Path)
a = p.parse_args()
def command(argv):
    try:
        r = subprocess.run(argv, text=True, input='', capture_output=True, timeout=30)
        return {'exit_code': r.returncode, 'output': (r.stdout + r.stderr).strip()}
    except (OSError, subprocess.TimeoutExpired) as e:
        return {'available': False, 'error_type': type(e).__name__}
def file_sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''): h.update(block)
    return h.hexdigest()
if not os.environ.get('TT_METAL_HOME'):
    p.error('TT_METAL_HOME is required to identify the actual upstream checkout')
metal = pathlib.Path(os.environ['TT_METAL_HOME'])
a.output.resolve().parent.mkdir(parents=True, exist_ok=True)
compiler_flags = shlex.split(os.environ.get('CXXFLAGS', ''))
simulator = pathlib.Path(os.environ.get('TT_METAL_SIMULATOR', '/nonexistent'))
disk = shutil.disk_usage(a.output.resolve().parent)
info = {
    'captured_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'os': platform.system(), 'architecture': platform.machine(),
    'kernel_release': platform.release(), 'logical_cpus': os.cpu_count(),
    'hardware_label': 'no Tenstorrent silicon; Wormhole official software simulator',
    'silicon_performance_measured': False,
    'disk_free_bytes_at_capture': disk.free,
    'clang': command(['clang++-20', '--version']),
    'cxx_include_search': command(['clang++-20', *compiler_flags, '-std=c++20', '-E', '-x', 'c++', '-v', '-']),
    'libstdcpp_link_path': command(['clang++-20', *compiler_flags, '--print-file-name=libstdc++.so']),
    'cmake': command(['cmake', '--version']), 'ninja': command(['ninja', '--version']),
    'python': platform.python_version(),
    'metal_commit': command(['git', '-C', str(metal), 'rev-parse', 'HEAD']),
    'metal_submodules': command(['git', '-C', str(metal), 'submodule', 'status', '--recursive']),
    'simulator_asset': simulator.name,
    'simulator_sha256': file_sha(simulator) if simulator.is_file() else None,
    'execution_switches': {key: os.environ.get(key) for key in ('TT_METAL_SLOW_DISPATCH_MODE', 'TT_METAL_DISABLE_SFPLOADMACRO')},
}
if pathlib.Path('/etc/os-release').exists():
    info['distribution'] = pathlib.Path('/etc/os-release').read_text()
if pathlib.Path('/proc/meminfo').exists():
    info['memory'] = '\n'.join(line for line in pathlib.Path('/proc/meminfo').read_text().splitlines() if line.startswith(('MemTotal:', 'MemAvailable:', 'SwapTotal:')))
a.output.write_text(json.dumps(info, indent=2) + '\n')
print(f'Captured {a.output.name}')
