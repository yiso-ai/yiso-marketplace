"""Offline connector-neutral capture/review/archive protocol. No mailbox/network access."""
import argparse, datetime, hashlib, importlib.util, json, sqlite3
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('profile_starter',ROOT/'assets/starter/starter.py')
starter=importlib.util.module_from_spec(spec);spec.loader.exec_module(starter)
canonical=starter.canonical;digest=starter.digest;require=starter.require;exact=starter.exact
SCHEMA='''
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS scope(id TEXT PRIMARY KEY,config_json TEXT NOT NULL,config_sha256 TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS captures(id TEXT PRIMARY KEY,scope_id TEXT NOT NULL REFERENCES scope(id),capture_sha256 TEXT NOT NULL,window_start TEXT NOT NULL,window_end TEXT NOT NULL,payload_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS revisions(id TEXT PRIMARY KEY,scope_id TEXT NOT NULL REFERENCES scope(id),source_key TEXT NOT NULL,content_sha256 TEXT NOT NULL,item_json TEXT NOT NULL,first_capture TEXT NOT NULL REFERENCES captures(id),UNIQUE(scope_id,source_key,content_sha256));
CREATE TABLE IF NOT EXISTS observations(capture_id TEXT NOT NULL REFERENCES captures(id),source_key TEXT NOT NULL,revision_id TEXT REFERENCES revisions(id),status TEXT NOT NULL,PRIMARY KEY(capture_id,source_key));
CREATE TABLE IF NOT EXISTS gap_events(seq INTEGER PRIMARY KEY AUTOINCREMENT,scope_id TEXT NOT NULL REFERENCES scope(id),source_key TEXT NOT NULL,action TEXT NOT NULL CHECK(action IN ('open','resolve')),reason TEXT NOT NULL,reference TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS reviews(id TEXT PRIMARY KEY,revision_id TEXT NOT NULL REFERENCES revisions(id),review_sha256 TEXT NOT NULL,payload_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS apply_intents(review_id TEXT PRIMARY KEY REFERENCES reviews(id),batch_json TEXT NOT NULL,batch_sha256 TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS scope_targets(scope_id TEXT PRIMARY KEY REFERENCES scope(id),target_path TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS target_bindings(review_id TEXT PRIMARY KEY REFERENCES reviews(id),target_path TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS receipts(review_id TEXT PRIMARY KEY REFERENCES reviews(id),revision_id TEXT NOT NULL UNIQUE REFERENCES revisions(id),memo_id TEXT NOT NULL,dataset_version INTEGER NOT NULL,receipt_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS coverage_ledger(id TEXT PRIMARY KEY,review_id TEXT NOT NULL REFERENCES reviews(id),covering_revision TEXT NOT NULL REFERENCES revisions(id),covered_revision TEXT NOT NULL REFERENCES revisions(id),kind TEXT NOT NULL CHECK(kind IN ('exact_duplicate','fully_covered','partially_covered')),reason TEXT NOT NULL,CHECK(covering_revision<>covered_revision));
CREATE TABLE IF NOT EXISTS revision_ledger(id TEXT PRIMARY KEY,review_id TEXT NOT NULL REFERENCES reviews(id),new_revision TEXT NOT NULL REFERENCES revisions(id),previous_revision TEXT NOT NULL REFERENCES revisions(id),reason TEXT NOT NULL,CHECK(new_revision<>previous_revision));
CREATE TABLE IF NOT EXISTS cursor_events(seq INTEGER PRIMARY KEY AUTOINCREMENT,scope_id TEXT NOT NULL REFERENCES scope(id),kind TEXT NOT NULL CHECK(kind IN ('scan','completion')),through_utc TEXT NOT NULL,reference TEXT NOT NULL);
'''
TABLES=['scope','captures','revisions','observations','gap_events','reviews','apply_intents','scope_targets','target_bindings','receipts','coverage_ledger','revision_ledger','cursor_events']

def utc(value):
    require(isinstance(value,str) and value.endswith('Z'),'UTC Z timestamp required')
    d=datetime.datetime.fromisoformat(value[:-1]+'+00:00');require(d.tzinfo is not None);return d

