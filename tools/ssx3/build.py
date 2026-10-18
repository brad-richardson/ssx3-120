#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = ["numpy==2.5.4", "Pillow==13.0.0"]
# ///
"""Local SSX 3 preparation. Never downloads or publishes game data.

Run directly (`python3 tools/ssx3/build.py ...`) or with only `uv`
installed (`./build ...`, which execs `uv run --locked --script` on this
file). The inline metadata above pins the exact interpreter range and
dependency versions; the committed `build.py.lock` next to this file
freezes their hashes per platform, so every host provisions identical
bytes. The lock is refreshed with
`uv lock --script tools/ssx3/build.py` whenever the pins change.
numpy/Pillow serve the remaster_font subprocess. Tricky audio needs
local ffmpeg + vgmstream (optional, probed, never fetched).
"""
import argparse
import gzip
import hashlib
import importlib.util
import json
import os
from contextlib import contextmanager
from pathlib import Path
import platform
import re
import shlex
import shutil
import struct
import subprocess
import sys
import tarfile
import time
import urllib.request
import zipfile

# Sibling imports (toolchain, prebuilt) resolve when this file runs as a
# script; they must also resolve when tests load it by path.
sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path(__file__).resolve().parents[2]
# Product/engine split (repo-split-2 step 2): the runtime, recompiler and
# vendor adapters live in the pinned engine checkout; product metadata,
# presets, builder and app shell live here. ENGINE defaults to the
# deps/PS2Recomp submodule and can be overridden with --engine (or
# SSX3_ENGINE_DIR) for engine-lane worktrees.
ENGINE = Path(os.environ.get('SSX3_ENGINE_DIR', str(ROOT / 'deps/PS2Recomp')))
# Generator source pin: the LANDED linux-host-1 commit (in fork/ssx3
# history, publicly resolvable). Its ps2xRecomp/ tree is byte-identical to
# the original lane commit 03c10b5a (verified by diff; that commit is not an
# ancestor of any public ref and GitHub refuses it as "not our ref", so a
# fresh clone could never fetch it). The generator binary is a pure function
# of ps2xRecomp/ + version-pinned FetchContent deps, so the outputs are
# identical; each prepare re-proves it via the register SHA.
GENERATOR = '467e86e85fee4dd73c8252f40a02c6394116888e'
FORK = 'https://github.com/brad-richardson/PS2Recomp.git'
ISO_SHA = '3c2f8eb182c9c6208a6e8172a41e61c98f420abe3f42c845f6829aeb9761ebf5'
ELF_SHA = '1b49d05ca2793922180851b9e1ce9ae2291d61a7863565ac4e71f12e967af7bc'
PUBLIC_APP_ID = 'com.ssx3.recomp'
PLAY_APP_ID = 'com.ps2x.runner'
APP_LABEL_DEFAULT = 'SSX3 120'
APP_LABEL_TEST_DEFAULT = 'SSX3 120 Test'


def default_app_label(app_id):
    """Home-screen name for an app id (odin-testapp-1 addendum 2, G7).

    Product ids show 'SSX3 120'; lab/test ids get a distinguishable
    'SSX3 120 Test' so two installs never look identical. Mirrors the
    engine's -Pps2xAppLabel gradle default and PS2X_APP_DISPLAY_NAME cache
    default; --app-label overrides on both platforms."""
    return APP_LABEL_DEFAULT if app_id in (PUBLIC_APP_ID, PLAY_APP_ID) else APP_LABEL_TEST_DEFAULT


def resolve_app_label(a):
    label = getattr(a, 'app_label', None)
    if label:
        return label
    return default_app_label(getattr(a, 'app_id', PUBLIC_APP_ID))
SECTOR = 2048
SLIM_MAGIC = b'PS2XSLM2'
MOVIE_PREFIX_SECTORS = 512  # 1 MiB; observed reads end by sector 272.


NDK_VERSION = '28.2.13676358'
SDK_PACKAGES = ['platforms;android-34', 'build-tools;34.0.0',
                'ndk;' + NDK_VERSION, 'cmake;3.22.1', 'platform-tools']


def supported_host():
    system, machine = platform.system(), platform.machine().lower()
    return (system == 'Darwin' and machine == 'arm64' or
            system == 'Linux' and machine in ('x86_64', 'aarch64', 'arm64'))


def probe(command):
    try:
        result = subprocess.run(list(map(str, command)), text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                timeout=30, check=False)
        return result.returncode == 0, result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired) as error:
        return False, str(error)


def compiler():
    """Respect overrides; otherwise prefer installed LLVM over Apple Clang."""
    cxx = os.environ.get('CXX')
    if not cxx and platform.system() == 'Darwin' and shutil.which('brew'):
        ok, prefix = probe(['brew', '--prefix', 'llvm'])
        if ok and Path(prefix, 'bin/clang++').is_file():
            cxx = str(Path(prefix, 'bin/clang++'))
    if not cxx:
        cxx = next((shutil.which(name) for name in
                    ['clang++-' + str(v) for v in range(24, 17, -1)] + ['clang++']
                    if shutil.which(name)), None)
    if not cxx:
        raise ValueError('install Clang (Ubuntu: apt install clang lld g++); set CC/CXX if needed')
    cxx_base = Path(cxx).name
    if 'clang++' in cxx_base:
        cc = os.environ.get('CC') or str(Path(cxx).with_name(cxx_base.replace('clang++', 'clang')))
    elif 'g++' in cxx_base:
        cc = os.environ.get('CC') or str(Path(cxx).with_name(cxx_base.replace('g++', 'gcc')))
    else:
        # Generic names such as c++ carry no C counterpart: use the system
        # compiler rather than compiling C sources as C++.
        cc = os.environ.get('CC') or shutil.which('cc')
        if not cc:
            raise ValueError('set CC when CXX=' + cxx + ' (cannot derive the C compiler)')
    for name in (cc, cxx):
        ok, detail = probe([name, '--version'])
        if not ok:
            raise ValueError('compiler cannot run: ' + name + ': ' + detail)
    return ['-DCMAKE_C_COMPILER=' + cc, '-DCMAKE_CXX_COMPILER=' + cxx]


def sdk_root():
    value = os.environ.get('ANDROID_HOME') or os.environ.get('ANDROID_SDK_ROOT')
    return Path(value).expanduser().resolve() if value else None


def ndk_root(explicit=None):
    if explicit:
        return explicit.expanduser().resolve()
    sdk = sdk_root()
    return sdk / 'ndk' / NDK_VERSION if sdk else None


def doctor(a):
    """Check actual executability, including translated SDK tools on Linux arm64."""
    failures = []

    def check(label, ok, detail, fix):
        print(('OK   ' if ok else 'MISS ') + label + ': ' + detail)
        if not ok:
            failures.append(label)
            print('     Fix: ' + fix)

    system, machine = platform.system(), platform.machine()
    check('host', supported_host(), system + ' ' + machine,
          'use macOS Apple silicon or Linux x86_64/arm64; on Windows use Ubuntu in WSL2')
    linux = system == 'Linux'
    packages = ('sudo apt-get update && sudo apt-get install -y python3 git cmake '
                'ninja-build clang lld g++ pkg-config curl ca-certificates unzip zip '
                'make autoconf automake libtool bison flex openjdk-17-jdk') if linux else (
                'brew install cmake ninja llvm python pkg-config autoconf automake libtool bison')
    for tool in ('git', 'cmake', 'ninja', 'pkg-config'):
        ok, detail = probe([tool, '--version'])
        check(tool, ok, detail.splitlines()[0] if detail else 'missing', packages)
    try:
        flags = compiler()
        check('host compiler', True, ' '.join(flags), packages)
    except ValueError as error:
        check('host compiler', False, str(error), packages)
    if a.target == 'prepare':
        if failures:
            raise ValueError('doctor: missing prerequisites: ' + ', '.join(failures))
        print('Doctor passed for native generator preparation (no disc needed).')
        return
    if a.target == 'mac':
        check('Mac target', system == 'Darwin' and machine == 'arm64', system + ' ' + machine,
              'mac/mac-libs/run require macOS Apple silicon; select --target android on Linux')
        for tool in ('clang', 'metal', 'metallib'):
            ok, detail = probe(['xcrun', '--find', tool])
            check('Xcode ' + tool, ok, detail, 'install Xcode and its Metal toolchain')
        ok, detail = probe(['brew', '--prefix'])
        check('Homebrew', ok, detail, 'install Homebrew and the dependencies in tools/ssx3/README.md')
    elif a.target == 'ios':
        check('iOS target', system == 'Darwin' and machine == 'arm64', system + ' ' + machine,
              'ios/ios-libs/run require macOS Apple silicon; select --target android on Linux')
        doctor_ios(a, check)
    else:
        if getattr(a, 'work', None):
            # A managed toolchain under --work counts as found (read-only;
            # doctor never downloads). Explicit environment still wins.
            import toolchain
            found_jdk, found_sdk, _, _ = toolchain.discover(
                a.work, getattr(a, 'jdk', None), getattr(a, 'ndk', None))
            if found_jdk is not None:
                os.environ.setdefault('JAVA_HOME', str(found_jdk))
            if found_sdk is not None:
                os.environ.setdefault('ANDROID_HOME', str(found_sdk))
        java_home = os.environ.get('JAVA_HOME')
        java = str(Path(java_home, 'bin/java')) if java_home else 'java'
        keytool = str(Path(java_home, 'bin/keytool')) if java_home else 'keytool'
        ok, detail = probe([java, '-version'])
        match = re.search(r'version "(\d+)', detail)
        check('JDK 17', ok and bool(match) and match[1] == '17', detail.splitlines()[0] if detail else 'missing',
              packages + '; set JAVA_HOME to the JDK 17 installation')
        ok, detail = probe([keytool, '-help'])
        check('keytool', ok, detail.splitlines()[0] if detail else 'missing', 'install JDK 17 and set JAVA_HOME/PATH')
        sdk = sdk_root()
        sdk_fix = ('install Android command-line tools; export ANDROID_HOME=/path/to/sdk; '
                   'run "$ANDROID_HOME/cmdline-tools/latest/bin/sdkmanager" ' +
                   ' '.join('"' + package + '"' for package in SDK_PACKAGES) +
                   '; review and accept the SDK licenses with sdkmanager --licenses; '
                   'or run ./build android --ssx3-iso <ISO> --work <DIR> '
                   '--accept-android-licenses to fetch the pinned toolchain')
        check('ANDROID_HOME', bool(sdk and sdk.is_dir()), str(sdk), sdk_fix)
        if sdk:
            check('SDK platform 34', (sdk / 'platforms/android-34/android.jar').is_file(),
                  str(sdk / 'platforms/android-34'), sdk_fix)
            for label, path, arg in (
                ('SDK aapt2', sdk / 'build-tools/34.0.0/aapt2', 'version'),
                ('SDK CMake', sdk / 'cmake/3.22.1/bin/cmake', '--version'),
                ('SDK Ninja', sdk / 'cmake/3.22.1/bin/ninja', '--version')):
                ok, detail = probe([path, arg])
                check(label, ok, detail.splitlines()[0] if detail else str(path), sdk_fix +
                      '; Linux arm64 also needs working x86_64 binfmt translation and x86_64 runtime libraries')
        ndk = ndk_root(a.ndk)
        revision = ''
        if ndk and (ndk / 'source.properties').is_file():
            revision = (ndk / 'source.properties').read_text()
        check('NDK revision', bool(re.search(r'Pkg.Revision\s*=\s*' + re.escape(NDK_VERSION) + r'\s*$', revision, re.M)),
              str(ndk), sdk_fix)
        if ndk:
            tag = 'darwin-x86_64' if system == 'Darwin' else 'linux-x86_64'
            clang = ndk / 'toolchains/llvm/prebuilt' / tag / 'bin/clang'
            ok, detail = probe([clang, '--version'])
            check('NDK host execution', ok, detail.splitlines()[0] if detail else str(clang),
                  sdk_fix + '; Linux arm64 requires x86_64 binfmt translation (NDK tools are x86_64)')
        for tool in ('make', 'bison', 'flex'):
            ok, detail = probe([tool, '--version'])
            check(tool, ok, detail.splitlines()[0] if detail else 'missing', packages)
        # F14: --install discovers its target here, not after the build.
        if getattr(a, 'install', False):
            check_install_device(a, check)
        # F01: the install step shells adb, so the doctor checks it the
        # same way install resolves it (managed SDK platform-tools first,
        # then the selected SDK, then PATH) and fails with the fix.
        adb_bin = toolchain.find_adb(getattr(a, 'work', None))
        if adb_bin is not None:
            ok, detail = probe([str(adb_bin), 'version'])
            check('adb', ok, detail.splitlines()[0] if detail else str(adb_bin),
                  'reinstall the SDK platform-tools package (' + ' '.join(
                      '"' + package + '"' for package in SDK_PACKAGES if 'platform-tools' in package) +
                  ') or put adb on PATH')
        else:
            check('adb', False, 'no adb in the selected SDK or on PATH',
                  'run ./build android --ssx3-iso <ISO> --work <DIR> '
                  '--accept-android-licenses to fetch the pinned toolchain '
                  '(includes platform-tools), or put adb on PATH')
    if failures:
        raise ValueError('doctor: missing prerequisites: ' + ', '.join(failures))
    print('Doctor passed for ' + a.target + '. No files or devices changed.')


def run(*args, cwd=None):
    print('+', ' '.join(map(str, args)), flush=True)
    subprocess.run(list(map(str, args)), cwd=cwd, check=True)


def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def verified_driver(name, entry, cache):
    """Fetch a Vulkan driver .so that ships inside a release archive.

    entry carries: url, archive_sha256 (the whole download), library (the
    member path inside the archive), sha256 (the member bytes that must end
    up as the driver). Only `library` is ever extracted. Any mismatch
    raises ValueError; callers fail closed to the system Vulkan driver, as
    with verified_asset.
    """
    dest = cache / name
    if dest.is_file() and sha(dest) == entry['sha256']:
        return dest
    tmp = cache / (name + '.download')
    tmp.unlink(missing_ok=True)
    try:
        with urllib.request.urlopen(entry['url'], timeout=60) as src, tmp.open('wb') as out:
            shutil.copyfileobj(src, out)
        if sha(tmp) != entry['archive_sha256']:
            raise ValueError(name + ' archive SHA-256 mismatch')
        with zipfile.ZipFile(tmp) as z:
            lib = entry['library']
            if lib.startswith('/') or '..' in Path(lib).parts:
                raise ValueError('unsafe Turnip library path: ' + lib)
            try:
                data = z.read(lib)
            except KeyError:
                raise ValueError('Turnip library missing from archive: ' + lib)
        if hashlib.sha256(data).hexdigest() != entry['sha256']:
            raise ValueError(name + ' driver SHA-256 mismatch')
        dest.unlink(missing_ok=True)
        dest.write_bytes(data)
        return dest
    finally:
        tmp.unlink(missing_ok=True)


