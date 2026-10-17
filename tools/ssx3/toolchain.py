"""Project-local JDK + Android SDK/NDK bootstrap (builder-ux-1).

Only downloads when the caller allows it (`ensure_*` with
``auto_download=True``); `discover` never touches the network or disk.
Everything managed lands under ``WORK/toolchain/``: no system install, no
shell-profile edits, no sudo. Standard library only.
"""

import json
import os
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

NDK_VERSION = '28.2.13676358'
SDK_PACKAGES = ['platforms;android-34', 'build-tools;34.0.0',
                'ndk;' + NDK_VERSION, 'cmake;3.22.1', 'platform-tools']

JDK_RELEASE = '17.0.20.1+1'
JDK_URLS = {
    # Official Adoptium release assets (immutable); SHA-256 from the
    # Adoptium release API (authoritative, 2026-10-17).
    'linux-x64': ('https://github.com/adoptium/temurin17-binaries/releases/download/'
                  'jdk-17.0.20.1%2B1/OpenJDK17U-jdk_x64_linux_hotspot_17.0.20.1_1.tar.gz',
                  '3808d1d15e3ec6bd5b84057fb5d84c33d8a1536a258146bcea2e603fc726e08e'),
    'linux-aarch64': ('https://github.com/adoptium/temurin17-binaries/releases/download/'
                      'jdk-17.0.20.1%2B1/OpenJDK17U-jdk_aarch64_linux_hotspot_17.0.20.1_1.tar.gz',
                      '457b57af8f9c93ec39080bb8c764f559dc8c89a6da1a39d718a400b7890d3e41'),
    'mac-aarch64': ('https://github.com/adoptium/temurin17-binaries/releases/download/'
                    'jdk-17.0.20.1%2B1/OpenJDK17U-jdk_aarch64_mac_hotspot_17.0.20.1_1.tar.gz',
                    '196d13ba5f10414bef7f6a05a9b3f00edacb18ebacef2b99485db9e2ee18f0e8'),
}

CMDLINE_BUILD = '15859902'
CMDLINE_URLS = {
    # Google publishes no checksums for these zips, so the SHA-256 below
    # is the bytes we fetched on 2026-10-17 from the "Command line tools
    # only" table (build 15859902, still the current latest). The SDK
    # packages installed with sdkmanager are checksum-verified by Google's
    # own repository metadata during install.
    'linux': ('https://dl.google.com/android/repository/'
              'commandlinetools-linux-15859902_latest.zip',
              '4e4c464f145a7512b57d088ac6c278c03c9eea610886b35a5e0804e74eedf583',
              181833628),
    'mac': ('https://dl.google.com/android/repository/'
            'commandlinetools-mac_arm64-15859902_latest.zip',
            '835b62a26162b229b441d1f6d4680383815a270809eb33522c0d480fa5002c4e',
            156083281),
}

ANDROID_TERMS = 'https://developer.android.com/studio/terms'

TOOLCHAIN_DIR = 'toolchain'

SEARCH_ORDER = """JDK 17 search order: --jdk, JAVA_HOME, managed WORK/toolchain/jdk-*,
common installs (Android Studio jbr, Temurin/HOMEbrew/OpenJDK system paths).
Android SDK search order: ANDROID_HOME (or ANDROID_SDK_ROOT), managed
WORK/toolchain/android-sdk, common installs (~/Android/Sdk,
~/Library/Android/sdk, /opt/android-sdk, /usr/local/android-sdk).
NDK search order: --ndk, ANDROID_NDK_HOME (or ANDROID_NDK_ROOT),
<sdk>/ndk/28.2.13676358, Android Studio's ndk/<ver> sibling dirs."""


def _host_jdk_key():
    import platform
    system, machine = platform.system(), platform.machine().lower()
    if system == 'Linux' and machine == 'x86_64':
        return 'linux-x64'
    if system == 'Linux' and machine in ('aarch64', 'arm64'):
        return 'linux-aarch64'
    if system == 'Darwin' and machine == 'arm64':
        return 'mac-aarch64'
    raise ValueError('toolchain bootstrap supports Linux x86_64/arm64 and '
                     'macOS arm64; got ' + system + ' ' + machine)


