"""Exact reviewed first-batch candidate. Not a general parser or automatic queue.
Default/dry-run is strictly read-only. Execute/reconcile require explicit CLI selection.
No deletion/retirement. Existing pinned runtime is imported without modification.
"""
import pathlib,sys,json,os,contextlib,importlib.util,uuid,hashlib,re,zipfile,io
ROOT=pathlib.Path(r"<workspace>\临时隔离验证-历史memo-20261116\member-a-existing-image-scheduler-candidate")
PROD=pathlib.Path(r"<workspace>\归档程序与记录\local_archiver\image-production-release-v2")
MAIN=pathlib.Path(r"<workspace>\正式数据库\Company-Profile-local.sqlite")
SCHEDULER_LOCK=pathlib.Path(r"<workspace>\归档程序与记录\same-class-scheduler.lock")
MODE_HELPER_PATH=pathlib.Path(r"<workspace>\临时隔离验证-历史memo-20261116\same-class-qualification-integration\mode-transition-v3.py")
NAV_HELPER=pathlib.Path('<workspace>\\临时隔离验证-历史memo-20261116\\parameterized-text-scheduler-v2-bookmark-candidate\\navigation_bookmarks.py')
NAV_SHA='7b11a61a6b0e8f78a899e1e50fe74be881cf9db59674bfa5b242860a696a5072'
BATCH=TICKET=QUAL=RUN=STATE=RESTORED=None
TICKET_SHA=QUAL_SHA=BATCH_SHA=None
CREATED=None
CONFIGURED=False

sys.path.insert(0,str(PROD));import archiver as a,controlled_writer as w,image_assets as im

def need(v,m):
 if not v:raise RuntimeError(m)
def fixed(p,exists=True):
 p=pathlib.Path(p)
 need(p.is_absolute() and '..' not in p.parts,'absolute canonical path required')
 return a.safe(p,exists)
def sha(p):return a.sha(fixed(p).read_bytes())
def load(p):return json.loads(fixed(p).read_text(encoding='utf-8-sig'))
def pins():return w.code_identity()
def current():
 need(fixed(w.target('production'))==fixed(MAIN),'fixed production target differs')
 for ext in ['-wal','-shm','-journal']:
  side=pathlib.Path(str(MAIN)+ext)
  if side.exists():need(fixed(side).stat().st_size==0,'nonempty SQLite sidecar: recovery review required')
 d=a.connect(MAIN,True)
 try:
  t=a.target_identity(d);c=d.execute('select * from local_archive_control where id=1').fetchone()
  need(d.execute('pragma journal_mode').fetchone()[0].lower()=='delete','WAL baseline unsupported')
  return {'db_sha256':sha(MAIN),'identity':t,'generation':c['generation'],'mode':c['mode'],'pins':json.loads(c['code_json'])}
 finally:d.close()

