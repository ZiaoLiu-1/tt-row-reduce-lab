#!/usr/bin/env python3
"""Record compiler, dependency and simulator identities without dumping the environment."""

import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
import subprocess


def command(argv: list[str]) -> dict:
    try:
        result = subprocess.run(argv, text=True, input="", capture_output=True, timeout=30)
        return {"exit_code": result.returncode, "output": (result.stdout + result.stderr).strip()}
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"available": False, "error_type": type(error).__name__}


def file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if not os.environ.get("TT_METAL_HOME"):
        parser.error("TT_METAL_HOME is required to identify the actual upstream checkout")

    metal = Path(os.environ["TT_METAL_HOME"])
    output_parent = args.output.resolve().parent
    output_parent.mkdir(parents=True, exist_ok=True)
    compiler_flags = shlex.split(os.environ.get("CXXFLAGS", ""))
    simulator = Path(os.environ.get("TT_METAL_SIMULATOR", "/nonexistent"))
    disk = shutil.disk_usage(output_parent)
    info = {
        "captured_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "os": platform.system(),
        "architecture": platform.machine(),
        "kernel_release": platform.release(),
        "logical_cpus": os.cpu_count(),
        "hardware_label": "no Tenstorrent silicon; Wormhole official software simulator",
        "silicon_performance_measured": False,
        "disk_free_bytes_at_capture": disk.free,
        "clang": command(["clang++-20", "--version"]),
        "cxx_include_search": command([
            "clang++-20", *compiler_flags, "-std=c++20", "-E", "-x", "c++", "-v", "-",
        ]),
        "libstdcpp_link_path": command([
            "clang++-20", *compiler_flags, "--print-file-name=libstdc++.so",
        ]),
        "cmake": command(["cmake", "--version"]),
        "ninja": command(["ninja", "--version"]),
        "python": platform.python_version(),
        "metal_commit": command(["git", "-C", str(metal), "rev-parse", "HEAD"]),
        "metal_submodules": command(["git", "-C", str(metal), "submodule", "status", "--recursive"]),
        "simulator_asset": simulator.name,
        "simulator_sha256": file_sha(simulator) if simulator.is_file() else None,
        "execution_switches": {
            key: os.environ.get(key)
            for key in ("TT_METAL_SLOW_DISPATCH_MODE", "TT_METAL_DISABLE_SFPLOADMACRO")
        },
    }
    os_release = Path("/etc/os-release")
    if os_release.exists():
        info["distribution"] = os_release.read_text()
    meminfo = Path("/proc/meminfo")
    if meminfo.exists():
        info["memory"] = "\n".join(
            line for line in meminfo.read_text().splitlines()
            if line.startswith(("MemTotal:", "MemAvailable:", "SwapTotal:"))
        )
    args.output.write_text(json.dumps(info, indent=2) + "\n")
    print(f"Captured {args.output.name}")


if __name__ == "__main__":
    main()
