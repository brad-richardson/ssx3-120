# SPDX-License-Identifier: GPL-3.0-or-later OR MIT
"""TK13: SSX Tricky PS2 course world (.pbd): header, instances, materials, models and mesh geometry.

Header/record layouts: GlitcherOG/SSX-Library PBDHandler.cs (GPL-3; read as a format reference only).
Mesh geometry is decoded here by walking the DMA chain from each mesh's StartPos (relative to the
mesh-data base) and simulating the VIF UNPACKs (STCYCL CL/WL write stride), not by fixed offsets.

Tricky chunk (measured on gari.pbd): cnt tag -> STCYCL; UNPACK V4-32 x2 (GIF tag + (strips,0,verts,0));
STCYCL; UNPACK V2-8 strip lengths (x3: one entry per interleaved qword triple); ref tag (1 qw, shared);
cnt tag -> STMASK; STCYCL CL=3 WL=1; UNPACK V2-16 UV at A, V3-16 normals at A+1, V3-16 xyz at A+2
(stride 3 qwords per vertex). Positions are int16/32768 * model scale (as in SSX 3), normals int16
unit*32767, UV int16/4096.
"""
import struct
from ps2mdr import vif_ops

HEADER_COUNTS = ['player_starts', 'patches', 'instances', 'particle_instances', 'materials', 'material_blocks',
                 'lights', 'splines', 'spline_segments', 'texture_flipbooks', 'models', 'particle_models',
                 'textures', 'cameras', 'lightmap_size']
HEADER_OFFS = ['player_start', 'patch', 'instance', 'particle_instance', 'material', 'material_block', 'light',
               'spline', 'spline_segment', 'texture_flipbook', 'model_pointer', 'model', 'particle_model_pointer',
               'particle_model', 'camera_pointer', 'camera', 'hash', 'mesh_data']


