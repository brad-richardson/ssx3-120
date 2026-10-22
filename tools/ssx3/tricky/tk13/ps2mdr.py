# SPDX-License-Identifier: GPL-3.0-or-later OR MIT
"""TK13: SSX 3 PS2 world models (kind 2, "MDR"), instances (kind 3) and the VIF chunks they carry.

Layouts measured on stock BAM.BIG ARA1 (group 33) and cross-read against GlitcherOG/SSX-Library
WorldMDR.cs / WorldInstance.cs (GPL-3, read as a format reference only; nothing copied).

Model (kind 2), little-endian:
  +0 handle rid<<8|track; +4 object count; +8 object table offset; +12 40; +16 u4; +20 anim time f32;
  +24 scale xyz (positions are int16/32768 * scale); +36 chunk data base (DMA ref addresses are relative
  to it); +40 material count; +44 material handles (kind 0).
  object (16 B): parent (-1), geometry block offset, s3 offset (0), matrix offset (-1).
  geometry block: bbox low/high (24 B), +24 u0, +28 mesh count, +32 mesh table offset; mesh table: u32
  offsets of 8-byte mesh headers (i16 material index, i16 u1, u24 chain offset (rel. data base), u8 0).
  chain: 16-B DMA tags: (ref qwc, addr) pairs (geometry, normals) per chunk, then 0x60000000 (ret).
Chunk VIF (geometry ref): [first chunk only: STCYCL 1,1; STMOD 0; STROW 1,1,1,1] ITOP 0;
  UNPACK V4-32 x2 at B (GIF tag + (strips, 0, verts, 0)); UNPACK V2-8 (len, 0) strip lengths at B+2;
  STMASK 0x50505050; UNPACK V2-16 masked UV at B+3+n; STMASK 0x40404040; UNPACK V3-16 masked xyz at B+3.
  (B = 0x1b for the first chunk, 0 after; all addresses with FLG.)
Normals ref: STMASK 0x40404040; UNPACK V3-16 masked normals at B+3+2n; STMASK 0; 0xDEADBEEF
  (patched by the game at load); ITOP 1.
Instance (kind 3) tail at +152: one DMA chain PER MESH (geometry ref copied from the model, colour ref
  relative to the tail, per chunk; then ret), all chains first, then per chunk: NOP; STCYCL 1,1; NOP; UNPACK V4-5 colours at B+3+2n;
  pad; STMASK 0; 0xDEADBEEF; ITOP 1.
"""
import struct

VL_BITS = {0: 32, 1: 16, 2: 8, 3: 16}


def unpack_bytes(cmd, num):
    num = num or 256
    vn, vl = (cmd >> 2) & 3, cmd & 3
    if vl == 3:  # V4-5: one 16-bit word per element
        bits = 16
    else:
        bits = (vn + 1) * VL_BITS[vl]
    return (bits * num + 31) // 32 * 4


def vif_ops(buf, start, end):
    """Decode VIF codes in buf[start:end] -> list of (cmd, num, imm, payload bytes)."""
    q, out = start, []
    while q < end:
        c = struct.unpack_from('<I', buf, q)[0]
        cmd, num, imm = c >> 24, (c >> 16) & 0xff, c & 0xffff
        q += 4
        if cmd & 0x60 == 0x60:
            n = unpack_bytes(cmd, num)
            out.append((cmd, num, imm, buf[q:q + n])); q += n
        elif cmd == 0x20:
            out.append((cmd, num, imm, buf[q:q + 4])); q += 4
        elif cmd in (0x30, 0x31):
            out.append((cmd, num, imm, buf[q:q + 16])); q += 16
        else:
            out.append((cmd, num, imm, b''))
    return out


def i16s(b, n, comps):
    return [struct.unpack_from('<%dh' % comps, b, 2 * comps * i) for i in range(n)]