def extract_elf(iso, dest):
    # Read the ISO9660 root directly; no mounting, BIOS or external extraction tool.
    with iso.open('rb') as f:
        f.seek(16 * 2048)
        pvd = f.read(2048)
        if pvd[:7] != b'\x01CD001\x01':
            raise ValueError('expected ISO9660 primary volume descriptor')
        root = pvd[156:190]
        extent, length = struct.unpack_from('<I', root, 2)[0], struct.unpack_from('<I', root, 10)[0]
        f.seek(extent * 2048)
        directory = f.read(length)
        pos = 0
        while pos < len(directory):
            size = directory[pos]
            if not size:
                pos = (pos // 2048 + 1) * 2048
                continue
            entry = directory[pos:pos + size]
            name = entry[33:33 + entry[32]].decode('ascii', errors='replace').split(';')[0]
            if name == 'SLUS_207.72':
                sector = struct.unpack_from('<I', entry, 2)[0]
                count = struct.unpack_from('<I', entry, 10)[0]
                f.seek(sector * 2048)
                dest.write_bytes(f.read(count))
                return
            pos += size
    raise ValueError('SLUS_207.72 missing from disc root')


def iso_records(iso):
    """Yield (path, first LBA, byte size) from the pinned ISO9660 tree."""
    with iso.open('rb') as f:
        f.seek(16 * SECTOR)
        pvd = f.read(SECTOR)
        if pvd[:7] != b'\x01CD001\x01':
            raise ValueError('expected ISO9660 primary volume descriptor')

        def walk(path, extent, length):
            f.seek(extent * SECTOR)
            directory = f.read(length)
            pos = 0
            while pos < length:
                size = directory[pos]
                if not size:
                    pos = (pos // SECTOR + 1) * SECTOR
                    continue
                entry = directory[pos:pos + size]
                pos += size
                if len(entry) < 34 or entry[32] == 1 and entry[33] in (0, 1):
                    continue
                name = entry[33:33 + entry[32]].decode('ascii').split(';')[0]
                lba = struct.unpack_from('<I', entry, 2)[0]
                bytes_ = struct.unpack_from('<I', entry, 10)[0]
                child = path + '/' + name if path else name
                if entry[25] & 2:
                    yield from walk(child, lba, bytes_)
                else:
                    yield child, lba, bytes_

        root = pvd[156:190]
        yield from walk('', struct.unpack_from('<I', root, 2)[0],
                        struct.unpack_from('<I', root, 10)[0])


def slim_holes(iso):
    entries = list(iso_records(iso))
    holes = []
    names = set()
    for name, lba, size in entries:
        upper = name.upper()
        kind = 1 if upper in ('PAD0.000', 'PAD1.000') else (
            2 if upper.startswith('DATA/MOVIES/') and upper.endswith('.MPC') else 0)
        if kind:
            sectors = (size + SECTOR - 1) // SECTOR
            if kind == 2:
                prefix = min(sectors, MOVIE_PREFIX_SECTORS)
                holes.append((lba, prefix, 3))  # retained movie prefix
                if sectors > prefix:
                    holes.append((lba + prefix, sectors - prefix, 2))
            else:
                holes.append((lba, sectors, kind))
            names.add(upper)
    if not {'PAD0.000', 'PAD1.000'} <= names or len([h for h in holes if h[2] == 3]) != 14:
        raise ValueError('expected two pads and fourteen movies')
    holes.sort()
    total = iso.stat().st_size // SECTOR
    for i, (start, count, _) in enumerate(holes):
        if not count or start + count > total or (i and start < holes[i - 1][0] + holes[i - 1][1]):
            raise ValueError('overlapping or out-of-range slim hole')
    return holes


def write_slim(iso, output, holes, source_sha):
    """Write retained sectors and an adjacent versioned LBA table atomically."""
    if iso.stat().st_size % SECTOR:
        raise ValueError('ISO size is not sector aligned')
    total = iso.stat().st_size // SECTOR
    if output.exists() or Path(str(output) + '.holes').exists():
        raise ValueError('refusing existing slim image or sidecar')
    ranges = sorted(holes)
    if not ranges or len(ranges) > 64:
        raise ValueError('invalid hole count')
    cursor = 0
    with iso.open('rb') as src, output.open('xb') as dst:
        for start, count, kind in ranges + [(total, 0, 0)]:
            if start < cursor or start + count > total or kind not in (0, 1, 2, 3):
                raise ValueError('invalid hole range')
            src.seek(cursor * SECTOR)
            remaining = (start - cursor + (count if kind == 3 else 0)) * SECTOR
            while remaining:
                block = src.read(min(8 * 1024 * 1024, remaining))
                if not block:
                    raise ValueError('short ISO read')
                dst.write(block)
                remaining -= len(block)
            cursor = start + count
    if output.stat().st_size != (total - sum(h[1] for h in ranges if h[2] != 3)) * SECTOR:
        raise ValueError('slim payload size mismatch')
    header = SLIM_MAGIC + source_sha.encode('ascii') + struct.pack('<QI', total, len(ranges))
    if len(source_sha) != 64 or len(header) != 84:
        raise ValueError('invalid source SHA')
    table = header + b''.join(struct.pack('<III', *hole) for hole in ranges)
    Path(str(output) + '.holes').write_bytes(table)
    return output


def configure(src, dst, *flags):
    run('cmake', '-S', src, '-B', dst, '-G', 'Ninja', '-DCMAKE_BUILD_TYPE=Release',
        '-DPS2X_BUILD_STUDIO=OFF', '-DPS2X_ENABLE_SCCACHE=OFF', *compiler(), *flags)


def prepare_generator(a):
    gen = a.work / 'generator-source'
    if not gen.exists():
        # Prefer the exact pinned commit already present in the engine checkout.
        # This also validates an unpublished lane commit without a fork push.
        # Public-safe: the shortcut applies only when the pin is part of the
        # published fork history (a lane-only object must never satisfy it).
        # A clean public clone therefore falls through to the FORK fetch,
        # which is the path the release check exercises.
        has_pin = subprocess.run(
            ['git', '-C', str(ENGINE), 'cat-file', '-e', GENERATOR + '^{commit}'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False).returncode == 0
        published = False
        for ref in ('fork/ssx3', 'origin/ssx3', 'HEAD'):
            if subprocess.run(
                    ['git', '-C', str(ENGINE), 'merge-base', '--is-ancestor', GENERATOR, ref],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    check=False).returncode == 0:
                published = True
                break
        local_pin = has_pin and published
        run('git', 'init', gen)
        run('git', '-C', gen, 'fetch', '--depth=1', str(ENGINE) if local_pin else FORK, GENERATOR)
        run('git', '-C', gen, 'checkout', '--detach', GENERATOR)
    actual = subprocess.check_output(['git', '-C', str(gen), 'rev-parse', 'HEAD'], text=True).strip()
    if actual != GENERATOR:
        raise ValueError('generator source pin mismatch')
    build = a.work / 'generator-build'
    configure(gen, build, '-DPS2X_BUILD_RUNTIME=OFF', '-DPS2X_BUILD_ANALYZER=OFF', '-DPS2X_BUILD_TEST=OFF')
    run('cmake', '--build', build, '--parallel', a.jobs, '--target', 'ps2_recomp')
    return build


def prepare_plan(work, iso, symbol_names=None):
    """F16/F17: 'skip' when the receipt matches, else ('regenerate', stale).

    A rerun after an interruption lands in 'regenerate' with every derived
    output listed (all regenerable from the ISO; signing keys, cards and
    the keystore live elsewhere and are never touched). Stale inputs.json
    pins (generator/toml/map) regenerate instead of being silently reused.
    """
    work = Path(work)
    if (work / 'inputs.json').is_file() and prepare_current(work, iso):
        inputs = json.loads((work / 'inputs.json').read_text())
        want_symbols = sha(symbol_names) if symbol_names else None
        if inputs.get('symbol_names_sha256') != want_symbols:
            print('prepare: symbol-names sidecar changed, regenerating')
        else:
            return 'skip', []
    stale = [work / name for name in ('codegen', 'codegen.new', 'SSX3.iso',
                                      'SSX3.iso.holes', 'SLUS_207.72',
                                      'generation.toml', 'inputs.json')]
    return 'regenerate', [p for p in stale if p.is_symlink() or p.exists()]


def _drop_paths(paths):
    for path in paths:
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)


def prepare(a):
    # F03: the one-command paths reach here without the step-by-step
    # main() mkdir, so prepare owns its work directory before writing
    # SLUS_207.72 / the slim image / codegen into it.
    a.work.mkdir(parents=True, exist_ok=True)
    if a.tools_only:
        prepare_generator(a)
        print('Native generator prepared; no disc read and no game sources generated.')
        return
    # F12: clear disc errors in the step-by-step route too.
    iso = validate_ssx3_iso(a.iso)
    if getattr(a, 'symbol_names', None) and not Path(a.symbol_names).is_file():
        raise ValueError('missing optional symbol names sidecar: ' + str(a.symbol_names))
    # F16/F17: resume when current; otherwise drop stale derived outputs so
    # `re-run the same command` recovers from an interruption.
    plan, stale = prepare_plan(a.work, iso, getattr(a, 'symbol_names', None))
    if plan == 'skip':
        print('prepare: outputs current for this disc, skipping')
        return
    _drop_paths(stale)
    elf = a.work / 'SLUS_207.72'
    extract_elf(iso, elf)
    if sha(elf) != ELF_SHA:
        raise ValueError('extracted ELF SHA256 mismatch')
    slim = write_slim(iso, a.work / 'SSX3.iso', slim_holes(iso), ISO_SHA)
    build = prepare_generator(a)
    new = a.work / 'codegen.new'
    _drop_paths([new])
    config = (ROOT / 'games/ssx3/ssx3.toml').read_text()
    # All local paths are anchored absolute in the private work directory.
    paths = {'input': elf, 'ghidra_output': ROOT / 'games/ssx3/ssx3-functions.sweep.csv', 'output': new}
    for key, value in paths.items():
        config = re.sub(r'^' + key + r' = .*$', key + ' = ' + json.dumps(str(value)), config, flags=re.M)
    if a.symbol_names:
        config = config.replace('[general]\n',
            '[general]\nsymbol_names = ' + json.dumps(str(Path(a.symbol_names).resolve())) + '\n', 1)
    toml = a.work / 'generation.toml'
    atomic_write_text(toml, config)
    run(build / 'ps2xRecomp/ps2_recomp', toml)
    register_sha = sha(new / 'register_functions.cpp')
    # F17: publish the finished codegen atomically; an interruption before
    # this line leaves no inputs.json, so the next run regenerates.
    new.replace(a.work / 'codegen')
    atomic_write_text(a.work / 'inputs.json', json.dumps({'iso': str(iso), 'iso_sha256': ISO_SHA,
        'slim_iso': str(slim), 'slim_sha256': sha(slim),
        'slim_table_sha256': sha(Path(str(slim) + '.holes')),
        'elf_sha256': ELF_SHA, 'generator': GENERATOR, 'register_sha256': register_sha,
        'symbol_names_sha256': sha(a.symbol_names) if a.symbol_names else None,
        'toml_sha256': sha(ROOT / 'games/ssx3/ssx3.toml'),
        'map_sha256': sha(ROOT / 'games/ssx3/ssx3-functions.sweep.csv')}, indent=2) + '\n')
    print('Prepared local game sources. Keep this work directory private.')


def mac(a):
    extra = 'ON' if a.suite else 'OFF'
    det = 'ON' if a.det else 'OFF'
    # Det flavor carries the VBlank XXH64 tap (gate boots + tap unit tests);
    # play builds keep it off. Separate dirs so flavors never mix.
    build_dir = a.work / ('runner-build-det' if a.det else 'runner-build')
    configure(ENGINE, build_dir, '-DPS2X_GAME_CODEGEN_DIR=' + str(a.work / 'codegen'),
        '-DPS2X_VU0_RECOMP_DIR=' + resolve_vu0_stage(a),
        '-DPS2X_BUILD_RECOMP=' + extra, '-DPS2X_BUILD_ANALYZER=' + extra,
        '-DPS2X_BUILD_TEST=' + extra, '-DPS2X_ENABLE_DEBUG_UI=OFF', '-DPS2X_ENABLE_RUNTIME_LOGS=OFF',
        '-DPS2X_ENABLE_AGRESSIVE_LOGS=OFF', '-DPS2X_ENABLE_DIAG_TAPS=OFF',
        '-DPS2X_ENABLE_DIAG_WATCH=OFF', '-DPS2X_ENABLE_TS2_DIAG=OFF',
        '-DPS2X_ENABLE_DET_HASH_TAP=' + det, '-DPS2X_GAME_PCH=ON')
    run('cmake', '--build', build_dir, '--parallel', a.jobs,
        '--target', 'ps2EntryRunner', *(['ps2x_tests'] if a.suite else []))


# --- VU0 images (build-parity-fix-1 D03). User-disc VU0 recomp stage, the
# same idea as the OM1 step: generated from the user's ELF, never shipped.
# The engine compiles <stage>/vu0e_*.cpp into ps2EntryRunner when the build
# points PS2X_VU0_RECOMP_DIR at it (ps2xRuntime/CMakeLists.txt VR3 block);
# an empty dir builds interpreter-only, and with VU0 recomp enabled but no
# images the runtime falls back to the interpreter per program
# (lookupRecompProgram returns nullptr on a registry miss and run() issues
# the pair through issuePair<false>; no fail, no crash). Generate once with
# `build.py vu0 --work <DIR>` (needs prepare outputs), or pass --vu0-stage
# explicitly. Without a stage the builds keep today's interpreter behavior
# with one printed line, so no existing flow breaks.
VU0_IMAGE_GLOB = 'vu0e_*.cpp'

# Offset manifest for the vu0 step (shipped): (micro_offset, elf_offset,
# length) spans plus zero-fill ranges that rebuild each covered 4 KiB VU0
# code image from the user's ELF, with ELF/image/output hashes and the
# emitter pin. Offsets, lengths, IDs and hashes only: no instruction
# words, generated C++, code image or data bytes are shipped. The manifest
# must state every covered image hash; programs outside it keep the
# interpreter fallback (engine default for an empty registry entry).
VU0_IMAGE_MANIFEST = ROOT / 'tools/ssx3/vu0-image-manifest.json'


def load_vu0_image_manifest():
    try:
        manifest = json.loads(VU0_IMAGE_MANIFEST.read_text())
    except (OSError, ValueError):
        raise ValueError('vu0 image manifest unreadable: ' + str(VU0_IMAGE_MANIFEST))
    if manifest.get('elf_sha256') != ELF_SHA or not manifest.get('images'):
        raise ValueError('vu0 image manifest does not cover this disc revision')
    return manifest


def reconstruct_vu0_images(elf_bytes, manifest):
    """Rebuild every covered 4 KiB image from the user's ELF bytes.

    Fail-closed: span bounds, micro-image assembly (spans plus zero-fill
    must cover exactly 4096 bytes with no overlap) and the image SHA-256
    are all verified before anything is emitted.
    """
    images = {}
    for image in manifest['images']:
        micro = bytearray(4096)
        covered = bytearray(4096)
        for span in image.get('spans', []):
            start, length, elf_off = span['micro_offset'], span['length'], span['elf_offset']
            if start < 0 or length <= 0 or start + length > 4096 or elf_off < 0 \
                    or elf_off + length > len(elf_bytes):
                raise ValueError('vu0 manifest span out of range: ' + str(span))
            if any(covered[start:start + length]):
                raise ValueError('vu0 manifest spans overlap')
            micro[start:start + length] = elf_bytes[elf_off:elf_off + length]
            covered[start:start + length] = b'\x01' * length
        for fill in image.get('zero_fill', []):
            start, length = fill['micro_offset'], fill['length']
            if start < 0 or length <= 0 or start + length > 4096:
                raise ValueError('vu0 manifest zero-fill out of range: ' + str(fill))
            if any(covered[start:start + length]):
                raise ValueError('vu0 manifest spans overlap')
            covered[start:start + length] = b'\x01' * length
        if not all(covered):
            raise ValueError('vu0 manifest leaves image bytes uncovered: ' + image['xxh64'])
        if hashlib.sha256(bytes(micro)).hexdigest() != image['image_sha256']:
            raise ValueError('reconstructed VU0 image does not match the manifest: ' + image['xxh64'])
        images['vu0e_' + image['xxh64'] + '.bin'] = bytes(micro)
    return images


def build_vu0_emitter(engine, work):
    """Compile the engine's standalone vu0dump emitter for this host.

    Same TU list and Release flags as the engine's vu0dump recipe; ARM Macs
    add the pinned sse2neon shim (same v1.9.1 the engine CMake fetches).
    The binary is cached in the work dir, keyed by the engine pin.
    """
    spec = load_vu0_image_manifest()['emitter']
    cache = Path(work) / 'vu0-emitter'
    binary = cache / 'vu0dump'
    receipt = cache / 'emitter.json'
    engine_head = subprocess.check_output(['git', '-C', str(engine), 'rev-parse', 'HEAD'],
                                          text=True).strip()
    try:
        current = json.loads(receipt.read_text())
    except (OSError, ValueError):
        current = {}
    if binary.is_file() and current.get('engine') == engine_head:
        return binary
    if platform.system() == 'Darwin':
        cxx = Path('/opt/homebrew/opt/llvm/bin/clang++')
        if not cxx.is_file():
            cxx = Path(shutil.which('clang++') or 'clang++')
    else:
        cxx = Path(shutil.which('clang++') or 'clang++')
    flags = list(spec['cxx_flags'])
    includes = [engine / 'ps2xRuntime/include', engine / 'ps2xRuntime/src/lib/vu',
                engine / 'ps2xRuntime/src/lib/Kernel']
    if platform.system() == 'Darwin' and platform.machine() == 'arm64':
        neon = Path(work) / 'sse2neon-source'
        source(spec['sse2neon_repo'], spec['sse2neon_pin'], neon)
        flags += spec['defines_arm64_mac']
        includes = [neon] + includes
    cache.mkdir(parents=True, exist_ok=True)
    cmd = [str(cxx)] + flags + [x for p in includes for x in ('-I', str(p))] \
        + [str(engine / s) for s in spec['sources']] + ['-o', str(binary)]
    print('+ ' + ' '.join(cmd))
    run(*cmd)
    receipt.write_text(json.dumps({'engine': engine_head,
                                   'emitter': spec['tool'] + '@' + engine_head}, indent=2) + '\n')
    return binary


def validate_vu0_stage(path):
    """Fail clearly when the user's VU0 image stage is incomplete."""
    stage = Path(path)
    if not stage.is_dir():
        raise ValueError('--vu0-stage must be a directory holding your locally generated '
                         'VU0 images (got ' + str(path) + '); see tools/ssx3/README.md')
    images = sorted(stage.glob(VU0_IMAGE_GLOB))
    if not images:
        raise ValueError('--vu0-stage holds no ' + VU0_IMAGE_GLOB + ' images: ' + str(stage))
    for image in images:
        try:
            text = image.read_text()
        except OSError:
            raise ValueError('--vu0-stage image unreadable: ' + str(image))
        # VBK1 emitter contract: pair + group + guarded-group tables.
        if 'VU0RecompImage<' not in text or 'kGroupPairs' not in text:
            raise ValueError('--vu0-stage image is not a guarded-group VU0 image: ' + str(image))
    return stage


def vu0_stage_file_hashes(stage):
    """SHA-256 of every vu0e_ image under the stage. None on read failure."""
    hashes = {}
    try:
        entries = sorted(Path(stage).glob(VU0_IMAGE_GLOB))
    except OSError:
        return None
    if not entries:
        return None
    for p in entries:
        if not p.is_file():
            return None
        try:
            hashes[p.name] = sha(p)
        except OSError:
            return None
    return hashes


def vu0_receipt_path(work):
    return Path(work) / 'vu0-stage.json'


def vu0_current(work):
    """Resume predicate: the default work/vu0-stage matches the ELF that
    generated it (receipt) and still satisfies the validate contract."""
    try:
        receipt = json.loads(vu0_receipt_path(work).read_text())
    except (OSError, ValueError):
        return False
    try:
        elf_sha = sha(Path(work) / 'SLUS_207.72')
    except OSError:
        return False
    if receipt.get('elf_sha256') != elf_sha:
        return False
    stage = Path(work) / 'vu0-stage'
    try:
        validate_vu0_stage(stage)
    except ValueError:
        return False
    want_out = receipt.get('outputs_sha256')
    if not isinstance(want_out, dict):
        return False
    have_out = vu0_stage_file_hashes(stage)
    if have_out is None or have_out != want_out:
        return False
    return True


def resolve_vu0_stage(a):
    """VU0 image dir string for a build, or '' for interpreter-only.

    Explicit --vu0-stage wins (validated). Otherwise the current default
    work/vu0-stage when its receipt still matches (validated + resume log
    line). Otherwise '' with one printed line: the build compiles zero VU0
    images and runs the interpreter, exactly like before this lane.
    """
    explicit = getattr(a, 'vu0_stage', None)
    if explicit:
        return str(validate_vu0_stage(explicit))
    work = a.work
    if vu0_current(work):
        print('  VU0 images current ' + str(work / 'vu0-stage'))
        return str(work / 'vu0-stage')
    print('  no VU0 image stage in ' + str(work) + '; building interpreter-only '
          '(run the vu0 step from your disc to stage one)')
    return ''


@contextmanager
def vu0_stage_lock(work):
    """Serialize stage generation per work dir (same shape as om1_stage_lock:
    the lock file holds the holder's pid; a present lock fails loud). Remove
    the lock file if the holder process is gone."""
    lock = Path(work) / 'vu0-stage.lock'
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            holder = lock.read_text().strip()
        except OSError:
            holder = '?'
        raise ValueError('vu0 generation is already running in ' + str(work) +
                         ' (lock held by pid ' + holder + '); remove ' + str(lock) +
                         ' if that process is gone')
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(str(os.getpid()))
        yield
    finally:
        try:
            lock.unlink()
        except OSError:
            pass


def publish_vu0_stage(work, new, receipt_data):
    """Atomically publish a verified temp stage (same shape as OM1 R6: the
    receipt lands first via temp file + os.replace, then the old stage is
    removed and the temp dir renamed into place)."""
    receipt = vu0_receipt_path(work)
    tmp = receipt.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(receipt_data))
    os.replace(tmp, receipt)
    stage = Path(work) / 'vu0-stage'
    if stage.is_symlink() or stage.exists():
        stage.unlink() if stage.is_symlink() else shutil.rmtree(stage)
    os.rename(new, stage)
    return stage


def vu0(a):
    """vu0 step (any host): rebuild the covered VU0 microprograms from the
    user's ELF into work/vu0-stage, the same idea as the OM1 step
    (generated from the user's disc, never shipped). No game boot, no
    display, no Mac-only capture: the shipped offset manifest rebuilds
    each 4 KiB image from ELF spans plus zero-fill (fail-closed on bounds
    and image hash), then the engine's standalone vu0dump emitter derives
    the pair/group tables. Validates, checks the expected output hashes,
    publishes atomically and writes the receipt (ELF SHA + output hashes
    + engine pin) that vu0_current gates on. Resume: a current stage
    prints one line and is left alone.
    """
    manifest = load_vu0_image_manifest()
    try:
        elf_bytes = (a.work / 'SLUS_207.72').read_bytes()
    except OSError:
        raise ValueError('vu0 needs work/SLUS_207.72: run prepare with --ssx3-iso first')
    if sha(a.work / 'SLUS_207.72') != ELF_SHA:
        raise ValueError('vu0: SLUS_207.72 is not the supported disc revision')
    if vu0_current(a.work):
        print('  VU0 images current ' + str(a.work / 'vu0-stage'))
        return a.work / 'vu0-stage'
    images = reconstruct_vu0_images(elf_bytes, manifest)
    emitter = build_vu0_emitter(ENGINE, a.work)
    with vu0_stage_lock(a.work):
        try:
            vu0_receipt_path(a.work).unlink()
        except OSError:
            pass
        new = a.work / 'vu0-stage.new'
        if new.is_symlink() or new.exists():
            new.unlink() if new.is_symlink() else shutil.rmtree(new)
        new.mkdir(parents=True)
        for name, code in sorted(images.items()):
            image_bin = new / name
            image_bin.write_bytes(code)
            print('+ emit ' + name)
            run(str(emitter), str(image_bin), str(new))
            image_bin.unlink()
        validate_vu0_stage(new)
        output_hashes = vu0_stage_file_hashes(new)
        if output_hashes is None:
            raise ValueError('vu0: cannot hash the generated stage under ' + str(new))
        for image in manifest['images']:
            for name, want in image.get('expected_outputs_sha256', {}).items():
                if output_hashes.get(name) != want:
                    raise ValueError('vu0: emitted ' + name + ' does not match the manifest')
        engine_head = subprocess.check_output(['git', '-C', str(ENGINE), 'rev-parse', 'HEAD'],
                                              text=True).strip()
        stage = publish_vu0_stage(a.work, new, {
            'elf_sha256': ELF_SHA, 'engine': engine_head,
            'outputs_sha256': output_hashes})
    print('  VU0 images generated ' + str(stage))
    return stage


SIGNING_KEYSTORE = 'ssx3-signing.keystore'
SIGNING_ALIAS = 'ssx3'
SIGNING_PASSWORD = 'ssx3local'


def ensure_keystore(work):
    # Per-user signing key, created once in the work dir (never in the
    # checkout) and reused on every later build. The password is not a
    # secret; possession of this file is what must be preserved.
    keystore = work / SIGNING_KEYSTORE
    if keystore.exists():
        return keystore
    keytool = shutil.which('keytool')
    if not keytool and os.environ.get('JAVA_HOME'):
        candidate = Path(os.environ['JAVA_HOME'], 'bin/keytool')
        if candidate.exists():
            keytool = str(candidate)
    if not keytool:
        raise ValueError('keytool not found; install JDK 17 and set JAVA_HOME')
    print('Creating local signing keystore: ' + str(keystore))
    print('Back up your keystore; a different key forces an uninstall, which wipes saves.')
    run(keytool, '-genkeypair', '-keystore', keystore, '-alias', SIGNING_ALIAS,
        '-storepass', SIGNING_PASSWORD, '-keypass', SIGNING_PASSWORD,
        '-keyalg', 'RSA', '-keysize', '2048', '-validity', '10000',
        '-dname', 'CN=SSX3 Local Build, OU=Local, O=Local, L=Local, ST=Local, C=US',
        '-noprompt')
    return keystore


def next_version_code(work):
    counter = work / 'version-code.txt'
    last = 1
    if counter.exists():
        try:
            last = int(counter.read_text().strip())
        except ValueError:
            last = 1
    code = last + 1
    counter.write_text(str(code) + '\n')
    return code


# PGO: bundled indexed profiles with a codegen freshness gate.
# Profiles hold function names, hashes and counters only; no game bytes.
# They are stored compressed in tools/ssx3/pgo/<platform>/ and decompressed
# into the user's private work dir on use (never inside the checkout).
PGO_DIR = ROOT / 'tools/ssx3/pgo'


def pgo_manifest(platform):
    """Load the bundled profile manifest for android/ios (None when absent)."""
    manifest = PGO_DIR / platform / 'MANIFEST.json'
    if not manifest.is_file():
        return None
    try:
        return json.loads(manifest.read_text())
    except (OSError, ValueError):
        return None


def codegen_register_sha(work):
    """SHA256 of the user's generated register_functions.cpp (None when absent)."""
    candidate = work / 'codegen' / 'register_functions.cpp'
    if not candidate.is_file():
        return None
    return sha(candidate)


def pgo_fresh(manifest, actual_register):
    """Whether the bundled profile applies to this codegen.

    Fresh when the register matches the trained SHA, or when it names a
    probe-validated codegen in the manifest's validated_registers list
    (D18: public unnamed codegen differs from the trained named codegen in
    symbol names only; game bodies are identical, so the lab stale probe
    reads 0 mismatches and the profile applies).
    """
    if not manifest or not actual_register:
        return False
    if actual_register == manifest.get('trained_codegen_register_sha256'):
        return True
    return any(entry.get('register_sha256') == actual_register
               for entry in manifest.get('validated_registers', []))


def resolve_pgo(work, platform, explicit=None, disabled=False):
    """Resolve a .profdata path for platform, or '' for a no-PGO build.

    Freshness gate: the bundled profile applies only when the user's codegen
    register SHA matches the profile's trained register SHA. Otherwise the
    builder prints one clear line and builds without PGO.
    """
    if disabled:
        print('PGO disabled by --no-pgo; building without PGO.')
        return ''
    if explicit:
        profile = Path(explicit).expanduser().resolve()
        if not profile.is_file():
            raise ValueError('missing --pgo-profile file: ' + str(explicit))
        print('PGO profile applied (explicit --pgo-profile): ' + str(profile))
        return str(profile)
    manifest = pgo_manifest(platform)
    archive = PGO_DIR / platform / 'profile.profdata.gz'
    if manifest is None or not archive.is_file():
        print('PGO profile missing for ' + platform + '; building without PGO.')
        return ''
    expected_register = manifest.get('trained_codegen_register_sha256')
    actual_register = codegen_register_sha(work)
    if not expected_register or not actual_register:
        print('PGO profile freshness unknown (no codegen register SHA); building without PGO.')
        return ''
    if not pgo_fresh(manifest, actual_register):
        print('PGO profile stale for this codegen (register ' + actual_register[:12]
              + ' != trained ' + str(expected_register)[:12] + '); building without PGO.')
        return ''
    dest = work / ('pgo-' + platform + '.profdata')
    want = manifest.get('profile_sha256')
    if dest.is_file() and want and sha(dest) == want:
        print('PGO profile applied (' + platform + ', ' + str(dest) + ').')
        return str(dest)
    with gzip.open(archive, 'rb') as src, dest.open('wb') as out:
        shutil.copyfileobj(src, out)
    if want and sha(dest) != want:
        dest.unlink(missing_ok=True)
        print('PGO profile SHA mismatch after decompress; building without PGO.')
        return ''
    print('PGO profile applied (' + platform + ', ' + str(dest) + ').')
    return str(dest)


def write_android_receipt(a, version_code, pgo_data, turnip_included, jnilibs):
    """Record the effective Android build inputs (G6 public-parity-2).

    The receipt names the app id/label, version, engine + GE1 vendor pins,
    staged microVU/GE1 library SHAs, whether the pinned Turnip driver was
    bundled (plus its SHA), the applied PGO profile (or null), and the
    shipped preset SHAs — so a release receipt proves V37/microVU/PGO are
    actually in the APK instead of silently fallen back."""
    turnip = json.loads((ROOT / 'tools/ssx3/turnip.json').read_text())
    libs = Path(jnilibs)
    def lib_sha(name):
        p = libs / 'arm64-v8a' / name
        return sha(p) if p.is_file() else None
    engine_head = subprocess.check_output(['git', '-C', str(ENGINE), 'rev-parse', 'HEAD'],
                                          text=True).strip()
    (a.work / 'android-app.json').write_text(json.dumps({
        'app_id': a.app_id, 'app_label': resolve_app_label(a),
        'version_code': version_code, 'engine': engine_head,
        'ge1_vendor': ANDROID_GE1_VENDOR_PIN,
        'microvu_lib_sha256': lib_sha('libmv2_microvu.so'),
        'ge1_lib_sha256': lib_sha('libge1_gs.so'),
        'turnip_included': turnip_included,
        'turnip_binary_sha256': turnip['binary']['sha256'] if turnip_included else None,
        'pgo_profile': pgo_data or None,
        'preset_sha256': sha(ROOT / 'presets/ps2x.env'),
        'full120_sha256': sha(ROOT / 'presets/full120.env')},
        indent=2, sort_keys=True) + '\n')
    print('Wrote Android build receipt ' + str(a.work / 'android-app.json'))


def android(a):
    ensure_android_toolchain(a)
    bundle = support_bundle(a)
    if bundle is not None:
        a.jnilibs = bundle / 'jniLibs'
    if not a.jnilibs:
        a.jnilibs = a.work / 'jniLibs'
        # Re-run the source recipe on a fallback: an existing staging directory
        # may contain libraries from a different source contract.
        if not a.ndk:
            sdk = os.environ.get('ANDROID_HOME') or os.environ.get('ANDROID_SDK_ROOT')
            a.ndk = Path(sdk) / 'ndk/28.2.13676358' if sdk else None
        original = a.prebuilt
        a.prebuilt = False
        try:
            android_libs(a)
        finally:
            a.prebuilt = original
    keystore = ensure_keystore(a.work)
    version_code = next_version_code(a.work)
    jnilibs = a.work / ('public-jniLibs-' + str(version_code))
    shutil.copytree(a.jnilibs.resolve(), jnilibs, dirs_exist_ok=True)
    if bundle is None:
        (jnilibs / 'arm64-v8a/libhardware.so').unlink(missing_ok=True)
    turnip_path = jnilibs / 'arm64-v8a/libvulkan_freedreno.so'
    notices_dir = a.work / ('public-notices-' + str(version_code))
    notices_dir.mkdir(parents=True, exist_ok=True)
    if bundle is not None:
        shutil.copytree(bundle / 'notices', notices_dir / 'third_party/support', dirs_exist_ok=True)
    turnip_included = False
    if a.no_turnip:
        turnip_path.unlink(missing_ok=True)
        print('Turnip disabled by --no-turnip; system Vulkan driver will be used.')
    else:
        manifest = json.loads((ROOT / 'tools/ssx3/turnip.json').read_text())
        cache = a.work / 'turnip-cache'
        cache.mkdir(exist_ok=True)
        def verified_asset(name, entry):
            if 'archive_sha256' in entry:
                return verified_driver(name, entry, cache)
            dest = cache / name
            if dest.is_file() and sha(dest) == entry['sha256']:
                return dest
            dest.unlink(missing_ok=True)
            tmp = cache / (name + '.download')
            tmp.unlink(missing_ok=True)
            try:
                with urllib.request.urlopen(entry['url'], timeout=60) as src, tmp.open('wb') as out:
                    shutil.copyfileobj(src, out)
                if sha(tmp) != entry['sha256']:
                    raise ValueError(name + ' SHA-256 mismatch')
                tmp.replace(dest)
                return dest
            finally:
                tmp.unlink(missing_ok=True)
        try:
            binary = verified_asset('libvulkan_freedreno.so', manifest['binary'])
            notices = verified_asset('turnip-notices.zip', manifest['notices'])
            # Release archives are pinned by SHA; also reject paths that could escape the asset root.
            with zipfile.ZipFile(notices) as z:
                for member in z.namelist():
                    parts = Path(member).parts
                    if member.startswith('/') or '..' in parts:
                        raise ValueError('unsafe Turnip notice path: ' + member)
                z.extractall(notices_dir / 'third_party/turnip')
            turnip_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(binary, turnip_path)
            turnip_included = True
            print('Turnip included (SHA-256 ' + manifest['binary']['sha256'] + ').')
        except (OSError, ValueError, zipfile.BadZipFile) as e:
            turnip_path.unlink(missing_ok=True)
            print('Turnip unavailable (' + str(e) + '); continuing with system Vulkan.', file=sys.stderr)
    print('Signing with local keystore ' + str(keystore) + ' (versionCode ' + str(version_code) + ').')
    print('Back up your keystore; a different key forces an uninstall, which wipes saves.')
    pgo_data = resolve_pgo(a.work, 'android', explicit=getattr(a, 'pgo_profile', None),
                           disabled=getattr(a, 'no_pgo', False))
    run('bash', ROOT / 'apps/android/gradlew', '-p', ROOT / 'apps/android', 'assembleRelease',
        '-Pps2xEngineCMakeLists=' + str(ENGINE / 'CMakeLists.txt'),
        '-Pps2xGameCodegenDir=' + str(a.work / 'codegen'),
        '-Pps2xJniLibsDir=' + str(jnilibs), '-Pps2xPublicNoticesDir=' + str(notices_dir),
        '-Pps2xVu0RecompDir=' + resolve_vu0_stage(a),
        '-Pps2xGamePch=ON',
        '-Pps2xDev=' + ('ON' if getattr(a, 'dev', False) else 'OFF'),
        '-Pps2xPgoData=' + pgo_data, '-Pps2xBootElf=/storage/emulated/0/Android/data/' + a.app_id + '/files/SLUS_207.72',
        '-Pps2xAppId=' + a.app_id,
        '-Pps2xAppLabel=' + resolve_app_label(a),
        '-Pps2xPrebuiltDir=' + (str(bundle) if bundle else ''),
        '-Pps2xJobs=' + str(a.jobs),
        '-Pps2xNativeBuildDir=' + str(a.work / 'android-native-build'),
        '-Pps2xVersionCode=' + str(version_code),
        '-Pps2xKeystoreFile=' + str(keystore),
        '-Pps2xKeystorePassword=' + SIGNING_PASSWORD,
        '-Pps2xKeyAlias=' + SIGNING_ALIAS,
        '-Pps2xKeyPassword=' + SIGNING_PASSWORD,
        '--max-workers=' + str(a.jobs))
    (a.work / 'app-id.txt').write_text(a.app_id + '\n')
    write_android_receipt(a, version_code, pgo_data, turnip_included, jnilibs)


def source(url, revision, destination):
    if not destination.exists():
        run('git', 'clone', '--filter=blob:none', '--no-checkout', url, destination)
        run('git', '-C', destination, 'checkout', '--detach', revision)
    try:
        expected = subprocess.check_output(['git', '-C', str(destination), 'rev-parse', revision + '^{commit}'], text=True).strip()
    except subprocess.CalledProcessError:
        # A blob-less cache from before a vendor repin cannot resolve the new
        # pin: fetch it, then check out.
        run('git', '-C', destination, 'fetch', 'origin', revision)
        run('git', '-C', destination, 'checkout', '--detach', revision)
        expected = subprocess.check_output(['git', '-C', str(destination), 'rev-parse', revision + '^{commit}'], text=True).strip()
    actual = subprocess.check_output(['git', '-C', str(destination), 'rev-parse', 'HEAD'], text=True).strip()
    if actual != expected:
        try:
            run('git', '-C', destination, 'fetch', 'origin', revision)
            run('git', '-C', destination, 'checkout', '--detach', revision)
        except subprocess.CalledProcessError:
            raise ValueError('source pin mismatch: delete ' + str(destination) + ' and re-run')
        actual = subprocess.check_output(['git', '-C', str(destination), 'rev-parse', 'HEAD'], text=True).strip()
        if actual != expected:
            raise ValueError('source pin mismatch: delete ' + str(destination) + ' and re-run')


def libs(a):
    vendor = a.work / 'mac-assembly/vendor-pcsx2'
    source('https://github.com/brad-richardson/armsx2.git',
           'afad5a04c2668a10e12adadaa51d54c46f832236', vendor)
    prefix = a.work / 'deps'
    brew_prefix = subprocess.check_output(['brew', '--prefix'], text=True).strip()
    prefixes = str(prefix) + ';' + brew_prefix
    for name, tag in [('plutovg', 'v1.1.0'), ('plutosvg', 'v0.0.7')]:
        src = a.work / (name + '-source')
        source('https://github.com/sammycage/' + name + '.git', tag, src)
        dst = a.work / (name + '-build')
        run('cmake', '-S', src, '-B', dst, '-G', 'Ninja', '-DCMAKE_BUILD_TYPE=Release',
            '-DCMAKE_INSTALL_PREFIX=' + str(prefix), '-DCMAKE_PREFIX_PATH=' + prefixes,
            '-DBUILD_SHARED_LIBS=ON')
        run('cmake', '--build', dst, '--parallel', a.jobs)
        run('cmake', '--install', dst)
    assembly = vendor.parent
    for src, dest in [(ENGINE / 'ps2xRuntime/third_party/armsx2/ge1', assembly / 'adapter'),
                      (ENGINE / 'ps2xRuntime/third_party/armsx2/mv2', assembly / 'mv2-adapter'),
                      (ROOT / 'tools/ssx3/mac-libs', assembly / 'mac-platform')]:
        shutil.copytree(src, dest, dirs_exist_ok=True)
    link = assembly / 'mac-platform/3rdparty'
    if not link.exists():
        link.symlink_to(vendor / '3rdparty', target_is_directory=True)
    build = a.work / 'libs-build'
    run('cmake', '-S', assembly / 'mac-platform', '-B', build, '-G', 'Ninja',
        '-DCMAKE_BUILD_TYPE=Release', '-DCMAKE_C_COMPILER=/usr/bin/cc',
        '-DCMAKE_CXX_COMPILER=/usr/bin/c++', '-DCMAKE_PREFIX_PATH=' + prefixes,
        '-DPS2RECOMP_SOURCE=' + str(ENGINE), '-DLTO_PCSX2_CORE=OFF')
    run('cmake', '--build', build, '--parallel', a.jobs, '--target', 'ge1_gs', 'mv2_microvu', 'ge1_metallibs')
    resources = a.work / 'resources'
    shutil.copytree(vendor / 'bin/resources', resources, dirs_exist_ok=True)
    for metallib in (build / 'ge1-adapter/metallibs').glob('*.metallib'):
        shutil.copy2(metallib, resources)


def font_pack(a, inputs):
    """Build the raster-edge font pack (on by default; --skip-fonts out).

    Stock atlas pixels come from the user's own texture dump
    (work/stock-dump, or --stock-dump); glyph metrics come from the
    user's own disc image. Returns the pack dir, or None when skipped
    or no dump is present. Code only: remaster_font.py ships no game
    data, images, geometry or atlases.
    """
    if a.skip_fonts:
        return None
    dump = Path(a.stock_dump) if a.stock_dump else a.work / 'stock-dump'
    if not dump.is_dir():
        print('no stock texture dump at ' + str(dump) + '; font pack '
              'skipped (pass --stock-dump with your own dump, or '
              '--skip-fonts to silence this)')
        return None
    out = a.work / 'font-pack'
    subprocess.run([sys.executable, str(ROOT / 'tools/ssx3/remaster_font.py'),
                    '--iso', inputs['iso'], '--stock-dump', str(dump),
                    '--out', str(out)], check=True, cwd=a.work)
    return out


def font_pack_sources(files):
    """(source_path, device_relative) pairs for a staged font pack.

    Pure helper so install() and tests share the layout: every file
    under <files>/font-pack pushes to <device>/font-pack/<name>.
    Returns [] when no pack was staged (dump missing or --skip-fonts).
    """
    staged = files / 'font-pack'
    if not staged.is_dir():
        return []
    return [(p, 'font-pack/' + p.relative_to(staged).as_posix())
            for p in sorted(staged.rglob('*')) if p.is_file()]


TFX_SELECTORS_JSON = ROOT / 'tools/ssx3/tfx-selectors.json'
TFX_SELECTORS_BIN = 'tfx-selectors.bin'


def validate_tfx_selectors(path=None):
    """Fail clearly when the shipped TFX selector coverage is not the
    renderer-specific pipeline-state metadata the prewarm path consumes:
    247 Metal rows of six little-endian u32 words (24 bytes each). Pure
    pipeline-state metadata: no textures, shader code, geometry or guest
    code. Returns the row count."""
    try:
        data = json.loads((Path(path) if path else TFX_SELECTORS_JSON).read_text())
    except (OSError, ValueError):
        raise ValueError('tfx selectors unreadable: ' + str(path or TFX_SELECTORS_JSON))
    if data.get('format') != 'PipelineSelectorMTL little-endian 24-byte words':
        raise ValueError('tfx selectors have an unexpected record format')
    rows = data.get('selectors')
    if not isinstance(rows, list) or len(rows) != 247:
        raise ValueError('tfx selectors must hold exactly 247 Metal rows')
    for row in rows:
        if not isinstance(row, list) or len(row) != 6:
            raise ValueError('tfx selector row must hold six words')
        for word in row:
            try:
                int(word, 16)
            except (TypeError, ValueError):
                raise ValueError('tfx selector word must be a hex string')
    return len(rows)


def stage_tfx_selectors(app):
    """Serialize the shipped Metal selector coverage into the staged iOS
    app bundle (tfx-selectors.bin, 247 x 24 bytes) and return its SHA-256.

    The file lands BEFORE the GE1_TFX_PREWARM preset knob takes effect:
    setting the knob without its file flips the adapter's pipe-flush
    cadence 0 -> 600 with no union coverage to prewarm. Fail loud when
    the coverage or the writer is missing."""
    target = Path(app) / TFX_SELECTORS_BIN
    if target.exists():
        raise ValueError('refusing to overwrite an existing staged selector file: ' + str(target))
    run(sys.executable, str(ROOT / 'tools/ssx3/write_prewarm.py'), str(target))
    if not target.is_file() or target.stat().st_size != 247 * 24:
        raise ValueError('tfx selector staging produced no 247-row coverage file: ' + str(target))
    return sha(target)


def stage_fonts(a, inputs, files, device):
    """Build the font pack into the staged device layout (APK path).

    On by default; --skip-fonts out; skips cleanly when no stock dump
    is present (the ISO carries glyph metrics but no atlas pixels, so
    the pack cannot be built from the disc alone). When a pack is
    built it is copied to <files>/font-pack and files/ps2x.env gains
    GE1_TEX_REPLACE (+ PRELOAD) pointing at the device copy; without
    a pack the env is left untouched so the game boots stock fonts.
    Returns the staged dir, or None when skipped.
    """
    pack = font_pack(a, inputs)
    target = files / 'font-pack'
    if pack is None:
        if target.is_dir():
            shutil.rmtree(target)
        return None
    if target.is_dir():
        shutil.rmtree(target)
    shutil.copytree(pack, target)
    env_file = files / 'ps2x.env'
    lines = [line for line in env_file.read_text().splitlines()
             if not line.startswith('GE1_TEX_REPLACE=')
             and not line.startswith('GE1_TEX_PRELOAD=')]
    lines += ['GE1_TEX_REPLACE=' + device + '/font-pack', 'GE1_TEX_PRELOAD=1']
    env_file.write_text('\n'.join(lines) + '\n')
    return target


def play(a):
    inputs = json.loads((a.work / 'inputs.json').read_text())
    env = os.environ.copy()
    env.update({'PS2X_CD_IMAGE': inputs['slim_iso'], 'PS2X_MC_ROOT': str(a.work / 'mc0'),
        'PS2X_SKIP_MOVIE': '1', 'PS2X_GS_BACKEND': 'external',
        'PS2X_GS_EXTERNAL_LIBRARY': str(a.work / 'libs-build/ge1-adapter/libge1_gs.dylib'),
        'PS2X_MICROVU_LIB': str(a.work / 'libs-build/libmv2_microvu.dylib'),
        'PS2X_VU1_ENGINE': 'microvu', 'PS2X_MTVU': '1', 'PS2X_VU0_RECOMP': '0',
        'GE1_RENDERER': 'metal', 'GE1_GS_RESOURCES_DIR': str(a.work / 'resources'),
        'GE1_GS_DATA_DIR': str(a.work / 'gs-data')})
    pack = font_pack(a, inputs)
    if pack is not None:
        env['GE1_TEX_REPLACE'] = str(pack)
    (a.work / 'gs-data').mkdir(exist_ok=True)
    subprocess.run([str(a.work / 'runner-build/ps2xRuntime/ps2EntryRunner'),
                    str(a.work / 'SLUS_207.72')], env=env, check=True, cwd=a.work)


def support_bundle(a):
    """Verified prebuilt support release, only when explicitly opted in.

    Source libraries are the default (Brad 10-16): `--prebuilt` opts in.
    """
    if not getattr(a, 'prebuilt', False):
        return None
    if getattr(a, 'jnilibs', None):
        # An explicit --jnilibs names the APK's libraries: a release bundle
        # must never silently replace them, so this counts as source.
        print('Explicit --jnilibs given; ignoring prebuilt releases.')
        return None
    if getattr(a, 'app_id', PUBLIC_APP_ID) != PUBLIC_APP_ID:
        print('Custom package ID requires source libraries.')
        return None
    import prebuilt
    return prebuilt.fetch(a.prebuilt_cache or a.work / 'prebuilt-cache', 'auto')


# The vendor recipe (build-dependencies.sh at the VENDOR pin) clones these by
# tag, and tags are mutable: the lane copy checks every clone out to the
# recorded commit before building, and records the resolved set for
# notices/SOURCES.json. Update this table together with the vendor pin.
# Commits were resolved from the tags with git ls-remote (peeled ^{}) on
# 2026-10-15; shaderc's glslang stays pinned inside the vendor script.
ANDROID_DEPS = [
    ('libpng', 'https://github.com/pnggroup/libpng', 'v1.6.51',
     '49363adcfaf098748d7a4c8c624ad8c45a8c3a86'),
    ('libjpeg-turbo', 'https://github.com/libjpeg-turbo/libjpeg-turbo', '3.1.4.1',
     '9217719d3a58633923b096af4c1d50d304768a64'),
    ('zstd', 'https://github.com/facebook/zstd', 'v1.5.7',
     'f8745da6ff1ad1e7bab384bd1f9d742439278e99'),
    ('lz4', 'https://github.com/lz4/lz4', 'v1.10.0',
     'ebb370ca83af193212df4dcbadcc5d87bc0de2f0'),
    ('libwebp', 'https://github.com/webmproject/libwebp', 'v1.6.0',
     '4fa21912338357f89e4fd51cf2368325b59e9bd9'),
    ('sdl3', 'https://github.com/libsdl-org/SDL', 'release-3.2.26',
     'badbf8da4ee72b3ef599c721ffc9899e8d7c8d90'),
    ('freetype', 'https://github.com/freetype/freetype', 'VER-2-14-1',
     '526ec5c47b9ebccc4754c85ac0c0cdf7c85a5e9b'),
    ('plutovg', 'https://github.com/sammycage/plutovg', 'v1.3.2',
     '5695a711dd1cff1f01fa6542f3fe6a15de082c63'),
    ('plutosvg', 'https://github.com/sammycage/plutosvg', 'v0.0.7',
     '31f7d2675416cd777c8e86220b035364873b2a8b'),
    ('curl', 'https://github.com/curl/curl', 'curl-8_11_1',
     '75a2079d5c28debb2eaa848ca9430f1fe0d7844c'),
    ('libpcap', 'https://github.com/the-tcpdump-group/libpcap', 'libpcap-1.10.5',
     'bbcbc9174df3298a854daee2b3e666a4b6e5383a'),
    ('shaderc', 'https://github.com/google/shaderc', 'v2025.4',
     '73743588fe9c39f2f1c780a087d94afac691a189'),
]


def pin_android_deps(deps_root):
    """Check every vendor dependency clone out to its pinned commit.

    Runs before the recipe builds, so a moved tag or a stale cache can never
    select the code: the tag clone is only the fetch vehicle. Returns the
    resolved {dir: commit} map and records it beside the clones.
    """
    deps_root.mkdir(parents=True, exist_ok=True)
    resolved = {}
    for name, url, tag, commit in ANDROID_DEPS:
        d = deps_root / name
        if not d.is_dir():
            run('git', 'clone', '--depth=1', '--branch', tag, '--recursive', url, d)
        actual = subprocess.check_output(['git', '-C', str(d), 'rev-parse', 'HEAD'],
                                         text=True).strip()
        if actual != commit:
            run('git', '-C', str(d), 'fetch', 'origin', commit)
            run('git', '-C', str(d), 'checkout', '--detach', commit)
            actual = subprocess.check_output(['git', '-C', str(d), 'rev-parse', 'HEAD'],
                                             text=True).strip()
        if actual != commit:
            raise ValueError('dependency pin mismatch: ' + str(d))
        resolved[name] = actual
    (deps_root / 'android-dependency-pins.json').write_text(
        json.dumps(resolved, indent=2, sort_keys=True) + '\n')
    return resolved


# D01: Android GE1 vendor = the shipped pin (the same tree as the iOS
# GE1_VENDOR_PIN below): blend-constant re-emission, explicit-LINEAR AHB
# import, font/word prescale and the icon-row/default-on commits. microVU
# stays no-LTO (android_libs builds the shared tree with LTO_PCSX2_CORE=OFF).
ANDROID_GE1_VENDOR_REPO = 'https://github.com/brad-richardson/armsx2.git'
ANDROID_GE1_VENDOR_PIN = 'e8ed655ec66eeba12ae3029cc26b45927964b476'
ANDROID_GE1_VENDOR_SHORT = 'e8ed655ec6'


def android_libs(a):
    ensure_android_toolchain(a)
    bundle = support_bundle(a)
    if bundle is not None:
        # Stage precisely the verified files; retain the existing command sequence.
        shutil.copytree(bundle / 'jniLibs', a.work / 'jniLibs', dirs_exist_ok=True)
        shutil.copytree(bundle / 'resources', a.work / 'android-resources', dirs_exist_ok=True)
        return
    if not a.ndk or not (a.ndk / 'build/cmake/android.toolchain.cmake').is_file():
        raise ValueError('android-libs requires --ndk (NDK 28.2.13676358)')
    vendor = a.work / 'android-assembly/vendor-pcsx2'
    # D01: the shipped GE1 pin (ANDROID_GE1_VENDOR_PIN, same tree the iOS
    # build uses): blend-constant re-emission, explicit-LINEAR AHB import,
    # font/word prescale and the icon-row/default-on commits. microVU stays
    # no-LTO (LTO_PCSX2_CORE=OFF below covers the shared GE1+microVU build).
    source(ANDROID_GE1_VENDOR_REPO, ANDROID_GE1_VENDOR_PIN, vendor)
    prefix = a.work / 'android-deps'
    prefix.mkdir(exist_ok=True)
    env = os.environ.copy()
    env.update({'ANDROID_NDK': str(a.ndk.resolve()), 'ANDROID_ABI': 'arm64-v8a', 'ANDROID_API': '29'})
    # Bound the pinned recipe's host-core parallelism to the user's build budget.
    recipe = (vendor / '.github/workflows/scripts/android/build-dependencies.sh').read_text()
    marker = 'NPROCS="$(getconf _NPROCESSORS_ONLN)"'
    if recipe.count(marker) != 1:
        raise ValueError('dependency recipe parallelism changed; review the vendor pin')
    glslang = re.search(r'^SHADERC_GLSLANG=([0-9a-f]{40})$', recipe, re.M)
    if not glslang:
        raise ValueError('dependency recipe glslang pin changed; review the vendor pin')
    # Tags are mutable: seat every clone on its recorded commit before the
    # recipe builds (a moved tag only changes what gets fetched, never what
    # gets built). The recipe still pins glslang itself; verify that below.
    pin_android_deps(a.work / 'deps-build')
    recipe_path = a.work / 'android-dependencies.sh'
    recipe_path.write_text(recipe.replace(marker, 'NPROCS=' + str(a.jobs)))
    subprocess.run(['bash', str(recipe_path), str(prefix)], env=env, cwd=a.work, check=True)
    actual_glslang = subprocess.check_output(
        ['git', '-C', str(a.work / 'deps-build/shaderc/third_party/glslang'),
         'rev-parse', 'HEAD'], text=True).strip()
    if actual_glslang != glslang[1]:
        raise ValueError('shaderc glslang pin mismatch: ' + actual_glslang)
    assembly = vendor.parent
    for src, dest in [(ENGINE / 'ps2xRuntime/third_party/armsx2/ge1', assembly / 'adapter'),
                      (ENGINE / 'ps2xRuntime/third_party/armsx2/mv2', assembly / 'mv2-adapter'),
                      (ROOT / 'tools/ssx3/android-libs', assembly / 'android-platform')]:
        shutil.copytree(src, dest, dirs_exist_ok=True)
    shutil.copy2(ENGINE / 'ps2xRuntime/third_party/armsx2/platform/android/ax4b_android_stubs.cpp',
                 assembly / 'adapter/ax4b_android_stubs.cpp')
    for name, target in [('3rdparty', vendor / '3rdparty'), ('pcsx2', vendor / 'pcsx2'),
                         ('tests', vendor / 'tests'), ('common', vendor / 'common')]:
        link = assembly / ('android-platform/3rdparty' if name == '3rdparty' else name)
        if not link.exists():
            link.symlink_to(target, target_is_directory=True)
    build = a.work / 'android-libs-build'
    run('cmake', '-S', assembly / 'android-platform', '-B', build, '-G', 'Ninja',
        '-DCMAKE_BUILD_TYPE=Release', '-DCMAKE_TOOLCHAIN_FILE=' + str(a.ndk.resolve() / 'build/cmake/android.toolchain.cmake'),
        '-DANDROID_ABI=arm64-v8a', '-DANDROID_PLATFORM=android-29', '-DANDROID_STL=c++_static',
        '-DCMAKE_PREFIX_PATH=' + str(prefix), '-DCMAKE_FIND_ROOT_PATH=' + str(prefix),
        '-DLTO_PCSX2_CORE=OFF')
    run('cmake', '--build', build, '--parallel', a.jobs, '--target', 'ge1_gs', 'mv2_microvu')
    jni = a.work / 'jniLibs/arm64-v8a'
    jni.mkdir(parents=True, exist_ok=True)
    shutil.copy2(build / 'ge1-adapter/libge1_gs.so', jni)
    shutil.copy2(build / 'mv2-adapter/libmv2_microvu.so', jni)
    shutil.copytree(vendor / 'bin/resources', a.work / 'android-resources', dirs_exist_ok=True)


# --- iOS device build (ios-builder-1). Same shape as the Android flow:
# doctor checks, resumable ios-libs, an `ios` app step, and a one-command
# `ios --ssx3-iso` (doctor -> prepare -> ios-libs -> app, then --install).
# macOS Apple silicon only. Game sources come from the user's own disc via
# prepare; the offline VU1 stage defaults to work/om1-stage, generated from
# that disc by the ios-om1 step (manifest-driven om1_replay), with --om1-stage
# kept as an override.

GE1_VENDOR_REPO = 'https://github.com/brad-richardson/armsx2.git'
GE1_VENDOR_PIN = 'e8ed655ec66eeba12ae3029cc26b45927964b476'
GE1_VENDOR_SHORT = 'e8ed655ec6'
# OM1 archive vendor: the commit that embedded the microVU/GIF test hooks
# (10-02). The stage references the full hook set, so the island must define
# it; older trees (including the pre-growth parent 35213339f) miss three
# hook globals and fail the final link undefined. This tree carries the
# 09-25 MTGS/GS growth too, which pulls GS members into the island -- but
# under the pinned -fno-common they demote behind the exports list exactly
# like the lab island's own pulled-but-hidden members, so Check C still
# passes (validated by the close, not by inspection). An ancestor of the GE1
# tip so it fetches the same way. GE1 stays on the new pin.
OM1_VENDOR_REPO = GE1_VENDOR_REPO
OM1_VENDOR_PIN = '70a478c1797ac8da62d3c12e6c12cd2bc3c43f28'
OM1_VENDOR_SHORT = '70a478c17'

# OM1 replay vendor: the om1-replay series on top of OM1_VENDOR_PIN
# (recorder sources + om1_replay driver + f-list tail fix). The ios-om1 step
# fetches this series and rebuilds the offline VU1 stage from the user's disc.
OM1_REPLAY_REPO = OM1_VENDOR_REPO
OM1_REPLAY_PIN = '4daed3bf635c49570ba9354a25a6634699a8a323'
OM1_REPLAY_SHORT = '4daed3bf6'
OM1_AREAS = ('sp1', 'mid', 'full', 'menus', 'late', 'sv1', 'grav', 'rb', 'jx')
OM1_MANIFEST = ROOT / 'tools/ssx3/ios/om1-manifest.json'
OM1_OFFLINE_CORE = ('OfflineCore.cpp', 'OfflineBridge.cpp', 'OfflineCore.h', 'OM1StubHost.cpp')

# Vendored iOS dependency sources missing from the vendor tree (the rest of
# the GE1_IOS_DEPS set ships inside it). Same-generation pins as the
# Android table where the dependency overlaps; seated on commits because
# tags are mutable.
IOS_DEPS = [
    ('libpng', 'https://github.com/pnggroup/libpng', 'v1.6.51',
     '49363adcfaf098748d7a4c8c624ad8c45a8c3a86'),
    ('libjpeg-turbo', 'https://github.com/libjpeg-turbo/libjpeg-turbo', '3.1.90',
     'e1dbfa7be7b7e54922020051dc77781e92739700'),
    ('zstd', 'https://github.com/facebook/zstd', 'v1.5.7',
     'f8745da6ff1ad1e7bab384bd1f9d742439278e99'),
    ('lz4', 'https://github.com/lz4/lz4', 'v1.10.0',
     'ebb370ca83af193212df4dcbadcc5d87bc0de2f0'),
    ('libwebp', 'https://github.com/webmproject/libwebp', 'v1.6.0',
     '4fa21912338357f89e4fd51cf2368325b59e9bd9'),
    ('freetype', 'https://github.com/freetype/freetype', 'VER-2-14-1',
     '526ec5c47b9ebccc4754c85ac0c0cdf7c85a5e9b'),
    ('sdl3', 'https://github.com/libsdl-org/SDL', 'release-3.2.26',
     'badbf8da4ee72b3ef599c721ffc9899e8d7c8d90'),
]
IOS_DEPS_TREE = ('rapidyaml', 'plutovg', 'plutosvg')

SDL2_TAG = 'release-2.32.10'
SDL2_TARBALL = ('https://github.com/libsdl-org/SDL/releases/download/'
                'release-2.32.10/SDL2-2.32.10.tar.gz')
SDL2_TARBALL_SHA = '5f5993c530f084535c65a6879e9b26ad441169b3e25d789d83287040a9ca5165'

FFMPEG_TARBALL = 'https://ffmpeg.org/releases/ffmpeg-7.1.1.tar.xz'
FFMPEG_TARBALL_SHA = '733984395e0dbbe5c046abda2dc49a5544e7e0e1e2366bba849222ae9e3a03b1'

# OM1 stage files the engine requires (ps2xRuntime/CMakeLists.txt OM1 block).
OM1_STAGE_REQUIRED = ('OfflineCore.cpp', 'OfflineBridge.cpp', 'OM1StubHost.cpp',
                      'armsx2/pcsx2/Config.h', 'armsx2/tests/ctest/core/StubHost.cpp')


def default_bundle_id(team):
    """Per-team bundle id: unique per signer, never collides across users."""
    return 'com.ssx3recomp.' + team.lower()


def check_team(a):
    team = getattr(a, 'team', None)
    if not team:
        raise ValueError('ios needs --team <APPLE_TEAM_ID> (your Apple Developer Team ID; '
                         'find it at developer.apple.com/account or in Xcode Settings > Accounts)')
    if not re.fullmatch(r'[A-Z0-9]{10}', team):
        raise ValueError('--team must be the 10-character Apple Team ID (got ' + team + ')')
    return team


def check_bundle_id(value):
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]*(?:\.[A-Za-z][A-Za-z0-9_-]*)+', value):
        raise ValueError('--bundle-id must be a dotted bundle identifier (got ' + value + ')')
    return value