def text(v):require(isinstance(v,str) and bool(v.strip()),'nonempty text required')
def db_open(path):
    db=sqlite3.connect(path);db.row_factory=sqlite3.Row;db.execute('PRAGMA foreign_keys=ON');return db

def initialize(path,config):
    exact(config,['scope_id','account_namespace','source_namespace','authorized_start_utc','authorized_end_utc','authorization_reference'])
    for v in config.values():text(v)
    require(utc(config['authorized_start_utc'])<utc(config['authorized_end_utc']),'invalid authorized window')
    db=db_open(path)
    try:
        db.executescript(SCHEMA)
        for table in TABLES:
            for action in ['UPDATE','DELETE']:db.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_{action.lower()}_immutable BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'append-only ledger'); END")
        old=db.execute('SELECT config_sha256 FROM scope WHERE id=?',(config['scope_id'],)).fetchone();h=digest(canonical(config))
        if old:require(old[0]==h,'scope config immutable; use new scope')
        else:db.execute('INSERT INTO scope VALUES(?,?,?)',(config['scope_id'],canonical(config),h))
        db.commit()
    finally:db.close()

def scope(db,id):
    r=db.execute('SELECT config_json FROM scope WHERE id=?',(id,)).fetchone();require(r is not None,'scope not authorized');return json.loads(r[0])
def current_cursor(db,id,kind):
    r=db.execute('SELECT through_utc FROM cursor_events WHERE scope_id=? AND kind=? ORDER BY seq DESC LIMIT 1',(id,kind)).fetchone();return r[0] if r else scope(db,id)['authorized_start_utc']
def gaps(db,id):
    return [dict(r) for r in db.execute("SELECT e.source_key,e.reason,e.reference FROM gap_events e WHERE e.scope_id=? AND e.action='open' AND e.seq=(SELECT max(x.seq) FROM gap_events x WHERE x.scope_id=e.scope_id AND x.source_key=e.source_key) ORDER BY e.source_key",(id,))]
def event(db,id,key,action,reason,reference):db.execute('INSERT INTO gap_events(scope_id,source_key,action,reason,reference) VALUES(?,?,?,?,?)',(id,key,action,reason,reference))
def maybe_complete(db,id,reference):
    if gaps(db,id):return
    scan=current_cursor(db,id,'scan');previous=current_cursor(db,id,'completion')
    if utc(scan)>utc(previous):db.execute("INSERT INTO cursor_events(scope_id,kind,through_utc,reference) VALUES(?,'completion',?,?)",(id,scan,reference))
def revision_id(scope_id,key,hash):return 'revision-'+digest(canonical([scope_id,key,hash]))

def validate_capture(p,cfg):
    exact(p,['format','capture_id','scope_id','account_namespace','source_namespace','window_start_utc','window_end_utc','snapshot_id','pages'])
    require(p['format']=='cp-source-capture-v1');text(p['capture_id']);text(p['snapshot_id'])
    for k in ['scope_id','account_namespace','source_namespace']:require(p[k]==cfg[k],'scope/account mismatch')
    a,b=utc(p['window_start_utc']),utc(p['window_end_utc']);require(utc(cfg['authorized_start_utc'])<=a<b<=utc(cfg['authorized_end_utc']),'capture outside authorized window')
    require(isinstance(p['pages'],list) and 0<len(p['pages'])<=10000,'complete pages required');next_cursor=None;seen_cursors=set();seen_keys=set();items=[];total=None
    for i,page in enumerate(p['pages']):
        exact(page,['index','request_cursor','next_cursor','snapshot_id','total_items','items']);require(type(page['index']) is int and page['index']==i);require(page['request_cursor']==next_cursor,'broken cursor chain');require(page['snapshot_id']==p['snapshot_id'],'snapshot changed')
        require(type(page['total_items']) is int and page['total_items']>=0)
        if total is None:total=page['total_items']
        require(total==page['total_items'],'total changed');require(isinstance(page['items'],list));next_cursor=page['next_cursor']
        if next_cursor is not None:text(next_cursor);require(next_cursor not in seen_cursors,'cursor cycle');seen_cursors.add(next_cursor)
        require((i==len(p['pages'])-1)==(next_cursor is None),'missing final page')
        for item in page['items']:
            exact(item,['source_key','source_time_utc','status','reason','metadata_origin','title','category','material_date','institution','display_author','content','content_sha256'])
            text(item['source_key']);require(item['source_key'] not in seen_keys,'duplicate source item');seen_keys.add(item['source_key']);require(a<=utc(item['source_time_utc'])<b,'item outside window');require(item['status'] in ('captured','unavailable'))
            require(item['metadata_origin'] in ('source','file_label'),'metadata origin required')
            if item['status']=='captured':
                text(item['content']);text(item['title']);require(item['category'] in ('internal','sellside','news'));require(item['content_sha256']==digest(item['content']),'capture content hash mismatch')
            else:require(item['content'] is None and item['content_sha256'] is None);text(item['reason'])
            items.append(item)
    require(len(items)==total,'incomplete enumerated manifest');return items

