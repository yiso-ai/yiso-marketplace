"""Compare two local SQLite snapshots read-only; does not certify cloud backups."""
import argparse, hashlib, json, sqlite3
from pathlib import Path

def normalized(value):
    if isinstance(value,bytes):return {'blob_hex':value.hex()}
    return value

def inspect(path):
    db=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True)
    try:
        if db.execute('PRAGMA integrity_check').fetchall()!=[('ok',)]:raise ValueError('integrity check failed')
        if db.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('foreign key check failed')
        schema=db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name").fetchall()
        hashes={}
        for typ,name,_,sql in schema:
            if typ!='table':continue
            identifier='"'+name.replace('"','""')+'"'
            rows=[json.dumps([normalized(v) for v in row],ensure_ascii=False,separators=(',',':'),allow_nan=False) for row in db.execute('SELECT * FROM '+identifier)]
            digest=hashlib.sha256('\n'.join(sorted(rows)).encode()).hexdigest()
            hashes[name]={'rows':len(rows),'sha256':digest}
        return {'schema_sha256':hashlib.sha256(json.dumps(schema,ensure_ascii=False,separators=(',',':')).encode()).hexdigest(),'tables':hashes}
    finally:db.close()

def compare(left,right):
    a,b=inspect(left),inspect(right)
    different=sorted(name for name in set(a['tables'])|set(b['tables']) if a['tables'].get(name)!=b['tables'].get(name))
    return {'equivalent':a==b,'schema_equal':a['schema_sha256']==b['schema_sha256'],'different_tables':different,'table_count':len(a['tables'])}

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('source');p.add_argument('restored');a=p.parse_args()
    try:r=compare(a.source,a.restored);print(json.dumps(r));raise SystemExit(0 if r['equivalent'] else 1)
    except (ValueError,sqlite3.Error,OSError) as error:print(json.dumps({'equivalent':False,'error':str(error)}));raise SystemExit(2)
