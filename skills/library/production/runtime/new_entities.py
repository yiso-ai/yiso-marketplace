"""Reviewed named research entities. No security identifiers, mergers or ticker guesses."""
import hashlib,json,re,unicodedata
SCHEME='owner_research_key_v1'
def canon(v):return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def sha(s):return hashlib.sha256(s.encode('utf-8')).hexdigest()
def norm(s):return ' '.join(unicodedata.normalize('NFKC',s).casefold().split())
def company_id(key):return 'local-entity-'+sha(SCHEME+'\x00'+key)
def stable_plan(p):
 x=json.loads(canon(p));x['review'].pop('reviewed_at');return x

def validate(i):
 plans=i.get('new_companies',[]);seen=set()
 for p in plans:
  if set(p)!={'identity_key','company_id','name','aliases','evidence','review'}:raise ValueError('new entity fields; security identifiers forbidden')
  if not re.fullmatch('[a-z0-9][a-z0-9._/-]{2,150}',p['identity_key']) or p['company_id']!=company_id(p['identity_key']):raise ValueError('stable company identity mismatch')
  if p['company_id'] in seen:raise ValueError('duplicate new company proposal')
  seen.add(p['company_id'])
  if not isinstance(p['name'],str) or not p['name'].strip() or len(p['name'])>300:raise ValueError('entity name invalid')
  if not isinstance(p['aliases'],list) or len(p['aliases'])>20 or any(not isinstance(x,str) or not x.strip() for x in p['aliases']):raise ValueError('entity alias invalid')
  names=[p['name']]+p['aliases']
  if len({norm(x) for x in names})!=len(names):raise ValueError('duplicate normalized entity aliases')
  e=p['evidence']
  if set(e)!={'quote','start_offset','end_offset'}:raise ValueError('entity evidence fields')
  s,t=e['start_offset'],e['end_offset']
  if type(s)!=int or type(t)!=int or not 0<=s<t<=len(i['body']) or i['body'][s:t]!=e['quote']:raise ValueError('entity evidence Unicode mismatch')
  if any(x not in e['quote'] for x in names):raise ValueError('entity name/alias must occur verbatim in evidence')
  r=p['review']
  if set(r)!={'reviewer','reviewed_at','reason','disambiguation_checked','security_identity_not_asserted'} or r['disambiguation_checked'] is not True or r['security_identity_not_asserted'] is not True or not r['reason'] or not r['reviewer']:raise ValueError('explicit entity disambiguation review required')
  import datetime
  if datetime.datetime.fromisoformat(r['reviewed_at']).tzinfo is None:raise ValueError('entity review timestamp timezone required')
  if p['company_id'] not in {x['company_id'] for x in i['companies'] if x['scope_kind'] in ('primary','substantive')}:raise ValueError('new entity requires substantive current memo assignment')

def upsert(d,i):
 for p in i.get('new_companies',[]):
  cid=p['company_id'];names=[p['name']]+p['aliases'];normalized={norm(x) for x in names}
  # Whole registry scan deliberately includes inactive/merged names: do not resurrect or merge.
  collisions={r[0] for r in d.execute('select id,name from cp_company') if norm(r[1]) in normalized and r[0]!=cid}
  collisions.update(r[0] for r in d.execute('select company_id,alias from cp_company_alias') if norm(r[1]) in normalized and r[0]!=cid)
  if collisions:raise ValueError('entity name/alias collides with existing registry; explicit resolution required')
  row=d.execute('select company_id,verification_status,source from cp_company_identifier where scheme=? and value=? and exchange=?',(SCHEME,p['identity_key'],'')).fetchone()
  existing=d.execute('select name,entity_type,status from cp_company where id=?',(cid,)).fetchone()
  source=canon({'scheme':SCHEME,'identity_key':p['identity_key'],'name':p['name'],'aliases':p['aliases'],'security_identity_not_asserted':True})
  if row or existing:
   if not row or tuple(row)!=(cid,'unverified',source) or not existing or tuple(existing)!=(p['name'],'unverified_issuer_or_entity','active'):raise ValueError('existing entity identity/metadata conflict')
   actual={(r[0],r[1],r[2]) for r in d.execute('select alias,normalized_alias,source from cp_company_alias where company_id=?',(cid,))}
   verify_alias_set(d,cid,{(x,norm(x),source) for x in names})
  else:
   d.execute('insert into cp_company values(?,?,?,?)',(cid,p['name'],'unverified_issuer_or_entity','active'))
   d.execute('insert into cp_company_identifier values(?,?,?,?,?,?)',(cid,SCHEME,p['identity_key'],'','unverified',source))
   for x in names:d.execute('insert into cp_company_alias values(?,?,?,?,?)',(cid,x,norm(x),None,source))

