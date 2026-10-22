#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later OR MIT
"""Native RefPack search behind the Python reference. [native-refpack-1]

`refpack_optimal.plan` (backward DP) is ~65% of the Tricky pack search and
`find_matches`/`_extend` another ~55% (profiles overlap); the greedy probes
in `largest_fitting` add ~34% [tk-speed-1 §3]. This module builds a small C
library (`refpack_native.c` in this directory) with the host compiler and
loads it via ctypes. Same API shapes as the Python reference; every entry
point falls back to the Python implementation (loudly, never silently) when
the build fails, the library mismatches, or `REFPACK_NATIVE=off`.

Build cache: `REFPACK_NATIVE_DIR` (build_tricky.py / run_chain.py set it
to `<work>/native`, so course/inner workers share one build) else
`~/.cache/ssx3-refpack-native`. The file name keys on the sha256 of the C
source + order-table header, so edits rebuild once and every other process
reuses the cached library. No new user prerequisites: users already need a
C compiler + CMake for ./build; only `cc` is invoked here.

Determinism: the C code is single-threaded with fixed-width integers and
no platform-dependent iteration, so output is identical across platforms
and thread counts (verified Mac arm64 + Linux x86_64).
"""
import ctypes
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCES = (HERE / 'refpack_native.c', HERE / 'refpack_cand_order.h')

_ENV_DIR = 'REFPACK_NATIVE_DIR'
_ENV_MODE = 'REFPACK_NATIVE'  # auto (default) | off
_VERSION = 'refpack-native 1 (optimal plan + greedy, order-table v1)'

_lib = None
_state = None  # (ok: bool, detail: str)
_logged = False


def _log_once(msg):
    global _logged
    if not _logged:
        _logged = True
        print(f'refpack-native: {msg}', flush=True)


def _cache_dir():
    d = os.environ.get(_ENV_DIR)
    if d:
        return Path(d)
    return Path.home() / '.cache' / 'ssx3-refpack-native'


def _lib_name():
    h = hashlib.sha256()
    for src in SOURCES:
        h.update(src.read_bytes())
    tag = h.hexdigest()[:16]
    suffix = '.dll' if sys.platform == 'win32' else '.so'
    return f'refpack_native-{tag}{suffix}'


def _compiler():
    cc = os.environ.get('CC')
    if cc:
        return [cc]
    hit = shutil.which('cc') or shutil.which('clang') or shutil.which('gcc')
    return [hit] if hit else []


def _try_build(dest):
    cmd = _compiler()
    if not cmd:
        return 'no C compiler found (cc/clang/gcc)'
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + '.tmp')
    args = cmd + ['-O2', '-fPIC', '-shared', '-o', str(tmp),
                  str(HERE / 'refpack_native.c')]
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.SubprocessError) as e:
        return f'compiler failed to start: {e}'
    if p.returncode != 0:
        try:
            tmp.unlink()
        except OSError:
            pass
        err = (p.stderr or '').strip().splitlines()
        return 'compile failed: ' + '; '.join(err[-3:] or ['unknown error'])
    try:
        os.replace(tmp, dest)
    except OSError as e:
        return f'cache install failed: {e}'
    return ''


def _load():
    """Return (CDLL or None, detail string). Never raises."""
    mode = os.environ.get(_ENV_MODE, 'auto').lower()
    if mode == 'off':
        return None, 'disabled via REFPACK_NATIVE=off'
    try:
        want = _lib_name()
    except OSError as e:
        return None, f'sources unreadable: {e}'
    base = str(_cache_dir() / want)
    try:
        if Path(base).is_file():
            lib = ctypes.CDLL(base)
            lib.rp_version.restype = ctypes.c_char_p
            if lib.rp_version().decode() != _VERSION:
                return None, f'version mismatch in cached {base}'
            return lib, f'loaded {base}'
    except (OSError, AttributeError, UnicodeDecodeError) as e:
        return None, f'cached library unusable ({base}): {e}'
    dest = _cache_dir() / want
    err = _try_build(dest)
    if err:
        return None, err
    try:
        lib = ctypes.CDLL(str(dest))
        lib.rp_version.restype = ctypes.c_char_p
        if lib.rp_version().decode() != _VERSION:
            return None, 'version mismatch after fresh build'
        return lib, f'built {dest}'
    except (OSError, AttributeError, UnicodeDecodeError) as e:
        return None, f'fresh build unusable: {e}'


def _lib_or_none():
    global _lib, _state
    if _lib is None and _state is None:
        _lib, detail = _load()
        _state = (_lib is not None, detail)
        _log_once(detail if _lib is not None
                  else f'falling back to Python ({detail})')
    return _lib


def available():
    """(ok, detail): whether the native library is usable in this process."""
    lib = _lib_or_none()
    return (lib is not None), _state[1]


def ensure(cache_dir):
    """Pre-build the library into cache_dir; returns (ok, detail).

    build_tricky.py / run_chain.py call this at startup so the whole run
    (including Pool workers via REFPACK_NATIVE_DIR) shares one cached
    build. Best effort: failure only means the Python fallback is used."""
    global _ENV_DIR
    cache_dir = Path(cache_dir)
    old = os.environ.get(_ENV_DIR)
    os.environ[_ENV_DIR] = str(cache_dir)
    global _lib, _state
    _lib, _state = None, None
    lib = _lib_or_none()
    ok, detail = (lib is not None), _state[1]
    if old is None:
        os.environ.pop(_ENV_DIR, None)
    else:
        os.environ[_ENV_DIR] = old
    _lib, _state = None, None
    return ok, detail


