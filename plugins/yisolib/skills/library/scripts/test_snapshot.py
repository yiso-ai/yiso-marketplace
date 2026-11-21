import sqlite3,tempfile,unittest
from contextlib import closing
from pathlib import Path
from verify_sqlite_snapshot import compare

class SnapshotTests(unittest.TestCase):
 def test_backup_and_changes(self):
  with tempfile.TemporaryDirectory() as tmp:
   a,b=Path(tmp)/'a.db',Path(tmp)/'b.db'
   with closing(sqlite3.connect(a)) as source:
    source.executescript("CREATE TABLE data(id TEXT PRIMARY KEY,body TEXT);INSERT INTO data VALUES('demo','合成正文');CREATE INDEX data_body ON data(body);")
    source.commit()
    with closing(sqlite3.connect(b)) as target:source.backup(target)
   self.assertTrue(compare(a,b)['equivalent'])
   with closing(sqlite3.connect(b)) as target, target:target.execute("UPDATE data SET body='changed'")
   self.assertEqual(compare(a,b)['different_tables'],['data'])
   with closing(sqlite3.connect(b)) as target, target:target.execute('DROP INDEX data_body')
   self.assertFalse(compare(a,b)['schema_equal'])

if __name__=='__main__':unittest.main()
