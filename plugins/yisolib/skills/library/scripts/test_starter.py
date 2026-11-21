"""Offline synthetic contract tests; no production access."""
import copy, importlib.util, json, socket, subprocess, sys, tempfile, time, unittest, urllib.error, urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]/'assets'/'starter'
spec=importlib.util.spec_from_file_location('starter',ROOT/'starter.py');s=importlib.util.module_from_spec(spec);spec.loader.exec_module(s)
class StarterTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.db=str(Path(self.temp.name)/'demo.sqlite');s.initialize(self.db);self.payload=json.loads((ROOT/'fixture.json').read_text())
 def tearDown(self): self.temp.cleanup()
 def test_import_replay_hash_dedup_public(self):
  self.assertEqual(s.import_batch(self.db,self.payload),{'version':1,'replayed':False});self.assertTrue(s.import_batch(self.db,self.payload)['replayed'])
  db=s.connect(self.db);self.assertEqual(db.execute('SELECT count(*) FROM bodies').fetchone()[0],1);self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok');self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[]);db.close()
  data=s.public_data(self.db);self.assertEqual(len(data['memos']),5);text=json.dumps(data);self.assertNotIn('source_key',text);self.assertNotIn('author_private',text);self.assertNotIn('合成作者仅后台',text);self.assertTrue(all(m['display_author']=='示例研究员' for m in data['memos'] if m['category']=='internal'));self.assertTrue(all(m['display_author'] is None for m in data['memos'] if m['category']!='internal'));self.assertTrue(all(m['institution']=='示例虚构研究机构' for m in data['memos'] if m['category']!='internal'));self.assertNotIn('sha256',text);self.assertEqual(data['memos'][-1]['tables'][0]['rows'][0],['样例数量','10'])
 def test_invalid_batches_atomic(self):
  mutations=[lambda p:p['memos'][1].update(sha256='0'*64),lambda p:p['memos'][1]['assignments'][0].update(quote='wrong'),lambda p:p['memos'][1]['assignments'].append(copy.deepcopy(p['memos'][1]['assignments'][0])),lambda p:p['memos'][1].update(primary_subject='unknown'),lambda p:p['memos'][1].update(source_key=p['memos'][0]['source_key']),lambda p:p['memos'][1].update(material_date='2026-02-30'),lambda p:p['memos'][1].update(private_locator='secret'),lambda p:p['memos'][1].update(title='<script>bad</script>'),lambda p:p['memos'][1]['tables'].append({'headers':['a'],'rows':[['x','y']]})]
  for mutate in mutations:
   p=copy.deepcopy(self.payload);mutate(p)
   with self.assertRaises(Exception):s.import_batch(self.db,p)
   self.assertEqual(s.public_data(self.db)['version'],0);self.assertEqual(s.public_data(self.db)['memos'],[])
 def test_optimistic_conflict_and_changed_replay(self):
  s.import_batch(self.db,self.payload);p=copy.deepcopy(self.payload);p['batch_id']='next'
  with self.assertRaisesRegex(ValueError,'version conflict'):s.import_batch(self.db,p)
  p=copy.deepcopy(self.payload);p['memos'][0]['title']='changed'
  with self.assertRaisesRegex(ValueError,'replay differs'):s.import_batch(self.db,p)
  self.assertEqual(s.public_data(self.db)['version'],1)
 def test_taxonomy_and_scores(self):
  s.import_batch(self.db,self.payload);data=s.public_data(self.db)
  self.assertEqual(len(data['industries']),3);self.assertEqual(len(data['theme_parents']),2)
  score=next(x for x in data['company_themes'] if x['company_id']=='demo-company-a' and x['theme_id']=='demo-theme')
  self.assertEqual((score['n_related'],score['n_all'],score['ratio']),(4,5,.8))
 def test_industry_mapping_at_reviewed_level(self):
  for mapping, level in zip(self.payload['company_industries'], [1, 2], strict=True):
   mapping['industry_id'] = f'demo-industry-{level}'
  s.import_batch(self.db,self.payload)
  saved = s.public_data(self.db)['company_industries']
  self.assertEqual({row['industry_id'] for row in saved}, {'demo-industry-1', 'demo-industry-2'})
 def test_taxonomy_rejects_invalid_nodes_and_cycles(self):
  for change in [lambda p:p['theme_parents'].append({'child_id':'demo-domain','parent_id':'demo-secondary-theme'}),lambda p:p['company_industries'][0].update(company_id='demo-theme'),lambda p:p['industries'][1].update(level=3),lambda p:p['theme_aliases'][0].update(theme_id='demo-company-a')]:
   p=copy.deepcopy(self.payload);change(p)
   with self.assertRaises(Exception):s.import_batch(self.db,p)
   self.assertEqual(s.public_data(self.db)['version'],0)
 def test_unicode_offsets_and_original_title(self):
  p=copy.deepcopy(self.payload);m=p['memos'][0];m['title']='原始标题  保持空格 | Example';m['body']='🧪测试文字';m['sha256']=s.digest(m['body'])
  for a in m['assignments']:a.update(start=0,end=2,quote='🧪测')
  s.import_batch(self.db,p);memo=next(m for m in s.public_data(self.db)['memos'] if m['id']==p['memos'][0]['id'])
  self.assertEqual(memo['title'],'原始标题  保持空格 | Example');self.assertEqual(memo['assignments'][0]['quote'],'🧪测')
 def test_strict_json(self):
  for text in ['{"a":1,"a":2}','{"x":NaN}','{"x":Infinity}']:
   with self.assertRaises(ValueError):s.load_json(text)
 def test_http_get_only(self):
  s.import_batch(self.db,self.payload)
  with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
  proc=subprocess.Popen([sys.executable,str(ROOT/'starter.py'),'serve','--db',self.db,'--port',str(port)],stdout=subprocess.DEVNULL)
  base=f'http://127.0.0.1:{port}'
  try:
   for _ in range(50):
    try:
     with urllib.request.urlopen(base+'/api/data') as r:self.assertEqual(json.load(r)['version'],1)
     break
    except OSError:time.sleep(.05)
   else:self.fail('server startup failed')
   collected=[];offset=0
   while True:
    with urllib.request.urlopen(base+'/api/data?limit=2&offset='+str(offset)) as r:page=json.load(r)
    collected.extend(m['id'] for m in page['memos'])
    if page['nextOffset'] is None:break
    self.assertGreater(page['nextOffset'],offset);offset=page['nextOffset']
   self.assertEqual(len(collected),5);self.assertEqual(len(set(collected)),5)
   for target in ['/','/app.js','/style.css']:
    with urllib.request.urlopen(base+target) as r:self.assertEqual(r.status,200);self.assertIn("frame-ancestors 'none'",r.headers['Content-Security-Policy'])
   for request,status in [(urllib.request.Request(base+'/api/data',method='POST'),501),(urllib.request.Request(base+'/schema.sql'),404),(urllib.request.Request(base+'/',headers={'Host':'attacker.invalid'}),403)]:
    with self.assertRaises(urllib.error.HTTPError) as c:urllib.request.urlopen(request)
    self.assertEqual(c.exception.code,status)
  finally:proc.terminate();proc.wait(timeout=5)
if __name__=='__main__':unittest.main(verbosity=2)
