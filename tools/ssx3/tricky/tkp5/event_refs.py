#!/usr/bin/env python3
"""TKP5: which programs of a composite's course script are named by the header event tables (TK57 §5).

    event_refs.py ISO COMPOSITE.BIG --group G --work DIR [--programs K ...]

Prints JSON: program count, class count, load-table programs, the event-table program set (same rule as TK57
interact_step: any aligned word between the class table and the program table that reads k<<8|track), and for each
--programs K whether K is a stub, in the load table, named by a class row, named by an event table.
"""
import argparse, json, struct, sys
from pathlib import Path

sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO / 'tk53'), str(REPO / 'tk33b'),
                str(REPO / 'tk16'), str(REPO / 'tk13'), str(REPO / 'tk12'),
                str(REPO / 'tk21'), str(REPO /'shared')]
from break_step import STUB, NONE, materialize  # noqa: E402
from wlib import W  # noqa: E402
import gamecube_game_dir as gdir  # noqa: E402
gdir.WORLD_EXTENSIONS = ("sdb", "ssb", "phm", "psm")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('iso'); ap.add_argument('composite')
    ap.add_argument('--group', type=int, required=True)
    ap.add_argument('--work', required=True)
    ap.add_argument('--programs', type=int, nargs='*', default=[])
    a = ap.parse_args()
    work = Path(a.work); work.mkdir(parents=True, exist_ok=True)
    full = materialize(a.iso, a.composite)
    tmp = work / 'full.bam.BIG'
    tmp.write_bytes(gdir.rename_world_members(full, 'bam'))
    recs = W(tmp).records(a.group)
    se, data = next((e, p) for e, p in recs if e['kind'] == 16)
    T = se['track']
    lc, la, cc, ca = struct.unpack_from('<4I', data, 16)
    pc, ptab, pend = struct.unpack_from('<3I', data, 56)
    st = struct.unpack_from(f'<{pc}I', data, ptab) + (pend,)
    loads = {h >> 8 for h in struct.unpack_from(f'<{lc}I', data, la)}
    rows = [struct.unpack_from('<6I', data, ca + 24 * r) for r in range(cc)]
    ev_lo, ev_hi = ca + 24 * cc, ptab
    words = struct.unpack_from(f'<{(ev_hi - ev_lo) // 4}I', data, ev_lo)
    in_tables = sorted({x >> 8 for x in words if x & 0xff == T and x != NONE and (x >> 8) < pc})
    out = dict(composite=a.composite, group=a.group, track=T, program_count=pc, class_count=cc,
               loads=sorted(loads), event_table_programs=in_tables, checks=[])
    for k in a.programs:
        out['checks'].append(dict(program=k, stub=bytes(data[st[k]:st[k] + 36]) == STUB, load=k in loads,
                                  rows=[r for r, row in enumerate(rows) if any(h != NONE and h >> 8 == k for h in row)],
                                  event_table=k in in_tables))
    tmp.unlink()
    print(json.dumps(out, indent=1))


if __name__ == '__main__':
    main()
