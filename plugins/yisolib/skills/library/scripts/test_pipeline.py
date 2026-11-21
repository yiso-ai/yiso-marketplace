import copy, json, os, sqlite3, tempfile, unittest
from pathlib import Path
import pipeline as p
import pipeline_directory as directory
FIX=p.ROOT/'assets/source-fixtures'
CFG={'scope_id':'demo-files','account_namespace':'synthetic-account','source_namespace':'local-text','authorized_start_utc':'2026-01-01T00:00:00Z','authorized_end_utc':'2027-01-01T00:00:00Z','authorization_reference':'Synthetic test authorization only'}
def item(key,body='合成原文研究。\n\n保留原始分析。',available=True):
 return {'source_key':key,'source_time_utc':'2026-02-01T00:00:00Z','status':'captured' if available else 'unavailable','reason':None if available else 'synthetic retrieval failure','metadata_origin':'source','title':'原始标题 '+key,'category':'internal','material_date':'2026-02-01','institution':'示例机构','display_author':'示例作者','content':body if available else None,'content_sha256':p.digest(body) if available else None}
def capture(items,id='capture-1',end='2026-03-01T00:00:00Z'):
 pages=[]
 for i,x in enumerate(items):pages.append({'index':i,'request_cursor':None if i==0 else 'cursor-'+str(i),'next_cursor':'cursor-'+str(i+1) if i+1<len(items) else None,'snapshot_id':id+'-group','total_items':len(items),'items':[x]})
 if not pages:pages=[{'index':0,'request_cursor':None,'next_cursor':None,'snapshot_id':id+'-group','total_items':0,'items':[]}]
 return {'format':'cp-source-capture-v1','capture_id':id,'scope_id':CFG['scope_id'],'account_namespace':CFG['account_namespace'],'source_namespace':CFG['source_namespace'],'window_start_utc':CFG['authorized_start_utc'],'window_end_utc':end,'snapshot_id':id+'-group','pages':pages}
def reviewed(ledger,rid,id='review-1',version=0):
 r=p.prepare_review(ledger,rid,id,version);r.update(decision='approve',reviewer='Synthetic reviewer',reviewed_at_utc='2026-03-02T00:00:00Z',reason='Synthetic explicit complete text review',full_text_read=True,semantic_review='approved');r['batch']['subjects']=[{'id':'demo-subject','name':'合成公司','kind':'company'}];m=r['batch']['memos'][0];m['primary_subject']='demo-subject';m['assignments']=[{'subject_id':'demo-subject','scope':'primary','start':0,'end':4,'quote':m['body'][:4]}];return r