def profile_certificates(profile):
    """SHA-1 fingerprints of a provisioning profile's developer certificates."""
    import hashlib
    import plistlib
    import subprocess
    raw = subprocess.run(['security', 'cms', '-D', '-i', str(profile)],
                         stdout=subprocess.PIPE, check=False).stdout
    certs = plistlib.loads(raw).get('DeveloperCertificates', [])
    return {hashlib.sha1(bytes(der)).hexdigest().upper() for der in certs}


def signing_identity(team, profile=None):
    """Fingerprint of the user's codesigning identity for this team.

    When the provisioning profile is known, the identity must be one of
    its certificates: signing with any other identity (e.g. a
    Distribution certificate under an unrelated team) produces an app
    the device refuses with 0xe8008015. Without a profile, any
    identity issued for the team is accepted (doctor-time probe)."""
    ok, detail = probe(['security', 'find-identity', '-v', '-p', 'codesigning'])
    if not ok:
        raise ValueError('cannot list signing identities: ' + detail)
    identities = {}  # fingerprint -> full name
    for line in detail.splitlines():
        match = re.search(r'^\s*\d+\)\s+([0-9A-F]+)\s+"([^"]+)"', line)
        if match:
            identities[match[1]] = match[2]
    if profile is not None:
        allowed = profile_certificates(profile)
        for fingerprint, name in identities.items():
            if fingerprint in allowed:
                return fingerprint
        raise ValueError('none of the keychain identities is in provisioning profile '
                         + str(profile) + ' (profile certs: ' + ', '.join(sorted(allowed))
                         + '); let Xcode refresh automatic signing for the bundle id')
    for fingerprint, name in identities.items():
        if '(' + team + ')' in name:
            return fingerprint
    raise ValueError('no signing identity for team ' + team + ' in the keychain; '
                     'sign into Xcode with your Apple ID (Xcode > Settings > Accounts) '
                     'and let Xcode create the Apple Development certificate')


