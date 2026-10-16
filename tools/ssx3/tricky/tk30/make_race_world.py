#!/usr/bin/env python3
"""TK12: a Tricky replace-world with the donor RACE course (+ optional host-instance hiding), as <NAME>.BIG.

    make_race_world.py BASE.BIG STOCK_BAM.BIG DONOR.AIP EXPERIMENT.JSON STEM OUT.BIG [--hide LOC ...] [--dz 1000000]
                       [--relocate-start-only] [--checkpoints 2]

BASE.BIG is the replace_terrain.py output (its terrain group holds the donor patches and the freeride-start
AIP). The terrain group is found from EXPERIMENT.JSON (`group`).

Fix R (race course; TK9/TK10 used relocate_race_starts, which moves the six gates and gives only the
gate track path the donor's FIRST race segment, leaving the rest of the chain, the checkpoints, the
finish event and the kind-21 race line on the host course):
  1. control: course_route.make_reset_aip(donor, m, t, s, stock AIP, relocate_start=True) must equal
     BASE's AIP byte for byte (the recipe BASE was built with);
  2. the same call with race_course=... (race_course.convert_race_course, the GameCube-route decode):
     the whole gate track chain carries the donor race chain with checkpoint (18) and finish (0)
     events, the gates move onto the donor start paths and each gate's AI slot gets that donor start
     path (the AI racing line);
  3. kind 21 (the race line the progress meter and race logic read) is regenerated from the same chain.
     race_line_table packs big-endian (GameCube); the PS2 record is the same words little-endian
     (control in REPORT: stock Snow Jam regenerates to the same 6536 B / 325 nodes, last-ulp float
     differences only), so every 4-byte word is swapped.
Fix H (--hide LOC): every kind-3 instance of location LOC moves down by DZ (translation, bounds centre,
  bounds low/high z at +72/+88/+104/+116). Nothing is removed, so no reference changes. For the host
  connector next to the relocated start (ARA1's A_ARA1: start house, huts, trees, 2-12k from the gate).
Groups that change are re-chunked (relayout_stream.assemble_archive, reuse_original_blocks); world members
are renamed data/worlds/<stem>.*. Prints a JSON receipt.
"""
import argparse, json, struct, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] /'tk12'))  # TK18 lane copy: + --race-line-kind/--markers
from wlib import W, sha, ROOT
from probe_worlds import probe_ssx3, resource_records
from relayout_stream import assemble_archive
from inspect_disc import Region
from course_route import make_reset_aip
from retrack import decode_group
import gamecube_game_dir as gdir
import io

gdir.WORLD_EXTENSIONS = ('sdb', 'ssb', 'phm', 'psm')


def swap_words(b):
    if len(b) % 4:
        raise SystemExit('kind-21 table not word aligned')
    return b''.join(b[i:i + 4][::-1] for i in range(0, len(b), 4))


def replace_payload(raw, kind, rid, payload):
    out, found = bytearray(), 0
    for e, p in resource_records(raw):
        hdr = bytearray(raw[e['offset']:e['offset'] + 8])
        if e['kind'] == kind and e['rid'] == rid and p:
            hdr[1:4] = len(payload).to_bytes(3, 'little'); p = payload; found += 1
        out += hdr + p
    if found != 1:
        raise SystemExit(f'expected one kind {kind} rid {rid}, found {found}')
    return bytes(out)


