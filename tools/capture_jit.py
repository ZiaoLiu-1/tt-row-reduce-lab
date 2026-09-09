#!/usr/bin/env python3
"""Hash real JIT ELF artifacts; this manifest alone proves no kernel correctness."""
import argparse
import datetime
import hashlib
import json
import pathlib
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--cache', required=True, type=pathlib.Path)
p.add_argument('--output', required=True, type=pathlib.Path)
a = p.parse_args()
if not a.cache.is_dir(): p.error('JIT cache directory does not exist')
artifacts = []
for path in sorted(a.cache.rglob('*.elf')):
    artifacts.append({'path_relative_to_cache': str(path.relative_to(a.cache)),
                      'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                      'size_bytes': path.stat().st_size})
if not artifacts: p.error('no real JIT ELF files found')
a.output.parent.mkdir(parents=True, exist_ok=True)
a.output.write_text(json.dumps({
    'captured_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'meaning': 'Actual compiled ELF identity only; inspect simulator results separately for correctness.',
    'artifacts': artifacts,
}, indent=2) + '\n')
print(f'Recorded {len(artifacts)} real ELF hashes.')
