#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later OR MIT
"""music_selfcheck.py <workdir>: music tooling regression checks.

Port of TK41 tk41_selfcheck.py (port adaptations only: workdir is a required
arg, no lane default). Needs a work dir with extract/tricky/<song>.mpf
extracts. Fails loudly on the first regression:
  1. all 21 Tricky .mpf parse (shakemomma shape, ginandsin/systemover
     endmark+1 plays);
  2. peaktime/downtime/superwoman converts reproduce arrangement + events;
  3. remus only=/jobs= parse by name (positional footgun guard).
"""
import os
import subprocess
import sys

TOOL = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TOOL)
from mpf3to4 import parse_v3, default_path  # noqa: E402

SONGS = ("adamsrev ginandsin ssxmenu bassinvader kingbeat bburner peaktime "
         "reality shakemomma smartbomb downtime finsym hiphop songfordot "
         "superwoman systemover topbomb moveit femenuquad slaybreak "
         "slayboarder").split()


def main():
    if len(sys.argv) < 2:
        raise SystemExit("usage: music_selfcheck.py <workdir with extract/tricky/*.mpf>")
    w = sys.argv[1]
    ex = os.path.join(w, "extract", "tricky")
    # 1. every song parses; every sample in range
    for s in SONGS:
        p = parse_v3(open(os.path.join(ex, s + ".mpf"), "rb").read())
        total = len(p["samples"])
        assert total > 0, s
        for nd in p["nodes"]:
            pl = nd["play"]
            if isinstance(pl, list):
                assert all(0 <= x < total for x in pl), (s, nd["idx"])
            elif pl is not None:
                assert 0 <= pl < total, (s, nd["idx"], pl)
    print(f"parse-all-21 OK ({len(SONGS)} songs)")
    # 2. converts preserve arrangement + events (Elysium trio)
    for s in ("peaktime", "downtime", "superwoman"):
        r = subprocess.run(
            [sys.executable, os.path.join(TOOL, "mpf3to4.py"), "convert",
             os.path.join(ex, s + ".mpf"), os.path.join(ex, s + ".mus"),
             "/tmp/tk41_selfcheck.mpf"],
            capture_output=True, text=True)
        assert r.returncode == 0, (s, r.stderr[-300:])
        assert "play order identical" in r.stdout, (s, r.stdout[-300:])
        assert "targets preserved" in r.stdout, (s, r.stdout[-300:])
    print("convert-trio OK (arrangement + events preserved)")
    # 3. remus arg parsing by name
    src = open(os.path.join(TOOL, "tk41_remus.py")).read()
    assert 'if k == "only"' in src and 'elif k == "jobs"' in src
    print("remus-args OK (name-based)")
    print("SELFCHECK PASS")


if __name__ == "__main__":
    main()
