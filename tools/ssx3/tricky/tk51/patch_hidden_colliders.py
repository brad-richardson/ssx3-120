#!/usr/bin/env python3
"""TK51 Part 3 independent post-composite importer of authored hidden colliders.
No render geometry or DMA chains. Retains donor node hierarchy, matrices,
node boxes and separate SSF parts; numeric surface IDs require prior audit.
Usage: patch_hidden_colliders.py INPUT OUTPUT --course mesa
"""
import argparse,math,json,struct,sys,subprocess
from pathlib import Path
from collections import Counter
sys.dont_write_bytecode=True
R=Path(__file__).resolve().parents[1];WROOT=Path('/Users/brad/dev/ssx3-work/TK51')
sys.path[:0]=[str(R/'tk51'),str(R/'tk16')]
from patch_source_bridge import materialize,sha,rewind,W,read_collision,encode_collision,block_spans,members_of,assemble_archive,gdir,ISO,PBD,ssf_to_gsf,instance_gameplay,read_tricky_collision
from make_scenery33 import mmul,xform

def inverse(a):
 b=[list(row)+[float(i==j) for j in range(4)] for i,row in enumerate(a)]
 for i in range(4):
  k=max(range(i,4),key=lambda j:abs(b[j][i]));b[i],b[k]=b[k],b[i];v=b[i][i];assert abs(v)>1e-10;b[i]=[x/v for x in b[i]]
  for j in range(4):
   if j!=i:
    v=b[j][i];b[j]=[x-v*y for x,y in zip(b[j],b[i])]
 return [row[4:] for row in b]

def collision_by_node(parts,objs):
 # SSX3 3a6d00 indexes the loaded pointer array by MODEL node, including
 # remaining nodes. Preserve the proven Part3 geometry-slot ordering and
 # append stock-format empty slots; do not shift geometry by bbox presence.
 encoded=encode_collision(parts);original=read_collision(encoded)
 kind,count,first,tail,refs=struct.unpack_from('<2H3I',encoded);assert kind==1
 chunks=[];at=first
 for _ in range(count):
  faces,verts,indices,bbox,pos,norm=struct.unpack_from('<2H4I',encoded,at)
  end=at+norm+16*faces;chunks.append(encoded[at:end]);at=end
 assert at==tail and count==sum('bbox' in ob for ob in objs),'authored parts must match geometry nodes'
 empty=bytes.fromhex('0000000014000000140000002000000020000000000000000000000000000000')
 out=bytearray(16);mapping=list(range(count))+[None]*(len(objs)-count)
 for chunk in chunks:out+=chunk
 for _ in range(len(objs)-count):out+=empty
 tail=len(out);out+=bytes.fromhex('efbeaddeefbeadde0000000000000000');out+=bytes.fromhex('efbeadde')*len(objs)
 struct.pack_into('<2H3I',out,0,1,len(objs),16,tail,tail+16);out=bytes(out);decoded=read_collision(out)
 assert len(decoded)==len(objs)
 assert [m for m,k in zip(decoded,mapping)if k is not None]==original,'authored geometry must stay byte-equivalent after indexing'
 assert all(not decoded[i]['vertices'] and not decoded[i]['triangles']for i,k in enumerate(mapping)if k is None)
 return out,decoded,mapping

def model_bytes(oid,model):
 objs=model['objects'];out=bytearray(48+16*len(objs));struct.pack_into('<5If3f2I',out,0,oid,len(objs),48,40,1,0.,*model['scale'],0,0)
 for j,ob in enumerate(objs):
  s2=0;mx=-1
  if 'bbox' in ob:
   out+=bytes(-len(out)%16);s2=len(out);out+=struct.pack('<6f3I',*ob['bbox'],ob['flags'],0,0)
  if ob['matrix'] is not None:
   out+=bytes(-len(out)%16);mx=len(out);out+=struct.pack('<16f',*ob['matrix'])
  struct.pack_into('<4i',out,48+j*16,ob['parent'],s2,0,mx)
 out+=bytes(-len(out)%16);struct.pack_into('<I',out,36,len(out));return bytes(out)