def parse_chunk(geom_ops, norm_ops):
    """-> dict(strips=[len..], uv=[(u,v)], pos=[(x,y,z)] int16, nrm=[(x,y,z)] int16, base, first)."""
    unp = [(c & 0x6f, n, imm, pl) for c, n, imm, pl in geom_ops if c & 0x60 == 0x60]
    first = any(c == 0x01 for c, *_ in geom_ops)
    (c0, n0, a0, gif), (c1, n1, a1, st), (c2, n2, a2, uv), (c3, n3, a3, xyz) = unp
    assert (c0, n0, c1, c2, c3) == (0x6c, 2, 0x66, 0x65, 0x69), (hex(c0), n0, hex(c1), hex(c2), hex(c3))
    strips_n, _, verts, _ = struct.unpack_from('<4I', gif, 16)
    lens = [struct.unpack_from('<2b', st, 2 * i) for i in range(n1)]
    # strip count vs unpack count
    nu = [(c & 0x6f, n, imm, pl) for c, n, imm, pl in norm_ops if c & 0x60 == 0x60]
    (c4, n4, a4, nr), = nu
    assert c4 == 0x69 and n2 == n3 == n4 == verts
    return dict(first=first, base=a0 & 0x3ff, gif=gif[:16], strips=lens, nstrips=strips_n, verts=verts,
                uv=i16s(uv, n2, 2), pos=i16s(xyz, n3, 3), nrm=i16s(nr, n4, 3),
                addr=dict(strips=a1 & 0x3ff, uv=a2 & 0x3ff, pos=a3 & 0x3ff, nrm=a4 & 0x3ff))


def parse_model(p):
    hid, nobj, objoff, u3, u4 = struct.unpack_from('<5I', p, 0)
    anim = struct.unpack_from('<f', p, 20)[0]
    scale = struct.unpack_from('<3f', p, 24)
    base, nmat = struct.unpack_from('<2I', p, 36)
    mats = list(struct.unpack_from('<%dI' % nmat, p, 44))
    objs = []
    for i in range(nobj):
        par, s2, s3, mx = struct.unpack_from('<4i', p, objoff + 16 * i)
        o = dict(parent=par, s3=s3, matrix=mx, meshes=[])
        if s2 > 0:
            o['bbox'] = struct.unpack_from('<6f', p, s2)
            o['u0'], ac, ao = struct.unpack_from('<3I', p, s2 + 24)
            for a in range(ac):
                ho = struct.unpack_from('<I', p, ao + 4 * a)[0]
                mat, mu1 = struct.unpack_from('<2h', p, ho)
                t = struct.unpack_from('<I', p, ho + 4)[0]
                q, tags = base + (t & 0xffffff), []
                while True:
                    tag, addr = struct.unpack_from('<2I', p, q); q += 16
                    tags.append((tag & 0xffff, tag >> 24, addr))
                    if tag >> 24 == 0x60: break
                o['meshes'].append(dict(material=mat, u1=mu1, hflag=t >> 24, tags=tags))
        objs.append(o)
    return dict(handle=hid, u3=u3, u4=u4, anim=anim, scale=scale, base=base, materials=mats, objects=objs)


def model_chunks(p, m=None):
    """Yield (object index, mesh index, chunk dict) for simple (pair-chained) meshes."""
    m = m or parse_model(p)
    for oi, o in enumerate(m['objects']):
        for mi, me in enumerate(o['meshes']):
            tags = me['tags']
            if len(tags) % 2 != 1 or any(t[1] != 0x30 for t in tags[:-1]):
                raise ValueError('non-pair chain')
            for k in range(0, len(tags) - 1, 2):
                g, n = tags[k], tags[k + 1]
                go = vif_ops(p, m['base'] + g[2], m['base'] + g[2] + 16 * g[0])
                no = vif_ops(p, m['base'] + n[2], m['base'] + n[2] + 16 * n[0])
                yield oi, mi, parse_chunk(go, no)


# ---------------------------------------------------------------- encoder
def _q(b):
    return b + bytes(-len(b) % 16)


def _unpack_word(cmd, num, addr):
    return struct.pack('<I', cmd << 24 | num << 16 | 0x8000 | addr)


def chunk_geometry(c, first):
    """VIF bytes of a chunk's geometry ref (see module doc). c: strips [(len,0)], uv, pos."""
    n, s = len(c['pos']), len(c['strips'])
    B = 0x1b if first else 0
    out = b''
    if first:
        out += struct.pack('<4I', 0x01000101, 0x05000000, 0, 0x30000000) + struct.pack('<4f', 1, 1, 1, 1)
    gif = struct.pack('<2I2I', 0x8000 | n, 0x302e4000, 0x412, 0) + struct.pack('<4I', s, 0, n, 0)
    out += struct.pack('<3I', 0, 0x04000000, 0) + _unpack_word(0x6c, 2, B) + gif
    out += struct.pack('<3I', 0, 0, 0) + _unpack_word(0x66, s, B + 2) + _q(b''.join(struct.pack('<2b', *x) for x in c['strips']))
    P = B + 2 + s
    out += struct.pack('<3I', 0x20000000, 0x50505050, 0) + _unpack_word(0x75, n, P + n) + _q(b''.join(struct.pack('<2h', *x) for x in c['uv']))
    out += struct.pack('<3I', 0x20000000, 0x40404040, 0) + _unpack_word(0x79, n, P) + _q(b''.join(struct.pack('<3h', *x) for x in c['pos']))
    return out


