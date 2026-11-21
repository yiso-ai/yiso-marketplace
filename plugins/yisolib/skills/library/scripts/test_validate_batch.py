import copy,unittest
from validate_batch import validate
class Checks(unittest.TestCase):
 def setUp(self):
  self.b={'mode':'draft','companies':['c'],'topics':['t'],'materials':[{'id':'m','source_key':'s','company_id':'c','kind':'internal','date':'2026-11-09','institution':'示例机构','title':'需求变化','body':'原文正文。','original_excerpt':'原文正文。','heading':'2026-11-09　需求变化 | 示例机构'}],'company_topic_edges':[{'company':'c','topic':'t','evidence_id':'m','reason':'实质业务','basis':'fact'}],'topic_company_edges':[{'company':'c','topic':'t'}]}
 def test_good(self): self.assertEqual(validate(self.b),[])
 def test_internal_changed(self):
  self.b['materials'][0]['body']='改写';self.assertTrue(validate(self.b))
 def test_analyst(self):
  self.b['materials'][0]['display_analysts']=['某分析师'];self.assertTrue(validate(self.b))
 def test_reverse(self):
  self.b['topic_company_edges']=[];self.assertTrue(validate(self.b))
 def test_source(self):
  self.b['materials'][0]['body']='https://example.com';self.assertTrue(validate(self.b))
 def test_publish(self):
  self.b['mode']='publish';self.assertTrue(validate(self.b))
 def test_unverified(self):
  self.b['company_topic_edges'][0]['basis']='unverified';self.assertTrue(validate(self.b))
 def test_company_endpoint(self):
  self.b['materials'][0]['company_id']='missing';self.assertTrue(validate(self.b))
 def test_duplicate(self):
  self.b['materials'].append(copy.deepcopy(self.b['materials'][0]));self.assertTrue(validate(self.b))
if __name__=='__main__':unittest.main()
