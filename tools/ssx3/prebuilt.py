#!/usr/bin/env python3
"""Build and verify game-free Android support bundles. No disc input is accepted."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[2]
NDK = '28.2.13676358'
VENDOR = 'afad5a04c2668a10e12adadaa51d54c46f832236'
RELEASES = 'https://github.com/brad-richardson/PS2Recomp/releases/download/'
FORMAT = 1
ARCHIVES = ('libps2EntryRunner.a', 'libps2_runtime.a', 'libps2_gs_shadow.a',
            'libps2_iop.a', 'libps2x_soundtouch.a', 'libps2x_rcheevos.a', 'libraylib.a')
SHARED = ('libhardware.so', 'libge1_gs.so', 'libmv2_microvu.so')
RECIPE = {'format': FORMAT, 'ndk': NDK, 'api': 29, 'abi': 'arm64-v8a',
          'march': 'armv8.2-a+fp16+dotprod', 'config': 'RelWithDebInfo',
          'vendor': VENDOR, 'app_id': 'com.ssx3.recomp', 'codegen_abi': 2}


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def contract():
    """Content identity avoids commit/tag self-reference and rejects stale headers."""
    paths = [ROOT / 'CMakeLists.txt', Path(__file__), ROOT / 'tools/ssx3/prebuilt-consumer.cmake',
             ROOT / 'tools/ssx3/build.py', ROOT / 'android/app/build.gradle']
    for name in ('ps2xRuntime', 'ps2xIOP', 'android/hwcompat', 'tools/ssx3/android-libs'):
        paths.extend(p for p in (ROOT / name).rglob('*') if p.is_file()
                     and not p.is_symlink() and not p.name.startswith('._'))
    h = hashlib.sha256(json.dumps(RECIPE, sort_keys=True).encode())
    for p in sorted(set(paths)):
        h.update(p.relative_to(ROOT).as_posix().encode() + b'\0')
        h.update(digest(p).encode() + b'\n')
    return h.hexdigest()


def safe_name(name):
    p = Path(name)
    return bool(name) and not p.is_absolute() and '\\' not in name and '..' not in p.parts


def allowed(name):
    if not safe_name(name):
        return False
    if name in ('manifest.json', 'runtime-definitions.txt', 'include/sse2neon.h'):
        return True
    if name in ['lib/' + x for x in ARCHIVES] + ['jniLibs/arm64-v8a/' + x for x in SHARED]:
        return True
    if name.startswith('notices/'):
        return Path(name).suffix.lower() not in ('.iso', '.elf', '.so', '.a', '.cpp', '.bin')
    # Renderer fonts and shader text only; no arcade cards, game database or UI art.
    if name.startswith('resources/fonts/'):
        return Path(name).suffix.lower() in ('.ttf', '.otf', '.txt') or 'license' in name.lower() or 'copyright' in name.lower()
    return name.startswith('resources/shaders/') and Path(name).suffix.lower() in ('.glsl', '.h', '.fx', '.vert', '.frag', '.geom', '.comp')


def verify(bundle, expected=None):
    bundle = Path(bundle)
    m = json.loads((bundle / 'manifest.json').read_text())
    if m.get('recipe') != RECIPE or m.get('contract') != (expected or contract()):
        raise ValueError('prebuilt source/toolchain contract mismatch')
    if not re.fullmatch('[0-9a-f]{40}', m.get('source_sha', '')):
        raise ValueError('missing matching source revision')
    files = m.get('files', {})
    required = {'lib/' + x for x in ARCHIVES} | {'jniLibs/arm64-v8a/' + x for x in SHARED}
    required |= {'include/sse2neon.h', 'runtime-definitions.txt', 'notices/PS2Recomp-LICENSE', 'notices/SOURCES.json'}
    if not required.issubset(files):
        raise ValueError('incomplete prebuilt bundle')
    actual = {p.relative_to(bundle).as_posix() for p in bundle.rglob('*') if p.is_file()}
    if actual != set(files) | {'manifest.json'}:
        raise ValueError('prebuilt manifest does not cover exactly the payload')
    if (bundle / 'manifest.json').is_symlink():
        raise ValueError('symlink manifest is not permitted')
    for name, sha in files.items():
        if not allowed(name) or not re.fullmatch('[0-9a-f]{64}', sha):
            raise ValueError('unapproved prebuilt file: ' + name)
        p = bundle / name
        if p.is_symlink() or any((bundle / parent).is_symlink() for parent in Path(name).parents):
            raise ValueError('symlinks are not permitted in bundles')
        if digest(p) != sha:
            raise ValueError('prebuilt SHA-256 mismatch: ' + name)
        if name.startswith('notices/'):
            if p.stat().st_size > 4 * 1024 * 1024 or b'\0' in p.read_bytes():
                raise ValueError('non-text or oversized notice: ' + name)
    return m


def extract(archive, destination):
    with zipfile.ZipFile(archive) as z:
        names = [i.filename for i in z.infolist()]
        if len(names) != len(set(names)) or sum(i.file_size for i in z.infolist()) > 3 * 1024**3:
            raise ValueError('duplicate names or oversized prebuilt archive')
        for item in z.infolist():
            if not allowed(item.filename) or (item.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('unsafe/unapproved archive member: ' + item.filename)
            target = destination / item.filename
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(item) as src, target.open('wb') as dst:
                shutil.copyfileobj(src, dst, 1024 * 1024)


def fetch(cache, mode='auto'):
    if mode == 'off':
        return None
    cache = Path(cache)
    stamp = contract()
    bundle = cache / stamp
    try:
        if bundle.is_dir():
            verify(bundle, stamp)
            print('Verified offline prebuilt cache: ' + str(bundle))
            return bundle
        cache.mkdir(parents=True, exist_ok=True)
        tag = 'android-prebuilt-' + stamp
        asset = 'android-arm64-' + stamp + '.zip'
        base = RELEASES + tag + '/'
        with urllib.request.urlopen(base + 'SHA256SUMS', timeout=30) as response:
            sums = response.read(64 * 1024).decode('ascii')
        matches = [line.split() for line in sums.splitlines() if len(line.split()) == 2 and line.split()[1] == asset]
        if len(matches) != 1 or not re.fullmatch('[0-9a-f]{64}', matches[0][0]):
            raise ValueError('release checksum entry missing or ambiguous')
        with tempfile.TemporaryDirectory(prefix='download-', dir=cache) as scratch:
            tmp = Path(scratch)
            archive = tmp / asset
            with urllib.request.urlopen(base + asset, timeout=60) as response, archive.open('wb') as dst:
                shutil.copyfileobj(response, dst, 1024 * 1024)
            if digest(archive) != matches[0][0]:
                raise ValueError('release asset SHA-256 mismatch')
            unpacked = tmp / 'bundle'
            unpacked.mkdir()
            extract(archive, unpacked)
            verify(unpacked, stamp)
            unpacked.rename(bundle)
        print('Verified prebuilt release: ' + tag)
        return bundle
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as e:
        print('Prebuilt unavailable (' + str(e) + '); building from source.', file=sys.stderr)
        return None


def run(*args):
    print('+', ' '.join(map(str, args)), flush=True)
    subprocess.run(list(map(str, args)), check=True)


def audit(bundle, nm, build_dir):
    """Fail closed on guest definitions/files; legitimate undefined table ABI is allowed."""
    verify(bundle)
    commands = json.loads((build_dir / 'compile_commands.json').read_text())
    for command in commands:
        path = Path(command['file'])
        if path.name == 'register_functions.cpp' or re.match(r'(sub_[0-9a-fA-F]+|vu0e?_[0-9a-fA-F]+)\.cpp$', path.name):
            raise ValueError('generated/stub registration source in export graph: ' + str(path))
    forbidden = re.compile(r'\b(?:sub_[0-9a-fA-F]{6,}|vu0e?_[0-9a-fA-F]{6,}|g_ps2RecompiledFunctionTable\w*|g_ps2xCodegenAbiVersion|_request_end_0x[0-9a-fA-F]+)\b')
    for p in sorted(bundle.rglob('*')):
        if p.suffix not in ('.a', '.so'):
            continue
        # Stream nm output: the unstripped renderer libraries can have many symbols.
        with tempfile.TemporaryFile(mode='w+t') as output:
            subprocess.run([str(nm), '--defined-only', '--demangle', str(p)], stdout=output, check=True)
            output.seek(0)
            for line in output:
                if forbidden.search(line):
                    raise ValueError('generated-code symbol in ' + p.name + ': ' + line.strip())
    print('PASS: no generated-code files or defined symbols in support artifacts')


def notices(root, destination, prefix):
    for p in sorted(root.rglob('*')):
        if not p.is_file() or p.is_symlink() or '.git' in p.parts:
            continue
        low = p.name.lower()
        if low.startswith(('license', 'licence', 'copying', 'notice', 'copyright')):
            target = destination / prefix / p.relative_to(root)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, target)


def build(a):
    if os.environ.get('PS2X_GAME_CODEGEN_DIR') or os.environ.get('PS2X_VU0_RECOMP_DIR'):
        raise ValueError('CI/export environment must not contain generated-code inputs')
    if (a.work / 'inputs.json').exists() or (a.work / 'codegen').exists():
        raise ValueError('support builds require a separate game-free work directory')
    properties = (a.ndk / 'source.properties').read_text()
    if not re.search(r'Pkg.Revision\s*=\s*' + re.escape(NDK) + r'\s*$', properties, re.M):
        raise ValueError('prebuilt requires exact NDK ' + NDK)
    a.work.mkdir(parents=True, exist_ok=True)
    stamp = contract()
    spec = importlib.util.spec_from_file_location('ssx3_source_builder', ROOT / 'tools/ssx3/build.py')
    recipe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(recipe)
    libraries = a.source_libraries.resolve() if a.source_libraries else a.work
    if a.source_libraries:
        # Local acceptance can reuse the freshly measured source libraries;
        # release CI never supplies this option. No disc or codegen is copied.
        vendor = libraries / 'android-assembly/vendor-pcsx2'
        actual = subprocess.check_output(['git', '-C', str(vendor), 'rev-parse', 'HEAD'], text=True).strip()
        if actual != VENDOR:
            raise ValueError('reused library vendor pin mismatch')
        for directory, staged in [('ge1', 'adapter'), ('mv2', 'mv2-adapter')]:
            for source in (ROOT / 'ps2xRuntime/third_party/armsx2' / directory).rglob('*'):
                if source.is_file():
                    copied = libraries / 'android-assembly' / staged / source.relative_to(ROOT / 'ps2xRuntime/third_party/armsx2' / directory)
                    if not copied.is_file() or digest(source) != digest(copied):
                        raise ValueError('reused adapter source mismatch: ' + str(source))
        if digest(ROOT / 'ps2xRuntime/third_party/armsx2/platform/android/ax4b_android_stubs.cpp') != digest(libraries / 'android-assembly/adapter/ax4b_android_stubs.cpp'):
            raise ValueError('reused Android stub source mismatch')
        for name in ('CMakeLists.txt',):
            if digest(ROOT / 'tools/ssx3/android-libs' / name) != digest(libraries / 'android-assembly/android-platform' / name):
                raise ValueError('reused library recipe mismatch')
    else:
        recipe.android_libs(a)
    runtime = a.work / 'runtime-build'
    flags = ['-DCMAKE_BUILD_TYPE=RelWithDebInfo', '-DCMAKE_POSITION_INDEPENDENT_CODE=ON',
             '-DCMAKE_TOOLCHAIN_FILE=' + str(a.ndk / 'build/cmake/android.toolchain.cmake'),
             '-DANDROID_ABI=arm64-v8a', '-DANDROID_PLATFORM=android-29', '-DANDROID_STL=c++_static',
             '-DPS2X_PREBUILT_EXPORT=ON', '-DPS2X_PREBUILT_STAMP=' + stamp,
             '-DPS2X_GAME_CODEGEN_DIR=', '-DPS2X_VU0_RECOMP_DIR=', '-DPS2X_PGO_DATA=',
             '-DPS2X_DEFAULT_BOOT_ELF=/storage/emulated/0/Android/data/com.ssx3.recomp/files/SLUS_207.72',
             '-DPS2X_ANDROID_MARCH=armv8.2-a+fp16+dotprod', '-DPS2X_GAME_IPO=ON',
             '-DCMAKE_C_FLAGS_RELWITHDEBINFO=-O3 -g -DNDEBUG', '-DCMAKE_CXX_FLAGS_RELWITHDEBINFO=-O3 -g -DNDEBUG']
    flags += ['-D' + option + '=OFF' for option in ('PS2X_BUILD_RECOMP', 'PS2X_BUILD_ANALYZER', 'PS2X_BUILD_TEST',
              'PS2X_BUILD_STUDIO', 'PS2X_ENABLE_SCCACHE', 'PS2X_ENABLE_DIAG_TAPS', 'PS2X_ENABLE_DIAG_WATCH',
              'PS2X_ENABLE_TS2_DIAG', 'PS2X_ENABLE_RUNTIME_LOGS', 'PS2X_ENABLE_AGRESSIVE_LOGS',
              'PS2X_ENABLE_IOP_RPC_TRACE', 'PS2X_ENABLE_DEBUG_UI')]
    run(a.cmake, '-S', ROOT, '-B', runtime, '-G', 'Ninja', *flags)
    run(a.cmake, '--build', runtime, '--parallel', a.jobs, '--target', 'ps2EntryRunner', 'hardware')
    bundle = a.work / 'bundle'
    if bundle.exists():
        raise ValueError('refusing existing output bundle; choose a fresh export directory')
    (bundle / 'lib').mkdir(parents=True)
    for name in (runtime / 'prebuilt-archives.txt').read_text().splitlines():
        src = Path(name)
        if src.name not in ARCHIVES:
            raise ValueError('unexpected support archive: ' + str(src))
        shutil.copy2(src, bundle / 'lib' / src.name)
    shutil.copytree(libraries / 'jniLibs', bundle / 'jniLibs')
    shutil.copy2(runtime / 'android/hwcompat/libhardware.so', bundle / 'jniLibs/arm64-v8a/libhardware.so')
    (bundle / 'include').mkdir()
    shutil.copy2(runtime / '_deps/sse2neon-src/sse2neon.h', bundle / 'include/sse2neon.h')
    shutil.copy2(runtime / 'prebuilt-definitions.txt', bundle / 'runtime-definitions.txt')
    for p in (libraries / 'android-resources').rglob('*'):
        name = 'resources/' + p.relative_to(libraries / 'android-resources').as_posix()
        if p.is_file() and allowed(name):
            target = bundle / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, target)
    notice_dir = bundle / 'notices'
    notice_dir.mkdir()
    shutil.copy2(ROOT / 'LICENSE', notice_dir / 'PS2Recomp-LICENSE')
    notices(ROOT / 'ps2xRuntime/third_party', notice_dir, 'runtime')
    notices(libraries / 'android-assembly/vendor-pcsx2', notice_dir, 'vendor')
    notices(libraries / 'deps-build', notice_dir, 'vendor-dependencies')
    notices(runtime / '_deps', notice_dir, 'runtime-dependencies')
    source_sha = subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip()
    sources = {'PS2Recomp': 'https://github.com/brad-richardson/PS2Recomp/tree/' + source_sha,
               'armsx2': 'https://github.com/brad-richardson/armsx2/tree/' + VENDOR,
               'recipe_contract': stamp, 'ndk': NDK}
    (notice_dir / 'SOURCES.json').write_text(json.dumps(sources, indent=2) + '\n')
    files = {p.relative_to(bundle).as_posix(): digest(p) for p in sorted(bundle.rglob('*')) if p.is_file()}
    (bundle / 'manifest.json').write_text(json.dumps({'recipe': RECIPE, 'contract': stamp,
        'source_sha': source_sha, 'files': files}, indent=2, sort_keys=True) + '\n')
    tools = next(p for p in (a.ndk / 'toolchains/llvm/prebuilt').iterdir() if (p / 'bin/llvm-nm').is_file()) / 'bin'
    audit(bundle, tools / 'llvm-nm', runtime)
    output = a.work / 'release'
    output.mkdir(exist_ok=True)
    archive = output / ('android-arm64-' + stamp + '.zip')
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for p in sorted(bundle.rglob('*')):
            if p.is_file():
                z.write(p, p.relative_to(bundle).as_posix())
    (output / 'SHA256SUMS').write_text(digest(archive) + '  ' + archive.name + '\n')
    (output / 'SOURCE.json').write_text(json.dumps(sources, indent=2) + '\n')
    print('Release tag: android-prebuilt-' + stamp)
    print('Release assets: ' + str(output))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    sub.add_parser('contract')
    v = sub.add_parser('verify')
    v.add_argument('bundle', type=Path)
    b = sub.add_parser('build')
    b.add_argument('--work', type=Path, required=True)
    b.add_argument('--ndk', type=Path, required=True)
    b.add_argument('--jobs', type=int, default=4)
    b.add_argument('--cmake', default='cmake')
    b.add_argument('--source-libraries', type=Path, help='local validation only: reuse matching, audited source libraries; never used by release CI')
    # android_libs delegates only to the source recipe, never download itself.
    b.set_defaults(prebuilt='off', prebuilt_cache=None)
    a = p.parse_args()
    if a.command == 'contract':
        print(contract())
    elif a.command == 'verify':
        verify(a.bundle)
        print('Verified source contract and all prebuilt file hashes')
    else:
        a.work, a.ndk = a.work.resolve(), a.ndk.resolve()
        if a.work == ROOT or ROOT in a.work.parents or not 1 <= a.jobs <= 16:
            p.error('use an external private work directory and 1..16 jobs')
        build(a)


if __name__ == '__main__':
    main()
