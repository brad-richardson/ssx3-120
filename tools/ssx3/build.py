#!/usr/bin/env python3
"""Local SSX 3 preparation. Never downloads or publishes game data."""
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
import time
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[2]
GENERATOR = '03c10b5aeccd4fb673fecaea3cf4a5f5064afe14'
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
    # clang++-18 must pair with clang-18, not a non-existent sibling clang.
    cc = os.environ.get('CC') or str(Path(cxx).with_name(
        Path(cxx).name.replace('clang++', 'clang').replace('g++', 'gcc')))
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
    else:
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
                   '; review and accept the SDK licenses with sdkmanager --licenses')
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
        # Prefer the exact pinned commit already present in this checkout.
        # This also validates an unpublished lane commit without a fork push.
        local_pin = subprocess.run(
            ['git', '-C', str(ROOT), 'cat-file', '-e', GENERATOR + '^{commit}'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False).returncode == 0
        run('git', 'init', gen)
        run('git', '-C', gen, 'fetch', '--depth=1', str(ROOT) if local_pin else FORK, GENERATOR)
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
    configure(ROOT, a.work / 'runner-build', '-DPS2X_GAME_CODEGEN_DIR=' + str(a.work / 'codegen'),
        '-DPS2X_VU0_RECOMP_DIR=', '-DPS2X_BUILD_RECOMP=' + extra, '-DPS2X_BUILD_ANALYZER=' + extra,
        '-DPS2X_BUILD_TEST=' + extra, '-DPS2X_ENABLE_DEBUG_UI=OFF', '-DPS2X_ENABLE_RUNTIME_LOGS=OFF',
        '-DPS2X_ENABLE_AGRESSIVE_LOGS=OFF', '-DPS2X_ENABLE_DIAG_TAPS=OFF',
        '-DPS2X_ENABLE_DIAG_WATCH=OFF', '-DPS2X_ENABLE_TS2_DIAG=OFF')
    run('cmake', '--build', a.work / 'runner-build', '--parallel', a.jobs,
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
        a.prebuilt = 'off'
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
    run('bash', ROOT / 'android/gradlew', '-p', ROOT / 'android', 'assembleRelease',
        '-Pps2xGameCodegenDir=' + str(a.work / 'codegen'),
        '-Pps2xJniLibsDir=' + str(jnilibs), '-Pps2xPublicNoticesDir=' + str(notices_dir),
        '-Pps2xVu0RecompDir=',
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
    actual = subprocess.check_output(['git', '-C', str(destination), 'rev-parse', 'HEAD'], text=True).strip()
    expected = subprocess.check_output(['git', '-C', str(destination), 'rev-parse', revision + '^{commit}'], text=True).strip()
    if actual != expected:
        raise ValueError('source pin mismatch: ' + str(destination))


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
    for src, dest in [(ROOT / 'ps2xRuntime/third_party/armsx2/ge1', assembly / 'adapter'),
                      (ROOT / 'ps2xRuntime/third_party/armsx2/mv2', assembly / 'mv2-adapter'),
                      (ROOT / 'tools/ssx3/mac-libs', assembly / 'mac-platform')]:
        shutil.copytree(src, dest, dirs_exist_ok=True)
    link = assembly / 'mac-platform/3rdparty'
    if not link.exists():
        link.symlink_to(vendor / '3rdparty', target_is_directory=True)
    build = a.work / 'libs-build'
    run('cmake', '-S', assembly / 'mac-platform', '-B', build, '-G', 'Ninja',
        '-DCMAKE_BUILD_TYPE=Release', '-DCMAKE_C_COMPILER=/usr/bin/cc',
        '-DCMAKE_CXX_COMPILER=/usr/bin/c++', '-DCMAKE_PREFIX_PATH=' + prefixes,
        '-DPS2RECOMP_SOURCE=' + str(ROOT), '-DLTO_PCSX2_CORE=OFF')
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
    if a.prebuilt == 'off':
        return None
    if getattr(a, 'app_id', PUBLIC_APP_ID) != PUBLIC_APP_ID:
        print('Custom package ID requires source libraries.')
        return None
    import prebuilt
    return prebuilt.fetch(a.prebuilt_cache or a.work / 'prebuilt-cache', a.prebuilt)


def android_libs(a):
    a.ndk = ndk_root(a.ndk)
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
    recipe_path = a.work / 'android-dependencies.sh'
    recipe_path.write_text(recipe.replace(marker, 'NPROCS=' + str(a.jobs)))
    subprocess.run(['bash', str(recipe_path), str(prefix)], env=env, cwd=a.work, check=True)
    assembly = vendor.parent
    for src, dest in [(ROOT / 'ps2xRuntime/third_party/armsx2/ge1', assembly / 'adapter'),
                      (ROOT / 'ps2xRuntime/third_party/armsx2/mv2', assembly / 'mv2-adapter'),
                      (ROOT / 'tools/ssx3/android-libs', assembly / 'android-platform')]:
        shutil.copytree(src, dest, dirs_exist_ok=True)
    shutil.copy2(ROOT / 'ps2xRuntime/third_party/armsx2/platform/android/ax4b_android_stubs.cpp',
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
    presets = ROOT / 'tools/ssx3/presets'
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
    apk = ROOT / 'android/app/build/outputs/apk/release/app-release.apk'
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


def install(a):
    if a.app_id == PLAY_APP_ID:
        raise ValueError('public install refuses the maintainer play package')
    inputs = json.loads((a.work / 'inputs.json').read_text())
    disc_sources = verified_disc_payload(a.work, inputs)
    if a.dry_run:
        for source_path, relative in disc_sources:
            print(f'Would copy {source_path} ({source_path.stat().st_size} bytes) to {relative}')
        return
    files = stage(a)
    apk = ROOT / 'android/app/build/outputs/apk/release/app-release.apk'
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


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('step', choices=['doctor', 'prepare', 'mac', 'mac-libs', 'run', 'android', 'android-libs', 'stage', 'install'])
    p.add_argument('--work', type=Path, help='external private output directory')
    p.add_argument('--target', choices=['prepare', 'android', 'mac'], default='android', help='doctor prerequisite set')
    p.add_argument('--tools-only', action='store_true', help='prepare the native generator without reading a disc')
    p.add_argument('--iso', type=Path, help='own supported USA disc image (prepare only)')
    p.add_argument('--symbol-names', type=Path, help='optional local names sidecar for development codegen')
    p.add_argument('--serial', help='adb device serial for install')
    p.add_argument('--dry-run', action='store_true', help='check and list install disc payload without touching a device')
    p.add_argument('--jnilibs', type=Path, help='own source-built Android JNI libraries')
    p.add_argument('--prebuilt', choices=['auto', 'off'], default='auto', help='verified Android support libraries, or build all libraries from source')
    p.add_argument('--prebuilt-cache', type=Path, help='verified offline support cache (default: WORK/prebuilt-cache)')
    p.add_argument('--no-turnip', action='store_true', help='omit the optional Turnip driver')
    p.add_argument('--stock-dump', type=Path, help='own texture dump holding the four stock font atlases (play builds the font pack from it)')
    p.add_argument('--skip-fonts', action='store_true', help='skip the default raster-edge font pack build in play')
    p.add_argument('--ndk', type=Path, help='local Android NDK 28.2.13676358')
    p.add_argument('--suite', action='store_true', help='also build optional Mac development suite and its analyzer/recompiler fixtures')
    p.add_argument('--jobs', type=int, default=8)
    p.add_argument('--app-id', default=PUBLIC_APP_ID, help='Android package id for android, stage and install')
    a = p.parse_args()
    if a.step == 'doctor':
        doctor(a)
        return
    if not supported_host():
        p.error('supported hosts: macOS Apple silicon, Linux x86_64/arm64; Windows users run Ubuntu in WSL2')
    if a.step in ('mac', 'mac-libs', 'run') and platform.system() != 'Darwin':
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
    if a.step == 'prepare' and not a.iso and not a.tools_only:
        p.error('prepare requires --iso')
    if a.step != 'prepare' and not (a.work / 'inputs.json').is_file():
        p.error('run prepare in this work directory first')
    a.work.mkdir(parents=True, exist_ok=True)
    {'prepare': prepare, 'mac': mac, 'android': android, 'mac-libs': libs, 'run': play,
     'android-libs': android_libs, 'stage': stage, 'install': install}[a.step](a)


if __name__ == '__main__':
    main()
