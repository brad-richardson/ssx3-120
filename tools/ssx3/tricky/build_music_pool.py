#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later OR MIT
"""build_music_pool.py: build the two music-pool composites from an assign TSV.

Port of TKP4b tkp4b_build_music.py (port adaptations only: hardcoded lane
paths become CLI args; the tk41b_composite.py subprocess call now resolves
to sibling music_composite.py; main() under a __main__ guard).

Builds music1 (MUSIC.BIG, 20 slots x mpf+mus) and music2 (MUSIC2.BIG,
15 slots x mpf+mus) with music_composite.py (home offsets, shrunk sizes,
same total; fatal descriptor_reproduces_big). Then verifies each:
  (a) builder receipt reproduces_big true;
  (b) materialized member OFFSETS byte-identical to stock (home-slot);
  (c) replaced members' head bytes == the staged Tricky bytes;
  (d) every untouched member byte-identical to stock (banks, hub/menu).
Writes music{1,2}-tkp4b (+ build logs) into --out-dir.

Usage:
  build_music_pool.py --ssx3-iso <SSX3.iso> --stage-dir <members>
      --assign-tsv <assignment> --out-dir <dir>
Default assignment: music-pool-default.tsv (TKP4b's shared pool).
"""
import argparse
import json
import os
import struct
import subprocess
import sys
from pathlib import Path

TOOL = Path(__file__).resolve().parent
SEC = 2048

SSX3_ISO = ""
STAGE = Path()
BUILT = Path()


def iso_find(ssx3_iso, path):
    with open(ssx3_iso, "rb") as f:
        f.seek(16 * SEC + 156)
        rec = f.read(34)
        lba, size = struct.unpack_from("<I", rec, 2)[0], struct.unpack_from("<I", rec, 10)[0]
        for part in path.strip("/").upper().split("/"):
            f.seek(lba * SEC)
            d = f.read(size)
            i, found = 0, None
            while i < len(d):
                ln = d[i]
                if ln == 0:
                    i = (i // SEC + 1) * SEC
                    continue
                nl = d[i + 32]
                n = d[i + 33:i + 33 + nl].decode("latin1").split(";")[0]
                if n.upper() == part:
                    found = (struct.unpack_from("<I", d, i + 2)[0],
                             struct.unpack_from("<I", d, i + 10)[0])
                    break
                i += ln
            assert found, part
            lba, size = found
        f.seek(lba * SEC)
        return lba, f.read(size)


def big_members(buf):
    assert buf[:4] in (b"BIGF", b"BIG4"), buf[:4]
    n = struct.unpack(">I", buf[8:12])[0]
    hl = struct.unpack(">I", buf[12:16])[0]
    d = buf[16:16 + hl]
    i, out = 0, []
    for _ in range(n):
        off, sz = struct.unpack_from(">II", d, i)
        i += 8
        e = d.index(b"\0", i)
        out.append((d[i:e].decode("latin1"), off, sz))
        i = e + 1
    return out


def load_assign(assign_tsv):
    rows = []
    for line in Path(assign_tsv).read_text().splitlines():
        if line.startswith("B1/") or line.startswith("B2/"):
            slot, song, _, _ = line.split("\t")
            rows.append((slot, song))
    return rows


def mus_member(slot):
    return "mxpxsong.mus" if slot == "mxpx" else f"{slot}.mus"


def main(argv=None):
    global SSX3_ISO, STAGE, BUILT
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ssx3-iso", required=True)
    ap.add_argument("--stage-dir", required=True,
                    help="dir with staged <song>.mpf + <song>.mus members")
    ap.add_argument("--assign-tsv", required=True,
                    help="slot assignment (B1//B2 rows; default: music-pool-default.tsv)")
    ap.add_argument("--out-dir", required=True)
    a = ap.parse_args(argv)
    SSX3_ISO = a.ssx3_iso
    STAGE = Path(a.stage_dir)
    BUILT = Path(a.out_dir)
    BUILT.mkdir(parents=True, exist_ok=True)
    rows = load_assign(a.assign_tsv)
    assert len(rows) == 35, len(rows)
    assert all(s != "STOCK" for _, s in rows)
    for big, disc in ((1, "DATA/AUDIO/MUSIC.BIG"), (2, "DATA/AUDIO/MUSIC2.BIG")):
        mine = [(r[0].split("/")[1], r[1]) for r in rows if r[0].startswith(f"B{big}/")]
        specs = []
        for slot, song in mine:
            specs.append(f"data\\audio\\{slot}.mpf={STAGE}/{song}.mpf")
            specs.append(f"data\\audio\\{mus_member(slot)}={STAGE}/{song}.mus")
        out = BUILT / f"music{big}-tkp4b"
        mat = BUILT / f"music{big}-tkp4b.materialized.big"
        cmd = [sys.executable, str(TOOL / "music_composite.py"), str(SSX3_ISO), disc, str(out),
               "--materialized", str(mat)] + specs
        print(f"building {out.name}: {len(mine)} slots", flush=True)
        r = subprocess.run(cmd, capture_output=True, text=True)
        (BUILT / f"music{big}-tkp4b.build.log").write_text(r.stdout + "\n--- stderr ---\n" + r.stderr)
        print(r.stdout[-1500:])
        if r.returncode != 0:
            print(r.stderr[-3000:])
            sys.exit(f"composite build failed rc={r.returncode}")
        assert '"descriptor_reproduces_big": true' in r.stdout, "builder receipt missing reproduces_big"
        # (b,c,d) verify against the materialized BIG
        _, stock = iso_find(SSX3_ISO, disc)
        sm = {n: (o, s) for (n, o, s) in big_members(stock)}
        pm = {n: (o, s) for (n, o, s) in big_members(mat.read_bytes())}
        assert set(sm) == set(pm), "member set changed"
        off_bad = [n for n in sm if sm[n][0] != pm[n][0]]
        assert not off_bad, f"offsets moved: {off_bad}"
        pbuf = mat.read_bytes()
        repl = {}
        for slot, song in mine:
            repl[f"data\\audio\\{slot}.mpf"] = (STAGE / f"{song}.mpf").read_bytes()
            repl[f"data\\audio\\{mus_member(slot)}"] = (STAGE / f"{song}.mus").read_bytes()
        for n, want in repl.items():
            o, s = pm[n]
            assert s == len(want), (n, s, len(want))
            assert pbuf[o:o + s] == want, f"content {n}"
        for n, (o, s) in sm.items():
            if n in repl:
                continue
            assert pbuf[pm[n][0]:pm[n][0] + s] == stock[o:o + s], f"untouched {n}"
        print(f"{out.name}: offsets home ({len(sm)} members), "
              f"{len(repl)} replaced bytes exact, {len(sm) - len(repl)} untouched identical")
        mat.unlink()  # transient (~450 MB); the composite + builder receipt persist
    print("BUILD OK")


if __name__ == "__main__":
    main()
