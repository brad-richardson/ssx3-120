#!/usr/bin/env python3
"""TK18 Part 2: decode a Tricky course's effect slots and effect programs (GC-layout .gsf from ps2_as_gc.py).

    tricky_effects.py COURSE.gsf COURSE.map COURSE.pbd OUT.json

Names follow SSX-Library SSFHandler.cs (GlitcherOG, read 2026-10-01): effect slot = 7 effect indices per slot
(1 constant, 2 collision, 3, 4, 5 effect-triggered, 6, 7); main types 0 (sub-ops below), 2 (0 ?, 1 SplinePathAnimation,
2 ?), 3, 4 wait, 5, 7 instance-effect call, 8 sound, 9, 13, 14 multiplier score, 17, 18, 21 run function,
24 teleport, 25 spline toggle.
"""
import collections, json, struct, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'shared')); sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tk13'))
from gamecube_scenery import instance_gameplay
from mapnames import map_names
from tricky_pbd import PBD

SUB0 = {0: 'Roller?', 2: 'Debounce', 5: 'DeadNode', 6: 'Counter', 7: 'Boost', 8: 'Timer', 9: 'Rail', 10: 'UVScroll',
        11: 'TexFlip', 12: 'Fence', 13: 'Flag', 14: 'Cracked', 15: 'LapBoost', 16: 'RandomBoost', 17: 'CrowdBox',
        18: 'ZBoost', 19: 'UVScrollTexFlip', 20: 'cMeshAnim', 21: 'TrickTrigger', 22: 'Particle', 23: 'Movie',
        24: 'TubeEndBoost', 256: 'AnimObject', 257: 'AnimDelta', 258: 'AnimCombo', 259: 'AnimTexFlip'}
MAIN = {3: 'Type3', 4: 'Wait', 5: 'Type5', 7: 'InstanceEffect', 8: 'Sound', 9: 'Type9', 13: 'Type13', 14: 'MultiplierScore',
        17: 'Type17', 18: 'Type18', 21: 'RunFunction', 24: 'Teleport', 25: 'SplineToggle'}
SUB2 = {0: 'Type2Sub0', 1: 'SplinePathAnimation', 2: 'Type2Sub2'}

gsf_p, map_p, pbd_p, out_p = sys.argv[1:5]
g = open(gsf_p, 'rb').read(); pbd = PBD(open(pbd_p, 'rb').read())
names = map_names(open(map_p, encoding='latin1').read())['INTERNAL INSTANCES']
rows = instance_gameplay(g, len(pbd.instances))
hdr = struct.unpack_from('>19I', g, 0)
slot_count, slot_off = hdr[3], hdr[4]
slots = [struct.unpack_from('>7i', g, slot_off + 28 * i) for i in range(slot_count)]
effects, effect_table, functions, function_table, _, end = struct.unpack_from('>6I', g, 36)
base = effect_table + effects * 8

def words(pos, n): return list(struct.unpack_from(f'>{n}I', g, pos))
def fl(w): return struct.unpack('>f', struct.pack('>I', w))[0]

def program(idx):
    count, rel = struct.unpack_from('>II', g, effect_table + 8 * idx); pos = base + rel; out = []
    for _ in range(count):
        op, size = struct.unpack_from('>II', g, pos); a = words(pos + 8, (size - 8) // 4); pos += size
        if op == 0: name = SUB0.get(a[0], f'sub0-{a[0]}')
        elif op == 2: name = SUB2.get(a[0], f'sub2-{a[0]}')
        else: name = MAIN.get(op, f'op{op}')
        rec = dict(op=op, name=name, args=a)
        if op == 4: rec['wait_s'] = round(fl(a[0]), 3)
        if op == 7: rec['instance'], rec['effect'] = a[0], a[1]; rec['instance_name'] = names.get(a[0], '?')
        if op == 24: rec['teleport_to'] = names.get(a[0], '?')
        if op == 0 and a[0] == 7: rec['boost'] = dict(mode=a[1], amount=round(fl(a[4]), 2), dir=[round(fl(x), 3) for x in a[5:8]])
        if op == 0 and a[0] == 18: rec['zboost'] = [round(fl(x), 3) if i in (1, 2, 5, 6, 7) else x for i, x in enumerate(a[1:])]
        out.append(rec)
    return out

progs = [program(i) for i in range(effects)]
users = collections.defaultdict(list)
for i, r in enumerate(rows):
    if r['effect_slot'] != -1: users[r['effect_slot']].append(i)
inst = []
for i, r in enumerate(rows):
    if r['effect_slot'] == -1: continue
    s = slots[r['effect_slot']]
    kinds = {f'slot{k + 1}': sorted({p['name'] for p in progs[e]}) for k, e in enumerate(s) if e != -1}
    inst.append(dict(instance=i, name=names.get(i, '?'), model=pbd.instances[i]['model'], visible=r['visible'],
                     collision_mode=r['collision_mode'], player_collision=r['player_collision'], effect_slot=r['effect_slot'],
                     slot_effects=list(s), kinds=kinds))
hist = collections.Counter(p['name'] for pr in progs for p in pr)
json.dump(dict(slot_count=slot_count, effects=effects, histogram=hist, instances=inst,
               programs={i: pr for i, pr in enumerate(progs)}), open(out_p, 'w'), indent=1)
print('slots', slot_count, 'effects', effects, 'instances with a slot', len(inst))
print(sorted(hist.items(), key=lambda kv: -kv[1]))