def _host_cmdline_key():
    import platform
    system = platform.system()
    if system == 'Linux':
        return 'linux'
    if system == 'Darwin':
        return 'mac'
    raise ValueError('toolchain bootstrap supports Linux and macOS; got ' + system)


def _run_java_version(java):
    try:
        result = subprocess.run([str(java), '-version'], text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                timeout=30, check=False)
        return result.returncode == 0, result.stdout
    except Exception as error:
        # A java that cannot even report its version is not a usable JDK.
        return False, str(error)


def jdk_ok(java_home):
    """A directory counts as JDK 17 when its bin/java reports version 17."""
    java_home = Path(java_home)
    java = java_home / 'bin/java'
    if not java.is_file():
        return False
    ok, detail = _run_java_version(java)
    import re
    match = re.search(r'version "(\d+)', detail)
    return ok and bool(match) and match[1] == '17'


def sdk_ok(sdk):
    """An SDK directory counts when the pinned platform + aapt2 are present."""
    sdk = Path(sdk)
    return ((sdk / 'platforms/android-34/android.jar').is_file()
            and (sdk / 'build-tools/34.0.0/aapt2').is_file())


def ndk_ok(ndk):
    """An NDK directory counts when source.properties pins the version."""
    import re
    props = Path(ndk) / 'source.properties'
    if not props.is_file():
        return False
    return bool(re.search(r'Pkg\.Revision\s*=\s*' + re.escape(NDK_VERSION) + r'\s*$',
                          props.read_text(), re.M))


def _common_jdk_dirs():
    import platform
    home = Path.home()
    candidates = []
    if platform.system() == 'Darwin':
        candidates += [Path('/Applications/Android Studio.app/Contents/jbr/Contents/Home'),
                       Path('/Library/Java/JavaVirtualMachines/temurin-17.jdk/Contents/Home')]
        brew = shutil.which('brew')
        if brew:
            try:
                prefix = subprocess.check_output(
                    ['brew', '--prefix', 'openjdk@17'], text=True, timeout=30).strip()
                candidates.append(Path(prefix, 'libexec/openjdk.jdk/Contents/Home'))
            except Exception:
                # Best-effort discovery: a missing formula, a broken brew,
                # or a hostile test mock just means "no JDK here".
                pass
    else:
        candidates += [Path('/usr/lib/jvm/java-17-openjdk-amd64'),
                       Path('/usr/lib/jvm/java-17-openjdk-arm64'),
                       Path('/usr/lib/jvm/java-17-openjdk'),
                       Path('/usr/lib/jvm/temurin-17-jdk-amd64'),
                       Path('/usr/lib/jvm/temurin-17-jdk-arm64'),
                       home / '.sdkman/candidates/java/17-tem']
    return candidates


def _common_sdk_dirs():
    home = Path.home()
    return [home / 'Android/Sdk', home / 'Library/Android/sdk',
            Path('/opt/android-sdk'), Path('/usr/local/android-sdk'),
            Path('/Applications/Android Studio.app/Contents/android-sdk')]


def find_jdk(explicit=None):
    """First usable JDK 17: --jdk, JAVA_HOME, common installs. None if absent."""
    if explicit and jdk_ok(explicit):
        return Path(explicit)
    java_home = os.environ.get('JAVA_HOME')
    if java_home and jdk_ok(java_home):
        return Path(java_home)
    for candidate in _common_jdk_dirs():
        if jdk_ok(candidate):
            return candidate
    return None


def find_sdk():
    """First usable Android SDK: ANDROID_HOME/ROOT, common installs. None if absent."""
    for var in ('ANDROID_HOME', 'ANDROID_SDK_ROOT'):
        value = os.environ.get(var)
        if value and sdk_ok(value):
            return Path(value)
    for candidate in _common_sdk_dirs():
        if sdk_ok(candidate):
            return candidate
    return None


