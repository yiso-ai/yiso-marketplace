PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS state(id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS subjects(id TEXT PRIMARY KEY,name TEXT NOT NULL,kind TEXT NOT NULL CHECK(kind IN ('company','theme')));
CREATE TABLE IF NOT EXISTS bodies(sha256 TEXT PRIMARY KEY CHECK(length(sha256)=64),body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS memos(id TEXT PRIMARY KEY,title TEXT NOT NULL,category TEXT NOT NULL CHECK(category IN ('internal','sellside','news')),material_date TEXT,institution TEXT,display_author TEXT,author_private TEXT,body_sha256 TEXT NOT NULL REFERENCES bodies(sha256),primary_subject TEXT NOT NULL REFERENCES subjects(id),source_key TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS evidence(id TEXT PRIMARY KEY,memo_id TEXT NOT NULL REFERENCES memos(id),body_sha256 TEXT NOT NULL REFERENCES bodies(sha256),start_offset INTEGER NOT NULL,end_offset INTEGER NOT NULL,quote TEXT NOT NULL,CHECK(start_offset>=0 AND end_offset>start_offset));
CREATE TABLE IF NOT EXISTS assignments(memo_id TEXT NOT NULL REFERENCES memos(id),subject_id TEXT NOT NULL REFERENCES subjects(id),evidence_id TEXT NOT NULL REFERENCES evidence(id),scope TEXT NOT NULL CHECK(scope IN ('primary','substantive')),PRIMARY KEY(memo_id,subject_id));
CREATE UNIQUE INDEX IF NOT EXISTS one_primary ON assignments(memo_id) WHERE scope='primary';
CREATE TABLE IF NOT EXISTS native_tables(id TEXT PRIMARY KEY,memo_id TEXT NOT NULL REFERENCES memos(id),content_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS batches(id TEXT PRIMARY KEY,sha256 TEXT NOT NULL,version INTEGER NOT NULL);
CREATE TRIGGER IF NOT EXISTS immutable_body BEFORE UPDATE ON bodies BEGIN SELECT RAISE(ABORT,'immutable body'); END;
CREATE TABLE IF NOT EXISTS industry_versions(id TEXT PRIMARY KEY,scheme TEXT NOT NULL,version TEXT NOT NULL,UNIQUE(scheme,version));
CREATE TABLE IF NOT EXISTS industries(id TEXT PRIMARY KEY,version_id TEXT NOT NULL REFERENCES industry_versions(id),name TEXT NOT NULL,level INTEGER NOT NULL CHECK(level IN (1,2,3)),parent_id TEXT REFERENCES industries(id),CHECK((level=1 AND parent_id IS NULL) OR (level>1 AND parent_id IS NOT NULL)));
CREATE TABLE IF NOT EXISTS company_industries(company_id TEXT NOT NULL REFERENCES subjects(id),industry_id TEXT NOT NULL REFERENCES industries(id),mapping_kind TEXT NOT NULL CHECK(mapping_kind IN ('official','research_mapping')),PRIMARY KEY(company_id,industry_id));
CREATE TABLE IF NOT EXISTS theme_parents(child_id TEXT PRIMARY KEY REFERENCES subjects(id),parent_id TEXT NOT NULL REFERENCES subjects(id),CHECK(child_id<>parent_id));
CREATE TABLE IF NOT EXISTS theme_aliases(theme_id TEXT NOT NULL REFERENCES subjects(id),alias TEXT NOT NULL,PRIMARY KEY(theme_id,alias));
CREATE TABLE IF NOT EXISTS theme_crosslinks(source_id TEXT NOT NULL REFERENCES subjects(id),target_id TEXT NOT NULL REFERENCES subjects(id),reason TEXT NOT NULL,PRIMARY KEY(source_id,target_id),CHECK(source_id<>target_id));

CREATE TABLE IF NOT EXISTS import_guard(id INTEGER PRIMARY KEY CHECK(id=1),ok INTEGER NOT NULL CHECK(ok=1));