def capture(path,p):
    db=db_open(path)
    try:
        db.execute('BEGIN IMMEDIATE');cfg=scope(db,p.get('scope_id'));items=validate_capture(p,cfg);h=digest(canonical(p));old=db.execute('SELECT capture_sha256 FROM captures WHERE id=?',(p['capture_id'],)).fetchone()
        if old:require(old[0]==h,'capture replay changed');db.rollback();return {'replayed':True}
        previous=current_cursor(db,p['scope_id'],'scan');require(utc(p['window_start_utc'])<=utc(previous),'unscanned window gap')
        db.execute('INSERT INTO captures VALUES(?,?,?,?,?,?)',(p['capture_id'],p['scope_id'],h,p['window_start_utc'],p['window_end_utc'],canonical(p)))
        for item in items:
            key=item['source_key'];rid=None
            if item['status']=='captured':
                rid=revision_id(p['scope_id'],key,item['content_sha256']);existing=db.execute('SELECT item_json FROM revisions WHERE id=?',(rid,)).fetchone()
                if existing:
                    # Metadata drift is not silently merged into a content identity.
                    old_item=json.loads(existing[0]);require(all(old_item[k]==item[k] for k in ['title','category','material_date','institution','display_author','content']),'same content has metadata drift; explicit new scope/reconciliation required')
                else:db.execute('INSERT INTO revisions VALUES(?,?,?,?,?,?)',(rid,p['scope_id'],key,item['content_sha256'],canonical(item),p['capture_id']))
                ingested=db.execute('SELECT 1 FROM receipts WHERE revision_id=?',(rid,)).fetchone()
                excluded=db.execute('SELECT payload_json FROM reviews WHERE revision_id=? ORDER BY rowid DESC LIMIT 1',(rid,)).fetchone()
                terminal=bool(ingested) or bool(excluded and json.loads(excluded[0])['decision'] in ('exclude','exclude_duplicate'))
                if not terminal:
                    prior=db.execute('SELECT 1 FROM revisions WHERE scope_id=? AND source_key=? AND id<>?',(p['scope_id'],key,rid)).fetchone();event(db,p['scope_id'],key,'open','changed_source_quarantined' if prior else 'semantic_review_required',rid)
            else:event(db,p['scope_id'],key,'open','source_unavailable: '+item['reason'],p['capture_id'])
            db.execute('INSERT INTO observations VALUES(?,?,?,?)',(p['capture_id'],key,rid,item['status']))
        if utc(p['window_end_utc'])>utc(previous):db.execute("INSERT INTO cursor_events(scope_id,kind,through_utc,reference) VALUES(?,'scan',?,?)",(p['scope_id'],p['window_end_utc'],p['capture_id']))
        maybe_complete(db,p['scope_id'],p['capture_id']);db.commit();return {'replayed':False,'captured_items':len(items),'open_gaps':len(gaps(db,p['scope_id']))}
    except Exception:db.rollback();raise
    finally:db.close()

