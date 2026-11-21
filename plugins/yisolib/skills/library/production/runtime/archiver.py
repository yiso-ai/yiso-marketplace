"""Reviewed JSON -> cp_* append-only acceptance writer. Production writes disabled.
No model calls, network, arbitrary SQL, deletes, updates, or cloud control changes.
"""
import argparse, datetime, hashlib, json, os, pathlib, re, sqlite3, stat, uuid, zipfile, sys
from xml.etree import ElementTree as ET
import extensions
import primary_subject
import new_entities
import image_assets
ROOT=pathlib.Path(__file__).resolve().parent
PROTOCOL='cp-local-batch-v1'
def sha(x): return hashlib.sha256(x if isinstance(x,bytes) else x.encode('utf-8')).hexdigest()
def canonical(x): return json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def safe(path,exists=True):
 p=pathlib.Path(path).absolute()
 for a in (p,*p.parents):
  if not a.exists():
   if a==p and not exists:continue
   raise ValueError('missing path '+str(a))
  st=a.lstat()
  if stat.S_ISLNK(st.st_mode) or getattr(st,'st_file_attributes',0)&1024:raise ValueError('reparse/symlink rejected')
 return p.resolve(strict=exists)
def read_json(p):
 p=safe(p)
 if p.stat().st_size>16*1024*1024:raise ValueError('batch too large')
 return json.loads(p.read_text(encoding='utf-8-sig'))
def atomic_json(p,obj):
 p=safe(p,False);tmp=p.with_name(p.name+'.'+uuid.uuid4().hex+'.tmp')
 with tmp.open('x',encoding='utf-8',newline='\n') as f:f.write(canonical(obj));f.flush();os.fsync(f.fileno())
 os.replace(tmp,p)
def connect(p,ro=False):
 d=sqlite3.connect(safe(p).as_uri()+('?mode=ro' if ro else '?mode=rw'),uri=True,timeout=5)
 d.row_factory=sqlite3.Row;d.execute('pragma foreign_keys=on');d.execute('pragma synchronous=FULL');return d

def extract(path):
 p=safe(path);raw=p.read_bytes()
 if len(raw)>32*1024*1024:raise ValueError('source too large')
 suffix=p.suffix.lower();tables=[]
 if suffix in ('.txt','.md'):body=raw.decode('utf-8-sig')
 elif suffix=='.docx':
  with zipfile.ZipFile(p) as z:
   info=z.getinfo('word/document.xml')
   if info.file_size>16*1024*1024:raise ValueError('DOCX XML too large')
   xml=z.read(info)
  if b'<!DOCTYPE' in xml or b'<!ENTITY' in xml:raise ValueError('XML entities forbidden')
  root=ET.fromstring(xml);ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
  chunks=[]
  def text(el):return ''.join(n.text or '' for n in el.iter() if n.tag.endswith('}t'))
  for el in root.find('w:body',ns):
   if el.tag.endswith('}p'):chunks.append(text(el))
   elif el.tag.endswith('}tbl'):
    rows=[[text(c) for c in r.findall('w:tc',ns)] for r in el.findall('w:tr',ns)]
    tables.append(rows);chunks.append('\n'.join('\t'.join(r) for r in rows))
  body='\n'.join(chunks)
 else:raise ValueError('PDF/image/other require reviewed cloud text extraction; never OCR silently')
 if not body.strip() or '\x00' in body:raise ValueError('empty/invalid body')
 return {'source_sha256':sha(raw),'body':body,'body_sha256':sha(body),'native_tables':tables,'limitations':['text/native tables only; images excluded','DOCX headers, footnotes and tracked changes require review'] if suffix=='.docx' else []}