def resolve(d,i,mid):
 for p in i.get('new_companies',[]):
  rid='local-resolution-'+sha(mid+'\x00'+p['company_id']+'\x00'+i['source']['namespace']+'\x00'+i['source']['record_key']);source=canon({'proposal':stable_plan(p),'source':i['source'],'body_sha256':sha(i['body']),'original_reviewed_at':p['review']['reviewed_at']})
  old=d.execute('select canonical_company_id,source_name,decision,reason,source_json,reviewed_by from cp_entity_resolution where id=?',(rid,)).fetchone()
  if old:
   actual=json.loads(old[4]);wanted=json.loads(source);actual.pop('original_reviewed_at',None);wanted.pop('original_reviewed_at',None)
   if tuple(old[:4])!=(p['company_id'],p['name'],'approved_named_reference',p['review']['reason']) or actual!=wanted or old[5]!=p['review']['reviewer']:raise ValueError('entity resolution replay mismatch')
  else:d.execute('insert into cp_entity_resolution values(?,?,?,?,?,?,?,?,?,?)',(rid,None,mid,p['company_id'],p['name'],'approved_named_reference',p['review']['reason'],source,p['review']['reviewer'],p['review']['reviewed_at']))

def install_guards(d):
 for table in ('cp_company','cp_company_alias','cp_company_identifier','cp_entity_resolution'):
  for action in ('update','delete'):
   d.execute('CREATE TRIGGER local_entity_'+table+'_'+action+' BEFORE '+action+' ON '+table+" BEGIN SELECT RAISE(ABORT,'entity records append-only; reviewed migration required'); END")

def validate_batch(batch):
 names={}
 for item in batch['items']:
  for p in item.get('new_companies',[]):
   for name in [p['name']]+p['aliases']:
    key=norm(name);owner=names.get(key)
    if owner is not None and owner!=p['company_id']:raise ValueError('batch normalized entity name/alias collision')
    names[key]=p['company_id']

