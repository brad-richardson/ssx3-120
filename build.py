#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later OR MIT
"""ssx3-120 product entrypoint: build from your own disc.

Forwards to tools/ssx3/build.py with the pinned engine (deps/PS2Recomp)
unless --engine or SSX3_ENGINE_DIR selects another checkout. Run
`python3 build.py doctor --target prepare` first; keep game outputs in an
external --work directory (never inside this checkout).
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENGINE_DEFAULT = HERE / "deps" / "PS2Recomp"

if __name__ == "__main__":
    import os
    os.environ.setdefault("SSX3_ENGINE_DIR", str(ENGINE_DEFAULT))
    sys.path.insert(0, str(HERE / "tools" / "ssx3"))
    sys.argv[0] = str(HERE / "tools" / "ssx3" / "build.py")
    import runpy
    runpy.run_path(str(HERE / "tools" / "ssx3" / "build.py"), run_name="__main__")
