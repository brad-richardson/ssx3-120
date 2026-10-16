#!/usr/bin/env python3
"""TK21 lane copy of TK16 make_scenery16.py (adds --pose, --no-collision-file; posed instance bounds). TK16 (from TK13 make_scenery.py; TK16 changes: --profile tricky, the default): import a Tricky course's scenery (models, instances, textures, collision) into a PS2 replace-world.

    make_scenery.py BASE.BIG EXPERIMENT.JSON DONOR.pbd DONOR.ssh DONOR_GC.nbd DONOR_GC.gsf STEM OUT.BIG
                    [--no-collision] [--no-textures] [--light-scale F] [--max-instances N] [--receipt R.json]

BASE.BIG: a TK12 replace-world (kind 3 and 12 removed, kind-16 binding table disabled).
EXPERIMENT.JSON: its terrain recipe (group, track, matrix, translation).
Donor: the PS2 Tricky .pbd (models, instances, materials, per-instance lights) and its decoded .ssh
(textures); the GameCube .nbd/.gsf of the same course for the gameplay table (visibility, player
collision, collision meshes). PS2 and GC instances agree one for one (model index and matrix
bit-identical on Garibaldi, REPORT §1), so the GC gameplay rows are keyed by instance index.

What is written (all into the location's own groups; nothing else changes):
- kind 2 models (ps2mdr.encode_model; single object; donor object matrices composed into the vertices;
  strips re-chunked to SSX 3's VU budget), rids above the host's (host models are dropped: the host
  instances are already gone and the record/memory budget needs the room);
- kind 3 instances, rids 0..N-1 (the kind-16 binding table is indexed by rid): rigid basis + uniform
  scale at +132 (the GC collision finding: the engine inverts the basis by transposing it), world
  bounds, page ref of the pinned texture group, baked vertex colours (one DMA chain per mesh, stock layout)
  from the donor instance's light matrix/colours;
- kind 0 materials + kind 9 textures (donor SSH image + palette in the SSX 3 world-texture layout:
  16-B header, 112-B reserved area, PSMT8-swizzled texels, CSM1-ordered palette; new global ids above the world's, capacity grown),
  appended to the pinned texture group;
- kind 16: definitions (non-colliding 0/0x10000, colliding 1/0x210000 + per-part (1e30, 0, 3, -1)),
  the instance binding table, the spline tail kept;
- kind 12 collision (ps2col.encode_collision). Solid set:
  --profile gc (TK13): visible, mode-1, no-effect donor instances whose collision is one part (the narrow phase
    walks one collision mesh per render part), whose mesh passes the winding check, not composed, and whose
    bounds are clear of the reset corridors (GC rule: 35-unit margin, 150 high).
  --profile tricky (TK16, default): what Tricky's own gameplay table (.ssf/.gsf) makes solid for the player:
    visible + player-collision (flag bit 5) and
      mode 1, with or without an effect slot (the effect is a separate callback; fences, crowd stands),
        its authored collision mesh; triangles whose authored normal disagrees with the winding are re-wound;
        a mesh that partitions into > 1 part (> 256 vertices) becomes the convex hull of its vertices;
      mode 3 AND immovable (property u0 >= 1e29: trunks, rocks, poles, sign stands, pylons): Tricky's physics
        record holds mass properties, not a mesh, so the collision is the convex hull of the render model;
    never: mode 2 (triggers, gems, branches), mode 3 movable (u0 = 0: crash bags), hidden, sheared instances,
    anything within the reset corridors (same GC rule). Hulls over 256 vertices fall back to the model box.
"""
import argparse, io, json, math, struct, sys
from collections import Counter, defaultdict
from pathlib import Path
HERE = Path(__file__).resolve().parents[1] / 'tk16'
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(HERE.parent /'tk13'))
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parent /'tk12')); sys.path.insert(0, str(HERE.parent / 'shared'))
from wlib import W, sha
from probe_worlds import probe_ssx3, resource_records
from relayout_stream import assemble_archive
from inspect_disc import Region
import gamecube_game_dir as gdir
from gamecube_scenery import instance_gameplay, post_countdown_hidden
from gamecube_collision import read_tricky_collision
from course_route import ssx3_paths
from ps2mdr import encode_model, encode_instance_tail
from ps2col import encode_collision, read_collision
from gamecube_collision import partition
from tricky_pbd import PBD
from ps2swz import swizzle8, csm1

gdir.WORLD_EXTENSIONS = ('sdb', 'ssb', 'phm', 'psm')
KIND_ORDER = [0, 6, 7, 2, 3, 4, 5, 1, 8, 12, 11, 13, 15, 17, 20, 14, 16, 18, 21, 22]
VU_LIMIT, VMAX, SMAX = 217, 71, 127


# ------------------------------------------------------------------ math
def mat4(rows):
    return [list(r) for r in rows]


def mmul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def xform(p, m, w=1.0):
    return [p[0] * m[0][j] + p[1] * m[1][j] + p[2] * m[2][j] + w * m[3][j] for j in range(3)]


def norm3(v):
    l = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / l for x in v]


# ------------------------------------------------------------------ models
def object_matrices(model):
    """Composed row-vector matrix per object (None = identity chain)."""
    out = []
    for o in model['objects']:
        m = None
        if o['matrix'] is not None:
            m = mat4([o['matrix'][0:4], o['matrix'][4:8], o['matrix'][8:12], o['matrix'][12:16]])
        if o['parent'] != -1:
            pm = out[o['parent']]
            if pm is not None:
                m = mmul(m, pm) if m is not None else pm
        out.append(m)
    return out


def rechunk(strips):
    """strips: list of lists of vertex tuples (uv, nrm, pos). Greedy pack into SSX 3 chunks."""
    parts = []
    for s in strips:
        # split long strips at even offsets (keeps the winding parity) with a 2-vertex overlap
        cap = 60
        while len(s) > cap:
            parts.append(s[:cap]); s = s[cap - 2:]
        parts.append(s)
    chunks, cur = [], []

    def cost(cs, base):
        return base + 2 + len(cs) + 3 * sum(len(x) for x in cs)
    for s in parts:
        base = 27 if not chunks else 0
        if cur and (cost(cur + [s], base) > VU_LIMIT or sum(len(x) for x in cur) + len(s) > VMAX):
            chunks.append(cur); cur = []
        cur.append(s)
    if cur:
        chunks.append(cur)
    out = []
    for k, cs in enumerate(chunks):
        assert cost(cs, 27 if k == 0 else 0) <= VU_LIMIT and all(len(s) <= SMAX for s in cs)
        verts = [v for s in cs for v in s]
        out.append(dict(strips=[(len(s), 0) for s in cs], uv=[v[0] for v in verts], nrm=[v[1] for v in verts],
                        pos=[v[2] for v in verts]))
    return out