def verify(d,plans,mid,source_id):
 if not plans:return
 src=d.execute('select namespace,record_key,source_type,content_hash,private_locator,processing_status from cp_source_record where id=?',(source_id,)).fetchone()
 if not src or src[5]!='parsed':raise ValueError('entity receipt source missing/unparsed')
 actual_source=dict(zip(('namespace','record_key','source_type','content_hash','private_locator'),src[:5]))
 memo=d.execute('select m.body_id,b.body,b.sha256,m.lifecycle from cp_memo m join cp_body b on b.id=m.body_id where m.id=?',(mid,)).fetchone()
 if not memo or memo[3]!='active' or sha(memo[1])!=memo[2]:raise ValueError('entity receipt current body invalid')
 for p in plans:
  cid=p['company_id'];row=d.execute('select name,entity_type,status from cp_company where id=?',(cid,)).fetchone()
  if not row or tuple(row)!=(p['name'],'unverified_issuer_or_entity','active'):raise ValueError('entity receipt metadata mismatch')
  rid='local-resolution-'+sha(mid+'\x00'+cid+'\x00'+src[0]+'\x00'+src[1])
  r=d.execute('select source_json,decision,canonical_company_id,source_name,reason,reviewed_by,reviewed_at from cp_entity_resolution where id=? and memo_id=?',(rid,mid)).fetchone()
  names=[p['name']]+p['aliases'];source=canon({'scheme':SCHEME,'identity_key':p['identity_key'],'name':p['name'],'aliases':p['aliases'],'security_identity_not_asserted':True})
  ident=d.execute('select company_id,verification_status,source from cp_company_identifier where scheme=? and value=? and exchange=?',(SCHEME,p['identity_key'],'')).fetchone()
  if not ident or tuple(ident)!=(cid,'unverified',source):raise ValueError('entity receipt research identity mismatch')
  actual={(x[0],x[1],x[2]) for x in d.execute('select alias,normalized_alias,source from cp_company_alias where company_id=?',(cid,))}
  verify_alias_set(d,cid,{(x,norm(x),source) for x in names})
  if not r or tuple(r[1:6])!=('approved_named_reference',cid,p['name'],p['review']['reason'],p['review']['reviewer']):raise ValueError('entity receipt resolution metadata mismatch')
  provenance=json.loads(r[0])
  if provenance.get('proposal')!=stable_plan(p):raise ValueError('entity receipt resolution proposal mismatch')
  if provenance.get('source')!=actual_source:raise ValueError('entity receipt source provenance mismatch')
  if provenance.get('body_sha256')!=memo[2]:raise ValueError('entity receipt body provenance mismatch')
  if provenance.get('original_reviewed_at')!=r[6]:raise ValueError('entity receipt review provenance mismatch')
  e=p['evidence'];start,end=e['start_offset'],e['end_offset']
  if not 0<=start<end<=len(memo[1]) or memo[1][start:end]!=e['quote'] or any(name not in e['quote'] for name in names):raise ValueError('entity receipt proposal evidence differs from current body')
  assoc=d.execute('select a.scope_kind,a.review_status,e.body_id,e.quote,e.start_offset,e.end_offset,e.review_status from cp_memo_company a join cp_evidence e on e.id=a.evidence_id and e.memo_id=a.memo_id where a.memo_id=? and a.company_id=?',(mid,cid)).fetchone()
  if not assoc or assoc[0] not in ('primary','substantive') or assoc[1]!='approved' or assoc[2]!=memo[0] or assoc[6]!='approved' or not 0<=assoc[4]<assoc[5]<=len(memo[1]) or memo[1][assoc[4]:assoc[5]]!=assoc[3]:raise ValueError('entity receipt current approved assignment/evidence mismatch')

# Reviewed alias additions preserve the immutable original entity proposal.
ALIAS_PROTOCOL='reviewed_alias_append_v1'

def alias_certificate_id(cid,normalized):
 return 'local-alias-certificate-'+sha(ALIAS_PROTOCOL+'\x00'+cid+'\x00'+normalized)