REVIEW_KEYS=['format','review_id','revision_id','source_content_sha256','decision','reviewer','reviewed_at_utc','reason','full_text_read','semantic_review','body_mode','title_mode','metadata_overrides','source_span','duplicate_of_revision_id','previous_revision_id','coverage','batch']
def review(path,r):
    exact(r,REVIEW_KEYS);require(r['format']=='cp-source-review-v1');require(r['decision'] in ('approve','approve_revision','exclude','exclude_duplicate'));text(r['review_id']);text(r['reviewer']);text(r['reason']);utc(r['reviewed_at_utc']);require(r['full_text_read'] is True and r['semantic_review']=='approved','explicit completed semantic review required')
    db=db_open(path)
    try:
        db.execute('BEGIN IMMEDIATE');rev=db.execute('SELECT * FROM revisions WHERE id=?',(r['revision_id'],)).fetchone();require(rev is not None,'unknown revision');require(rev['content_sha256']==r['source_content_sha256'],'review source hash mismatch');item=json.loads(rev['item_json']);h=digest(canonical(r));prior=db.execute('SELECT review_sha256 FROM reviews WHERE id=?',(r['review_id'],)).fetchone()
        if prior:require(prior[0]==h,'review replay changed');db.rollback();return {'replayed':True}
        require(not db.execute('SELECT 1 FROM receipts WHERE revision_id=?',(rev['id'],)).fetchone(),'revision already ingested')
        for intent in db.execute('SELECT i.batch_json,b.target_path FROM reviews r JOIN apply_intents i ON i.review_id=r.id JOIN target_bindings b ON b.review_id=r.id WHERE r.revision_id=?',(rev['id'],)):
            target=sqlite3.connect('file:'+str(Path(intent['target_path']).resolve())+'?mode=ro',uri=True)
            try:committed=target.execute('SELECT 1 FROM batches WHERE id=?',(json.loads(intent['batch_json'])['batch_id'],)).fetchone()
            finally:target.close()
            require(not committed,'committed intent requires reconcile before new review')
        require(isinstance(r['coverage'],list));db.execute('INSERT INTO reviews VALUES(?,?,?,?)',(r['review_id'],rev['id'],h,canonical(r)))
        if r['decision']=='exclude_duplicate':
            require(r['batch'] is None and r['previous_revision_id'] is None and r['body_mode'] is None and r['source_span'] is None and not r['coverage'],'duplicate exclusion cannot write archive')
            covering=db.execute('SELECT x.* FROM revisions x JOIN receipts t ON t.revision_id=x.id WHERE x.id=?',(r['duplicate_of_revision_id'],)).fetchone();require(covering is not None and covering['scope_id']==rev['scope_id'] and covering['id']!=rev['id'] and covering['content_sha256']==rev['content_sha256'],'duplicate must reference ingested exact source hash')
            bound=db.execute('SELECT i.batch_json,t.receipt_json,b.target_path FROM receipts t JOIN apply_intents i ON i.review_id=t.review_id JOIN target_bindings b ON b.review_id=t.review_id WHERE t.revision_id=?',(covering['id'],)).fetchone();verify_archive(bound['target_path'],json.loads(bound['batch_json']),json.loads(bound['receipt_json']))
            db.execute('INSERT INTO coverage_ledger VALUES(?,?,?,?,?,?)',('duplicate-'+digest(r['review_id']),r['review_id'],covering['id'],rev['id'],'exact_duplicate',r['reason']));resolve_if_current(db,rev,r['review_id']);maybe_complete(db,rev['scope_id'],r['review_id']);db.commit();return {'replayed':False,'decision':'exclude_duplicate','new_memo':False}
        require(r['duplicate_of_revision_id'] is None,'duplicate reference requires explicit exclusion')
        if r['decision']=='exclude':
            require(r['batch'] is None and r['previous_revision_id'] is None and r['body_mode'] is None and r['source_span'] is None and not r['coverage'],'excluded record cannot write archive')
            resolve_if_current(db,rev,r['review_id']);maybe_complete(db,rev['scope_id'],r['review_id']);db.commit();return {'replayed':False,'decision':'exclude'}
        require(r['body_mode'] in ('verbatim','verbatim_excerpt','reviewed_safe_summary'),'explicit body transformation required')
        batch=r['batch'];require(isinstance(batch,dict) and isinstance(batch.get('memos'),list) and len(batch['memos'])==1,'review must approve exactly one memo');m=batch['memos'][0]
        require(m['category']==item['category'],'source category must be preserved')
        require(isinstance(r['metadata_overrides'],dict),'metadata review required')
        for key,decision in r['metadata_overrides'].items():
            require(key in ('title','material_date','institution','display_author'),'unsupported metadata override');exact(decision,['value','reason']);text(decision['reason']);require(decision['value']==m[key],'metadata review value mismatch')
        for key in ('material_date','institution','display_author'):
            if m[key]!=item[key] or (item['metadata_origin']=='file_label' and m[key] is not None):require(key in r['metadata_overrides'],'explicit verified metadata required')
        require(r['title_mode'] in ('preserve','verified_source_title','reviewed_company_summary'),'explicit title mode required')
        if r['title_mode']=='preserve':require(m['title']==item['title'] and item['metadata_origin']=='source','source title must be preserved or file label explicitly verified')
        elif r['title_mode']=='verified_source_title':require(item['metadata_origin']=='file_label' and 'title' in r['metadata_overrides'],'file title verification required')
        else:require(item['category']=='sellside','revised display title only allowed for sellside');text(m['title'])
        require(m['id']=='memo-'+digest(rev['id'])[:32] and m['source_key']=='source-'+digest(canonical([rev['scope_id'],rev['source_key'],rev['content_sha256']]))[:32],'deterministic source identity required')
        require(batch['batch_id']=='batch-'+digest(r['review_id'])[:32],'review batch identity mismatch')
        if item['category']=='internal':require(r['body_mode'] in ('verbatim','verbatim_excerpt'),'internal source text must remain verbatim')
        if r['body_mode']=='verbatim':require(m['body']==item['content'],'verbatim body differs')
        if r['body_mode']=='verbatim_excerpt':
            span=r['source_span'];exact(span,['start','end']);require(type(span['start']) is int and type(span['end']) is int and 0<=span['start']<span['end']<=len(item['content']),'invalid source span');require(item['content'][span['start']:span['end']]==m['body'],'source excerpt differs')
        else:require(r['source_span'] is None,'span only allowed for verbatim excerpt')
        require(m['sha256']==digest(m['body']),'review body hash mismatch');require(r['decision']=='approve_revision' or r['previous_revision_id'] is None)
        existing=db.execute('SELECT id FROM revisions WHERE scope_id=? AND source_key=? AND id<>?',(rev['scope_id'],rev['source_key'],rev['id'])).fetchall()
        if existing:
            require(r['decision']=='approve_revision' and r['previous_revision_id'] in [x[0] for x in existing],'source change requires explicit revision review')
        if r['decision']=='approve_revision':
            old=db.execute('SELECT * FROM revisions WHERE id=?',(r['previous_revision_id'],)).fetchone();require(old is not None and old['scope_id']==rev['scope_id'] and old['source_key']==rev['source_key'],'previous revision mismatch')
            db.execute('INSERT INTO revision_ledger VALUES(?,?,?,?,?)',('revision-link-'+digest(r['review_id']),r['review_id'],rev['id'],old['id'],r['reason']))
        for i,c in enumerate(r['coverage']):
            exact(c,['covered_revision_id','kind','reason']);text(c['reason']);require(c['kind'] in ('exact_duplicate','fully_covered','partially_covered'));covered=db.execute('SELECT * FROM revisions WHERE id=?',(c['covered_revision_id'],)).fetchone();require(covered is not None and covered['scope_id']==rev['scope_id'] and covered['id']!=rev['id'],'coverage scope mismatch')
            if c['kind']=='exact_duplicate':require(covered['content_sha256']==rev['content_sha256'],'not an exact duplicate')
            db.execute('INSERT INTO coverage_ledger VALUES(?,?,?,?,?,?)',('coverage-'+digest(canonical([r['review_id'],i])),r['review_id'],rev['id'],covered['id'],c['kind'],c['reason']))
        # Reuse actual starter engine against an isolated ephemeral schema to validate
        # full syntax at apply time; no approval is inferred from technical validation.
        db.execute('INSERT INTO apply_intents VALUES(?,?,?)',(r['review_id'],canonical(batch),digest(canonical(batch))));db.commit();return {'replayed':False,'decision':r['decision'],'ready_to_apply':True}
    except Exception:db.rollback();raise
    finally:db.close()

