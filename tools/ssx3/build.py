#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy", "Pillow"]
# ///
"""Local SSX 3 preparation. Never downloads or publishes game data.

Run directly (`python3 tools/ssx3/build.py ...`) or with only `uv`
installed (`./build ...`, which execs `uv run --script` on this file).
The inline metadata above lets uv provision Python plus the font-pack
dependencies (numpy/Pillow, used by the remaster_font subprocess).
"""
import argparse
import hashlib
import json
import os
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


def prepare(a):
    if a.tools_only:
        prepare_generator(a)
        print('Native generator prepared; no disc read and no game sources generated.')
        return
    iso = a.iso.resolve()
    if sha(iso) != ISO_SHA:
        raise ValueError('unsupported disc SHA256; see docs/ssx3-inputs.md')
    elf = a.work / 'SLUS_207.72'
    extract_elf(iso, elf)
    if sha(elf) != ELF_SHA:
        raise ValueError('extracted ELF SHA256 mismatch')
    slim = write_slim(iso, a.work / 'SSX3.iso', slim_holes(iso), ISO_SHA)
    build = prepare_generator(a)
    output = a.work / 'codegen'
    if output.exists():
        raise ValueError('refusing existing codegen; choose a fresh --work directory')
    config = (ROOT / 'games/ssx3/ssx3.toml').read_text()
    # All local paths are anchored absolute in the private work directory.
    paths = {'input': elf, 'ghidra_output': ROOT / 'games/ssx3/ssx3-functions.sweep.csv', 'output': output}
    for key, value in paths.items():
        config = re.sub(r'^' + key + r' = .*$', key + ' = ' + json.dumps(str(value)), config, flags=re.M)
    if a.symbol_names and not a.symbol_names.is_file():
        raise ValueError('missing optional symbol names sidecar: ' + str(a.symbol_names))
    if a.symbol_names:
        config = config.replace('[general]\n',
            '[general]\nsymbol_names = ' + json.dumps(str(a.symbol_names.resolve())) + '\n', 1)
    toml = a.work / 'generation.toml'
    toml.write_text(config)
    run(build / 'ps2xRecomp/ps2_recomp', toml)
    register_sha = sha(output / 'register_functions.cpp')
    (a.work / 'inputs.json').write_text(json.dumps({'iso': str(iso), 'iso_sha256': ISO_SHA,
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
        '-DPS2X_VU0_RECOMP_DIR=', '-DPS2X_BUILD_RECOMP=' + extra, '-DPS2X_BUILD_ANALYZER=' + extra,
        '-DPS2X_BUILD_TEST=' + extra, '-DPS2X_ENABLE_DEBUG_UI=OFF', '-DPS2X_ENABLE_RUNTIME_LOGS=OFF',
        '-DPS2X_ENABLE_AGRESSIVE_LOGS=OFF', '-DPS2X_ENABLE_DIAG_TAPS=OFF',
        '-DPS2X_ENABLE_DIAG_WATCH=OFF', '-DPS2X_ENABLE_TS2_DIAG=OFF',
        '-DPS2X_ENABLE_DET_HASH_TAP=' + det, '-DPS2X_GAME_PCH=ON')
    run('cmake', '--build', build_dir, '--parallel', a.jobs,
        '--target', 'ps2EntryRunner', *(['ps2x_tests'] if a.suite else []))


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
            print('Turnip included (SHA-256 ' + manifest['binary']['sha256'] + ').')
        except (OSError, ValueError, zipfile.BadZipFile) as e:
            turnip_path.unlink(missing_ok=True)
            print('Turnip unavailable (' + str(e) + '); continuing with system Vulkan.', file=sys.stderr)
    print('Signing with local keystore ' + str(keystore) + ' (versionCode ' + str(version_code) + ').')
    print('Back up your keystore; a different key forces an uninstall, which wipes saves.')
    run('bash', ROOT / 'apps/android/gradlew', '-p', ROOT / 'apps/android', 'assembleRelease',
        '-Pps2xEngineCMakeLists=' + str(ENGINE / 'CMakeLists.txt'),
        '-Pps2xGameCodegenDir=' + str(a.work / 'codegen'),
        '-Pps2xJniLibsDir=' + str(jnilibs), '-Pps2xPublicNoticesDir=' + str(notices_dir),
        '-Pps2xVu0RecompDir=',
        '-Pps2xGamePch=ON',
        '-Pps2xPgoData=', '-Pps2xBootElf=/storage/emulated/0/Android/data/' + a.app_id + '/files/SLUS_207.72',
        '-Pps2xAppId=' + a.app_id,
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
    source('https://github.com/brad-richardson/armsx2.git',
           'afad5a04c2668a10e12adadaa51d54c46f832236', vendor)
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
OM1_REPLAY_PIN = '5feeabfcfebfca2b49dcc3fe6681656185b49607'
OM1_REPLAY_SHORT = '5feeabfcf'
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
    """Whether ios-libs' staged archives are present (resume skip)."""
    libs = work / 'ios-libs'
    return ((libs / 'libge1_gs.a').is_file() and (libs / 'libPCSX2.a').is_file()
            and (libs / 'libcommon.a').is_file()
            and (libs / 'om1/pcsx2/libPCSX2.a').is_file()
            and (libs / 'libSDL2.a').is_file()
            and (libs / 'MoltenVK.framework').is_dir()
            and (work / 'ios-ffmpeg/lib/libavcodec.a').is_file())


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


def sign_staged_app(work, stage_app, team, bundle_id):
    """Sign the staged app with the user's team (profile from Xcode's
    automatic signing; entitlements generated, never committed)."""
    import plistlib
    profile = find_provisioning_profile(team, bundle_id)
    identity = signing_identity(team, profile=profile)
    shutil.copy2(profile, stage_app / 'embedded.mobileprovision')
    entitlements = {'application-identifier': team + '.' + bundle_id,
                    'com.apple.developer.team-identifier': team,
                    'get-task-allow': True}
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
    user's SLUS_207.72, run all 9 areas into work/om1-stage, copy in the
    offline-core sources, and link stage/armsx2 at the fetched vendor tree
    (the engine compiles the stage's StubHost.cpp against that full tree)."""
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
    stage = a.work / 'om1-stage'
    stage.mkdir(parents=True, exist_ok=True)
    for area in OM1_AREAS:
        run(str(tool), str(OM1_MANIFEST), str(elf), area, str(stage))
    for name in OM1_OFFLINE_CORE:
        shutil.copy2(OM1_OFFLINE_CORE_DIR / name, stage / name)
    link_om1_vendor(stage, vendor)
    validate_om1_stage(stage)
    core_hashes = om1_core_hashes()
    if core_hashes is None:
        raise ValueError('ios-om1: a canonical offline-core source is missing under '
                         + str(OM1_OFFLINE_CORE_DIR))
    output_hashes = om1_stage_file_hashes(stage)
    if output_hashes is None:
        raise ValueError('ios-om1: cannot hash the generated stage under ' + str(stage))
    ios_om1_receipt_path(a.work).write_text(json.dumps({
        'replay_pin': OM1_REPLAY_PIN, 'manifest_sha256': sha(OM1_MANIFEST),
        'elf_sha256': elf_sha, 'core_sha256': core_hashes,
        'outputs_sha256': output_hashes}))
    print('  offline VU1 stage regenerated ' + str(stage))
    return stage


def ios(a):
    team = check_team(a)
    bundle_id = check_bundle_id(getattr(a, 'bundle_id', None) or default_bundle_id(team))
    if a.dry_run:
        print('Would build the iOS app (no Xcode, no device touched):')
        print('  team ' + team + ', bundle id ' + bundle_id)
        print('  engine ' + str(ENGINE))
        print('  GE1 vendor ' + GE1_VENDOR_REPO + ' @ ' + GE1_VENDOR_SHORT)
        print('  OM1 vendor ' + OM1_VENDOR_REPO + ' @ ' + OM1_VENDOR_SHORT)
        print('  offline VU1 stage ' + str(getattr(a, 'om1_stage', None) or 'generated work/om1-stage'))
        print('  prepare outputs ' + ('current' if prepare_current(a.work) else 'MISSING (run prepare)') + ': '
              + str(a.work / 'codegen'))
        print('  ios-libs ' + ('current' if ios_libs_current(a.work) else 'MISSING (run ios-libs)') + ': '
              + str(a.work / 'ios-libs'))
        print('  stage into ' + str(a.work / 'ios-stage') + ' (SLUS_207.72, slim ISO, '
              'presets/ios.env, presets/full120-ios.env, Settings.bundle, MoltenVK.framework)')
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
    build_dir = a.work / 'ios-app-build'
    run('cmake', '-S', ENGINE, '-B', build_dir, '-G', 'Xcode',
        '-DCMAKE_TOOLCHAIN_FILE=' + str(ROOT / 'tools/ssx3/ios/toolchain.cmake'),
        '-DCMAKE_CONFIGURATION_TYPES=Release',
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
        '-DCMAKE_XCODE_ATTRIBUTE_PRODUCT_BUNDLE_IDENTIFIER=' + bundle_id,
        '-DCMAKE_XCODE_ATTRIBUTE_DEVELOPMENT_TEAM=' + team,
        '-DCMAKE_XCODE_ATTRIBUTE_CODE_SIGN_STYLE=Automatic')
    run('cmake', '--build', build_dir, '--config', 'Release',
        '--target', 'ps2EntryRunner', '--', '-jobs', str(a.jobs))
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
    for name in ('ios.env', 'full120-ios.env'):
        src = ROOT / 'presets' / name
        if not src.is_file():
            raise ValueError('missing shipped preset: ' + str(src))
        shutil.copy2(src, app / ('ps2x.env' if name == 'ios.env' else name))
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
    subprocess.run(['find', str(app), '-name', '._*', '-delete'], check=False)
    identity = sign_staged_app(a.work, app, team, bundle_id)
    (a.work / 'ios-app.json').write_text(json.dumps({
        'team': team, 'bundle_id': bundle_id, 'identity': identity,
        'engine': subprocess.check_output(['git', '-C', str(ENGINE), 'rev-parse', 'HEAD'],
                                          text=True).strip(),
        'ge1_vendor': GE1_VENDOR_PIN, 'om1_vendor': receipt.get('om1_vendor'),
        'om1_stage': str(om1_stage), 'om1_lib': str(om1_lib),
        'slim_sha256': inputs['slim_sha256'], 'elf_sha256': inputs['elf_sha256'],
        'signed_binary_sha256': sha(app / 'ps2EntryRunner'),
        'preset_sha256': sha(app / 'ps2x.env')}, indent=2, sort_keys=True) + '\n')
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
    a.iso = a.ssx3_iso.resolve()
    doctor(a)
    if a.dry_run:
        ios(a)
        return
    if prepare_current(a.work):
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
    resources = a.work / 'android-resources'
    if not resources.is_dir():
        raise ValueError('stage requires android-libs first (missing android-resources in work dir)')
    target = files / 'ge1-resources'
    shutil.copytree(resources, target, dirs_exist_ok=True)
    (files / 'ge1-data').mkdir(exist_ok=True)
    (files / 'mc0').mkdir(exist_ok=True)
    device = '/storage/emulated/0/Android/data/' + a.app_id + '/files'
    stage_fonts(a, inputs, files, device)
    apk = ROOT / 'apps/android/app/build/outputs/apk/release/app-release.apk'
    print('Staged device layout in ' + str(files))
    if a.step != 'stage':
        return files
    print('Push it with these commands (stage prints them; it does not run them):')
    print('  adb install -r ' + str(apk))
    print('  adb shell am start -n ' + a.app_id + '/android.app.NativeActivity')
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
    print('  adb shell chmod -R o+rX ' + device + '/ge1-resources')
    print('  adb shell chmod o+r ' + device + '/SLUS_207.72 ' + device + '/SSX3.iso ' +
          device + '/SSX3.iso.holes ' + device + '/ps2x.env ' + device + '/full120.env')
    print('The app creates ge1-data and the empty mc0 card dir on first launch.')
    print('Back up your keystore; a different key forces an uninstall, which wipes saves.')
    return files


def adb(a, *args, capture=False):
    command = ['adb'] + (['-s', a.serial] if a.serial else []) + list(map(str, args))
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
    if ndk is not None:
        a.ndk = ndk
    java_env = str(java_home) if java_home is not None else os.environ.get('JAVA_HOME')
    if sdk_managed and java_env:
        toolchain.require_licenses(sdk, getattr(a, 'accept_android_licenses', False), java_env)


def install(a):
    if a.app_id == PLAY_APP_ID:
        raise ValueError('public install refuses the maintainer play package')
    if not a.dry_run:
        ensure_install_toolchain(a)
    inputs = json.loads((a.work / 'inputs.json').read_text())
    disc_sources = verified_disc_payload(a.work, inputs)
    if a.dry_run:
        for source_path, relative in disc_sources:
            print(f'Would copy {source_path} ({source_path.stat().st_size} bytes) to {relative}')
        return
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
        adb(a, 'shell', 'am', 'start', '-n', a.app_id + '/android.app.NativeActivity')
        # am start returns before Application.onCreate runs. Wait for the app
        # to create both directories before stopping its bootstrap process.
        deadline = time.monotonic() + 30
        for directory in ('ge1-data', 'mc0'):
            remote = device + '/' + directory
            while subprocess.run(['adb'] + (['-s', a.serial] if a.serial else []) +
                                 ['shell', 'test', '-d', remote], check=False).returncode:
                if time.monotonic() >= deadline:
                    raise ValueError('app did not create ' + remote)
                time.sleep(0.25)
    finally:
        adb(a, 'shell', 'am', 'force-stop', a.app_id)
    sources = disc_sources + [(files / 'ps2x.env', 'ps2x.env'),
                              (files / 'full120.env', 'full120.env')]
    for source_path, relative in sources:
        remote = device + '/' + relative
        expected = sha(source_path)
        exists = subprocess.run(['adb'] + (['-s', a.serial] if a.serial else []) +
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
    for tree in ('ge1-resources', 'font-pack'):
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
                'validated (ISO9660, %d bytes) but this checkout has no Tricky port')


def tricky_supported():
    """Whether this checkout can consume a Tricky disc (tk-build-1 wires it)."""
    return ((ROOT / 'games' / 'tricky').is_dir()
            or globals().get('build_tricky') is not None)


def validate_tricky_iso(path):
    """Accept and validate --tricky-iso; fail clearly until the port lands."""
    iso = Path(path)
    if not iso.is_file():
        raise ValueError('tricky ISO not found: ' + str(iso))
    with iso.open('rb') as f:
        f.seek(16 * SECTOR)
        if f.read(7) != b'\x01CD001\x01':
            raise ValueError('not an ISO9660 disc image: ' + str(iso))
    if not tricky_supported():
        raise ValueError(TRICKY_LATER % (str(iso), iso.stat().st_size))
    return iso


def prepare_current(work):
    """Whether prepare's outputs are current (same disc, ELF, slim, codegen)."""
    try:
        inputs = json.loads((work / 'inputs.json').read_text())
    except (OSError, ValueError):
        return False
    if inputs.get('iso_sha256') != ISO_SHA:
        raise ValueError('work dir prepared from a different disc; use a fresh --work directory')
    return ((work / 'SLUS_207.72').is_file() and (work / 'codegen').is_dir()
            and Path(inputs.get('slim_iso') or '').is_file())


def android_libs_current(work):
    """Whether android-libs' staged libraries and resources are current."""
    jni = work / 'jniLibs/arm64-v8a'
    return ((jni / 'libge1_gs.so').is_file() and (jni / 'libmv2_microvu.so').is_file()
            and (work / 'android-resources').is_dir())


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
    ensure_android_toolchain(a)
    a.target = 'android'
    a.iso = a.ssx3_iso.resolve()
    doctor(a)
    if prepare_current(a.work):
        print('prepare: outputs current for this disc, skipping')
    else:
        prepare(a)
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
    p.add_argument('step', choices=['doctor', 'prepare', 'mac', 'mac-libs', 'run', 'android', 'android-libs', 'stage', 'install', 'ios', 'ios-libs'])
    p.add_argument('--work', type=Path, help='external private output directory')
    p.add_argument('--target', choices=['prepare', 'android', 'mac', 'ios'], default='android', help='doctor prerequisite set')
    p.add_argument('--tools-only', action='store_true', help='prepare the native generator without reading a disc')
    p.add_argument('--iso', type=Path, help='own supported USA disc image (prepare only)')
    p.add_argument('--ssx3-iso', '--ssx3-disc', dest='ssx3_iso', type=Path,
                   help='own supported USA disc image: android becomes the one-command build (doctor, prepare, android-libs, android)')
    p.add_argument('--tricky-iso', '--tricky-disc', dest='tricky_iso', type=Path,
                   help='own Tricky disc image: validated now; the Tricky port arrives in a later version')
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
    p.add_argument('--dry-run', action='store_true', help='check and list install disc payload without touching a device')
    p.add_argument('--jnilibs', type=Path, help='own source-built Android JNI libraries')
    p.add_argument('--prebuilt', action='store_true', help='opt in to verified prebuilt Android support libraries (default: build them from source)')
    p.add_argument('--prebuilt-cache', type=Path, help='verified offline support cache (default: WORK/prebuilt-cache)')
    p.add_argument('--no-turnip', action='store_true', help='omit the optional Turnip driver')
    p.add_argument('--stock-dump', type=Path, help='own texture dump holding the four stock font atlases (play builds the font pack from it)')
    p.add_argument('--skip-fonts', action='store_true', help='skip the default raster-edge font pack build in play')
    p.add_argument('--ndk', type=Path, help='local Android NDK 28.2.13676358')
    p.add_argument('--suite', action='store_true', help='also build optional Mac development suite and its analyzer/recompiler fixtures')
    p.add_argument('--det', action='store_true', help='mac flavor with the VBlank det-hash tap (gate boots + tap unit tests; separate runner-build-det dir)')
    p.add_argument('--jobs', type=int, default=8)
    p.add_argument('--app-id', default=PUBLIC_APP_ID, help='Android package id for android, stage and install')
    p.add_argument('--engine', type=Path, default=None,
                   help='engine checkout (default: SSX3_ENGINE_DIR or deps/PS2Recomp submodule)')
    a = p.parse_args()
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
    if a.step != 'prepare' and not (a.work / 'inputs.json').is_file():
        p.error('run prepare in this work directory first')
    a.work.mkdir(parents=True, exist_ok=True)
    {'prepare': prepare, 'mac': mac, 'android': android, 'mac-libs': libs, 'run': play,
     'android-libs': android_libs, 'stage': stage, 'install': install,
     'ios-libs': ios_libs, 'ios': ios}[a.step](a)


if __name__ == '__main__':
    main()
