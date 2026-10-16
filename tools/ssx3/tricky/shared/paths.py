#!/usr/bin/env python3
"""Environment-driven defaults for author-machine data locations.

This repository ships no game data: every tool that reads discs, extracted
trees, or staged builds takes an explicit path flag. When a flag is omitted,
the default below is used instead of a hard-coded author-machine path.

Two variables:

- ``SSX3_WORKBENCH``: root of the ssx3-workbench tree. Expected layout:
  ``builds/`` (staged experiment builds), ``source/`` (e.g.
  ``source/ssx3/BAM.BIG``), ``extracted/`` (e.g.
  ``extracted/garibaldi/gari.pbd``), ``native/`` (e.g. ``native/GXBE69``,
  the extracted GameCube tree), ``emulator/`` (PCSX2 profiles).
- ``SSX3_GAMES``: root of the local games library. Expected layout:
  ``ps2/`` (e.g. ``SSX 3 (USA).iso``), ``gamecube/`` (e.g.
  ``SSX 3 (USA).rvz``).

Each helper raises a clear error naming its variable when the variable is
unset; callers resolve the default only when the corresponding flag is
omitted, so importing this module never raises.
"""
import os
from pathlib import Path


def workbench_root() -> Path:
    """Root of the ssx3-workbench tree (see module docstring for layout)."""
    try:
        return Path(os.environ["SSX3_WORKBENCH"])
    except KeyError:
        raise RuntimeError(
            "SSX3_WORKBENCH is not set; point it at your ssx3-workbench "
            "tree (builds/, source/, extracted/, native/) or pass the "
            "path explicitly with the tool's path flag."
        ) from None


def games_root() -> Path:
    """Root of the local games library (see module docstring for layout)."""
    try:
        return Path(os.environ["SSX3_GAMES"])
    except KeyError:
        raise RuntimeError(
            "SSX3_GAMES is not set; point it at your local games library "
            "(ps2/, gamecube/) or pass the path explicitly with the "
            "tool's path flag."
        ) from None
