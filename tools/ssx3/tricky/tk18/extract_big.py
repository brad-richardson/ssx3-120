#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later OR MIT
"""TK18: decode every member of a Tricky DATA/MODELS/<C>.BIG (C0FB, RefPack) into OUTDIR/<basename>.

    extract_big.py BIG OUTDIR
"""
import hashlib, io, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] /'shared'))
from inspect_disc import Region, big_members
from inventory_tricky import decode_member

data = Path(sys.argv[1]).read_bytes()
out = Path(sys.argv[2]); out.mkdir(parents=True, exist_ok=True)
region = Region(io.BytesIO(data), 0, len(data))
kind, members = big_members(region)
for e in members:
    raw, packed = decode_member(region, e)
    name = e['path'].split('/')[-1]
    (out / name).write_bytes(raw)
    print(f"{name}\t{len(raw)}\t{'refpack' if packed else 'raw'}\t{hashlib.sha256(raw).hexdigest()[:16]}")
