#!/usr/bin/env python3
"""Read GameCube Tricky scenery without assuming the PS2 mesh layout.

The NBD owns shared signed-16 positions (quarter units), signed-16 normals
(1/16384), UVs (1/4096), indexed GX strips, prefabs and instance transforms.
All relative pointers are checked against their owning model before use.
"""
import argparse
import collections
import json
import math
from pathlib import Path
import struct

from gamecube_surfaces import NBD_MAGICS


def buffer_group(position, uv, normal):
    """SSX 3 kind-23 references. Distinct IDs matter: stock often uses 0/0/0.

    GXBE69 802467E4 resolves +0 through kind 25, +8 through kind 26,
    and +4 through kind 27. The stored order is position, UV, normal.
    """
    if any(not 0 <= value <= 0xffffffff for value in (position, uv, normal)):
        raise ValueError('Buffer reference is not an unsigned object ID')
    return struct.pack('>3I', position, uv, normal)


def instance_gameplay(gsf, expected_count):
    """GC gameplay properties: flags precede U2 (the PS2 order is reversed).

    Bit 0 controls initial visibility; bit 5 enables player collision.
    Hidden trigger/physics meshes must not become ordinary visible scenery.
    Keep the collision and effect references for a separate gameplay compiler.
    """
    if len(gsf) < 76 or struct.unpack_from('>I', gsf)[0] != 0x00021e00:
        raise ValueError('Unsupported Tricky GC GSF header')
    count, start, instances, table, _, end = struct.unpack_from('>6I', gsf, 52)
    if (instances != expected_count or not 76 <= start <= table <= end <= len(gsf) or
            start+count*24 != table or table+instances*4 != end):
        raise ValueError('Invalid GSF instance/property table extents')
    properties = []
    for i in range(count):
        u0, bounce, flags, u2, surface, mode, collision, effect, u8 = struct.unpack_from('>2f2HI4h', gsf, start+24*i)
        if not math.isfinite(u0) or not math.isfinite(bounce) or mode not in (0, 1, 2, 3):
            raise ValueError('Unsupported GSF instance property')
        properties.append(dict(property_index=i, flags=flags, visible=bool(flags & 1),
                               player_collision=bool(flags & 32), surface=surface,
                               collision_mode=mode, collision_or_physics=collision, effect_slot=effect,
                               # u0 is an immovability sentinel (1e30 on 483 of 531 records);
                               # bounce is the restitution the physics classes carry.
                               immovable=u0 >= 1e29, bounce=bounce))
    result = []
    for i in range(instances):
        index = struct.unpack_from('>I', gsf, table+4*i)[0]
        if index >= count:
            raise ValueError('GSF instance refers outside property table')
        result.append(dict(properties[index]))
    return result


def post_countdown_hidden(gsf, instance_count):
    """Resolve the authored NoCountDown visibility program, not model names.

    This static race profile has no Tricky script VM. Bake the state after
    its temporary gate has been removed. Only function calls, instance calls
    and the known hide command are supported; unknown behavior fails closed.
    GC effect records carry their own byte lengths, unlike the PS2 layout.
    """
    if len(gsf) < 76:
        raise ValueError('Truncated GSF function header')
    effects, effect_table, functions, function_table, _, end = struct.unpack_from('>6I', gsf, 36)
    base = effect_table + effects*8
    if not (76 <= function_table <= effect_table <= base <= end <= len(gsf)) or function_table+24*functions != effect_table:
        raise ValueError('Invalid GSF function/effect tables')
    names = [gsf[function_table+24*i+8:function_table+24*i+24].split(b'\0')[0] for i in range(functions)]
    if names.count(b'NoCountDown') != 1:
        raise ValueError('Static race profile requires one authored NoCountDown function')
    hidden, active = set(), set()

    def visit(kind, index, instance=None):
        key = kind, index
        if key in active:
            raise ValueError('Cyclic GSF visibility program')
        limit, table, stride = (functions, function_table, 24) if kind == 'function' else (effects, effect_table, 8)
        if not 0 <= index < limit:
            raise ValueError('GSF visibility call is outside its table')
        count, relative = struct.unpack_from('>II', gsf, table+stride*index)
        pos = base+relative
        if pos < base or pos > end or count > (end-pos)//8:
            raise ValueError('Invalid GSF visibility program extent')
        active.add(key)
        for _ in range(count):
            if pos+8 > end:
                raise ValueError('Truncated GSF visibility command')
            opcode, size = struct.unpack_from('>II', gsf, pos)
            if size < 8 or pos+size > end:
                raise ValueError('Invalid GSF visibility command size')
            if opcode == 21 and size == 12:
                visit('function', struct.unpack_from('>I', gsf, pos+8)[0], instance)
            elif opcode == 7 and size == 16:
                target, effect = struct.unpack_from('>II', gsf, pos+8)
                if target >= instance_count:
                    raise ValueError('GSF visibility instance is outside scene')
                visit('effect', effect, target)
            elif opcode == 0 and size == 16 and struct.unpack_from('>II', gsf, pos+8) == (5, 2) and instance is not None:
                hidden.add(instance)
            else:
                raise ValueError(f'Unsupported post-countdown command {opcode}/{size}')
            pos += size
        active.remove(key)

    visit('function', names.index(b'NoCountDown'))
    return sorted(hidden)


