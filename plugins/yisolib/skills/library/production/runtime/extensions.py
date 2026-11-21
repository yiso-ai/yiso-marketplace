"""Strict append-only extensions over existing cp_* schema. No new entity/edge inference."""
import hashlib,json,re

def canon(v):return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def digest(v):return hashlib.sha256(v.encode('utf-8')).hexdigest()
def quote_check(e,body):
 if set(e)!={'quote','start_offset','end_offset','claim_kind'}:raise ValueError('extension evidence keys')
 s,t=e['start_offset'],e['end_offset']
 if type(s)!=int or type(t)!=int or not 0<=s<t<=len(body) or body[s:t]!=e['quote'] or not e['quote'].strip():raise ValueError('extension evidence mismatch')
 if e['claim_kind'] not in ('fact','source_opinion'):raise ValueError('relation/relevance requires fact or attributable source opinion')
def validate(i):
 seen=set()
 for t in i.get('native_tables',[]):
  if set(t)!={'content','limitations','native_origin','source_sha256','private_locator'}:raise ValueError('native table keys')
  if t['native_origin'] not in ('docx_table','xlsx_cells'):raise ValueError('image/OCR table forbidden')
  if t['source_sha256']!=i['source']['content_hash'] or t['source_sha256'] in seen:raise ValueError('one native table object per memo/source hash; merge native cells before submission')
  seen.add(t['source_sha256'])
  if not isinstance(t['limitations'],list) or any(not isinstance(s,str) for s in t['limitations']) or not t['private_locator']:raise ValueError('native table provenance/limitations required')
  c=t['content']
  if set(c)!={'cells','format','formulas_executed','range','sheet','values_are_original_cached_results'} or c['format']!='native-cells-v1' or c['formulas_executed'] is not False or c['values_are_original_cached_results'] is not True:raise ValueError('native cell format or original values invalid')
  if not c['sheet'] or not re.fullmatch(r'[A-Z]+[1-9][0-9]*:[A-Z]+[1-9][0-9]*',c['range']):raise ValueError('native table range/sheet required')
  def xy(addr):
   m=re.fullmatch(r'([A-Z]+)([1-9][0-9]*)',addr)
   if not m:raise ValueError('cell address invalid')
   col=0
   for ch in m[1]:col=col*26+ord(ch)-64
   return col,int(m[2])
  lo,hi=map(xy,c['range'].split(':'));addresses=set()
  if not isinstance(c['cells'],list) or not 1<=len(c['cells'])<=10000:raise ValueError('native cells count')
  for cell in c['cells']:
   if set(cell)!={'address','data_type','formula','raw_value','style','text'}:raise ValueError('native cell fields')
   pos=xy(cell['address'])
   if cell['address'] in addresses or not (lo[0]<=pos[0]<=hi[0] and lo[1]<=pos[1]<=hi[1]):raise ValueError('duplicate/outside cell')
   addresses.add(cell['address'])
   if not isinstance(cell['text'],str) or cell['data_type'] not in ('s','n','b','e','str','inlineStr') or not isinstance(cell['style'],dict):raise ValueError('cell type/style invalid')
   if cell['formula'] is not None and (not isinstance(cell['formula'],dict) or set(cell['formula'])!={'attributes','resolved_shared_formula','text'}):raise ValueError('native formula metadata invalid')
   if t['native_origin']=='docx_table' and cell['formula'] is not None:raise ValueError('DOCX formula interpretation unsupported')
 seen=set()
 for r in i.get('relations',[]):
  if set(r)!={'relation_id','source_company_id','target_company_id','stance','event_time','qualifier','evidence','reason'}:raise ValueError('relation fields')
  if r['relation_id'] in seen:raise ValueError('duplicate relation in item')
  seen.add(r['relation_id']);quote_check(r['evidence'],i['body'])
  if r['stance'] not in ('supports','contradicts','qualifies') or not r['reason'] or not isinstance(r['qualifier'],dict):raise ValueError('relation stance/reason/qualifier')
  assignments={a['company_id'] for a in i['companies'] if a['scope_kind'] in ('primary','substantive')}
  if r['source_company_id']==r['target_company_id'] or not {r['source_company_id'],r['target_company_id']}<=assignments:raise ValueError('both relation endpoints need substantive current-body assignments')
 seen=set()
 for r in i.get('relevance',[]):
  if set(r)!={'company_id','theme_id','evidence','reason'} or not r['reason']:raise ValueError('relevance fields')
  key=(r['company_id'],r['theme_id'])
  if key in seen:raise ValueError('duplicate relevance')
  seen.add(key);quote_check(r['evidence'],i['body'])
  if r['company_id'] not in {a['company_id'] for a in i['companies'] if a['scope_kind'] in ('primary','substantive')} or r['theme_id'] not in {a['theme_id'] for a in i['themes']}:raise ValueError('relevance needs reviewed company and theme assignments')

