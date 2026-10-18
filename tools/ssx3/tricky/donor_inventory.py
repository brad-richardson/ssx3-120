#!/usr/bin/env python3
"""Read original donor effect bindings twice; lane-local extraction only."""
import argparse, hashlib, json, struct, sys
from pathlib import Path
sys.dont_write_bytecode = True
T = Path(__file__).resolve().parent
sys.path[:0] = [str(T / 'shared'), str(T / 'tk13'), str(T / 'tk30')]
# Port of TK52 tooling/donor_inventory.py (port adaptations only: import
# re-root; reads the user's own Tricky ISO, no behaviour edits).
from inspect_disc import Region, iso_files, big_members
from inventory_tricky import decode_member
from tricky_pbd import PBD
from mapnames import map_names
from gamecube_scenery import instance_gameplay
from ps2_as_gc30 import ssf_to_gsf

sha = lambda b: hashlib.sha256(b).hexdigest()
p = argparse.ArgumentParser()
p.add_argument('--iso', required=True)
p.add_argument('--out', required=True)
p.add_argument('--receipt', required=True)
a = p.parse_args()
out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
receipt = {}
with Path(a.iso).open('rb') as f:
    iso = Region(f, 0, Path(a.iso).stat().st_size)
    _, files = iso_files(iso)
    for course in ('pipe', 'untrack', 'alaska'):
        e = next(e for e in files if e['path'] == 'DATA/MODELS/'+course.upper()+'.BIG')
        reg = iso.child(e['offset'], e['size']); _, members = big_members(reg)
        blobs = {}; inputs = []
        d = out/course; d.mkdir(exist_ok=True)
        for ext in ('pbd', 'map', 'ssf', 'aip'):
            m = next(m for m in members if m['path'] == 'data/models/'+course+'.'+ext)
            b, _ = decode_member(reg, m); b2, _ = decode_member(reg, m)
            assert b == b2
            dest = d/(course+'.'+ext); dest.write_bytes(b)
            assert dest.read_bytes() == dest.read_bytes() == b
            blobs[ext] = b
            inputs.append(dict(member=m['path'], sha256_read1=sha(b), sha256_read2=sha(b2)))
        pb = PBD(blobs['pbd']); g = ssf_to_gsf(blobs['ssf'])
        (d/(course+'.gsf')).write_bytes(g)
        names = map_names(blobs['map'].decode('latin1'))['INTERNAL INSTANCES']
        rows = instance_gameplay(g, len(pb.instances)); h = struct.unpack_from('>19I', g)
        slots = [struct.unpack_from('>7i', g, h[4]+28*i) for i in range(h[3])]
        effects, table = h[9:11]; base = table+8*effects
        programs = {}
        for i in range(effects):
            count, rel = struct.unpack_from('>2I', g, table+8*i); pos = base+rel; ops=[]
            for _ in range(count):
                op, size = struct.unpack_from('>2I', g, pos)
                args = list(struct.unpack_from('>'+str((size-8)//4)+'I', g, pos+8))
                if op in (14, 17, 18): ops.append(dict(op=op, args=args))
                pos += size
            if ops: programs[i] = ops
        users = []
        for i, row in enumerate(rows):
            slot = row['effect_slot']
            if slot < 0: continue
            target = [j for j in slots[slot] if j in programs]
            if target:
                users.append(dict(donor_instance=i, name=names.get(i, '?'), gameplay=row,
                                  programs={j: programs[j] for j in target},
                                  model=pb.instances[i]['model'], matrix=pb.instances[i].get('matrix')))
        receipt[course] = dict(archive=e, inputs=inputs, instance_count=len(rows),
                               relevant_programs=programs, relevant_users=users,
                               relevant_slots={i:list(s) for i,s in enumerate(slots) if any(j in programs for j in s)})
Path(a.receipt).write_text(json.dumps(receipt, indent=2)+'\n')
for c, x in receipt.items():
    print(c, 'instances', x['instance_count'], 'users', len(x['relevant_users']))