class TrickyScenery:
    def __init__(self, data, gsf=None, *, phase='initial'):
        self.data = data
        if len(data) < 160 or self.u32(0) not in NBD_MAGICS:
            raise ValueError('Expected a GameCube Tricky NBD header')
        self.header = self.unpack('40I', 0)
        self.positions = self.array(self.header[34], self.header[35], '3h')
        self.uvs = self.array(self.header[35], self.header[36], '2h')
        self.normals = self.array(self.header[36], len(data), '3h')
        self.flipbooks = []
        o, end = self.header[25:27]
        for _ in range(self.header[10]):
            self.span(o, 4, self.header[25], end)
            count = self.u32(o)
            if count == 0:
                raise ValueError('Empty scenery texture flipbook')
            self.span(o+4, count*4, self.header[25], end)
            frames = self.unpack(f'{count}I', o+4)
            if any(frame >= self.header[13] for frame in frames):
                raise ValueError('Scenery flipbook image is outside texture table')
            self.flipbooks.append(frames)
            o += 4+count*4
        if self.header[10] and o != end:
            raise ValueError('Scenery flipbook table does not end at model pointers')
        self.materials = []
        for i in range(self.header[5]):
            o = self.header[20] + 72*i
            self.span(o, 72)
            # GSTE69 8012e424 reads a full word, compares -1, then replaces it
            # with a pointer from the flipbook table. +70 only happens to work
            # for small positive IDs; the PS2 halfword layout is not this ABI.
            flipbook = self.unpack('i', o+68)[0]
            if not -1 <= flipbook < len(self.flipbooks):
                raise ValueError('Scenery material refers outside flipbook table')
            self.materials.append(dict(texture=self.unpack('H', o)[0],
                                       flipbook=flipbook, raw=data[o:o+72]))
        self.material_blocks = []
        o = self.header[21]
        for _ in range(self.header[6]):
            n = self.u32(o)
            self.span(o+4, 4*n, self.header[21], self.header[22])
            block = self.unpack(f'{n}I', o+4)
            if any(m >= len(self.materials) for m in block):
                raise ValueError('Scenery material block refers outside the material table')
            self.material_blocks.append(block)
            o += 4 + 4*n
        self.models = [self.model(i) for i in range(self.header[11])]
        self.instances = []
        for i in range(self.header[3]):
            o = self.header[18] + 140*i
            self.span(o, 140, self.header[18], self.header[19])
            model = self.u32(o+64)
            if model >= len(self.models):
                raise ValueError('Scenery instance refers outside the model table')
            self.instances.append(dict(model=model, matrix=self.floats(16, o),
                                       bounds=self.floats(6, o+76), raw=data[o:o+140]))
        self.gameplay = instance_gameplay(gsf, len(self.instances)) if gsf is not None else None
        if phase not in ('initial', 'racing') or phase == 'racing' and gsf is None:
            raise ValueError('Racing scenery requires GSF gameplay data')
        self.post_countdown_hidden = post_countdown_hidden(gsf, len(self.instances)) if phase == 'racing' else []
        if self.gameplay is not None:
            for i in self.post_countdown_hidden:
                self.gameplay[i]['visible'] = False
                self.gameplay[i]['hidden_by'] = 'NoCountDown'
            for instance, gameplay in zip(self.instances, self.gameplay):
                instance['gameplay'] = gameplay

    def span(self, offset, length, low=0, high=None):
        high = len(self.data) if high is None else min(high, len(self.data))
        if length < 0 or offset < low or offset > high or length > high-offset:
            raise ValueError(f'Scenery span {offset}+{length} lies outside {low}..{high}')

    def unpack(self, fmt, offset):
        self.span(offset, struct.calcsize('>'+fmt))
        return struct.unpack_from('>'+fmt, self.data, offset)

    def u32(self, offset):
        return self.unpack('I', offset)[0]

    def floats(self, n, offset):
        values = self.unpack(f'{n}f', offset)
        if not all(math.isfinite(v) for v in values):
            raise ValueError('Nonfinite scenery transform or bounds')
        return values

    def array(self, start, end, fmt):
        size = struct.calcsize('>'+fmt)
        self.span(start, end-start)
        if (end-start) % size:
            raise ValueError('Misaligned scenery vertex array')
        return list(struct.iter_unpack('>'+fmt, self.data[start:end]))

    def strips(self, start, size):
        start += self.header[33]
        self.span(start, size, self.header[33], self.header[34])
        end, o, strips = start+size, start, []
        while o < end:
            opcode = self.data[o]
            o += 1
            if opcode == 0:  # GX display lists are padded with NOPs.
                continue
            if opcode not in (0x9a, 0x9b):
                raise ValueError(f'Unsupported scenery GX primitive {opcode:#x}')
            self.span(o, 2, start, end)
            count = self.unpack('H', o)[0]
            o += 2
            self.span(o, 6*count, start, end)
            vertices = [self.unpack('3H', o+6*i) for i in range(count)]
            for position, normal, uv in vertices:
                if position >= len(self.positions) or normal >= len(self.normals) or uv >= len(self.uvs):
                    raise ValueError('Scenery strip refers outside a vertex array')
            strips.append(dict(opcode=opcode, vertices=vertices))
            o += 6*count
        return strips

    def model(self, rid):
        table = self.header[26]
        self.span(table+4*rid, 4, table, self.header[27])
        start = self.header[27] + self.u32(table+4*rid)
        self.span(start, 28, self.header[27], self.header[28])
        length, count, objects, material, flags = self.unpack('5I', start)
        self.span(start, length, self.header[27], self.header[28])
        end = start+length
        self.span(start+objects, count*24, start, end)
        if material >= len(self.material_blocks):
            raise ValueError('Model refers outside the material-block table')
        parts = []
        for i in range(count):
            o = start+objects+24*i
            parent, high, medium, low, animation, matrix = self.unpack('6I', o)
            if parent != 0xffffffff and parent >= i:
                raise ValueError('Scenery parent must precede its child')
            part = dict(parent=parent, matrix=None, animated=animation != 0, meshes=[])
            if matrix != 0xffffffff:
                self.span(o+matrix, 64, start, end)
                part['matrix'] = self.floats(16, o+matrix)
            if high not in (0, 0xffffffff):
                h = o+high
                self.span(h, 44, start, end)
                entry_length = self.u32(h)
                self.span(h, entry_length, start, end)
                mesh_flags, mesh_count, faces, offsets = self.unpack('4I', h+28)
                self.span(h+offsets, mesh_count*4, h, h+entry_length)
                part.update(bounds=self.floats(6, h+4), flags=mesh_flags, face_count=faces)
                for j in range(mesh_count):
                    pointer = h+offsets+4*j
                    entry = pointer+self.u32(pointer)
                    self.span(entry, 16, h, h+entry_length)
                    size, material_index, dl, dl_size = self.unpack('4I', entry)
                    if size != 16 or material_index >= len(self.material_blocks[material]):
                        raise ValueError('Unsupported scenery mesh entry')
                    part['meshes'].append(dict(material=self.material_blocks[material][material_index],
                                               strips=self.strips(dl, dl_size)))
            parts.append(part)
        return dict(rid=rid, flags=flags, animation_time=self.floats(1, start+20)[0], parts=parts)

    def report(self):
        return dict(models=len(self.models), instances=len(self.instances), materials=len(self.materials),
                    flipbooks=len(self.flipbooks),
                    positions=len(self.positions), normals=len(self.normals), uvs=len(self.uvs),
                    animated_models=[m['rid'] for m in self.models if any(p['animated'] for p in m['parts'])],
                    mesh_count=sum(len(p['meshes']) for m in self.models for p in m['parts']),
                    instance_counts=dict(collections.Counter(i['model'] for i in self.instances)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('nbd', type=Path)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    result = json.dumps(TrickyScenery(args.nbd.read_bytes()).report(), indent=2)+'\n'
    if args.report:
        args.report.write_text(result)
    else:
        print(result, end='')


if __name__ == '__main__':
    main()
