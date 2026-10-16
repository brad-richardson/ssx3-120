#!/usr/bin/env python3
"""big.py: read-only ISO9660 + BIGF access without extracting.
  big.py ls <iso> <ISO/PATH>                   list BIGF members (name off size)
  big.py get <iso> <ISO/PATH> <member> <out>   extract one member
  big.py lsf <file>  /  getf <file> <member> <out>  same on a host BIGF file
"""
import struct,sys
def iso_find(f,path):
    f.seek(16*2048+156); rec=f.read(34)
    lba,size=struct.unpack_from('<I',rec,2)[0],struct.unpack_from('<I',rec,10)[0]
    for part in path.strip('/').upper().split('/'):
        f.seek(lba*2048); d=f.read(size); i=0; found=None
        while i<len(d):
            l=d[i]
            if l==0: i=(i//2048+1)*2048; continue
            nl=d[i+32]; n=d[i+33:i+33+nl].decode('latin1').split(';')[0]
            if n.upper()==part: found=(struct.unpack_from('<I',d,i+2)[0],struct.unpack_from('<I',d,i+10)[0]); break
            i+=l
        if not found: raise SystemExit('not found '+part)
        lba,size=found
    return lba*2048,size
def members(f,base):
    f.seek(base); h=f.read(16)
    assert h[:4] in (b'BIGF',b'BIG4'),h[:4]
    n=struct.unpack('>I',h[8:12])[0]; hl=struct.unpack('>I',h[12:16])[0]
    f.seek(base+16); d=f.read(hl); i=0; out=[]
    for _ in range(n):
        off,sz=struct.unpack_from('>II',d,i); i+=8
        e=d.index(b'\0',i); out.append((d[i:e].decode('latin1'),off,sz)); i=e+1
    return out
def main():
    a=sys.argv
    if a[1] in('ls','get'):
        f=open(a[2],'rb'); base,_=iso_find(f,a[3]); rest=a[4:]
    else:
        f=open(a[2],'rb'); base=0; rest=a[3:]
    ms=members(f,base)
    if a[1] in('ls','lsf'):
        for n,o,s in ms: print(n,o,s)
    else:
        for n,o,s in ms:
            if n.lower()==rest[0].lower():
                f.seek(base+o); open(rest[1],'wb').write(f.read(s)); print('wrote',rest[1],s); return
        raise SystemExit('no member')
main()
