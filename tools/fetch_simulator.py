#!/usr/bin/env python3
"""Fetch the pinned official ttsim asset and verify SHA-256 before use."""
import argparse
import hashlib
import json
import pathlib
import platform
import shutil
import urllib.request
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--metal', required=True, type=pathlib.Path)
p.add_argument('--output', required=True, type=pathlib.Path)
a = p.parse_args()
project = pathlib.Path(__file__).resolve().parents[1]
lock = json.loads((project / 'upstream.lock').read_text())['ttsim']
arch = platform.machine()
key = {'aarch64': 'aarch64', 'x86_64': 'x86_64'}.get(arch)
if key is None: p.error('official Linux aarch64/x86_64 asset required')
if platform.system() != 'Linux': p.error('run this on the target Linux environment')
a.output.mkdir(parents=True, exist_ok=True)
asset = lock[key + '_asset']; target = a.output / asset
if not target.exists():
    part = target.with_suffix('.part')
    urllib.request.urlretrieve(f"https://github.com/tenstorrent/ttsim/releases/download/{lock['version']}/{asset}", part)
    if hashlib.sha256(part.read_bytes()).hexdigest() != lock[key + '_sha256']:
        raise SystemExit('download SHA-256 mismatch')
    part.rename(target)
if hashlib.sha256(target.read_bytes()).hexdigest() != lock[key + '_sha256']:
    raise SystemExit('existing asset SHA-256 mismatch')
shutil.copyfile(a.metal / 'tt_metal/soc_descriptors/wormhole_b0_80_arch.yaml', a.output / 'soc_descriptor.yaml')
print(json.dumps({'asset': asset, 'sha256': lock[key + '_sha256'], 'version': lock['version'], 'architecture': arch}))
