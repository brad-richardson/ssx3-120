#!/usr/bin/env python3
"""TK51 hidden-collider census, one course (port of the TK51 lane census).

Reads the donor <stem>.pbd/.ssf/.map from the run workdir (extracted from
the user's Tricky disc) and writes the single course census row as a
one-element list, the same row schema the tracked census file carries.
The driver generates this into the workdir at build time; nothing
game-derived is tracked in git.

Usage:
  make_hidden_census.py --donor-dir <donor/<stem>/> --stem <stem>
      --out <census.json>
"""
import argparse
import collections
import hashlib
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
R = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(R / 'tk13'), str(R / 'shared')]
sys.path.insert(0, str(R / 'tk16'))
from tricky_pbd import PBD
from ps2_as_gc import ssf_to_gsf
from gamecube_scenery import instance_gameplay, post_countdown_hidden
from gamecube_collision import read_tricky_collision
from mapnames import map_names


def census_row(donor_dir, stem):
    folder = Path(donor_dir)
    pbd_p = folder / (stem + '.pbd')
    ssf_p = folder / (stem + '.ssf')
    map_p = folder / (stem + '.map')
    for p in (pbd_p, ssf_p, map_p):
        if not p.is_file():
            raise SystemExit(f'hidden census: missing donor input {p}')
    p = PBD(pbd_p.read_bytes())
    raw = ssf_p.read_bytes()
    gs = ssf_to_gsf(raw)
    gp = instance_gameplay(gs, len(p.instances))
    cols = read_tricky_collision(gs)
    note = None
    try:
        hidden = set(post_countdown_hidden(gs, len(gp)))
    except ValueError as e:
        if str(e) != 'Static race profile requires one authored NoCountDown function':
            raise
        hidden = set()
        note = 'No authored NoCountDown function; census uses initial visibility'
    names = map_names(map_p.read_text(encoding='latin1'))['INTERNAL INSTANCES']
    selected = []
    for i, g in enumerate(gp):
        if g['player_collision'] and (not g['visible'] or i in hidden):
            parts = cols[g['collision_or_physics']] if g['collision_mode'] == 1 else []
            selected.append(dict(source=i, name=names.get(i), initially_visible=g['visible'],
                                  candidate_include=not g['visible'],
                                  hidden_after_countdown=i in hidden, gameplay=g,
                                  parts=[dict(vertices=len(m['vertices']), faces=len(m['triangles']))
                                         for m in parts]))
    for row in selected:
        g = row['gameplay']
        objs = p.models[p.instances[row['source']]['model']]['objects']
        flags = [ob.get('flags', 0) for ob in objs]
        row['authored_node_flags'] = flags
        row['solid_proxy_candidate'] = (row['candidate_include'] and g['collision_mode'] == 1
                                        and g['immovable'] and g['effect_slot'] == -1
                                        and bool(g['flags'] & 128)
                                        and g['surface'] not in (0, 13, 18))
        row['classification'] = ('eligible authored physical proxy'
                                 if row['solid_proxy_candidate'] else
                                 'exclude from solid import: soft/effect helper, hazard surface '
                                 'or no authored static-physical flag128, or countdown-only')
    row = dict(course=stem, ssf_sha256=hashlib.sha256(raw).hexdigest(), instances=len(gp),
               invisible_player_collidable=len(selected),
               initially_invisible=sum(not r['initially_visible'] for r in selected),
               visibility_note=note,
               surface_counts=dict(collections.Counter(str(r['gameplay']['surface'])
                                                       for r in selected)),
               mode_counts=dict(collections.Counter(str(r['gameplay']['collision_mode'])
                                                    for r in selected)),
               hidden=selected, candidate_scope=stem != 'megaple')
    eligible = [r for r in selected if r['solid_proxy_candidate']]
    row.update(solid_proxy_count=len(eligible),
               non_solid_hidden_count=row['initially_invisible'] - len(eligible),
               solid_proxy_surface_counts=dict(collections.Counter(str(r['gameplay']['surface'])
                                                                   for r in eligible)))
    print(stem, len(selected), row['surface_counts'], row['mode_counts'], flush=True)
    return row


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--donor-dir', required=True,
                    help='donor stem dir holding <stem>.pbd/.ssf/.map')
    ap.add_argument('--stem', required=True, help='donor stem (e.g. merquer)')
    ap.add_argument('--out', required=True, help='output census JSON path')
    a = ap.parse_args()
    out = Path(a.out)
    if out.exists():
        raise SystemExit(f'hidden census: refusing to overwrite {out}')
    out.parent.mkdir(parents=True, exist_ok=True)
    # Tracked-file framing (indent=1, no trailing newline): rows generated
    # from the same discs assemble byte-identically to the retired file.
    out.write_text(json.dumps([census_row(a.donor_dir, a.stem)], indent=1))


if __name__ == '__main__':
    main()
