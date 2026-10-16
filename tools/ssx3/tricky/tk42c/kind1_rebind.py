#!/usr/bin/env python3
"""TK24: bind Tricky slot-1 constant effects to SSX 3 class programs in a finished Tricky world (post-TK19c), in place.

    bind_effects.py WORLD.BIG SCENERY.json EFFECTS.json OUT.BIG [--types UVScroll] [--names PREFIX ...] [--scale S]
                    [--receipt R.json]

WORLD.BIG     a replace-world (e.g. TK19c/w/gari-c/GARI.BIG) whose scenery group was written by make_scenery16/21
SCENERY.json  that build's receipt (instance_source: world instance k -> donor instance index; group)
EFFECTS.json  TK18 tricky_effects.py output for the same donor (TK24a/effects/<course>.json)

UVScroll (Tricky op 0, args [10, mode, u, v, 1.0, ...]) -> SSX 3 builtin 21 UVScrollModifier, shaped like the stock
class programs (BAM.BIG ARA1 p41 / ABA1 p26): CALLBUILTIN 2,0,0 (Object); PUSHF key4 u; PUSHF key5 v;
CALLBUILTIN 2,21,2; RETNIL; one function record (back, 0, 3, argc). Key 4 = u, key 5 = v [read: stock programs push
key 4 then key 5; ARA1 p47 negates 0.1 into key 4]. Units assumed equal to Tricky's (stock 0.004/0.006/0.1 vs
Tricky 0.0042/0.05) -- `--scale` multiplies both.

Each distinct (u, v) gets one stub slot (first STUB slots >= 64 B from slot 3, TK21 rule). Each target instance gets
its current definition with word 2 (class handle) = slot << 8 | track; definitions are deduplicated and appended.

TK42 adds --types Flag: all Flag targets share one class program, the stock EBC3 slot-14 Flag program
byte-verbatim (FLAG_EBC3_P14, 112 B; first STUB slot >= 112 B). Tricky Flag values are recorded, not mapped.
"""
import argparse, io, json, struct, sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
R = HERE.parent
sys.path.insert(0, str(R /'tk12')); sys.path.insert(0, str(R / 'shared'))
from wlib import W, sha
from probe_worlds import probe_ssx3, resource_records
from relayout_stream import assemble_archive
from inspect_disc import Region
import gamecube_game_dir as gdir
gdir.WORLD_EXTENSIONS = ("sdb", "ssb", "phm", "psm")

STUB = bytes.fromhex('4c554e001400000024000000240000002aff0000ecffffff000000000200000000000000')
f32 = lambda x: struct.unpack('<f', struct.pack('<I', x))[0]


def uvscroll_program(u, v):
    code = bytes([33, 2, 0, 0])
    code += bytes([38, 4, 0, 0]) + struct.pack('<f', u) + bytes([38, 5, 0, 0]) + struct.pack('<f', v)
    code += bytes([33, 2, 21, 2]) + bytes([42, 0xff, 0, 0])
    code_end = 16 + len(code); data_end = code_end + 16
    return struct.pack('<4I', 0x4e554c, code_end, data_end, data_end) + code + struct.pack('<iIII', -code_end, 0, 3, 2)


# TK42: stock EBC3 slot-14 Flag class program, byte-verbatim from local/source/ssx3/BAM.BIG
# (112 B, 19 ops, pushes only + CALLBUILTIN 2,12,17; the only push-only Flag program in
# stock; see receipts/flag-ebc3-p14.hex). Tricky Flag arg values are NOT mapped (key
# semantics unknown); every bound flag runs these verbatim stock parameters.
FLAG_EBC3_P14 = bytes.fromhex(
    '4c554e00600000007000000070000000'
    '2904080028051e00281300002814000028150000281000002811000028120000'
    '261600006666663f28010000280201002907000028080000290a0200280b0500'
    '290d0000280e000021020c112aff0000a0ffffff000000000300000011000000')