def doctor_ios(a, check):
    """iOS doctor checks. check(label, ok, detail, fix) mirrors doctor()."""
    for tool in ('clang', 'metal', 'metallib'):
        ok, detail = probe(['xcrun', '--find', tool])
        check('Xcode ' + tool, ok, detail, 'install Xcode and its Metal toolchain')
    ok, detail = probe(['cmake', '--version'])
    check('cmake', ok, detail.splitlines()[0] if detail else 'missing',
          'install CMake (brew install cmake)')
    team = getattr(a, 'team', None)
    if team:
        try:
            fingerprint = signing_identity(team)
            check('signing identity', True, team + ' ' + fingerprint,
                  'sign into Xcode with your Apple ID')
        except ValueError as error:
            check('signing identity', False, str(error),
                  'sign into Xcode with your Apple ID (Xcode > Settings > Accounts); '
                  'free Apple ID personal teams work, re-sign weekly')
    else:
        print('SKIP signing identity: pass --team <APPLE_TEAM_ID> to check it')
    if getattr(a, 'device', None):
        try:
            resolve_device(getattr(a, 'device', None), getattr(a, 'install', False))
            check('iOS device', True, str(getattr(a, 'device', None)), '')
        except ValueError as error:
            check('iOS device', False, str(error),
                  'connect the device over USB, trust the computer, and pass its UDID '
                  '(xcrun devicectl list devices)')
    elif getattr(a, 'install', False):
        # F14: default --install resolves its target here, not at install time.
        try:
            found = resolve_device(None, for_install=True)
            check('iOS device', True, found, '')
        except ValueError as error:
            check('iOS device', False, str(error),
                  'connect one device over USB, trust the computer, enable '
                  'Developer Mode, and list it with (xcrun devicectl list devices)')


def pin_ios_deps(deps_root):
    """Clone the iOS dependency sources and seat each on its pinned commit."""
    deps_root.mkdir(parents=True, exist_ok=True)
    resolved = {}
    for name, url, tag, commit in IOS_DEPS:
        d = deps_root / name
        if not d.is_dir():
            run('git', 'clone', '--depth=1', '--branch', tag, url, d)
        actual = subprocess.check_output(['git', '-C', str(d), 'rev-parse', 'HEAD'],
                                         text=True).strip()
        if actual != commit:
            if name == 'libjpeg-turbo':
                # A re-pin must not trip over our own vendored-build edits:
                # reset the patched file when the only dirt is that file
                # carrying our marker (either edit generation reapplies both).
                dirty = subprocess.check_output(['git', '-C', str(d), 'status', '--porcelain'],
                                                text=True).strip()
                patched = (d / 'CMakeLists.txt').read_text()
                if dirty == 'M CMakeLists.txt' and JPEG_GUARD_MARKER in patched:
                    run('git', '-C', str(d), 'checkout', '--', 'CMakeLists.txt')
                elif dirty:
                    raise ValueError('dependency tree dirty (not our patch): ' + str(d))
            run('git', '-C', str(d), 'fetch', 'origin', commit)
            run('git', '-C', str(d), 'checkout', '--detach', commit)
            actual = subprocess.check_output(['git', '-C', str(d), 'rev-parse', 'HEAD'],
                                             text=True).strip()
        if actual != commit:
            raise ValueError('dependency pin mismatch: ' + str(d))
        if name == 'libjpeg-turbo':
            unguard_libjpeg_turbo(d)
        if name == 'zstd':
            # The engine consumes the dep root via add_subdirectory, but
            # upstream keeps the cmake project under build/cmake: stage the
            # lab's thin superbuild wrapper (tools/ssx3/ios/) at the root.
            shutil.copy2(ROOT / 'tools/ssx3/ios/zstd-superbuild-CMakeLists.txt',
                         d / 'CMakeLists.txt')
        resolved[name] = actual
    (deps_root / 'ios-dependency-pins.json').write_text(
        json.dumps(resolved, indent=2, sort_keys=True) + '\n')
    return resolved


# The lab's vendored libjpeg-turbo carries exactly one downstream
# modification: the upstream add_subdirectory() FATAL is removed (their
# marker: "add_subdirectory guard removed for vendored build"). The engine
# consumes every iOS dep via add_subdirectory, so the public builder
# applies the same removal after seating the pin; any upstream drift in
# the guarded text fails loud instead of silently building something else.
JPEG_GUARD = ('if(NOT CMAKE_SOURCE_DIR STREQUAL CMAKE_CURRENT_SOURCE_DIR)\n'
              '  message(FATAL_ERROR "The ${CMAKE_PROJECT_NAME} build system cannot be '
              'integrated into another build system using add_subdirectory().  '
              'Use ExternalProject_Add() instead.")\n'
              'endif()')
JPEG_GUARD_MARKER = '# add_subdirectory guard removed for vendored build'
JPEG_UNINSTALL = ('add_custom_target(uninstall '
                  'COMMAND ${CMAKE_COMMAND} -P cmake_uninstall.cmake)')
JPEG_UNINSTALL_SUBDIR = ('if(CMAKE_SOURCE_DIR STREQUAL CMAKE_CURRENT_SOURCE_DIR)\n'
                         '  add_custom_target(uninstall '
                         'COMMAND ${CMAKE_COMMAND} -P cmake_uninstall.cmake)\n'
                         'endif()')


def unguard_libjpeg_turbo(dep_dir):
    """Apply the lab's two vendored-build edits: remove upstream's
    add_subdirectory() FATAL and keep the uninstall target top-level-only
    (both verified by diff against upstream 3.1.90)."""
    cmake_lists = Path(dep_dir) / 'CMakeLists.txt'
    text = cmake_lists.read_text()
    changed = False
    if JPEG_GUARD_MARKER not in text:
        if JPEG_GUARD not in text:
            raise ValueError('libjpeg-turbo guard text changed; review the vendored-build patch')
        text = text.replace(JPEG_GUARD, JPEG_GUARD_MARKER)
        changed = True
    if JPEG_UNINSTALL_SUBDIR not in text:
        if JPEG_UNINSTALL not in text:
            raise ValueError('libjpeg-turbo uninstall text changed; review the vendored-build patch')
        text = text.replace(JPEG_UNINSTALL, JPEG_UNINSTALL_SUBDIR)
        changed = True
    if changed:
        cmake_lists.write_text(text)
    return changed


def fetch_tarball(url, want_sha, dest, want_bytes=None):
    """Fetch a pinned release tarball; any size or SHA mismatch fails closed."""
    dest = Path(dest)
    if dest.is_file() and sha(dest) == want_sha:
        return dest
    tmp = dest.with_name(dest.name + '.download')
    tmp.unlink(missing_ok=True)
    try:
        print('+ fetch ' + url)
        with urllib.request.urlopen(url, timeout=120) as src, tmp.open('wb') as out:
            shutil.copyfileobj(src, out)
        if want_bytes is not None and tmp.stat().st_size != want_bytes:
            raise ValueError('size mismatch for ' + url)
        if sha(tmp) != want_sha:
            raise ValueError('SHA-256 mismatch for ' + url)
        tmp.replace(dest)
        return dest
    finally:
        tmp.unlink(missing_ok=True)


def extract_once(archive, mode, dest, marker):
    """Extract a tarball once: skip when the marker exists, otherwise wipe
    the destination and extract fresh (a crashed run must never resume a
    half-extracted tree)."""
    dest = Path(dest)
    if (dest / marker).exists():
        return dest
    if dest.is_dir() or dest.is_symlink():
        shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    with tarfile.open(archive, 'r:' + mode) as t:
        members = t.getnames()
        if any(n.startswith('/') or '..' in Path(n).parts for n in members):
            raise ValueError('unsafe archive path in ' + str(archive))
        t.extractall(dest)
    if not (dest / marker).exists():
        raise ValueError('archive layout changed in ' + str(archive))
    return dest


def validate_om1_stage(path):
    """Fail clearly when the user's offline VU1 stage is incomplete."""
    stage = Path(path)
    if not stage.is_dir():
        raise ValueError('--om1-stage must be a directory holding your locally generated '
                         'offline VU1 tables (got ' + str(path) + '); see '
                         'tools/ssx3/offline-core/README.md')
    missing = [name for name in OM1_STAGE_REQUIRED if not (stage / name).is_file()]
    if missing:
        raise ValueError('--om1-stage is missing: ' + ', '.join(missing))
    if not list(stage.glob('*.s')) or not list(stage.glob('*_tables.c')):
        raise ValueError('--om1-stage needs the recorded assembly (*.s) and tables (*_tables.c); '
                         'the portable recording recipe is still incomplete, so generate the '
                         'stage with the lane tooling (see tools/ssx3/offline-core/README.md)')
    return stage


def assemble_bundle(bundle_dir, vendor, deps_src):
    """Assemble a GE1_IOS_DEPS bundle: fetched sources plus the three that
    ship inside the given vendor tree (fail loud when a tree dep moves)."""
    bundle_dir = Path(bundle_dir)
    for name, _, _, _ in IOS_DEPS:
        link = bundle_dir / name
        if link.is_symlink() or link.exists():
            link.unlink() if link.is_symlink() else shutil.rmtree(link)
        bundle_dir.mkdir(parents=True, exist_ok=True)
        link.symlink_to(Path(deps_src) / name, target_is_directory=True)
    for name in IOS_DEPS_TREE:
        target = Path(vendor) / '3rdparty' / name
        if not target.is_dir():
            raise ValueError('vendor tree moved: 3rdparty/' + name + ' missing under ' + str(vendor))
        link = bundle_dir / name
        if link.is_symlink() or link.exists():
            link.unlink() if link.is_symlink() else shutil.rmtree(link)
        link.symlink_to(target, target_is_directory=True)
    return bundle_dir


def archive_build_env(bundle, extra_cflags=''):
    """Environment for the iOS archive builds. -fno-common is load-bearing:
    tentative definitions must come out as defined BSS (the shape every
    shipped pin has), never COMMONs: the OM1 island close demotes defined
    globals behind its exports list but COMMONs stay global and trip the
    GE1-intersection check. The current Xcode toolchain defaults to
    -fcommon, so pin it explicitly instead of inheriting the default."""
    env = os.environ.copy()
    env['SSX3_IOS_DEPS'] = str(bundle)
    for key in ('CFLAGS', 'CXXFLAGS'):
        env[key] = (env.get(key, '') + ' -fno-common ' + extra_cflags).strip()
    return env


