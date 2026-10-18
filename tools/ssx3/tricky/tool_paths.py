#!/usr/bin/env python3
"""Tricky audio-tool resolution (tk-toolchain-1; Brad 10-18: audio optional).

uv is the only hard requirement. ffmpeg + vgmstream are OPTIONAL and
always local: explicit argv > SSX3_TRICKY_* env > PATH plus well-known
Homebrew/apt prefixes, each gated by a minimum-version and capability
probe. Nothing is downloaded and nothing is pinned: a machine without
them builds everything except Tricky audio (the driver skips those
phases with one clear message and stages no audio aliases, so the
runtime falls back to SSX 3 audio on Tricky courses).
"""
import hashlib
import os
import platform
import re
import shutil
import subprocess
from pathlib import Path

FFMPEG_ENV = 'SSX3_TRICKY_FFMPEG'
VGMSTREAM_ENV = 'SSX3_TRICKY_VGMSTREAM'

# Minimum local versions (the capability probe is the real gate; these
# only refuse ancients). Ubuntu 24.04 apt ships ffmpeg 6.x, so the floor
# is 6.0; the MicroTalk SCHl decode path in vgmstream is years old.
FFMPEG_MIN_VERSION = (6, 0)
VGMSTREAM_MIN_R = 1900

# Extra prefixes searched for local tools when PATH is minimal
# (launchd/cron/systemd); shutil.which(PATH) is always tried first.
LOCAL_PREFIXES = ('/opt/homebrew/bin', '/usr/local/bin',
                  '/usr/bin', '/bin')


def _install_hint():
    if platform.system() == 'Darwin':
        return ('macOS: brew install ffmpeg vgmstream '
                '(then rebuild; no other installs needed)')
    return ('Linux: apt install ffmpeg vgmstream '
            '(some distros lack vgmstream: build the CLI from '
            'https://github.com/vgmstream/vgmstream, then rebuild)')


def audio_missing_message():
    return ('Tricky courses will use SSX 3 music and speech; '
            'install ffmpeg + vgmstream and rebuild for Tricky audio '
            '(' + _install_hint() + ').')


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def find_local(name):
    """Every local install of `name`: PATH first, then known prefixes."""
    seen = set()
    found = []
    which = shutil.which(name)
    if which:
        found.append(Path(which))
        seen.add(str(Path(which)))
    for prefix in LOCAL_PREFIXES:
        cand = Path(prefix, name)
        if str(cand) in seen:
            continue
        seen.add(str(cand))
        if cand.is_file() and os.access(cand, os.X_OK):
            found.append(cand)
    return found


def probe_ffmpeg(exe):
    """Version + capability gate on a local ffmpeg; returns its identity.

    Needs >= 6.0 and a working lavfi source through the exact filters
    the audio phases use (ebur128 + volumedetect loudness, aresample
    resampling). Anything else fails with the install line.
    """
    exe = Path(exe)
    if not exe.is_file() or not os.access(exe, os.X_OK):
        raise ValueError(f'ffmpeg is not an executable file: {exe} '
                         f'({_install_hint()}).')
    try:
        out = subprocess.run([str(exe), '-version'], capture_output=True,
                             text=True, timeout=60)
        first = (out.stdout.splitlines() or [''])[0]
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError(f'ffmpeg does not run: {exe}: {error} '
                         f'({_install_hint()}).')
    m = re.search(r'ffmpeg version (\d+)\.(\d+)', first)
    if not m or (int(m.group(1)), int(m.group(2))) < FFMPEG_MIN_VERSION:
        raise ValueError(
            f'ffmpeg too old: {first!r} (need >= '
            f'{FFMPEG_MIN_VERSION[0]}.{FFMPEG_MIN_VERSION[1]}; '
            f'{_install_hint()}).')
    cap = subprocess.run(
        [str(exe), '-hide_banner', '-loglevel', 'error',
         '-f', 'lavfi', '-i', 'sine=frequency=440:duration=1',
         '-af', 'ebur128,volumedetect,aresample=32000', '-f', 'null', '-'],
        capture_output=True, text=True, timeout=120)
    if cap.returncode != 0:
        raise ValueError(
            f'ffmpeg lacks the audio filters the Tricky phases need '
            f'(ebur128/volumedetect/aresample): {(cap.stderr or "")[-300:]} '
            f'({_install_hint()}).')
    print(f'ffmpeg: {first}', flush=True)
    return {'path': str(exe), 'version': first, 'sha256': sha256_file(exe)}