STUB = bytes.fromhex('4c554e001400000024000000240000002aff0000ecffffff000000000200000000000000')


def boost_program(b, M3):
    """PS2 Luno class program: push keyed args (op 40 PUSHB key=A value=B; op 38 PUSHF key=A + LE float), CALLBUILTIN
    dest 2, builtin 7 (Boost), argc; RETNIL. Keys (TK21 decode of builtin 7 / node ctor 0x341388): 0 instance (default =
    the instance running the class), 1 mode (int), 2 duration s (x frame rate), 3 (stock 0.9), 4 amount, 5-7 direction
    (normalised; mode 2 = instance frame with the amount negated). The donor direction goes through the placement M3."""
    d = b['dir']
    w = [sum(M3[r][c] * d[c] for c in range(3)) for r in range(3)]
    n = math.sqrt(sum(x * x for x in w)) or 1.0
    w = [x / n for x in w]
    code = bytearray()
    code += bytes([40, 1, int(b.get('mode', 1)), 0]); argc = 1
    for key, val in ((2, b.get('duration')), (3, b.get('key3', 0.9)), (4, b['amount']), (5, w[0]), (6, w[1]), (7, w[2])):
        if val is None: continue
        code += bytes([38, key, 0, 0]) + struct.pack('<f', float(val)); argc += 1
    code += bytes([33, 2, 7, argc]) + bytes([42, 0xff, 0, 0])
    code_end = 16 + len(code); data_end = code_end + 16
    return struct.pack('<4I', 0x4e554c, code_end, data_end, data_end) + bytes(code) + struct.pack('<iIII', -code_end, 0, 3, argc)


def _pose_point(p, pz):
    """TK21: object-local pose (dx, dy, dz, rx, ry, rz degrees; Tricky anim channels) applied before the object matrix."""
    dx, dy, dz, rx, ry, rz = pz
    x, y, z = p
    for ang, ax in ((rx, 0), (ry, 1), (rz, 2)):
        if not ang: continue
        c, s_ = math.cos(math.radians(ang)), math.sin(math.radians(ang))
        if ax == 0: y, z = c * y - s_ * z, s_ * y + c * z
        elif ax == 1: x, z = c * x + s_ * z, -s_ * x + c * z
        else: x, y = c * x - s_ * y, s_ * x + c * y
    return [x + dx, y + dy, z + dz]


def convert_model(pbd, model, tex_material, pose=None):
    """-> dict(scale, bbox, meshes=[dict(texture, chunks)]) or raises ValueError(reason)."""
    geo = [(i, o) for i, o in enumerate(model['objects']) if o['meshes']]
    if not geo:
        raise ValueError('no geometry')
    mats = object_matrices(model)
    composed = any(mats[i] is not None for i, _ in geo) or bool(pose)
    sx, sy, sz = [s or 1.0 for s in model['scale']]
    raw = []  # (texture, [strip vertex lists as float pos, int nrm, int uv])
    block = pbd.material_blocks[model['material_block']]
    for i, o in geo:
        m = mats[i]
        for me in o['meshes']:
            tex = pbd.materials[block[me['material_slot']]]['texture']
            strips = []
            for ch in pbd.mesh_chunks(me):
                k = 0
                for ln, _ in ch['strips']:
                    n = ln // 3
                    vs = []
                    for j in range(k, k + n):
                        p = (ch['pos'][j][0] / 32768 * sx, ch['pos'][j][1] / 32768 * sy, ch['pos'][j][2] / 32768 * sz)
                        nr = ch['nrm'][j]
                        if pose and i in pose:
                            p = _pose_point(p, pose[i])
                            nr = [int(round(x * 32767)) for x in norm3(_pose_point([c / 32767 for c in nr], (0, 0, 0) + tuple(pose[i][3:])))]
                        if m is not None:
                            p = xform(p, m)
                            nr = [int(round(x * 32767)) for x in norm3(xform([c / 32767 for c in nr], m, 0.0))]
                        vs.append((ch['uv'][j], tuple(nr), p))
                    k += n
                    strips.append(vs)
            raw.append((tex, strips))
    # quantize positions: keep the donor scale when nothing was composed (exact ints)
    if composed:
        ext = [max(abs(v[2][a]) for _, ss in raw for s in ss for v in s) or 1.0 for a in range(3)]
        scale = [e * 32767 / 32768 * 1.0001 for e in ext]
    else:
        scale = [sx, sy, sz]
    meshes_by_tex = defaultdict(list)
    for tex, strips in raw:
        qs = [[(uv, nr, tuple(max(-32768, min(32767, int(round(p[a] / scale[a] * 32768)))) for a in range(3)))
               for uv, nr, p in s] for s in strips]
        meshes_by_tex[tex] += qs
    lo = [min(v[2][a] for _, ss in raw for s in ss for v in s) for a in range(3)]
    hi = [max(v[2][a] for _, ss in raw for s in ss for v in s) for a in range(3)]
    meshes = [dict(texture=t, chunks=rechunk(ss)) for t, ss in meshes_by_tex.items()]
    return dict(scale=scale, bbox=lo + hi, meshes=meshes, composed=composed,
                animated=any(o['anim'] for o in model['objects']))


# ------------------------------------------------------------------ textures
def ssh_entries(ssh):
    n = struct.unpack_from('<I', ssh, 8)[0]
    return [struct.unpack_from('<I', ssh, 20 + 8 * j)[0] for j in range(n)]


