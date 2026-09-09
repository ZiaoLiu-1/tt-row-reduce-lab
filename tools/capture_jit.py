#!/usr/bin/env python3
"""Hash JIT ELF artifacts for comparison with a recorded simulator run."""

import argparse
import datetime
import hashlib
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if not args.cache.is_dir():
        parser.error("JIT cache directory does not exist")

    artifacts = []
    for path in sorted(args.cache.rglob("*.elf")):
        artifacts.append({
            "path_relative_to_cache": str(path.relative_to(args.cache)),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "size_bytes": path.stat().st_size,
        })
    if not artifacts:
        parser.error("no real JIT ELF files found")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({
        "captured_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "meaning": "Actual compiled ELF identity only; inspect simulator results separately for correctness.",
        "artifacts": artifacts,
    }, indent=2) + "\n")
    print(f"Recorded {len(artifacts)} real ELF hashes.")


if __name__ == "__main__":
    main()
