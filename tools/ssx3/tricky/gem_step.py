#!/usr/bin/env python3
"""Extend a current TK45c table only from authored MultiplierScore pickups.

Inputs are current TKP4 composites and original donor files extracted by
donor_inventory.py. Worlds remain byte-identical. All imported translations
and donor/model mappings are verified before any sidecar row is added.
"""
import argparse, hashlib, json, math, struct, sys
from pathlib import Path
sys.dont_write_bytecode=True
# Port: the lane repo-rooted every import; here every helper is a sibling in
# this package (wlib, tricky_pbd, tkp4_speech/parse_desc, gamecube_scenery,
# replace_terrain/placement, ps2_as_gc30 come from the world-pipeline port).
sys.path[:0]=[str(Path(__file__).resolve().parent)]
from speech_merge import parse_desc
from wlib import W
from tricky_pbd import PBD
from gamecube_scenery import instance_gameplay
from replace_terrain import placement
from ps2_as_gc30 import ssf_to_gsf
sha=lambda b:hashlib.sha256(b).hexdigest()
f32=lambda x:struct.unpack('<f',struct.pack('<f',x))[0]
def twice(path):
    b=Path(path).read_bytes();b2=Path(path).read_bytes();assert b==b2
    return b,dict(path=str(path),sha_read1=sha(b),sha_read2=sha(b2))
def neighbor(x,d):
    b=struct.unpack('<I',struct.pack('<f',x))[0];b+=d if x>0 else -d
    return struct.unpack('<f',struct.pack('<I',b))[0]
def materialize(path,iso,out):
    b,receipt=twice(path);size,segs=parse_desc(b)
    def resolve():
        raw=bytearray()
        with open(iso,'rb') as f:
            for k,x,n in segs:
                if k=='self':raw+=b[x:x+2048*n]
                else:f.seek(2048*x);raw+=f.read(2048*n)
        return bytes(raw[:size])
    full=resolve();assert full==resolve()
    out.parent.mkdir(parents=True,exist_ok=True);out.write_bytes(full)
    back,fullreceipt=twice(out);assert back==full
    return W(out),receipt,fullreceipt
