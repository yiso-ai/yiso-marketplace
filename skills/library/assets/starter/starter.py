"""Synthetic new-instance demo. NOT a production runner or migration replacement."""
import argparse, datetime, hashlib, http.server, json, re, sqlite3
from pathlib import Path
from urllib.parse import urlsplit,parse_qs
ROOT=Path(__file__).resolve().parent

def canonical(x): return json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def digest(s): return hashlib.sha256(s.encode('utf-8')).hexdigest()
def load_json(text):
    def pairs(items):
        out={}
        for key,value in items:
            require(key not in out,'duplicate JSON field');out[key]=value
        return out
    def invalid(value): raise ValueError('non-finite JSON number')
    return json.loads(text,object_pairs_hook=pairs,parse_constant=invalid)
def require(ok,message='invalid import'):
    if not ok: raise ValueError(message)
def exact(obj,keys): require(isinstance(obj,dict) and set(obj)==set(keys),'unexpected or missing fields')
def safe(x):
    if isinstance(x,str):
        require(len(x)<200000 and not re.search(r'https?://|data:|javascript:|<[^>]*>|!\[|Bearer\s|PRIVATE KEY|[\x00-\x08]',x,re.I),'unsafe text')
    elif isinstance(x,list):
        for v in x: safe(v)
    elif isinstance(x,dict):
        for k,v in x.items(): safe(k);safe(v)
    else: require(x is None or isinstance(x,(int,float,bool)))
def ident(x): require(isinstance(x,str) and re.fullmatch(r'[a-zA-Z0-9_-]{1,80}',x),'invalid identifier')
def string(x): require(isinstance(x,str) and 0<len(x.strip())<=200000,'empty or invalid text')
def connect(path):
    db=sqlite3.connect(path);db.row_factory=sqlite3.Row;db.execute('PRAGMA foreign_keys=ON');return db

def initialize(path):
    db=connect(path);db.executescript((ROOT/'schema.sql').read_text());db.close()