def resolve_ffmpeg(explicit=None):
    """Local ffmpeg identity, or (None, reason) when unusable/absent.

    Explicit (argv/env) is probed too, so a typo fails here with the
    fix instead of mid-encode.
    """
    cand = explicit or os.environ.get(FFMPEG_ENV)
    if cand:
        try:
            return probe_ffmpeg(cand), ''
        except ValueError as error:
            raise ValueError(f'{FFMPEG_ENV or "explicit"} ffmpeg: {error}')
    failures = []
    for cand in find_local('ffmpeg'):
        try:
            return probe_ffmpeg(cand), ''
        except ValueError as error:
            failures.append(f'{cand}: {error}')
    if failures:
        return None, 'unusable local ffmpeg: ' + '; '.join(failures)
    return None, 'no ffmpeg on PATH (' + _install_hint() + ')'


def _probe_vgmstream(exe):
    """The binary must run; returns its version line (best effort)."""
    try:
        out = subprocess.run([str(exe), '-h'], capture_output=True,
                             text=True, timeout=60)
        text = out.stdout + out.stderr
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError(f'vgmstream-cli does not run: {exe}: {error} '
                         f'({_install_hint()}).')
    version = ''
    for line in text.splitlines():
        if 'vgmstream' in line.lower() and ('r21' in line or 'libvgmstream' in line):
            version = line.strip()
            break
    return version or '(version line not parsed)'


def _smoke_vgmstream(exe, iso_member_bytes):
    """Decode one real SCHl stream; empty output fails closed."""
    import struct
    import tempfile
    pos = [m.start() for m in re.finditer(b'SCHl', iso_member_bytes)]
    if not pos:
        raise ValueError('vgmstream smoke: no SCHl stream in the probe member')
    end = pos[1] if len(pos) > 1 else len(iso_member_bytes)
    e = iso_member_bytes.find(b'SCEl', pos[0])
    esz = struct.unpack('<I', iso_member_bytes[e + 4:e + 8])[0]
    with tempfile.TemporaryDirectory(prefix='vgmprobe-') as tmp:
        sp = Path(tmp, 'probe.str')
        wp = Path(tmp, 'probe.wav')
        sp.write_bytes(iso_member_bytes[pos[0]:e + esz])
        r = subprocess.run([str(exe), '-o', str(wp), str(sp)],
                           capture_output=True, text=True, timeout=300)
        if not wp.is_file() or wp.stat().st_size == 0:
            raise ValueError(
                f'vgmstream smoke decode failed: {(r.stdout + r.stderr)[-500:]}')
        size = wp.stat().st_size
    print(f'vgmstream smoke: one SCHl stream -> {size} byte WAV', flush=True)
    return size


def probe_vgmstream(exe, smoke_bytes=None):
    """Version + smoke-decode gate on a local vgmstream-cli."""
    exe = Path(exe)
    if not exe.is_file() or not os.access(exe, os.X_OK):
        raise ValueError(f'vgmstream-cli is not an executable file: {exe} '
                         f'({_install_hint()}).')
    version = _probe_vgmstream(exe)
    m = re.search(r'\br(\d+)', version)
    if not m or int(m.group(1)) < VGMSTREAM_MIN_R:
        raise ValueError(
            f'vgmstream-cli too old or unversioned: {version!r} (need '
            f'r{VGMSTREAM_MIN_R}+; {_install_hint()}).')
    ident = {'path': str(exe), 'version': version,
             'sha256': sha256_file(exe)}
    if smoke_bytes is not None:
        ident['smoke_wav_bytes'] = _smoke_vgmstream(exe, smoke_bytes)
    print(f'vgmstream: {exe} [{version}]', flush=True)
    return ident


def resolve_vgmstream(explicit=None, smoke_bytes=None):
    """Local vgmstream-cli (path, identity), or (None, None, reason)."""
    cand = explicit or os.environ.get(VGMSTREAM_ENV)
    if cand:
        try:
            ident = probe_vgmstream(cand, smoke_bytes)
            return Path(cand), ident, ''
        except ValueError as error:
            raise ValueError(f'explicit vgmstream: {error}')
    failures = []
    for cand in find_local('vgmstream-cli'):
        try:
            ident = probe_vgmstream(cand, smoke_bytes)
            return cand, ident, ''
        except ValueError as error:
            failures.append(f'{cand}: {error}')
    if failures:
        return None, None, 'unusable local vgmstream-cli: ' + '; '.join(failures)
    return None, None, 'no vgmstream-cli on PATH (' + _install_hint() + ')'


def ffmpeg_path(explicit=None):
    """Resolved local ffmpeg path for phase helpers (audio is on here,
    so absence is a hard error with the install line)."""
    ident, reason = resolve_ffmpeg(explicit)
    if ident is None:
        raise ValueError(reason)
    return ident['path']
