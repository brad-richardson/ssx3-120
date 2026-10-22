#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later OR MIT
"""tk-mega-1: derive the borrowed rail host-constants template from a stock SSX 3 world archive.

    rail_template.py STOCK-BAM.BIG OUT.json [--group 115] [--tmp DIR]

Reads the kind-8 spline table of one stock group (default 115, the DSS2
host group) plus its single kind-16 spline-binding script, replicating the
template selection in tk21/make_rails21.py: the first rail-bound
(03000a00) spline with >= 2 segments supplies header [36:48],
second-segment prefix [192:204] and trailer [184:192]; the first-segment
prefix [48:60] is the majority value across rail-bound splines (same
Counter ordering). Writes {"host_constants": {...}} for --template.

Game data stays outside git: only the four host-constant hex strings go
to OUT.json beside the chain workdir (never committed).
"""
import argparse, json, struct, sys
from collections import Counter
from pathlib import Path
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent / 'tk12'))
sys.path.insert(0, str(_HERE.parent / 'tk13'))
sys.path.insert(0, str(_HERE.parent / 'shared'))
from wlib import W
from probe_worlds import resource_records
import gamecube_game_dir as gdir

gdir.WORLD_EXTENSIONS = ('sdb', 'ssb', 'phm', 'psm')
RAIL_BINDING = bytes.fromhex('03000a00')


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('stock_bam')
    ap.add_argument('out')
    ap.add_argument('--group', type=int, default=115)
    ap.add_argument('--tmp', default=None,
                    help='scratch for the bam-renamed copy (default: <out-dir>/tmp)')
    a = ap.parse_args()
    base_bytes = Path(a.stock_bam).read_bytes()
    tmp = None
    if W(a.stock_bam).stem != 'bam':  # probe/resource records read data/worlds/bam.*: rename in a scratch copy
        tdir = Path(a.tmp) if a.tmp else Path(a.out).parent / 'tmp'
        tdir.mkdir(parents=True, exist_ok=True)
        tmp = str(tdir / 'template-bam.BIG')
        Path(tmp).write_bytes(gdir.rename_world_members(base_bytes, 'bam'))
    base = W(tmp or a.stock_bam)
    recs = list(resource_records(base.raw(a.group)))
    old = [(e, p) for e, p in recs if e['kind'] == 8]
    scripts = [(e, p) for e, p in recs if e['kind'] == 16]
    if len(scripts) != 1:
        raise SystemExit('expected one kind-16 record')
    script = scripts[0][1]
    count, table = struct.unpack_from('<II', script, 84)
    if count != len(old) or table + 4 * count != len(script):
        raise SystemExit('unsupported host spline binding table extent')
    bindings = [script[table + 4 * i: table + 4 * i + 4] for i in range(count)]
    tmpl = next((p for (e, p), b in zip(old, bindings)
                 if b == RAIL_BINDING and struct.unpack_from('<I', p, 32)[0] >= 2), None)
    if tmpl is None:
        raise SystemExit('no rail-bound spline with >= 2 segments')
    seg_first_prefix = Counter(p[48:60] for (e, p), b in zip(old, bindings)
                               if b == RAIL_BINDING).most_common(1)[0][0]
    consts = dict(header_36_48=tmpl[36:48].hex(), first_prefix=seg_first_prefix.hex(),
                  prefix=tmpl[192:204].hex(), trailer=tmpl[184:192].hex())
    if any(p[48 + 144 * j + 136:48 + 144 * j + 144] != tmpl[184:192]
           for _, p in old for j in range(struct.unpack_from('<I', p, 32)[0])):
        raise SystemExit('host segment trailers differ; profile does not hold')
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(dict(host_constants=consts), indent=1) + '\n')
    print(json.dumps(dict(stock_bam=a.stock_bam, group=a.group, host_splines=len(old),
                          rail_bound=sum(b == RAIL_BINDING for b in bindings),
                          host_constants=consts, out=a.out), indent=1))


if __name__ == '__main__':
    main()