def main():
 global ISO
 ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('input');ap.add_argument('output');ap.add_argument('--course',required=True,choices=['mesa','snow','aloha','merq','untrack','gari','alaska','elys','pipe']);ap.add_argument('--only-source',type=int,help='Authorized diagnostic subset; donor instance ID');ap.add_argument('--census',default=None,help='workdir hidden-census.json from make_hidden_census.py');ap.add_argument('--scenery-dir',default='/Users/brad/dev/ssx3-work/pinned/tricky/scenery-effects/scenery');ap.add_argument('--donor-dir',default='/Users/brad/dev/ssx3-work/pinned/tricky/donor');ap.add_argument('--ssx3-iso',default=str(ISO),help='stock SSX3 ISO (sector source for PS2XCMP1 + composite reference)');a=ap.parse_args();src=Path(a.input);out=Path(a.output);assert not out.exists();out.parent.mkdir(parents=True,exist_ok=True)
 if a.census is None or not Path(a.census).is_file():raise SystemExit('hidden census missing: generate it with tk51/make_hidden_census.py --donor-dir <donor>/<stem> --stem <stem> --out <workdir>/hidden-census.json, then pass --census')
 ISO=Path(a.ssx3_iso);sys.modules['patch_source_bridge'].ISO=ISO
 inputs=[sha(src.read_bytes()) for _ in range(2)];assert inputs[0]==inputs[1]
 census=json.loads(Path(a.census).read_text());donor_stem={'merq':'merquer','elys':'elysium'}.get(a.course,a.course);census=next(r for r in census if r['course']==donor_stem);eligible={x['source'] for x in census['hidden'] if x['solid_proxy_candidate']}
 if a.only_source is not None:
  assert a.course=='mesa' and a.only_source in eligible,'diagnostic subset must name an admitted Mesa proxy'
  eligible={a.only_source}
 if not eligible:
  out.write_bytes(src.read_bytes());outputs=[sha(out.read_bytes())for _ in range(2)];assert inputs==outputs;receipt=dict(course=a.course,input_sha_reads=inputs,output_sha_reads=outputs,hidden_instances=[],no_op=True,trigger_reset_helpers_not_imported=True);out.with_suffix('.patch.json').write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps(receipt));return
 config={'gari':(33,'gari-f',0,0),'mesa':(80,'mesa-final',1443,1400),'snow':(56,'snow-f',0,0),'aloha':(22,'aloha-b',3,0),'merq':(148,'merq-d',0,0),'untrack':(158,None,1,0)};G,scenery,anchor,anchor_rid=config[a.course]
 raw=materialize(src.read_bytes());stem=a.course;bam=gdir.rename_world_members(raw,'bam');tmp=out.with_suffix('.bam.tmp');tmp.write_bytes(bam);world=W(tmp);recs=world.records(G);T=next(e['track']for e,p in recs if e['kind']==3)
 if scenery:
  rc=json.loads((Path(a.scenery_dir)/(scenery+'-scenery.json')).read_text());assert rc['instance_source'][anchor_rid]==anchor
 D=Path(a.donor_dir)/donor_stem;D=D if D.exists()else WROOT/'p3-donor/untrack';pbd=PBD((D/(donor_stem+'.pbd')).read_bytes());ssf=(D/(donor_stem+'.ssf')).read_bytes();gs=ssf_to_gsf(ssf);gp=instance_gameplay(gs,len(pbd.instances));colls=read_tricky_collision(gs)
 # Derive the already-installed placement from a visible instance, never a new fit.
 oldip=next(p for e,p in recs if e['kind']==3 and e['rid']==anchor_rid);vals=struct.unpack_from('<16f',oldip,16);scale=struct.unpack_from('<f',oldip,132)[0];oldm=[list(vals[k*4:k*4+4]) for k in range(4)]
 for k in range(3):oldm[k][:3]=[v*scale for v in oldm[k][:3]]
 placement=mmul(inverse(pbd.instances[anchor]['matrix']),oldm);page=struct.unpack_from('<I',oldip,124)[0]
 nextids={k:max((e['rid'] for e,p in recs if e['kind']==k),default=-1)+1 for k in (2,3,12)};script=bytearray(next(p for e,p in recs if e['kind']==16));original=bytes(script);base,nb,bat,nd,dat,ns,splat=struct.unpack_from('<7I',script,64);binds=list(struct.unpack_from(f'<{nb}H',script,bat));offsets=list(struct.unpack_from(f'<{nd}I',script,dat));added=[];receipts=[]
 # Definition+8 names a class row, not a program (TK53 MEMO section1).
 cc,ca=struct.unpack_from('<2I',script,24);pc,pt,pe=struct.unpack_from('<3I',script,56);st=(*struct.unpack_from(f'<{pc}I',script,pt),pe)
 rows=[struct.unpack_from('<6I',script,ca+24*r)for r in range(cc)]
 used_rows={struct.unpack_from('<I',script,base+o+8)[0]>>8 for o in offsets}
 refs={h>>8 for row in rows for h in row if h!=0xffffffff};lc,la=struct.unpack_from('<2I',script,16);refs|={h>>8 for h in struct.unpack_from(f'<{lc}I',script,la)}
 stub=bytes.fromhex('4c554e001400000024000000240000002aff0000ecffffff000000000200000000000000')
 noops=[k for k in range(pc)if script[st[k]:st[k]+36]==stub];assert noops,'no stock RETNIL program'
 noop=noops[0]
 free_rows=[r for r in range(2,cc)if r not in used_rows and rows[r]==(0xffffffff,)*6];assert free_rows,'no unreferenced class row';class_row=free_rows[0]
 # Stock Object builtin at create, with stock RETNIL programs in every other slot.
 code=bytes([33,2,0,0,42,0xff,0,0]);end=16+len(code);objprog=struct.pack('<4I',0x4e554c,end,end+16,end+16)+code+struct.pack('<iIII',-end,0,3,0)
 frees=[k for k in noops if k not in refs and st[k+1]-st[k]>=len(objprog)];assert frees,'no unused program slot for stock Object';create=frees[0]
 oldprog=bytes(script[st[create]:st[create+1]]);script[st[create]:st[create+1]]=objprog+bytes(len(oldprog)-len(objprog))
 handles=[(noop<<8)|T]*6;handles[1]=(create<<8)|T;struct.pack_into('<6I',script,ca+24*class_row,*handles)
 assert all(h not in(0,0xffffffff) and h>>8<pc for h in handles)
 class_handle=(class_row<<8)|T
 class_receipt=dict(row=class_row,handle=hex(class_handle),slots=[hex(h)for h in handles],create_slot=1,contact_slot=2,create_program=create,other_slots_noop_program=noop,old_unused_program_sha=sha(oldprog),new_program_sha=sha(script[st[create]:st[create+1]]),existing_class_row_unreferenced=True,existing_program_unreferenced=True)
 original_allowed_ranges=[(ca+24*class_row,ca+24*(class_row+1)),(st[create],st[create+1])]
 hidden_order=[i for i,g in enumerate(gp)if not g['visible'] and g['player_collision']];id_base=dict(nextids);collision_order=[i for i in hidden_order if gp[i]['collision_mode']==1]
 for i,g in enumerate(gp):
  if i not in eligible:continue
  mode=g['collision_mode'];assert mode in (1,2),'sphere-tree port not inferred from render geometry';ins=pbd.instances[i];model=pbd.models[ins['model']];objs=model['objects'];ordinal=hidden_order.index(i);rid=id_base[3]+ordinal;mrid=id_base[2]+ordinal;nextids[3]=max(nextids[3],rid+1);nextids[2]=max(nextids[2],mrid+1);oid=lambda r:(r<<8)|T
  B=mmul(ins['matrix'],placement);lens=[math.sqrt(sum(B[k][j]**2 for j in range(3))) for k in range(3)];s=sum(lens)/3
  assert all(abs(l/s-1)<1e-4 for l in lens) and all(abs(sum(B[k][c]*B[j][c] for c in range(3)))<1e-4*s*s for k in range(3) for j in range(k)),'authored nonuniform transform needs explicit support'
  mx=[[v/s for v in B[k][:3]]+[0.] for k in range(3)]+[B[3][:3]+[1.]];corners=[xform((x,y,z),placement) for x in (ins['low'][0],ins['high'][0]) for y in (ins['low'][1],ins['high'][1]) for z in (ins['low'][2],ins['high'][2])];lo=[min(c[k] for c in corners) for k in range(3)];hi=[max(c[k] for c in corners) for k in range(3)];ctr=[(x+y)/2 for x,y in zip(lo,hi)];head=bytearray(160);struct.pack_into('<16f',head,16,*[v for row in mx for v in row]);struct.pack_into('<4f3f3f',head,80,*ctr,math.dist(lo,hi)/2,*lo,*hi);struct.pack_into('<3If2I2h3I',head,120,oid(rid),page,oid(mrid),s,0,0,-1,0,0,160,0)
  mb=model_bytes(oid(mrid),model);added += [(dict(kind=2,track=T,rid=mrid),mb),(dict(kind=3,track=T,rid=rid),bytes(head))];coloid=0xffffffff;part_counts=[]
  if mode==1:
   parts=rewind(colls[g['collision_or_physics']]);coll,decoded,node_parts=collision_by_node(parts,objs);crid=id_base[12]+collision_order.index(i);nextids[12]=max(nextids[12],crid+1);coloid=oid(crid);added.append((dict(kind=12,track=T,rid=crid),coll));part_counts=[dict(vertices=len(m['vertices']),faces=len(m['triangles'])) for m in decoded]
  propcount,propat=struct.unpack_from('<2I',ssf,52);u0,bounce=struct.unpack_from('<2f',ssf,propat+24*g['property_index']);surface=-1 if g['surface']==0xffffffff else g['surface'];definition=struct.pack('<4I',mode,0x200000,class_handle,coloid)
  for ob in objs:definition+=struct.pack('<2f2h',u0,bounce,1 if 'bbox' in ob else 0,surface if 'bbox' in ob else -1)
  script+=bytes(-len(script)%4);di=len(offsets);offsets.append(len(script)-base);script+=definition
  while len(binds)<=rid:binds.append(0)
  binds[rid]=di;receipts.append(dict(source=i,world_rid=rid,model_rid=mrid,mode=mode,surface=surface,render_meshes=0,parts=part_counts,geometry_part_count=len(parts),collision_parts_by_model_node=node_parts,nodes=len(objs),parents=[ob['parent'] for ob in objs],node_flags=[1 if 'bbox' in ob else 0 for ob in objs],node_matrices_preserved=True,world_transform_preserved=True,u0=u0,bounce=bounce,source_effect=g['effect_slot'],effect_vm_not_imported=True,class_handle=hex(class_handle),class_row=class_row))
 newbat=len(script);script+=struct.pack(f'<{len(binds)}H',*binds);script+=bytes(-len(script)%4);newdat=len(script);script+=struct.pack(f'<{len(offsets)}I',*offsets);struct.pack_into('<6I',script,68,len(binds),newbat,len(offsets),newdat,ns,splat)
 recs=[(dict(e,size=len(script)),bytes(script))if e['kind']==16 else(e,p) for e,p in recs];recs+=added;recs.sort(key=lambda ep:ep[0]['kind']);patched=b''.join(bytes([e['kind']])+len(p).to_bytes(3,'little')+bytes([e['track']])+e['rid'].to_bytes(3,'little')+p for e,p in recs);raws={g['index']:world.raw(g['index']) for g in world.sdb['groups']};raws[G]=patched;ssb=dict(members_of(bam))['data/worlds/bam.ssb'];report=dict(groups=[dict(index=g['index'],offset=g['ssb_offset'],sha256=sha(world.raw(g['index'])),blocks=[dict(offset=o,size=n) for o,n in block_spans(ssb,g['ssb_offset'])])for g in world.sdb['groups']]);sdb=bytearray(world.sdb_bytes);lc,nodes,_=struct.unpack_from('<III',sdb,8);gtab=(80+88*lc+15)//16*16+nodes*96;go=gtab+68*G;struct.pack_into('<H',sdb,go,len(recs));struct.pack_into('<I',sdb,go+8,sum(len(p)+8 for e,p in recs if e['kind']<=12));counts=Counter(e['kind'] for e,p in recs)
 for k in range(14):struct.pack_into('<H',sdb,go+12+2*k,counts[k])
 for k in (2,3,12):
  at=80+88*T+32+2*k;old=struct.unpack_from('<h',sdb,at)[0];struct.pack_into('<h',sdb,at,max(old,nextids[k]))
 result,_,_=assemble_archive(bam,report,raws,bytes(sdb),jobs=2,reuse_original_blocks=True);full=out.with_suffix('.materialized.BIG');full.write_bytes(gdir.rename_world_members(result,stem));back=W(full);assert all(back.raw(g['index'])==raws[g['index']] for g in world.sdb['groups']);new={(e['kind'],e['rid']):p for e,p in back.records(G)};changed=[(e['kind'],e['rid'])for e,p in world.records(G)if new[(e['kind'],e['rid'])]!=p];assert changed==[(16,next(e['rid']for e,p in recs if e['kind']==16))];assert original[:68]==script[:68];expected=bytearray(original)
 for start,stop in original_allowed_ranges:expected[start:stop]=script[start:stop]
 assert expected[92:]==script[92:len(original)],'only class row and unused Object program may change in old payload'
 with out.with_suffix('.composite.json').open('w')as f:subprocess.run([sys.executable,str(R/'tk10/make_composite.py'),str(ISO),str(full),str(out)],stdout=f,check=True)
 outputs=[sha(out.read_bytes()) for _ in range(2)];assert outputs[0]==outputs[1];receipt=dict(course=a.course,diagnostic_only_source=a.only_source,input_sha_reads=inputs,output_sha_reads=outputs,donor_ssf_sha=sha(ssf),hidden_instances=receipts,changed_existing_records=changed,existing_records_preserved_except_script=True,all_groups_readback=True,visibility_flags='0x00200000; no static draw bit0',source_parts_preserved=True,trigger_reset_helpers_not_imported=True,class_binding=class_receipt,existing_payload_allowed_changes=original_allowed_ranges,eligibility='initially hidden, player-collidable, mode1, immovable, no effect, SSF static flag128; reset/hazard surfaces0,13,18 excluded');out.with_suffix('.patch.json').write_text(json.dumps(receipt,indent=2)+'\n');tmp.unlink();print(json.dumps(receipt,indent=2))
if __name__=='__main__':main()