def add_evidence(d,i,mid,bid,eid,e):
 r=i['review'];d.execute('insert into cp_evidence values(?,?,?,?,?,?,?,?,?,?,?)',(eid,mid,bid,e['quote'],e['start_offset'],e['end_offset'],'unicode:'+str(e['start_offset']),e['claim_kind'],'approved',r['reviewer'],r['reviewed_at']))
def apply(d,i,mid,bid):
 review=i['review']
 for t in i.get('native_tables',[]):
  text=canon(t['content']);tid=mid+'-table-'+t['source_sha256']
  d.execute('insert into cp_native_table values(?,?,?,?,?,?,?,?,?,?,?)',(tid,mid,bid,t['source_sha256'],t['private_locator'],text,digest(text),'approved',canon(t['limitations']),review['reviewer'],review['reviewed_at']))
 for r in i.get('relations',[]):
  row=d.execute('select source_company_id,target_company_id,status from cp_relation where id=?',(r['relation_id'],)).fetchone()
  if not row or tuple(row[:2])!=(r['source_company_id'],r['target_company_id']) or row[2]=='rejected':raise ValueError('relation must exist with exact nonrejected endpoints')
  eid=mid+'-relation-'+digest(r['relation_id']);add_evidence(d,i,mid,bid,eid,r['evidence'])
  qualifier=dict(r['qualifier']);qualifier['review_reason']=r['reason']
  d.execute('insert into cp_relation_evidence values(?,?,?,?,?,?)',(r['relation_id'],eid,r['stance'],r['event_time'],'approved',canon(qualifier)))
 for r in i.get('relevance',[]):
  eid=mid+'-relevance-'+digest(r['company_id']+'\x00'+r['theme_id']);add_evidence(d,i,mid,bid,eid,r['evidence'])
  d.execute('insert into cp_relevance values(?,?,?,?,?,?)',(mid,r['company_id'],r['theme_id'],eid,r['reason'],'approved'))

def duplicate_check(d,i,mid):
 memo=d.execute('select body_id,lifecycle from cp_memo where id=?',(mid,)).fetchone()
 if not memo or memo[1]!='active':raise ValueError('extension memo inactive/missing')
 bid=memo[0]
 # New extension facts on an existing body need a separate plan, not implicit merge.
 for t in i.get('native_tables',[]):
  row=d.execute('select content_json,content_sha256,limitations_json,private_locator,source_body_id,review_status from cp_native_table where memo_id=? and source_sha256=?',(mid,t['source_sha256'])).fetchone()
  text=canon(t['content'])
  if not row or row[1]!=digest(text) or row[0]!=text or row[2]!=canon(t['limitations']):raise ValueError('duplicate native table differs; reconciliation required')
  if tuple(row[3:])!=(t['private_locator'],bid,'approved'):raise ValueError('duplicate native table provenance/body/review mismatch')
  origins=[]
  for source in d.execute('select r.metadata_json from cp_source_record r join cp_memo_source s on s.source_id=r.id where s.memo_id=? and r.content_hash=?',(mid,t['source_sha256'])):
   origins.extend(json.loads(source[0]).get('native_origins',[]))
  if {'source_sha256':t['source_sha256'],'native_origin':t['native_origin']} not in origins:raise ValueError('duplicate native origin provenance mismatch')
 for r in i.get('relations',[]):
  relation=d.execute('select source_company_id,target_company_id,status from cp_relation where id=?',(r['relation_id'],)).fetchone()
  if not relation or tuple(relation[:2])!=(r['source_company_id'],r['target_company_id']) or relation[2]=='rejected':raise ValueError('duplicate relation endpoint/status mismatch')
  eid=mid+'-relation-'+digest(r['relation_id']);q=dict(r['qualifier']);q['review_reason']=r['reason']
  row=d.execute('select stance,event_time,status,qualifier_json from cp_relation_evidence where relation_id=? and evidence_id=?',(r['relation_id'],eid)).fetchone()
  if not row or tuple(row)!=(r['stance'],r['event_time'],'approved',canon(q)):raise ValueError('duplicate relation differs; reconciliation required')
  check_evidence(d,eid,r['evidence'],mid,bid)
 for r in i.get('relevance',[]):
  eid=mid+'-relevance-'+digest(r['company_id']+'\x00'+r['theme_id']);row=d.execute('select reason,review_status from cp_relevance where memo_id=? and company_id=? and theme_id=? and evidence_id=?',(mid,r['company_id'],r['theme_id'],eid)).fetchone()
  if not row or tuple(row)!=(r['reason'],'approved'):raise ValueError('duplicate relevance differs; reconciliation required')
  check_evidence(d,eid,r['evidence'],mid,bid)
def check_evidence(d,eid,e,mid,bid):
 row=d.execute('select quote,start_offset,end_offset,claim_kind,review_status,memo_id,body_id from cp_evidence where id=?',(eid,)).fetchone()
 if not row or tuple(row)!=(e['quote'],e['start_offset'],e['end_offset'],e['claim_kind'],'approved',mid,bid):raise ValueError('extension evidence receipt mismatch')