def validate(batch):
 if set(batch)!={'protocol','batch_id','items'} or batch['protocol'] not in (PROTOCOL,'cp-local-batch-v2','cp-local-batch-v3'):raise ValueError('invalid batch contract')
 if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',batch['batch_id']):raise ValueError('unsafe batch_id')
 if not isinstance(batch['items'],list) or not 1<=len(batch['items'])<=100:raise ValueError('1..100 items required')
 seen=set()
 for i in batch['items']:
  required={'source','body','title','category','material_date','institution','review','companies','themes'}
  if not required<=set(i) or set(i)-required-{'native_tables','relations','relevance','primary_subject','new_companies','original_images','image_record','original_vector_images'}:raise ValueError('unknown/missing item keys')
  if i.get('original_vector_images') and batch['protocol']!='cp-local-batch-v3':raise ValueError('vector images require explicit batch v3')
  image_assets.validate_item(i)
  extensions.validate(i)
  new_entities.validate(i)
  if i['category'] not in ('internal','sellside','news') or not isinstance(i['body'],str) or not i['body'].strip() or '\x00' in i['body']:raise ValueError('invalid body/category')
  if not isinstance(i['title'],str) or not i['title'].strip():raise ValueError('title required')
  if len(i['body'])>2000000:raise ValueError('body too large')
  if i['material_date'] is not None:datetime.date.fromisoformat(i['material_date'])
  s=i['source']
  if set(s)!={'namespace','record_key','source_type','content_hash','private_locator'}:raise ValueError('source keys')
  if any(not isinstance(s[k],str) or not s[k] for k in s):raise ValueError('source metadata required')
  if not re.fullmatch('[a-f0-9]{64}',s['content_hash']):raise ValueError('source SHA256 required')
  key=(s['namespace'],s['record_key'])
  if key in seen:raise ValueError('repeated source within batch')
  seen.add(key);r=i['review']
  if set(r)!={'reviewer','reviewed_at','reason','dedup_checked'} or r['dedup_checked'] is not True or not r['reason'] or not r['reviewer']:raise ValueError('explicit semantic review required')
  dt=datetime.datetime.fromisoformat(r['reviewed_at'])
  if dt.tzinfo is None:raise ValueError('review timestamp timezone required')
  primary_subject.validate(i,batch['protocol'])
  for kind in ('companies','themes'):
   ids=set()
   for a in i[kind]:
    field='company_id' if kind=='companies' else 'theme_id'
    keys={field,'reason','evidence'}|({'scope_kind'} if kind=='companies' else set())
    if set(a)!=keys or not a['reason'] or a[field] in ids:raise ValueError('invalid/duplicate assignment')
    ids.add(a[field])
    if kind=='companies' and a['scope_kind'] not in ('primary','substantive','mention'):raise ValueError('scope invalid')
    e=a['evidence']
    if set(e)!={'quote','start_offset','end_offset','claim_kind'}:raise ValueError('evidence fields')
    start,end=e['start_offset'],e['end_offset']
    if type(start)!=int or type(end)!=int or not 0<=start<end<=len(i['body']) or i['body'][start:end]!=e['quote'] or not e['quote'].strip():raise ValueError('Unicode evidence mismatch')
    if e['claim_kind'] not in ('fact','source_opinion','unverified'):raise ValueError('claim_kind invalid')
 new_entities.validate_batch(batch)
 return sha(canonical(batch))

def schema_hash(d):
 return sha(canonical([list(r) for r in d.execute("select type,name,tbl_name,sql from sqlite_master where sql is not null order by type,name")]))
def install_identity(d):
 if d.execute("select 1 from local_archive_identity where id='database_uuid'").fetchone():raise ValueError('identity already installed')
 d.execute("insert into local_archive_identity values('database_uuid',?)",(str(uuid.uuid4()),))
 d.execute("insert into local_archive_identity values('schema_sha256',?)",(schema_hash(d),))
def schema_lineage(d):
 values=dict(d.execute('select id,scope from local_archive_identity'))
 if 'database_uuid' not in values or 'schema_sha256' not in values:raise ValueError('database identity missing')
 lineage=[values['schema_sha256']];previous='0'*64
 if d.execute("select 1 from sqlite_master where type='table' and name='local_archive_schema_revision'").fetchone():
  for expected_revision,row in enumerate(d.execute('select * from local_archive_schema_revision order by revision'),1):
   payload=json.loads(row['payload_json'])
   if row['revision']!=expected_revision or row['previous_schema_sha256']!=lineage[-1] or row['previous_revision_sha256']!=previous or row['revision_sha256']!=sha(canonical(payload)) or payload['previous_schema_sha256']!=row['previous_schema_sha256'] or payload['schema_sha256']!=row['schema_sha256'] or payload['target_uuid']!=values['database_uuid'] or payload['target_scope']!=values['main'] or payload['to_generation']!=payload['from_generation']+1:raise ValueError('schema revision chain invalid')
   lineage.append(row['schema_sha256']);previous=row['revision_sha256']
 return values,lineage

def target_identity(d):
 values,lineage=schema_lineage(d)
 if lineage[-1]!=schema_hash(d):raise ValueError('missing database UUID or schema drift')
 return {'database_uuid':values['database_uuid'],'schema_sha256':lineage[-1],'scope':values['main']}

def receipt_target_valid(d,target):
 current=target_identity(d);_,lineage=schema_lineage(d)
 return isinstance(target,dict) and set(target)==set(current) and target['database_uuid']==current['database_uuid'] and target['scope']==current['scope'] and target['schema_sha256'] in lineage

