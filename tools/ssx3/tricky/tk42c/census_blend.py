#!/usr/bin/env python3
"""TK42c: census of donor blend-flag materials per course.

Usage: census_blend.py DONOR_DIR OUT.json
DONOR_DIR holds <course>.pbd + <course>.ssh (+ optional <course>.map for names).

Prints per material: texture, flipbook, ints[6]; per texture: alpha histogram of
used palette entries; per material: instance count + model list.
Writes JSON with the full census.
"""
import json, struct, sys
from pathlib import Path
from collections import Counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tk13'))
from tricky_pbd import PBD


def ssh_entries(ssh):
    n = struct.unpack_from('<I', ssh, 8)[0]
    return [struct.unpack_from('<I', ssh, 20 + 8 * j)[0] for j in range(n)]


def tex_alpha(ssh, off):
    """Alpha histogram over USED palette entries: {alpha_byte: pixel_count}."""
    typ = ssh[off]
    nxt = int.from_bytes(ssh[off + 1:off + 4], 'little')
    w, h = struct.unpack_from('<HH', ssh, off + 4)
    if typ != 2 or off + 16 + w * h > len(ssh):
        return dict(typ=typ, w=w, h=h, error='bad-entry')
    pix = ssh[off + 16:off + 16 + w * h]
    po = off + nxt
    if po + 16 + 1024 > len(ssh):
        return dict(typ=typ, w=w, h=h, error='bad-palette')
    pal = ssh[po + 16:po + 16 + 1024]
    alphas = [pal[4 * i + 3] for i in range(256)]
    hist = Counter()
    for px in set(pix):
        hist[alphas[px]] += pix.count(px)
    used = sorted(set(pix))
    return dict(typ=typ, w=w, h=h, distinct_px=len(used),
                alpha_hist={hex(k): v for k, v in sorted(hist.items())},
                n_transparent=sum(v for k, v in hist.items() if k == 0),
                n_partial=sum(v for k, v in hist.items() if 0 < k < 0x80),
                n_half=sum(v for k, v in hist.items() if k == 0x80),
                n_opaque=sum(v for k, v in hist.items() if k > 0x80),
                pal_sha=__import__('hashlib').sha256(pal).hexdigest()[:16])


def main():
    donor = Path(sys.argv[1])
    out = sys.argv[2]
    pbds = sorted(donor.glob('*.pbd'), key=lambda p: (p.stem != donor.name, len(p.name)))
    pbd_path = pbds[0]
    ssh_path = next(donor.glob('*.ssh'))
    # prefer the plain <course>.ssh over sky/L variants
    cands = sorted(donor.glob('*.ssh'), key=lambda p: (len(p.name), p.name))
    for c in cands:
        if '_' not in c.stem:
            ssh_path = c
            break
    pbd = PBD(pbd_path.read_bytes())
    ssh = ssh_path.read_bytes()
    offs = ssh_entries(ssh)
    print(f'# {pbd_path.name}: {pbd.counts["instances"]} inst, {pbd.counts["materials"]} mats, '
          f'{pbd.counts["models"]} models; {ssh_path.name}: {len(offs)} entries')

    # model -> set of material indices (via material blocks)
    model_mats = {}
    for mi, m in enumerate(pbd.models):
        block = pbd.material_blocks[m['material_block']]
        s = set()
        for ob in m['objects']:
            for mesh in ob['meshes']:
                if mesh['material_slot'] < len(block):
                    s.add(block[mesh['material_slot']])
        model_mats[mi] = sorted(s)
    # material -> instances
    mat_insts = {i: [] for i in range(pbd.counts['materials'])}
    for i, inst in enumerate(pbd.instances):
        for mi in model_mats.get(inst['model'], []):
            mat_insts[mi].append(i)

    tex_cache = {}
    rows = []
    for mi, mat in enumerate(pbd.materials):
        t = mat['texture']
        if t not in tex_cache:
            tex_cache[t] = tex_alpha(ssh, offs[t]) if 0 <= t < len(offs) else dict(error='oob')
        rows.append(dict(mat=mi, texidx=t, flip=mat['flipbook'],
                         u3=hex(mat['u3']), u8=hex(mat['u8']),
                         ints=[hex(v) for v in mat['ints']],
                         ninst=len(mat_insts[mi]), insts=mat_insts[mi][:12],
                         tex=tex_cache[t]))
    # distinct ints vectors summary
    ivec = Counter(tuple(r['ints']) for r in rows)
    print(f'# distinct ints-vectors: {len(ivec)}')
    for vec, n in ivec.most_common():
        print(f'#   n={n} {vec}')
    Path(out).write_text(json.dumps(dict(pbd=str(pbd_path), ssh=str(ssh_path),
                                         counts=pbd.counts, rows=rows), indent=1))
    print(f'# wrote {out}')


if __name__ == '__main__':
    main()
