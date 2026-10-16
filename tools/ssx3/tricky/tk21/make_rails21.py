#!/usr/bin/env python3
"""TK16 (copy of TK13's, temp dir only; TK13 from TK12, unchanged except the temp file): import the donor course's rails into a PS2 replace-world (item 3), as <STEM>.BIG.

    make_rails.py BASE.BIG EXPERIMENT.JSON DONOR.nbd DONOR.gsf STEM OUT.BIG

Mechanism: every replace-world so far keeps the HOST location's kind-8 splines (rails/paths at the host
course's positions) and no donor rails. The GameCube route already imports donor rails
(tools/gamecube_spline_import.py + gamecube_splines.encode_spline: Tricky GC NBD/GSF -> SSX 3 GC kind 8,
big-endian, 12-byte segment prefix). The PS2 kind-8 record is the same data little-endian with a shifted
segment layout (decoded on stock ARA1, REPORT §rails):

    header 48 B: +0 handle (rid<<8 | track), +4 low xyz, +16 high xyz, +28 0, +32 segment count,
                 +36 host constant (copied), +40 0xFFFFFFFF, +44 0                 (GC: oid = track<<24|rid)
    segment 144 B: +0 three host constant words (copied from the host template; first segment differs),
                 +12 length, +16 coefficient rows a,b,c,d as xyzw (d.w = 1), +80 inverse-distance cubic,
                 +96 previous / +100 next as GLOBAL segment indices in record order (NULL at the ends),
                 +104 parent spline rid, +108 low xyz, +120 high xyz, +132 previous distance,
                 +136 8-byte trailer (host constant, copied)
    GC for comparison: +12 rows, +76 inverse, +92 prev/+96 next (per-spline indices), +100 oid, +104 bounds,
                 +128 length, +132 previous distance, +136 trailer.

The donor geometry comes from the GameCube Tricky files (the same course data: the PS2 and GC race lines
are byte-identical after placement, `routes/gari.txt`), read and fitted by gamecube_splines (strict reader,
coefficient transform, inverse-distance rescale by (100*scale)^(3-k)); only the byte layout is PS2's.
The course-script spline binding table (kind 16: count at +84, table at its end, u16 pairs) is rewritten to one
entry per donor rail with the host's rail binding 0x0003/0x000a (as the GC importer's diagnostic profile).
The host's course programs must already be disabled (replace_terrain --disable-course-scripts).
Readback: every emitted curve is sampled against the transformed donor curve (max error printed).
"""
import io, itertools, json, math, struct, sys
from pathlib import Path
_R = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(_R /'tk16')); sys.path.insert(0, str(_R /'tk12')); sys.path.insert(0, str(_R /'tk13'))
from wlib import W, sha
from probe_worlds import probe_ssx3, resource_records
from relayout_stream import assemble_archive
from inspect_disc import Region
from import_terrain import apply
from gamecube_splines import read_tricky_splines, similarity_scale, curve_bounds, curve_point, NULL
import gamecube_game_dir as gdir

gdir.WORLD_EXTENSIONS = ('sdb', 'ssb', 'phm', 'psm')
RAIL_BINDING = bytes.fromhex('03000a00')