class PBD:
    def __init__(self, data):
        self.d = d = data
        self.counts = dict(zip(HEADER_COUNTS, struct.unpack_from('<15I', d, 4)))
        self.offs = dict(zip(HEADER_OFFS, struct.unpack_from('<18I', d, 64)))
        n = self.counts['instances']
        nxt = self.offs['particle_instance'] or self.offs['material']  # no particle table on some courses
        self.instance_size = 256  # PBDHandler.cs instance record; tables are 16-aligned
        assert 0 <= nxt - self.offs['instance'] - 256 * n < 16, (nxt, n)
        self.instances = [self._instance(i) for i in range(n)]
        self.materials = [self._material(i) for i in range(self.counts['materials'])]
        self.material_blocks = self._material_blocks()
        self.models = [self._model(i) for i in range(self.counts['models'])]

    # ---- records
    def _instance(self, i):
        d, o = self.d, self.offs['instance'] + i * self.instance_size
        m = struct.unpack_from('<16f', d, o)
        light = struct.unpack_from('<16f', d, o + 64)
        cols = [struct.unpack_from('<4f', d, o + 128 + 16 * k) for k in range(4)]
        model, prev, nxt = struct.unpack_from('<3i', d, o + 192)
        lo, hi = struct.unpack_from('<3f', d, o + 204), struct.unpack_from('<3f', d, o + 216)
        u26, u27, u28, model2, u30, u31, u32 = struct.unpack_from('<7i', d, o + 228)
        return dict(matrix=[m[0:4], m[4:8], m[8:12], m[12:16]], light=light, colors=cols, model=model,
                    prev=prev, next=nxt, low=lo, high=hi, u26=u26, u27=u27, u28=u28, model2=model2,
                    u30=u30, u31=u31, u32=u32)

    def _material(self, i):
        d, o = self.d, self.offs['material'] + 72 * i
        tex, u2 = struct.unpack_from('<2h', d, o)
        flip, u20 = struct.unpack_from('<2h', d, o + 68)
        return dict(texture=tex, u2=u2, u3=struct.unpack_from('<I', d, o + 4)[0],
                    f=struct.unpack_from('<4f', d, o + 8), u8=struct.unpack_from('<I', d, o + 24)[0],
                    f2=struct.unpack_from('<4f', d, o + 28), ints=struct.unpack_from('<6I', d, o + 44),
                    flipbook=flip, u20=u20)

    def _material_blocks(self):
        d, o, out = self.d, self.offs['material_block'], []
        for _ in range(self.counts['material_blocks']):
            n = struct.unpack_from('<I', d, o)[0]
            out.append(list(struct.unpack_from('<%dI' % n, d, o + 4))); o += 4 + 4 * n
        return out

    def _model(self, i):
        d = self.d
        ptr = struct.unpack_from('<I', d, self.offs['model_pointer'] + 4 * i)[0]
        b = self.offs['model'] + ptr
        total, nobj, objoff, mblock, u3 = struct.unpack_from('<5I', d, b)
        anim = struct.unpack_from('<f', d, b + 20)[0]
        scale = struct.unpack_from('<3f', d, b + 24)
        tmesh, vcount, tscount, u4, nontri = struct.unpack_from('<5I', d, b + 36)
        objs = []
        for a in range(nobj):
            o = b + objoff + 24 * a
            par, hi, me, lo, an, mx = struct.unpack_from('<6i', d, o)
            ob = dict(parent=par, anim=an != 0, matrix=None, meshes=[])
            if mx != -1:
                ob['matrix'] = struct.unpack_from('<16f', d, o + mx)
            if hi not in (0, -1):
                h = o + hi
                ob['bbox'] = struct.unpack_from('<6f', d, h + 4)
                ob['flags'], mc, ob['faces'] = struct.unpack_from('<3I', d, h + 28)
                mo = h + 44 + 4 * mc
                for k in range(mc):
                    el, mbp, mlen, start, l1, l2, l3 = struct.unpack_from('<7I', d, mo + 28 * k)
                    ob['meshes'].append(dict(material_slot=mbp, start=start, length=mlen))
            objs.append(ob)
        return dict(index=i, scale=scale, material_block=mblock, anim=anim, objects=objs, vertex_count=vcount,
                    tristrip_count=tscount, mesh_count=tmesh, u3=u3, u4=u4, nontri=nontri)

    # ---- geometry
    def mesh_chunks(self, mesh):
        """Walk the mesh's DMA chain; return a list of chunk dicts (strips, uv, nrm, pos int16)."""
        d, base = self.d, self.offs['mesh_data']
        q, end_q = base + mesh['start'], base + mesh['start'] + mesh['length']
        ops = []
        guard = 0
        while q < end_q:
            guard += 1
            assert guard < 10000
            tag, addr = struct.unpack_from('<2I', d, q)
            qwc, tid = tag & 0xffff, (tag >> 28) & 7
            vif_tag = vif_ops(d, q + 8, q + 16)
            if tid == 1:      # cnt: data follows the tag
                ops += vif_tag + vif_ops(d, q + 16, q + 16 + 16 * qwc); q += 16 + 16 * qwc
            elif tid == 3:    # ref: data at addr (mesh-data relative), shared; not geometry
                ops += vif_tag; q += 16
            elif tid in (0, 6, 7):  # refe / ret / end
                break
            else:
                raise ValueError('unexpected DMA tag id %d' % tid)
        return self._chunks_from_ops(ops)

    @staticmethod
    def _chunks_from_ops(ops):
        chunks, cur, cl, wl = [], None, 1, 1
        for cmd, num, imm, pl in ops:
            if cmd == 0x01:
                cl, wl = imm & 0xff, (imm >> 8) & 0xff
                continue
            if cmd & 0x60 != 0x60:
                continue
            fmt, n = cmd & 0x6f, num or 256
            if fmt == 0x6c and n == 2:
                if cur: chunks.append(cur)
                s, _, v, _ = struct.unpack_from('<4I', pl, 16)
                cur = dict(nstrips=s, verts=v, strips=None, uv=None, v3=[], gif=pl[:16])
            elif fmt == 0x66:
                cur['strips'] = [struct.unpack_from('<2b', pl, 2 * i) for i in range(n)]
            elif fmt == 0x65:
                cur['uv'] = [struct.unpack_from('<2h', pl, 4 * i) for i in range(n)]
                cur['stride'] = cl if wl < cl else 1
            elif fmt == 0x69:
                cur['v3'].append([struct.unpack_from('<3h', pl, 6 * i) for i in range(n)])
        if cur: chunks.append(cur)
        out = []
        for c in chunks:
            assert len(c['v3']) == 2 and c['uv'] is not None, 'chunk layout'
            nrm, pos = c['v3']
            n = c['verts']
            assert len(pos) == len(nrm) == len(c['uv']) == n
            out.append(dict(strips=c['strips'], nstrips=c['nstrips'], uv=c['uv'], nrm=nrm, pos=pos,
                            stride=c['stride']))
        return out