FOOTER = struct.pack('<4I', 0x20000000, 0, 0xDEADBEEF, 0x04000001)


def chunk_normals(c, first):
    n, s = len(c['pos']), len(c['strips'])
    B = 0x1b if first else 0
    P = B + 2 + s
    return struct.pack('<3I', 0x20000000, 0x40404040, 0) + _unpack_word(0x79, n, P + 2 * n) + \
        _q(b''.join(struct.pack('<3h', *x) for x in c['nrm'])) + FOOTER


def chunk_colors(c, first, colors):
    n, s = len(c['pos']), len(c['strips'])
    B = 0x1b if first else 0
    P = B + 2 + s
    return struct.pack('<3I', 0, 0x01000101, 0) + _unpack_word(0x6f, n, P + 2 * n) + \
        _q(struct.pack('<%dH' % n, *colors)) + FOOTER


def encode_model(handle, scale, materials, meshes, bbox, u4=0, u0=0, mesh_u1=0, anim=0.0):
    """meshes: [dict(material=i, chunks=[chunk dict])]; single object, no matrix. -> bytes, layout
    (per mesh chunk list of (geom qwc, geom addr) for instance tails)."""
    nmat = len(materials)
    objoff = 44 + 4 * nmat
    s2 = (objoff + 16 + 15) & ~15
    table = s2 + 36
    hdrs = table + 4 * len(meshes)
    base = (hdrs + 8 * len(meshes) + 15) & ~15
    chains = [16 * (2 * len(m['chunks']) + 1) for m in meshes]
    geo_start = sum(chains)
    geo, nrm = [], []
    for m in meshes:
        for k, c in enumerate(m['chunks']):
            geo.append(chunk_geometry(c, k == 0)); nrm.append(chunk_normals(c, k == 0))
    ga, pos = [], geo_start
    for g in geo: ga.append(pos); pos += len(g)
    na = []
    for g in nrm: na.append(pos); pos += len(g)
    body = bytearray(); layout = []; ci = 0; chain_offs = []
    for m in meshes:
        chain_offs.append(len(body)); lay = []
        for c in m['chunks']:
            body += struct.pack('<4I', len(geo[ci]) // 16 | 0x30000000, ga[ci], 0, 0)
            body += struct.pack('<4I', len(nrm[ci]) // 16 | 0x30000000, na[ci], 0, 0)
            lay.append((len(geo[ci]) // 16, ga[ci])); ci += 1
        body += struct.pack('<4I', 0x60000000, 0, 0, 0)
        layout.append(lay)
    body += b''.join(geo) + b''.join(nrm)
    h = bytearray(base)
    struct.pack_into('<5If3f2I', h, 0, handle, 1, objoff, 40, u4, anim, *scale, base, nmat)
    struct.pack_into('<%dI' % nmat, h, 44, *materials)
    struct.pack_into('<4i', h, objoff, -1, s2, 0, -1)
    struct.pack_into('<6f3I', h, s2, *bbox, u0, len(meshes), table)
    for i, m in enumerate(meshes):
        struct.pack_into('<I', h, table + 4 * i, hdrs + 8 * i)
        struct.pack_into('<2hI', h, hdrs + 8 * i, m['material'], m.get('u1', mesh_u1), chain_offs[i])
    return bytes(h) + bytes(body), layout


def encode_instance_tail(layout, colors):
    """layout from encode_model; colors: per mesh, per chunk (chunk, first, [ABGR1555 words]).
    One DMA chain per mesh ((geometry ref copied from the model, colour ref relative to the tail) per
    chunk, then ret), all chains first, then the colour blocks in the same order."""
    head = sum(16 * (2 * len(lay) + 1) for lay in layout)
    blocks, tags, pos = [], b'', head
    for lay, cols in zip(layout, colors):
        for (qwc, addr), (chunk, first, col) in zip(lay, cols):
            blk = chunk_colors(chunk, first, col)
            tags += struct.pack('<4I', qwc | 0x30000000, addr, 0, 0) + struct.pack('<4I', len(blk) // 16 | 0x30000000, pos, 0, 0)
            blocks.append(blk); pos += len(blk)
        tags += struct.pack('<4I', 0x60000000, 0, 0, 0)
    return tags + b''.join(blocks)
