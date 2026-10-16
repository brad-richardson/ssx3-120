#!/usr/bin/env python3
"""TK51 Part 2: import hidden donor1444's authored SSF surface into visible donor1443/world1400.

Usage: patch_source_bridge.py INPUT_COMPOSITE OUTPUT_COMPOSITE --donor-id 1444
The donor transforms are identical. Join the four authored meshes into one exact
63-vertex/56-face part for SSX3's one-render-part query; no hull, new triangles,
render geometry, or animation. Every authored position and normal is retained.
"""
from pathlib import Path
import argparse,sys,struct,json,hashlib,subprocess
from collections import Counter
sys.dont_write_bytecode=True
R=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(R/'tk33b'),str(R/'tk42c'),str(R/'tk13'),str(R/'tk12'),str(R/'shared')]
from wlib import W
from make_scenery33 import rewind
from gamecube_collision import read_tricky_collision
from gamecube_scenery import instance_gameplay
sys.path.insert(0,str(R/'tk16'))
from ps2_as_gc import ssf_to_gsf
from tricky_pbd import PBD
from ps2col import encode_collision,read_collision
from patch_blend import block_spans
from retrack import members_of
from relayout_stream import assemble_archive
import gamecube_game_dir as gdir
gdir.WORLD_EXTENSIONS=('sdb','ssb','phm','psm')
ISO=Path('/Users/brad/dev/ssx3-work/pinned/game/ssx3-usa-3c2f8eb1/SSX 3 (USA).iso')
DONOR=Path('/Users/brad/dev/ssx3-work/pinned/tricky/donor/mesa/mesa.pbd')
sha=lambda b:hashlib.sha256(b).hexdigest()
def materialize(blob):
 lines=blob[:2048].split(b'\0')[0].decode().splitlines();assert lines[0]=='PS2XCMP1';out=bytearray()
 with ISO.open('rb') as f:
  for ln in lines[3:]:
   if ln=='end':break
   kind,x,n=ln.split();x=int(x);n=int(n)
   if kind=='self':out+=blob[x:x+n*2048]
   else:assert kind=='iso';f.seek(x*2048);out+=f.read(n*2048)
 return bytes(out[:int(lines[1].split()[1])])
def mesh(pbd):
 ssf=DONOR.with_suffix('.ssf').read_bytes();gs=ssf_to_gsf(ssf);gp=instance_gameplay(gs,len(pbd.instances));g=gp[1444]
 assert g['player_collision'] and not g['visible'] and g['collision_mode']==1 and g['collision_or_physics']==2
 assert pbd.instances[1444]['matrix']==pbd.instances[1443]['matrix']
 source=read_tricky_collision(gs)[2];parts=rewind(source);vertices=[];triangles=[];normals=[]
 for part in parts:
  off=len(vertices);vertices+=part['vertices'];triangles += [tuple(i+off for i in tri) for tri in part['triangles']];normals+=part['normals']
 assert len(vertices)==63 and len(triangles)==56 and len(vertices)<=256
 merged=dict(vertices=vertices,triangles=triangles,normals=normals)
 result=encode_collision([merged]);decoded=read_collision(result);assert len(decoded)==1
 # Reversal is solely the verified Tricky -> SSX3 winding convention.
 assert decoded[0]['normals']==normals
 expected=[tuple(vertices[i] for i in (a,c,b)) for a,b,c in triangles]
 actual=[tuple(decoded[0]['vertices'][i] for i in tri) for tri in decoded[0]['triangles']]
 assert actual==expected  # partition remaps indices in first-use order
 return result,dict(source='mesa.ssf collision table 2 / hidden donor1444',ssf_sha=sha(ssf),source_parts=len(source),vertices=len(vertices),triangles=len(triangles),parts=len(decoded),positions_and_normals_preserved=True,source_surface=g['surface'])