def resolve_if_current(db,rev,reference):
    # A prior review must never close a gap belonging to a later changed revision.
    latest=db.execute('SELECT revision_id,status FROM observations o JOIN captures c ON c.id=o.capture_id WHERE c.scope_id=? AND o.source_key=? ORDER BY c.rowid DESC LIMIT 1',(rev['scope_id'],rev['source_key'])).fetchone()
    open_now=any(g['source_key']==rev['source_key'] for g in gaps(db,rev['scope_id']))
    if open_now and latest and latest['revision_id']==rev['id'] and latest['status']=='captured':event(db,rev['scope_id'],rev['source_key'],'resolve','reviewed_terminal',reference)

def apply(path,target,review_id,crash_after_archive=False):
    db=db_open(path)
    try:
        require(db.execute('SELECT 1 FROM apply_intents WHERE review_id=?',(review_id,)).fetchone(),'approved intent required')
        target_path=str(Path(target).resolve());db.execute('BEGIN IMMEDIATE');binding=db.execute('SELECT target_path FROM target_bindings WHERE review_id=?',(review_id,)).fetchone()
        owner_scope=db.execute('SELECT x.scope_id FROM reviews r JOIN revisions x ON x.id=r.revision_id WHERE r.id=?',(review_id,)).fetchone()[0]
        scope_binding=db.execute('SELECT target_path FROM scope_targets WHERE scope_id=?',(owner_scope,)).fetchone()
        if scope_binding:require(scope_binding[0]==target_path,'scope target database changed; new scope or explicit migration required')
        else:db.execute('INSERT INTO scope_targets VALUES(?,?)',(owner_scope,target_path))
        if binding:require(binding[0]==target_path,'target database changed')
        else:db.execute('INSERT INTO target_bindings VALUES(?,?)',(review_id,target_path))
        db.commit()
        # Hold ledger write lock through canonical commit and readback, preventing a
        # concurrent capture/revocation from invalidating the reviewed observation.
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT r.*,i.batch_json FROM reviews r JOIN apply_intents i ON i.review_id=r.id WHERE r.id=?',(review_id,)).fetchone();rev=db.execute('SELECT * FROM revisions WHERE id=?',(row['revision_id'],)).fetchone()
        latest_review=db.execute('SELECT id,payload_json FROM reviews WHERE revision_id=? ORDER BY rowid DESC LIMIT 1',(rev['id'],)).fetchone();require(latest_review['id']==review_id and json.loads(latest_review['payload_json'])['decision'] in ('approve','approve_revision'),'review superseded or excluded')
        latest_observation=db.execute('SELECT o.revision_id,o.status FROM observations o JOIN captures c ON c.id=o.capture_id WHERE c.scope_id=? AND o.source_key=? ORDER BY c.rowid DESC LIMIT 1',(rev['scope_id'],rev['source_key'])).fetchone();require(latest_observation is not None and latest_observation['revision_id']==rev['id'] and latest_observation['status']=='captured','source changed or unavailable after review')
        for historical in db.execute('SELECT i.batch_json,t.receipt_json FROM receipts t JOIN revisions x ON x.id=t.revision_id JOIN apply_intents i ON i.review_id=t.review_id WHERE x.scope_id=?',(owner_scope,)):
            verify_archive(target,json.loads(historical['batch_json']),json.loads(historical['receipt_json']))
        batch=json.loads(row['batch_json']);prior=db.execute('SELECT receipt_json FROM receipts WHERE review_id=?',(review_id,)).fetchone()
        if prior:
            result=json.loads(prior[0]);verify_archive(target,batch,result);resolve_if_current(db,rev,review_id);maybe_complete(db,rev['scope_id'],review_id);db.commit();return {'replayed':True,'receipt':result}
        result=starter.import_batch(target,batch);verify_archive(target,batch,result)
        if crash_after_archive:raise RuntimeError('synthetic crash after archive commit')
        db.execute('INSERT INTO receipts VALUES(?,?,?,?,?)',(review_id,rev['id'],batch['memos'][0]['id'],result['version'],canonical(result)));resolve_if_current(db,rev,review_id);maybe_complete(db,rev['scope_id'],review_id);db.commit();return {'replayed':result['replayed'],'receipt':result}
    except Exception:db.rollback();raise
    finally:db.close()