def stable_item_hash(i):
 x=json.loads(canonical(i));x['review'].pop('reviewed_at')
 for p in x.get('new_companies',[]):p['review'].pop('reviewed_at')
 return sha(canonical(x))

def prepare_copy(main,target):
 main=safe(main);target=safe(target,False)
 if target.name!='acceptance.sqlite' or target.parent!=ROOT or target.exists():raise ValueError('new fixed isolation target only')
 a=connect(main,True);b=sqlite3.connect(target)
 try:
  a.backup(b)
  b.execute('create table local_archive_identity(id text primary key, scope text not null)')
  b.execute("insert into local_archive_identity values('main','acceptance-only')")
  b.execute('create table local_archive_receipt(batch_id text primary key,payload_sha256 text not null,receipt_json text not null)')
  primary_subject.install(b)
  new_entities.install_guards(b)
  install_identity(b)
  b.commit()
 finally:a.close();b.close()
 return {'path':str(target),'sha256':sha(target.read_bytes())}

def check_target(p):
 p=safe(p)
 if p.name!='acceptance.sqlite' or p.parent!=ROOT:raise ValueError('production disabled: fixed acceptance database only')
 d=connect(p)
 try:
  if d.execute("select scope from local_archive_identity where id='main'").fetchone()[0]!='acceptance-only':raise ValueError('identity mismatch')
 finally:d.close()
 return p

def verify_receipt(d,receipt):
 if not receipt_target_valid(d,receipt.get('target')):raise ValueError('receipt database identity/schema mismatch; legacy receipts require explicit reconciliation')
 for x in receipt['items']:
  m=d.execute('select m.body_id,b.sha256,b.body from cp_memo m join cp_body b on b.id=m.body_id where m.id=?',(x['memo_id'],)).fetchone()
  if not m or m['sha256']!=x['body_sha256'] or sha(m['body'])!=x['body_sha256']:raise ValueError('canonical readback mismatch')
  if not d.execute('select 1 from cp_memo_source where memo_id=? and source_id=?',(x['memo_id'],x['source_id'])).fetchone():raise ValueError('source link missing')
  extensions.duplicate_check(d,x.get('extension_plan',{}),x['memo_id'])
  primary_subject.verify(d,x.get('primary_subject'),x['memo_id'])
  new_entities.verify(d,x.get('new_companies',[]),x['memo_id'],x['source_id'])
  image_assets.verify_records(d,x.get('original_image_records',[]),x['memo_id'],x['source_id'])
  image_assets.verify_image_record_receipt(d,x.get('image_record'),x['memo_id'],x['source_id'])
  image_assets.verify_vector_records(d,x.get('vector_image_records',[]),x['memo_id'],x['source_id'])
 return receipt

def apply(db,batch,expected_target,failpoint=None):
 return _apply_engine(db,batch,expected_target,check_target,None,failpoint)

