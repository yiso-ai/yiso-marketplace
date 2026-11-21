"""Read-only narrow literal new-entity feature gate; never creates entities itself."""
import json
import archiver as a,new_entities as ne
FEATURE='literal_source_named_entity_no_alias_v1'
COVERAGE=['active','retired','merged','aliases','identifiers','semantic_near_matches']
def need(v,m):
 if not v:raise ValueError('literal entity qualification: '+m)
def registry(d,exclude=()):
 exclude=set(exclude);result={}
 for table,key in [('cp_company','id'),('cp_company_alias','company_id'),('cp_company_identifier','company_id'),('cp_entity_resolution','canonical_company_id')]:
  rows=[dict(r) for r in d.execute('select * from '+table) if r[key] not in exclude];result[table]=sorted(rows,key=a.canonical)
 return a.sha(a.canonical(result))
def proposals(batch):
 result=[];all_new={p['company_id'] for i in batch['items'] for p in i.get('new_companies',[])};declared=set()
 for index,item in enumerate(batch['items']):
  plans=item.get('new_companies',[]);need(len(plans)<=1,'one literal declaration per item')
  for p in plans:
   ne.validate(item);need(p['aliases']==[],'aliases must be empty');need(p['name']==p['name'].strip(),'name must be verbatim clean literal')
   cid=p['company_id'];need(cid not in declared,'duplicate declaration; later items reuse ID without new plan')
   need(item.get('primary_subject')=={'kind':'company','subject_id':cid,'reason':item.get('primary_subject',{}).get('reason')},'declaration requires company primary')
   assignments=[x for x in item['companies'] if x['company_id']==cid and x['scope_kind']=='primary'];need(len(assignments)==1,'declaration primary evidence missing')
   evidence=assignments[0]['evidence'];need(p['name'] in evidence['quote'] and item['body'][evidence['start_offset']:evidence['end_offset']]==evidence['quote'],'primary quote must directly include literal name')
   e=p['evidence'];need(max(e['start_offset'],evidence['start_offset'])<min(e['end_offset'],evidence['end_offset']),'declaration and primary proof must overlap')
   declared.add(cid);result.append({'item_index':index,'company_id':cid,'identity_key':p['identity_key'],'name':p['name'],'proposal_sha256':a.sha(a.canonical(p))})
  for assignment in item['companies']:
   need(assignment['company_id'] not in all_new or assignment['company_id'] in declared,'reference before declaration')
 ne.validate_batch(batch)
 return result

def validate(d,batch,review,before=True):
 plans=proposals(batch);need(plans,'feature needs at least one declaration')
 need(set(review)=={'protocol','registry_sha256','coverage','plans','identity_reviews'},'review fields')
 need(review['protocol']=='independent-literal-entity-review-v1' and review['coverage']==COVERAGE and review['plans']==plans,'sealed registry review must bind full ordered proposals')
 ids={p['company_id'] for p in plans}
 need(registry(d,exclude=ids)==review['registry_sha256'],'whole registry changed outside declared entities')
 need(len(review['identity_reviews'])==len(plans),'identity review cardinality')
 for plan,r in zip(plans,review['identity_reviews']):
  need(set(r)=={'company_id','decision','near_identity_candidates','reason'} and r['company_id']==plan['company_id'],'identity review shape')
  need(r['decision']=='create_source_literal_unverified' and r['near_identity_candidates']==[] and isinstance(r['reason'],str) and r['reason'].strip(),'same/possible existing identity must reuse or hold')
  name=plan['name'];cid=plan['company_id'];norm=ne.norm(name)
  conflicts={x['id'] for x in d.execute('select id,name from cp_company') if ne.norm(x['name'])==norm and x['id']!=cid}
  conflicts.update(x['company_id'] for x in d.execute('select company_id,alias from cp_company_alias') if ne.norm(x['alias'])==norm and x['company_id']!=cid)
  need(not conflicts,'name collides with registry including inactive/merged/alias')
  existing=d.execute('select name,entity_type,status from cp_company where id=?',(cid,)).fetchone()
  identity=d.execute('select * from cp_company_identifier where scheme=? and value=?',(ne.SCHEME,plan['identity_key'])).fetchall()
  if before:
   need(existing is None and not identity,'existing identity must be reused, not redeclared')
   for table,key in [('cp_company_alias','company_id'),('cp_company_identifier','company_id'),('cp_entity_resolution','canonical_company_id')]:
    need(not d.execute('select 1 from '+table+' where '+key+'=?',(cid,)).fetchone(),'preexisting/orphan row for proposed CID in '+table)
  else:
   need(existing is not None and tuple(existing)==(name,'unverified_issuer_or_entity','active'),'post entity metadata')
   p=batch['items'][plan['item_index']]['new_companies'][0];source=ne.canon({'scheme':ne.SCHEME,'identity_key':p['identity_key'],'name':name,'aliases':[],'security_identity_not_asserted':True})
   all_identifiers=[tuple(x) for x in d.execute('select company_id,scheme,value,exchange,verification_status,source from cp_company_identifier where company_id=?',(cid,))]
   need(all_identifiers==[(cid,ne.SCHEME,plan['identity_key'],'','unverified',source)],'post complete identifier row set must be exactly the single approved research key')
   aliases=[tuple(x) for x in d.execute('select alias,normalized_alias,language,source from cp_company_alias where company_id=?',(cid,))]
   need(aliases==[(name,ne.norm(name),None,source)],'only name-self lookup allowed, no synonyms')

   item=batch['items'][plan['item_index']];mid='local-memo-'+a.sha(item['body']);src=item['source']
   rid='local-resolution-'+ne.sha(mid+'\x00'+cid+'\x00'+src['namespace']+'\x00'+src['record_key'])
   provenance=ne.canon({'proposal':ne.stable_plan(p),'source':src,'body_sha256':ne.sha(item['body']),'original_reviewed_at':p['review']['reviewed_at']})
   expected_resolution=(rid,None,mid,cid,name,'approved_named_reference',p['review']['reason'],provenance,p['review']['reviewer'],p['review']['reviewed_at'])
   resolutions=[tuple(x) for x in d.execute('select id,proposal_id,memo_id,canonical_company_id,source_name,decision,reason,source_json,reviewed_by,reviewed_at from cp_entity_resolution where canonical_company_id=?',(cid,))]
   need(resolutions==[expected_resolution],'post complete resolution set differs from pinned deterministic expected row')

 return plans
