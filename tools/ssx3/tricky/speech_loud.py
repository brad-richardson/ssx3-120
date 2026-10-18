#!/usr/bin/env python3
"""speech_loud.py: loudness helpers (ffmpeg ebur128 measurement).

Port of TK25c tk25c_loud.py (port adaptations only: the cache path is set
via init_cache() instead of a hardcoded lane path). Integrated-loudness
measurement only; normalization is a plain gain in the slot builder so the
Tricky lines keep their dynamics.
"""
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import tool_paths  # noqa: E402

CACHE_PATH = None  # set via init_cache() before measuring

_cache = None


def init_cache(path):
    """Point the LUFS/peak cache at a work-dir file (port adaptation: the
    lane hardcoded a private scratch path)."""
    global CACHE_PATH, _cache
    CACHE_PATH = path
    _cache = None


def _path():
    if CACHE_PATH is None:
        raise RuntimeError("speech_loud.init_cache(path) was not called")
    return CACHE_PATH

def _load():
    global _cache
    if _cache is None:
        try:
            with open(_path()) as f:
                _cache = json.load(f)
        except (OSError, ValueError):
            _cache = {}
    return _cache

def _save():
    with open(_path(), "w") as f:
        json.dump(_load(), f, indent=1, sort_keys=True)

def measure_lufs(path):
    """Integrated loudness (LUFS) of an audio file via ebur128. Cached by path+mtime+size."""
    st = os.stat(path)
    key = f"lufs|{path}|{st.st_size}|{int(st.st_mtime)}"
    c = _load()
    if key in c:
        return c[key]
    ffmpeg = str(tool_paths.ffmpeg_path())
    r = subprocess.run([ffmpeg, "-hide_banner", "-v", "info", "-i", path,
                        "-af", "ebur128", "-f", "null", "-"],
                       capture_output=True, text=True)
    m = re.search(r"Integrated loudness:\s*\n\s*I:\s+(-?\d+(?:\.\d+)?)\s+LUFS", r.stderr)
    assert m, f"no LUFS for {path}:\n{r.stderr[-800:]}"
    v = float(m.group(1))
    c[key] = v
    _save()
    return v

def measure_peak_db(path):
    """Peak level (dBFS) via volumedetect. Cached like measure_lufs."""
    st = os.stat(path)
    key = f"peak|{path}|{st.st_size}|{int(st.st_mtime)}"
    c = _load()
    if key in c:
        return c[key]
    ffmpeg = str(tool_paths.ffmpeg_path())
    r = subprocess.run([ffmpeg, "-hide_banner", "-v", "info", "-i", path,
                        "-af", "volumedetect", "-f", "null", "-"],
                       capture_output=True, text=True)
    m = re.search(r"max_volume:\s+(-?\d+(?:\.\d+)?)\s+dB", r.stderr)
    assert m, f"no peak for {path}:\n{r.stderr[-800:]}"
    v = float(m.group(1))
    c[key] = v
    _save()
    return v

def apply_gain_db(src, gain_db, dst):
    ffmpeg = str(tool_paths.ffmpeg_path())
    r = subprocess.run([ffmpeg, "-hide_banner", "-v", "error", "-y",
                        "-i", src, "-af", f"volume={gain_db:.2f}dB", dst],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-500:]