def _apply_engine(db,batch,expected_target,target_guard,state_guard,failpoint=None,backup_root=None,receipt_root=None):
 payload_hash=validate(batch);p=target_guard(db);lock=p.with_suffix('.writer.lock');fd=None;d=None
 try:
  fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
  d=connect(p)
  control_context=state_guard(d) if state_guard else None
  if not isinstance(expected_target,dict) or set(expected_target)!={'database_uuid','schema_sha256'}:raise ValueError('expected target UUID/schema required')
  if batch['protocol']=='cp-local-batch-v3':image_assets.vector_state(d,True)
  observed=target_identity(d)
  if any(expected_target[k]!=observed[k] for k in expected_target):raise ValueError('expected target identity mismatch before write')
  prior=d.execute('select * from local_archive_receipt where batch_id=?',(batch['batch_id'],)).fetchone()
  if prior:
   if prior['payload_sha256']!=payload_hash:raise ValueError('batch ID payload changed')
   result=verify_receipt(d,json.loads(prior['receipt_json']));result['replayed']=True;result['verified_against_target']=target_identity(d);result['replay_control']=control_context;atomic_json((safe(receipt_root) if receipt_root is not None else p.parent)/('receipt-'+batch['batch_id']+'.json'),result);return result
  identity=target_identity(d);before_backup_version=d.execute('pragma data_version').fetchone()[0]
  backup=(safe(backup_root) if backup_root is not None else p.parent)/('backup-'+batch['batch_id']+'-'+uuid.uuid4().hex+'.sqlite')
  b=sqlite3.connect(backup)
  try:d.backup(b)
  finally:b.close()
  image_store=image_assets.store_root(d,True)
  image_backup=image_assets.snapshot_bundle(backup,image_store,backup.with_suffix('.bundle'))
  d.execute('BEGIN IMMEDIATE')
  if state_guard and state_guard(d)!=control_context:raise ValueError('control context changed before write lock')
  locked_identity=target_identity(d)
  if any(expected_target[k]!=locked_identity[k] for k in expected_target):raise ValueError('expected target identity mismatch after write lock')
  if d.execute('pragma data_version').fetchone()[0]!=before_backup_version:raise ValueError('external writer changed database during backup; retry for a fresh recovery point')
  result={'batch_id':batch['batch_id'],'payload_sha256':payload_hash,'committed_at':now(),'backup':str(backup),'backup_kind':'pretransaction_consistent_snapshot','target':identity,'control':control_context,'image_backup_bundle':{'path':str(backup.with_suffix('.bundle')),**image_backup},'items':[]}
  for i in batch['items']:
   image_assets.precheck_image_record(d,i)
   new_entities.upsert(d,i)
   s=i['source'];bh=sha(i['body']);ih=stable_item_hash(i);sid='local-src-'+sha(s['namespace']+'\x00'+s['record_key'])
   old=d.execute('select * from cp_source_record where namespace=? and record_key=?',(s['namespace'],s['record_key'])).fetchone()
   if old:
    meta=json.loads(old['metadata_json'])
    if old['content_hash']!=s['content_hash'] or (meta.get('local_item_stable_sha256')!=ih and meta.get('local_item_sha256')!=sha(canonical(i))):raise ValueError('existing source requires revision/reconciliation review')
    links=d.execute('select memo_id from cp_memo_source where source_id=?',(old['id'],)).fetchall()
    if len(links)!=1:raise ValueError('ambiguous source links')
    mid=links[0][0];sid=old['id'];action='replayed_source'
   else:
    existing=d.execute('select b.id,b.body,m.id memo_id from cp_body b left join cp_memo m on m.body_id=b.id and m.lifecycle=\'active\' where b.sha256=?',(bh,)).fetchall()
    if existing:
     if len(existing)!=1 or existing[0]['body']!=i['body'] or not existing[0]['memo_id']:raise ValueError('duplicate ambiguity')
     mid=existing[0]['memo_id'];action='exact_duplicate'
     metadata=d.execute('select category,material_date,title,institution from cp_memo where id=?',(mid,)).fetchone()
     if tuple(metadata)!=(i['category'],i['material_date'],i['title'],i['institution']):raise ValueError('duplicate metadata difference requires reconciliation')
     # Existing body is reused, never modified; new assignments require separate reviewed plan.
     actual={(r[0],r[1]) for r in d.execute("select company_id,scope_kind from cp_memo_company where memo_id=? and review_status='approved'",(mid,))}
     wanted={(a['company_id'],a['scope_kind']) for a in i['companies']}
     themes={r[0] for r in d.execute("select theme_id from cp_memo_theme where memo_id=? and review_status='approved'",(mid,))}
     if actual!=wanted or themes!={a['theme_id'] for a in i['themes']}:raise ValueError('duplicate assignment difference requires reconciliation')
     extensions.duplicate_check(d,i,mid)
     if i.get('original_vector_images'):raise ValueError('existing Memo vector backfill requires separate reviewed revision; forward-only')
     if i.get('original_images'):
      image_assets.check_image_record_duplicate(d,i,mid)
    else:
     bid='local-body-'+bh;mid='local-memo-'+bh;action='created';r=i['review']
     d.execute('insert into cp_body values(?,?,?)',(bid,i['body'],bh))
     d.execute('insert into cp_memo values(?,?,?,?,?,?,?,?,?)',(mid,mid,bid,i['category'],i['material_date'],i['title'],i['institution'],'known' if i['material_date'] else 'unknown','active'))
     d.execute('insert into cp_memo_revision values(?,1,?,?,?)',(mid,bid,r['reviewed_at'],r['reason']))
     for kind in ('companies','themes'):
      for n,a in enumerate(i[kind]):
       field='company_id' if kind=='companies' else 'theme_id';table='cp_company' if kind=='companies' else 'cp_theme'
       if not d.execute('select 1 from '+table+" where id=? and status='active'",(a[field],)).fetchone():raise ValueError('unknown/inactive entity')
       e=a['evidence'];eid=mid+'-'+kind+'-'+str(n)
       d.execute('insert into cp_evidence values(?,?,?,?,?,?,?,?,?,?,?)',(eid,mid,bid,e['quote'],e['start_offset'],e['end_offset'],'unicode:'+str(e['start_offset']),e['claim_kind'],'approved',r['reviewer'],r['reviewed_at']))
       if kind=='companies':d.execute('insert into cp_memo_company values(?,?,?,?,?,?)',(mid,a[field],a['scope_kind'],a['reason'],'approved',eid))
       else:d.execute('insert into cp_memo_theme values(?,?,?,?,?)',(mid,a[field],a['reason'],'approved',eid))
     primary_subject.insert(d,i,mid,bid)
     extensions.apply(d,i,mid,bid)
     for stage in ('body_import','semantic_review'):
      d.execute('insert into cp_processing values(?,?,1,?,?,?,?,?)',(mid,stage,'passed',bid,r['reviewer'],r['reviewed_at'],r['reason']))
    d.execute('insert into cp_source_record values(?,?,?,?,?,?,?,?)',(sid,s['namespace'],s['record_key'],s['source_type'],s['content_hash'],s['private_locator'],canonical({'local_item_stable_sha256':ih,'body_sha256':bh,'review':i['review'],'native_origins':[{'source_sha256':t['source_sha256'],'native_origin':t['native_origin']} for t in i.get('native_tables',[])]}),'parsed'))
    d.execute('insert into cp_memo_source values(?,?,?)',(mid,sid,'duplicate' if action=='exact_duplicate' else 'primary'))
   new_entities.resolve(d,i,mid)
   vector_records=image_assets.apply_vectors(d,i,mid,sid)
   image_record=image_assets.apply_image_record(d,i,mid,sid)
   image_records=image_assets.apply_images(d,i,mid,sid) if i.get('original_images') else []
   result['items'].append({'memo_id':mid,'source_id':sid,'action':action,'body_sha256':bh,'review':i['review'],'new_companies':i.get('new_companies',[]),'primary_subject':i.get('primary_subject'),'extension_plan':{k:i.get(k,[]) for k in ('native_tables','relations','relevance')},'original_image_records':image_records,'image_record':image_record})
   if vector_records:result['items'][-1]['vector_image_records']=vector_records
   if failpoint=='after_item':raise RuntimeError('injected precommit failure')
   if failpoint=='hard_exit_after_item':os._exit(96)
  verify_receipt(d,result)
  if d.execute('pragma foreign_key_check').fetchone():raise ValueError('foreign key violation')
  d.execute("insert into cp_body_fts(cp_body_fts,rank) values('integrity-check',1)")
  d.execute('insert into local_archive_receipt values(?,?,?)',(batch['batch_id'],payload_hash,canonical(result)))
  d.commit()
  if failpoint=='after_commit':raise RuntimeError('injected lost response')
  verify_receipt(d,result);atomic_json((safe(receipt_root) if receipt_root is not None else p.parent)/('receipt-'+batch['batch_id']+'.json'),result);return result
 finally:
  if d:d.close()
  if fd is not None:os.close(fd);lock.unlink()