def main():
    p=argparse.ArgumentParser();p.add_argument('--worlds',required=True);p.add_argument('--iso',required=True);p.add_argument('--donor',required=True);p.add_argument('--donor-bindings',required=True);p.add_argument('--sidecar',required=True);p.add_argument('--work',required=True);p.add_argument('--out',required=True);p.add_argument('--receipt',required=True);p.add_argument('--only-course',choices=['alaska','pipe']);p.add_argument('--only-donor',type=int);p.add_argument('--alaska-scenery',required=True,help='TK18 alaska-b-scenery-summary.json (port: was a hardcoded lane path)');a=p.parse_args()
    assert a.only_donor is None or a.only_course is not None
    side,sideinput=twice(a.sidecar);donorbytes,donorinput=twice(a.donor_bindings);donors=json.loads(donorbytes)
    work=Path(a.work);work.mkdir(parents=True,exist_ok=True);added=[];courses=[]
    for course,group,yaw in [('pipe',49,120.8),('alaska',115,154.1)]:
        world,inp,full=materialize(Path(a.worlds)/(course.upper()+'.BIG'),a.iso,work/(course.upper()+'.BIG'))
        k3=sorted([(e['rid'],data) for e,data in world.records(group) if e['kind']==3])
        d=Path(a.donor)/course;pb,pr=twice(d/(course+'.pbd'));gb,gr=twice(d/(course+'.gsf'));ssf,sr=twice(d/(course+'.ssf'))
        assert ssf_to_gsf(ssf)==gb
        pbd=PBD(pb);gp=instance_gameplay(gb,len(pbd.instances))
        original=next(x for x in donors[course]['inputs'] if x['member'].endswith('.pbd'))
        assert sha(pb)==original['sha256_read1']==original['sha256_read2']
        original_ssf=next(x for x in donors[course]['inputs'] if x['member'].endswith('.ssf'))
        assert sha(ssf)==original_ssf['sha256_read1']==original_ssf['sha256_read2']
        source_receipt=None
        if course=='pipe':
            source=[i for i,g in enumerate(gp) if g['visible']]
            M,T=placement([157.85255432128906,10.177421569824219,4894.36865234375],[-268342.9479166667,116586.7265625,-461145.8854166667],yaw,.55)
        else:
            sb,source_receipt=twice(a.alaska_scenery);sc=json.loads(sb)
            assert sc['donor']['pbd_sha256']==sha(pb)
            source=sc['instance_source'];M,T=placement([0]*3,[0]*3,yaw,.55)
            # Recover the original translation within the intersection of every
            # imported f32 rounding interval, then verify every translation bit.
            lo=[-math.inf]*3;hi=[math.inf]*3
            for (rid,data),i in zip(k3,source):
                for k,x in enumerate(struct.unpack_from('<3f',data,64)):
                    dot=sum(M[k][j]*pbd.instances[i]['matrix'][3][j] for j in range(3))
                    lo[k]=max(lo[k],(neighbor(x,-1)+x)/2-dot)
                    hi[k]=min(hi[k],(neighbor(x,1)+x)/2-dot)
            assert all(x<y for x,y in zip(lo,hi))
            T=[(x+y)/2 for x,y in zip(lo,hi)]
        assert len(source)==len(k3) and len(set(source))==len(source)
        modelmap={};bydonor={};translation_differences=0
        for (rid,data),i in zip(k3,source):
            assert rid==len(bydonor)
            inst=pbd.instances[i];model=struct.unpack_from('<I',data,128)[0]
            assert modelmap.setdefault(inst['model'],model)==model
            predicted=struct.pack('<3f',*[sum(M[k][j]*inst['matrix'][3][j] for j in range(3))+T[k] for k in range(3)])
            translation_differences+=sum(x!=y for x,y in zip(predicted,data[64:76]))
            bydonor[i]=(rid,data)
        assert translation_differences==0 and len(set(modelmap.values()))==len(modelmap)
        rows=[];dropped=[]
        for u in donors[course]['relevant_users']:
            ops=[x for v in u['programs'].values() for x in v if x['op']==14]
            if not ops:continue
            assert len(ops)==1 and len(ops[0]['args'])==1
            value=struct.unpack('<f',struct.pack('<I',ops[0]['args'][0]))[0]
            assert value in (2,3,5) and u['name'].startswith('Gem_TrickMultiplier_')
            assert u['gameplay']['player_collision'] and u['gameplay']['collision_mode']==2
            i=u['donor_instance']
            if i not in bydonor:dropped.append(i);continue
            rid,data=bydonor[i];center=list(struct.unpack_from('<3f',data,64));radius=struct.unpack_from('<f',data,92)[0]
            assert all(math.isfinite(x) for x in center+[radius]) and radius>0
            row=[course.upper(),str(rid)]+[repr(x) for x in center+[radius]]+[str(int(value)),u['name']]
            assert [f32(float(x)) for x in row[2:6]]==center+[radius]
            rows.append(row)
        assert rows
        confirmed=len(rows)
        if a.only_course and a.only_course!=course:rows=[]
        elif a.only_donor is not None:rows=[row for row in rows if source[int(row[1])]==a.only_donor]
        added+=rows
        courses.append(dict(course=course,composite=inp,full=full,donor_pbd=pr,donor_gsf=gr,donor_ssf=sr,source_receipt=source_receipt,group=group,imported=len(k3),models=len(modelmap),matrix=M,translation=T,translation_differing_bytes=translation_differences,confirmed=confirmed,added=len(rows),dropped=dropped))
    # UNTRACK has no MultiplierScore program or users; retain zero rows.
    assert not any(x['op']==14 for v in donors['untrack']['relevant_programs'].values() for x in v)
    _,untrack=twice(Path(a.worlds)/'UNTRACK.BIG')
    existing=[x.split() for x in side.decode().splitlines() if x.strip() and not x.startswith('#')]
    assert added
    assert not any(x[0] in ('PIPE','ALASKA','UNTRACK') for x in existing)
    allrows=existing+added;assert len(allrows)<=512
    spheres={}
    for row in allrows:spheres.setdefault(row[0],[]).append(tuple(map(float,row[2:6])))
    gaps={};arches=sorted(spheres)
    for j,c in enumerate(arches):
        for d in arches[j+1:]:
            gap=min(math.dist(x[:3],y[:3])-x[3]-y[3] for x in spheres[c] for y in spheres[d]);assert gap>=10000
            gaps[c+'-'+d]=gap
    result=side+(b'' if side.endswith(b'\n') else b'\n')+''.join(' '.join(row)+'\n' for row in added).encode()
    dest=Path(a.out);dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(result);back,output=twice(dest);assert back==result and back.startswith(side)
    Path(a.receipt).write_text(json.dumps(dict(input_sidecar=sideinput,donor_bindings=donorinput,courses=courses,untrack=untrack,untrack_rows_added=0,existing_rows=len(existing),added_rows=len(added),total_rows=len(allrows),capacity=512,inter_course_min_gaps=gaps,output=output,existing_prefix_identical=True,world_composites_unchanged=True),indent=2)+'\n')
    print('added',len(added),'total',len(allrows),'SHA',sha(result))


if __name__ == "__main__":
    main()