def main():
    ap = argparse.ArgumentParser()
    for a in ('base', 'stock', 'donor', 'experiment', 'stem', 'out'):
        ap.add_argument(a)
    ap.add_argument('--hide', action='append', default=[])
    ap.add_argument('--dz', type=float, default=1000000.0)
    ap.add_argument('--relocate-start-only', action='store_true', help='control: skip fix R')
    ap.add_argument('--checkpoints', type=int, default=2)
    ap.add_argument('--chain-tolerance', type=float, help='TK18: donor race-chain endpoint tolerance (default 1000; Alaska race 6->7 gap is 1299)')
    ap.add_argument('--race-line-kind', default='race', choices=['race', 'slopestyle'])
    ap.add_argument('--markers', default='', help='slopestyle: the two type-1 marker fractions (host stock kind-21), comma list')
    ap.add_argument('--base-race-starts', action='store_true',
                    help='BASE was built with relocate_race_starts=True (TK10 build_world.py); used for the control only')
    ap.add_argument('--keep-reset', type=int, default=0,
                    help='TK10_KEEP_RESET=N: the N longest donor reset paths only (ABC1 has 48 slots)')
    ap.add_argument('--gate-chain', help='explicit host gate track chain (comma list), computed on the STOCK AIP; '
                    'needed when the freeride start shares the gate track (ABC1: relocate_start rewrites track 0 first)')
    a = ap.parse_args()
    if a.chain_tolerance:
        import race_course, functools
        race_course.donor_race_chain = functools.partial(race_course.donor_race_chain, tolerance=a.chain_tolerance)
    if a.gate_chain:
        import course_route, functools
        chain = [int(v) for v in a.gate_chain.split(',')]
        course_route.convert_race_course = functools.partial(course_route.convert_race_course, gate_track_chain=chain)
    if a.keep_reset:
        import course_route
        sys.path.insert(0, str(ROOT / 'tk10'))
        from build_world import ai_paths_keeping
        course_route.ai_paths = ai_paths_keeping(a.keep_reset)
        globals()['make_reset_aip'] = course_route.make_reset_aip
    base_bytes = Path(a.base).read_bytes()
    base = W(a.base)
    stock = W(a.stock)
    exp = json.loads(Path(a.experiment).read_text())
    G = exp['group']
    donor = Path(a.donor).read_bytes()
    m, t, sc = exp['matrix'], exp['translation'], exp['scale']
    raws = {g['index']: base.raw(g['index']) for g in base.sdb['groups']}
    aip = [p for e, p in resource_records(raws[G]) if e['kind'] == 14 and e['rid'] == 0 and p][0]
    stock_aip = [p for e, p in stock.records(G) if e['kind'] == 14 and e['rid'] == 0 and p][0]
    control, _ = make_reset_aip(donor, m, t, sc, stock_aip, relocate_start=True, relocate_race_starts=a.base_race_starts)
    if control != aip:
        raise SystemExit(f'control failed: {sha(control)} != base AIP {sha(aip)}')
    rec = dict(gate_chain=a.gate_chain, control_recipe=dict(relocate_start=True, relocate_race_starts=a.base_race_starts, keep_reset=a.keep_reset), base=a.base, base_sha256=sha(base_bytes), donor_sha256=sha(donor), group=G,
               control_reproduces_base_aip=True)
    changed = set()
    if not a.relocate_start_only:
        race = dict(checkpoint_limit=a.checkpoints, race_line_kind=a.race_line_kind, marker_fractions=tuple(float(v) for v in a.markers.split(',') if v))
        new_aip, info = make_reset_aip(donor, m, t, sc, stock_aip, relocate_start=True, race_course=race)
        table = swap_words(race['table'])
        old21 = [p for e, p in resource_records(raws[G]) if e['kind'] == 21][0]
        raws[G] = replace_payload(replace_payload(raws[G], 14, 0, new_aip), 21, 0, table)
        changed.add(G)
        rs = info['race_starts'] if info.get('race_starts') else None
        rec['race'] = dict(aip_bytes=[len(aip), len(new_aip)], kind21_bytes=[len(old21), len(table)],
                           kind21_nodes=struct.unpack_from('<I', table)[0],
                           kind21_total=struct.unpack_from('<f', table, len(table) - 4 - 8 * 2)[0],
                           report={k: v for k, v in rs.items() if k != 'gates'} if rs else None,
                           gates=[dict(start_record=g['start_record'], ai_path=g['ai_path'], donor_start_path=g['donor_start_path'],
                                       position=[round(v, 1) for v in g['position']]) for g in rs['gates']] if rs else None,
                           replaced_ai_slots=info['replaced_path_indices'][:12])
    hidden = {}
    for name in a.hide:
        loc = base.loc(name)
        n = 0
        for g in range(loc['group_start'], loc['last_group'] + 1):
            raw = bytearray(raws[g])
            for e, p in resource_records(bytes(raw)):
                if e['kind'] != 3:
                    continue
                o = e['offset'] + 8
                for off in (72, 88, 104, 116):
                    v = struct.unpack_from('<f', raw, o + off)[0]
                    struct.pack_into('<f', raw, o + off, v - a.dz)
                n += 1
            if n:
                raws[g] = bytes(raw); changed.add(g)
        hidden[name] = n
    rec['hidden_instances'] = hidden
    report = probe_ssx3(Region(io.BytesIO(base_bytes), 0, len(base_bytes)))
    bam, layout, _ = assemble_archive(base_bytes, report, raws, base.sdb_bytes, jobs=6, reuse_original_blocks=True)
    out = gdir.rename_world_members(bam, a.stem) if a.stem != 'bam' else bam
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_bytes(out)
    back = W(a.out)
    rec['all_groups_decode'] = all(back.raw(g['index']) == raws[g['index']] for g in base.sdb['groups'])
    rec['groups_changed'] = sorted(changed)
    rec['out'] = a.out; rec['out_sha256'] = sha(Path(a.out).read_bytes()); rec['out_bytes'] = len(out)
    print(json.dumps(rec, indent=1, default=str))


if __name__ == '__main__':
    main()