def encode_ps2(spline, rid, track, first_global, matrix, translation, hdr_template, seg_first_prefix, seg_prefix, trailer):
    scale = similarity_scale(matrix, translation)
    chain = spline['segments']
    data = bytearray(48 + 144 * len(chain))
    lows, highs = [], []
    for j, seg in enumerate(chain):
        o = 48 + 144 * j
        rows = [apply(matrix, translation if k == 3 else [0, 0, 0], c[:3]) + [c[3]] for k, c in enumerate(seg['coefficients'])]
        struct.pack_into('<16f', data, o + 16, *(x for r in rows for x in r))
        actual = struct.unpack_from('<16f', data, o + 16)
        low, high = curve_bounds([actual[k:k + 4] for k in range(0, 16, 4)])
        lows.append(low); highs.append(high)
        inverse = [v / (100 * scale) ** (3 - k) for k, v in enumerate(seg['inverse_distance'])]
        closed = spline['closed']
        prev = first_global + (j - 1) if j else (first_global + len(chain) - 1 if closed else NULL)
        nxt = first_global + j + 1 if j + 1 < len(chain) else (first_global if closed else NULL)
        data[o:o + 12] = seg_first_prefix if j == 0 else seg_prefix
        struct.pack_into('<f', data, o + 12, seg['length'] * scale)
        struct.pack_into('<4f3I6ff', data, o + 80, *inverse, prev, nxt, rid, *low, *high, seg['previous_distance'] * scale)
        data[o + 136:o + 144] = trailer
    low = [min(v[k] for v in lows) for k in range(3)]
    high = [max(v[k] for v in highs) for k in range(3)]
    struct.pack_into('<I6f2I', data, 0, rid << 8 | track, *low, *high, 0, len(chain))
    data[36:48] = hdr_template[36:48]
    return bytes(data)


