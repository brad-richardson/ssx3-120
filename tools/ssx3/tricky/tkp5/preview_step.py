#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later OR MIT
"""TKP5 (lane copy of TK52 preview_step.py; only the course table grows): replace a Tricky course's host COURSEPIC
pixels from original Tricky PS2LOAD art, for every course in COURSES (host member from the TK56 host-loc table).

Preserve the complete current COURSPIT directory and every SSH header/name.
Inputs are read twice; only pixel and CLUT bytes change. Worlds and their
compact scenery texture handles remain unchanged. No generated data in git.
"""
import argparse, hashlib, json, struct, sys
from pathlib import Path
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT /'shared'), str(ROOT / 'tk9vestigial')]
from inspect_disc import Region, iso_files, big_members
from inventory_tricky import decode_member

def sha(b): return hashlib.sha256(b).hexdigest()
def twice(p):
    p=Path(p);a=p.read_bytes();b=p.read_bytes()
    assert a==b, f'input changed: {p}'
    return a, {'path':str(p),'read1':sha(a),'read2':sha(b)}
def layout(d):
    assert d[:4]==b'SHPS' and struct.unpack_from('<I',d,8)[0]==1
    o=struct.unpack_from('<I',d,20)[0]
    assert d[o]==2 and struct.unpack_from('<HH',d,o+4)==(256,256)
    pal=o+int.from_bytes(d[o+1:o+4],'little')
    assert d[pal]==0x21 and struct.unpack_from('<HH',d,pal+4)==(256,1)
    return o+16,pal+16

COURSES={'gari':('ARA1.ssh','ldtrackgari.ssh'),'snow':('BRA2.ssh','ldtracksnow.ssh'),'mesa':('CRA3.ssh','ldtrackmesa.ssh'),
         'merq':('ERA5.ssh','ldtrackmerq.ssh'),'aloha':('ABC1.ssh','ldtrackaloh.ssh'),'elys':('EBC3.ssh','ldtrackelys.ssh'),
         'alaska':('DSS2.ssh','ldtrackalas.ssh'),'pipe':('BHP1.ssh','ldtrackpipe.ssh'),'untrack':('ESS3.ssh','ldtrackuntr.ssh'),
         'mega':('DBC2.ssh','ldtracktoky.ssh')}

def csm1(i):
    return (i & 0xe7) | ((i & 0x10) >> 1) | ((i & 0x08) << 1)

def rgba_csm1(d):
    """Decode SSX3 SSH pixels and CSM1 CLUT, expanding PS2 alpha to RGBA8."""
    po,pa=layout(d)
    colors=[d[pa+4*csm1(i):pa+4*csm1(i)+4] for i in range(256)]
    return b''.join(bytes((c[0],c[1],c[2],min(255,c[3]*2)))
                    for c in (colors[i] for i in d[po:po+65536]))

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--courspit',required=True);ap.add_argument('--donor-iso',required=True)
    ap.add_argument('--course',choices=sorted(COURSES),required=True)
    ap.add_argument('--out',required=True);ap.add_argument('--receipt',required=True)
    a=ap.parse_args();data,inp=twice(a.courspit)
    target,donor=COURSES[a.course]
    with Path(a.donor_iso).open('rb') as f:
        iso=Region(f,0,Path(a.donor_iso).stat().st_size);_,files=iso_files(iso)
        entry=next(e for e in files if e['path']=='DATA/TEXTURES/PS2LOAD.BIG')
        reg=iso.child(entry['offset'],entry['size']);_,entries=big_members(reg)
        e=next(e for e in entries if e['path']==donor)
        src,_=decode_member(reg,e);src2,_=decode_member(reg,e);assert src==src2
    import io
    _,members=big_members(Region(io.BytesIO(data),0,len(data)))
    m=next(m for m in members if m['path'].casefold()==target.casefold())
    off=m['offset'];size=m['size'];tpl=data[off:off+size]
    po,pa=layout(tpl);so,sa=layout(src);repl=bytearray(tpl)
    repl[po:po+65536]=src[so:so+65536]
    # Tricky SSH uses a natural-order CLUT; SSX3 COURSEPIC uses CSM1.
    for i in range(256):
        repl[pa+4*csm1(i):pa+4*csm1(i)+4]=src[sa+4*i:sa+4*i+4]
    out=bytearray(data);out[off:off+size]=repl
    dst=Path(a.out);dst.parent.mkdir(parents=True,exist_ok=True);dst.write_bytes(out)
    back,outsha=twice(dst);assert back==out
    preserved=all(back[n['offset']:n['offset']+n['size']]==data[n['offset']:n['offset']+n['size']] for n in members if n!=m)
    header_ok=repl[:po]==tpl[:po] and repl[po+65536:pa]==tpl[po+65536:pa] and repl[pa+1024:]==tpl[pa+1024:]
    assert preserved and header_ok
    donor_csm1=bytearray(src)
    for i in range(256):
        donor_csm1[sa+4*csm1(i):sa+4*csm1(i)+4]=src[sa+4*i:sa+4*i+4]
    donor_rgba=rgba_csm1(donor_csm1)
    output_rgba=rgba_csm1(back[off:off+size])
    assert output_rgba==donor_rgba, 'rendered donor/output RGBA differs'
    receipt={'course':a.course,'input':inp,'output':outsha,'donor_iso':a.donor_iso,
             'donor_archive':entry,'donor_member':e,'donor_decoded_sha_read1':sha(src),'donor_decoded_sha_read2':sha(src2),
             'target':target,'texture_id_before':tpl[16:20].hex(),'texture_id_after':repl[16:20].hex(),
             'other_members_identical':preserved,'ssh_metadata_identical':header_ok,
             'directory_identical':back[:min(n['offset'] for n in members)]==data[:min(n['offset'] for n in members)],
             'palette_conversion':'natural to CSM1: swap index bits 3/4',
             'rendered_rgba_equal_donor':output_rgba==donor_rgba,
             'donor_rgba_sha256':sha(donor_rgba),'output_rgba_sha256':sha(output_rgba),
             'differing_rgba_pixels':sum(output_rgba[i:i+4]!=donor_rgba[i:i+4] for i in range(0,len(output_rgba),4))}
    Path(a.receipt).write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps(receipt,indent=2))
if __name__=='__main__':main()