class PipelineTests(unittest.TestCase):
 def setUp(self):
  self.t=tempfile.TemporaryDirectory();self.root=Path(self.t.name);self.ledger=str(self.root/'ledger.sqlite');self.target=str(self.root/'archive.sqlite');p.initialize(self.ledger,CFG);p.starter.initialize(self.target)
 def tearDown(self):self.t.cleanup()
 def rid(self,key,body=None):return next(r['id'] for r in p.status(self.ledger,CFG['scope_id'])['revisions'] if r['source_key']==key and (body is None or r['content_sha256']==p.digest(body)))
 def ingest(self,key='a.txt',id='review-1',version=0):
  r=reviewed(self.ledger,self.rid(key),id,version);p.review(self.ledger,r);p.apply(self.ledger,self.target,id);return r
 def test_gap_survives_round_and_completion_waits(self):
  first=capture([item('a.txt'),item('b.txt',available=False)]);p.capture(self.ledger,first);self.ingest();s=p.status(self.ledger,CFG['scope_id']);self.assertEqual(s['scan_cursor'],first['window_end_utc']);self.assertEqual(s['completion_cursor'],CFG['authorized_start_utc']);self.assertEqual(s['gaps'][0]['source_key'],'b.txt')
  p.capture(self.ledger,capture([item('a.txt')],'capture-2','2026-04-01T00:00:00Z'));self.assertEqual(len(p.status(self.ledger,CFG['scope_id'])['gaps']),1)
  p.capture(self.ledger,capture([item('b.txt')],'capture-3','2026-05-01T00:00:00Z'));self.ingest('b.txt','review-b',1);s=p.status(self.ledger,CFG['scope_id']);self.assertEqual(s['gaps'],[]);self.assertEqual(s['completion_cursor'],s['scan_cursor'])
 def test_incomplete_and_scope_mismatch_do_not_advance(self):
  for mutate in [lambda x:x['pages'].pop(),lambda x:x.update(account_namespace='other'),lambda x:x['pages'][1].update(request_cursor='wrong'),lambda x:x['pages'][1].update(total_items=3),lambda x:x['pages'][0]['items'][0].update(content_sha256='0'*64)]:
   c=capture([item('a.txt'),item('b.txt')]);mutate(c)
   with self.assertRaises(Exception):p.capture(self.ledger,c)
   self.assertEqual(p.status(self.ledger,CFG['scope_id'])['scan_cursor'],CFG['authorized_start_utc'])
 def test_cross_source_duplicate_exclusion_requires_existing_archive(self):
  p.capture(self.ledger,capture([item('a.txt'),item('b.txt')]));first=self.ingest();duplicate=reviewed(self.ledger,self.rid('b.txt'),'dedup',1);duplicate.update(decision='exclude_duplicate',body_mode=None,source_span=None,batch=None,duplicate_of_revision_id=first['revision_id']);p.review(self.ledger,duplicate)
  self.assertEqual(len(p.starter.public_data(self.target)['memos']),1);self.assertEqual(p.status(self.ledger,CFG['scope_id'])['gaps'],[])
  db=p.db_open(self.ledger);row=db.execute('SELECT * FROM coverage_ledger').fetchone();self.assertEqual(row['covering_revision'],first['revision_id']);self.assertEqual(row['covered_revision'],duplicate['revision_id']);db.close()
 def test_scope_target_fixed_and_replaced_database_detected(self):
  p.capture(self.ledger,capture([item('a.txt'),item('b.txt')]));self.ingest();r=reviewed(self.ledger,self.rid('b.txt'),'review-b',1);p.review(self.ledger,r);other=str(self.root/'other.sqlite');p.starter.initialize(other)
  with self.assertRaisesRegex(ValueError,'scope target database changed'):p.apply(self.ledger,other,'review-b')
  os.replace(other,self.target)
  with self.assertRaisesRegex(ValueError,'receipt readback mismatch'):p.apply(self.ledger,self.target,'review-b')
  self.assertEqual(p.starter.public_data(self.target)['memos'],[])
 def test_recovered_same_hash_replay_closes_gap(self):
  p.capture(self.ledger,capture([item('a.txt')]));r=self.ingest();p.capture(self.ledger,capture([item('a.txt',available=False)],'lost','2026-04-01T00:00:00Z'));self.assertTrue(p.status(self.ledger,CFG['scope_id'])['gaps'])
  p.capture(self.ledger,capture([item('a.txt')],'recovered','2026-05-01T00:00:00Z'));self.assertTrue(p.status(self.ledger,CFG['scope_id'])['gaps']);self.assertTrue(p.apply(self.ledger,self.target,r['review_id'])['replayed']);state=p.status(self.ledger,CFG['scope_id']);self.assertEqual(state['gaps'],[]);self.assertEqual(state['completion_cursor'],state['scan_cursor']);self.assertEqual(p.starter.public_data(self.target)['version'],1)
 def test_sellside_reviewed_title_preserves_source_title(self):
  source=item('research.txt');source['category']='sellside';p.capture(self.ledger,capture([source]));r=reviewed(self.ledger,self.rid('research.txt'));r['title_mode']='reviewed_company_summary';r['batch']['memos'][0]['title']='合成公司的新增研究结论';p.review(self.ledger,r);p.apply(self.ledger,self.target,r['review_id']);self.assertEqual(p.starter.public_data(self.target)['memos'][0]['title'],'合成公司的新增研究结论')
  db=p.db_open(self.ledger);self.assertEqual(json.loads(db.execute('SELECT item_json FROM revisions').fetchone()[0])['title'],source['title']);db.close()
 def test_file_metadata_must_be_explicitly_verified(self):
  source=item('filename.txt');source.update(metadata_origin='file_label',material_date=None,institution=None,display_author=None);p.capture(self.ledger,capture([source]));r=reviewed(self.ledger,self.rid('filename.txt'))
  with self.assertRaisesRegex(ValueError,'file label explicitly verified'):p.review(self.ledger,r)
  r['title_mode']='verified_source_title';r['batch']['memos'][0]['title']='经阅读全文核实的原标题';r['metadata_overrides']={'title':{'value':'经阅读全文核实的原标题','reason':'Synthetic reviewer inspected actual document heading'}};p.review(self.ledger,r);p.apply(self.ledger,self.target,r['review_id']);self.assertIsNone(p.starter.public_data(self.target)['memos'][0]['material_date'])
 def test_historical_reconcile_after_new_capture_never_closes_new_gap(self):
  p.capture(self.ledger,capture([item('a.txt')]));r=reviewed(self.ledger,self.rid('a.txt'));p.review(self.ledger,r)
  with self.assertRaises(RuntimeError):p.apply(self.ledger,self.target,r['review_id'],True)
  exclude=copy.deepcopy(r);exclude.update(review_id='exclude-after-crash',decision='exclude',batch=None,body_mode=None)
  with self.assertRaisesRegex(ValueError,'requires reconcile'):p.review(self.ledger,exclude)
  p.capture(self.ledger,capture([item('a.txt','后续改变后的原文。')],'changed'))
  with self.assertRaisesRegex(ValueError,'source changed'):p.apply(self.ledger,self.target,r['review_id'])
  result=p.reconcile(self.ledger,self.target,r['review_id']);self.assertFalse(result['archive_written']);self.assertTrue(p.status(self.ledger,CFG['scope_id'])['gaps']);self.assertEqual(p.starter.public_data(self.target)['version'],1)
 def test_directory_root_namespace_rejects_other_directory(self):
  left=self.root/'left';right=self.root/'right';left.mkdir();right.mkdir();(left/'same.txt').write_text('左侧原文');(right/'same.txt').write_text('右侧不同原文');cfg={**CFG,'source_namespace':directory.directory_namespace(left)}
  with self.assertRaisesRegex(ValueError,'root namespace mismatch'):directory.scan(right,cfg,'wrong',CFG['authorized_start_utc'],CFG['authorized_end_utc'],'internal',None,None)
 def test_pending_review_cannot_ingest(self):
  p.capture(self.ledger,capture([item('a.txt')]));r=p.prepare_review(self.ledger,self.rid('a.txt'),'pending',0)
  with self.assertRaises(ValueError):p.review(self.ledger,r)
  with self.assertRaises(ValueError):p.apply(self.ledger,self.target,'pending')
 def test_crash_recovery_same_batch_and_hash_readback(self):
  p.capture(self.ledger,capture([item('a.txt')]));r=reviewed(self.ledger,self.rid('a.txt'));p.review(self.ledger,r)
  with self.assertRaisesRegex(RuntimeError,'synthetic crash'):p.apply(self.ledger,self.target,r['review_id'],True)
  self.assertEqual(p.starter.public_data(self.target)['version'],1);self.assertEqual(len(p.status(self.ledger,CFG['scope_id'])['gaps']),1)
  self.assertTrue(p.apply(self.ledger,self.target,r['review_id'])['replayed']);self.assertEqual(p.status(self.ledger,CFG['scope_id'])['gaps'],[]);self.assertEqual(len(p.starter.public_data(self.target)['memos']),1)
  other=str(self.root/'other.sqlite');p.starter.initialize(other)
  with self.assertRaisesRegex(ValueError,'target database changed'):p.apply(self.ledger,other,r['review_id'])
  db=sqlite3.connect(self.target);db.execute('UPDATE memos SET title=?',('corrupted',));db.commit();db.close()
  with self.assertRaisesRegex(ValueError,'readback mismatch'):p.apply(self.ledger,self.target,r['review_id'])
 def test_exclusion_supersedes_old_intent(self):
  p.capture(self.ledger,capture([item('a.txt')]));r=reviewed(self.ledger,self.rid('a.txt'));p.review(self.ledger,r);exclude=copy.deepcopy(r);exclude.update(review_id='exclude',decision='exclude',batch=None,body_mode=None,source_span=None);p.review(self.ledger,exclude)
  with self.assertRaisesRegex(ValueError,'superseded'):p.apply(self.ledger,self.target,r['review_id'])
  self.assertEqual(p.starter.public_data(self.target)['memos'],[])
 def test_changed_or_unavailable_source_blocks_old_intent(self):
  for replacement in [item('a.txt','变更后的合成原文。'),item('a.txt',available=False)]:
   with self.subTest(replacement=replacement['status']):
    # Independent ledger per subcase.
    ledger=str(self.root/(replacement['status']+'.sqlite'));p.initialize(ledger,CFG);p.capture(ledger,capture([item('a.txt')]));rid=p.status(ledger,CFG['scope_id'])['revisions'][0]['id'];r=reviewed(ledger,rid);p.review(ledger,r);p.capture(ledger,capture([replacement],'capture-new'))
    with self.assertRaisesRegex(ValueError,'source changed or unavailable'):p.apply(ledger,self.target,r['review_id'])
    self.assertTrue(p.status(ledger,CFG['scope_id'])['gaps'])
 def test_revision_and_coverage_ledgers_do_not_hide(self):
  p.capture(self.ledger,capture([item('a.txt')]));r1=self.ingest();old=r1['revision_id'];body='更新后的原始研究。\n\n包含实质新信息。';p.capture(self.ledger,capture([item('a.txt',body)],'changed'));rid=self.rid('a.txt',body);r2=reviewed(self.ledger,rid,'review-new',1)
  with self.assertRaisesRegex(ValueError,'explicit revision'):p.review(self.ledger,r2)
  r2.update(decision='approve_revision',previous_revision_id=old,coverage=[{'covered_revision_id':old,'kind':'partially_covered','reason':'Contains new research; do not hide earlier memo'}]);p.review(self.ledger,r2);p.apply(self.ledger,self.target,r2['review_id']);self.assertEqual(len(p.starter.public_data(self.target)['memos']),2)
  db=p.db_open(self.ledger);self.assertEqual(db.execute('SELECT count(*) FROM revision_ledger').fetchone()[0],1);self.assertEqual(db.execute('SELECT count(*) FROM coverage_ledger').fetchone()[0],1)
  with self.assertRaises(sqlite3.IntegrityError):db.execute("UPDATE reviews SET payload_json='{}'")
  db.close()
 def test_internal_verbatim_excerpt_and_hash_bound(self):
  p.capture(self.ledger,capture([item('a.txt')]));r=reviewed(self.ledger,self.rid('a.txt'));r['body_mode']='reviewed_safe_summary'
  with self.assertRaisesRegex(ValueError,'remain verbatim'):p.review(self.ledger,r)
  r['body_mode']='verbatim_excerpt';r['source_span']={'start':0,'end':8};m=r['batch']['memos'][0];m['body']=m['body'][:8];m['sha256']=p.digest(m['body']);p.review(self.ledger,r);p.apply(self.ledger,self.target,r['review_id']);self.assertEqual(p.starter.public_data(self.target)['memos'][0]['body'],item('a.txt')['content'][:8])
 def test_directory_scan_stable_multpage_symlink_binary(self):
  root=self.root/'source';root.mkdir()
  for i in range(3):f=root/(str(i)+'.txt');f.write_text('合成文件研究 '+str(i));os.utime(f,(1769904000,1769904000))
  directory_cfg={**CFG,'scope_id':'directory-scope','source_namespace':directory.directory_namespace(root)};p.initialize(self.ledger,directory_cfg)
  c=directory.scan(root,directory_cfg,'directory',CFG['authorized_start_utc'],'2026-03-01T00:00:00Z','internal',None,'Synthetic author',1);self.assertEqual(len(c['pages']),3);self.assertTrue(all(i['material_date'] is None and i['metadata_origin']=='file_label' for page in c['pages'] for i in page['items']));p.capture(self.ledger,c)
  (root/'escape.txt').symlink_to(self.root/'archive.sqlite')
  with self.assertRaisesRegex(ValueError,'symlink'):directory.scan(root,directory_cfg,'bad',CFG['authorized_start_utc'],'2026-03-01T00:00:00Z','internal',None,None)
  (root/'escape.txt').unlink();(root/'binary.txt').write_bytes(b'\x00\x01')
  with self.assertRaises(Exception):directory.scan(root,directory_cfg,'bad',CFG['authorized_start_utc'],'2027-01-01T00:00:00Z','internal',None,None)
if __name__=='__main__':unittest.main(verbosity=2)