def main():
    base_p, exp_p, nbd_p, gsf_p, stem, out_p = sys.argv[1:7]  # TK21: optional argv[7] = template rails.json
    base_bytes = Path(base_p).read_bytes()
    tmp = None
    if W(base_p).stem != 'bam':  # probe_ssx3/assemble_archive read data/worlds/bam.*: rename in a scratch copy
        tmp = str(Path.home() / 'dev/ssx3-work/TK21/tmp' / (Path(out_p).parent.name + '-rails-bam.BIG'))  # left in place; no deletes
        base_bytes = gdir.rename_world_members(base_bytes, 'bam')
        Path(tmp).write_bytes(base_bytes)
    base = W(tmp or base_p)
    exp = json.loads(Path(exp_p).read_text())
    G, m, t = exp['group'], exp['matrix'], exp['translation']
    rails = read_tricky_splines(Path(nbd_p).read_bytes(), Path(gsf_p).read_bytes())
    raw = base.raw(G)
    recs = list(resource_records(raw))
    old = [(e, p) for e, p in recs if e['kind'] == 8]
    track = old[0][0]['track']
    if sorted(e['rid'] for e, _ in old) != list(range(len(old))) or any(e['track'] != track for e, _ in old):
        raise SystemExit('expected a dense host kind-8 table on one track')
    scripts = [(e, p) for e, p in recs if e['kind'] == 16]
    if len(scripts) != 1:
        raise SystemExit('expected one kind-16 record')
    script = scripts[0][1]
    count, table = struct.unpack_from('<II', script, 84)
    if count != len(old) or table + 4 * count != len(script):
        raise SystemExit('unsupported host spline binding table extent')
    # host templates: a rail-bound host spline with >= 2 segments
    bindings = [script[table + 4 * i: table + 4 * i + 4] for i in range(count)]
    tmpl = next((p for (e, p), b in zip(old, bindings) if b == RAIL_BINDING and struct.unpack_from('<I', p, 32)[0] >= 2), None)
    from collections import Counter
    borrowed = None
    if tmpl is not None:
        hdr_t, seg_prefix, trailer = tmpl[:48], tmpl[48 + 144:48 + 144 + 12], tmpl[48 + 136:48 + 144]
        # first-segment prefix: the majority value among rail-bound host splines (rid 0 alone carries 0x55879b)
        seg_first_prefix = Counter(p[48:60] for (e, p), b in zip(old, bindings) if b == RAIL_BINDING).most_common(1)[0][0]
    elif len(sys.argv) > 7:
        # TK21: the host has no rail-bound spline (DBC2): borrow another host's rail-class constants (its make_rails receipt)
        borrowed = sys.argv[7]
        hc = json.loads(Path(borrowed).read_text())['host_constants']
        hdr_t = bytes(36) + bytes.fromhex(hc['header_36_48'])
        seg_first_prefix, seg_prefix, trailer = (bytes.fromhex(hc[k]) for k in ('first_prefix', 'prefix', 'trailer'))
    else:
        raise SystemExit('host has no rail-bound spline; pass a template receipt (TK21)')
    consts = dict(header_36_48=hdr_t[36:48].hex(), first_prefix=seg_first_prefix.hex(), prefix=seg_prefix.hex(), trailer=trailer.hex())
    if any(p[48 + 144 * j + 136:48 + 144 * j + 144] != trailer for _, p in old for j in range(struct.unpack_from('<I', p, 32)[0])):
        raise SystemExit('host segment trailers differ; profile does not hold')
    added, gi = [], 0
    for rid, spline in enumerate(rails):
        if spline['flags'] != 0:
            raise SystemExit(f'donor rail {rid} flags {spline["flags"]} unsupported')
        p = encode_ps2(spline, rid, track, gi, m, t, hdr_t, seg_first_prefix, seg_prefix, trailer)
        gi += len(spline['segments'])
        added.append((dict(kind=8, track=track, rid=rid, size=len(p)), p))
    # audit: emitted curve vs transformed donor curve
    worst = 0.0
    for (e, p), spline in zip(added, rails):
        for j, seg in enumerate(spline['segments']):
            rows = [struct.unpack_from('<4f', p, 48 + 144 * j + 16 + 16 * k) for k in range(4)]
            for s in (0, 0.25, 0.5, 0.75, 1):
                want = apply(m, t, curve_point(seg['coefficients'], s))
                worst = max(worst, math.dist(curve_point(rows, s), want))
    new_script = bytearray(script[:table])
    struct.pack_into('<I', new_script, 84, len(added))
    new_script += RAIL_BINDING * len(added)
    out, inserted = bytearray(), False
    for e, p in recs:
        if e['kind'] == 8:
            if not inserted:
                for ae, ap in added:
                    out += bytes([8]) + len(ap).to_bytes(3, 'little') + bytes([track]) + ae['rid'].to_bytes(3, 'little') + ap
                inserted = True
            continue
        hdr = bytearray(raw[e['offset']:e['offset'] + 8])
        if e['kind'] == 16:
            hdr[1:4] = len(new_script).to_bytes(3, 'little'); p = bytes(new_script)
        out += hdr + p
    raws = {g['index']: base.raw(g['index']) for g in base.sdb['groups']}
    raws[G] = bytes(out)
    report = probe_ssx3(Region(io.BytesIO(base_bytes), 0, len(base_bytes)))
    # kind 8 counts toward the SDB memsize (kinds 0-12): patch the group's memsize like replace_terrain.update_sdb
    from replace_terrain import update_sdb
    sdb_bytes, _ = update_sdb(base.sdb_bytes, G, recs, list(resource_records(bytes(out))))
    bam, layout, _ = assemble_archive(base_bytes, report, raws, sdb_bytes, jobs=6, reuse_original_blocks=True)
    world = gdir.rename_world_members(bam, stem)
    Path(out_p).parent.mkdir(parents=True, exist_ok=True)
    Path(out_p).write_bytes(world)
    back = W(out_p)
    ok = back.raw(G) == raws[G] and all(back.raw(g['index']) == raws[g['index']] for g in base.sdb['groups'])
    print(json.dumps(dict(base=base_p, base_sha256=sha(base_bytes), group=G, track=track,
                          host_splines=len(old), host_bindings={b.hex(): bindings.count(b) for b in set(bindings)},
                          donor_rails=len(rails), donor_segments=gi, emitted_bytes=sum(len(p) for _, p in added),
                          host_constants=consts, borrowed_template=borrowed, host_own_constants=dict(first=Counter(p[48:60].hex() for _, p in old).most_common(3), trailer=Counter(p[48 + 136:48 + 144].hex() for _, p in old).most_common(3)), max_curve_error=worst, all_groups_decode=ok,
                          out=out_p, out_sha256=sha(Path(out_p).read_bytes()), out_bytes=len(world)), indent=1))


if __name__ == '__main__':
    main()