def _setup(lib):
    # All pointers are c_void_p: bytes and ctypes arrays both convert.
    lib.rp_version.restype = ctypes.c_char_p
    lib.rp_encode_cap.restype = ctypes.c_int
    lib.rp_encode_cap.argtypes = [ctypes.c_int]
    lib.rp_find_matches.restype = ctypes.c_int
    lib.rp_find_matches.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int,
                                    ctypes.c_void_p, ctypes.c_void_p,
                                    ctypes.c_void_p, ctypes.c_void_p]
    lib.rp_optimal_plan.restype = ctypes.c_int
    lib.rp_optimal_plan.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int,
                                    ctypes.c_void_p, ctypes.c_void_p,
                                    ctypes.c_void_p, ctypes.c_void_p,
                                    ctypes.c_int]
    lib.rp_optimal_encode.restype = ctypes.c_int
    lib.rp_optimal_encode.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                      ctypes.c_int, ctypes.c_int,
                                      ctypes.c_void_p, ctypes.c_int]
    lib.rp_greedy_encode.restype = ctypes.c_int
    lib.rp_greedy_encode.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                     ctypes.c_int, ctypes.c_void_p,
                                     ctypes.c_int]


def find_matches(data, candidates=128, skip_threshold=64):
    """Native find_matches; same return shape as refpack_optimal."""
    if int(candidates) < 0:
        raise ValueError('candidates must be non-negative')
    lib = _lib_or_none()
    if lib is None:
        raise RuntimeError('native unavailable: ' + _state[1])
    _setup(lib)
    data = bytes(data)
    n = len(data)
    nm = (ctypes.c_ubyte * (n + 1))()
    mc = (ctypes.c_ubyte * (n * 3 + 1))()
    ml = (ctypes.c_int * (n * 3 + 1))()
    md = (ctypes.c_int * (n * 3 + 1))()
    rc = lib.rp_find_matches(data, n, int(candidates), int(skip_threshold),
                             nm, mc, ml, md)
    if rc != 0:
        raise RuntimeError(f'rp_find_matches failed (rc={rc})')
    out = [()] * n
    for pos in range(n):
        k = nm[pos]
        out[pos] = tuple((ml[pos * 3 + t], md[pos * 3 + t]) for t in range(k))
    return out


def plan(data, candidates=128, skip_threshold=64):
    """Native plan(); picks are ('lit', k) / ('match', p, L, dist) /
    ('stop', rem), exactly like refpack_optimal.plan."""
    if int(candidates) < 0:
        raise ValueError('candidates must be non-negative')
    lib = _lib_or_none()
    if lib is None:
        raise RuntimeError('native unavailable: ' + _state[1])
    _setup(lib)
    data = bytes(data)
    n = len(data)
    if n > 0xffffff:
        raise ValueError('10FB supports at most 24-bit output lengths')
    kinds = (ctypes.c_ubyte * (n + 1))()
    pa = (ctypes.c_int * (n + 1))()
    pb = (ctypes.c_int * (n + 1))()
    pc = (ctypes.c_int * (n + 1))()
    rc = lib.rp_optimal_plan(data, n, int(candidates), int(skip_threshold),
                             kinds, pa, pb, pc, n + 1)
    if rc < 0:
        raise RuntimeError(f'rp_optimal_plan failed (rc={rc})')
    picks = []
    for i in range(rc):
        k = kinds[i]
        if k == 0:
            picks.append(('stop', int(pa[i])))
        elif k == 1:
            picks.append(('lit', int(pa[i])))
        elif k == 2:
            picks.append(('match', int(pa[i]), int(pb[i]), int(pc[i])))
        else:
            raise RuntimeError(f'bad pick kind {k}')
    return picks


def encode_optimal(data, candidates=128, skip_threshold=64):
    """Native refpack_optimal.encode."""
    lib = _lib_or_none()
    if lib is None:
        raise RuntimeError('native unavailable: ' + _state[1])
    _setup(lib)
    data = bytes(data)
    n = len(data)
    if n > 0xffffff:
        raise ValueError('10FB supports at most 24-bit output lengths')
    cap = lib.rp_encode_cap(n)
    out = (ctypes.c_ubyte * cap)()
    rc = lib.rp_optimal_encode(data, n, int(candidates), int(skip_threshold),
                               out, cap)
    if rc < 0:
        raise RuntimeError('rp_optimal_encode failed')
    return bytes(out[:rc])


def encode_greedy(data, candidates=64):
    """Native refpack_encode.encode."""
    if int(candidates) < 1:
        raise ValueError('At least one match candidate is required')
    lib = _lib_or_none()
    if lib is None:
        raise RuntimeError('native unavailable: ' + _state[1])
    _setup(lib)
    data = bytes(data)
    n = len(data)
    if n > 0xffffff:
        raise ValueError('10FB supports at most 24-bit output lengths')
    cap = lib.rp_encode_cap(n)
    out = (ctypes.c_ubyte * cap)()
    rc = lib.rp_greedy_encode(data, n, int(candidates), out, cap)
    if rc < 0:
        raise RuntimeError('rp_greedy_encode failed')
    return bytes(out[:rc])
