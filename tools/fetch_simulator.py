#!/usr/bin/env python3
"""Fetch the pinned official ttsim asset and verify SHA-256 before use."""

import argparse
import hashlib
import json
from pathlib import Path
import platform
import shutil
import urllib.request


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metal", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    project = Path(__file__).resolve().parents[1]
    lock = json.loads((project / "upstream.lock").read_text())["ttsim"]
    architecture = platform.machine()
    key = {"aarch64": "aarch64", "x86_64": "x86_64"}.get(architecture)
    if key is None:
        parser.error("official Linux aarch64/x86_64 asset required")
    if platform.system() != "Linux":
        parser.error("run this on the target Linux environment")

    args.output.mkdir(parents=True, exist_ok=True)
    asset = lock[key + "_asset"]
    target = args.output / asset
    if not target.exists():
        partial = target.with_suffix(".part")
        url = f"https://github.com/tenstorrent/ttsim/releases/download/{lock['version']}/{asset}"
        urllib.request.urlretrieve(url, partial)
        if hashlib.sha256(partial.read_bytes()).hexdigest() != lock[key + "_sha256"]:
            raise SystemExit("download SHA-256 mismatch")
        partial.rename(target)
    if hashlib.sha256(target.read_bytes()).hexdigest() != lock[key + "_sha256"]:
        raise SystemExit("existing asset SHA-256 mismatch")

    shutil.copyfile(
        args.metal / "tt_metal/soc_descriptors/wormhole_b0_80_arch.yaml",
        args.output / "soc_descriptor.yaml",
    )
    print(json.dumps({
        "asset": asset,
        "sha256": lock[key + "_sha256"],
        "version": lock["version"],
        "architecture": architecture,
    }))


if __name__ == "__main__":
    main()
