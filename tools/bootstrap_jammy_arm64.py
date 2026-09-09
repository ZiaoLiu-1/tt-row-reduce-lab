#!/usr/bin/env python3
import argparse, gzip, hashlib, pathlib, platform, shutil, subprocess, urllib.request
parser = argparse.ArgumentParser(description='Unprivileged Clang20/GCC12 prefix for Ubuntu 22.04 ARM64; invoke under with-heavy-lock.sh')
parser.add_argument('--root', required=True, type=pathlib.Path)
root = parser.parse_args().root.resolve()
if platform.system() != 'Linux' or platform.machine() != 'aarch64':
    raise SystemExit('This bootstrap is for Linux aarch64 only')
if 'VERSION_ID="22.04"' not in pathlib.Path('/etc/os-release').read_text():
    raise SystemExit('This bootstrap is pinned to Ubuntu 22.04')
root.mkdir(parents=True, exist_ok=True)
if shutil.disk_usage(root).free < 3_000_000_000:
    raise SystemExit('Need at least 3 GB before this dependency stage; source/build require additional space')
downloads=root/'downloads'; prefix=root/'prefix'
downloads.mkdir(exist_ok=True); prefix.mkdir(exist_ok=True)
index=urllib.request.urlopen('https://apt.llvm.org/jammy/dists/llvm-toolchain-jammy-20/main/binary-arm64/Packages.gz').read()
(downloads/'llvm-Packages.gz').write_bytes(index)
selected={'clang-20','libclang-cpp20','libclang1-20','libclang-common-20-dev','libllvm20','llvm-20-linker-tools','lld-20','llvm-20','llvm-20-runtime'}
packages=[]
for stanza in gzip.decompress(index).decode().split('\n\n'):
 d=dict(x.split(': ',1) for x in stanza.splitlines() if ': ' in x and not x.startswith(' '))
 if d.get('Package') in selected: packages.append(d)
print('LLVM download bytes',sum(int(d['Size']) for d in packages), 'unpacked KiB',sum(int(d['Installed-Size']) for d in packages),flush=True)
for d in packages:
 file=downloads/pathlib.Path(d['Filename']).name
 if not file.exists():
  part=file.with_suffix('.part')
  urllib.request.urlretrieve('https://apt.llvm.org/jammy/'+d['Filename'],part)
  if hashlib.sha256(part.read_bytes()).hexdigest()!=d['SHA256']:
   raise SystemExit('LLVM archive SHA-256 mismatch: '+d['Package'])
  part.replace(file)
 if hashlib.sha256(file.read_bytes()).hexdigest()!=d['SHA256']:
  raise SystemExit('Existing LLVM archive SHA-256 mismatch: '+d['Package'])
 print('unpack',d['Package'],d['Version'],flush=True)
 subprocess.run(['dpkg-deb','-x',str(file),str(prefix)],check=True)
# apt download performs repository index checksum verification and is unprivileged.
ubuntu=['gcc-12','g++-12','cpp-12','libgcc-12-dev','libstdc++-12-dev','libstdc++6','libhwloc-dev','libhwloc15','libnuma-dev','libnuma1','libtbb-dev','libtbb12','libtbbmalloc2','libpfm4','libcapstone-dev','libcapstone4']
subprocess.run(['apt-get','download',*ubuntu],cwd=downloads,check=True)
for file in sorted(downloads.glob('*.deb')):
 if file.name.split('_')[0] in ubuntu:
  subprocess.run(['dpkg-deb','-x',str(file),str(prefix)],check=True)
subprocess.run(['python3','-m','venv','--without-pip',str(root/'venv')],check=True)
subprocess.run(['python3','-m','pip','--python',str(root/'venv/bin/python'),'install','pip','cmake==4.0.2','ninja==1.11.1.4','pyyaml','jinja2','loguru'],check=True)

wrappers = root/'wrappers'; wrappers.mkdir(exist_ok=True)
for tool in ('clang-20', 'clang++-20'):
    wrapper = wrappers/tool
    import shlex
    wrapper.write_text('#!/usr/bin/env bash\nexec ' + shlex.quote(str(prefix/'usr/bin'/tool)) + ' --gcc-toolchain=' + shlex.quote(str(prefix/'usr')) + ' "$@"\n')
    wrapper.chmod(0o755)
print('Prefix ready. Set RR_ENV_ROOT, then source tools/activate_linux_prefix.sh.')