def find_ndk(sdk=None, explicit=None):
    """First pinned NDK: --ndk, ANDROID_NDK_HOME/ROOT, <sdk>/ndk/<ver>."""
    if explicit and ndk_ok(explicit):
        return Path(explicit)
    for var in ('ANDROID_NDK_HOME', 'ANDROID_NDK_ROOT'):
        value = os.environ.get(var)
        if value and ndk_ok(value):
            return Path(value)
    if sdk:
        pinned = Path(sdk) / 'ndk' / NDK_VERSION
        if ndk_ok(pinned):
            return pinned
        # Android Studio nests other revisions beside the pin; never accept
        # a wrong revision silently, so only the exact pin counts.
    return None


def managed_jdk(work):
    found = sorted(Path(work, TOOLCHAIN_DIR).glob('jdk-*'))
    for candidate in found:
        if jdk_ok(candidate):
            return candidate
    return None


def managed_sdk(work):
    candidate = Path(work, TOOLCHAIN_DIR, 'android-sdk')
    return candidate if sdk_ok(candidate) else None


def tool_dir(work):
    d = Path(work, TOOLCHAIN_DIR)
    d.mkdir(parents=True, exist_ok=True)
    return d


def downloads_dir(work):
    d = tool_dir(work) / 'downloads'
    d.mkdir(parents=True, exist_ok=True)
    return d