def run_vendor_build(vendor, bundle, work, jobs, label, extra_cflags=''):
    """GE1 + PCSX2 static archives via the pinned engine recipe
    (ps2xRuntime/third_party/armsx2/build.sh ios). Returns the build dir."""
    env = archive_build_env(bundle, extra_cflags)
    build_sh = ENGINE / 'ps2xRuntime/third_party/armsx2/build.sh'
    proc = subprocess.run(['bash', str(build_sh), 'ios', '--armsx2', str(vendor),
                           '--build-root', str(work), '--jobs', str(jobs)],
                          env=env, cwd=str(work), text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    print(proc.stdout)
    if proc.returncode:
        raise ValueError(label + ' iOS archive build failed (see output above)')
    match = re.search(r'^BUILD_DIR=(.+)$', proc.stdout, re.M)
    if not match:
        raise ValueError(label + ' iOS archive build printed no BUILD_DIR')
    build_dir = Path(match[1].strip())
    for name in ('ge1-adapter/libge1_gs.a', 'pcsx2/libPCSX2.a', 'common/libcommon.a'):
        if not (build_dir / name).is_file():
            raise ValueError(label + ' iOS archive build is missing ' + name)
    return build_dir


def newest_build_dir(work):
    """Newest ios-build-* stamp dir (harvest fallback, not a resume key)."""
    candidates = sorted(Path(work).glob('ios-build-*'))
    if not candidates:
        raise ValueError('no ios-build-* directory under ' + str(work))
    return candidates[-1]


def run_om1_build(vendor, bundle, work, jobs):
    """OM1 archives from the pre-growth vendor. Its ge1_gs target cannot
    link (the current adapter needs new-core symbols the old tree lacks),
    so on that expected failure finish everything else with ninja -k 0 and
    harvest the OM1_LIB inputs. The harvest only accepts a stamp dir
    modified by this run, never a stale one (which may be the GE1 set
    that cannot close). PCSX2_RECOMPILER_TESTS is on for this run only:
    the stage is compiled with it and references the hook globals, so the
    island must define them; the GE1 side must not (Check C forbids the
    intersection)."""
    start = time.time()
    try:
        return run_vendor_build(vendor, bundle, work, jobs, 'OM1',
                                extra_cflags='-DPCSX2_RECOMPILER_TESTS')
    except ValueError as err:
        build_dir = newest_build_dir(work)
        if build_dir.stat().st_mtime < start:
            raise err
    proc = subprocess.run(['ninja', '-C', str(build_dir), '-k', '0', '-j', str(jobs)],
                          text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          check=False)
    print(proc.stdout)
    failed = [line for line in proc.stdout.splitlines() if line.startswith('FAILED')]
    if any('ge1-adapter' not in line for line in failed):
        raise ValueError('OM1 archive build has unexpected failures: '
                         + '; '.join(line[:160] for line in failed if 'ge1-adapter' not in line))
    missing = [name for name in ('pcsx2/libPCSX2.a', 'common/libcommon.a')
               if not (build_dir / name).is_file()]
    if proc.returncode and not missing:
        print('OM1: ge1_gs failed as predicted (adapter needs new-core symbols); '
              'harvested PCSX2 archives from ' + str(build_dir))
        return build_dir
    if missing or proc.returncode:
        raise ValueError('OM1 archive build failed without usable archives'
                         + (': ' + ', '.join(missing) if missing else ''))
    return build_dir


def prune_om1_copy(build_dir, dest):
    """Copy an OM1 archive set into the lab-pin layout: common/, pcsx2/ and
    3rdparty/ without the SDL set (the engine excludes it) and without the
    GE1 adapter (the island must never contain GE1 members)."""
    build_dir, dest = Path(build_dir), Path(dest)
    if dest.is_dir():
        shutil.rmtree(dest)
    for name in ('common', 'pcsx2'):
        src = build_dir / name
        if not src.is_dir():
            raise ValueError('OM1 archive build is missing ' + name)
        (dest / name).mkdir(parents=True)
        for archive in sorted(src.glob('*.a')):
            shutil.copy2(archive, dest / name / archive.name)
        if (src / 'include').is_dir():
            shutil.copytree(src / 'include', dest / name / 'include')
    for archive in sorted((build_dir / '3rdparty').rglob('*.a')):
        rel = archive.relative_to(build_dir)
        if 'sdl3' in rel.parts or '._' in archive.name:
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(archive, target)
    if not (dest / 'pcsx2/libPCSX2.a').is_file():
        raise ValueError('OM1 archive copy is missing pcsx2/libPCSX2.a')
    return dest


def ios_libs_current(work):
    """Whether ios-libs' staged archives are present (resume skip).

    F16: the ios-libs.json receipt pins must also match; a drifted pin
    rebuilds instead of silently reusing stale archives.
    """
    libs = work / 'ios-libs'
    if not ((libs / 'libge1_gs.a').is_file() and (libs / 'libPCSX2.a').is_file()
            and (libs / 'libcommon.a').is_file()
            and (libs / 'om1/pcsx2/libPCSX2.a').is_file()
            and (libs / 'libSDL2.a').is_file()
            and (libs / 'MoltenVK.framework').is_dir()
            and (work / 'ios-ffmpeg/lib/libavcodec.a').is_file()):
        return False
    try:
        receipt = json.loads((work / 'ios-libs.json').read_text())
    except (OSError, ValueError):
        return True
    for key, want in (('ge1_vendor', GE1_VENDOR_PIN), ('om1_vendor', OM1_VENDOR_PIN),
                      ('sdl2_tarball_sha256', SDL2_TARBALL_SHA),
                      ('ffmpeg_tarball_sha256', FFMPEG_TARBALL_SHA)):
        if key in receipt and receipt[key] != want:
            print('ios-libs: ' + key + ' changed, rebuilding')
            return False
    recorded_deps = receipt.get('ios_dep_pins') or {}
    for name, _, _, commit in IOS_DEPS:
        if name in recorded_deps and recorded_deps[name] != commit:
            print('ios-libs: dependency ' + name + ' pin changed, rebuilding')
            return False
    return True


def ios_libs(a):
    vendor = a.work / 'ios-vendor'
    source(GE1_VENDOR_REPO, GE1_VENDOR_PIN, vendor)
    actual = subprocess.check_output(['git', '-C', str(vendor), 'rev-parse', 'HEAD'],
                                     text=True).strip()
    if actual != GE1_VENDOR_PIN:
        raise ValueError('GE1 vendor pin mismatch: ' + actual)
    # Fail loud while the vendor is fresh in mind: the upscale flip, the
    # Metal shader count and the font count the app configure asserts.
    marker = vendor / 'pcsx2/GS/Renderers/HW/GSTextureOnDeviceUpscale.cpp'
    if 'PrescaleTargetLocked' not in marker.read_text():
        raise ValueError('GE1 vendor changed: prescale code missing at ' + GE1_VENDOR_SHORT)
    metal = list((vendor / 'pcsx2/GS/Renderers/Metal').glob('*.metal'))
    if len(metal) != 8:
        raise ValueError('GE1 vendor changed: expected 8 .metal shaders, found ' + str(len(metal)))
    fonts = [p for p in (vendor / 'bin/resources/fonts').glob('*')
             if p.suffix in ('.ttf', '.otf') and p.is_file()]
    if len(fonts) != 5:
        raise ValueError('GE1 vendor changed: expected 5 PCSX2 fonts, found ' + str(len(fonts)))
    om1_vendor = a.work / 'ios-vendor-om1'
    source(OM1_VENDOR_REPO, OM1_VENDOR_PIN, om1_vendor)
    om1_actual = subprocess.check_output(['git', '-C', str(om1_vendor), 'rev-parse', 'HEAD'],
                                         text=True).strip()
    if om1_actual != OM1_VENDOR_PIN:
        raise ValueError('OM1 vendor pin mismatch: ' + om1_actual)
    resolved = pin_ios_deps(a.work / 'ios-deps-src')
    # One GE1_IOS_DEPS bundle per vendor tree (the tree links differ).
    bundle_ge1 = assemble_bundle(a.work / 'ios-deps-bundle-ge1', vendor, a.work / 'ios-deps-src')
    bundle_om1 = assemble_bundle(a.work / 'ios-deps-bundle-om1', om1_vendor, a.work / 'ios-deps-src')
    # SDL2 for iOS (the app links SDL2main + libSDL2.a via raylib's SDL platform).
    sdl_src = a.work / 'ios-sdl2-src'
    fetch_tarball(SDL2_TARBALL, SDL2_TARBALL_SHA, a.work / 'ios-sdl2.tar.gz')
    extract_once(a.work / 'ios-sdl2.tar.gz', 'gz', sdl_src, 'SDL2-2.32.10/CMakeLists.txt')
    roots = [p for p in sdl_src.iterdir() if p.is_dir()]
    if len(roots) != 1:
        raise ValueError('SDL2 layout changed in ' + SDL2_TARBALL)
    sdl_prefix = a.work / 'ios-sdl2'
    run('cmake', '-S', roots[0], '-B', a.work / 'ios-sdl2-build', '-G', 'Ninja',
        '-DCMAKE_BUILD_TYPE=Release', '-DCMAKE_SYSTEM_NAME=iOS',
        '-DCMAKE_OSX_SYSROOT=iphoneos', '-DCMAKE_OSX_ARCHITECTURES=arm64',
        '-DCMAKE_OSX_DEPLOYMENT_TARGET=17.0', '-DSDL_SHARED=OFF', '-DSDL_STATIC=ON',
        '-DCMAKE_INSTALL_PREFIX=' + str(sdl_prefix))
    run('cmake', '--build', a.work / 'ios-sdl2-build', '--parallel', a.jobs)
    run('cmake', '--install', a.work / 'ios-sdl2-build')
    # Minimal FFmpeg 7.1.1 for iOS, verbatim from the engine recipe
    # (ps2xRuntime/cmake/iOS-FFmpeg-7.1.1.md): MPEG-1/2 decode for movies.
    ff_src = a.work / 'ios-ffmpeg-src'
    fetch_tarball(FFMPEG_TARBALL, FFMPEG_TARBALL_SHA, a.work / 'ios-ffmpeg.tar.xz')
    extract_once(a.work / 'ios-ffmpeg.tar.xz', 'xz', ff_src, 'ffmpeg-7.1.1/configure')
    ff_roots = [p for p in ff_src.iterdir() if p.is_dir()]
    if len(ff_roots) != 1:
        raise ValueError('FFmpeg layout changed in ' + FFMPEG_TARBALL)
    ff_prefix = a.work / 'ios-ffmpeg'
    sdk = subprocess.check_output(['xcrun', '--sdk', 'iphoneos', '--show-sdk-path'],
                                  text=True).strip()
    configure_cmd = ['./configure', '--target-os=darwin', '--arch=aarch64', '--cpu=generic',
        '--enable-cross-compile', '--cc=xcrun -sdk iphoneos clang', '--sysroot=' + sdk,
        '--extra-cflags=-arch arm64 -mios-version-min=17.0 -isysroot ' + sdk,
        '--extra-ldflags=-arch arm64 -mios-version-min=17.0 -isysroot ' + sdk,
        '--disable-everything', '--disable-programs', '--disable-doc', '--disable-avdevice',
        '--disable-avfilter', '--disable-avformat', '--disable-swresample', '--disable-network',
        '--disable-iconv', '--disable-bzlib', '--disable-lzma', '--disable-zlib',
        '--enable-decoder=mpeg2video', '--enable-parser=mpegvideo', '--enable-swscale',
        '--disable-asm', '--enable-static', '--disable-shared', '--disable-debug',
        '--prefix=' + str(ff_prefix)]
    print('+', ' '.join(configure_cmd), flush=True)
    subprocess.run(configure_cmd, cwd=ff_roots[0], check=True)
    subprocess.run(['make', '-j2'], cwd=ff_roots[0], check=True)
    subprocess.run(['make', 'install'], cwd=ff_roots[0], check=True)
    # Static archives via the pinned engine recipe
    # (ps2xRuntime/third_party/armsx2/build.sh ios), one build per vendor:
    # the GE1 set from the new pin (upscale defaults on), the OM1 set from
    # the pre-growth pin (a new-vendor island cannot close; see OM1_VENDOR).
    # No stamp cleanup here: each run mints fresh stamp dirs, and wiping
    # would destroy the other vendor's build (superseded stamps are removed
    # explicitly once both builds succeed).
    def reuse_or_build(key, want_vendor, vendor_dir, bundle, label):
        """Reuse the receipt-pinned build dir when it is still valid."""
        try:
            prior = json.loads((a.work / 'ios-libs.json').read_text())
        except (OSError, ValueError):
            prior = {}
        if prior.get(key + '_vendor') == want_vendor \
                and prior.get('archive_cflags') == '-fno-common':
            candidate = Path(prior.get(key + '_dir') or prior.get(key + '_build') or '')
            if candidate.name and all((candidate / name).is_file() for name in
                                      ('ge1-adapter/libge1_gs.a', 'pcsx2/libPCSX2.a',
                                       'common/libcommon.a')):
                print(key + ': reusing ' + str(candidate))
                return candidate
        return run_vendor_build(vendor_dir, bundle, a.work, a.jobs, label)

    ge1_build = reuse_or_build('ge1', GE1_VENDOR_PIN, vendor, bundle_ge1, 'GE1')
    try:
        prior_om1 = json.loads((a.work / 'ios-libs.json').read_text())
    except (OSError, ValueError):
        prior_om1 = {}
    om1_candidate = Path(prior_om1.get('om1_build') or '')
    if (prior_om1.get('om1_vendor') == OM1_VENDOR_PIN
            and prior_om1.get('archive_cflags') == '-fno-common'
            and prior_om1.get('om1_cflags') == '-DPCSX2_RECOMPILER_TESTS'
            and om1_candidate.name
            and (om1_candidate / 'pcsx2/libPCSX2.a').is_file()
            and (om1_candidate / 'common/libcommon.a').is_file()):
        print('om1: reusing ' + str(om1_candidate))
        om1_build = om1_candidate
    else:
        om1_build = run_om1_build(om1_vendor, bundle_om1, a.work, a.jobs)
    ge1_lib = ge1_build / 'ge1-adapter/libge1_gs.a'
    pcsx2_lib = ge1_build / 'pcsx2/libPCSX2.a'
    # MoltenVK device framework from the pinned upstream release tarball.
    mvk = json.loads((ROOT / 'tools/ssx3/moltenvk.json').read_text())
    fetch_tarball(mvk['url'], mvk['archive_sha256'], a.work / 'ios-moltenvk.tar',
                  mvk['archive_size'])
    mvk_src = a.work / 'ios-moltenvk-src'
    extract_once(a.work / 'ios-moltenvk.tar', '', mvk_src, mvk['device_framework'] + '/MoltenVK')
    mvk_fw = mvk_src / mvk['device_framework']
    if not (mvk_fw / 'MoltenVK').is_file():
        raise ValueError('MoltenVK layout changed in ' + mvk['url'])
    libs = a.work / 'ios-libs'
    if libs.is_dir():
        shutil.rmtree(libs)
    libs.mkdir(parents=True)
    shutil.copy2(ge1_lib, libs / 'libge1_gs.a')
    shutil.copy2(pcsx2_lib, libs / 'libPCSX2.a')
    common_lib = ge1_build / 'common/libcommon.a'
    if not common_lib.is_file():
        raise ValueError('GE1 iOS build is missing common/libcommon.a')
    shutil.copy2(common_lib, libs / 'libcommon.a')
    om1_dir = prune_om1_copy(om1_build, libs / 'om1')
    # Harvested when the OM1 run's ge1_gs link cannot succeed (adapter needs
    # new-core symbols); the OM1_LIB inputs are what matter.
    om1_ge1_gs = 'built' if (om1_build / 'ge1-adapter/libge1_gs.a').is_file() else 'harvested'
    sdl_lib = sdl_prefix / 'lib/libSDL2.a'
    if not sdl_lib.is_file():
        raise ValueError('SDL2 install is missing lib/libSDL2.a')
    shutil.copy2(sdl_lib, libs / 'libSDL2.a')
    shutil.copytree(mvk_fw, libs / 'MoltenVK.framework')
    (a.work / 'ios-libs.json').write_text(json.dumps({
        'archive_cflags': '-fno-common',
        'ge1_vendor': GE1_VENDOR_PIN, 'ge1_dir': str(ge1_build),
        'om1_vendor': OM1_VENDOR_PIN, 'om1_dir': str(om1_dir),
        'om1_build': str(om1_build), 'om1_ge1_gs': om1_ge1_gs,
        'om1_cflags': '-DPCSX2_RECOMPILER_TESTS',
        'ios_dep_pins': resolved, 'sdl2_tarball_sha256': SDL2_TARBALL_SHA,
        'ffmpeg_tarball_sha256': FFMPEG_TARBALL_SHA,
        'moltenvk': mvk['url'] + ' @ ' + mvk['archive_sha256'],
        'libge1_gs_sha256': sha(libs / 'libge1_gs.a'),
        'libPCSX2_sha256': sha(libs / 'libPCSX2.a'),
        'om1_libPCSX2_sha256': sha(om1_dir / 'pcsx2/libPCSX2.a')},
        indent=2, sort_keys=True) + '\n')
    print('iOS libraries staged in ' + str(libs))


def devicectl_devices():
    """Raw `devicectl list devices` output (read-only probe)."""
    proc = subprocess.run(['xcrun', 'devicectl', 'list', 'devices'], text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          timeout=60, check=False)
    return proc.returncode == 0, proc.stdout


def resolve_device(want, for_install=False):
    """Resolve an iOS device UDID: explicit --device wins, else the single
    connected device. Never guesses among several."""
    ok, listing = devicectl_devices()
    if not ok:
        raise ValueError('cannot list devices:\n' + listing)
    # Only connected physical devices count: paired-but-absent phones and
    # shutdown simulators are not install targets.
    seen = [m.group(1) for line in listing.splitlines()
            if 'connected' in line and 'physical' in line
            for m in [re.search(r'([0-9A-Fa-f]{8}-[0-9A-Fa-f]{16}) \(UDID\)', line)]
            if m]
    if want and want not in seen and want in listing:
        # Explicit UDID present but not marked connected (state wording
        # differs across Xcode versions): trust the explicit choice.
        return want
    if want:
        if want in seen or want in listing:
            return want
        raise ValueError('device ' + want + ' not connected:\n' + listing)
    if not for_install:
        raise ValueError('--device <udid> is required (connected: '
                         + (', '.join(seen) if seen else 'none') + ')')
    if len(seen) == 1:
        return seen[0]
    raise ValueError('pass --device <udid> (connected: '
                     + (', '.join(seen) if seen else 'none') + '):\n' + listing)


def find_provisioning_profile(team, bundle_id, profiles_dir=None):
    """Newest valid iOS provisioning profile for this team + bundle id.

    Xcode's automatic signing creates and refreshes these; the builder
    reuses them to sign the staged app (staging adds the disc payload
    after the build, which invalidates any build-time signature)."""
    import plistlib
    from datetime import datetime, timezone
    wanted = team + '.' + bundle_id
    base = Path(profiles_dir) if profiles_dir is not None else Path.home()
    # Xcode's automatic signing keeps its profiles under UserData, while
    # manually installed ones live under MobileDevice: search both.
    patterns = ('Library/MobileDevice/Provisioning Profiles/*.mobileprovision',
                'Library/Developer/Xcode/UserData/Provisioning Profiles/*.mobileprovision')
    profiles = sorted(p for pattern in patterns for p in base.glob(pattern))
    best, best_expiry = None, None
    now = datetime.now(timezone.utc)
    for profile in profiles:
        try:
            proc = subprocess.run(['security', 'cms', '-D', '-i', str(profile)],
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                  timeout=30, check=False)
            if proc.returncode:
                continue
            data = plistlib.loads(proc.stdout)
        except Exception:
            continue
        try:
            expiry = data.get('ExpirationDate')
            if expiry is not None and expiry.tzinfo is None:
                # plist dates carry no zone; Apple writes them in UTC.
                expiry = expiry.replace(tzinfo=timezone.utc)
            app_id = (data.get('Entitlements') or {}).get('application-identifier')
            platforms = data.get('Platform') or []
        except Exception:
            continue
        if app_id not in (wanted, team + '.*') or not expiry or expiry <= now:
            continue
        if 'iOS' not in platforms:
            continue
        if best_expiry is None or expiry > best_expiry:
            best, best_expiry = profile, expiry
    if best is None:
        raise ValueError('no valid provisioning profile for ' + wanted + '; '
                         'build once with Xcode automatic signing (or open the built '
                         'project in Xcode) so Xcode registers the bundle id, then re-run')
    return best


def sign_staged_app(work, stage_app, team, bundle_id, dev=False):
    """Sign the staged app with the user's team (profile from Xcode's
    automatic signing; entitlements generated, never committed).
    F8 (appsec-fix-1): public builds omit get-task-allow; lab/dev builds
    keep it via --dev."""
    import plistlib
    profile = find_provisioning_profile(team, bundle_id)
    identity = signing_identity(team, profile=profile)
    shutil.copy2(profile, stage_app / 'embedded.mobileprovision')
    entitlements = {'application-identifier': team + '.' + bundle_id,
                    'com.apple.developer.team-identifier': team,
                    'get-task-allow': bool(dev)}
    ent_file = work / 'ios-entitlements.plist'
    with open(ent_file, 'wb') as f:
        plistlib.dump(entitlements, f)
    info = stage_app / 'Info.plist'
    with open(info, 'rb') as f:
        plist = plistlib.load(f)
    if plist.get('CFBundleIdentifier') != bundle_id:
        # The engine plist template hardcodes its own id; the build setting
        # carries the user's id for provisioning, so align the staged copy
        # before sealing (recorded, not silent).
        print('Info.plist CFBundleIdentifier was ' + str(plist.get('CFBundleIdentifier'))
              + '; staging as ' + bundle_id)
        plist['CFBundleIdentifier'] = bundle_id
        with open(info, 'wb') as f:
            plistlib.dump(plist, f)
    framework = stage_app / 'Frameworks/MoltenVK.framework'
    run('codesign', '--force', '--sign', identity, '--timestamp=none', framework)
    run('codesign', '--force', '--sign', identity, '--timestamp=none',
        '--entitlements', ent_file, stage_app)
    run('codesign', '--verify', '--strict', '--verbose=2', stage_app)
    return identity


def ios_om1_receipt_path(work):
    return work / 'om1-stage.json'


OM1_OFFLINE_CORE_DIR = ROOT / 'tools/ssx3/offline-core'


def om1_core_hashes(core_dir=None):
    """SHA-256 of each canonical offline-core source. None when any of the
    four is missing (R1: a missing source must never resume-skip)."""
    base = Path(core_dir) if core_dir is not None else OM1_OFFLINE_CORE_DIR
    hashes = {}
    for name in OM1_OFFLINE_CORE:
        try:
            hashes[name] = sha(base / name)
        except OSError:
            return None
    return hashes


def ios_om1_current(work, core_dir=None):
    """Resume-skip predicate: the generated stage matches vendor pin,
    manifest, ELF, the four canonical offline-core sources (R1: changing any
    of them regenerates) and file set (validate_om1_stage contract)."""
    try:
        receipt = json.loads(ios_om1_receipt_path(work).read_text())
    except (OSError, ValueError):
        return False
    if receipt.get('replay_pin') != OM1_REPLAY_PIN:
        return False
    if receipt.get('manifest_sha256') != sha(OM1_MANIFEST):
        return False
    try:
        elf_sha = sha(work / 'SLUS_207.72')
    except OSError:
        return False
    if receipt.get('elf_sha256') != elf_sha:
        return False
    want_core = receipt.get('core_sha256')
    if not isinstance(want_core, dict):
        return False
    have_core = om1_core_hashes(core_dir)
    if have_core is None or have_core != want_core:
        return False
    if not om1_vendor_current(work):
        return False
    stage = work / 'om1-stage'
    try:
        validate_om1_stage(stage)
    except ValueError:
        return False
    want_out = receipt.get('outputs_sha256')
    if not isinstance(want_out, dict):
        return False
    have_out = om1_stage_file_hashes(stage)
    if have_out is None or have_out != want_out:
        return False
    return True


def om1_stage_file_hashes(stage):
    """SHA-256 of every regular file under the stage (R5: the builder
    certifies staged bytes by hash, not by existence). Symlinks are excluded
    except that exactly one — the armsx2 vendor link, whose identity
    om1_vendor_current covers — may exist. None on any read failure."""
    base = Path(stage)
    try:
        entries = sorted(base.rglob('*'))
    except OSError:
        return None
    links = sorted(str(p.relative_to(base)) for p in entries if p.is_symlink())
    if links != ['armsx2']:
        return None
    hashes = {}
    for p in entries:
        if p.is_symlink() or not p.is_file():
            continue
        try:
            hashes[str(p.relative_to(base))] = sha(p)
        except OSError:
            return None
    return hashes


def om1_vendor_head(vendor):
    """Detached-HEAD SHA of the vendor checkout, or None when unresolvable."""
    try:
        return subprocess.check_output(
            ['git', '-C', str(vendor), 'rev-parse', 'HEAD'], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def om1_vendor_current(work):
    """R2: stage/armsx2 must be a symlink resolving to this work's vendor
    checkout, and that checkout must sit exactly on the replay pin. A copied
    work dir keeps an absolute link into the old tree; a re-seated vendor
    with a different HEAD is not the pinned one. Either case regenerates."""
    vendor = Path(work) / 'ios-vendor-om1replay'
    link = Path(work) / 'om1-stage' / 'armsx2'
    if not link.is_symlink():
        return False
    try:
        if os.path.realpath(link) != os.path.realpath(vendor):
            return False
    except OSError:
        return False
    return om1_vendor_head(vendor) == OM1_REPLAY_PIN


def check_vendor_clean(vendor):
    """Fail loud on a dirty vendor tree: builds must come from the pinned
    commit's bytes, never from uncommitted local edits."""
    out = subprocess.check_output(
        ['git', '-C', str(vendor), 'status', '--porcelain'], text=True)
    if out.strip():
        raise ValueError('ios-om1 vendor checkout is dirty (uncommitted changes under '
                         + str(vendor) + '); delete it and re-run')


def link_om1_vendor(stage, vendor):
    """Point stage/armsx2 at the fetched om1-replay vendor tree.

    The engine consumes the stage's armsx2/ as a full ARMSX2 tree (it
    compiles armsx2/tests/ctest/core/StubHost.cpp with include dirs rooted
    under armsx2/, so StubHost's pcsx2/* includes plus 3rdparty headers must
    resolve there). Copying single files is not enough; link the tree."""
    link = Path(stage) / 'armsx2'
    if link.is_symlink() or link.exists():
        link.unlink() if link.is_symlink() else shutil.rmtree(link)
    link.symlink_to(Path(vendor), target_is_directory=True)
    return link


@contextmanager
def om1_stage_lock(work):
    """R6: serialize stage generation per work dir. The lock file holds the
    holder's pid; a present lock fails loud instead of interleaving two
    generations over the same vendor/build/stage paths. Remove the lock file
    if the holder process is gone."""
    lock = Path(work) / 'om1-stage.lock'
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            holder = lock.read_text().strip()
        except OSError:
            holder = '?'
        raise ValueError('ios-om1 generation is already running in ' + str(work) +
                         ' (lock held by pid ' + holder + '); remove ' + str(lock) +
                         ' if that process is gone')
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(str(os.getpid()))
        yield
    finally:
        try:
            lock.unlink()
        except OSError:
            pass


def publish_om1_stage(work, new, receipt_data):
    """R6: atomically publish a verified temp stage. The receipt lands first
    (temp file + os.replace); a crash before the dir swap leaves a receipt
    whose output hashes match nothing, so the next run regenerates. Then the
    old stage (if any) is removed and the temp dir renamed into place."""
    receipt = ios_om1_receipt_path(work)
    tmp = receipt.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(receipt_data))
    os.replace(tmp, receipt)
    stage = Path(work) / 'om1-stage'
    if stage.is_symlink() or stage.exists():
        stage.unlink() if stage.is_symlink() else shutil.rmtree(stage)
    os.rename(new, stage)
    return stage


def ensure_ios_om1_stage(a):
    """Default --om1-stage: generate work/om1-stage from the user's disc
    (Mac-only), or resume-skip when the receipt still matches."""
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        raise ValueError('the generated --om1-stage default needs a Mac '
                         '(Apple silicon); pass --om1-stage explicitly elsewhere')
    if a.dry_run:
        print('  offline VU1 stage generated ' + str(a.work / 'om1-stage'))
        return a.work / 'om1-stage'
    if ios_om1_current(a.work):
        print('  offline VU1 stage current ' + str(a.work / 'om1-stage'))
        return a.work / 'om1-stage'
    return ios_om1(a)


def ios_om1(a):
    """Mac-only ios-om1 step: fetch the om1-replay vendor series, build
    om1_replay with Apple Clang, verify the manifest ELF pin against the
    user's SLUS_207.72, run all 9 areas into a temp stage, copy in the
    offline-core sources, and link stage/armsx2 at the fetched vendor tree
    (the engine compiles the stage's StubHost.cpp against that full tree).
    The verified temp stage is published atomically; concurrent runs are
    serialized by om1_stage_lock."""
    for tool in ('/usr/bin/clang', '/usr/bin/clang++', 'cmake', 'ninja'):
        if not shutil.which(tool):
            raise ValueError('ios-om1 needs ' + tool + ' (Mac-only step)')
    manifest = json.loads(OM1_MANIFEST.read_text())
    elf = a.work / 'SLUS_207.72'
    try:
        elf_sha = sha(elf)
    except OSError:
        raise ValueError('ios-om1 needs work/SLUS_207.72: run prepare with --ssx3-iso first')
    if elf_sha != manifest['elf']['sha256']:
        raise ValueError('ios-om1: SLUS_207.72 sha ' + elf_sha[:12] + ' does not match '
                         'the manifest pin ' + manifest['elf']['sha256'][:12] +
                         ' (manifest pieces are exact ELF offsets of that SLUS revision)')
    with om1_stage_lock(a.work):
        try:
            ios_om1_receipt_path(a.work).unlink()
        except OSError:
            pass
        vendor = a.work / 'ios-vendor-om1replay'
        source(OM1_REPLAY_REPO, OM1_REPLAY_PIN, vendor)
        actual = subprocess.check_output(['git', '-C', str(vendor), 'rev-parse', 'HEAD'], text=True).strip()
        if actual != OM1_REPLAY_PIN:
            raise ValueError('ios-om1 vendor checkout is ' + actual[:12] + ', want ' + OM1_REPLAY_SHORT)
        check_vendor_clean(vendor)
        deps = a.work / 'ios-om1-deps'
        for name in ('plutovg', 'plutosvg'):
            src = vendor / '3rdparty' / name
            bdir = a.work / ('ios-om1-deps-build-' + name)
            extra = ['-DCMAKE_PREFIX_PATH=' + str(deps)] if name == 'plutosvg' else []
            run('cmake', '-S', str(src), '-B', str(bdir), '-G', 'Ninja',
                '-DCMAKE_BUILD_TYPE=Release', '-DCMAKE_INSTALL_PREFIX=' + str(deps),
                '-DCMAKE_C_COMPILER=/usr/bin/clang', '-DCMAKE_CXX_COMPILER=/usr/bin/clang++', *extra)
            run('cmake', '--build', str(bdir), '--parallel', str(a.jobs))
            run('cmake', '--install', str(bdir))
        bdir = a.work / 'ios-om1-build'
        run('cmake', '-S', str(vendor), '-B', str(bdir), '-G', 'Ninja',
            '-DCMAKE_BUILD_TYPE=Release', '-DCMAKE_C_COMPILER=/usr/bin/clang',
            '-DCMAKE_CXX_COMPILER=/usr/bin/clang++', '-DENABLE_QT_UI=OFF',
            '-DENABLE_TESTS=OFF', '-DENABLE_RECOMPILER_TEST_HOOKS=ON',
            '-DUSE_VULKAN=OFF', '-DCMAKE_DISABLE_PRECOMPILE_HEADERS=ON',
            '-DCMAKE_C_FLAGS=-fno-common', '-DCMAKE_CXX_FLAGS=-fno-common',
            '-DCMAKE_PREFIX_PATH=' + str(deps))
        run('cmake', '--build', str(bdir), '--target', 'om1_replay', '--parallel', str(a.jobs))
        tool = bdir / 'om1-adapter' / 'om1_replay'
        new = a.work / 'om1-stage.new'
        if new.is_symlink() or new.exists():
            new.unlink() if new.is_symlink() else shutil.rmtree(new)
        new.mkdir(parents=True)
        for area in OM1_AREAS:
            run(str(tool), str(OM1_MANIFEST), str(elf), area, str(new))
        for name in OM1_OFFLINE_CORE:
            shutil.copy2(OM1_OFFLINE_CORE_DIR / name, new / name)
        link_om1_vendor(new, vendor)
        validate_om1_stage(new)
        core_hashes = om1_core_hashes()
        if core_hashes is None:
            raise ValueError('ios-om1: a canonical offline-core source is missing under '
                             + str(OM1_OFFLINE_CORE_DIR))
        output_hashes = om1_stage_file_hashes(new)
        if output_hashes is None:
            raise ValueError('ios-om1: cannot hash the generated stage under ' + str(new))
        stage = publish_om1_stage(a.work, new, {
            'replay_pin': OM1_REPLAY_PIN, 'manifest_sha256': sha(OM1_MANIFEST),
            'elf_sha256': elf_sha, 'core_sha256': core_hashes,
            'outputs_sha256': output_hashes})
    print('  offline VU1 stage regenerated ' + str(stage))
    return stage


IOS_MCPU = 'apple-a15'


def xcrun_llvm():
    """Apple compilers for the iOS app build (D05: never inherit CC/CXX)."""
    cc = subprocess.check_output(['xcrun', '--sdk', 'iphoneos', '--find', 'clang'],
                                 text=True).strip()
    cxx = subprocess.check_output(['xcrun', '--sdk', 'iphoneos', '--find', 'clang++'],
                                  text=True).strip()
    return cc, cxx


def ios_build_versions(cc, cxx):
    """Compiler/SDK versions recorded in the iOS build receipt (D05)."""
    def first_line(*cmd):
        try:
            return subprocess.check_output(list(cmd), text=True).strip().splitlines()[0]
        except (OSError, subprocess.CalledProcessError, IndexError):
            return 'unknown'
    return {
        'c_compiler': cc,
        'cxx_compiler': cxx,
        'c_compiler_version': first_line(cc, '--version'),
        'xcode_version': first_line('xcodebuild', '-version'),
        'iphoneos_sdk_version': first_line('xcrun', '--sdk', 'iphoneos', '--show-sdk-version'),
    }


def stage_ios_presets(app):
    """Copy the shipped iOS presets into the staged app bundle.

    G9 (public-parity-2): the staged 120-layer name must match what the
    runtime reads (bundle/full120.env in ps2_ios_runtime.mm). The source
    preset keeps its full120-ios.env name so it cannot collide with the
    Android presets/full120.env. Returns {staged name: sha256}."""
    staged = {}
    for name, dest in (('ios.env', 'ps2x.env'), ('full120-ios.env', 'full120.env')):
        src = ROOT / 'presets' / name
        if not src.is_file():
            raise ValueError('missing shipped preset: ' + str(src))
        shutil.copy2(src, Path(app) / dest)
        staged[dest] = sha(Path(app) / dest)
    return staged


def ios_release_flags(pgo_data):
    """iOS app Release flags (D04 apple-a15 tuning plus optional PGO use)."""
    base = '-O3 -DNDEBUG -mcpu=' + IOS_MCPU
    flags = ['-DCMAKE_C_FLAGS_RELEASE=' + base, '-DCMAKE_CXX_FLAGS_RELEASE=' + base]
    if pgo_data:
        flags[0] += ' -fprofile-use=' + pgo_data
        flags[1] += ' -fprofile-use=' + pgo_data
        flags.append('-DCMAKE_EXE_LINKER_FLAGS_RELEASE=-fprofile-use=' + pgo_data)
    return flags


def ios(a):
    team = check_team(a)
    bundle_id = check_bundle_id(getattr(a, 'bundle_id', None) or default_bundle_id(team))
    if a.dry_run:
        print('Would build the iOS app (no Xcode, no device touched):')
        print('  team ' + team + ', bundle id ' + bundle_id + ', app label ' + resolve_app_label(a))
        print('  engine ' + str(ENGINE))
        print('  GE1 vendor ' + GE1_VENDOR_REPO + ' @ ' + GE1_VENDOR_SHORT)
        print('  OM1 vendor ' + OM1_VENDOR_REPO + ' @ ' + OM1_VENDOR_SHORT)
        print('  offline VU1 stage ' + str(getattr(a, 'om1_stage', None) or 'generated work/om1-stage'))
        print('  Release flags -O3 -DNDEBUG -mcpu=' + IOS_MCPU + '; PGO '
              + ('off (--no-pgo)' if getattr(a, 'no_pgo', False)
                 else ('explicit ' + str(getattr(a, 'pgo_profile', None))
                       if getattr(a, 'pgo_profile', None) else 'bundled when fresh for your codegen')))
        print('  prepare outputs ' + ('current' if prepare_current(a.work) else 'MISSING (run prepare)') + ': '
              + str(a.work / 'codegen'))
        print('  ios-libs ' + ('current' if ios_libs_current(a.work) else 'MISSING (run ios-libs)') + ': '
              + str(a.work / 'ios-libs'))
        print('  stage into ' + str(a.work / 'ios-stage') + ' (SLUS_207.72, slim ISO, '
              'presets/ios.env->ps2x.env, presets/full120-ios.env->full120.env, Settings.bundle, MoltenVK.framework)')
        if getattr(a, 'install', False):
            print('  install with xcrun devicectl to ' + str(getattr(a, 'device', None) or 'the connected device'))
        return
    inputs = json.loads((a.work / 'inputs.json').read_text())
    if not ios_libs_current(a.work):
        raise ValueError('ios-libs outputs are missing or stale; run ios-libs in this work directory first')
    if getattr(a, 'om1_stage', None):
        om1_stage = validate_om1_stage(a.om1_stage)
    else:
        om1_stage = ensure_ios_om1_stage(a)
    receipt = json.loads((a.work / 'ios-libs.json').read_text())
    ge1_build = Path(receipt.get('ge1_dir') or receipt.get('ge1_build') or '')
    vendor = a.work / 'ios-vendor'
    try:
        om1_lib = Path(receipt['om1_dir'])
    except KeyError:
        raise ValueError('ios-libs predates the OM1 archive set; re-run ios-libs')
    if not ge1_build.is_dir() or not vendor.is_dir() or not om1_lib.is_dir():
        raise ValueError('ios-libs build directory is gone; re-run ios-libs')
    pgo_data = resolve_pgo(a.work, 'ios', explicit=getattr(a, 'pgo_profile', None),
                           disabled=getattr(a, 'no_pgo', False))
    cc, cxx = xcrun_llvm()
    versions = ios_build_versions(cc, cxx)
    print('iOS compilers: ' + cc + ' / ' + cxx + ' (' + versions['c_compiler_version']
          + '; Xcode ' + versions['xcode_version'] + '; iphoneos '
          + versions['iphoneos_sdk_version'] + ')')
    release_flags = ios_release_flags(pgo_data)
    build_dir = a.work / 'ios-app-build'
    # D05: the app configure AND build must not inherit Homebrew CC/CXX.
    saved_cc = os.environ.pop('CC', None)
    saved_cxx = os.environ.pop('CXX', None)
    try:
        run('cmake', '-S', ENGINE, '-B', build_dir, '-G', 'Xcode',
            '-DCMAKE_TOOLCHAIN_FILE=' + str(ROOT / 'tools/ssx3/ios/toolchain.cmake'),
            '-DCMAKE_C_COMPILER=' + cc, '-DCMAKE_CXX_COMPILER=' + cxx,
            '-DCMAKE_CONFIGURATION_TYPES=Release', *release_flags,
            '-DPS2X_BUILD_RECOMP=OFF', '-DPS2X_BUILD_ANALYZER=OFF',
            '-DPS2X_BUILD_TEST=OFF', '-DPS2X_BUILD_STUDIO=OFF',
            '-DPS2X_ENABLE_SCCACHE=OFF', '-DPS2X_ENABLE_AGRESSIVE_LOGS=OFF',
            '-DPS2X_ENABLE_RUNTIME_LOGS=OFF', '-DPS2X_ENABLE_DIAG_TAPS=OFF',
            '-DPS2X_ENABLE_FFMPEG=ON', '-DPS2X_FFMPEG_IOS_ROOT=' + str(a.work / 'ios-ffmpeg'),
            '-DSDL2_DIR=' + str(a.work / 'ios-sdl2/lib/cmake/SDL2'),
            '-DPS2X_GAME_CODEGEN_DIR=' + str(a.work / 'codegen'),
            '-DPS2X_GE1_IOS_BUILD=' + str(ge1_build),
            '-DPS2X_GE1_PCSX2_ROOT=' + str(vendor),
            '-DPS2X_OM1_STAGE=' + str(om1_stage), '-DPS2X_OM1_LIB=' + str(om1_lib),
            '-DPS2X_VU0_RECOMP_DIR=' + resolve_vu0_stage(a),
            '-DCMAKE_XCODE_ATTRIBUTE_PRODUCT_BUNDLE_IDENTIFIER=' + bundle_id,
            '-DCMAKE_XCODE_ATTRIBUTE_DEVELOPMENT_TEAM=' + team,
            '-DPS2X_APP_DISPLAY_NAME=' + resolve_app_label(a),
            '-DCMAKE_XCODE_ATTRIBUTE_CODE_SIGN_STYLE=Automatic')
        run('cmake', '--build', build_dir, '--config', 'Release',
            '--target', 'ps2EntryRunner', '--', '-jobs', str(a.jobs))
    finally:
        if saved_cc is not None:
            os.environ['CC'] = saved_cc
        if saved_cxx is not None:
            os.environ['CXX'] = saved_cxx
    built = build_dir / 'ps2xRuntime/Release-iphoneos/ps2EntryRunner.app'
    if not built.is_dir():
        raise ValueError('iOS app bundle missing after build: ' + str(built))
    (a.work / 'ios-source-binary-sha.txt').write_text(
        sha(built / 'ps2EntryRunner') + '  ps2EntryRunner\n')
    import plistlib
    with open(built / 'Info.plist', 'rb') as f:
        built_id = plistlib.load(f).get('CFBundleIdentifier')
    print('built bundle id: ' + str(built_id))
    disc = verified_disc_payload(a.work, inputs)
    files = {relative: source for source, relative in disc}
    stage_dir = a.work / 'ios-stage'
    app = stage_dir / (bundle_id + '.app')
    if app.exists():
        shutil.rmtree(app)
    stage_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(built, app, symlinks=True)
    shutil.copy2(files['SLUS_207.72'], app / 'SLUS_207.72')
    shutil.copy2(files['SSX3.iso'], app / 'SSX3.iso')
    shutil.copy2(files['SSX3.iso.holes'], app / 'SSX3.iso.holes')
    staged_presets = stage_ios_presets(app)
    shutil.copytree(ENGINE / 'ps2xRuntime/ios/Settings.bundle', app / 'Settings.bundle')
    frameworks = app / 'Frameworks'
    frameworks.mkdir(exist_ok=True)
    shutil.copytree(a.work / 'ios-libs/MoltenVK.framework',
                    frameworks / 'MoltenVK.framework', symlinks=True)
    pack = font_pack(a, inputs)
    if pack is not None:
        target = app / 'font-pack'
        shutil.copytree(pack, target)
        env_file = app / 'ps2x.env'
        lines = [line for line in env_file.read_text().splitlines()
                 if not line.startswith('GE1_TEX_REPLACE=')
                 and not line.startswith('GE1_TEX_PRELOAD=')]
        lines += ['GE1_TEX_REPLACE=${BUNDLE}/font-pack', 'GE1_TEX_PRELOAD=1']
        env_file.write_text('\n'.join(lines) + '\n')
    # P2: the Metal TFX selector coverage ships inside the bundle; the
    # staged ps2x.env points GE1_TFX_PREWARM at it. The file is written
    # and verified BEFORE the knob can take effect (the knob without its
    # file still flips the adapter's pipe-flush cadence 0 -> 600).
    validate_tfx_selectors()
    tfx_sha = stage_tfx_selectors(app)
    subprocess.run(['find', str(app), '-name', '._*', '-delete'], check=False)
    identity = sign_staged_app(a.work, app, team, bundle_id, dev=getattr(a, 'dev', False))
    (a.work / 'ios-app.json').write_text(json.dumps({
        'team': team, 'bundle_id': bundle_id, 'app_label': resolve_app_label(a), 'identity': identity,
        'engine': subprocess.check_output(['git', '-C', str(ENGINE), 'rev-parse', 'HEAD'],
                                          text=True).strip(),
        'ge1_vendor': GE1_VENDOR_PIN, 'om1_vendor': receipt.get('om1_vendor'),
        'om1_stage': str(om1_stage), 'om1_lib': str(om1_lib),
        'tfx_selectors_sha256': tfx_sha,
        'slim_sha256': inputs['slim_sha256'], 'elf_sha256': inputs['elf_sha256'],
        'signed_binary_sha256': sha(app / 'ps2EntryRunner'),
        'preset_sha256': sha(app / 'ps2x.env'),
        'full120_sha256': staged_presets['full120.env'],
        'xcode_version': versions['xcode_version'],
        'iphoneos_sdk_version': versions['iphoneos_sdk_version'],
        'c_compiler': versions['c_compiler'],
        'cxx_compiler': versions['cxx_compiler'],
        'c_compiler_version': versions['c_compiler_version'],
        'c_flags_release': release_flags[0], 'cxx_flags_release': release_flags[1],
        'pgo_profile': pgo_data or None}, indent=2, sort_keys=True) + '\n')
    print('Staged signed iOS app in ' + str(app))
    print('Install it with: ./build.py ios --ssx3-iso <ISO> --work <DIR> --team ' + team
          + ' --om1-stage <STAGE> --install (plain install; existing cards and saves are kept)')


def ios_install(a):
    apps = sorted((a.work / 'ios-stage').glob('*.app'))
    if len(apps) != 1:
        raise ValueError('ios install needs exactly one staged app in '
                         + str(a.work / 'ios-stage') + '; run ios first')
    udid = resolve_device(getattr(a, 'device', None), for_install=True)
    # Plain install only: devicectl install never uninstalls, so the app's
    # Documents (cards, saves, settings) survive every upgrade.
    run('xcrun', 'devicectl', 'device', 'install', 'app', '--device', udid,
        '--timeout', '1200', apps[0])
    print('Installed on ' + udid + '; existing cards and saves were not removed.')


def ios_onecmd(a):
    """One-command iOS build: doctor -> prepare -> ios-libs -> ios (-> install).

    Resumable: prepare and ios-libs are skipped when their outputs are
    current for this disc. ios always runs (Xcode is incremental)."""
    if a.tricky_iso:
        validate_tricky_iso(a.tricky_iso)
    if not a.work:
        a.work = Path.home() / 'ssx3-work'
    a.work = a.work.resolve()
    if a.work == ROOT or ROOT in a.work.parents:
        raise ValueError('--work must be outside the source checkout')
    check_team(a)
    a.target = 'ios'
    if not a.ssx3_iso:
        raise ValueError('ios one-command needs --ssx3-iso <your-USA-disc>')
    # F12: disc identity and native host tools before any download.
    a.iso = validate_ssx3_iso(a.ssx3_iso)
    preflight_host_tools()
    # F03: create WORK before any step writes into it (fresh iOS one-command
    # setups failed when prepare wrote SLUS_207.72 under a missing parent).
    a.work.mkdir(parents=True, exist_ok=True)
    # F15: disk preflight before anything heavy (measured budgets in README).
    check_capacity(a.work, 'ios')
    doctor(a)
    if a.dry_run:
        ios(a)
        return
    if prepare_current(a.work, a.iso):
        print('prepare: outputs current for this disc, skipping')
    else:
        prepare(a)
    if ios_libs_current(a.work):
        print('ios-libs: staged libraries current, skipping')
    else:
        ios_libs(a)
    ios(a)
    if a.install:
        ios_install(a)


def stage(a):
    app_id_file = a.work / 'app-id.txt'
    if not app_id_file.is_file():
        raise ValueError('stage requires a completed android build in this work directory')
    built_id = app_id_file.read_text().strip()
    if built_id != a.app_id:
        raise ValueError('stage --app-id must match the built APK: ' + built_id)
    inputs = json.loads((a.work / 'inputs.json').read_text())
    files = a.work / 'stage/files'
    files.mkdir(parents=True, exist_ok=True)
    presets = ROOT / 'presets'
    for name in ('ps2x.env', 'full120.env'):
        src = presets / name
        if not src.is_file():
            raise ValueError('missing shipped preset: ' + str(src))
        if name == 'ps2x.env':
            (files / name).write_text(src.read_text().replace(PUBLIC_APP_ID, a.app_id))
        else:
            shutil.copy2(src, files / name)
    # Android online staging (appsec-fix-1): the public Android build carries
    # online like the iOS preset does. presets/online.env stays the single
    # definition of the lobby rows; the staged ps2x.env appends them so the
    # runtime (which layers only ps2x.env + full120.env) arms
    # PS2X_NET=1 / ssx.bradr.dev / Quick Play. STOCK120 is deliberately not
    # staged (code default off, per the decided default).
    online_src = presets / 'online.env'
    if not online_src.is_file():
        raise ValueError('missing shipped preset: ' + str(online_src))
    online_rows = [line.strip() for line in online_src.read_text().splitlines()
                   if line.strip() and not line.strip().startswith('#')]
    with (files / 'ps2x.env').open('a') as staged:
        staged.write('# Online layer from presets/online.env (staged copy).\n')
        for row in online_rows:
            staged.write(row + '\n')
    resources = a.work / 'android-resources'
    if not resources.is_dir():
        raise ValueError('stage requires android-libs first (missing android-resources in work dir)')
    target = files / 'ge1-resources'
    shutil.copytree(resources, target, dirs_exist_ok=True)
    (files / 'ge1-data').mkdir(exist_ok=True)
    (files / 'mc0').mkdir(exist_ok=True)
    device = '/storage/emulated/0/Android/data/' + a.app_id + '/files'
    stage_fonts(a, inputs, files, device)
    tricky_staged, tricky_trees = stage_tricky(a, files)
    apk = ROOT / 'apps/android/app/build/outputs/apk/release/app-release.apk'
    print('Staged device layout in ' + str(files))
    if a.step != 'stage':
        return files
    print('Push it with these commands (stage prints them; it does not run them):')
    print('  adb install -r ' + str(apk))
    # F6: bootstrap through the exported Launch60 alias (the trampoline),
    # never the non-exported NativeActivity directly.
    print('  adb shell am start -n ' + a.app_id + '/.Launch60')
    print('  adb shell am force-stop ' + a.app_id)
    print('  adb shell test -d ' + device + '/ge1-data')
    print('  adb shell test -d ' + device + '/mc0')
    print('  adb push ' + str(a.work / 'SLUS_207.72') + ' ' + device + '/SLUS_207.72')
    print('  adb push "' + str(inputs['slim_iso']) + '" ' + device + '/SSX3.iso')
    print('  adb push "' + str(inputs['slim_iso']) + '.holes" ' + device + '/SSX3.iso.holes')
    print('  adb push ' + str(files / 'ps2x.env') + ' ' + device + '/ps2x.env')
    print('  adb push ' + str(files / 'full120.env') + ' ' + device + '/full120.env')
    print('  adb push ' + str(files / 'ge1-resources') + ' ' + device + '/')
    if (files / 'font-pack').is_dir():
        print('  adb push ' + str(files / 'font-pack') + ' ' + device + '/')
        print('  adb shell chmod -R o+rX ' + device + '/font-pack')
    for _, relative in tricky_staged:
        print('  adb push ' + str(files / relative) + ' ' + device + '/' + relative)
    for tree in tricky_trees:
        print('  adb push ' + str(files / tree) + ' ' + device + '/')
        print('  adb shell chmod -R o+rX ' + device + '/' + tree)
    print('  adb shell chmod -R o+rX ' + device + '/ge1-resources')
    print('  adb shell chmod o+r ' + device + '/SLUS_207.72 ' + device + '/SSX3.iso ' +
          device + '/SSX3.iso.holes ' + device + '/ps2x.env ' + device + '/full120.env')
    print('The app creates ge1-data and the empty mc0 card dir on first launch.')
    print('Back up your keystore; a different key forces an uninstall, which wipes saves.')
    return files


def resolve_adb(a):
    """adb binary for install/device steps (F01): the selected SDK wins.

    Order matches toolchain.find_adb: managed SDK under --work, then
    ANDROID_HOME/ROOT, common installs, then PATH. Fails closed with the
    fix (install the platform-tools package or put adb on PATH) instead
    of an exec error from a bare `adb`.
    """
    import toolchain
    adb = toolchain.find_adb(getattr(a, 'work', None))
    if adb is None:
        raise ValueError('adb not found: install the Android SDK platform-tools '
                         'package (the one-command build fetches it into '
                         'WORK/toolchain/android-sdk) or put adb on PATH')
    return str(adb)


def check_device_flags(step, device, serial):
    """F11: --serial is Android-only, --device is iOS-only. Returns the
    p.error message, or None when the combination is valid."""
    if step == 'android' and device:
        return ('--device is iOS-only; use --serial <adb-serial> to pick '
                'an Android device (adb devices lists them)')
    if step in ('ios', 'ios-libs') and serial:
        return ('--serial is Android-only; use --device <udid> to pick an '
                'iOS device (xcrun devicectl list devices)')
    return None


def needs_prepare(step, dry_run):
    """Whether a step-by-step invocation requires prepared outputs (F06).

    `ios --dry-run` is the pre-prepare probe: ios() reports MISSING
    outputs itself, so the gate lets it through without inputs.json.
    Every other non-prepare/tricky step (including `install --dry-run`,
    which lists the prepared payload) still requires prepare first.
    """
    return step != 'prepare' and step != 'tricky' and not (step == 'ios' and dry_run)


def host_memory_bytes():
    """Total host RAM in bytes, or None when it cannot be read (stdlib only)."""
    import platform as platform_module
    try:
        if platform_module.system() == 'Darwin':
            return int(subprocess.check_output(['sysctl', '-n', 'hw.memsize'], text=True).strip())
        if platform_module.system() == 'Linux':
            with open('/proc/meminfo') as f:
                for line in f:
                    if line.startswith('MemTotal:'):
                        return int(line.split()[1]) * 1024
    except (OSError, subprocess.CalledProcessError, ValueError, IndexError):
        pass
    return None


# F15: measured cold-build footprints (mini-external live dirs, 2026-10-18;
# see README budgets): full Android SDK 3.4 GB (NDK 2.8), managed JDK 0.3 GB,
# staged slim ISO 1.7 GB, prepare outputs ~0.8 GB (codegen 272 MB +
# generator 504 MB), Mac runner build 1.9 GB, whole Mac work dir 5.8 GB,
# iOS work dir 13 GB, APK ~0.34 GB. Requirements keep ~2x headroom over the
# measured work contents plus Gradle/Xcode caches outside --work.
MIN_FREE_BYTES = {'android': 25 * 1024**3, 'ios': 20 * 1024**3}
MIN_FREE_BYTES_DEFAULT = 12 * 1024**3
MIN_MEMORY_BYTES_WARN = 8 * 1024**3


def check_capacity(work, target):
    """F15: fail before heavy work when the work filesystem is too full.

    Disk is enforced (a full disk mid-build corrupts outputs); low RAM is
    a warning only (small hosts can still build with --jobs 1).
    """
    required = MIN_FREE_BYTES.get(target, MIN_FREE_BYTES_DEFAULT)
    free = shutil.disk_usage(str(work)).free
    if free < required:
        raise ValueError('host preflight: only %.1f GB free under %s, need ~%d GB free '
                         'for a %s cold build (measured footprints in README; '
                         'free space or pick another --work)' %
                         (free / 1024**3, work, required // 1024**3, target))
    total = host_memory_bytes()
    if total is not None and total < MIN_MEMORY_BYTES_WARN:
        print('warning: host RAM is %.1f GB; 8+ GB is recommended '
              '(use --jobs 1 on small hosts)' % (total / 1024**3))
    return True


def check_install_device(a, check):
    """F14: early install-target check for Android --install.

    Runs inside doctor() so a missing/unauthorized/ambiguous device fails
    before the build, not after it. Honors --serial: a named serial must
    be present and authorized.
    """
    try:
        binary = resolve_adb(a)
    except ValueError as error:
        check('Android device', False, str(error),
              'fetch the pinned toolchain or put adb on PATH (see the adb check above)')
        return
    ok, listing = probe([binary, 'devices'])
    if not ok:
        check('Android device', False, 'cannot list devices: ' + listing,
              'reconnect the device and retry')
        return
    states = {}
    for line in listing.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2:
            states[parts[0]] = parts[1]
    want = getattr(a, 'serial', None)
    fix = ('connect the device over USB, accept the on-device RSA trust '
           'prompt and enable USB debugging; WSL2: attach the USB device '
           'to Ubuntu first (usbipd); with several devices pass '
           '--serial <adb-serial> from `adb devices`')
    if want:
        state = states.get(want)
        if state == 'device':
            check('Android device', True, want, '')
        elif state == 'unauthorized':
            check('Android device', False, want + ' is unauthorized',
                  'accept the RSA trust prompt on the device, then retry')
        else:
            check('Android device', False,
                  '--serial ' + want + ' not connected (adb devices: ' +
                  (', '.join(sorted(states)) if states else 'none') + ')', fix)
        return
    ready = sorted(serial for serial, state in states.items() if state == 'device')
    if len(ready) == 1:
        check('Android device', True, ready[0], '')
    elif not states:
        check('Android device', False, 'no Android device listed by adb devices', fix)
    elif not ready:
        check('Android device', False,
              'no authorized device (states: ' + ', '.join(sorted(set(states.values()))) + ')',
              'accept the RSA trust prompt on the device, then retry')
    else:
        check('Android device', False,
              'several devices connected (' + ', '.join(ready) + ')', fix)


def adb(a, *args, capture=False):
    binary = resolve_adb(a)
    command = [binary] + (['-s', a.serial] if a.serial else []) + list(map(str, args))
    if capture:
        return subprocess.check_output(command, text=True).strip()
    run(*command)


def apk_package(apk):
    sdk = os.environ.get('ANDROID_HOME') or os.environ.get('ANDROID_SDK_ROOT')
    if not sdk:
        raise ValueError('set ANDROID_HOME to verify the APK package before install')
    tools = sorted(Path(sdk, 'build-tools').glob('*/aapt2'), reverse=True)
    if not tools:
        raise ValueError('aapt2 missing from Android SDK build-tools')
    return subprocess.check_output([str(tools[0]), 'dump', 'packagename', str(apk)], text=True).strip()


def verified_disc_payload(work, inputs):
    iso = Path(inputs['iso'])
    slim = Path(inputs['slim_iso'])
    table = Path(str(slim) + '.holes')
    if sha(iso) != inputs['iso_sha256'] or sha(work / 'SLUS_207.72') != inputs['elf_sha256']:
        raise ValueError('disc or extracted ELF changed since prepare')
    if sha(slim) != inputs['slim_sha256'] or sha(table) != inputs['slim_table_sha256']:
        raise ValueError('slim image or table changed since prepare')
    return [(slim, 'SSX3.iso'), (table, 'SSX3.iso.holes'),
            (work / 'SLUS_207.72', 'SLUS_207.72')]


def ensure_install_toolchain(a):
    """Discovery-only toolchain for install (never downloads).

    install only shells out to keytool/aapt2/adb: an installed or managed
    toolchain is picked up when present, otherwise the existing precise
    keytool/ANDROID_HOME errors speak. The managed-SDK licence gate still
    applies when the managed SDK is the one in use.
    """
    import toolchain
    java_home, sdk, sdk_managed, ndk = toolchain.discover(
        a.work, getattr(a, 'jdk', None), getattr(a, 'ndk', None))
    if java_home is not None:
        os.environ.setdefault('JAVA_HOME', str(java_home))
    if sdk is not None:
        os.environ.setdefault('ANDROID_HOME', str(sdk))
        # F01: install shells adb through resolve_adb, but child Gradle
        # and user commands also expect it: the selected SDK's
        # platform-tools joins PATH alongside the JDK bin.
        platform_tools = str(Path(sdk, 'platform-tools'))
        if Path(platform_tools).is_dir():
            os.environ['PATH'] = (platform_tools + os.pathsep +
                                  os.environ.get('PATH', ''))
    if ndk is not None:
        a.ndk = ndk
    java_env = str(java_home) if java_home is not None else os.environ.get('JAVA_HOME')
    if sdk_managed and java_env:
        toolchain.require_licenses(sdk, getattr(a, 'accept_android_licenses', False), java_env)


def verified_tricky_payload(work, record):
    """Re-verify the Tricky disc and outputs before pushing (slow: hashes 2.9 GB)."""
    iso = Path(record['tricky_iso'])
    if sha(iso) != record['tricky_iso_sha256']:
        raise ValueError('Tricky disc changed since the tricky step')
    verify_tricky_outputs(work, record)
    return True


def install(a):
    if a.app_id == PLAY_APP_ID:
        raise ValueError('public install refuses the maintainer play package')
    if not a.dry_run:
        ensure_install_toolchain(a)
    inputs = json.loads((a.work / 'inputs.json').read_text())
    disc_sources = verified_disc_payload(a.work, inputs)
    record = tricky_record(a.work)
    if a.dry_run:
        for source_path, relative in disc_sources:
            print(f'Would copy {source_path} ({source_path.stat().st_size} bytes) to {relative}')
        if record is not None:
            tricky = Path(a.work) / 'tricky'
            for name in TRICKY_DEVICE_FILES:
                candidate = tricky / name
                if candidate.is_file():
                    print(f'Would copy {candidate} ({candidate.stat().st_size} bytes) to {name}')
            for name in TRICKY_DEVICE_TREES:
                members = [p for p in (tricky / name).rglob('*') if p.is_file()] \
                    if (tricky / name).is_dir() else []
                if members:
                    total = sum(p.stat().st_size for p in members)
                    print(f'Would copy {tricky / name} ({len(members)} files, '
                          f'{total} bytes) to {name}/')
        else:
            print('No Tricky layer in this work directory; stock SSX 3 only.')
        return
    if record is not None:
        verified_tricky_payload(a.work, record)
    files = stage(a)
    apk = ROOT / 'apps/android/app/build/outputs/apk/release/app-release.apk'
    if not apk.is_file():
        raise ValueError('missing release APK; run android first')
    if apk_package(apk) != a.app_id:
        raise ValueError('release APK package differs from --app-id; refusing install')
    device = '/sdcard/Android/data/' + a.app_id + '/files'
    # The APK contains code and libraries only. adb install -r preserves app data.
    adb(a, 'install', '-r', apk)
    if not adb(a, 'shell', 'pm', 'path', a.app_id, capture=True).startswith('package:'):
        raise ValueError('installed public package not found')
    # Application.onCreate creates writable external data/card directories as
    # the app before the native activity loads. A boot without staged disc data
    # may exit immediately; only directory creation is needed here.
    try:
        # F6: bootstrap through the exported Launch60 alias (the trampoline),
        # never the non-exported NativeActivity directly.
        adb(a, 'shell', 'am', 'start', '-n', a.app_id + '/.Launch60')
        # am start returns before Application.onCreate runs. Wait for the app
        # to create both directories before stopping its bootstrap process.
        deadline = time.monotonic() + 30
        binary = resolve_adb(a)
        for directory in ('ge1-data', 'mc0'):
            remote = device + '/' + directory
            while subprocess.run([binary] + (['-s', a.serial] if a.serial else []) +
                                 ['shell', 'test', '-d', remote], check=False).returncode:
                if time.monotonic() >= deadline:
                    raise ValueError('app did not create ' + remote)
                time.sleep(0.25)
    finally:
        adb(a, 'shell', 'am', 'force-stop', a.app_id)
    sources = disc_sources + [(files / 'ps2x.env', 'ps2x.env'),
                              (files / 'full120.env', 'full120.env')]
    tricky_staged, tricky_trees = tricky_device_sources(files, record)
    sources += tricky_staged
    for source_path, relative in sources:
        remote = device + '/' + relative
        expected = sha(source_path)
        binary = resolve_adb(a)
        exists = subprocess.run([binary] + (['-s', a.serial] if a.serial else []) +
                                ['shell', 'test', '-f', shlex.quote(remote)], check=False).returncode == 0
        current = adb(a, 'shell', 'sha256sum', shlex.quote(remote), capture=True).split()[0] if exists else None
        if current == expected:
            print('SHA256 match, skip ' + relative)
            adb(a, 'shell', 'chmod', 'o+r', shlex.quote(remote))
            continue
        adb(a, 'push', source_path, remote)
        actual = adb(a, 'shell', 'sha256sum', shlex.quote(remote), capture=True).split()[0]
        if actual != expected:
            raise ValueError('device SHA256 mismatch: ' + relative)
        adb(a, 'shell', 'chmod', 'o+r', shlex.quote(remote))
    for tree in ('ge1-resources', 'font-pack') + tuple(tricky_trees):
        local = files / tree
        if not local.is_dir():
            continue
        adb(a, 'push', local, device + '/')
        adb(a, 'shell', 'chmod', '-R', 'o+rX', shlex.quote(device + '/' + tree))
        for source_path in sorted(local.rglob('*')):
            if source_path.is_file():
                remote = device + '/' + tree + '/' + source_path.relative_to(local).as_posix()
                actual = adb(a, 'shell', 'sha256sum', shlex.quote(remote), capture=True).split()[0]
                if actual != sha(source_path):
                    raise ValueError('device SHA256 mismatch: ' + remote)
    print('Installed ' + a.app_id + ' with matching files; existing cards and saves were not removed.')


TRICKY_LATER = ('Tricky courses arrive in a later version: --tricky-iso %s '
                'validated (ISO9660, %d bytes) but this checkout has no Tricky '
                'converter driver (expected tools/ssx3/tricky/build_tricky.py)')


TRICKY_DRIVER = Path(__file__).resolve().parent / 'tricky' / 'build_tricky.py'


def tricky_supported():
    """Whether this checkout can consume a Tricky disc.

    True when the converter driver exists at
    tools/ssx3/tricky/build_tricky.py; otherwise --tricky-iso validates
    cheaply and stops with TRICKY_LATER.
    """
    return ((ROOT / 'games' / 'tricky').is_dir() or TRICKY_DRIVER.is_file())


def check_tricky_iso_file(path):
    """Cheap --tricky-iso checks (exists + ISO9660); no driver gate."""
    iso = Path(path)
    if not iso.is_file():
        raise ValueError('tricky ISO not found: ' + str(iso))
    with iso.open('rb') as f:
        f.seek(16 * SECTOR)
        if f.read(7) != b'\x01CD001\x01':
            raise ValueError('not an ISO9660 disc image: ' + str(iso))
    return iso


def validate_tricky_iso(path):
    """Accept and validate --tricky-iso; fail clearly until the port lands."""
    iso = check_tricky_iso_file(path)
    if not tricky_supported():
        raise ValueError(TRICKY_LATER % (str(iso), iso.stat().st_size))
    return iso


def validate_ssx3_iso(path):
    """F12: cheap-to-full --ssx3-iso validation before any toolchain download.

    Missing files and non-ISO9660 inputs fail with the fix (previously a
    raw FileNotFoundError from sha()); a hash mismatch names the wanted
    USA pressing instead of the generic rejection arriving only after the
    JDK/SDK download in prepare().
    """
    iso = Path(path)
    if not iso.is_file():
        raise ValueError('SSX 3 ISO not found: ' + str(iso) +
                         ' (--ssx3-iso must point at your USA disc image; '
                         'see docs/ssx3-inputs.md)')
    with iso.open('rb') as f:
        f.seek(16 * SECTOR)
        if f.read(7) != b'\x01CD001\x01':
            raise ValueError('not an ISO9660 disc image: ' + str(iso))
    if sha(iso) != ISO_SHA:
        raise ValueError('unsupported disc SHA256 (want the SSX 3 USA '
                         'SLUS-207.72 pressing; other regions/pressings are '
                         'refused); see docs/ssx3-inputs.md')
    return iso.resolve()


def preflight_host_tools():
    """F12: native host-tool check before any toolchain download.

    Covers the tools doctor() checks that need no JDK/SDK/NDK and no disc
    (git, CMake, Ninja, pkg-config, host compiler, make, bison, flex).
    Raises naming the install line; the full doctor still runs after the
    toolchain resolves and validates the fetched pieces too.
    """
    system = platform.system()
    packages = ('sudo apt-get update && sudo apt-get install -y python3 git cmake '
                'ninja-build clang lld g++ pkg-config curl ca-certificates unzip zip '
                'make autoconf automake libtool bison flex openjdk-17-jdk') if system == 'Linux' else (
                'brew install cmake ninja llvm python pkg-config autoconf automake libtool bison')
    missing = []
    for tool in ('git', 'cmake', 'ninja', 'pkg-config', 'make', 'bison', 'flex'):
        ok, _ = probe([tool, '--version'])
        if not ok:
            missing.append(tool)
    try:
        compiler()
    except ValueError:
        missing.append('host compiler (clang)')
    if missing:
        raise ValueError('host preflight: missing ' + ', '.join(missing) +
                         '; install with: ' + packages)
    return True


def atomic_write_text(path, text):
    """F17: crash-safe receipt write (temp file + os.replace, same dir)."""
    path = Path(path)
    tmp = Path(str(path) + '.tmp')
    tmp.write_text(text)
    tmp.replace(path)


def recorded_prepare_pins():
    """Pins prepare() records in inputs.json: generator + config/map content."""
    return {'generator': GENERATOR,
            'toml_sha256': sha(ROOT / 'games/ssx3/ssx3.toml'),
            'map_sha256': sha(ROOT / 'games/ssx3/ssx3-functions.sweep.csv')}


def prepare_current(work, iso=None):
    """Whether prepare's outputs are current (same disc, pins, ELF, codegen).

    F16: beyond existence, the recorded generator/config/map pins must
    match this checkout; a newly supplied ISO path re-records (its bytes
    are hashed only when the path differs from the receipt).
    """
    try:
        inputs = json.loads((work / 'inputs.json').read_text())
    except (OSError, ValueError):
        return False
    if inputs.get('iso_sha256') != ISO_SHA:
        raise ValueError('work dir prepared from a different disc; use a fresh --work directory')
    for key, want in recorded_prepare_pins().items():
        if inputs.get(key) != want:
            print('prepare: ' + key + ' changed since prepare, regenerating')
            return False
    if iso is not None and str(Path(iso).resolve()) != inputs.get('iso'):
        try:
            same_pressing = sha(iso) == ISO_SHA
        except OSError:
            return False
        if not same_pressing:
            raise ValueError('work dir prepared from a different disc; use a fresh --work directory')
        print('prepare: ISO path changed, re-recording')
        return False
    return ((work / 'SLUS_207.72').is_file() and (work / 'codegen').is_dir()
            and Path(inputs.get('slim_iso') or '').is_file())


def android_libs_current(work):
    """Whether android-libs' staged libraries and resources are current.

    F16: recorded dependency/vendor pins must also match; a drifted pin
    rebuilds instead of silently reusing stale libraries.
    """
    jni = work / 'jniLibs/arm64-v8a'
    if not ((jni / 'libge1_gs.so').is_file() and (jni / 'libmv2_microvu.so').is_file()
            and (work / 'android-resources').is_dir()):
        return False
    try:
        recorded = json.loads((work / 'deps-build' / 'android-dependency-pins.json').read_text())
    except (OSError, ValueError):
        recorded = {}
    for name, _, _, commit in ANDROID_DEPS:
        if name in recorded and recorded[name] != commit:
            print('android-libs: dependency ' + name + ' pin changed, rebuilding')
            return False
    vendor = work / 'android-assembly/vendor-pcsx2'
    if (vendor / '.git').exists():
        try:
            actual = subprocess.check_output(['git', '-C', str(vendor), 'rev-parse', 'HEAD'],
                                             text=True).strip()
        except (OSError, subprocess.CalledProcessError):
            return False
        if actual != ANDROID_GE1_VENDOR_PIN:
            print('android-libs: GE1 vendor pin changed, rebuilding')
            return False
    return True


TRICKY_ELF_NAME = 'SLUS_203.26'
# Pinned Tricky disc identities: full-ISO SHA-256 -> serial, region, exact
# byte size and MD5. Only locally measured dumps are keys (see
# docs/tricky-inputs.md); any other pressing, including PAL/JP, is refused
# until its dump is measured and keyed here.
TRICKY_DISCS = {
    'deae53c10447e59c2c4cd1d3a904a1ed04cbc818444f4883962a0060da837970': {
        'serial': 'SLUS-20326', 'region': 'NTSC-U', 'bytes': 2902425600,
        'md5': '7d8ee567145bdf0c236ce5f5c679108a'},
}
# Internal markers after the ELF name: the per-course model archive and the
# music archive whose v3 .mpf members the music converter checks itself.
TRICKY_MARKERS = ('DATA/MODELS/GARI.BIG', 'DATA/AUDIO/MUSIC.BIG')
# Device-bound Tricky payload the stage step copies from <work>/tricky/:
# top-level files push beside ps2x.env; tricky-ov/ pushes as one tree.
# music-inf-tricky-big is always built; english-silent-big only when the
# english base is supplied (stage skips absent allowlist files, and the
# driver manifest names the omission instead of a dangling alias).
TRICKY_DEVICE_FILES = ('speech-tricky-big', 'music-tricky-big',
                       'music2-tricky-big', 'music-inf-tricky-big',
                       'english-silent-big', 'm-tricky.txt', 'tricky.env',
                       'trickyhud.bin', 'tricky-song.wav',
                       'tricky-gems.tsv', 'tricky-resetvol.tsv')
TRICKY_DEVICE_TREES = ('tricky-ov',)
# Text payloads whose baked device paths follow the app id (same treatment
# as ps2x.env's PUBLIC_APP_ID replace). The converter driver writes
# PUBLIC_APP_ID-based device paths; stage rewrites them to the built id.
TRICKY_ID_REWRITES = ('m-tricky.txt', 'tricky.env')


def sha2(path):
    """Double-read SHA-256 (inputs are re-read and compared, per lane convention)."""
    first, second = sha(path), sha(path)
    if first != second:
        raise ValueError('unstable read (two SHAs differ): ' + str(path))
    return first


def tricky_disc_identity(iso):
    """Full disc identity for a validated Tricky ISO (slow: hashes 2.9 GB).

    Checks the pinned key table, the exact byte size, the ELF name in the
    disc root and the internal markers, in that order. Refuses with the
    docs/tricky-inputs.md pointer on any mismatch.
    """
    iso = Path(iso)
    digest = sha(iso)
    try:
        disc = TRICKY_DISCS[digest]
    except KeyError:
        raise ValueError('unsupported Tricky disc SHA256; see docs/tricky-inputs.md')
    if iso.stat().st_size != disc['bytes']:
        raise ValueError('Tricky ISO byte size mismatch for ' + disc['serial'])
    names = {name.upper() for name, _, _ in iso_records(iso)}
    if TRICKY_ELF_NAME not in names:
        raise ValueError(TRICKY_ELF_NAME + ' missing from the Tricky disc root')
    for marker in TRICKY_MARKERS:
        if marker not in names:
            raise ValueError('Tricky disc marker missing: ' + marker)
    return {'sha256': digest, 'serial': disc['serial'], 'region': disc['region'],
            'bytes': disc['bytes'], 'md5': disc['md5']}


def tricky_inputs_path(work):
    return Path(work) / 'tricky_inputs.json'


_TRICKY_TOOLS = None


def tricky_tools():
    """tools/ssx3/tricky/tool_paths.py loaded by path (pinned tool
    resolution shared with the driver, without making tricky/ a package)."""
    global _TRICKY_TOOLS
    if _TRICKY_TOOLS is None:
        spec = importlib.util.spec_from_file_location(
            'tricky_tool_paths',
            Path(__file__).resolve().parent / 'tricky' / 'tool_paths.py')
        _TRICKY_TOOLS = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_TRICKY_TOOLS)
    return _TRICKY_TOOLS


def tricky_current(work, iso):
    """Whether <work>/tricky/ is current for this Tricky disc.

    The 2.9 GB image is not re-hashed: the recorded SHA must be a pinned
    key, the image size must match it, and every recorded output must be
    present with a matching double-read SHA. A size mismatch refuses like
    prepare's different-disc refusal.
    """
    record_file = tricky_inputs_path(work)
    try:
        record = json.loads(record_file.read_text())
    except (OSError, ValueError):
        return False
    disc = TRICKY_DISCS.get(record.get('tricky_iso_sha256'))
    if disc is None:
        return False
    if not record.get('outputs'):
        return False
    if Path(iso).stat().st_size != record.get('tricky_iso_bytes'):
        raise ValueError('work dir built from a different Tricky disc; use a fresh --work directory')
    # F06 (toolchain half): a resolved-toolchain or pin change invalidates
    # the skip. The driver writes its exact pins + tool identity to
    # <work>/tricky/tools.json; anything else (or a missing receipt) means
    # "not current" rather than a silent stale reuse.
    tools_receipt = Path(work) / 'tricky' / 'tools.json'
    try:
        receipt = json.loads(tools_receipt.read_text())
    except (OSError, ValueError):
        return False
    if receipt.get('pins') != record.get('pins'):
        return False
    if receipt.get('toolchain') != record.get('toolchain'):
        return False
    if receipt.get('ssx3', {}).get('sha256') != record.get('ssx3_iso_sha256'):
        return False
    tricky = Path(work) / 'tricky'
    for relative, entry in record.get('outputs', {}).items():
        candidate = tricky / relative
        if not candidate.is_file():
            return False
        try:
            match = sha2(candidate) == entry['sha256']
        except (TypeError, KeyError, AttributeError):
            return False
        if not match:
            return False
    return True


def record_tricky_inputs(work, iso, identity, argv, ssx3):
    """Write tricky_inputs.json beside inputs.json (double-read output SHAs).

    Copies the driver's tools.json pins + tool identity + SSX3 identity
    into the record so the skip-check can refuse stale reuse (F06).
    """
    tricky = Path(work) / 'tricky'
    outputs = {}
    for candidate in sorted(tricky.rglob('*')):
        if candidate.is_file():
            relative = candidate.relative_to(tricky).as_posix()
            outputs[relative] = {'sha256': sha2(candidate),
                                 'bytes': candidate.stat().st_size}
    try:
        receipt = json.loads((tricky / 'tools.json').read_text())
    except (OSError, ValueError):
        raise ValueError('tricky driver left no tools.json; refusing to record')
    try:
        repo_commit = subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'],
                                              text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        repo_commit = 'unknown'
    record = {'tricky_iso': str(iso), 'tricky_iso_sha256': identity['sha256'],
              'tricky_iso_bytes': identity['bytes'], 'tricky_serial': identity['serial'],
              'ssx3_iso': str(ssx3['path']), 'ssx3_iso_sha256': ssx3['sha256'],
              'ssx3_iso_bytes': ssx3['bytes'],
              'pins': receipt.get('pins'), 'toolchain': receipt.get('toolchain'),
              'driver': {'path': str(TRICKY_DRIVER.relative_to(ROOT))
                         if TRICKY_DRIVER.is_relative_to(ROOT) else str(TRICKY_DRIVER),
                         'argv': list(map(str, argv)), 'repo_commit': repo_commit},
              'outputs': outputs,
              'created_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    tricky_inputs_path(work).write_text(json.dumps(record, indent=2) + '\n')
    return record


def build_tricky(a, iso):
    """New step (not a prepare flag): generate the Tricky layer from a second disc.

    Requires a prepared --work (inputs.json): the Tricky layer is optional
    and keeps stock-only prepare -> stage -> install untouched. With no
    --iso there is no <work>/tricky/ and stage writes no Tricky layer.
    """
    if not (Path(a.work) / 'inputs.json').is_file():
        raise ValueError('tricky requires a prepared work directory (run prepare first)')
    iso = validate_tricky_iso(Path(iso).resolve())
    identity = tricky_disc_identity(iso)
    print('Tricky disc: %s %s (%d bytes, SHA-256 %s...).' % (
        identity['serial'], identity['region'], identity['bytes'], identity['sha256'][:16]))
    inputs = json.loads((Path(a.work) / 'inputs.json').read_text())
    # F13: the SSX3 ISO may have moved or changed since prepare; re-hash
    # it here instead of trusting the old record's path.
    ssx3 = Path(inputs['iso'])
    if not ssx3.is_file() or sha(ssx3) != inputs.get('iso_sha256'):
        raise ValueError('SSX3 ISO changed since prepare ('
                         + str(ssx3) + '); re-run prepare in a fresh --work directory')
    ssx3_ident = {'path': str(ssx3), 'sha256': inputs['iso_sha256'],
                  'bytes': ssx3.stat().st_size}
    if tricky_current(a.work, iso):
        print('tricky: outputs current for this disc, skipping')
        return tricky_inputs_path(a.work)
    tricky = Path(a.work) / 'tricky'
    if tricky.exists():
        raise ValueError('refusing existing tricky dir; remove ' + str(tricky) +
                         ' or choose a fresh --work directory')
    # Audio tools are optional local installs resolved by the driver
    # (probe-only, no downloads, so this stays after the skip-check and
    # needs no wrapper resolution). Forward the requested audio mode.
    audio_req = getattr(a, 'tricky_audio', None) or 'auto'
    if audio_req not in ('on', 'off', 'auto'):
        raise ValueError("--tricky-audio must be on|off|auto: %r" % (audio_req,))
    # Port-owned orchestration driver (tk-port-1/tk-port-2): it reads the
    # user's own discs and writes the <work>/tricky/ device layout (plus any
    # intermediates); this step only validates, invokes and records.
    # Proposed contract: build_tricky.py --tricky-iso <iso> --ssx3-iso <iso>
    # --work <work>/tricky [--jobs N] [--tricky-audio on|off|auto].
    argv = [sys.executable, str(TRICKY_DRIVER), '--tricky-iso', str(iso),
            '--ssx3-iso', str(ssx3), '--work', str(tricky),
            '--jobs', str(a.jobs), '--tricky-audio', audio_req]
    tools = tricky_tools()
    for flag, attr, env in (('--vgmstream', 'vgmstream', tools.VGMSTREAM_ENV),
                            ('--ffmpeg', 'ffmpeg', tools.FFMPEG_ENV)):
        val = getattr(a, attr, None) or os.environ.get(env)
        if val:
            argv += [flag, str(val)]
    run(*argv)
    record = record_tricky_inputs(a.work, iso, identity, argv, ssx3_ident)
    print('Tricky layer ready: %d files in %s.' % (len(record['outputs']), tricky))
    return tricky_inputs_path(a.work)


def tricky_dry_run(a):
    """Validate the Tricky disc and list the payload the tricky step would build."""
    iso = check_tricky_iso_file(Path(a.iso).resolve())
    identity = tricky_disc_identity(iso)
    print('Tricky disc: %s %s (%d bytes, SHA-256 %s).' % (
        identity['serial'], identity['region'], identity['bytes'], identity['sha256']))
    if tricky_supported():
        print('Converter driver present: ' + str(TRICKY_DRIVER))
    else:
        print('Converter driver missing: the tricky step stops after validation; '
              'see docs/tricky-inputs.md')
    print('Planned device files (beside ps2x.env):')
    for name in TRICKY_DEVICE_FILES:
        print('  ' + name)
    print('Planned device trees:')
    for name in TRICKY_DEVICE_TREES:
        print('  ' + name + '/')
    print('Nothing was built or staged.')


def tricky_record(work):
    """The tricky_inputs.json record, or None when no Tricky layer was built."""
    record_file = tricky_inputs_path(work)
    if not record_file.is_file():
        return None
    return json.loads(record_file.read_text())


def verify_tricky_outputs(work, record):
    """Re-verify staged Tricky outputs against the build record (fail closed)."""
    tricky = Path(work) / 'tricky'
    for relative, entry in record['outputs'].items():
        candidate = tricky / relative
        if not candidate.is_file() or sha2(candidate) != entry['sha256']:
            raise ValueError('Tricky output changed since build_tricky: ' + relative +
                             '; re-run the tricky step')
    return True


def tricky_device_sources(files, record):
    """(source, device-relative) pairs for the staged Tricky layer.

    Pure helper so stage/install/dry-run share the layout: allowlisted top
    files push beside ps2x.env; allowlisted trees push as whole trees.
    Returns ([files], [trees]); both empty when no layer was staged.
    """
    if record is None:
        return [], []
    staged = [(files / name, name) for name in TRICKY_DEVICE_FILES
              if (files / name).is_file()]
    trees = [name for name in TRICKY_DEVICE_TREES if (files / name).is_dir()]
    return staged, trees


def stage_tricky(a, files):
    """Copy the built Tricky layer into the staged device layout (APK path).

    Copies only the allowlisted device payload, rewrites the baked
    PUBLIC_APP_ID/PLAY_APP_ID device paths to the built --app-id in the two
    text payloads, then re-verifies every staged byte against
    tricky_inputs.json. Without <work>/tricky/ this is a no-op and the app
    boots stock SSX 3 only. Returns (staged_files, staged_trees).
    """
    record = tricky_record(a.work)
    if record is None:
        return [], []
    verify_tricky_outputs(a.work, record)
    staged, trees = [], []
    for name in TRICKY_DEVICE_FILES:
        source, target = Path(a.work) / 'tricky' / name, files / name
        if not source.is_file():
            continue
        if name in TRICKY_ID_REWRITES:
            text = source.read_text()
            for old in (PUBLIC_APP_ID, PLAY_APP_ID):
                text = text.replace(old, a.app_id)
            target.write_text(text)
        else:
            shutil.copy2(source, target)
        staged.append((target, name))
    for name in TRICKY_DEVICE_TREES:
        source, target = Path(a.work) / 'tricky' / name, files / name
        if not source.is_dir():
            continue
        if target.is_dir():
            shutil.rmtree(target)
        shutil.copytree(source, target)
        trees.append(name)
    # Re-verify the staged copies (text payloads legitimately differ by id).
    for target, name in staged:
        if name in TRICKY_ID_REWRITES:
            continue
        expected = record['outputs'].get(name)
        if expected is None or sha2(target) != expected['sha256']:
            raise ValueError('staged Tricky file mismatch: ' + name)
    for name in trees:
        for candidate in sorted((files / name).rglob('*')):
            if not candidate.is_file():
                continue
            relative = (name + '/' + candidate.relative_to(files / name).as_posix())
            expected = record['outputs'].get(relative)
            if expected is None or sha2(candidate) != expected['sha256']:
                raise ValueError('staged Tricky file mismatch: ' + relative)
    return staged, trees


def ensure_android_toolchain(a):
    """Point this process at a usable JDK/SDK/NDK, fetching only when allowed.

    Uses the builder-managed toolchain under --work when present, otherwise
    system and common installs; downloads the pinned archives when missing
    unless --no-auto-download is set. Any use of the managed SDK requires
    --accept-android-licenses (or an interactive yes). Sets a.ndk to the
    pinned NDK. Idempotent: repeat calls only re-run read-only discovery.
    """
    import toolchain
    if toolchain.NDK_VERSION != NDK_VERSION or toolchain.SDK_PACKAGES != SDK_PACKAGES:
        raise ValueError('toolchain pin drift: toolchain.py disagrees with build.py')
    if not getattr(a, 'work', None):
        raise ValueError('--work is required for Android builds')
    env = toolchain.resolve(a.work, jdk_explicit=getattr(a, 'jdk', None),
                            ndk_explicit=getattr(a, 'ndk', None),
                            auto_download=not getattr(a, 'no_auto_download', False),
                            accept_licenses=getattr(a, 'accept_android_licenses', False))
    toolchain.apply(env)
    a.ndk = Path(env['ANDROID_NDK_HOME'])
    return env


def android_onecmd(a):
    """One-command Android build: doctor → prepare → android-libs → android (→ install).

    Resumable: prepare and android-libs are skipped when their outputs are
    current for this disc. android always runs (Gradle is incremental); pass
    --install to push to a connected device afterwards.
    """
    if a.tricky_iso:
        validate_tricky_iso(a.tricky_iso)
    if not a.work:
        a.work = Path.home() / 'ssx3-work'
    a.work = a.work.resolve()
    if a.work == ROOT or ROOT in a.work.parents:
        raise ValueError('--work must be outside the source checkout')
    # F03: create WORK before any step writes into it.
    a.work.mkdir(parents=True, exist_ok=True)
    if not a.ssx3_iso:
        raise ValueError('android one-command needs --ssx3-iso <your-USA-disc>')
    # F15: disk preflight before anything heavy (measured budgets in README).
    check_capacity(a.work, 'android')
    # F12: disc identity and native host tools before any JDK/SDK download.
    a.iso = validate_ssx3_iso(a.ssx3_iso)
    preflight_host_tools()
    ensure_android_toolchain(a)
    a.target = 'android'
    doctor(a)
    if prepare_current(a.work, a.iso):
        print('prepare: outputs current for this disc, skipping')
    else:
        prepare(a)
    if a.tricky_iso:
        build_tricky(a, a.tricky_iso)
    if android_libs_current(a.work):
        print('android-libs: staged libraries current, skipping')
    else:
        android_libs(a)
    if not a.prebuilt:
        # Source route: the staged libraries are current (just built or
        # skipped as current), so android must not rebuild them. With
        # --prebuilt the bundle lookup in android() decides instead.
        a.jnilibs = a.work / 'jniLibs'
    android(a)
    if a.install:
        install(a)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('step', choices=['doctor', 'prepare', 'mac', 'mac-libs', 'run', 'android', 'android-libs', 'stage', 'install', 'ios', 'ios-libs', 'tricky', 'vu0'])
    p.add_argument('--work', type=Path, help='external private output directory')
    p.add_argument('--target', choices=['prepare', 'android', 'mac', 'ios'], default='android', help='doctor prerequisite set')
    p.add_argument('--tools-only', action='store_true', help='prepare the native generator without reading a disc')
    p.add_argument('--iso', type=Path, help='own supported disc image (prepare: SSX 3 USA; tricky: Tricky)')
    p.add_argument('--ssx3-iso', '--ssx3-disc', dest='ssx3_iso', type=Path,
                   help='own supported USA disc image: android becomes the one-command build (doctor, prepare, android-libs, android)')
    p.add_argument('--tricky-iso', '--tricky-disc', dest='tricky_iso', type=Path,
                   help='own Tricky disc image: validated always; the tricky step runs it when the converter driver is present')
    p.add_argument('--install', action='store_true',
                   help='with android --ssx3-iso: install to a connected device after the build')
    p.add_argument('--accept-android-licenses', action='store_true',
                   help='accept the Android SDK licences for the builder-managed SDK (required to fetch or use it; terms print first, never silent)')
    p.add_argument('--no-auto-download', action='store_true',
                   help='never fetch JDK/SDK/NDK: use only installed toolchains, else fail with a precise missing-X message')
    p.add_argument('--jdk', type=Path, help='local JDK 17 installation (default: JAVA_HOME, managed toolchain, common paths)')
    p.add_argument('--symbol-names', type=Path, help='optional local names sidecar for development codegen')
    p.add_argument('--serial', help='adb device serial for install')
    p.add_argument('--team', help='Apple Developer Team ID for ios (10 characters; Xcode signs with this team)')
    p.add_argument('--device', help='iOS device UDID for ios install (default: the single connected device)')
    p.add_argument('--bundle-id', help='iOS bundle id (default: com.ssx3recomp.<team>)')
    p.add_argument('--om1-stage', type=Path, help='offline VU1 stage directory override (default: generated from your disc into work/om1-stage; ios app only)')
    p.add_argument('--vu0-stage', type=Path, help='VU0 image directory override (default: work/vu0-stage rebuilt from your disc by the vu0 step; android/ios/mac builds)')
    p.add_argument('--dry-run', action='store_true', help='install: check and list disc payload without touching a device; tricky: validate the disc and list the planned payload without building')
    p.add_argument('--jnilibs', type=Path, help='own source-built Android JNI libraries')
    p.add_argument('--prebuilt', action='store_true', help='opt in to verified prebuilt Android support libraries (default: build them from source)')
    p.add_argument('--prebuilt-cache', type=Path, help='verified offline support cache (default: WORK/prebuilt-cache)')
    p.add_argument('--no-turnip', action='store_true', help='omit the optional Turnip driver')
    p.add_argument('--stock-dump', type=Path, help='own texture dump holding the four stock font atlases (play builds the font pack from it)')
    p.add_argument('--skip-fonts', action='store_true', help='skip the default raster-edge font pack build in play')
    p.add_argument('--ndk', type=Path, help='local Android NDK 28.2.13676358')
    p.add_argument('--pgo-profile', type=Path, help='explicit .profdata for android/ios PGO-use (default: bundled tools/ssx3/pgo/<platform> when fresh for your codegen)')
    p.add_argument('--no-pgo', action='store_true', help='build without PGO even when a fresh bundled profile exists')
    p.add_argument('--suite', action='store_true', help='also build optional Mac development suite and its analyzer/recompiler fixtures')
    p.add_argument('--det', action='store_true', help='mac flavor with the VBlank det-hash tap (gate boots + tap unit tests; separate runner-build-det dir)')
    p.add_argument('--jobs', type=int, default=8)
    p.add_argument('--tricky-audio', default='auto', choices=['on', 'off', 'auto'],
                   help='tricky step audio phases (need local ffmpeg + vgmstream): '
                        'on fails without them, off skips them, auto runs them when present')
    p.add_argument('--app-id', default=PUBLIC_APP_ID, help='Android package id for android, stage and install')
    p.add_argument('--app-label', default=None,
                   help="home-screen name for android and ios (default: 'SSX3 120' for product ids, 'SSX3 120 Test' otherwise)")
    p.add_argument('--dev', action='store_true',
                   help='lab/dev build: keep get-task-allow (iOS), debuggable + shell-profileable (Android)')
    p.add_argument('--engine', type=Path, default=None,
                   help='engine checkout (default: SSX3_ENGINE_DIR or deps/PS2Recomp submodule)')
    a = p.parse_args()
    device_flag_error = check_device_flags(a.step, a.device, a.serial)
    if device_flag_error:
        p.error(device_flag_error)
    global ENGINE
    if a.engine is not None:
        ENGINE = a.engine.resolve()
    if not (ENGINE / 'ps2xRuntime/CMakeLists.txt').is_file():
        p.error('--engine must point at the pinned PS2Recomp checkout (missing ps2xRuntime/CMakeLists.txt): ' + str(ENGINE))
    actual_engine = subprocess.check_output(['git', '-C', str(ENGINE), 'rev-parse', 'HEAD'], text=True).strip()
    print('engine: ' + str(ENGINE) + ' @ ' + actual_engine)
    if a.step == 'doctor':
        doctor(a)
        return
    if not supported_host():
        p.error('supported hosts: macOS Apple silicon, Linux x86_64/arm64; Windows users run Ubuntu in WSL2')
    if a.step in ('mac', 'mac-libs', 'run', 'ios', 'ios-libs') and platform.system() != 'Darwin':
        p.error(a.step + ' requires macOS Apple silicon; Linux hosts build Android APKs')
    if not a.work:
        p.error('--work is required for build steps')
    if a.tools_only and a.step != 'prepare':
        p.error('--tools-only is only valid with prepare')
    a.work = a.work.resolve()
    if a.work == ROOT or ROOT in a.work.parents:
        p.error('--work must be outside the source checkout')
    if a.jobs < 1 or a.jobs > 16:
        p.error('--jobs must be 1..16')
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+', a.app_id):
        p.error('--app-id must be a dotted Android application id')
    if a.app_label is not None and not a.app_label.strip():
        p.error('--app-label must be a non-empty display name')
    if a.tricky_iso:
        validate_tricky_iso(a.tricky_iso)
    if a.step == 'android' and a.ssx3_iso:
        android_onecmd(a)
        return
    if a.step == 'ios' and a.ssx3_iso:
        ios_onecmd(a)
        return
    if a.ssx3_iso:
        p.error('--ssx3-iso is only valid with the android/ios steps')
    if a.install:
        p.error('--install is only valid with android/ios --ssx3-iso')
    if a.step == 'prepare' and not a.iso and not a.tools_only:
        p.error('prepare requires --iso')
    if a.step == 'tricky' and not a.iso:
        p.error('tricky requires --iso (the Tricky disc)')
    if a.step == 'tricky' and a.tricky_iso:
        p.error('--tricky-iso is only valid with the android step; the tricky step takes --iso')
    if a.step == 'tricky' and a.dry_run:
        tricky_dry_run(a)
        return
    if needs_prepare(a.step, a.dry_run) and not (a.work / 'inputs.json').is_file():
        p.error('run prepare in this work directory first')
    a.work.mkdir(parents=True, exist_ok=True)
    if a.step == 'tricky':
        build_tricky(a, a.iso)
        return
    {'prepare': prepare, 'mac': mac, 'android': android, 'mac-libs': libs, 'run': play,
     'android-libs': android_libs, 'stage': stage, 'install': install,
     'ios-libs': ios_libs, 'ios': ios, 'vu0': vu0}[a.step](a)


if __name__ == '__main__':
    main()
