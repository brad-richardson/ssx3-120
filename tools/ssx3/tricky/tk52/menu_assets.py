#!/usr/bin/env python3
"""Extract current COURSPIT members for the game's existing FE async loader.

No pixel/header/texture-ID changes. One synthetic ISO file per selected member;
the game supplies ownership and texture handles through its normal async slot.
"""
import argparse, io, json, sys
from pathlib import Path
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] /'shared'))
from inspect_disc import Region, big_members
from preview_step import twice, sha

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--courspit', required=True)
p.add_argument('--member', action='append', required=True)
p.add_argument('--out', required=True)
p.add_argument('--receipt', required=True)
a = p.parse_args()
data, inp = twice(a.courspit)
_, members = big_members(Region(io.BytesIO(data), 0, len(data)))
outputs = []
for name in a.member:
    m = next(m for m in members if m['path'].casefold() == name.casefold())
    blob = data[m['offset']:m['offset']+m['size']]
    assert blob[:4] == b'SHPS' and len(blob) <= 0x109a0
    dest = Path(a.out) / 'DATA/UI' / ('TP'+m['path'].upper())
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(blob)
    back, receipt = twice(dest)
    assert back == blob
    outputs.append(dict(member=name, input_member_sha256=sha(blob),
                        texture_id=blob[16:20].hex(), output=receipt))
Path(a.receipt).write_text(json.dumps(dict(input=inp, outputs=outputs), indent=2)+'\n')