def reconcile(path,target,review_id):
    """Record only an already committed, verified historical batch. Never writes archive."""
    db=db_open(path)
    try:
        db.execute('BEGIN IMMEDIATE');row=db.execute('SELECT r.revision_id,i.batch_json,b.target_path FROM reviews r JOIN apply_intents i ON i.review_id=r.id JOIN target_bindings b ON b.review_id=r.id WHERE r.id=?',(review_id,)).fetchone();require(row is not None and row['target_path']==str(Path(target).resolve()),'bound committed intent required')
        batch=json.loads(row['batch_json']);archive=sqlite3.connect('file:'+str(Path(target).resolve())+'?mode=ro',uri=True)
        try:committed=archive.execute('SELECT version FROM batches WHERE id=?',(batch['batch_id'],)).fetchone()
        finally:archive.close()
        require(committed is not None,'no committed batch; reconcile cannot import');result={'version':committed[0],'replayed':True};verify_archive(target,batch,result)
        rev=db.execute('SELECT * FROM revisions WHERE id=?',(row['revision_id'],)).fetchone();scope_target=db.execute('SELECT target_path FROM scope_targets WHERE scope_id=?',(rev['scope_id'],)).fetchone();require(scope_target and scope_target[0]==str(Path(target).resolve()),'scope target mismatch')
        prior=db.execute('SELECT 1 FROM receipts WHERE review_id=?',(review_id,)).fetchone()
        if not prior:db.execute('INSERT INTO receipts VALUES(?,?,?,?,?)',(review_id,rev['id'],batch['memos'][0]['id'],result['version'],canonical(result)))
        latest=db.execute('SELECT id FROM reviews WHERE revision_id=? ORDER BY rowid DESC LIMIT 1',(rev['id'],)).fetchone()
        if latest[0]==review_id:resolve_if_current(db,rev,review_id);maybe_complete(db,rev['scope_id'],review_id)
        db.commit();return {'reconciled':True,'archive_written':False,'receipt':result}
    except Exception:db.rollback();raise
    finally:db.close()

