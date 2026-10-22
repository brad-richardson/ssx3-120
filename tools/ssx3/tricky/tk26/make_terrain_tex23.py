#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later OR MIT
"""TK23 lane copy of TK19 make_terrain_tex.py (working tree f405e1cc, with TK19 Part 2 --mips-only/--compact-ids) + --exact-bounds.

    make_terrain_tex.py WORLD.BIG EXPERIMENT.json DONOR.pbd DONOR.ssh DONOR_L.ssh STEM OUT.BIG [--receipt R.json]
        [--mips 4] [--lm-alpha-scale 0.5] [--lm-inset 0.5] [--no-lightmaps] [--tex-max N]

WORLD.BIG: a TK16 world (replace_terrain + make_scenery16 + make_rails16); every patch still carries the SSX 3
template's texture/lightmap handles. EXPERIMENT.json: its replace_terrain recipe (group, track, matrix, translation).

Mapping (TK19 REPORT §1, all measured):
  Tricky PS2 patch (448 B, .pbd): +0 lightmap rect (u, v, w, h; 8-px cells of a 128-px sheet), +16 four corner
    UV vec4 (u, v, 1, 1), +432 u32 = lightmap sheet << 16 | texture index (.ssh entry). Same rect/UV words as the GC
    .nbd (3,885/3,885 Garibaldi patches); the GC texture i16 at 428 equals the PS2 +432 low half.
  SSX 3 PS2 patch (432 B): +16 lightmap rect, +32 four corner (u, v) pairs, +340 page (texture group << 16 |
    track << 8), +416 u16 texture rid (kind 9), +418 u16 lightmap rid (kind 10).
  Kind 9 (terrain texture, as stock): 8-bit PSMT8-swizzled levels 128/64/32/16 back to back (each swizzled at its
    own size), flags (levels-1) << 28 | 0x3000, CSM1 palette block at +nxt.
  Kind 10: type 5 (PSMCT32, linear), 128x128, flags 0x1000, 112-B reserved area, RGBA texels.
  Lightmap pass (GS capture c2): base texture MODULATE, then the sheet with ALPHA (Cd - Cs) * As / 128, i.e.
    lit = (T - X_rgb) * A / 128. Tricky's sheets use the same RGB/A split; their A runs to 255 for full light, which
    the GameCube evidence puts at 1.0 (Tricky lightmap scale 1 vs SSX 3 scale 2), so A is halved
    (--lm-alpha-scale 0.5) and RGB copied.
Patches are matched to donor patches by their coefficient block (the float32 bytes replace_terrain wrote).
New kind-9/10 records get global ids above the world's, go into the pinned texture group, and the SDB group
record and the global kind-9/10 capacities are updated. Group bytes are read back.
"""
import argparse, io, json, math, struct, sys
from collections import Counter, defaultdict
from pathlib import Path
HERE = Path(__file__).resolve().parent
R = HERE.parent
sys.path.insert(0, str(R /'tk13')); sys.path.insert(0, str(R /'tk12')); sys.path.insert(0, str(R / 'shared'))
from wlib import W, sha
from probe_worlds import probe_ssx3, resource_records
from relayout_stream import assemble_archive
from inspect_disc import Region
import gamecube_game_dir as gdir
gdir.WORLD_EXTENSIONS = ('sdb', 'ssb', 'phm', 'psm')  # PS2 world members (the shared helper lists the GC names)
from import_terrain import transform_coefficients
from ps2swz import swizzle8, csm1

TRICKY_STRIDE = 448


def donor_patches(pbd):
    n, off = struct.unpack_from('<I', pbd, 8)[0], struct.unpack_from('<I', pbd, 68)[0]
    out = []
    for i in range(n):
        o = off + TRICKY_STRIDE * i
        word = struct.unpack_from('<I', pbd, o + 432)[0]
        out.append(dict(coeffs=[list(struct.unpack_from('<4f', pbd, o + 80 + 16 * j)[:3]) for j in range(16)],
                        rect=struct.unpack_from('<4f', pbd, o), uv=[struct.unpack_from('<2f', pbd, o + 16 + 16 * k) for k in range(4)],
                        texture=word & 0xffff, lightmap=word >> 16))
    return out