def import_batch(path,p):
    exact(p,['batch_id','expected_version','subjects','memos','industry_versions','industries','company_industries','theme_parents','theme_aliases','theme_crosslinks']);safe(p);ident(p['batch_id']);require(type(p['expected_version']) is int and p['expected_version']>=0)
    require(isinstance(p['subjects'],list) and isinstance(p['memos'],list));fingerprint=digest(canonical(p));db=connect(path)
    try:
        db.execute('BEGIN IMMEDIATE')
        prior=db.execute('SELECT * FROM batches WHERE id=?',(p['batch_id'],)).fetchone()
        if prior:
            require(prior['sha256']==fingerprint,'batch replay differs');db.rollback();return {'version':prior['version'],'replayed':True}
        version=db.execute('SELECT version FROM state WHERE id=1').fetchone()[0];require(version==p['expected_version'],'version conflict')
        for s in p['subjects']:
            exact(s,['id','name','kind']);ident(s['id']);string(s['name']);require(s['kind'] in ('company','theme'))
            old=db.execute('SELECT * FROM subjects WHERE id=?',(s['id'],)).fetchone()
            if old: require(dict(old)==s,'subject conflict')
            else: db.execute('INSERT INTO subjects VALUES(?,?,?)',(s['id'],s['name'],s['kind']))
        for table,keys in [('industry_versions',['id','scheme','version']),('industries',['id','version_id','name','level','parent_id']),('company_industries',['company_id','industry_id','mapping_kind']),('theme_parents',['child_id','parent_id']),('theme_aliases',['theme_id','alias']),('theme_crosslinks',['source_id','target_id','reason'])]:
            require(isinstance(p[table],list))
            for row in p[table]:
                exact(row,keys)
                for k,v in row.items():
                    if k=='level': require(type(v) is int and v in (1,2,3))
                    elif v is None: require(k=='parent_id')
                    else: string(v)
                cols=','.join(keys);marks=','.join('?' for _ in keys)
                db.execute('INSERT INTO '+table+'('+cols+') VALUES('+marks+')',[row[k] for k in keys])
        validate_taxonomy(db)
        for m in p['memos']:
            exact(m,['id','title','category','material_date','institution','display_author','author_private','body','sha256','primary_subject','source_key','assignments','tables'])
            for k in ('id','primary_subject','source_key'): ident(m[k])
            for k in ('title','body'): string(m[k])
            require(m['category'] in ('internal','sellside','news'))
            for k in ('institution','display_author','author_private'):
                if m[k] is not None: string(m[k])
            if m['category'] in ('sellside','news'): string(m['institution'])
            if m['material_date'] is not None:
                require(isinstance(m['material_date'],str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}',m['material_date']));datetime.date.fromisoformat(m['material_date'])
            require(m['sha256']==digest(m['body']),'body hash mismatch')
            require(isinstance(m['assignments'],list) and isinstance(m['tables'],list))
            primaries=[a for a in m['assignments'] if isinstance(a,dict) and a.get('scope')=='primary']
            require(len(primaries)==1 and primaries[0].get('subject_id')==m['primary_subject'],'unique primary required')
            db.execute('INSERT OR IGNORE INTO bodies VALUES(?,?)',(m['sha256'],m['body']))
            require(db.execute('SELECT body FROM bodies WHERE sha256=?',(m['sha256'],)).fetchone()[0]==m['body'])
            db.execute('INSERT INTO memos VALUES(?,?,?,?,?,?,?,?,?,?)',(m['id'],m['title'],m['category'],m['material_date'],m['institution'],m['display_author'],m['author_private'],m['sha256'],m['primary_subject'],m['source_key']))
            for i,a in enumerate(m['assignments']):
                exact(a,['subject_id','scope','start','end','quote']);ident(a['subject_id']);require(a['scope'] in ('primary','substantive'))
                require(type(a['start']) is int and type(a['end']) is int and 0<=a['start']<a['end']<=len(m['body']));require(m['body'][a['start']:a['end']]==a['quote'],'evidence mismatch')
                eid=m['id']+'-e-'+str(i)
                db.execute('INSERT INTO evidence VALUES(?,?,?,?,?,?)',(eid,m['id'],m['sha256'],a['start'],a['end'],a['quote']))
                db.execute('INSERT INTO assignments VALUES(?,?,?,?)',(m['id'],a['subject_id'],eid,a['scope']))
            for i,t in enumerate(m['tables']):
                exact(t,['headers','rows']);require(isinstance(t['headers'],list) and 0<len(t['headers'])<=30 and all(isinstance(c,str) for c in t['headers']))
                require(isinstance(t['rows'],list) and len(t['rows'])<=1000 and all(isinstance(r,list) and len(r)==len(t['headers']) and all(isinstance(c,str) for c in r) for r in t['rows']))
                db.execute('INSERT INTO native_tables VALUES(?,?,?)',(m['id']+'-t-'+str(i),m['id'],canonical(t)))
        require(not db.execute('PRAGMA foreign_key_check').fetchall(),'foreign key failure')
        changed=db.execute('UPDATE state SET version=version+1 WHERE id=1 AND version=?',(version,)).rowcount;require(changed==1,'version conflict')
        db.execute('INSERT INTO batches VALUES(?,?,?)',(p['batch_id'],fingerprint,version+1));db.commit();return {'version':version+1,'replayed':False}
    except Exception:
        db.rollback();raise
    finally: db.close()

def validate_taxonomy(db):
    require(not db.execute("SELECT 1 FROM industries n LEFT JOIN industries p ON p.id=n.parent_id WHERE n.level>1 AND (p.id IS NULL OR p.level<>n.level-1 OR p.version_id<>n.version_id)").fetchone(),'industry parent mismatch')
    require(not db.execute("SELECT 1 FROM company_industries x JOIN subjects s ON s.id=x.company_id WHERE s.kind<>'company'").fetchone(),'industry mapping requires a company')
    for table,columns in [('theme_parents',['child_id','parent_id']),('theme_aliases',['theme_id']),('theme_crosslinks',['source_id','target_id'])]:
        for col in columns:
            require(not db.execute('SELECT 1 FROM '+table+' x LEFT JOIN subjects s ON s.id=x.'+col+" WHERE s.id IS NULL OR s.kind<>'theme'").fetchone(),'theme identity required')
    parents=dict(db.execute('SELECT child_id,parent_id FROM theme_parents'))
    for start in parents:
        seen=set();cur=start
        while cur in parents:
            require(cur not in seen,'theme cycle');seen.add(cur);cur=parents[cur]

def directory_data(db):
    result={t:[dict(r) for r in db.execute('SELECT * FROM '+t)] for t in ['industry_versions','industries','company_industries','theme_parents','theme_aliases','theme_crosslinks']}
    scores=[]
    for c in db.execute("SELECT id FROM subjects WHERE kind='company'"):
        total=db.execute('SELECT count(DISTINCT memo_id) FROM assignments WHERE subject_id=?',(c['id'],)).fetchone()[0]
        for t in db.execute("SELECT id FROM subjects WHERE kind='theme'"):
            count=db.execute('SELECT count(DISTINCT a.memo_id) FROM assignments a JOIN assignments b ON a.memo_id=b.memo_id WHERE a.subject_id=? AND b.subject_id=?',(c['id'],t['id'])).fetchone()[0]
            if count:scores.append({'company_id':c['id'],'theme_id':t['id'],'n_related':count,'n_all':total,'ratio':count/total})
    result['company_themes']=sorted(scores,key=lambda r:(-r['n_related'],-r['ratio'],r['company_id'],r['theme_id']))
    return result

def public_data(path):
    db=sqlite3.connect('file:'+str(Path(path).resolve())+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
    try:
        db.execute('BEGIN')
        result={'version':db.execute('SELECT version FROM state').fetchone()[0],'subjects':[dict(r) for r in db.execute('SELECT id,name,kind FROM subjects ORDER BY name')],'memos':[]}
        for r in db.execute("SELECT m.id,m.title,m.category,m.material_date,m.institution,CASE WHEN m.category='internal' THEN m.display_author ELSE NULL END display_author,m.primary_subject,b.body FROM memos m JOIN bodies b ON b.sha256=m.body_sha256 ORDER BY material_date IS NULL,material_date DESC,m.id"):
            m=dict(r);m['assignments']=[dict(a) for a in db.execute('SELECT a.subject_id,a.scope,e.quote,e.start_offset,e.end_offset FROM assignments a JOIN evidence e ON e.id=a.evidence_id JOIN memos m ON m.id=a.memo_id WHERE a.memo_id=? AND e.memo_id=m.id AND e.body_sha256=m.body_sha256',(m['id'],))];m['tables']=[json.loads(t[0]) for t in db.execute('SELECT content_json FROM native_tables WHERE memo_id=?',(m['id'],))];result['memos'].append(m)
        result.update(directory_data(db));return result
    finally: db.close()

def serve(path,port):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            # No remote host rebinding, CORS or filesystem routing.
            if self.headers.get('Host') not in (f'127.0.0.1:{port}',f'localhost:{port}'):
                self.send_error(403);return
            target=urlsplit(self.path).path
            if target=='/api/data':
                query=parse_qs(urlsplit(self.path).query)
                try: offset=int(query.get('offset',['0'])[0]);limit=int(query.get('limit',['50'])[0]);require(offset>=0 and 1<=limit<=50)
                except (ValueError,TypeError):self.send_error(400);return
                data=public_data(path);total=len(data['memos']);data['memos']=data['memos'][offset:offset+limit];data.update(total=total,nextOffset=offset+len(data['memos']) if offset+len(data['memos'])<total else None)
                content=canonical(data).encode();mime='application/json'
            elif target in ('/','/style.css','/app.js'):
                filename={'/':'index.html','/style.css':'style.css','/app.js':'app.js'}[target];content=(ROOT/filename).read_bytes();mime={'/':'text/html','/style.css':'text/css','/app.js':'text/javascript'}[target]
            else: self.send_error(404);return
            self.send_response(200);self.send_header('Content-Type',mime+'; charset=utf-8');self.send_header('Content-Length',str(len(content)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'none'; frame-ancestors 'none'; base-uri 'none'");self.end_headers();self.wfile.write(content)
        def log_message(self,*args): pass
    print(f'Synthetic local demo: http://127.0.0.1:{port} (GET only)',flush=True)
    http.server.ThreadingHTTPServer(('127.0.0.1',port),Handler).serve_forever()

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=['init','import','serve']);parser.add_argument('--db',required=True);parser.add_argument('--input');parser.add_argument('--port',type=int,default=8765);a=parser.parse_args()
    if a.action=='init': initialize(a.db)
    elif a.action=='import':
        require(bool(a.input),'--input required');print(canonical(import_batch(a.db,load_json(Path(a.input).read_text()))))
    else: serve(a.db,a.port)