def validate_alias_plan(d,p):
 fields={'company_id','canonical_name','alias','normalized_alias','language','memo_id','body_id','body_sha256','source_id','source','evidence','review'}
 if set(p)!=fields:raise ValueError('alias plan fields')
 for k in ('company_id','canonical_name','alias','normalized_alias','memo_id','body_id','body_sha256','source_id'):
  if not isinstance(p[k],str) or not p[k] or '\x00' in p[k]:raise ValueError('alias plan string')
 if len(p['alias'])>300 or p['alias']!=p['alias'].strip() or norm(p['alias'])!=p['normalized_alias'] or norm(p['canonical_name'])==p['normalized_alias']:raise ValueError('alias normalization/name invalid')
 if p['language'] not in ('en','zh'):raise ValueError('alias language requires explicit en/zh')
 if not re.fullmatch('[a-f0-9]{64}',p['body_sha256']):raise ValueError('alias body SHA invalid')
 r=p['review']
 if set(r)!={'reviewer','reviewed_at','reason','disambiguation_checked','security_identity_not_asserted'} or r['disambiguation_checked'] is not True or r['security_identity_not_asserted'] is not True or any(not isinstance(r[k],str) or not r[k].strip() for k in ('reviewer','reviewed_at','reason')):raise ValueError('alias explicit semantic review required')
 import datetime
 if datetime.datetime.fromisoformat(r['reviewed_at']).tzinfo is None:raise ValueError('alias review timezone required')
 row=d.execute('select name,status from cp_company where id=?',(p['company_id'],)).fetchone()
 if not row or tuple(row)!=(p['canonical_name'],'active'):raise ValueError('alias canonical entity mismatch')
 # Includes inactive/merged entities. Never merge or create another issuer.
 conflicts={x[0] for x in d.execute('select id,name from cp_company') if norm(x[1])==p['normalized_alias'] and x[0]!=p['company_id']}
 conflicts.update(x[0] for x in d.execute('select company_id,alias from cp_company_alias') if norm(x[1])==p['normalized_alias'] and x[0]!=p['company_id'])
 if conflicts:raise ValueError('alias collision across company registry')
 memo=d.execute('select m.body_id,b.body,b.sha256,m.lifecycle from cp_memo m join cp_body b on b.id=m.body_id where m.id=?',(p['memo_id'],)).fetchone()
 if not memo or memo[3]!='active' or memo[0]!=p['body_id'] or memo[2]!=p['body_sha256'] or sha(memo[1])!=p['body_sha256']:raise ValueError('alias proof body mismatch')
 e=p['evidence']
 if set(e)!={'quote','start_offset','end_offset'}:raise ValueError('alias evidence fields')
 start,end=e['start_offset'],e['end_offset']
 if type(start)!=int or type(end)!=int or not 0<=start<end<=len(memo[1]) or memo[1][start:end]!=e['quote']:raise ValueError('alias Unicode evidence mismatch')
 if p['alias'] not in e['quote'] or p['canonical_name'] not in e['quote']:raise ValueError('alias and canonical name need same reviewed evidence span')
 src=d.execute('select namespace,record_key,source_type,content_hash,private_locator,processing_status from cp_source_record where id=?',(p['source_id'],)).fetchone()
 if not src or src[5]!='parsed' or p['source']!=dict(zip(('namespace','record_key','source_type','content_hash','private_locator'),src[:5])):raise ValueError('alias original source mismatch')
 if not d.execute('select 1 from cp_memo_source where memo_id=? and source_id=?',(p['memo_id'],p['source_id'])).fetchone():raise ValueError('alias source not linked to evidence memo')
 assoc=d.execute('select x.scope_kind,x.review_status,e.body_id,e.quote,e.start_offset,e.end_offset,e.review_status from cp_memo_company x join cp_evidence e on e.id=x.evidence_id and e.memo_id=x.memo_id where x.memo_id=? and x.company_id=?',(p['memo_id'],p['company_id'])).fetchone()
 if not assoc or assoc[0] not in ('primary','substantive') or assoc[1]!='approved' or assoc[2]!=p['body_id'] or assoc[6]!='approved' or not 0<=assoc[4]<assoc[5]<=len(memo[1]) or memo[1][assoc[4]:assoc[5]]!=assoc[3]:raise ValueError('alias proof lacks approved same-body company assignment')
 return sha(canon(p))

def alias_certificate_values(p):
 ph=sha(canon(p));cid=alias_certificate_id(p['company_id'],p['normalized_alias'])
 source=canon({'protocol':ALIAS_PROTOCOL,'certificate_id':cid,'plan_sha256':ph})
 certificate=(cid,None,p['memo_id'],p['company_id'],p['alias'],'approved_existing_alias',p['review']['reason'],canon({'protocol':ALIAS_PROTOCOL,'plan':p,'plan_sha256':ph}),p['review']['reviewer'],p['review']['reviewed_at'])
 alias=(p['company_id'],p['alias'],p['normalized_alias'],p['language'],source)
 return certificate,alias

