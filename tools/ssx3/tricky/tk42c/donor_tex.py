#!/usr/bin/env python3
"""TK42b: trace donor instance -> model -> material block -> material -> SSH texture entry.

Usage: donor_tex.py COURSE.pbd COURSE.ssh INST [INST ...]
Prints per instance: model, material block, per-mesh material slot/material/texture/flipbook,
and per texture: SSH offset, type, WxH, pixel distinct count, palette distinct count.
"""
import struct, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tk13'))
from tricky_pbd import PBD

def ssh_entries(ssh):
    n = struct.unpack_from('<I', ssh, 8)[0]
    return [struct.unpack_from('<I', ssh, 20 + 8 * j)[0] for j in range(n)]

def main():
    pbd = PBD(open(sys.argv[1], 'rb').read())
    ssh = open(sys.argv[2], 'rb').read()
    offs = ssh_entries(ssh)
    print(f'pbd: {pbd.counts["instances"]} instances, {pbd.counts["materials"]} materials, '
          f'{pbd.counts["models"]} models, {pbd.counts["textures"]} textures; ssh entries: {len(offs)}')
    for a in sys.argv[3:]:
        i = int(a)
        inst = pbd.instances[i]
        m = pbd.models[inst['model']]
        print(f'--- donor inst {i}: model={inst["model"]} model2={inst["model2"]} mblock={m["material_block"]} '
              f'scale={tuple(round(v,3) for v in m["scale"])} nobjs={len(m["objects"])}')
        block = pbd.material_blocks[m['material_block']]
        print(f'    block[{m["material_block"]}] len={len(block)}: {block[:12]}')
        seen = set()
        for oi, ob in enumerate(m['objects']):
            for mesh in ob['meshes']:
                mi = block[mesh['material_slot']]
                mat = pbd.materials[mi]
                t = mat['texture']
                tag = (mi, t)
                extra = ''
                if tag not in seen:
                    seen.add(tag)
                    if 0 <= t < len(offs):
                        o = offs[t]
                        typ = ssh[o]
                        nxt = int.from_bytes(ssh[o+1:o+4], 'little')
                        w, h = struct.unpack_from('<HH', ssh, o+4)
                        if typ == 2 and o + 16 + w*h <= len(ssh):
                            pix = ssh[o+16:o+16+w*h]
                            extra = f' distinct_px={len(set(pix))}'
                        else:
                            extra = f' BADENTRY typ={typ} w={w} h={h}'
                        po = o + nxt
                        if po + 16 + 1024 <= len(ssh):
                            ptyp = ssh[po]
                            pw, ph = struct.unpack_from('<HH', ssh, po+4)
                            pal = ssh[po+16:po+16+1024]
                            extra += f' pal_typ={ptyp:#x} {pw}x{ph} distinct_pal={len(set(pal[i:i+4] for i in range(0,1024,4)))}'
                        extra = f' ssh_off={o:#x} typ={typ} {w}x{h}' + extra
                    else:
                        extra = f' TEXTURE {t} OUT OF RANGE (ssh has {len(offs)})'
                print(f'    obj{oi} slot={mesh["material_slot"]} mat={mi} tex={t} flip={mat["flipbook"]}{extra}')
                extra = ''

if __name__ == '__main__':
    main()