def flag_program():
    return FLAG_EBC3_P14


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('world'); ap.add_argument('scenery'); ap.add_argument('effects'); ap.add_argument('out')
    ap.add_argument('--types', nargs='+', default=['UVScroll'])
    ap.add_argument('--names', nargs='*', default=None, help='only instances whose donor name starts with one of these')
    ap.add_argument('--scale', type=float, default=1.0)
    ap.add_argument('--receipt')
    ap.add_argument('--kind0-as-trigger', action='store_true', help='rewrite kind-0 (static) targets as drawn kind-2 triggers (TK21 pad shape)')
    ap.add_argument('--kind1', action='store_true', help='TK42c: rewrite target as non-solid kind-1 (1,0x210000,slot,-1,1e30,0,3,-1)')
    ap.add_argument('--donors', nargs='*', type=int, default=None, help='TK42c: only these donor instances')
    ap.add_argument('--reuse-slot', type=int, default=None, help='TK42c: reuse program slot S instead of writing a new one')
    a = ap.parse_args()
    if a.types not in (['UVScroll'], ['Flag']):
        raise SystemExit('only UVScroll or Flag is implemented')
    is_flag = a.types == ['Flag']
    sc = json.loads(Path(a.scenery).read_text()); ef = json.loads(Path(a.effects).read_text())
    G = sc['group']; src = sc['instance_source']
    progs = ef['programs']
    want = {}  # donor instance -> (params, name)
    for i in ef['instances']:
        if a.types[0] not in i['kinds'].get('slot1', []): continue
        if a.names is not None and not i['name'].startswith(tuple(a.names)): continue
        if a.donors is not None and i['instance'] not in a.donors: continue
        ops = [o for o in progs[str(i['slot_effects'][0])] if o['name'] == a.types[0]]
        args = ops[0]['args']
        if is_flag:
            want[i['instance']] = (tuple(args), i['name'])
        else:
            want[i['instance']] = ((round(f32(args[2]) * a.scale, 6), round(f32(args[3]) * a.scale, 6)), i['name'])

    base_bytes = Path(a.world).read_bytes()
    stem = W(a.world).stem
    bam_bytes = gdir.rename_world_members(base_bytes, 'bam')
    tmp = Path(a.out).with_suffix('.bam.tmp'); tmp.parent.mkdir(parents=True, exist_ok=True); tmp.write_bytes(bam_bytes)
    world = W(tmp)
    recs = world.records(G)
    (ki, (se, script)), = [(n, r) for n, r in enumerate(recs) if r[0]['kind'] == 16]
    T = se['track']
    data = bytearray(script)
    pc, ptab, pend = struct.unpack_from('<3I', data, 56)
    st = struct.unpack_from(f'<{pc}I', data, ptab) + (pend,)
    base = struct.unpack_from('<I', data, 64)[0]
    nb, bat, nd, dat, nspl, spl_at = struct.unpack_from('<6I', data, 68)
    bindings = list(struct.unpack_from(f'<{nb}H', data, bat))
    doffs = struct.unpack_from(f'<{nd}I', data, dat)
    defs = [bytes(data[base + o:base + o + 28]) for o in doffs]
    assert all(b - a == 28 for a, b in zip(doffs, doffs[1:])) and bat - base - doffs[-1] == 28
    tail = bytes(data[spl_at:])  # splines + whatever follows
    assert nb == len(src), (nb, len(src))

    pairs = sorted({params for params, _ in want.values()})
    min_stub = 112 if is_flag else 64
    free = [k for k in range(3, pc) if st[k + 1] - st[k] >= min_stub and bytes(data[st[k]:st[k] + 36]) == STUB]
    if a.reuse_slot is None and len(pairs) > len(free): raise SystemExit('not enough stub slots')
    slot_of = {}
    prog_of = {}
    for params, slot in zip(pairs, free):
        if a.reuse_slot is not None:
            break
        blob = flag_program() if is_flag else uvscroll_program(*params)
        if len(blob) > st[slot + 1] - st[slot]: raise SystemExit(f'slot {slot} too small')
        data[st[slot]:st[slot + 1]] = blob + bytes(st[slot + 1] - st[slot] - len(blob))
        slot_of[params] = slot
        prog_of[params] = blob
    def_ids = {d: n for n, d in enumerate(defs)}
    bound = []
    old_classes = Counter()
    for k, donor in enumerate(src):
        if donor not in want: continue
        params, name = want[donor]
        d = bytearray(defs[bindings[k]])
        old_classes[hex(struct.unpack_from('<I', d, 8)[0])] += 1
        eff_slot = a.reuse_slot if a.reuse_slot is not None else slot_of[params]
        struct.pack_into('<I', d, 8, eff_slot << 8 | T)
        if d[0] == 0 and a.kind0_as_trigger:  # kind 0 (static, no collision) never runs a class; use TK21's drawn kind-2 trigger shape
            d = bytearray(struct.pack('<4I', 2, 0x00210000, slot_of[params] << 8 | T, 0xffffffff) + struct.pack('<2f2h', 0., 0., 2, -1))
        if a.kind1:  # TK42c: non-solid kind-1 (stock kind-1 part + no collision handle)
            d = bytearray(struct.pack('<4I', 1, 0x00210000, eff_slot << 8 | T, 0xffffffff) + struct.pack('<2f2h', 1e30, 0., 3, -1))
        d = bytes(d)
        if d not in def_ids: def_ids[d] = len(defs); defs.append(d)
        bindings[k] = def_ids[d]
        entry = dict(world_instance=k, donor=donor, name=name,
                     slot=(a.reuse_slot if a.reuse_slot is not None else slot_of[params]), kind=d[0])
        if is_flag:
            entry['args'] = list(params)
        else:
            entry['u'], entry['v'] = params
        bound.append(entry)

    def layout(head, defs, bindings):
        out = bytearray(head[:base]); offs = []
        for d in defs:
            offs.append(len(out) - base); out += d
        inst_at = len(out); out += struct.pack(f'<{len(bindings)}H', *bindings); out += bytes(-len(out) % 4)
        def_at = len(out); out += struct.pack(f'<{len(offs)}I', *offs)
        new_spl = len(out); out += tail
        struct.pack_into('<6I', out, 68, len(bindings), inst_at, len(defs), def_at, nspl, new_spl)
        return out
    if bytes(layout(script, defs[:nd], list(struct.unpack_from(f'<{nb}H', script, bat)))) != bytes(script):
        raise SystemExit('identity re-layout does not reproduce the kind-16 record')
    out = layout(data, defs, bindings)
    recs[ki] = (dict(se, size=len(out)), bytes(out))
    raw = b''.join(bytes([e['kind']]) + len(p).to_bytes(3, 'little') + bytes([e['track']]) + e['rid'].to_bytes(3, 'little') + p
                   for e, p in recs)
    groups = world.sdb['groups']
    raws = {g['index']: world.raw(g['index']) for g in groups}; raws[G] = raw

    sdb = bytearray(world.sdb_bytes)
    lc, nodes, _ = struct.unpack_from('<III', sdb, 8)
    gtab = (80 + 88 * lc + 15) // 16 * 16 + nodes * 96
    o = gtab + 68 * G
    struct.pack_into('<H', sdb, o, len(recs))
    struct.pack_into('<I', sdb, o + 8, sum(len(p) + 8 for e, p in recs if e['kind'] <= 12))
    c = Counter(e['kind'] for e, p in recs)
    for k in range(14):
        struct.pack_into('<H', sdb, o + 12 + 2 * k, c.get(k, 0))
    report = probe_ssx3(Region(io.BytesIO(bam_bytes), 0, len(bam_bytes)))
    bam, layout, _ = assemble_archive(bam_bytes, report, raws, bytes(sdb), jobs=6, reuse_original_blocks=True)
    res = gdir.rename_world_members(bam, stem)
    Path(a.out).write_bytes(res); tmp.unlink()
    back = W(a.out)
    ok = all(back.raw(g['index']) == raws[g['index']] for g in groups)
    rc = dict(world=a.world, world_sha256=sha(base_bytes), out=a.out, out_sha256=sha(res), out_bytes=len(res), readback=ok,
              group=G, track=T, kind16_bytes=[len(script), len(out)], definitions=[nd, len(defs)],
              classes={','.join(map(str, params)): dict(slot=s, class_word=hex(s << 8 | T), program=prog_of[params].hex()) for params, s in slot_of.items()},
              old_classes=dict(old_classes), bound=len(bound), by_name=dict(Counter(b['name'].rsplit('_', 1)[0] for b in bound)),
              instances=bound)
    if a.receipt: Path(a.receipt).write_text(json.dumps(rc, indent=1))
    print(json.dumps({k: v for k, v in rc.items() if k != 'instances'}, indent=1))


if __name__ == '__main__':
    main()