def verify_alias_certificate(d,cid,normalized):
 row=d.execute('select company_id,alias,normalized_alias,language,source from cp_company_alias where company_id=? and normalized_alias=?',(cid,normalized)).fetchone()
 if not row:raise ValueError('approved alias row missing')
 try:meta=json.loads(row[4])
 except Exception as e:raise ValueError('alias source certificate JSON') from e
 if set(meta)!={'protocol','certificate_id','plan_sha256'} or meta['protocol']!=ALIAS_PROTOCOL or meta['certificate_id']!=alias_certificate_id(cid,normalized):raise ValueError('extra alias lacks approved certificate')
 cert=d.execute('select id,proposal_id,memo_id,canonical_company_id,source_name,decision,reason,source_json,reviewed_by,reviewed_at from cp_entity_resolution where id=?',(meta['certificate_id'],)).fetchone()
 if not cert:raise ValueError('alias certificate row missing')
 proof=json.loads(cert[7])
 if set(proof)!={'protocol','plan','plan_sha256'} or proof['protocol']!=ALIAS_PROTOCOL:raise ValueError('alias certificate protocol')
 p=proof['plan'];ph=validate_alias_plan(d,p)
 if p['company_id']!=cid or p['normalized_alias']!=normalized or ph!=meta['plan_sha256'] or ph!=proof['plan_sha256']:raise ValueError('alias certificate plan binding')
 wanted_cert,wanted_alias=alias_certificate_values(p)
 if tuple(cert)!=wanted_cert or tuple(row)!=wanted_alias:raise ValueError('alias certificate/row exact mismatch')
 return {'certificate_id':cert[0],'plan_sha256':ph,'alias':row[1],'normalized_alias':normalized}

def verify_alias_set(d,cid,expected):
 # Original proposal rows stay exact. Extras require independent, complete proof.
 rows=d.execute('select alias,normalized_alias,source,language from cp_company_alias where company_id=?',(cid,)).fetchall()
 actual={(x[0],x[1],x[2]) for x in rows}
 if not expected<=actual:raise ValueError('original entity aliases missing or changed')
 for x in rows:
  if (x[0],x[1],x[2]) in expected:
   if x[3] is not None:raise ValueError('original alias language changed')
  else:verify_alias_certificate(d,cid,x[1])

def append_reviewed_aliases(d,plans,failpoint=None):
 """Internal transaction helper only. Caller owns fixed path, pins, backup and control.
 Does not commit, enable the writer, alter schema, rewrite entities or archive memos.
 """
 if not d.in_transaction:raise ValueError('alias append requires caller transaction')
 if not isinstance(plans,list) or not 1<=len(plans)<=20:raise ValueError('alias plan list')
 if failpoint not in (None,'after_first_certificate','after_first_pair'):raise ValueError('alias failpoint invalid')
 seen={};prepared=[]
 for p in plans:
  validate_alias_plan(d,p);key=p['normalized_alias']
  if key in seen:raise ValueError('duplicate normalized alias in batch')
  seen[key]=p['company_id'];cert,alias=alias_certificate_values(p)
  old_alias=d.execute('select company_id,alias,normalized_alias,language,source from cp_company_alias where company_id=? and normalized_alias=?',(p['company_id'],key)).fetchone()
  old_cert=d.execute('select id,proposal_id,memo_id,canonical_company_id,source_name,decision,reason,source_json,reviewed_by,reviewed_at from cp_entity_resolution where id=?',(cert[0],)).fetchone()
  if old_alias or old_cert:
   if not old_alias or not old_cert or tuple(old_alias)!=alias or tuple(old_cert)!=cert:raise ValueError('existing alias/certificate differs; never overwrite')
   verify_alias_certificate(d,p['company_id'],key)
  prepared.append((p,cert,alias,bool(old_alias)))
 result=[]
 for index,(p,cert,alias,replayed) in enumerate(prepared):
  if not replayed:
   d.execute('insert into cp_entity_resolution(id,proposal_id,memo_id,canonical_company_id,source_name,decision,reason,source_json,reviewed_by,reviewed_at) values(?,?,?,?,?,?,?,?,?,?)',cert)
   if index==0 and failpoint=='after_first_certificate':raise RuntimeError('synthetic alias failure after certificate')
   d.execute('insert into cp_company_alias(company_id,alias,normalized_alias,language,source) values(?,?,?,?,?)',alias)
   if index==0 and failpoint=='after_first_pair':raise RuntimeError('synthetic alias failure after pair')
  proof=verify_alias_certificate(d,p['company_id'],p['normalized_alias'])
  result.append(dict(proof,replayed=replayed))
 return result
