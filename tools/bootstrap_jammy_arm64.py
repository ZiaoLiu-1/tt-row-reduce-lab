#!/usr/bin/env python3
"""Install an unprivileged Clang 20/GCC 12 prefix for Ubuntu 22.04 ARM64.

Invoke under with-heavy-lock.sh when sharing the build machine.
"""

import argparse
import gzip
import hashlib
from pathlib import Path
import platform
import shlex
import shutil
import subprocess
import urllib.request

LLVM_REPOSITORY = "https://apt.llvm.org/jammy/"
LLVM_INDEX = "dists/llvm-toolchain-jammy-20/main/binary-arm64/Packages.gz"
LLVM_PACKAGES = {
    "clang-20",
    "libclang-cpp20",
    "libclang1-20",
    "libclang-common-20-dev",
    "libllvm20",
    "llvm-20-linker-tools",
    "lld-20",
    "llvm-20",
    "llvm-20-runtime",
}
UBUNTU_PACKAGES = [
    "gcc-12",
    "g++-12",
    "cpp-12",
    "libgcc-12-dev",
    "libstdc++-12-dev",
    "libstdc++6",
    "libhwloc-dev",
    "libhwloc15",
    "libnuma-dev",
    "libnuma1",
    "libtbb-dev",
    "libtbb12",
    "libtbbmalloc2",
    "libpfm4",
    "libcapstone-dev",
    "libcapstone4",
]


def install_llvm(downloads: Path, prefix: Path) -> None:
    index = urllib.request.urlopen(LLVM_REPOSITORY + LLVM_INDEX).read()
    (downloads / "llvm-Packages.gz").write_bytes(index)
    packages = []
    for stanza in gzip.decompress(index).decode().split("\n\n"):
        fields = dict(
            line.split(": ", 1)
            for line in stanza.splitlines()
            if ": " in line and not line.startswith(" ")
        )
        if fields.get("Package") in LLVM_PACKAGES:
            packages.append(fields)

    print(
        "LLVM download bytes", sum(int(package["Size"]) for package in packages),
        "unpacked KiB", sum(int(package["Installed-Size"]) for package in packages),
        flush=True,
    )
    for package in packages:
        archive = downloads / Path(package["Filename"]).name
        if not archive.exists():
            partial = archive.with_suffix(".part")
            urllib.request.urlretrieve(LLVM_REPOSITORY + package["Filename"], partial)
            if hashlib.sha256(partial.read_bytes()).hexdigest() != package["SHA256"]:
                raise SystemExit("LLVM archive SHA-256 mismatch: " + package["Package"])
            partial.replace(archive)
        if hashlib.sha256(archive.read_bytes()).hexdigest() != package["SHA256"]:
            raise SystemExit("Existing LLVM archive SHA-256 mismatch: " + package["Package"])
        print("unpack", package["Package"], package["Version"], flush=True)
        subprocess.run(["dpkg-deb", "-x", str(archive), str(prefix)], check=True)


def install_ubuntu_packages(downloads: Path, prefix: Path) -> None:
    # apt download verifies repository index checksums without requiring root.
    subprocess.run(["apt-get", "download", *UBUNTU_PACKAGES], cwd=downloads, check=True)
    for archive in sorted(downloads.glob("*.deb")):
        if archive.name.split("_")[0] in UBUNTU_PACKAGES:
            subprocess.run(["dpkg-deb", "-x", str(archive), str(prefix)], check=True)


def install_python_tools(root: Path) -> None:
    subprocess.run(["python3", "-m", "venv", "--without-pip", str(root / "venv")], check=True)
    subprocess.run([
        "python3", "-m", "pip", "--python", str(root / "venv/bin/python"),
        "--cache-dir", str(root / "downloads/pip-cache"), "install",
        "pip", "cmake==4.0.2", "ninja==1.11.1.4", "pyyaml", "jinja2", "loguru",
    ], check=True)


def write_compiler_wrappers(root: Path, prefix: Path) -> None:
    wrappers = root / "wrappers"
    wrappers.mkdir(exist_ok=True)
    for tool in ("clang-20", "clang++-20"):
        wrapper = wrappers / tool
        compiler = shlex.quote(str(prefix / "usr/bin" / tool))
        toolchain = shlex.quote(str(prefix / "usr"))
        wrapper.write_text(
            f'#!/usr/bin/env bash\nexec {compiler} --gcc-toolchain={toolchain} "$@"\n'
        )
        wrapper.chmod(0o755)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    root = parser.parse_args().root.resolve()

    if platform.system() != "Linux" or platform.machine() != "aarch64":
        raise SystemExit("This bootstrap is for Linux aarch64 only")
    if 'VERSION_ID="22.04"' not in Path("/etc/os-release").read_text():
        raise SystemExit("This bootstrap is pinned to Ubuntu 22.04")
    root.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(root).free < 3_000_000_000:
        raise SystemExit("Need at least 3 GB before this dependency stage; source/build require additional space")

    downloads = root / "downloads"
    prefix = root / "prefix"
    downloads.mkdir(exist_ok=True)
    prefix.mkdir(exist_ok=True)
    install_llvm(downloads, prefix)
    install_ubuntu_packages(downloads, prefix)
    install_python_tools(root)
    write_compiler_wrappers(root, prefix)
    print("Prefix ready. Set RR_ENV_ROOT, then source tools/activate_linux_prefix.sh.")


if __name__ == "__main__":
    main()