def world_texture(ssh, off, tex_max=None):
    """Donor SSH entry -> SSX 3 kind-9 payload (8-bit indexed + 0x21 palette). TK16: tex_max halves the texel grid
    (nearest, every other texel/row) while a side exceeds it and the half stays swizzlable (w % 16, h % 4)."""
    typ, nxt = ssh[off], int.from_bytes(ssh[off + 1:off + 4], 'little')
    w, h = struct.unpack_from('<HH', ssh, off + 4)
    if typ != 2:
        raise ValueError(f'texture type {typ}')
    pix = ssh[off + 16:off + 16 + w * h]
    po = off + nxt
    ptyp, pw, ph = ssh[po], *struct.unpack_from('<HH', ssh, po + 4)
    if ptyp != 0x21 or pw * ph != 256:
        raise ValueError('palette')
    nat = ssh[po + 16:po + 16 + 1024]
    # SSX 3 world textures (measured on stock ARA1, tex_probe.py): 8-bit texels in the PSMT8 swizzled layout and the
    # CLUT in CSM1 order; Tricky's SSH holds linear texels and a natural-order CLUT.
    while tex_max and max(w, h) > tex_max and (w // 2) % 16 == 0 and (h // 2) % 4 == 0:
        pix = b''.join(pix[r * w:(r + 1) * w:2] for r in range(0, h, 2)); w, h = w // 2, h // 2
    if w % 16 or h % 4:
        raise ValueError(f'texture {w}x{h} not swizzlable')
    pix = swizzle8(pix, w, h)
    pal = bytearray(1024)
    for i in range(256):
        pal[4 * csm1(i):4 * csm1(i) + 4] = nat[4 * i:4 * i + 4]
    pal = bytes(pal)
    reserved = struct.pack('<I', 0x80) + bytes(108)
    img = bytearray(struct.pack('<B3sHHHHI', 2, (16 + 112 + len(pix)).to_bytes(3, 'little'), w, h, 0, 0, 0x3000))
    img += reserved + pix + bytes(-len(pix) % 16)
    pblk = struct.pack('<B3sHHHHI', 0x21, b'\0\0\0', 255, 1, 255, 0, 0x3000) + reserved + pal
    struct.pack_into('<I', img, 0, 2 | (len(img) << 8))
    return bytes(img) + pblk


# ------------------------------------------------------------------ helpers
def colour(nrm, inst, light_scale):
    L = inst['light']
    n = [c / 32767 for c in nrm]
    rgb = list(inst['colors'][3][:3])
    for k in range(3):
        l = (L[k], L[4 + k], L[8 + k])
        d = n[0] * l[0] + n[1] * l[1] + n[2] * l[2]
        if d > 0:
            for c in range(3):
                rgb[c] += d * inst['colors'][k][c]
    q = [max(0, min(31, int(v * light_scale / 8))) for v in rgb]
    return 0x8000 | q[2] << 10 | q[1] << 5 | q[0]


def segment_box(a, b, lo, hi):
    t0, t1 = 0., 1.
    for k in range(3):
        d = b[k] - a[k]
        if abs(d) < 1e-12:
            if not lo[k] <= a[k] <= hi[k]: return False
        else:
            x, y = sorted(((lo[k] - a[k]) / d, (hi[k] - a[k]) / d))
            t0, t1 = max(t0, x), min(t1, y)
            if t0 > t1: return False
    return True


def reset_segments(aip):
    out = []
    for i, path in enumerate(ssx3_paths(aip)[0]):
        if not struct.unpack_from('<I', path, 24)[0]: continue
        a = struct.unpack_from('<3f', path, 36)
        for j in range(struct.unpack_from('<I', path, 28)[0]):
            *v, d = struct.unpack_from('<4f', path, 72 + 16 * j)
            b = tuple(a[k] + v[k] * d for k in range(3))
            out.append((i, a, b)); a = b
    return out


# ------------------------------------------------------------------ TK16 collision helpers
def rewind(source):
    """Per triangle, keep the authored normal and pick the index order the encoder's reversal makes consistent."""
    out = []
    for m in source:
        V, tris = m['vertices'], []
        for (a, b, c), n in zip(m['triangles'], m['normals']):
            A, B, C = V[a], V[c], V[b]  # the encoder writes (a, c, b)
            u = [B[k] - A[k] for k in range(3)]; v = [C[k] - A[k] for k in range(3)]
            cr = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
            tris.append((a, c, b) if sum(x * y for x, y in zip(cr, n)) > 0 else (a, b, c))
        out.append(dict(m, triangles=tris))
    return out


def _cross(u, v):
    return (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def box_mesh(lo, hi):
    P = [(x, y, z) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]
    c = [(lo[k] + hi[k]) / 2 for k in range(3)]
    quads = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    tris = []
    for q in quads:
        for t in ((q[0], q[1], q[2]), (q[0], q[2], q[3])):
            n = _cross(_sub(P[t[1]], P[t[0]]), _sub(P[t[2]], P[t[0]]))
            if _dot(n, _sub(P[t[0]], c)) < 0: t = (t[0], t[2], t[1])
            tris.append(t)
    return _mesh(P, tris)


def _mesh(P, tris):
    used = sorted({i for t in tris for i in t}); remap = {i: k for k, i in enumerate(used)}
    verts = [tuple(P[i]) + (1.,) for i in used]
    T, N = [], []
    for a, b, c in tris:
        n = _cross(_sub(P[b], P[a]), _sub(P[c], P[a])); l = math.sqrt(_dot(n, n))
        if l == 0: continue
        T.append((remap[a], remap[b], remap[c])); N.append((n[0] / l, n[1] / l, n[2] / l, 0.))
    return dict(vertices=verts, triangles=T, normals=N)


def convex_hull(points):
    """Incremental 3D hull; returns a Tricky-convention mesh (outward normal = (b-a)x(c-a)) or None if degenerate."""
    P = sorted({(round(p[0], 3), round(p[1], 3), round(p[2], 3)) for p in points})
    if len(P) < 4: return None
    ext = max(max(p[k] for p in P) - min(p[k] for p in P) for k in range(3))
    eps = max(ext, 1.0) * 1e-6
    i0 = 0; i1 = max(range(len(P)), key=lambda i: _dot(_sub(P[i], P[i0]), _sub(P[i], P[i0])))
    d01 = _sub(P[i1], P[i0])
    i2 = max(range(len(P)), key=lambda i: _dot(c := _cross(d01, _sub(P[i], P[i0])), c))
    n012 = _cross(d01, _sub(P[i2], P[i0]))
    i3 = max(range(len(P)), key=lambda i: abs(_dot(n012, _sub(P[i], P[i0]))))
    if abs(_dot(n012, _sub(P[i3], P[i0]))) <= eps * math.sqrt(_dot(n012, n012)) * 10: return None
    cen = tuple(sum(P[i][k] for i in (i0, i1, i2, i3)) / 4 for k in range(3))
    faces = {}

    def add(a, b, c):
        n = _cross(_sub(P[b], P[a]), _sub(P[c], P[a]))
        if _dot(n, _sub(P[a], cen)) < 0: a, b, c = a, c, b; n = (-n[0], -n[1], -n[2])
        l = math.sqrt(_dot(n, n)) or 1.0
        faces[(a, b, c)] = ((n[0] / l, n[1] / l, n[2] / l), _dot(n, P[a]) / l)
    for t in ((i0, i1, i2), (i0, i1, i3), (i0, i2, i3), (i1, i2, i3)): add(*t)
    order = sorted(set(range(len(P))) - {i0, i1, i2, i3}, key=lambda i: -_dot(_sub(P[i], cen), _sub(P[i], cen)))
    for i in order:
        p = P[i]
        vis = [f for f, (n, d) in faces.items() if _dot(n, p) - d > eps]
        if not vis: continue
        edges = {}
        for a, b, c in vis:
            for e in ((a, b), (b, c), (c, a)): edges[e] = edges.get(e, 0) + 1
        horizon = [e for e in edges if (e[1], e[0]) not in edges]
        for f in vis: del faces[f]
        for a, b in horizon:
            n = _cross(_sub(P[b], P[a]), _sub(p, P[a])); l = math.sqrt(_dot(n, n)) or 1.0
            faces[(a, b, i)] = ((n[0] / l, n[1] / l, n[2] / l), _dot(n, P[a]) / l)
    return _mesh(P, list(faces))


def model_points(cm):
    sc = cm['scale']
    return [tuple(q[a] / 32768 * sc[a] for a in range(3)) for m in cm['meshes'] for ch in m['chunks'] for q in ch['pos']]


# ------------------------------------------------------------------ TK16b riding-surface rule
# A reset corridor touching an instance box protects it (GC rule) UNLESS the
# corridor rides on the instance's own collision surface (a highway deck, a
# bridge): every informative corridor sample inside the box has collision below
# it within tolerance and no collision above it. Merqury's deck flies 458-562
# above its slab (§6 guessed ~150 from the box top, which sits 224 above the
# corridor at the far end of the deck: too tight, the deck would stay
# protected). Samples beside the mesh (no surface above or below) carry no
# information and are skipped; at least one sample must decide.
SURFACE_TOL = 600.0


def _tri_world(meshes, B):
    out = []
    for m in meshes:
        V = [xform(v[:3], B) for v in m['vertices']]
        for a, b, c in m['triangles']:
            out.append((V[a], V[b], V[c]))
    return out


def candidate_world_tris(gp, inst, B, collisions, models):
    """World-space collision triangles for a solid-candidate instance (mirrors the encode path below)."""
    if gp['collision_mode'] == 1:
        src = rewind(collisions[gp['collision_or_physics']])
        if len([pp for mm in src for pp in partition(mm)]) != 1:
            src, _ = hull_or_box([v[:3] for mm in src for v in mm['vertices']])
        return _tri_world(src, B)
    src, _ = hull_or_box(model_points(models[inst['model']]))
    return _tri_world(src, B)


def _seg_tri(p, q, a, b, c):
    e1 = [b[k] - a[k] for k in range(3)]; e2 = [c[k] - a[k] for k in range(3)]; d = [q[k] - p[k] for k in range(3)]
    h = (d[1] * e2[2] - d[2] * e2[1], d[2] * e2[0] - d[0] * e2[2], d[0] * e2[1] - d[1] * e2[0])
    det = sum(e1[k] * h[k] for k in range(3))
    if abs(det) < 1e-9: return False
    f = 1 / det; s = [p[k] - a[k] for k in range(3)]; u = f * sum(s[k] * h[k] for k in range(3))
    if u < 0 or u > 1: return False
    qq = (s[1] * e1[2] - s[2] * e1[1], s[2] * e1[0] - s[0] * e1[2], s[0] * e1[1] - s[1] * e1[0])
    v = f * sum(d[k] * qq[k] for k in range(3))
    if v < 0 or u + v > 1: return False
    t = f * sum(e2[k] * qq[k] for k in range(3))
    return 0 <= t <= 1


def _clip_t(a, b, lo, hi):
    t0, t1 = 0., 1.
    for k in range(3):
        d = b[k] - a[k]
        if abs(d) < 1e-12:
            if not lo[k] <= a[k] <= hi[k]: return None
        else:
            x, y = sorted(((lo[k] - a[k]) / d, (hi[k] - a[k]) / d))
            t0, t1 = max(t0, x), min(t1, y)
            if t0 > t1: return None
    return t0, t1


def _surface_clearances(tris, p):
    """(clearance below, clearance above) from point p to collision surfaces covering its (x, y); None where absent."""
    x, y, z = p
    below, above = None, None
    for A_, B_, C_ in tris:
        d = ((B_[1] - C_[1]) * (A_[0] - C_[0]) + (C_[0] - B_[0]) * (A_[1] - C_[1]))
        if abs(d) < 1.0: continue  # degenerate in plan (walls)
        u = ((B_[1] - C_[1]) * (x - C_[0]) + (C_[0] - B_[0]) * (y - C_[1])) / d
        v = ((C_[1] - A_[1]) * (x - C_[0]) + (A_[0] - C_[0]) * (y - C_[1])) / d
        if u < 0 or v < 0 or u + v > 1: continue
        e1 = [B_[k] - A_[k] for k in range(3)]; e2 = [C_[k] - A_[k] for k in range(3)]
        n = _cross(e1, e2)
        if abs(n[2]) < 0.05 * math.sqrt(_dot(n, n)): continue  # near-vertical
        sz = A_[2] - (n[0] * (x - A_[0]) + n[1] * (y - A_[1])) / n[2]
        if sz <= z:
            if below is None or z - sz < below: below = z - sz
        else:
            if above is None or sz - z < above: above = sz - z
    return below, above


def corridor_rides_surface(tris, corridors, box_lo, box_hi, tol=SURFACE_TOL):
    """True when every reset-corridor sample inside the box rides on the instance's own surface."""
    segs = []
    for _, a, b in corridors:
        c = _clip_t(a, b, box_lo, box_hi)
        if c is not None: segs.append((a, b, c[0], c[1]))
    if not segs: return False
    for a, b, t0, t1 in segs:  # through-volume: a corridor leg pierces a triangle
        p = [a[k] + (b[k] - a[k]) * t0 for k in range(3)]
        q = [a[k] + (b[k] - a[k]) * t1 for k in range(3)]
        if any(_seg_tri(p, q, *t) for t in tris): return False
    pts = []
    for a, b, t0, t1 in segs:
        n = max(1, int(math.dist(a, b) * (t1 - t0) / 100))
        for j in range(n + 1):
            t = t0 + (t1 - t0) * j / n
            pts.append([a[k] + (b[k] - a[k]) * t for k in range(3)])
    decided = 0
    for p in pts:
        below, above = _surface_clearances(tris, p)
        if below is None and above is None: continue  # beside the mesh: no information
        decided += 1
        if above is not None and above <= tol: return False  # structure overhead: the leg runs under it
        if below is None or below > tol: return False  # no surface under the leg: not a riding surface
    return decided > 0


def hull_or_box(points):
    h = convex_hull(points)
    if h is not None and 0 < len(h['vertices']) <= 256 and h['triangles']:
        return [h], 'hull'
    lo = [min(p[k] for p in points) for k in range(3)]; hi = [max(p[k] for p in points) for k in range(3)]
    for k in range(3):
        if hi[k] - lo[k] < 1.0: lo[k] -= 0.5; hi[k] += 0.5
    return [box_mesh(lo, hi)], 'box'


def main():
    ap = argparse.ArgumentParser()
    for a in ('base', 'experiment', 'pbd', 'ssh', 'nbd', 'gsf', 'stem', 'out'):
        ap.add_argument(a)
    ap.add_argument('--no-collision', action='store_true')
    ap.add_argument('--no-textures', action='store_true', help='bind every material to one host texture')
    ap.add_argument('--light-scale', type=float, default=1.0)
    ap.add_argument('--max-instances', type=int)
    ap.add_argument('--def-class', type=lambda x: int(x, 0), default=0x108, help='definition word 2 (behaviour class oid; stock commonest 0x108)')
    ap.add_argument('--no-bindings', action='store_true', help='bisect: leave the kind-16 binding table disabled')
    ap.add_argument('--no-instances', action='store_true', help='bisect: models/materials/textures only; kind 16 untouched')
    ap.add_argument('--only-models', help='bisect: comma list of donor model ids to place')
    ap.add_argument('--stock-control', help='bisect: place ONE stock ARA1 instance (rid 0) + its model from this BAM.BIG instead of donor scenery')
    ap.add_argument('--mesh-u1', default='first1', choices=['all1', 'first1', 'all0'], help='mesh header u1 pattern')
    ap.add_argument('--stock-rid', type=int, default=0, help='--stock-control: the stock instance rid to copy')
    ap.add_argument('--no-collision-instance', type=int, action='append', default=[], help='fixture A/B: keep this donor instance non-colliding')
    ap.add_argument('--receipt')
    ap.add_argument('--tmp', default=str(Path.home() / 'dev/ssx3-work/TK16/tmp'))
    ap.add_argument('--collision-route', help='TK16: route file (x y [z] per line); with --collision-radius, solids only near it')
    ap.add_argument('--collision-radius', type=float, help='TK16: keep collision only for instances whose world AABB is within this of the route')
    ap.add_argument('--drop-instances', help='TK16: file of donor instance indices to leave out (course-group budget, budget_drop.py)')
    ap.add_argument('--corridor-top-exempt', type=float, help='TK16 (tricky profile): a reset corridor passing within this of the top of an instance box or above it does not protect it (riding surfaces: highways, bridges)')
    ap.add_argument('--tex-max', type=int, help='TK16: halve donor textures while a side exceeds this (texture group budget)')
    ap.add_argument('--pose', action='append', default=[], help='TK21: MODEL:T pose the donor model at its animation time T (all instances)')
    ap.add_argument('--no-collision-file', help='TK21: file of donor instance indices kept non-colliding')
    ap.add_argument('--boost-json', help='TK21 Part 2: JSON list of {instances, mode, amount, dir (donor frame), key3, flags}: import those donor volumes as kind-2 triggers bound to a Boost class program written into a stub script slot')
    ap.add_argument('--profile', choices=['gc', 'tricky'], default='tricky', help='solid set (see the docstring)')
    args = ap.parse_args()
    if args.no_collision_file:
        args.no_collision_instance += [int(x) for x in open(args.no_collision_file).read().split()]
    poses = {}
    boosts = json.loads(Path(args.boost_json).read_text()) if args.boost_json else []
    boost_of = {i: k for k, b in enumerate(boosts) for i in b['instances']}
    args.no_collision_instance += list(boost_of)  # volumes are triggers, never solid
    if args.pose:
        from anim_dump import curves, value
        _pd = Path(args.pbd).read_bytes(); _pp = PBD(_pd)
        for spec in args.pose:
            mi, t = spec.split(':'); mi, t = int(mi), float(t)
            CH = {1: 0, 2: 1, 4: 2, 8: 3, 16: 4, 32: 5}
            poses[mi] = {}
            for ob, ch in curves(_pd, _pp, mi).items():
                v = [0.0] * 6
                for bit, rows in ch.items():
                    v[CH[bit]] = value(rows, min(t, rows[-1][5]))
                poses[mi][ob] = tuple(v)

    exp = json.loads(Path(args.experiment).read_text())
    G, T = exp['group'], exp['track']
    M3, TR = exp['matrix'], exp['translation']
    Pm = [[M3[0][0], M3[1][0], M3[2][0], 0], [M3[0][1], M3[1][1], M3[2][1], 0],
          [M3[0][2], M3[1][2], M3[2][2], 0], [TR[0], TR[1], TR[2], 1]]  # row-vector form of t = M s + T
    base_bytes = Path(args.base).read_bytes()
    Path(args.tmp).mkdir(parents=True, exist_ok=True)
    tmp = Path(args.tmp) / (Path(args.out).parent.name + '-' + Path(args.out).stem + '-bam.BIG')
    bam_bytes = gdir.rename_world_members(base_bytes, 'bam')
    tmp.write_bytes(bam_bytes)
    world = W(tmp)
    loc = next(l for l in world.locations() if l['index'] == T)
    groups = world.sdb['groups']
    # pinned texture group = the page ref every patch uses
    recs = world.records(G)
    pages = Counter(struct.unpack_from('<I', p, 340)[0] for e, p in recs if e['kind'] == 1)
    page = pages.most_common(1)[0][0]
    TG = page >> 16
    if not (loc['group_start'] <= TG < loc['last_group']):
        raise SystemExit(f'page group {TG} not in the location')

    # TK21: stub script slots for the Boost class programs (first stubs large enough, in slot order, from slot 3)
    _sc = next(p for e, p in recs if e['kind'] == 16)
    _pc, _pt, _pe = struct.unpack_from('<3I', _sc, 56); _st = struct.unpack_from(f'<{_pc}I', _sc, _pt) + (_pe,)
    _free = [k for k in range(3, _pc) if _st[k + 1] - _st[k] >= 112 and _sc[_st[k]:_st[k] + 36] == STUB]
    if len(boosts) > len(_free): raise SystemExit('not enough stub slots')
    boost_slot = _free[:len(boosts)]
    pbd = PBD(Path(args.pbd).read_bytes())
    ssh = Path(args.ssh).read_bytes()
    gsf = Path(args.gsf).read_bytes()
    # GC gameplay rows (visibility incl. the post-countdown NoCountDown set, collision) keyed by instance index; the
    # GC NBD instance table must name the same models as the PS2 pbd (the full GC scenery reader is not needed and
    # rejects courses without particle models).
    nbd = Path(args.nbd).read_bytes()
    hdr = struct.unpack_from('>40I', nbd)
    gc_models = [struct.unpack_from('>I', nbd, hdr[18] + 140 * i + 64)[0] for i in range(hdr[3])]
    if gc_models != [i['model'] for i in pbd.instances]:
        raise SystemExit('PS2/GC instance tables disagree')
    rows = instance_gameplay(gsf, len(gc_models))
    for i in post_countdown_hidden(gsf, len(gc_models)):
        rows[i]['visible'] = False
    gc = type('GC', (), {})(); gc.instances = [dict(gameplay=r) for r in rows]
    collisions = read_tricky_collision(gsf)

    # ---- select instances + convert models
    skipped, models, order = Counter(), {}, []
    bad_models = {}
    dropped = {int(x) for x in open(args.drop_instances).read().split()} if args.drop_instances else set()
    for i, inst in enumerate(pbd.instances):
        gp = gc.instances[i]['gameplay']
        if not gp['visible'] and i not in boost_of:
            skipped['hidden'] += 1; continue
        if i in dropped:
            skipped['dropped: budget'] += 1; continue
        mi = inst['model']
        if args.only_models and mi not in {int(x) for x in args.only_models.split(',')}:
            continue
        if mi in bad_models:
            skipped['model: ' + bad_models[mi]] += 1; continue
        if mi not in models:
            try:
                models[mi] = convert_model(pbd, pbd.models[mi], None, poses.get(mi))
            except (ValueError, AssertionError) as ex:
                bad_models[mi] = str(ex)[:40]; skipped['model: ' + bad_models[mi]] += 1; continue
        order.append(i)
        if args.max_instances and len(order) >= args.max_instances:
            break

    # ---- records of the terrain group (drop host models; keep everything else)
    host_model_max = max((e['rid'] for e, p in recs if e['kind'] == 2), default=-1)
    kept = [(e, p) for e, p in recs if e['kind'] != 2]
    assert not any(e['kind'] in (3, 12) for e, p in kept), 'base still has kind 3/12'
    mat_next = max(e['rid'] for e, p in recs if e['kind'] == 0) + 1
    # textures: global ids above every kind-9 id in the world
    tex_max = -1
    for g in groups:
        for e, p in world.records(g['index']):
            if e['kind'] == 9: tex_max = max(tex_max, e['rid'])
    tex_recs, mat_recs, tex_rid, mat_rid = [], [], {}, {}
    host_tex = struct.unpack_from('<h', next(p for e, p in recs if e['kind'] == 0), 0)[0]
    offs = ssh_entries(ssh)

    def material_for(tidx):
        if tidx not in mat_rid:
            if args.no_textures:
                rid9 = host_tex
            else:
                if tidx not in tex_rid:
                    tex_rid[tidx] = tex_max + 1 + len(tex_rid)
                    tex_recs.append((dict(kind=9, track=255, rid=tex_rid[tidx]), world_texture(ssh, offs[tidx], args.tex_max)))
                rid9 = tex_rid[tidx]
            mat_rid[tidx] = mat_next + len(mat_rid)
            mat_recs.append((dict(kind=0, track=T, rid=mat_rid[tidx]),
                             struct.pack('<10h', rid9, -1, -1, 0, 0, 0, 1, 1, -1, -1)))
        return mat_rid[tidx]

    oid = lambda rid: rid << 8 | T
    model_recs, model_rid, layouts = [], {}, {}
    for mi in sorted({pbd.instances[i]['model'] for i in order}):
        cm = models[mi]
        mats = [oid(material_for(m['texture'])) for m in cm['meshes']]
        rid = host_model_max + 1 + len(model_rid)
        meshes = [dict(material=k, u1={'all1': 1, 'all0': 0, 'first1': int(k == 0)}[args.mesh_u1], chunks=m['chunks']) for k, m in enumerate(cm['meshes'])]
        data, lay = encode_model(oid(rid), cm['scale'], mats, meshes, cm['bbox'], u4=1)
        model_rid[mi] = rid; layouts[mi] = (lay, cm)
        model_recs.append((dict(kind=2, track=T, rid=rid), data))

    # ---- instances
    aip = next(p for e, p in recs if e['kind'] == 14 and p[:4] == b'iiii')
    corridors = reset_segments(aip)
    near_pts = None
    if args.collision_radius is not None:
        rt = [tuple(map(float, l.split())) for l in open(args.collision_route) if l.strip()]
        near_pts = []
        for a, b in zip(rt, rt[1:]):
            n = max(1, int(math.dist(a[:3], b[:3]) / 300))
            near_pts += [tuple(a[k] + (b[k] - a[k]) * j / n for k in range(3)) for j in range(n)]
        near_pts.append(rt[-1][:3])
    inst_recs, defs, def_ids, bindings, col_recs, col_ids = [], [], {}, [], [], {}
    enabled, protected, nonuniform = [], [], []
    solid_classes = Counter()
    for new_id, i in enumerate([] if args.no_instances else order):
        inst = pbd.instances[i]; gp = gc.instances[i]['gameplay']
        A = mat4(inst['matrix'])
        B = mmul(A, Pm)
        axes = [B[k][:3] for k in range(3)]
        lens = [math.sqrt(sum(x * x for x in a)) for a in axes]
        s = sum(lens) / 3
        ortho = all(abs(l / s - 1) < 1e-4 for l in lens) and all(
            abs(sum(axes[a][k] * axes[b][k] for k in range(3))) < 1e-4 * s * s for a in range(3) for b in range(a))
        if not ortho:
            nonuniform.append(i)
        R = [[B[r][c] / s for c in range(3)] + [0.0] for r in range(3)] + [B[3][:3] + [1.0]]
        # world bounds: the donor instance box through the placement
        lo, hi = inst['low'], inst['high']
        corners = [xform((x, y, z), Pm) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]
        if inst['model'] in poses:  # TK21: union with the posed model box through the instance and the placement
            bb = models[inst['model']]['bbox']
            corners += [xform(xform((x, y, z), A), Pm) for x in (bb[0], bb[3]) for y in (bb[1], bb[4]) for z in (bb[2], bb[5])]
        wlo = [min(c[k] for c in corners) for k in range(3)]; whi = [max(c[k] for c in corners) for k in range(3)]
        ctr = [(a + b) / 2 for a, b in zip(wlo, whi)]; rad = math.dist(wlo, whi) / 2
        lay, cm = layouts[inst['model']]
        cols = [[(c, k == 0, [colour(n, inst, args.light_scale) for n in c['nrm']]) for k, c in enumerate(m['chunks'])]
                for m in cm['meshes']]
        tail = encode_instance_tail(lay, cols)
        head = bytearray(160)
        struct.pack_into('<16f', head, 16, *[v for r in R for v in r])
        struct.pack_into('<4f3f3f', head, 80, *ctr, rad, *wlo, *whi)
        struct.pack_into('<3If2I2h3I', head, 120, oid(new_id), page, oid(model_rid[inst['model']]), s, 0, 0, -1, 0, 0, 160, 0)
        inst_recs.append((dict(kind=3, track=T, rid=new_id), bytes(head) + tail))
        # gameplay definition
        col = None
        if i in args.no_collision_instance:
            skipped['collision: fixture control'] += 1
        elif near_pts is not None and gp['player_collision'] and min(
                math.sqrt(sum(max(wlo[k] - q[k], 0, q[k] - whi[k]) ** 2 for k in range(3))) for q in near_pts) > args.collision_radius:
            skipped['collision: beyond radius'] += 1
        elif not args.no_collision and args.profile == 'tricky' and gp['player_collision'] and ortho and (
                gp['collision_mode'] == 1 or (gp['collision_mode'] == 3 and gp['immovable'])):
            box_lo = [wlo[0] - 35, wlo[1] - 35, wlo[2] - 150]; box_hi = [whi[0] + 35, whi[1] + 35, whi[2] + 35]
            if args.corridor_top_exempt is not None:
                box_hi[2] = whi[2] - args.corridor_top_exempt
            hit = next((pi for pi, a, b in corridors if segment_box(a, b, box_lo, box_hi)), None)
            rides = False
            if hit is not None:  # TK16b: the corridor may ride on this surface (deck, bridge) instead of crossing it
                try:
                    rides = corridor_rides_surface(candidate_world_tris(gp, inst, B, collisions, models),
                                                   corridors, box_lo, box_hi)
                except (ValueError, AssertionError):
                    rides = False
                if rides:
                    skipped['collision: corridor rides surface'] += 1
                else:
                    protected.append(dict(source=i, instance=new_id, reset_path=hit)); skipped['collision: reset corridor'] += 1
            if hit is not None and not rides:
                pass
            else:
                if gp['collision_mode'] == 1:
                    key = ('mesh', gp['collision_or_physics'])
                else:
                    key = ('model', inst['model'])
                if key not in col_ids:
                    if key[0] == 'mesh':
                        src = rewind(collisions[key[1]]); how = 'mesh'
                        if len(encode_parts := [pp for mm in src for pp in partition(mm)]) != 1:
                            src, how = hull_or_box([v[:3] for mm in src for v in mm['vertices']]); how = 'multipart ' + how
                    else:
                        src, how = hull_or_box(model_points(models[key[1]])); how = 'render ' + how
                    try:
                        data = encode_collision(src); read_collision(data)
                        assert struct.unpack_from('<H', data, 2)[0] == 1
                        col_ids[key] = (len(col_recs), how); col_recs.append((dict(kind=12, track=T, rid=len(col_recs)), data))
                    except (ValueError, AssertionError) as ex:
                        col_ids[key] = (None, 'invalid: ' + str(ex)[:30])
                col, how = col_ids[key]
                cls = f'mode {gp["collision_mode"]} effect {"Y" if gp["effect_slot"] != -1 else "N"} {how}'
                solid_classes[cls] += 1
                if col is None:
                    skipped['collision: ' + how] += 1
                else:
                    enabled.append(dict(source=i, instance=new_id, collision=col, source_collision=list(key), how=how))
        elif not args.no_collision and args.profile == 'gc' and gp['player_collision'] and gp['collision_mode'] == 1 and gp['effect_slot'] == -1 and ortho \
                and not models[inst['model']]['composed']:
            box_lo = [wlo[0] - 35, wlo[1] - 35, wlo[2] - 150]; box_hi = [whi[0] + 35, whi[1] + 35, whi[2] + 35]
            hit = next((pi for pi, a, b in corridors if segment_box(a, b, box_lo, box_hi)), None)
            if hit is not None:
                protected.append(dict(source=i, instance=new_id, reset_path=hit)); skipped['collision: reset corridor'] += 1
            else:
                ci = gp['collision_or_physics']
                if ci not in col_ids:
                    try:
                        data = encode_collision(collisions[ci]); read_collision(data)
                        parts = struct.unpack_from('<H', data, 2)[0]
                        if parts != 1:
                            col_ids[ci] = None
                        else:
                            col_ids[ci] = len(col_recs); col_recs.append((dict(kind=12, track=T, rid=len(col_recs)), data))
                    except ValueError:
                        col_ids[ci] = None
                col = col_ids[ci]
                if col is None:
                    skipped['collision: multipart/invalid'] += 1
                else:
                    enabled.append(dict(source=i, instance=new_id, collision=col, source_collision=ci))
        elif gp['player_collision'] and not args.no_collision:
            skipped[f'collision: mode {gp["collision_mode"]} effect {"Y" if gp["effect_slot"] != -1 else "N"} immovable {gp["immovable"]} ortho {ortho}'] += 1
        if i in boost_of:  # TK21: stock Boost trigger definition shape (C_CHP2/EHP3): kind 2, no collision handle, part (0, 0, 2, -1)
            b = boosts[boost_of[i]]
            d = struct.pack('<4I', 2, int(str(b.get('flags', '0x200000')), 0), boost_slot[boost_of[i]] << 8 | T, 0xffffffff) + struct.pack('<2f2h', 0., 0., 2, -1)
            col = None
        elif col is not None:
            d = struct.pack('<4I', 1, 0x00210000, args.def_class, oid(col)) + struct.pack('<2f2h', 1e30, 0., 3, -1)
        else:
            d = struct.pack('<4I', 0, 0x00010000, args.def_class, 0xffffffff) + struct.pack('<2f2h', 1e30, 0., 0, -1)
        if d not in def_ids:
            def_ids[d] = len(defs); defs.append(d)
        bindings.append(def_ids[d])

    if args.stock_control:
        sw = W(args.stock_control); srec = sw.records(G)
        sm = {e['rid']: p for e, p in srec if e['kind'] == 2}
        si = next(p for e, p in srec if e['kind'] == 3 and e['rid'] == args.stock_rid)
        mh = struct.unpack_from('<I', si, 128)[0] >> 8
        mp = bytearray(sm[mh]); struct.pack_into('<I', mp, 0, oid(host_model_max + 1))
        ip = bytearray(si); struct.pack_into('<I', ip, 124, page); struct.pack_into('<I', ip, 128, oid(host_model_max + 1))
        model_recs = [(dict(kind=2, track=T, rid=host_model_max + 1), bytes(mp))]
        inst_recs = [(dict(kind=3, track=T, rid=0), bytes(ip))]
        mat_recs, tex_recs, col_recs = [], [], []
        defs = [struct.pack('<4I', 0, 0x00010000, args.def_class, 0xffffffff) + struct.pack('<2f2h', 1e30, 0., 0, -1)]
        bindings = [0]; enabled = []

    # ---- kind 16
    (se, script), = [(e, p) for e, p in kept if e['kind'] == 16]
    base = struct.unpack_from('<I', script, 64)[0]
    count, table, ndefs, offsets, nspl, spl_at = struct.unpack_from('<6I', script, 68)
    if count != 0:
        raise SystemExit('binding table not disabled in base')
    if not args.no_instances and not args.no_bindings:
        data = bytearray(script[:base]); doffs = []
        boost_rc = []
        for k, b in enumerate(boosts):  # TK21: write each Boost class program into its stub slot
            slot = boost_slot[k]
            pc, ptab, pend = struct.unpack_from('<3I', data, 56)
            st = struct.unpack_from(f'<{pc}I', data, ptab) + (pend,)
            blob = boost_program(b, M3)
            if bytes(data[st[slot]:st[slot] + 36]) != STUB or len(blob) > st[slot + 1] - st[slot]:
                raise SystemExit(f'slot {slot} not a stub or too small')
            data[st[slot]:st[slot + 1]] = blob + bytes(st[slot + 1] - st[slot] - len(blob))
            boost_rc.append(dict(slot=slot, class_word=hex(slot << 8 | T), bytes=len(blob), blob=blob.hex(), **{k2: v for k2, v in b.items() if k2 != 'instances'}, instances=len(b['instances'])))
        for d in defs:
            doffs.append(len(data) - base); data += d
        inst_at = len(data); data += struct.pack('<%dH' % len(bindings), *bindings); data += bytes(-len(data) % 4)
        def_at = len(data); data += struct.pack('<%dI' % len(doffs), *doffs)
        new_spl = len(data); data += script[spl_at:spl_at + 4 * nspl]
        struct.pack_into('<6I', data, 68, len(bindings), inst_at, len(defs), def_at, nspl, new_spl)
        if len(script) > spl_at + 4 * nspl:
            data += script[spl_at + 4 * nspl:]
        kept = [(dict(e, size=len(data)), bytes(data)) if e['kind'] == 16 else (e, p) for e, p in kept]

    # ---- assemble the terrain group in the stock kind order
    allrecs = kept + mat_recs + model_recs + inst_recs + col_recs
    buckets = defaultdict(list)
    for e, p in allrecs:
        buckets[e['kind']].append((e, p))
    new = []
    for k in KIND_ORDER + sorted(set(buckets) - set(KIND_ORDER)):
        new += sorted(buckets.get(k, []), key=lambda r: r[0]['rid'])
    raw = b''.join(bytes([e['kind']]) + len(p).to_bytes(3, 'little') + bytes([e['track']]) + e['rid'].to_bytes(3, 'little') + p
                   for e, p in new)
    raws = {g['index']: world.raw(g['index']) for g in groups}
    raws[G] = raw
    if tex_recs:
        raws[TG] = world.raw(TG) + b''.join(bytes([9]) + len(p).to_bytes(3, 'little') + bytes([255]) +
                                            e['rid'].to_bytes(3, 'little') + p for e, p in tex_recs)

    # ---- SDB: group counts/memsize, location capacities, global texture capacity
    sdb = bytearray(world.sdb_bytes)
    lc, nodes, _ = struct.unpack_from('<III', sdb, 8)
    gtab = (80 + 88 * lc + 15) // 16 * 16 + nodes * 96

    def set_group(gi, rows):
        o = gtab + 68 * gi
        struct.pack_into('<H', sdb, o, len(rows))
        struct.pack_into('<I', sdb, o + 8, sum(len(p) + 8 for e, p in rows if e['kind'] <= 12))
        c = Counter(e['kind'] for e, p in rows)
        for k in range(14):
            struct.pack_into('<H', sdb, o + 12 + 2 * k, c.get(k, 0))
    before = bytes(sdb[gtab + 68 * TG:gtab + 68 * TG + 68])
    set_group(TG, list(resource_records(world.raw(TG))))
    if bytes(sdb[gtab + 68 * TG:gtab + 68 * TG + 68]) != before:
        raise SystemExit('group record rewrite does not reproduce the stock record')
    set_group(G, new)
    if tex_recs:
        set_group(TG, list(resource_records(raws[TG])))
    lrec = 80 + 88 * loc['index'] + 32
    caps = list(struct.unpack_from('<28h', sdb, lrec))
    for k in (0, 2, 3, 12):
        rows = [e['rid'] for e, p in new if e['kind'] == k]
        caps[k] = max(caps[k], (max(rows) + 1) if rows else 0)
    struct.pack_into('<28h', sdb, lrec, *caps)
    if tex_recs:
        gc9 = struct.unpack_from('<h', sdb, 24 + 2 * 9)[0]
        need = max(tex_rid.values()) + 1
        if need > 32767: raise SystemExit('texture capacity overflow')
        struct.pack_into('<h', sdb, 24 + 2 * 9, max(gc9, need))
    report = probe_ssx3(Region(io.BytesIO(bam_bytes), 0, len(bam_bytes)))
    bam, layout, _ = assemble_archive(bam_bytes, report, raws, bytes(sdb), jobs=6, reuse_original_blocks=True)
    out = gdir.rename_world_members(bam, args.stem)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_bytes(out)
    back = W(args.out)
    ok = all(back.raw(g['index']) == raws[g['index']] for g in groups)
    rc = dict(base=args.base, base_sha256=sha(base_bytes), out=args.out, out_sha256=sha(out), out_bytes=len(out),
              group=G, texture_group=TG, page=hex(page), readback=ok,
              donor=dict(pbd_sha256=sha(Path(args.pbd).read_bytes()), gsf_sha256=sha(gsf), instances=len(pbd.instances)),
              imported=dict(instances=len(inst_recs), models=len(model_recs), materials=len(mat_recs),
                            textures=len(tex_recs), collision_resources=len(col_recs), collision_instances=len(enabled),
                            definitions=len(defs), host_models_dropped=host_model_max + 1,
                            composed_models=sorted(mi for mi in model_rid if models[mi]['composed']),
                            animated_as_static=sorted(mi for mi in model_rid if models[mi]['animated'])),
              skipped=dict(skipped), bad_models=bad_models, nonuniform_instances=nonuniform,
              protected=protected, group_resources=len(new),
              group_memsize=sum(len(p) + 8 for e, p in new if e['kind'] <= 12),
              texture_ids=[min(tex_rid.values()), max(tex_rid.values())] if tex_rid else None,
              chunks=sum(len(m['chunks']) for mi in model_rid for m in models[mi]['meshes']),
              instance_source=order, collision_enabled=enabled, boosts=locals().get('boost_rc'), poses={str(k): {str(o): [round(x, 3) for x in v] for o, v in d.items()} for k, d in poses.items()}, profile=args.profile, solid_classes=dict(solid_classes))
    if args.receipt:
        Path(args.receipt).write_text(json.dumps(rc, indent=1))
    print(json.dumps({k: v for k, v in rc.items() if k not in ('instance_source', 'collision_enabled', 'protected')}, indent=1))


if __name__ == '__main__':
    main()
