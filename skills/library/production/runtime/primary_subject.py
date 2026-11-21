"""Explicit one-per-memo primary subject. No first-theme or keyword inference."""
import hashlib,json
DDL="""
CREATE TABLE local_memo_primary_subject(
 memo_id TEXT PRIMARY KEY NOT NULL,
 subject_kind TEXT NOT NULL CHECK(subject_kind IN ('company','theme')),
 company_id TEXT,
 theme_id TEXT,
 body_id TEXT NOT NULL,
 evidence_id TEXT NOT NULL,
 reason TEXT NOT NULL CHECK(length(trim(reason))>0),
 reviewed_by TEXT NOT NULL,
 reviewed_at TEXT NOT NULL,
 FOREIGN KEY(memo_id) REFERENCES cp_memo(id),
 FOREIGN KEY(company_id) REFERENCES cp_company(id),
 FOREIGN KEY(theme_id) REFERENCES cp_theme(id),
 FOREIGN KEY(body_id) REFERENCES cp_body(id),
 FOREIGN KEY(evidence_id,memo_id) REFERENCES cp_evidence(id,memo_id),
 CHECK((subject_kind='company' AND company_id IS NOT NULL AND theme_id IS NULL) OR (subject_kind='theme' AND company_id IS NULL AND theme_id IS NOT NULL))
);
CREATE TRIGGER local_primary_subject_insert BEFORE INSERT ON local_memo_primary_subject BEGIN
 SELECT RAISE(ABORT,'primary subject requires current approved evidence') WHERE NOT EXISTS(SELECT 1 FROM cp_memo m JOIN cp_evidence e ON e.id=NEW.evidence_id AND e.memo_id=m.id AND e.body_id=m.body_id WHERE m.id=NEW.memo_id AND m.body_id=NEW.body_id AND m.lifecycle='active' AND e.review_status='approved');
 SELECT RAISE(ABORT,'primary company assignment mismatch') WHERE NEW.subject_kind='company' AND NOT EXISTS(SELECT 1 FROM cp_memo_company WHERE memo_id=NEW.memo_id AND company_id=NEW.company_id AND scope_kind='primary' AND review_status='approved' AND evidence_id=NEW.evidence_id);
 SELECT RAISE(ABORT,'primary theme assignment mismatch') WHERE NEW.subject_kind='theme' AND NOT EXISTS(SELECT 1 FROM cp_memo_theme WHERE memo_id=NEW.memo_id AND theme_id=NEW.theme_id AND review_status='approved' AND evidence_id=NEW.evidence_id);
 SELECT RAISE(ABORT,'theme primary conflicts with company primary') WHERE NEW.subject_kind='theme' AND EXISTS(SELECT 1 FROM cp_memo_company WHERE memo_id=NEW.memo_id AND scope_kind='primary' AND review_status<>'rejected');
 SELECT RAISE(ABORT,'multiple company primary assignments') WHERE NEW.subject_kind='company' AND (SELECT count(*) FROM cp_memo_company WHERE memo_id=NEW.memo_id AND scope_kind='primary' AND review_status<>'rejected')<>1;
END;
CREATE TRIGGER local_primary_subject_no_update BEFORE UPDATE ON local_memo_primary_subject BEGIN SELECT RAISE(ABORT,'primary subject immutable; reviewed revision required'); END;
CREATE TRIGGER local_primary_subject_no_delete BEFORE DELETE ON local_memo_primary_subject BEGIN SELECT RAISE(ABORT,'primary subject immutable; reviewed revision required'); END;
"""
def install(d):
 # execute each complete SQL statement without executescript implicit transaction commits.
 import sqlite3
 current=''
 for line in DDL.splitlines(True):
  current+=line
  if sqlite3.complete_statement(current):d.execute(current);current=''
 if current.strip():raise ValueError('incomplete primary schema SQL')
def validate(i,protocol):
 p=i.get('primary_subject')
 companies=i['companies'];themes=i['themes']
 if protocol=='cp-local-batch-v1':
  if p is not None:raise ValueError('explicit primary subject requires batch v2')
  if len(companies)<1 or sum(a['scope_kind']=='primary' for a in companies)!=1:raise ValueError('exactly one reviewed primary company required in v1')
  return
 if p is None or set(p)!={'kind','subject_id','reason'} or p['kind'] not in ('company','theme') or not p['reason']:raise ValueError('explicit primary subject kind/id/reason required in v2')
 selected=companies if p['kind']=='company' else themes;field='company_id' if p['kind']=='company' else 'theme_id'
 matches=[a for a in selected if a[field]==p['subject_id']]
 if len(matches)!=1:raise ValueError('explicit primary subject needs exactly one matching assignment')
 primaries=[a for a in companies if a['scope_kind']=='primary']
 if p['kind']=='company' and (len(primaries)!=1 or primaries[0]['company_id']!=p['subject_id']):raise ValueError('explicit primary company conflicts with company assignments')
 if p['kind']=='theme' and primaries:raise ValueError('theme primary cannot retain a company primary')
def expected(i,mid,bid):
 p=i.get('primary_subject')
 if p is None:return None
 kind='companies' if p['kind']=='company' else 'themes';field='company_id' if p['kind']=='company' else 'theme_id';n=next(n for n,x in enumerate(i[kind]) if x[field]==p['subject_id']);eid=mid+'-'+kind+'-'+str(n)
 return (mid,p['kind'],p['subject_id'] if p['kind']=='company' else None,p['subject_id'] if p['kind']=='theme' else None,bid,eid,p['reason'],i['review']['reviewer'],i['review']['reviewed_at'])
def insert(d,i,mid,bid):
 row=expected(i,mid,bid)
 if row:d.execute('insert into local_memo_primary_subject values(?,?,?,?,?,?,?,?,?)',row)
def verify(d,p,mid):
 if p is None:return
 row=d.execute('select * from local_memo_primary_subject where memo_id=?',(mid,)).fetchone()
 if not row:raise ValueError('explicit primary mapping absent; legacy review/migration required')
 m=d.execute('select body_id,lifecycle from cp_memo where id=?',(mid,)).fetchone()
 if not m or m[1]!='active' or row['body_id']!=m[0] or row['subject_kind']!=p['kind'] or (row['company_id'] if p['kind']=='company' else row['theme_id'])!=p['subject_id'] or row['reason']!=p['reason']:raise ValueError('explicit primary mapping differs from reviewed plan')
 e=d.execute('select body_id,review_status from cp_evidence where id=? and memo_id=?',(row['evidence_id'],mid)).fetchone()
 if not e or tuple(e)!=(m[0],'approved'):raise ValueError('explicit primary evidence stale/unapproved')
 if p['kind']=='company':
  matches=d.execute("select company_id,evidence_id from cp_memo_company where memo_id=? and scope_kind='primary' and review_status<>'rejected'",(mid,)).fetchall()
  if len(matches)!=1 or tuple(matches[0])!=(p['subject_id'],row['evidence_id']):raise ValueError('stored company primary assignment differs')
 else:
  if d.execute("select 1 from cp_memo_company where memo_id=? and scope_kind='primary' and review_status<>'rejected'",(mid,)).fetchone():raise ValueError('stored theme primary conflicts with company primary')
  match=d.execute("select evidence_id from cp_memo_theme where memo_id=? and theme_id=? and review_status='approved'",(mid,p['subject_id'])).fetchone()
  if not match or match[0]!=row['evidence_id']:raise ValueError('stored primary theme assignment differs')