def exact_keys(v,keys,name):need(isinstance(v,dict) and set(v)==set(keys),name+' schema mismatch')
def filename(n):need(isinstance(n,str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,140}',n) is not None and '..' not in n,'safe artifact filename');return n
def hexhash(x):need(isinstance(x,str) and re.fullmatch('[a-f0-9]{64}',x) is not None,'SHA256 format');return x
def configure(ticket_id,ticket_sha,approved_profile_sha):
 # Only caller-supplied release SHA authorizes a profile; an internal "reviewed" flag never does.
 global BATCH,TICKET,QUAL,RUN,STATE,RESTORED,TICKET_SHA,QUAL_SHA,BATCH_SHA,CREATED,CONFIGURED
 filename(ticket_id);hexhash(ticket_sha);hexhash(approved_profile_sha)
 folder=fixed(ROOT/'inputs'/ticket_id);need(folder.parent==fixed(ROOT/'inputs'),'fixed input root')
 TICKET=folder/'ticket.json';QUAL=folder/'profile.json';BATCH=folder/'batch.json'
 TICKET_SHA=ticket_sha;QUAL_SHA=approved_profile_sha
 need(sha(TICKET)==TICKET_SHA and sha(QUAL)==QUAL_SHA,'caller-approved ticket/profile mismatch')
 t=load(TICKET);exact_keys(t,['protocol','ticket_id','batch_sha256','profile_sha256','expected_baseline','expected_counts','created'],'ticket')
 need(t['protocol']=='reviewed-text-ticket-v1' and t['ticket_id']==ticket_id and t['profile_sha256']==QUAL_SHA,'ticket identity')
 exact_keys(t['expected_baseline'],['db_sha256','identity','generation','mode','pins'],'baseline')
 exact_keys(t['expected_counts'],['memo_count','receipt_count'],'baseline counts')
 need(type(t['created']) is int and 1<=t['created']<=100,'created bound');CREATED=t['created'];BATCH_SHA=hexhash(t['batch_sha256'])
 label='historical-'+ticket_id+'-qualified';filename(label)
 RUN=w.PROGRAM_ROOT/'正式续档'/label;STATE=RUN/'state.json';RESTORED=ROOT/'restores'/label
 CONFIGURED=True


def validate_single_png(item,z,doc,ps,lo,hi):
 W='{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
 A='{http://schemas.openxmlformats.org/drawingml/2006/main}'
 WP='{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}'
 P='{http://schemas.openxmlformats.org/drawingml/2006/picture}'
 R='{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
 claims=item.get('original_images');need(isinstance(claims,list) and len(claims)==1,'exactly one original PNG claim required')
 im.validate_item(item);c=claims[0];need(c['role']=='standalone_image_table','only original PNG table qualified')
 need(c['origin']=={'kind':'docx_media','part':'word/document.xml','relationship_id':c['origin'].get('relationship_id')},'DOCX image origin required')
 drawings=list(doc.iter(W+'drawing'));need(len(drawings)==1 and any(drawings[0] in list(p.iter()) for p in ps[lo:hi+1]),'single drawing must belong selected event')
 need(not any(n.tag in {W+'pict',W+'object',W+'altChunk'} for n in doc.iter()),'alternate visual/object forbidden')
 dr=drawings[0]
 def structure(n):return [n.tag,sorted(n.attrib),[structure(x) for x in n]]
 need(structure(dr)==['{http://schemas.openxmlformats.org/wordprocessingml/2006/main}drawing', [], [['{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}inline', ['distB', 'distL', 'distR', 'distT', '{http://schemas.microsoft.com/office/word/2010/wordprocessingDrawing}anchorId', '{http://schemas.microsoft.com/office/word/2010/wordprocessingDrawing}editId'], [['{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}extent', ['cx', 'cy'], []], ['{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}effectExtent', ['b', 'l', 'r', 't'], []], ['{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}docPr', ['id', 'name'], []], ['{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}cNvGraphicFramePr', [], [['{http://schemas.openxmlformats.org/drawingml/2006/main}graphicFrameLocks', ['noChangeAspect'], []]]], ['{http://schemas.openxmlformats.org/drawingml/2006/main}graphic', [], [['{http://schemas.openxmlformats.org/drawingml/2006/main}graphicData', ['uri'], [['{http://schemas.openxmlformats.org/drawingml/2006/picture}pic', [], [['{http://schemas.openxmlformats.org/drawingml/2006/picture}nvPicPr', [], [['{http://schemas.openxmlformats.org/drawingml/2006/picture}cNvPr', ['id', 'name'], []], ['{http://schemas.openxmlformats.org/drawingml/2006/picture}cNvPicPr', [], []]]], ['{http://schemas.openxmlformats.org/drawingml/2006/picture}blipFill', [], [['{http://schemas.openxmlformats.org/drawingml/2006/main}blip', ['{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed'], []], ['{http://schemas.openxmlformats.org/drawingml/2006/main}stretch', [], [['{http://schemas.openxmlformats.org/drawingml/2006/main}fillRect', [], []]]]]], ['{http://schemas.openxmlformats.org/drawingml/2006/picture}spPr', [], [['{http://schemas.openxmlformats.org/drawingml/2006/main}xfrm', [], [['{http://schemas.openxmlformats.org/drawingml/2006/main}off', ['x', 'y'], []], ['{http://schemas.openxmlformats.org/drawingml/2006/main}ext', ['cx', 'cy'], []]]], ['{http://schemas.openxmlformats.org/drawingml/2006/main}prstGeom', ['prst'], [['{http://schemas.openxmlformats.org/drawingml/2006/main}avLst', [], []]]]]]]]]]]]]]]],'drawing exact hierarchy/cardinality/expanded attributes differ')
 allowed={W+'drawing',WP+'inline',WP+'extent',WP+'effectExtent',WP+'docPr',WP+'cNvGraphicFramePr',A+'graphicFrameLocks',A+'graphic',A+'graphicData',P+'pic',P+'nvPicPr',P+'cNvPr',P+'cNvPicPr',P+'blipFill',A+'blip',A+'stretch',A+'fillRect',P+'spPr',A+'xfrm',A+'off',A+'ext',A+'prstGeom',A+'avLst'}
 need(all(n.tag in allowed for n in dr.iter()),'unknown drawing effect/crop/alternate node')
 for n in dr.iter():
  need(not(n.text or '').strip() and not(n.tail or '').strip(),'drawing contains text')
  local=n.tag.split('}')[-1]
  attrs={k.split('}')[-1]:v for k,v in n.attrib.items()}
  permitted={'drawing':set(),'inline':{'distT','distB','distL','distR','anchorId','editId'},'extent':{'cx','cy'},'effectExtent':{'l','t','r','b'},'docPr':{'id','name','descr'},'cNvGraphicFramePr':set(),'graphicFrameLocks':{'noChangeAspect'},'graphic':set(),'graphicData':{'uri'},'pic':set(),'nvPicPr':set(),'cNvPr':{'id','name','descr'},'cNvPicPr':set(),'blipFill':set(),'blip':{'embed'},'stretch':set(),'fillRect':set(),'spPr':set(),'xfrm':set(),'off':{'x','y'},'ext':{'cx','cy'},'prstGeom':{'prst'},'avLst':set()}
  need(set(attrs)<=permitted[local],'unknown drawing attribute')
  if local in ['effectExtent','off']:need(all(v=='0' for v in attrs.values()),'effect/offset not zero')
  if local=='prstGeom':need(attrs=={'prst':'rect'},'nonrect geometry')
  if local=='graphicData':need(attrs=={'uri':P[1:-1]},'graphic payload namespace')
  if local in ['extent','ext']:need(set(attrs)=={'cx','cy'} and all(v.isdigit() and int(v)>0 for v in attrs.values()),'extent invalid')
  if local=='graphicFrameLocks':need(attrs.get('noChangeAspect')=='1','aspect lock required')
 blips=list(dr.iter(A+'blip'));need(len(blips)==1,'one blip required');rid=c['origin']['relationship_id'];need(blips[0].attrib=={R+'embed':rid},'image reference differs')
 uses=[n for n in doc.iter() if n.get(R+'embed')==rid or n.get(R+'id')==rid];need(len(uses)==1,'image occurrence ambiguous')
 rels=im._docx_xml(z,'word/_rels/document.xml.rels');matches=[n for n in rels if n.get('Id')==rid];need(len(matches)==1,'relationship ambiguous');rel=matches[0];target=rel.get('Target','');need(rel.get('TargetMode','Internal')=='Internal' and rel.get('Type')==R[1:-1]+'/image','nonimage relationship')
 need(re.fullmatch(r'media/[A-Za-z0-9_.-]+\.png',target) is not None,'canonical PNG target')
 raw=im._docx_member(z,'word/'+target,im.MAX_BYTES);need(im.sha(raw)==c['image_sha256'],'original PNG bytes mismatch');info=im.image_info(raw);need(info['mime_type']=='image/png' and info['validator']=='png-subset-v1','unqualified PNG subtype')
 need(c['reviewed_body_sha256']==a.sha(item['body']),'fullbody hash changed')
 return set(dr.iter())

def source_projection(item,docx):
 # Read-only exact source projection. Independent semantic/style review still mandatory.
 W='{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
 loc=json.loads(item['source']['private_locator']);lo=loc['paragraph_start'];hi=loc['paragraph_end_inclusive']
 need(type(lo) is int and type(hi) is int and 0<=lo<=hi,'source range')
 with zipfile.ZipFile(io.BytesIO(docx)) as z:
  im._docx_inventory(z);doc=im._docx_xml(z,'word/document.xml');body=doc.find(W+'body');ps=body.findall(W+'p');need(hi<len(ps),'source range outside document')
  need(sha(NAV_HELPER)==NAV_SHA,'navigation helper changed');sp=importlib.util.spec_from_file_location('image_nav_gate',NAV_HELPER);nv=importlib.util.module_from_spec(sp);sp.loader.exec_module(nv);nv.inspect(z,doc,lo,hi)
  children=list(body);start=children.index(ps[lo]);end=children.index(ps[hi]);need(all(n.tag==W+'p' for n in children[start:end+1]),'table/nonparagraph in selected event')
  numbering=im._docx_xml(z,'word/numbering.xml') if 'word/numbering.xml' in z.namelist() else None
  ignored=validate_single_png(item,z,doc,ps,lo,hi)
  out=[]
  for p in ps[lo:hi+1]:
   np=p.find(W+'pPr/'+W+'numPr')
   if np is not None:
    ni=np.find(W+'numId');lv=np.find(W+'ilvl');need(ni is not None and numbering is not None,'unresolved numbering')
    nums=[x for x in numbering.findall(W+'num') if x.get(W+'numId')==ni.get(W+'val')];need(len(nums)==1 and not nums[0].findall(W+'lvlOverride'),'numbering override/ambiguity held')
    aid=nums[0].find(W+'abstractNumId');need(aid is not None,'missing abstract numbering')
    abstracts=[x for x in numbering.findall(W+'abstractNum') if x.get(W+'abstractNumId')==aid.get(W+'val')];need(len(abstracts)==1,'abstract numbering ambiguity')
    levels=[x for x in abstracts[0].findall(W+'lvl') if x.get(W+'ilvl')==(lv.get(W+'val') if lv is not None else '0')];need(len(levels)==1,'numbering level missing');fmt=levels[0].find(W+'numFmt');need(fmt is not None and fmt.get(W+'val')=='bullet','nonbullet automatic numbering not qualified')
   parents={c:n for n in p.iter() for c in n}
   for node in p.iter():
    if node in ignored:continue
    need(node.tag not in {W+'drawing',W+'pict',W+'object',W+'sym',W+'ins',W+'del',W+'fldSimple',W+'instrText',W+'fldChar',W+'altChunk'},'unsupported selected source node')
    if node.tag==W+'t':out.append(node.text or '')
    elif node.tag in [W+'br',W+'cr']:out.append('\n')
    elif node.tag==W+'tab':
     parent=parents.get(node)
     if parent is not None and parent.tag==W+'r':out.append('\t')
     else:
      while parent is not None and parent.tag!=W+'pPr':parent=parents.get(parent)
      need(parent is not None,'unknown tab position')
   out.append('\n')
  need(''.join(out)==item['body'],'original source text differs from full reviewed body')
  return [lo,hi]
def qualification(expected_baseline=True):
 need(CONFIGURED,'configure exact ticket first')
 need(sha(TICKET)==TICKET_SHA and sha(QUAL)==QUAL_SHA and sha(BATCH)==BATCH_SHA,'immutable artifacts changed')
 t=load(TICKET);q=load(QUAL);b=load(BATCH);c=current();folder=BATCH.parent
 exact_keys(q,['protocol','batch_sha256','pins','schema_sha256','features','items','sources','independent_reviews'],'profile')
 need(q['protocol']=='exact-reviewed-text-source-profile-v1' and q['batch_sha256']==BATCH_SHA,'profile identity')
 need('single_original_png_table_v1' in q['features'],'explicit image feature required');need(q['features'] and set(q['features'])<= {'plain_text','reviewed_ordinary_bullet','existing_company_primary','existing_theme_primary','single_original_png_table_v1'},'unqualified feature')
 need(c['pins']==q['pins']==pins() and c['identity']['schema_sha256']==q['schema_sha256'],'pins/schema drift')
 need(c['identity']==t['expected_baseline']['identity'],'database UUID/schema/scope drift')
 if expected_baseline:
  need(c==t['expected_baseline'] and c['mode']=='paused','locked baseline review stale')
  d=a.connect(MAIN,True)
  try:counts={'memo_count':d.execute('select count(*) from cp_memo').fetchone()[0],'receipt_count':d.execute('select count(*) from local_archive_receipt').fetchone()[0]}
  finally:d.close()
  need(counts==t['expected_counts'],'baseline counts differ')
 need(len(b['items'])==CREATED and b['items']==q['items'],'full ordered item metadata/source/relations differs')
 a.validate(b);need(b['batch_id']==t['ticket_id'],'batch/ticket ID differs')
 source_refs=[];source_data={}
 for source in q['sources']:
  exact_keys(source,['name','sha256','package_parts'],'source profile');name=filename(source['name']);h=hexhash(source['sha256']);path=folder/name
  need(name==h+'.docx' and sha(path)==h and h not in source_data,'source identity/uniqueness')
  data=fixed(path).read_bytes()
  with zipfile.ZipFile(io.BytesIO(data)) as z:
   im._docx_inventory(z);parts={n:a.sha(z.read(n)) for n in z.namelist()}
  need(parts==source['package_parts'],'exact package part inventory/hash mismatch')
  source_data[h]=data;source_refs.append({'path':str(path),'sha256':h})
 itemhashes=[];ranges=[]
 for item in b['items']:
  need(not any(item.get(k) for k in ['new_companies','original_vector_images','native_tables','image_record']),'existing entities only, no vector/native table')
  need(item.get('primary_subject',{}).get('kind')=='company' and item.get('companies'),'existing company primary required')
  ed=a.connect(MAIN,True)
  try:
   for edge in item['companies']:
    rr=ed.execute('select status from cp_company where id=?',(edge['company_id'],)).fetchone();need(rr is not None and rr[0]=='active','company endpoint must already be active')
  finally:ed.close()
  h=item['source']['content_hash'];need(h in source_data,'source not independently profiled')
  span=source_projection(item,source_data[h]);ranges.append({'source_sha256':h,'range':span,'body_sha256':a.sha(item['body'])});itemhashes.append(a.sha(a.canonical(item)))
 covered=set()
 for x in ranges:
  positions={(x['source_sha256'],n) for n in range(x['range'][0],x['range'][1]+1)};need(not covered.intersection(positions),'overlapping source ranges');covered.update(positions)
 need(q['independent_reviews'],'independent sealed review required');refs=[]
 for review in q['independent_reviews']:
  exact_keys(review,['name','sha256'],'review artifact');path=folder/filename(review['name']);need(sha(path)==hexhash(review['sha256']),'independent review changed')
  r=load(path);exact_keys(r,['protocol','item_sha256_ordered','source_ranges','source_sha256'],'independent review')
  need(r['protocol']=='independent-exact-text-review-v1' and r['item_sha256_ordered']==itemhashes and r['source_ranges']==ranges and sorted(r['source_sha256'])==sorted(source_data),'review does not bind exact full items/sources/ranges')
  refs.append({'path':str(path),'sha256':review['sha256']})
 # Supply original persistence routine only derived, validated paths; never ticket-supplied roots/code.
 validated={'approved_exact_artifacts':[{'source_artifacts':source_refs}],'artifacts':refs+[{'path':str(QUAL),'sha256':QUAL_SHA},{'path':str(NAV_HELPER),'sha256':NAV_SHA}],'historical_reference_payload':'qualified-text-class-no-current-batch-E2E'}
 return t,validated,b,c

def write_state(obj):
 need(fixed(RUN).parent==fixed(w.PROGRAM_ROOT/'正式续档'),'output root mismatch');a.atomic_json(fixed(STATE,False),obj)
@contextlib.contextmanager
def scheduler_lock():
 p=fixed(SCHEDULER_LOCK,False);need(p==SCHEDULER_LOCK,'lock path mismatch')
 fd=os.open(p,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
 try:yield
 finally:os.close(fd);p.unlink()
def verify_all(db,root):
 d=a.connect(fixed(db),True)
 try:
  need(d.execute('pragma integrity_check').fetchone()[0]=='ok','integrity check failed');need(not d.execute('pragma foreign_key_check').fetchall(),'FK failed')
  receipts=[json.loads(r[0]) for r in d.execute('select receipt_json from local_archive_receipt')]
  for receipt in receipts:a.verify_receipt(d,receipt)
  images=im.verify_all(d,fixed(root));return {'memo_count':d.execute('select count(*) from cp_memo').fetchone()[0],'receipt_count':len(receipts),'images':images}
 finally:d.close()
def receipt_readback(batch,db=MAIN):
 d=a.connect(fixed(db),True)
 try:
  row=d.execute('select * from local_archive_receipt where batch_id=?',(batch['batch_id'],)).fetchone()
  if not row:return None
  need(row['payload_sha256']==a.validate(batch),'same batch ID conflicting payload')
  receipt=json.loads(row['receipt_json']);a.verify_receipt(d,receipt)
  need(len(receipt['items'])==len(batch['items']),'receipt item count')
  for item,r in zip(batch['items'],receipt['items']):
   m=d.execute('select b.body,m.title,m.category,m.material_date,m.institution from cp_memo m join cp_body b on b.id=m.body_id where m.id=?',(r['memo_id'],)).fetchone()
   need(tuple(m)==(item['body'],item['title'],item['category'],item['material_date'],item['institution']),'per-item full body/metadata readback')
   need(r['action']=='created','unexpected duplicate/replayed source outcome')
   rows=d.execute('select image_sha256,source_sha256,role,private_locator from cp_memo_original_image where memo_id=?',(r['memo_id'],)).fetchall()
   cl=item['original_images'][0];need(len(rows)==1 and tuple(rows[0])[:3]==(cl['image_sha256'],cl['source_sha256'],cl['role']),'image binding readback differs')
   where=json.loads(json.loads(rows[0]['private_locator'])['location']);need(where.get('relationship_id')==cl['origin']['relationship_id'],'image source occurrence readback differs')
  return receipt
 finally:d.close()
def guard(p):return w.guard_path('production',p)

MODE_HELPER=MODE_HELPER_PATH
MODE_HELPER_SHA='d30389dc0930ea80849bba4744ba6cac6d38cc66f2a0d7e9461c87159f2d5aa8'
def mode_helper():
 need(sha(MODE_HELPER)==MODE_HELPER_SHA,'mode helper changed')
 spec=importlib.util.spec_from_file_location('qualified_mode_transition_v2',MODE_HELPER);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);return mod

def persist_source_and_evidence(batch,ticket,qualification):
 inp=fixed(MAIN.parent/'image-input');out=fixed(RUN)
 def exact_copy(src,dst,expected):
  data=fixed(src).read_bytes();need(a.sha(data)==expected,'copy source hash mismatch');dst=fixed(dst,False)
  if dst.exists():need(dst.read_bytes()==data,'existing destination differs')
  else:
   with dst.open('xb') as f:f.write(data);f.flush();os.fsync(f.fileno())
  need(sha(dst)==expected,'copy readback mismatch')
 sources={x['sha256']:x for ent in qualification['approved_exact_artifacts'] for x in ent['source_artifacts']}
 for i in batch['items']:
  h=i['source']['content_hash'];need(h in sources,'source outside ticket');exact_copy(pathlib.Path(sources[h]['path']),inp/(h+'.bin'),h)
 exact_copy(BATCH,out/'batch.json',BATCH_SHA)
 evidence=out/'source-review-evidence';evidence.mkdir(exist_ok=False)
 refs=[{'path':str(TICKET),'sha256':TICKET_SHA},{'path':str(QUAL),'sha256':QUAL_SHA},{'path':str(MODE_HELPER),'sha256':MODE_HELPER_SHA},{'path':str(pathlib.Path(__file__)),'sha256':sha(pathlib.Path(__file__))}]+qualification['artifacts']
 names=set();manifest=[]
 for n,ref in enumerate(refs):
  src=pathlib.Path(ref['path']);name=str(n).zfill(3)+'-'+src.name;need(name not in names,'evidence name collision');names.add(name)
  exact_copy(src,evidence/name,ref['sha256']);manifest.append({'original':str(src),'stored':name,'sha256':ref['sha256']})
 a.atomic_json(evidence/'manifest.json',manifest)
 a.atomic_json(out/'preflight-review.json',{'status':'passed_exact_artifact_qualification_no_current_batch_e2e','payload_sha256':BATCH_SHA,'ticket_sha256':TICKET_SHA,'qualification_sha256':QUAL_SHA,'current_batch_e2e_run':False,'historical_e2e_reference':qualification['historical_reference_payload'],'baseline':ticket['expected_baseline']})
def business_digest(db):
 d=a.connect(fixed(db),True)
 try:
  tables=[r[0] for r in d.execute("select name from sqlite_master where type='table' and (name like 'cp_%' or name in ('local_archive_receipt','local_memo_primary_subject')) order by name")]
  digests={}
  for table in tables:
   need(table.replace('_','').isalnum(),'unsafe schema identifier')
   rows=sorted(a.canonical([{'type':'blob','hex':v.hex()} if isinstance(v,bytes) else {'type':type(v).__name__,'value':v} for v in row]) for row in d.execute('select * from "'+table+'"'))
   digests[table]=a.sha(a.canonical(rows))
  return digests
 finally:d.close()
def owned_paused(intent):
 mh=mode_helper();return mh.verify_transition_events(MAIN,intent['baseline'],intent['event_ids'])
def post(batch,ticket,intent):
 st=owned_paused(intent);qualification(False)
 receipt=receipt_readback(batch);need(receipt is not None,'committed receipt absent')
 before=verify_all(MAIN,MAIN.parent/'images');live_business=business_digest(MAIN)
 need(before['memo_count']==ticket['expected_counts']['memo_count']+CREATED and before['receipt_count']==ticket['expected_counts']['receipt_count']+1,'unexpected ticket-derived count delta')
 write_state({'phase':'post_pending','batch_id':batch['batch_id'],'payload_sha256':BATCH_SHA,'intent':intent,'receipt':receipt,'observed':st,'current_batch_e2e_run':False})
 bundle=w.BACKUP_ROOT/('post-'+batch['batch_id']+'.bundle');restored=RESTORED
 need(not bundle.exists() and not restored.exists(),'partial/existing post artifacts: preserve and reconcile separately')
 info=im.bundle(MAIN,MAIN.parent/'images',fixed(bundle,False));need(current()==st,'live DB changed during snapshot')
 result=im.restore_bundle(bundle,info['manifest_sha256'],fixed(restored,False))
 need(verify_all(restored/'database.sqlite',restored/'images')==before,'restored counts/images/receipts differ')
 need(receipt_readback(batch,restored/'database.sqlite')==receipt,'restored exact receipt/readback differs')
 need(business_digest(restored/'database.sqlite')==live_business,'restored full business digest differs')
 need(current()==st,'live DB changed during restore')
 proof={'verified_at':a.now(),'status':'passed_actual_joint_restore','production_sha256':st['db_sha256'],'generation':st['generation'],'mode':st['mode'],'code_matches':True,'counts':{'memos':before['memo_count'],'receipts':before['receipt_count']},'restored':result,'images':before['images'],'all_receipts_verified':before['receipt_count'],'business_digest_equal':True,'business_digest_sha256':a.sha(a.canonical(live_business)),'integrity':'ok','foreign_key_errors':0,'current_batch_e2e_run':False}
 complete={'batch_id':batch['batch_id'],'payload_sha256':BATCH_SHA,'created':CREATED,'memos':before['memo_count'],'receipts':before['receipt_count'],'mode':st['mode'],'generation':st['generation'],'integrity':'ok','foreign_key_errors':0,'all_receipts_verified':before['receipt_count'],'images':before['images'],'postwrite_bundle':info,'automatic_continuous_runner':False,'current_batch_e2e_run':False,'qualification_sha256':QUAL_SHA}
 # Publish restore proof first; checkpoint never sees complete without verified restore proof.
 a.atomic_json(RUN/'final-restore-verification.json',proof);a.atomic_json(RUN/'execution-complete.json',complete)
 out={'phase':'completed','batch_id':batch['batch_id'],'payload_sha256':BATCH_SHA,'intent':intent,'qualification_sha256':QUAL_SHA,'ticket_sha256':TICKET_SHA,'current_batch_e2e_run':False,'post_bundle':info,'actual_restore':result,'readback':before,'final_state':st,'physical_backup_retention_enforced':False}
 write_state(out);return out

def execute():
 with scheduler_lock():
  ticket,q,b,baseline=qualification(True);need(baseline['mode']=='paused','not paused');mh=mode_helper()
  need(not RUN.exists(),'existing run: reconciliation, never blind replay');need(receipt_readback(b) is None,'already committed')
  fixed(RUN.parent);fixed(ROOT/'restores');RUN.mkdir();events=[str(uuid.uuid4()),str(uuid.uuid4())]
  intent={'phase':'intent','batch_id':b['batch_id'],'payload_sha256':BATCH_SHA,'baseline':baseline,'event_ids':events,'ticket_sha256':TICKET_SHA,'current_batch_e2e_run':False}
  write_state(intent);a.atomic_json(RUN/'execution-intent.json',intent)
  persist_source_and_evidence(b,ticket,q)
  identity={k:baseline['identity'][k] for k in ['database_uuid','schema_sha256']}
  context=None
  try:
   # Context encloses enable, enabled log, and engine. Even failed log pauses only own generation.
   with mh.enabled_owned(MAIN,baseline,events,pins) as context:
    enabled=context['enabled']
    write_state({'phase':'enabled_intent','batch_id':b['batch_id'],'payload_sha256':BATCH_SHA,'intent':intent,'enabled':enabled,'current_batch_e2e_run':False})
    def gate(d):
     guard(pathlib.Path(d.execute('pragma database_list').fetchone()[2]));row=w.check_control(d,'production',identity,enabled['generation'])
     need(sha(MAIN)==enabled['db_sha256'],'enabled DB hash drift');need(pins()==enabled['pins'],'pins changed')
     need(sha(BATCH)==BATCH_SHA and sha(TICKET)==TICKET_SHA and sha(QUAL)==QUAL_SHA and sha(MODE_HELPER)==MODE_HELPER_SHA,'ticket/helper changed')
     return {'generation':row['generation'],'mode':row['mode'],'scope':baseline['identity']['scope'],'code_identity':enabled['pins'],'qualification_sha256':QUAL_SHA,'current_batch_e2e_run':False}
    a._apply_engine(MAIN,b,identity,guard,gate,backup_root=w.backup_dir('production'),receipt_root=w.receipt_dir('production'))
  except Exception as ex:
   # Persisted original intent/event IDs remain sufficient for strict read-only reconciliation.
   try:write_state({'phase':'needs_reconcile','batch_id':b['batch_id'],'payload_sha256':BATCH_SHA,'intent':intent,'owned_context':context,'error':str(ex)})
   except Exception as log_error:raise RuntimeError('blocked: transaction outcome/log failure; inspect saved intent and events') from log_error
   raise
  return post(b,ticket,intent)
def reconcile():
 with scheduler_lock():
  t,q,b,c=qualification(False);st=load(STATE)
  need(st['batch_id']==b['batch_id'] and st.get('payload_sha256')==BATCH_SHA,'intent mismatch')
  if st['phase']=='completed':
   need(receipt_readback(b) is not None,'completed receipt missing')
   return {'kind':'verified_historical_completion','historical_result':st,'current_state':c,'current_state_matches_completion':c==st['final_state']}
  need(st['phase'] in ['intent','enabled_intent','needs_reconcile','post_pending'],'phase not automatically reconcilable')
  intent=st if st['phase']=='intent' else st['intent'];need(intent==load(RUN/'execution-intent.json'),'persisted intent mismatch')
  owned_paused(intent)
  need(receipt_readback(b) is not None,'no receipt: no automatic retry/reset')
  return post(b,t,intent)

def dry_run():
 t,q,b,c=qualification(True);mode_helper();need(receipt_readback(b) is None,'already committed')
 need(not SCHEDULER_LOCK.exists() and not MAIN.with_suffix('.writer.lock').exists(),'writer/scheduler lock present')
 return {'kind':'read_only_production_candidate_dry_run','payload_sha256':BATCH_SHA,'items':len(b['items']),'baseline':c,'ticket_sha256':TICKET_SHA,'qualification_sha256':QUAL_SHA,'current_batch_e2e_run':False,'executed':False,'files_written':False,'deployment_approved_by_this_test':False,'candidate_code_sha256':sha(pathlib.Path(__file__))}

if __name__=='__main__':
 import argparse
 parser=argparse.ArgumentParser();parser.add_argument('--ticket',required=True);parser.add_argument('--ticket-sha',required=True);parser.add_argument('--approved-profile-sha',required=True);parser.add_argument('--operation',choices=['dry-run','execute-reviewed','reconcile-reviewed'],default='dry-run');args=parser.parse_args()
 configure(args.ticket,args.ticket_sha,args.approved_profile_sha)
 if args.operation=='dry-run':out=dry_run()
 elif args.operation=='execute-reviewed':out=execute()
 else:out=reconcile()
 print(json.dumps(out,ensure_ascii=False,indent=2))