def sha256_file(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def fetch(url, want_sha, dest, want_bytes=None):
    """Download a pinned archive; any size or SHA-256 mismatch fails closed."""
    dest = Path(dest)
    if dest.is_file() and sha256_file(dest) == want_sha:
        return dest
    tmp = dest.with_name(dest.name + '.download')
    tmp.unlink(missing_ok=True)
    try:
        print('+ fetch ' + url)
        with urllib.request.urlopen(url, timeout=120) as src, tmp.open('wb') as out:
            shutil.copyfileobj(src, out)
        if want_bytes and tmp.stat().st_size != want_bytes:
            raise ValueError('size mismatch for ' + url)
        if sha256_file(tmp) != want_sha:
            raise ValueError('SHA-256 mismatch for ' + url)
        tmp.replace(dest)
        return dest
    finally:
        tmp.unlink(missing_ok=True)


def _safe_members(names):
    for name in names:
        parts = Path(name).parts
        if name.startswith('/') or '..' in parts:
            raise ValueError('unsafe archive path: ' + name)


def unpack_jdk(archive, dest_root):
    """Extract a Temurin tarball; returns the dir holding bin/java."""
    dest_root = Path(dest_root)
    with tarfile.open(archive, 'r:gz') as t:
        _safe_members(t.getnames())
        t.extractall(dest_root)
    hits = [p.parent.parent for p in dest_root.glob('*/bin/java')]
    hits = [p for p in hits if (p / 'bin/java').is_file()]
    if not hits:
        hits = [p.parent.parent for p in dest_root.glob('*/*/bin/java')]
        hits = [p for p in hits if (p / 'bin/java').is_file()]
    if len(hits) != 1:
        raise ValueError('JDK layout changed in ' + str(archive))
    if not jdk_ok(hits[0]):
        raise ValueError('extracted JDK is not version 17: ' + str(hits[0]))
    return hits[0]


def unpack_cmdline(archive, sdk):
    """Stage Google's command-line tools at <sdk>/cmdline-tools/latest."""
    sdk = Path(sdk)
    target = sdk / 'cmdline-tools' / 'latest'
    if (target / 'bin/sdkmanager').is_file() and os.access(target / 'bin/sdkmanager', os.X_OK):
        return target
    staging = sdk / 'cmdline-tools' / '_new'
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    with zipfile.ZipFile(archive) as z:
        _safe_members(z.namelist())
        # Every Google zip nests one top-level 'cmdline-tools' dir; strip it.
        tops = {name.split('/', 1)[0] for name in z.namelist() if '/' in name}
        if tops != {'cmdline-tools'}:
            raise ValueError('command-line tools layout changed in ' + str(archive))
        for member in z.namelist():
            rest = member.split('/', 1)
            if len(rest) != 2 or not rest[1]:
                continue
            out = staging / rest[1]
            if member.endswith('/'):
                out.mkdir(parents=True, exist_ok=True)
            else:
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(z.read(member))
                # Zip extraction drops unix modes: restore the archived
                # permissions (sdkmanager ships 755 and must stay executable).
                perms = z.getinfo(member).external_attr >> 16
                if perms:
                    out.chmod(perms)
    if target.is_dir():
        shutil.rmtree(target)
    staging.replace(target)
    if not (target / 'bin/sdkmanager').is_file():
        raise ValueError('sdkmanager missing after unpack: ' + str(target))
    return target


def sdkmanager(sdk, java_home, *args, accept_input=None):
    """Run the managed sdkmanager with the managed JDK on PATH."""
    sdk, java_home = Path(sdk), Path(java_home)
    binary = sdk / 'cmdline-tools' / 'latest' / 'bin' / 'sdkmanager'
    env = os.environ.copy()
    env.update({'JAVA_HOME': str(java_home),
                'PATH': str(java_home / 'bin') + os.pathsep + env.get('PATH', '')})
    command = [str(binary), '--sdk_root=' + str(sdk)] + list(map(str, args))
    print('+', ' '.join(command), flush=True)
    subprocess.run(command, env=env, input=accept_input, check=True,
                   stdout=sys.stdout, stderr=sys.stderr)


def licenses_accepted(sdk):
    return (Path(sdk) / 'licenses' / 'android-sdk-license').is_file()


def require_licenses(sdk, accept_licenses, java_home):
    """Google's SDK licence is accepted by the user, never silently.

    With --accept-android-licenses the acceptance runs through sdkmanager
    (recorded under <sdk>/licenses). Without it, an interactive run may
    answer the prompt; anywhere else this fails with the documented path.
    """
    sdk = Path(sdk)
    if licenses_accepted(sdk):
        return
    print('The Android SDK requires Google\'s licence:')
    print('  ' + ANDROID_TERMS)
    print('Packages: ' + ' '.join(SDK_PACKAGES))
    if not accept_licenses:
        if sys.stdin.isatty():
            answer = input('Accept these licences and install into ' + str(sdk) + '? [y/N] ')
            if answer.strip().lower() not in ('y', 'yes'):
                raise ValueError('Android SDK licences not accepted; re-run with '
                                 '--accept-android-licenses to accept')
        else:
            raise ValueError('Android SDK licences not accepted: re-run with '
                             '--accept-android-licenses (terms: ' + ANDROID_TERMS + ')')
    yes = ('y\n' * 256).encode()
    sdkmanager(sdk, java_home, '--licenses', accept_input=yes)
    if not licenses_accepted(sdk):
        raise ValueError('licence acceptance did not record; aborting before install')
    print('Recorded SDK licence acceptance under ' + str(sdk / 'licenses'))


def ensure_jdk(work, jdk_explicit=None, auto_download=True):
    """(java_home, managed): existing install wins; fetch Temurin only when missing."""
    found = find_jdk(jdk_explicit)
    if found:
        return found, False
    have = managed_jdk(work)
    if have:
        return have, True
    if not auto_download:
        raise ValueError('missing JDK 17; set JAVA_HOME to a JDK 17 install '
                         'or drop --no-auto-download to let ./build fetch Temurin ' + JDK_RELEASE)
    key = _host_jdk_key()
    url, want_sha = JDK_URLS[key]
    archive = fetch(url, want_sha, downloads_dir(work) / ('temurin17-' + key + '.tar.gz'))
    staged = tool_dir(work) / ('jdk-' + key + '-' + JDK_RELEASE.replace('+', 'p'))
    if not jdk_ok(staged):
        shutil.rmtree(staged, ignore_errors=True)
        unpack_jdk(archive, tool_dir(work))
        got = managed_jdk(work)
        if got is None:
            raise ValueError('JDK fetch unpacked but no usable JDK 17 found')
        if got != staged:
            shutil.rmtree(staged, ignore_errors=True)
            got.replace(staged)
    print('JDK 17 managed at ' + str(staged))
    return staged, True


def ensure_sdk(work, java_home, auto_download=True, accept_licenses=False):
    """(sdk, managed): existing install wins; download + sdkmanager only when missing."""
    found = find_sdk()
    if found:
        return found, False
    have = managed_sdk(work)
    if have:
        return have, True
    if not auto_download:
        raise ValueError('missing Android SDK (need platforms;android-34 + '
                         'build-tools;34.0.0); set ANDROID_HOME to an SDK install '
                         'or drop --no-auto-download to let ./build fetch it')
    key = _host_cmdline_key()
    url, want_sha, want_bytes = CMDLINE_URLS[key]
    archive = fetch(url, want_sha, downloads_dir(work) / ('cmdline-tools-' + key + '.zip'),
                    want_bytes)
    sdk = tool_dir(work) / 'android-sdk'
    unpack_cmdline(archive, sdk)
    require_licenses(sdk, accept_licenses, java_home)
    sdkmanager(sdk, java_home, *SDK_PACKAGES)
    if not sdk_ok(sdk):
        raise ValueError('SDK install finished but the pinned platform/tools are missing')
    print('Android SDK managed at ' + str(sdk))
    return sdk, True


def ensure_ndk(sdk, ndk_explicit=None, auto_download=True):
    found = find_ndk(sdk, ndk_explicit)
    if found:
        return found
    if not auto_download:
        raise ValueError('missing NDK ' + NDK_VERSION + '; pass --ndk <path> '
                         'or drop --no-auto-download to let ./build fetch it')
    raise ValueError('NDK ' + NDK_VERSION + ' missing under ' + str(sdk) +
                     '; delete the managed SDK to re-install, or pass --ndk <path>')


def discover(work=None, jdk_explicit=None, ndk_explicit=None):
    """Read-only toolchain discovery: (java_home, sdk, sdk_managed, ndk).

    Never downloads, never writes. Managed entries count only when complete.
    """
    java_home = find_jdk(jdk_explicit)
    if java_home is None and work is not None:
        java_home = managed_jdk(work)
    sdk, sdk_managed = find_sdk(), False
    if sdk is None and work is not None:
        sdk = managed_sdk(work)
        sdk_managed = sdk is not None
    ndk = find_ndk(sdk, ndk_explicit)
    return java_home, sdk, sdk_managed, ndk


def resolve(work, jdk_explicit=None, ndk_explicit=None,
            auto_download=True, accept_licenses=False):
    """Ensure a usable JDK + SDK + NDK; returns the env mapping to apply.

    Downloads only when auto_download is set and something is missing. Any
    use of the builder-managed SDK requires the licence flag (or an
    interactive yes); system SDK installs need no flag.
    """
    java_home, jdk_managed = ensure_jdk(work, jdk_explicit, auto_download)
    sdk, sdk_managed = ensure_sdk(work, java_home, auto_download, accept_licenses)
    if sdk_managed:
        require_licenses(sdk, accept_licenses, java_home)
    ndk = ensure_ndk(sdk, ndk_explicit, auto_download)
    record = {'jdk': str(java_home), 'jdk_managed': jdk_managed,
              'sdk': str(sdk), 'sdk_managed': sdk_managed,
              'ndk': str(ndk), 'jdk_release': JDK_RELEASE,
              'cmdline_build': CMDLINE_BUILD}
    try:
        (tool_dir(work) / 'toolchain.json').write_text(
            json.dumps(record, indent=2, sort_keys=True) + '\n')
    except OSError:
        pass
    return {'JAVA_HOME': str(java_home), 'ANDROID_HOME': str(sdk),
            'ANDROID_SDK_ROOT': str(sdk), 'ANDROID_NDK_HOME': str(ndk),
            'PATH': str(Path(java_home) / 'bin') + os.pathsep + os.environ.get('PATH', ''),
            'managed_sdk': sdk_managed}


def apply(env):
    """Point this process (and its children) at the resolved toolchain."""
    os.environ['JAVA_HOME'] = env['JAVA_HOME']
    os.environ['ANDROID_HOME'] = env['ANDROID_HOME']
    os.environ['ANDROID_SDK_ROOT'] = env['ANDROID_SDK_ROOT']
    os.environ['ANDROID_NDK_HOME'] = env['ANDROID_NDK_HOME']
    os.environ['PATH'] = env['PATH']