def main():
 ap=argparse.ArgumentParser();sub=ap.add_subparsers(dest='command',required=True)
 for name in ('validate','apply'):
  a=sub.add_parser(name);a.add_argument('--batch',required=True)
  if name=='apply':
   a.add_argument('--db',required=True);a.add_argument('--expected-database-uuid',required=True);a.add_argument('--expected-schema-sha256',required=True)
 a=sub.add_parser('apply-stdin');a.add_argument('--db',required=True)
 a=sub.add_parser('extract');a.add_argument('--source',required=True);a.add_argument('--output',required=True)
 a=sub.add_parser('prepare-copy');a.add_argument('--main',required=True);a.add_argument('--target',required=True)
 args=ap.parse_args()
 if args.command=='validate':out={'validated_sha256':validate(read_json(args.batch))}
 elif args.command=='apply':out=apply(args.db,read_json(args.batch),{'database_uuid':args.expected_database_uuid,'schema_sha256':args.expected_schema_sha256})
 elif args.command=='apply-stdin':
  raw=sys.stdin.buffer.read(2*1024*1024+1)
  if len(raw)>2*1024*1024:raise ValueError('stdin envelope exceeds 2 MiB')
  envelope=json.loads(raw.decode('ascii'))
  if set(envelope)!={'batch','expected_sha256','expected_target'}:raise ValueError('stdin envelope keys')
  if sha(canonical(envelope['batch']))!=envelope['expected_sha256']:raise ValueError('stdin payload hash mismatch')
  out=apply(args.db,envelope['batch'],envelope['expected_target'])
 elif args.command=='extract':out=extract(args.source);atomic_json(pathlib.Path(args.output),out)
 else:out=prepare_copy(args.main,args.target)
 print(json.dumps(out,ensure_ascii=True,sort_keys=True,separators=(',',':')))
if __name__=='__main__':main()

