"""Test-only real SQLite implementation of the small D1 contract."""
import json,sqlite3,sys
p=json.load(sys.stdin)
c=sqlite3.connect(p['db']);c.row_factory=sqlite3.Row;c.execute('PRAGMA foreign_keys=ON')
try:
    if 'schema' in p:c.executescript(p['schema']);out=[]
    else:
        c.execute('BEGIN IMMEDIATE');out=[]
        for q in p['queries']:
            cursor=c.execute(q['sql'],q['args']);rows=[dict(r) for r in cursor.fetchall()];out.append({'results':rows,'success':True})
        c.commit()
    print(json.dumps({'result':out}))
except Exception as e:
    c.rollback();print(json.dumps({'error':str(e)}))
finally:c.close()