def verify_archive(target,batch,receipt):
    db=sqlite3.connect('file:'+str(Path(target).resolve())+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
    try:
        db.execute('BEGIN');r=db.execute('SELECT * FROM batches WHERE id=?',(batch['batch_id'],)).fetchone()
        require(r is not None and r['sha256']==digest(canonical(batch)) and r['version']==receipt['version'],'archive receipt readback mismatch')
        require(db.execute('SELECT version FROM state WHERE id=1').fetchone()[0]>=receipt['version'],'archive head behind receipt')
        for m in batch['memos']:
            actual=db.execute('SELECT m.*,b.body FROM memos m JOIN bodies b ON b.sha256=m.body_sha256 WHERE m.id=?',(m['id'],)).fetchone();require(actual is not None,'archive memo missing')
            for k in ['id','title','category','material_date','institution','display_author','author_private','primary_subject','source_key','body']:require(actual[k]==m[k],'archive memo readback mismatch')
            require(actual['body_sha256']==m['sha256'] and digest(actual['body'])==m['sha256'],'archive body readback mismatch')
            rows=db.execute('SELECT a.subject_id,a.scope,e.quote,e.start_offset,e.end_offset,e.body_sha256 FROM assignments a JOIN evidence e ON e.id=a.evidence_id WHERE a.memo_id=?',(m['id'],)).fetchall();require(len(rows)==len(m['assignments']),'archive assignment count mismatch')
            expected={x['subject_id']:x for x in m['assignments']}
            for x in rows:
                a=expected.get(x['subject_id']);require(a is not None and x['scope']==a['scope'] and x['quote']==a['quote'] and x['start_offset']==a['start'] and x['end_offset']==a['end'] and x['body_sha256']==m['sha256'],'archive evidence readback mismatch')
            tables=db.execute('SELECT content_json FROM native_tables WHERE memo_id=? ORDER BY id',(m['id'],)).fetchall();require(sorted(canonical(json.loads(t[0])) for t in tables)==sorted(canonical(t) for t in m['tables']),'archive native tables mismatch')
    finally:db.close()

def status(path,id):
    db=db_open(path)
    try:
        db.execute('BEGIN');scope(db,id)
        return {'scope_id':id,'scan_cursor':current_cursor(db,id,'scan'),'completion_cursor':current_cursor(db,id,'completion'),'gaps':gaps(db,id),'revisions':[dict(r) for r in db.execute('SELECT id,source_key,content_sha256 FROM revisions WHERE scope_id=? ORDER BY rowid',(id,))]}
    finally:db.close()

def prepare_review(path,rid,review_id,expected_version):
    require(type(expected_version) is int and expected_version>=0);text(review_id);db=db_open(path)
    try:rev=db.execute('SELECT * FROM revisions WHERE id=?',(rid,)).fetchone();require(rev is not None,'unknown revision');item=json.loads(rev['item_json'])
    finally:db.close()
    batch={'batch_id':'batch-'+digest(review_id)[:32],'expected_version':expected_version,'subjects':[],'memos':[]}
    for key in ['industry_versions','industries','company_industries','theme_parents','theme_aliases','theme_crosslinks']:batch[key]=[]
    memo={k:item[k] for k in ['title','category','material_date','institution','display_author']}
    memo.update(id='memo-'+digest(rid)[:32],source_key='source-'+digest(canonical([rev['scope_id'],rev['source_key'],rev['content_sha256']]))[:32],author_private=None,body=item['content'],sha256=rev['content_sha256'],primary_subject='',assignments=[],tables=[]);batch['memos']=[memo]
    return {'format':'cp-source-review-v1','review_id':review_id,'revision_id':rid,'source_content_sha256':rev['content_sha256'],'decision':'pending','reviewer':'','reviewed_at_utc':'','reason':'','full_text_read':False,'semantic_review':'pending','body_mode':'verbatim','title_mode':'preserve','metadata_overrides':{},'source_span':None,'duplicate_of_revision_id':None,'previous_revision_id':None,'coverage':[],'batch':batch}

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['init','capture','prepare-review','review','apply','reconcile','status']);p.add_argument('--ledger',required=True);p.add_argument('--input');p.add_argument('--target');p.add_argument('--review-id');p.add_argument('--scope');p.add_argument('--revision');p.add_argument('--expected-version',type=int);p.add_argument('--output');a=p.parse_args()
    payload=starter.load_json(Path(a.input).read_text()) if a.input else None
    if a.action=='init':initialize(a.ledger,payload);result={'initialized':True}
    elif a.action=='capture':result=capture(a.ledger,payload)
    elif a.action=='prepare-review':result=prepare_review(a.ledger,a.revision,a.review_id,a.expected_version)
    elif a.action=='review':result=review(a.ledger,payload)
    elif a.action in ('apply','reconcile'):require(bool(a.target) and bool(a.review_id));result=(apply if a.action=='apply' else reconcile)(a.ledger,a.target,a.review_id)
    else:result=status(a.ledger,a.scope)
    if a.output:Path(a.output).write_text(json.dumps(result,ensure_ascii=False,indent=2));print(canonical({'written':str(Path(a.output))}))
    else:print(canonical(result))