def main():
 ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('input');ap.add_argument('output');ap.add_argument('--donor-id',type=int,required=True);a=ap.parse_args();assert a.donor_id==1444,'single-instance proof only'
 src=Path(a.input);out=Path(a.output);assert not out.exists();out.parent.mkdir(parents=True,exist_ok=True)
 inputs=[sha(src.read_bytes()) for _ in range(2)];assert inputs[0]==inputs[1];blob=src.read_bytes();raw=materialize(blob)
 full=out.with_suffix('.input.BIG');full.write_bytes(raw);stem=W(full).stem;bam_bytes=gdir.rename_world_members(raw,'bam');tmp=out.with_suffix('.bam.tmp');tmp.write_bytes(bam_bytes);world=W(tmp)
 G=80;rid=1400;recs=world.records(G);ip=next(p for e,p in recs if e['kind']==3 and e['rid']==rid);track=next(e['track'] for e,p in recs if e['kind']==3 and e['rid']==rid)
 pbd=PBD(DONOR.read_bytes());coll,geometry=mesh(pbd)
 colrid=max(e['rid'] for e,p in recs if e['kind']==12)+1;coloid=(colrid<<8)|track
 script=bytearray(next(p for e,p in recs if e['kind']==16));original_script=bytes(script)
 base=struct.unpack_from('<I',script,64)[0];nb,bat,nd,dat,ns,splat=struct.unpack_from('<6I',script,68);binds=list(struct.unpack_from(f'<{nb}H',script,bat));offsets=list(struct.unpack_from(f'<{nd}I',script,dat));olddef=struct.unpack_from('<4I',script,base+offsets[binds[rid]]);assert olddef==(0,0x10000,0x108,0xffffffff)
 newdef=struct.pack('<4I2f2h',1,0x210000,olddef[2],coloid,1e30,0.,3,12)
 script+=bytes(-len(script)%4);offsets.append(len(script)-base);script+=newdef;binds[rid]=nd;newbat=len(script);script+=struct.pack(f'<{nb}H',*binds);script+=bytes(-len(script)%4);newdat=len(script);script+=struct.pack(f'<{nd+1}I',*offsets);struct.pack_into('<6I',script,68,nb,newbat,nd+1,newdat,ns,splat)
 for i,(e,p) in enumerate(recs):
  if e['kind']==16:recs[i]=(dict(e,size=len(script)),bytes(script))
 at=next(i for i,(e,p) in enumerate(recs) if e['kind']>12);recs.insert(at,(dict(kind=12,track=track,rid=colrid,size=len(coll)),coll))
 patched=b''.join(bytes([e['kind']])+len(p).to_bytes(3,'little')+bytes([e['track']])+e['rid'].to_bytes(3,'little')+p for e,p in recs)
 raws={g['index']:world.raw(g['index']) for g in world.sdb['groups']};raws[G]=patched;ssb=dict(members_of(bam_bytes))['data/worlds/bam.ssb'];report=dict(groups=[dict(index=g['index'],offset=g['ssb_offset'],sha256=sha(world.raw(g['index'])),blocks=[dict(offset=o,size=n) for o,n in block_spans(ssb,g['ssb_offset'])]) for g in world.sdb['groups']])
 sdb=bytearray(world.sdb_bytes);lc,nodes,_=struct.unpack_from('<III',sdb,8);gtab=(80+88*lc+15)//16*16+nodes*96;go=gtab+68*G;struct.pack_into('<H',sdb,go,len(recs));struct.pack_into('<I',sdb,go+8,sum(len(p)+8 for e,p in recs if e['kind']<=12));counts=Counter(e['kind'] for e,p in recs)
 for k in range(14):struct.pack_into('<H',sdb,go+12+2*k,counts[k])
 location=next(l for l in world.locations() if l['index']==track);capoff=80+88*location['index']+32+24;oldcap=struct.unpack_from('<h',sdb,capoff)[0];struct.pack_into('<h',sdb,capoff,max(oldcap,colrid+1))
 result,layout,_=assemble_archive(bam_bytes,report,raws,bytes(sdb),jobs=2,reuse_original_blocks=True);patchedfile=out.with_suffix('.materialized.BIG');patchedfile.write_bytes(gdir.rename_world_members(result,stem));back=W(patchedfile)
 assert all(back.raw(g['index'])==raws[g['index']] for g in world.sdb['groups'])
 oldrecs={(e['kind'],e['rid']):p for e,p in world.records(G)};newrecs={(e['kind'],e['rid']):p for e,p in back.records(G)};changed=[key for key in oldrecs if oldrecs[key]!=newrecs[key]];assert changed==[(16,next(e['rid'] for e,p in recs if e['kind']==16))]
 # Existing definition/spline payload stays byte-exact, apart from the table header.
 assert original_script[:68]==script[:68] and original_script[92:]==script[92:len(original_script)]
 with out.with_suffix('.composite.json').open('w') as f:subprocess.run([sys.executable,str(R/'local/research/TK10/make_composite.py'),str(ISO),str(patchedfile),str(out)],stdout=f,check=True)
 outputs=[sha(out.read_bytes()) for _ in range(2)];assert outputs[0]==outputs[1];assert W(patchedfile).raw(G)==patched
 receipt=dict(input=str(src),input_sha_reads=inputs,output=str(out),output_sha_reads=outputs,donor_sha=sha(DONOR.read_bytes()),donor_id=a.donor_id,world_rid=rid,geometry=geometry,collision_rid=colrid,collision_oid=hex(coloid),old_definition=olddef,new_definition=struct.unpack_from('<4I',newdef),changed_existing_records=changed,added_records=[[12,colrid]],existing_script_payload_preserved=True,all_groups_readback=True,group_bytes_before=len(world.raw(G)),group_bytes_after=len(patched),group_count=len(world.sdb['groups']))
 out.with_suffix('.patch.json').write_text(json.dumps(receipt,indent=2)+'\n');tmp.unlink();print(json.dumps(receipt,indent=2))
if __name__=='__main__':main()
