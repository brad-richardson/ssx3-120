#!/usr/bin/env python3
"""TK12: carry the donor's authored reset surfaces into a PS2 replace-world (item 5), as <STEM>.BIG.

    make_resets.py BASE.BIG EXPERIMENT.JSON DONOR.pbd STEM OUT.BIG

Mechanism: replace_terrain.py makes every imported patch from one host template patch, so every patch gets
the template's surface word (u16 +8 surface 0, u16 +10 flags 0x9). Tricky's authored reset surfaces
(donor surface 0: cliff faces, out-of-bounds slopes; Garibaldi 622, Snowdream 304, Aloha 415 patches, all
PS2-measured identical) become plain snow, and the rider rides/falls past them without a reset.
Fix (profile reset-v2): on each patch whose donor surface is 0, keep surface 0 at +8 and OR flag 0x0002
into the u16 at +10 (the engine's direct course-path reset; stock PS2 patches carry the same bit, e.g.
ARA1 flags 11 = 0x9|0x2). The donor surfaces come from the PS2 .pbd patch tail (little-endian surface
word at patch+424), matched ordinally and checked by geometry against every imported patch (transformed
donor coefficients vs the PS2 record's rows at +64; max error must stay < 1 unit).
Only the terrain group changes; readback decodes every group.
"""
import io, json, math, os, struct, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from wlib import W, sha
from probe_worlds import probe_ssx3, resource_records
from relayout_stream import assemble_archive
from inspect_disc import Region
from import_terrain import transform_coefficients
import gamecube_game_dir as gdir

gdir.WORLD_EXTENSIONS = ('sdb', 'ssb', 'phm', 'psm')

# PS2 donor .pbd patch table: same 448-byte stride and +80 coefficients as
# the retired GameCube NBD reader saw (big-endian there, little-endian
# here); the authored surface word is the '<I' at the patch tail
# ('<I4h' surface,u2,vis,tex,lm; cf. shared/inventory_tricky.py PATCH_TAIL).
PBD_PATCH_STRIDE = 448
PBD_PATCH_COEFF = 80
PBD_PATCH_SURFACE = 424


def pbd_surfaces(raw):
    """Ordinal (coeffs, surface) list from a Tricky PS2 donor .pbd.

    Same pairing contract as the retired GameCube NBD reader: 16
    little-endian coefficient triples per patch, reset iff surface == 0.
    Refuses non-patch-table input fail-closed.
    """
    if len(raw) < 144:
        raise ValueError('Too short for a Tricky PBD patch table')
    count = struct.unpack_from('<I', raw, 8)[0]
    start = struct.unpack_from('<I', raw, 68)[0]
    inst = struct.unpack_from('<I', raw, 72)[0]
    if not count or start + PBD_PATCH_STRIDE * count != inst or inst > len(raw):
        raise ValueError('Invalid Tricky PBD patch table extent')
    result = []
    for i in range(count):
        at = start + PBD_PATCH_STRIDE * i
        coeffs = [struct.unpack_from('<4f', raw, at + PBD_PATCH_COEFF + 16 * j)[:3] for j in range(16)]
        if not all(math.isfinite(v) for c in coeffs for v in c):
            raise ValueError(f'Nonfinite donor terrain at patch {i}')
        result.append((coeffs, struct.unpack_from('<I', raw, at + PBD_PATCH_SURFACE)[0]))
    return result


def main():
    base_p, exp_p, pbd_p, stem, out_p = sys.argv[1:6]
    base_bytes = Path(base_p).read_bytes()
    base_sha = sha(base_bytes)
    tmp = None
    if W(base_p).stem != 'bam':
        tmp = out_p + '.bam-tmp'
        base_bytes = gdir.rename_world_members(base_bytes, 'bam')
        Path(tmp).write_bytes(base_bytes)
    base = W(tmp or base_p)
    exp = json.loads(Path(exp_p).read_text())
    G = exp['group']
    src = pbd_surfaces(Path(pbd_p).read_bytes())
    raw = bytearray(base.raw(G))
    patches = [(e, p) for e, p in resource_records(bytes(raw)) if e['kind'] == 1]
    if len(patches) != len(src):
        raise SystemExit(f'patch count {len(patches)} != donor {len(src)}')
    worst, flagged, already = 0.0, [], 0
    for (e, p), (coeffs, surface) in zip(patches, src):
        want = transform_coefficients(coeffs, exp['matrix'], exp['translation'])
        got = [struct.unpack_from('<3f', p, 64 + 16 * j) for j in range(16)]
        worst = max(worst, max(math.dist(a, b) for a, b in zip(want, got)))
        if surface != 0:
            continue
        o = e['offset'] + 8
        surf, flags = struct.unpack_from('<2H', raw, o + 8)
        if surf != 0:
            raise SystemExit(f'patch rid {e["rid"]} surface {surf} != 0; profile expects the template surface 0')
        if flags & 2:
            already += 1
        struct.pack_into('<H', raw, o + 10, flags | 2)
        flagged.append(e['rid'])
    if worst >= 1.0:
        raise SystemExit(f'geometry mismatch {worst}: refusing ordinal surface assignment')
    raws = {g['index']: base.raw(g['index']) for g in base.sdb['groups']}
    raws[G] = bytes(raw)
    report = probe_ssx3(Region(io.BytesIO(base_bytes), 0, len(base_bytes)))
    bam, layout, _ = assemble_archive(base_bytes, report, raws, base.sdb_bytes, jobs=6, reuse_original_blocks=True)
    world = gdir.rename_world_members(bam, stem)
    Path(out_p).parent.mkdir(parents=True, exist_ok=True)
    Path(out_p).write_bytes(world)
    if tmp:
        os.remove(tmp)
    back = W(out_p)
    ok = all(back.raw(g['index']) == raws[g['index']] for g in base.sdb['groups'])
    print(json.dumps(dict(base=base_p, base_sha256=base_sha, group=G, donor_patches=len(src),
                          geometry_max_error=worst, reset_patches_flagged=len(flagged), already_flagged=already,
                          flag_bit=2, all_groups_decode=ok, out=out_p, out_sha256=sha(Path(out_p).read_bytes()),
                          out_bytes=len(world))))


if __name__ == '__main__':
    main()