def ssh_entries(ssh):
    return [struct.unpack_from('<I', ssh, 20 + 8 * j)[0] for j in range(struct.unpack_from('<I', ssh, 8)[0])]


def nearest_index(pal, cache, rgba):
    hit = cache.get(rgba)
    if hit is None:
        r, g, b, a = rgba
        hit = min(range(256), key=lambda i: (pal[i][0] - r) ** 2 + (pal[i][1] - g) ** 2 + (pal[i][2] - b) ** 2 + (pal[i][3] - a) ** 2)
        cache[rgba] = hit
    return hit


def terrain_texture(ssh, off, mips, tex_max=None):
    """Donor SSH 8-bit entry -> stock-layout kind-9 payload with `mips` levels (2x2 box filter, nearest palette entry)."""
    typ, nxt = ssh[off], int.from_bytes(ssh[off + 1:off + 4], 'little')
    w, h = struct.unpack_from('<HH', ssh, off + 4)
    if typ != 2:
        raise ValueError(f'texture type {typ}')
    pix = ssh[off + 16:off + 16 + w * h]
    po = off + nxt
    if ssh[po] != 0x21 or struct.unpack_from('<HH', ssh, po + 4)[0] * struct.unpack_from('<HH', ssh, po + 4)[1] != 256:
        raise ValueError('palette')
    nat = ssh[po + 16:po + 16 + 1024]
    pal = [tuple(nat[4 * i:4 * i + 4]) for i in range(256)]
    while tex_max and max(w, h) > tex_max:
        pix = b''.join(pix[r * w:(r + 1) * w:2] for r in range(0, h, 2)); w, h = w // 2, h // 2
    levels, cache, cur, cw, ch = [], {}, pix, w, h
    for lv in range(mips):
        if cw % 16 or ch % 4:
            break
        levels.append(swizzle8(cur, cw, ch))
        if lv + 1 == mips:
            break
        nw, nh = cw // 2, ch // 2
        nxtpix = bytearray(nw * nh)
        for y in range(nh):
            for x in range(nw):
                q = [pal[cur[(2 * y + dy) * cw + 2 * x + dx]] for dy in (0, 1) for dx in (0, 1)]
                nxtpix[y * nw + x] = nearest_index(pal, cache, tuple((sum(c[k] for c in q) + 2) // 4 for k in range(4)))
        cur, cw, ch = bytes(nxtpix), nw, nh
    body = b''.join(levels)
    cpal = bytearray(1024)
    for i in range(256):
        cpal[4 * csm1(i):4 * csm1(i) + 4] = nat[4 * i:4 * i + 4]
    reserved = struct.pack('<I', 0x80) + bytes(108)
    img = bytearray(struct.pack('<B3sHHHHI', 2, b'\0\0\0', w, h, 0, 0, ((len(levels) - 1) << 28) | 0x3000))
    img += reserved + body + bytes(-len(body) % 16)
    struct.pack_into('<I', img, 0, 2 | (len(img) << 8))
    pblk = struct.pack('<B3sHHHHI', 0x21, b'\0\0\0', 255, 1, 255, 0, 0x3000) + reserved + bytes(cpal)
    return bytes(img) + pblk, len(levels)


def lightmap_records(lssh, alpha_scale, rgb_scale=1.0):
    out = []
    for off in ssh_entries(lssh):
        typ, w, h = lssh[off], *struct.unpack_from('<HH', lssh, off + 4)
        if typ != 5:
            raise ValueError(f'lightmap type {typ}')
        px = bytearray(lssh[off + 16:off + 16 + 4 * w * h])
        for i in range(3, len(px), 4):
            px[i] = min(255, int(px[i] * alpha_scale + 0.5))
            if rgb_scale != 1.0:
                for k in (i - 3, i - 2, i - 1):
                    px[k] = min(255, int(px[k] * rgb_scale + 0.5))
        hdr = struct.pack('<B3sHHHHI', 5, b'\0\0\0', w, h, 0, 0, 0x1000) + struct.pack('<I', 0x80) + bytes(108)
        out.append(dict(width=w, payload=hdr + bytes(px)))
    return out


_DN = 64
_PW = [[(i / _DN) ** r for r in range(4)] for i in range(_DN + 1)]


def dense_bounds(q):
    """TK23: box of the stored float32 coefficients sampled on a 65x65 (u, v) grid, widened 0.5 outward."""
    c = [struct.unpack_from('<3f', q, 64 + 16 * j) for j in range(16)][::-1]  # c[row*4+col]: v^row u^col
    lo, hi = [math.inf] * 3, [-math.inf] * 3
    for a in range(3):
        C = [[c[r * 4 + col][a] for col in range(4)] for r in range(4)]
        for pv in _PW:
            row = [sum(C[r][col] * pv[r] for r in range(4)) for col in range(4)]
            for pu in _PW:
                x = row[0] + row[1] * pu[1] + row[2] * pu[2] + row[3] * pu[3]
                if x < lo[a]: lo[a] = x
                if x > hi[a]: hi[a] = x
    return [x - 0.5 for x in lo], [x + 0.5 for x in hi]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for a in ('world', 'experiment', 'pbd', 'ssh', 'lssh', 'stem', 'out'):
        ap.add_argument(a)
    ap.add_argument('--receipt')
    ap.add_argument('--exact-bounds', action='store_true', help='TK23: rewrite every patch box (+344/+356) and sphere (+320) from a '
                    'dense 65x65 surface sample + 0.5 outward (stock boxes are exact; import_terrain bounds a 1/8 grid and '
                    'under-encloses crests by up to ~6 units)')
    ap.add_argument('--mips', type=int, default=4)
    ap.add_argument('--tex-max', type=int)
    ap.add_argument('--offline-tex-max', type=int, help='with --mips-only: textures not on the list are halved (nearest) to this side and get one level')
    ap.add_argument('--mips-only', help='file of donor texture indices that get --mips levels; every other texture gets one level')
    ap.add_argument('--lm-alpha-scale', type=float, default=0.5)
    ap.add_argument('--lm-rgb-scale', type=float, default=1.0, help='diagnostic: scale the sheet RGB (subtractive term)')
    ap.add_argument('--lm-inset', type=float, default=0.5, help='texels trimmed from each cell edge (GC route: half a texel)')
    ap.add_argument('--no-lightmaps', action='store_true', help='bisect: keep the template lightmap, import textures only')
    ap.add_argument('--prune', action='store_true', help='drop pinned-group records nothing references after the rebind: '
                    'kind 10 no patch uses, kind 9 no patch or kind-0 material (any group) uses')
    ap.add_argument('--prune-orphans', action='store_true', help='with --prune: kind-0 materials no kind-2 model in the course '
                    'group names (host leftovers) get field 0 = a live material\'s texture and field 1 = -1 first, so the host textures '
                    'only they named can go')
    ap.add_argument('--compact-ids', action='store_true', help='renumber every kind-9/10 record of the pinned group with an id at '
                    'or above the stock capacity into ids freed by this location\'s unloaded texture groups and the prune, rewrite '
                    'material and patch references, and restore the stock capacities (UI font handles, Part 2 §6)')
    ap.add_argument('--far', type=float, default=150000.0, help='compact ids: a location this far (spatial root boxes) never co-loads')
    ap.add_argument('--stock-caps', default='788,623', help='stock SDB kind-9,kind-10 capacities (SLUS-20772 BAM.BIG)')
    ap.add_argument('--prune-kinds', default='9,10', help='with --prune: which kinds may be dropped')
    ap.add_argument('--no-textures', action='store_true', help='bisect: keep the template texture/UVs, import lightmaps only')
    ap.add_argument('--tmp', default=str(Path.home() / 'dev/ssx3-work/TK19/tmp'))
    args = ap.parse_args()

    exp = json.loads(Path(args.experiment).read_text())
    G, T, M3, TR = exp['group'], exp['track'], exp['matrix'], exp['translation']
    base = Path(args.world).read_bytes()
    Path(args.tmp).mkdir(parents=True, exist_ok=True)
    tmp = Path(args.tmp) / (Path(args.out).parent.name + '-' + Path(args.out).stem + '-bam.BIG')
    bam = gdir.rename_world_members(base, 'bam')
    tmp.write_bytes(bam)
    world = W(tmp)
    groups = world.sdb['groups']
    recs = world.records(G)
    pages = Counter(struct.unpack_from('<I', p, 340)[0] for e, p in recs if e['kind'] == 1)
    if len(pages) != 1:
        raise SystemExit(f'patches use {len(pages)} pages; expected one pinned group')
    page = next(iter(pages)); TG = page >> 16

    pbd, ssh, lssh = (Path(x).read_bytes() for x in (args.pbd, args.ssh, args.lssh))
    donor = donor_patches(pbd)
    key = {}
    for i, d in enumerate(donor):
        c = transform_coefficients(d['coeffs'], M3, TR)
        key.setdefault(b''.join(struct.pack('<4f', *v, 1.0) for v in c), []).append(i)  # TK23: duplicates (Megaplex has a coincident pair) in order
    dup_donors = [list(v) for v in key.values() if len(v) > 1]
    patches = [(k, e, p) for k, (e, p) in enumerate(recs) if e['kind'] == 1]
    match = {}
    for k, e, p in patches:
        lst = key.get(p[64:320])
        if not lst:
            raise SystemExit(f'patch rid {e["rid"]}: no donor patch with these coefficients')
        match[k] = lst.pop(0)
    if len(set(match.values())) != len(patches) or len(patches) != len(donor):
        raise SystemExit(f'patch mapping not one to one ({len(set(match.values()))} donors for {len(patches)} patches, donor {len(donor)})')

    # ---- new kind 9 / 10 records
    gmax = {9: -1, 10: -1}
    for g in groups:
        for e, p in world.records(g['index']):
            if e['kind'] in gmax: gmax[e['kind']] = max(gmax[e['kind']], e['rid'])
    offs = ssh_entries(ssh)
    used_tex = [] if args.no_textures else sorted({d['texture'] for d in donor})
    tex_rid, new_recs, levels = {}, [], Counter()
    mips_set = {int(x) for x in open(args.mips_only).read().split()} if args.mips_only else set(used_tex)
    for t in used_tex:
        payload, nlev = terrain_texture(ssh, offs[t], args.mips if t in mips_set else 1,
                                        args.tex_max if t in mips_set else (args.offline_tex_max or args.tex_max))
        levels[nlev] += 1
        tex_rid[t] = gmax[9] + 1 + len(tex_rid)
        new_recs.append((dict(kind=9, track=255, rid=tex_rid[t]), payload))
    lm_rid, sheets = {}, []
    if not args.no_lightmaps:
        sheets = lightmap_records(lssh, args.lm_alpha_scale, args.lm_rgb_scale)
        used_lm = sorted({d['lightmap'] for d in donor})
        if used_lm[-1] >= len(sheets):
            raise SystemExit('lightmap index past the _L.ssh sheets')
        for l in used_lm:
            lm_rid[l] = gmax[10] + 1 + len(lm_rid)
            new_recs.append((dict(kind=10, track=255, rid=lm_rid[l]), sheets[l]['payload']))
    if (tex_rid and max(tex_rid.values()) > 0xffff) or (lm_rid and max(lm_rid.values()) > 0xffff):
        raise SystemExit('rid exceeds the u16 patch field')

    # ---- rebind patches
    out = list(recs)
    bounds_change = []
    for k, e, p in patches:
        d = donor[match[k]]
        q = bytearray(p)
        if not args.no_lightmaps:
            u, v, w, h = d['rect']
            s = args.lm_inset / sheets[d['lightmap']]['width']
            struct.pack_into('<4f', q, 16, u + s, v + s, w - 2 * s, h - 2 * s)
            struct.pack_into('<H', q, 418, lm_rid[d['lightmap']])
        if not args.no_textures:
            for c, (tu, tv) in enumerate(d['uv']):
                struct.pack_into('<2f', q, 32 + 8 * c, tu, tv)
            struct.pack_into('<H', q, 416, tex_rid[d['texture']])
        if args.exact_bounds:
            lo, hi = dense_bounds(q)
            old = struct.unpack_from('<6f', q, 344)
            bounds_change.append(max(max(old[a] - lo[a], hi[a] - old[3 + a]) for a in range(3)))
            struct.pack_into('<4f', q, 320, *[(lo[a] + hi[a]) / 2 for a in range(3)], math.dist(lo, hi) / 2)
            struct.pack_into('<3f', q, 344, *lo); struct.pack_into('<3f', q, 356, *hi)
        out[k] = (e, bytes(q))

    ser = lambda rows: b''.join(bytes([e['kind']]) + len(p).to_bytes(3, 'little') + bytes([e['track']]) + e['rid'].to_bytes(3, 'little') + p for e, p in rows)
    raws = {g['index']: world.raw(g['index']) for g in groups}
    raws[G] = ser(out)
    if ser(recs) != world.raw(G):
        raise SystemExit('terrain group re-serialisation does not reproduce the input')
    tg_old = world.records(TG)
    pruned, repointed = [], []
    if args.prune and args.prune_orphans:
        live = set()
        for e, p in out:
            if e['kind'] == 2:
                n = struct.unpack_from('<I', p, 40)[0]
                live |= {h >> 8 for h in struct.unpack_from('<%dI' % n, p, 44)}
        live_tex = next(struct.unpack_from('<h', p, 0)[0] for e, p in out if e['kind'] == 0 and e['rid'] in live)
        for k, (e, p) in enumerate(out):
            if e['kind'] == 0 and e['rid'] not in live:
                q = bytearray(p)
                struct.pack_into('<h', q, 0, live_tex)
                if struct.unpack_from('<h', q, 2)[0] >= 0:
                    struct.pack_into('<h', q, 2, -1)
                out[k] = (e, bytes(q))
                repointed.append(e['rid'])
        raws[G] = ser(out)
    if args.prune:
        lm_used = {struct.unpack_from('<H', p, 418)[0] for e, p in out if e['kind'] == 1}
        tex_used = {struct.unpack_from('<H', p, 416)[0] for e, p in out if e['kind'] == 1}
        for g in groups:
            rows = out if g['index'] == G else world.records(g['index'])
            for e, p in rows:
                if e['kind'] == 0:
                    for f in struct.unpack_from('<2h', p, 0):
                        if f >= 0:
                            tex_used.add(f)
        keep = []
        for e, p in tg_old:
            kinds = {int(k) for k in args.prune_kinds.split(',')}
            if e['kind'] in kinds and ((e['kind'] == 10 and e['rid'] not in lm_used) or (e['kind'] == 9 and e['rid'] not in tex_used)):
                pruned.append(dict(kind=e['kind'], rid=e['rid'], bytes=len(p) + 8))
            else:
                keep.append((e, p))
        tg_old = keep
    remap = {9: {}, 10: {}}
    if args.compact_ids:
        cap9, cap10 = (int(x) for x in args.stock_caps.split(','))
        caps = {9: cap9, 10: cap10}
        locs = world.locations()
        loc = next(l for l in locs if l['group_start'] <= TG <= l['last_group'])
        sp = world.sdb['spatial']
        box = lambda l: (sp[l['spatial_start']]['floats'][0:3], sp[l['spatial_start']]['floats'][4:7])
        gap = lambda a, b: math.sqrt(sum(max(0, a[0][k] - b[1][k], b[0][k] - a[1][k]) ** 2 for k in range(3)))
        far = {l['name'] for l in locs if l['spatial_count'] >= 1 and 'SKY' not in l['name'] and l['name'] != 'TRANSP'
               and gap(box(l), box(loc)) >= args.far}
        allowed = {gi for gi in range(len(groups)) if groups[gi]['location'] in far}
        allowed |= {gi for gi in range(loc['group_start'], loc['last_group'] + 1)
                    if gi not in (TG, G) and not set(groups[gi]['kind_counts']) - {9, 10}}
        where = {9: defaultdict(set), 10: defaultdict(set)}
        for gi in range(len(groups)):
            for e, p in world.records(gi):
                if e['kind'] in where:
                    where[e['kind']][e['rid']].add(gi)
        pool = {kk: {r for r, gs in where[kk].items() if gs <= allowed} for kk in (9, 10)}
        for x in pruned:
            if where[x['kind']][x['rid']] <= allowed | {TG}:
                pool[x['kind']].add(x['rid'])
        resident = {(e['kind'], e['rid']) for e, p in tg_old + new_recs}
        for kk in (9, 10):
            pool[kk] = sorted(r for r in pool[kk] if r < caps[kk] and (kk, r) not in resident)
            movers = sorted(e['rid'] for e, p in tg_old + new_recs if e['kind'] == kk and e['rid'] >= caps[kk])
            if len(movers) > len(pool[kk]):
                raise SystemExit(f'compact ids: {len(movers)} kind-{kk} records above the stock cap, only {len(pool[kk])} free ids')
            remap[kk] = dict(zip(movers, pool[kk]))
        ren = lambda rows: [(dict(e, rid=remap[e['kind']].get(e['rid'], e['rid'])) if e['kind'] in (9, 10) else e, p) for e, p in rows]
        tg_old, new_recs = ren(tg_old), ren(new_recs)
        for k, (e, p) in enumerate(out):
            if e['kind'] == 0:
                q = bytearray(p)
                for off in (0, 2):
                    f = struct.unpack_from('<h', q, off)[0]
                    if f in remap[9]:
                        struct.pack_into('<h', q, off, remap[9][f])
                out[k] = (e, bytes(q))
            elif e['kind'] == 1:
                q = bytearray(p)
                t, l = struct.unpack_from('<HH', q, 416)
                struct.pack_into('<HH', q, 416, remap[9].get(t, t), remap[10].get(l, l))
                out[k] = (e, bytes(q))
        raws[G] = ser(out)
        tex_rid = {t: remap[9].get(r, r) for t, r in tex_rid.items()}
        lm_rid = {t: remap[10].get(r, r) for t, r in lm_rid.items()}
    raws[TG] = ser(tg_old) + ser(new_recs)

    sdb = bytearray(world.sdb_bytes)
    lc, nodes, _ = struct.unpack_from('<III', sdb, 8)
    gtab = (80 + 88 * lc + 15) // 16 * 16 + nodes * 96

    def set_group(gi, rows):
        o = gtab + 68 * gi
        struct.pack_into('<H', sdb, o, len(rows))
        struct.pack_into('<I', sdb, o + 8, sum(len(p) + 8 for e, p in rows if e['kind'] <= 12))
        c = Counter(e['kind'] for e, p in rows)
        for kk in range(14):
            struct.pack_into('<H', sdb, o + 12 + 2 * kk, c.get(kk, 0))
    before = bytes(sdb[gtab + 68 * TG:gtab + 68 * TG + 68])
    set_group(TG, list(resource_records(world.raw(TG))))
    if bytes(sdb[gtab + 68 * TG:gtab + 68 * TG + 68]) != before:
        raise SystemExit('group record rewrite does not reproduce the input record')
    tg_rows = list(resource_records(raws[TG]))
    set_group(TG, tg_rows)
    if args.compact_ids:
        top = {9: -1, 10: -1}
        for gi in range(len(groups)):
            rows = tg_rows if gi == TG else world.records(gi)
            for e, p in rows:
                if e['kind'] in top: top[e['kind']] = max(top[e['kind']], e['rid'])
        for kk in (9, 10):
            if top[kk] + 1 > caps[kk]:
                raise SystemExit(f'compact ids: kind {kk} id {top[kk]} still at/above the stock cap {caps[kk]}')
            struct.pack_into('<h', sdb, 24 + 2 * kk, caps[kk])
    else:
        for kk, rid in ((9, max(tex_rid.values()) if tex_rid else -1), (10, max(lm_rid.values()) if lm_rid else -1)):
            cap = struct.unpack_from('<h', sdb, 24 + 2 * kk)[0]
            if rid + 1 > 32767: raise SystemExit('capacity overflow')
            struct.pack_into('<h', sdb, 24 + 2 * kk, max(cap, rid + 1))
    report = probe_ssx3(Region(io.BytesIO(bam), 0, len(bam)))
    newbam, layout, _ = assemble_archive(bam, report, raws, bytes(sdb), jobs=6, reuse_original_blocks=True)
    final = gdir.rename_world_members(newbam, args.stem)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_bytes(final)
    back = W(args.out)
    ok = all(back.raw(g['index']) == raws[g['index']] for g in groups)
    tex_bytes = sum(len(p) + 8 for e, p in new_recs if e['kind'] == 9)
    lm_bytes = sum(len(p) + 8 for e, p in new_recs if e['kind'] == 10)
    rc = dict(world=args.world, world_sha256=sha(base), out=args.out, out_sha256=sha(final), out_bytes=len(final), readback=ok,
              group=G, texture_group=TG, page=hex(page), patches=len(patches), donor_patches=len(donor),
              textures=len(tex_rid), texture_levels=dict(levels), texture_rids=[min(tex_rid.values()), max(tex_rid.values())] if tex_rid else None,
              lightmaps=len(lm_rid), lightmap_rids=[min(lm_rid.values()), max(lm_rid.values())] if lm_rid else None,
              repointed_orphan_materials=len(repointed),
              duplicate_donor_patches=dup_donors,
              exact_bounds=dict(patches=len(bounds_change), grew_max=max(bounds_change), grew_over_1=sum(x > 1 for x in bounds_change),
                                grew_over_4=sum(x > 4 for x in bounds_change)) if bounds_change else None,
              compact_ids=dict(kind9=len(remap[9]), kind10=len(remap[10]), pool9=len(pool[9]) if args.compact_ids else None,
                               pool10=len(pool[10]) if args.compact_ids else None, map9=remap[9], map10=remap[10], caps=[struct.unpack_from('<h', sdb, 24 + 18)[0], struct.unpack_from('<h', sdb, 44)[0]]),
              pruned=dict(records=len(pruned), bytes=sum(x['bytes'] for x in pruned), kinds=dict(Counter(x['kind'] for x in pruned)),
                          rids=sorted((x['kind'], x['rid']) for x in pruned)),
              lm_alpha_scale=args.lm_alpha_scale, lm_rgb_scale=args.lm_rgb_scale, lm_inset=args.lm_inset,
              added_bytes=dict(textures=tex_bytes, lightmaps=lm_bytes),
              texture_group_memsize=sum(len(p) + 8 for e, p in tg_rows if e['kind'] <= 12),
              texture_group_memsize_before=groups[TG]['memsize'], texture_group_records=len(tg_rows),
              course_group_memsize=groups[G]['memsize'],
              donor=dict(pbd=sha(pbd), ssh=sha(ssh), lssh=sha(lssh)))
    if args.receipt:
        Path(args.receipt).write_text(json.dumps(rc, indent=1))
    print(json.dumps(rc, indent=1))


if __name__ == '__main__':
    main()
