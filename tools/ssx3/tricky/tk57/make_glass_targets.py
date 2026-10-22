#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later OR MIT
"""TK57 glass targets, one course (port of the TK57 lane glass_targets.py).

Reads the donor <stem>.pbd/.ssf/.map from the run workdir (extracted from
the user's Tricky disc) plus the course's scenery receipt, and writes the
single course glass-targets entry as a one-key dict, the same entry schema
the tracked targets file carries. The driver generates this into the
workdir at build time; nothing game-derived is tracked in git.

Each Tricky `Break*` function is a list of op-7 (instance, effect)
commands. Effect body (0, [5, 4]) = hide (DeadNode); (0, [20, 1, ...]) =
reveal the broken twin. The pane is the hide member that is visible with
collision mode 2 (the rider-hit record); the other hide members are the
scan overlays. Members map to world rids through the scenery receipt
(instance_source).

Usage:
  make_glass_targets.py --donor-dir <donor/<stem>/> --donor-stem <stem>
      --scenery-receipt <receipt.json> --scenery-name <name>
      --world <WORLD> --course <course> --out <targets.json>
"""
import argparse
import json
import struct
import sys
from pathlib import Path

sys.dont_write_bytecode = True
R = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(R / 'tk13'), str(R / 'shared')]
sys.path.insert(0, str(R / 'tk16'))
from tricky_pbd import PBD
from ps2_as_gc import ssf_to_gsf
from gamecube_scenery import instance_gameplay
from mapnames import map_names


def glass_entry(donor_dir, donor_stem, scenery_receipt, scenery_name, world, course):
    folder = Path(donor_dir)
    pbd_p = folder / (donor_stem + '.pbd')
    ssf_p = folder / (donor_stem + '.ssf')
    map_p = folder / (donor_stem + '.map')
    for p in (pbd_p, ssf_p, map_p, Path(scenery_receipt)):
        if not p.is_file():
            raise SystemExit(f'glass targets: missing input {p}')
    g = ssf_to_gsf(ssf_p.read_bytes())
    pbd = PBD(pbd_p.read_bytes())
    names = map_names(map_p.read_text(encoding='latin1'))['INTERNAL INSTANCES']
    rows = instance_gameplay(g, len(pbd.instances))
    effects, effect_table, functions, function_table, _, end = struct.unpack_from('>6I', g, 36)
    base = effect_table + effects * 8

    def prog(t, s, i):
        n, rel = struct.unpack_from('>II', g, t + s * i)
        pos = base + rel
        o = []
        for _ in range(n):
            op, size = struct.unpack_from('>II', g, pos)
            o.append((op, struct.unpack_from(f'>{(size - 8) // 4}I', g, pos + 8)))
            pos += size
        return o

    sc = json.loads(Path(scenery_receipt).read_text())
    world_of = {s: i for i, s in enumerate(sc['instance_source'])}
    items = []
    for f in range(functions):
        fname = g[function_table + 24 * f + 8:function_table + 24 * f + 24].split(b'\0')[0].decode('latin1')
        if not fname.startswith('Break'):
            continue
        hide, reveal, other = [], [], []
        for op, words in prog(function_table, 24, f):
            if op != 7:
                other.append((op, words))
                continue
            inst, eff = words
            body = prog(effect_table, 8, eff)
            if body == [(0, (5, 4))]:
                hide.append(inst)
            elif len(body) == 1 and body[0][0] == 0 and body[0][1][:2] == (20, 1):
                reveal.append(inst)
            else:
                other.append((inst, body))
        panes = [i for i in hide if rows[i]['visible'] and rows[i]['collision_mode'] == 2]
        item = dict(function=fname, panes=[(i, names.get(i)) for i in panes],
                    hide=[(i, names.get(i)) for i in hide],
                    reveal=[(i, names.get(i)) for i in reveal], other=other)
        if len(panes) == 1 and not other:
            pw = world_of.get(panes[0])
            scans = [world_of.get(i) for i in hide if i != panes[0]]
            item['spec'] = (f'{pw}:{",".join(str(s) for s in scans if s is not None)}'
                            if pw is not None else None)
            item['not_imported'] = [i for i in hide + reveal if world_of.get(i) is None]
        items.append(item)
    entry = dict(world=world, group=sc['group'], scenery=scenery_name, donor=donor_stem,
                 items=items, specs=[i['spec'] for i in items if i.get('spec')])
    print(course, world, 'group', sc['group'], 'Break fns', len(items), 'specs', entry['specs'],
          'skipped', [(i['function'], i['panes'], i['other'][:1])
                      for i in items if not i.get('spec')], flush=True)
    return entry


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--donor-dir', required=True,
                    help='donor stem dir holding <stem>.pbd/.ssf/.map')
    ap.add_argument('--donor-stem', required=True, help='donor stem (e.g. merquer)')
    ap.add_argument('--scenery-receipt', required=True, help='course scenery receipt JSON')
    ap.add_argument('--scenery-name', required=True, help='scenery receipt name (e.g. gari-sg)')
    ap.add_argument('--world', required=True, help='world name (e.g. GARI)')
    ap.add_argument('--course', required=True, help='chain course key (e.g. gari)')
    ap.add_argument('--out', required=True, help='output targets JSON path')
    a = ap.parse_args()
    out = Path(a.out)
    if out.exists():
        raise SystemExit(f'glass targets: refusing to overwrite {out}')
    out.parent.mkdir(parents=True, exist_ok=True)
    entry = glass_entry(a.donor_dir, a.donor_stem, a.scenery_receipt,
                        a.scenery_name, a.world, a.course)
    out.write_text(json.dumps({a.course: entry}, indent=1) + '\n')


if __name__ == '__main__':
    main()
