"""Explicit, fixed-path local authority. Initialization and enabling are separate actions.
Candidate and production run identical gates. Never alters legacy cloud control tables.
"""
import argparse,contextlib,json,os,pathlib,sqlite3,uuid,sys
import archiver as a
ROOT=pathlib.Path(__file__).resolve().parent
BASE=pathlib.Path('<workspace>')
DATABASE_ROOT=BASE/'正式数据库'
BACKUP_ROOT=BASE/'备份'
PROGRAM_ROOT=BASE/'归档程序与记录'
TEST_ROOT=PROGRAM_ROOT/'测试'
RECEIPT_ROOT=PROGRAM_ROOT/'正式回执'
MAIN=DATABASE_ROOT/'Company-Profile-local.sqlite'
CANDIDATE=TEST_ROOT/'rehearsal-main.sqlite'
FILES=('archiver.py','extensions.py','controlled_writer.py','primary_subject.py','new_entities.py','image_assets.py')
def code_identity():return {n:a.sha((ROOT/n).read_bytes()) for n in FILES}
RELEASE_ROOT=PROGRAM_ROOT/'local_archiver'/'image-production-release-v2'
MIGRATION_MAIN_SHA='b45ce6f316194bca0957fa31b880112ecc9644eff5c8a88b79ab6f074051a9fa'
MIGRATION_IDENTITY={'database_uuid': 'c881b3f0-a2f0-451c-88ec-c003d238d3a2', 'schema_sha256': '3f2b3e777daf5280ad9720062c77f302016e3e792bda495d45c363f36a491478'}
MIGRATION_OLD_CODE={'archiver.py': 'ef8c8baf62018936f5238c0adf4ddcd04d5bf34566d1e7297098319d24499a48', 'controlled_writer.py': '825b3e0f393a8454f3588b1702aa272d4568475f22b9ab09352a273018d4513e', 'extensions.py': 'fb79698bd3d76515d761617a376d463b7cdc5e372c7ed5ff994ceefa7fdaca4e', 'new_entities.py': 'fac0784d03c30995e4d177ce36a881572f89e45bda702574c8caf33ea6bd4e5f', 'primary_subject.py': 'f28e873748edbcddb5fb7738082e8627447b356f2f2c8fc24a9c01d8d5609264'}

def target(kind):
 if ROOT!=RELEASE_ROOT:raise ValueError('release runtime path mismatch')
 if kind!='production':raise ValueError('candidate/test workflows are not restored in this production-only relocation')
 p=MAIN if kind=='production' else CANDIDATE
 a.safe(ROOT);a.safe(DATABASE_ROOT);a.safe(BACKUP_ROOT);a.safe(RECEIPT_ROOT);a.safe(p,p.exists())
 if kind=='candidate' and p.exists() and p.samefile(MAIN):raise ValueError('rehearsal aliases production')
 return p

def backup_dir(kind):
 target(kind)
 return a.safe(BACKUP_ROOT if kind=='production' else TEST_ROOT/'backups')

def receipt_dir(kind):
 target(kind)
 return a.safe(RECEIPT_ROOT if kind=='production' else TEST_ROOT/'receipts')

def scope(kind):
 target(kind)
 return 'local-production-v1'
@contextlib.contextmanager
def writer_lock(p):
 lock=p.with_suffix('.writer.lock');fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
 try:yield
 finally:os.close(fd);lock.unlink()
def baseline(p):
 p=a.safe(p);d=a.connect(p,True)
 try:
  if d.execute('pragma journal_mode').fetchone()[0].lower()=='wal':raise ValueError('initialization/restore baseline requires non-WAL database; reviewed checkpoint needed')
  if d.execute('pragma integrity_check').fetchone()[0]!='ok' or d.execute('pragma foreign_key_check').fetchone():raise ValueError('baseline database invalid')
  return {'path':str(p),'file_sha256':a.sha(p.read_bytes()),'schema_sha256':a.schema_hash(d),'memo_count':d.execute('select count(*) from cp_memo').fetchone()[0]}
 finally:d.close()
def clone_candidate(expected_main_sha256):
 raise ValueError('retired historical entry point; use reviewed migration or joint restore')
 if CANDIDATE.exists():raise ValueError('candidate already exists')
 if a.sha(a.safe(MAIN).read_bytes())!=expected_main_sha256:raise ValueError('main changed before candidate clone')
 src=a.connect(MAIN,True);dst=sqlite3.connect(CANDIDATE)
 try:src.backup(dst)
 finally:src.close();dst.close()
 return baseline(CANDIDATE)
def initialize(kind,expected_file_sha256,expected_schema_sha256):
 raise ValueError('retired historical entry point; use reviewed migration or joint restore')
 p=a.safe(target(kind));expected=scope(kind)
 with writer_lock(p):
  info=baseline(p)
  if info['file_sha256']!=expected_file_sha256 or info['schema_sha256']!=expected_schema_sha256:raise ValueError('initialization baseline mismatch')
  d=a.connect(p);before=d.execute('pragma data_version').fetchone()[0]
  backup=p.with_name('pre-local-authority-'+uuid.uuid4().hex+'.sqlite');out=sqlite3.connect(backup)
  try:d.backup(out)
  finally:out.close()
  try:
   d.execute('BEGIN IMMEDIATE')
   if d.execute('pragma data_version').fetchone()[0]!=before:raise ValueError('external write during initialization backup')
   if a.sha(p.read_bytes())!=expected_file_sha256 or a.schema_hash(d)!=expected_schema_sha256:raise ValueError('initialization baseline changed before transaction lock')
   if d.execute("select 1 from sqlite_master where name like 'local_archive_%'").fetchone():raise ValueError('local identity exists; never overwrite or auto-reset')
   d.execute('create table local_archive_identity(id text primary key,scope text not null)')
   d.execute('insert into local_archive_identity values(?,?)',('main',expected))
   d.execute('create table local_archive_receipt(batch_id text primary key,payload_sha256 text not null,receipt_json text not null)')
   d.execute("create table local_archive_control(id integer primary key check(id=1),mode text not null check(mode in ('paused','enabled')),generation integer not null,code_json text not null,initialization_backup text not null,created_at text not null)")
   d.execute('create table local_archive_control_event(id text primary key,generation integer not null,action text not null,detail_json text not null,created_at text not null)')
   d.execute("insert into local_archive_control values(1,'paused',1,?,?,?)",(a.canonical(code_identity()),str(backup),a.now()))
   # Explicit append-only local policy; does not fake original safety permits.
   for table in ('cp_body','cp_memo','cp_memo_revision','cp_evidence','cp_processing','cp_source_record','cp_memo_source','cp_memo_company','cp_memo_theme','cp_native_table','cp_relation_evidence','cp_relevance','local_archive_identity','local_archive_receipt','local_archive_control_event'):
    for action in ('update','delete'):
     d.execute('create trigger local_guard_'+table+'_'+action+' before '+action+' on '+table+" begin select raise(abort,'local append-only policy: reviewed migration required'); end")
   d.execute("create trigger local_guard_evidence_insert before insert on cp_evidence begin select raise(abort,'local evidence body/quote mismatch') where not exists(select 1 from cp_memo m join cp_body b on b.id=m.body_id where m.id=new.memo_id and b.id=new.body_id and substr(b.body,new.start_offset+1,new.end_offset-new.start_offset)=new.quote); end")
   primary_subject.install(d);new_entities.install_guards(d)
   a.install_identity(d);identity=a.target_identity(d)
   d.execute('insert into local_archive_control_event values(?,?,?,?,?)',(str(uuid.uuid4()),1,'initialized_paused',a.canonical({'target':identity,'baseline':info,'backup':str(backup),'code':code_identity()}),a.now()))
   if d.execute('pragma foreign_key_check').fetchone():raise ValueError('initialization foreign key errors')
   d.commit()
  finally:d.close()
  receipt={'kind':kind,'target':identity,'mode':'paused','generation':1,'backup':str(backup),'baseline':info,'code':code_identity()};a.atomic_json(ROOT/('initialization-'+kind+'.json'),receipt);return receipt

def guard_path(kind,p):
 p=a.safe(p)
 if p!=a.safe(target(kind)):raise ValueError('controlled target path mismatch')
 return p

def check_control(d,kind,expected_identity,generation,enabled=True):
 observed=a.target_identity(d)
 if observed['scope']!=scope(kind) or any(observed[k]!=expected_identity.get(k) for k in ('database_uuid','schema_sha256')):raise ValueError('controlled database identity mismatch')
 c=d.execute('select * from local_archive_control where id=1').fetchone()
 if not c or c['generation']!=generation:raise ValueError('control generation mismatch')
 if json.loads(c['code_json'])!=code_identity():raise ValueError('executable identity changed; review and explicit upgrade required')
 if enabled and c['mode']!='enabled':raise ValueError('local writer paused')
 return c

def set_mode(kind,expected_identity,generation,mode):
 if mode not in ('paused','enabled'):raise ValueError('mode invalid')
 p=a.safe(target(kind))
 with writer_lock(p):
  d=a.connect(p)
  try:
   d.execute('BEGIN IMMEDIATE');c=check_control(d,kind,expected_identity,generation,False)
   if c['mode']==mode:d.rollback();return {'mode':mode,'generation':generation,'target':a.target_identity(d)}
   d.execute('update local_archive_control set mode=?,generation=generation+1 where id=1',(mode,));d.execute('insert into local_archive_control_event values(?,?,?,?,?)',(str(uuid.uuid4()),generation+1,'set_mode',a.canonical({'previous_mode':c['mode'],'new_mode':mode,'target':expected_identity}),a.now()));d.commit();return {'mode':mode,'generation':generation+1,'target':a.target_identity(d)}
  finally:d.close()
def apply(kind,batch,expected_identity,generation,failpoint=None):
 if kind=='production' and failpoint is not None:raise ValueError('production failpoints forbidden')
 if set(expected_identity)!={'database_uuid','schema_sha256'}:raise ValueError('expected target UUID/schema required')
 def gate(d):
  row=check_control(d,kind,expected_identity,generation)
  return {'generation':row['generation'],'mode':row['mode'],'code_identity':json.loads(row['code_json']),'scope':scope(kind)}
 return a._apply_engine(target(kind),batch,expected_identity,lambda p:guard_path(kind,p),gate,failpoint,backup_root=backup_dir(kind),receipt_root=receipt_dir(kind))

def status(kind,recover_receipt=False):
 p=a.safe(target(kind));d=a.connect(p,True)
 try:
  identity=a.target_identity(d);control=dict(d.execute('select * from local_archive_control where id=1').fetchone())
  event=d.execute("select detail_json from local_archive_control_event where action='initialized_paused' and generation=1").fetchone()
  if not event:raise ValueError('initialization event missing')
  initial=json.loads(event[0])
  if not a.receipt_target_valid(d,initial['target']):raise ValueError('initialization identity differs')
 finally:d.close()
 result={'kind':kind,'target':identity,'mode':control['mode'],'generation':control['generation'],'code_matches':json.loads(control['code_json'])==code_identity(),'initialization':initial}
 if recover_receipt:a.atomic_json(ROOT/('initialization-'+kind+'.json'),{'kind':kind,'target':initial['target'],'mode':'paused','generation':1,'backup':initial['backup'],'baseline':initial['baseline'],'code':initial['code'],'recovered_from_database_event':True})
 return result

def restore_copy(kind,backup,expected_backup_sha256,output):
 # Restore drill only. Never replace a live main or candidate implicitly.
 backup=a.safe(backup);out=a.safe(output,False)
 if out.parent!=TEST_ROOT or not out.name.startswith('restore-controlled-') or out.suffix!='.sqlite' or out.exists():raise ValueError('new fixed-directory recovery copy only')
 if a.sha(backup.read_bytes())!=expected_backup_sha256:raise ValueError('recovery backup hash mismatch')
 src=a.connect(backup,True)
 if src.execute("select 1 from sqlite_master where name='cp_original_image_asset'").fetchone() and src.execute('select count(*) from cp_original_image_asset').fetchone()[0]:
  src.close();raise ValueError('image-backed database requires joint bundle restore')
 dst=sqlite3.connect(out)
 try:
  if src.execute('pragma integrity_check').fetchone()[0]!='ok' or src.execute('pragma foreign_key_check').fetchone():raise ValueError('invalid backup')
  src.backup(dst)
 finally:src.close();dst.close()
 return baseline(out)
import primary_subject
REVISION_DDL="""CREATE TABLE local_archive_schema_revision(revision integer primary key check(revision>0),previous_schema_sha256 text not null,schema_sha256 text not null,previous_revision_sha256 text not null,payload_json text not null check(json_valid(payload_json)),revision_sha256 text not null);
CREATE TRIGGER local_schema_revision_no_update BEFORE UPDATE ON local_archive_schema_revision BEGIN SELECT RAISE(ABORT,'schema revisions immutable'); END;
CREATE TRIGGER local_schema_revision_no_delete BEFORE DELETE ON local_archive_schema_revision BEGIN SELECT RAISE(ABORT,'schema revisions immutable'); END;
CREATE TRIGGER local_schema_revision_sequence BEFORE INSERT ON local_archive_schema_revision BEGIN SELECT RAISE(ABORT,'schema revision sequence mismatch') WHERE NEW.revision<>(SELECT coalesce(max(revision),0)+1 FROM local_archive_schema_revision); END;
"""
def execute_ddl(d,script):
 current=''
 for line in script.splitlines(True):
  current+=line
  if sqlite3.complete_statement(current):d.execute(current);current=''
 if current.strip():raise ValueError('incomplete migration SQL')

def migration_payload(d):
 if not d.execute("select 1 from sqlite_master where name='local_archive_schema_revision' and type='table'").fetchone():return None
 row=d.execute('select payload_json from local_archive_schema_revision where revision=1').fetchone()
 return json.loads(row[0]) if row else None

def migrate_primary(kind,expected_identity,generation,old_code,new_code,failpoint=None):
 raise ValueError('retired historical entry point; use reviewed migration or joint restore')
 if kind=='production' and failpoint is not None:raise ValueError('production failpoints forbidden')
 if kind=='production' and failpoint:raise ValueError('fault injection forbidden for production')
 if set(expected_identity)!={'database_uuid','schema_sha256'}:raise ValueError('migration expected identity fields')
 if new_code!=code_identity():raise ValueError('migration new executable manifest mismatch')
 p=a.safe(target(kind))
 with writer_lock(p):
  d=a.connect(p)
  try:
   current=a.target_identity(d);control=d.execute('select * from local_archive_control where id=1').fetchone()
   if current['scope']!=scope(kind) or current['database_uuid']!=expected_identity['database_uuid']:raise ValueError('migration target UUID/scope mismatch')
   existing=migration_payload(d)
   if existing:
    if existing['migration_id']!='explicit-primary-subject-v2' or existing['previous_schema_sha256']!=expected_identity['schema_sha256'] or existing['from_generation']!=generation or existing['old_code']!=old_code or existing['new_code']!=new_code or current['schema_sha256']!=existing['schema_sha256'] or json.loads(control['code_json'])!=new_code:raise ValueError('migration replay contract mismatch')
    result=dict(existing,replayed=True)
   else:
    if current['schema_sha256']!=expected_identity['schema_sha256']:raise ValueError('migration old schema mismatch')
    if control['mode']!='paused':raise ValueError('migration requires paused writer')
    if control['generation']!=generation:raise ValueError('migration generation mismatch')
    if json.loads(control['code_json'])!=old_code:raise ValueError('migration old executable pin mismatch')
    if d.execute("select 1 from sqlite_master where name='local_memo_primary_subject'").fetchone():raise ValueError('untracked primary schema already present')
    before=d.execute('pragma data_version').fetchone()[0];backup=p.with_name('pre-primary-migration-'+uuid.uuid4().hex+'.sqlite');out=sqlite3.connect(backup)
    try:d.backup(out)
    finally:out.close()
    backup_hash=a.sha(backup.read_bytes());d.execute('BEGIN IMMEDIATE')
    locked=a.target_identity(d);locked_control=d.execute('select * from local_archive_control where id=1').fetchone()
    if locked!=current or dict(locked_control)!=dict(control) or d.execute('pragma data_version').fetchone()[0]!=before:raise ValueError('database/control changed during migration backup')
    execute_ddl(d,REVISION_DDL);primary_subject.install(d)
    if failpoint=='after_ddl':raise RuntimeError('injected migration failure after DDL')
    if failpoint=='hard_exit_after_ddl':os._exit(93)
    new_schema=a.schema_hash(d)
    result={'migration_id':'explicit-primary-subject-v2','previous_schema_sha256':current['schema_sha256'],'schema_sha256':new_schema,'target_uuid':current['database_uuid'],'target_scope':current['scope'],'from_generation':generation,'to_generation':generation+1,'old_code':old_code,'new_code':new_code,'backup':str(backup),'backup_sha256':backup_hash,'created_at':a.now()}
    payload=a.canonical(result)
    d.execute('insert into local_archive_schema_revision values(1,?,?,?,?,?)',(current['schema_sha256'],new_schema,'0'*64,payload,a.sha(payload)))
    d.execute("update local_archive_control set code_json=?,generation=? where id=1 and mode='paused'",(a.canonical(new_code),generation+1))
    d.execute('insert into local_archive_control_event values(?,?,?,?,?)',(str(uuid.uuid4()),generation+1,'schema_migration',payload,a.now()))
    if a.target_identity(d)['schema_sha256']!=new_schema or d.execute('pragma foreign_key_check').fetchone():raise ValueError('migration postcondition failed')
    if failpoint=='before_commit':raise RuntimeError('injected migration failure before commit')
    d.commit()
    if failpoint=='after_commit':raise RuntimeError('injected migration committed response lost')
   a.atomic_json(ROOT/('primary-migration-'+kind+'.json'),result);return result
  finally:d.close()


import new_entities

def entity_migration_payload(d):
 row=d.execute('select payload_json from local_archive_schema_revision where revision=2').fetchone()
 return json.loads(row[0]) if row else None

def migrate_entities(kind,expected_identity,generation,old_code,new_code,failpoint=None):
 raise ValueError('retired historical entry point; use reviewed migration or joint restore')
 if kind=='production' and failpoint is not None:raise ValueError('production failpoints forbidden')
 if kind=='production' and failpoint:raise ValueError('fault injection forbidden for production')
 if set(expected_identity)!={'database_uuid','schema_sha256'}:raise ValueError('entity migration expected identity fields')
 if new_code!=code_identity() or len(new_code)!=5 or 'new_entities.py' not in new_code:raise ValueError('entity migration new five-file manifest mismatch')
 p=a.safe(target(kind))
 with writer_lock(p):
  d=a.connect(p)
  try:
   current=a.target_identity(d);control=d.execute('select * from local_archive_control where id=1').fetchone()
   if current['scope']!=scope(kind) or current['database_uuid']!=expected_identity['database_uuid']:raise ValueError('entity migration target UUID/scope mismatch')
   first=d.execute('select payload_json from local_archive_schema_revision where revision=1').fetchone()
   if not first or json.loads(first[0])['migration_id']!='explicit-primary-subject-v2':raise ValueError('entity migration requires reviewed theme revision 1')
   existing=entity_migration_payload(d)
   if existing:
    if existing['migration_id']!='reviewed-named-entities-v1' or existing['previous_schema_sha256']!=expected_identity['schema_sha256'] or existing['from_generation']!=generation or existing['old_code']!=old_code or existing['new_code']!=new_code or current['schema_sha256']!=existing['schema_sha256'] or json.loads(control['code_json'])!=new_code:raise ValueError('entity migration replay contract mismatch')
    result=dict(existing,replayed=True)
   else:
    if current['schema_sha256']!=expected_identity['schema_sha256']:raise ValueError('entity migration old schema mismatch')
    if control['mode']!='paused':raise ValueError('entity migration requires paused writer')
    if control['generation']!=generation:raise ValueError('entity migration generation mismatch')
    if json.loads(control['code_json'])!=old_code or len(old_code)!=4:raise ValueError('entity migration old four-file pin mismatch')
    if d.execute('select max(revision) from local_archive_schema_revision').fetchone()[0]!=1:raise ValueError('entity migration unexpected schema chain')
    if d.execute("select 1 from sqlite_master where type='trigger' and name like 'local_entity_%'").fetchone():raise ValueError('untracked entity guards already present')
    previous=d.execute('select revision_sha256 from local_archive_schema_revision where revision=1').fetchone()[0]
    before=d.execute('pragma data_version').fetchone()[0];backup=p.with_name('pre-entity-migration-'+uuid.uuid4().hex+'.sqlite');out=sqlite3.connect(backup)
    try:d.backup(out)
    finally:out.close()
    backup_hash=a.sha(backup.read_bytes());d.execute('BEGIN IMMEDIATE');locked=a.target_identity(d);locked_control=d.execute('select * from local_archive_control where id=1').fetchone()
    if locked!=current or dict(locked_control)!=dict(control) or d.execute('pragma data_version').fetchone()[0]!=before:raise ValueError('database/control changed during entity migration backup')
    new_entities.install_guards(d)
    if failpoint=='after_ddl':raise RuntimeError('injected entity migration failure after DDL')
    if failpoint=='hard_exit_after_ddl':os._exit(94)
    new_schema=a.schema_hash(d)
    result={'migration_id':'reviewed-named-entities-v1','previous_schema_sha256':current['schema_sha256'],'schema_sha256':new_schema,'target_uuid':current['database_uuid'],'target_scope':current['scope'],'from_generation':generation,'to_generation':generation+1,'old_code':old_code,'new_code':new_code,'backup':str(backup),'backup_sha256':backup_hash,'created_at':a.now()}
    payload=a.canonical(result);d.execute('insert into local_archive_schema_revision values(2,?,?,?,?,?)',(current['schema_sha256'],new_schema,previous,payload,a.sha(payload)))
    d.execute("update local_archive_control set code_json=?,generation=? where id=1 and mode='paused'",(a.canonical(new_code),generation+1));d.execute('insert into local_archive_control_event values(?,?,?,?,?)',(str(uuid.uuid4()),generation+1,'schema_migration',payload,a.now()))
    if a.target_identity(d)['schema_sha256']!=new_schema or d.execute('pragma foreign_key_check').fetchone():raise ValueError('entity migration postcondition failed')
    if failpoint=='before_commit':raise RuntimeError('injected entity migration failure before commit')
    d.commit()
    if failpoint=='after_commit':raise RuntimeError('injected entity migration committed response lost')
   a.atomic_json(ROOT/('entity-migration-'+kind+'.json'),result);return result
  finally:d.close()


import image_assets

def migrate_images(kind,expected_identity,generation,old_code,new_code,failpoint=None):
 raise ValueError('retired historical entry point; use reviewed migration or joint restore')
 if kind=='production' and failpoint is not None:raise ValueError('production failpoints forbidden')
 target(kind)
 if expected_identity!=MIGRATION_IDENTITY or generation!=171 or old_code!=MIGRATION_OLD_CODE:raise ValueError('fixed image release migration baseline mismatch')
 if set(expected_identity)!={'database_uuid','schema_sha256'}:raise ValueError('image migration expected identity')
 if new_code!=code_identity() or len(new_code)!=6 or 'image_assets.py' not in new_code:raise ValueError('image six-file manifest mismatch')
 p=a.safe(target(kind))
 with writer_lock(p):
  d=a.connect(p)
  try:
   current=a.target_identity(d);control=d.execute('select * from local_archive_control where id=1').fetchone()
   if current['database_uuid']!=expected_identity['database_uuid'] or current['scope']!=scope(kind):raise ValueError('image migration UUID/scope mismatch')
   old=d.execute('select payload_json from local_archive_schema_revision where revision=3').fetchone()
   if old:
    payload=json.loads(old[0])
    if payload['migration_id']!='original-images-v1' or payload['from_generation']!=generation or payload['previous_schema_sha256']!=expected_identity['schema_sha256'] or payload['old_code']!=old_code or payload['new_code']!=new_code or payload['schema_sha256']!=current['schema_sha256']:raise ValueError('image migration replay mismatch')
    if control['mode']!='paused' or json.loads(control['code_json'])!=new_code:raise ValueError('image migration replay state mismatch')
    result=dict(payload,replayed=True)
   else:
    if a.sha(p.read_bytes())!=MIGRATION_MAIN_SHA:raise ValueError('fixed image migration file SHA mismatch')
    if current['schema_sha256']!=expected_identity['schema_sha256'] or control['mode']!='paused' or control['generation']!=generation or json.loads(control['code_json'])!=old_code or len(old_code)!=5:raise ValueError('image migration old control mismatch')
    if d.execute('select max(revision) from local_archive_schema_revision').fetchone()[0]!=2:raise ValueError('image migration requires revision 2')
    if d.execute("select 1 from sqlite_master where name='cp_original_image_asset'").fetchone():raise ValueError('untracked images schema')
    before=d.execute('pragma data_version').fetchone()[0];backup=p.with_name('pre-images-migration-'+uuid.uuid4().hex+'.sqlite');out=sqlite3.connect(backup)
    try:d.backup(out)
    finally:out.close()
    backup_sha=a.sha(backup.read_bytes());d.execute('BEGIN IMMEDIATE')
    if a.target_identity(d)!=current or dict(d.execute('select * from local_archive_control where id=1').fetchone())!=dict(control) or d.execute('pragma data_version').fetchone()[0]!=before:raise ValueError('image migration state changed during backup')
    previous=d.execute('select revision_sha256 from local_archive_schema_revision where revision=2').fetchone()[0]
    if a.sha(p.read_bytes())!=MIGRATION_MAIN_SHA:raise ValueError('migration file changed before locked DDL')
    image_assets.install(d)
    if failpoint=='after_ddl':raise RuntimeError('image migration injected after DDL')
    if failpoint=='hard_exit_after_ddl':os._exit(95)
    new_schema=a.schema_hash(d);result={'migration_id':'original-images-v1','previous_schema_sha256':current['schema_sha256'],'schema_sha256':new_schema,'target_uuid':current['database_uuid'],'target_scope':current['scope'],'from_generation':generation,'to_generation':generation+1,'old_code':old_code,'new_code':new_code,'backup':str(backup),'backup_sha256':backup_sha,'created_at':a.now()}
    payload=a.canonical(result);d.execute('insert into local_archive_schema_revision values(3,?,?,?,?,?)',(current['schema_sha256'],new_schema,previous,payload,a.sha(payload)))
    d.execute("update local_archive_control set code_json=?,generation=? where id=1 and mode='paused'",(a.canonical(new_code),generation+1));d.execute('insert into local_archive_control_event values(?,?,?,?,?)',(str(uuid.uuid4()),generation+1,'schema_migration',payload,a.now()))
    if a.target_identity(d)['schema_sha256']!=new_schema or d.execute('pragma foreign_key_check').fetchone():raise ValueError('image migration postcondition failed')
    if failpoint=='before_commit':raise RuntimeError('image migration injected before commit')
    d.commit()
    if failpoint=='after_commit':raise RuntimeError('image migration committed response lost')
   a.atomic_json(ROOT/('image-migration-'+kind+'.json'),result);return result
  finally:d.close()


def migrate_vectors(kind,expected_identity,generation,expected_file_sha256,old_code,new_code,failpoint=None):
 raise ValueError("EMF-only migration retired in combined proposal; use reviewed migrate_image_formats")
 """Explicit reviewed migration only; never invoked implicitly by apply."""
 if kind=='production' and failpoint is not None:raise ValueError('production failpoints forbidden')
 if set(expected_identity)!={'database_uuid','schema_sha256'} or new_code!=code_identity() or set(old_code)!=set(FILES):raise ValueError('vector migration code/identity contract')
 p=a.safe(target(kind))
 with writer_lock(p):
  d=a.connect(p)
  try:
   current=a.target_identity(d);control=d.execute('select * from local_archive_control where id=1').fetchone()
   if current['database_uuid']!=expected_identity['database_uuid'] or current['scope']!=scope(kind):raise ValueError('vector migration target mismatch')
   prior=[json.loads(r[0]) for r in d.execute('select payload_json from local_archive_schema_revision') if json.loads(r[0]).get('migration_id')=='vector-originals-derived-v2']
   if prior:
    if len(prior)!=1:raise ValueError('multiple vector migrations')
    result=prior[0]
    if any(result[k]!=v for k,v in dict(previous_schema_sha256=expected_identity['schema_sha256'],from_generation=generation,old_code=old_code,new_code=new_code,previous_file_sha256=expected_file_sha256).items()) or control['mode']!='paused' or control['generation']!=generation+1 or json.loads(control['code_json'])!=new_code or current['schema_sha256']!=result['schema_sha256']:raise ValueError('vector migration replay mismatch')
    image_assets.vector_state(d,True);image_assets.verify_all(d,image_assets.store_root(d));return dict(result,replayed=True)
   if current['schema_sha256']!=expected_identity['schema_sha256'] or control['mode']!='paused' or control['generation']!=generation or json.loads(control['code_json'])!=old_code or a.sha(p.read_bytes())!=expected_file_sha256:raise ValueError('vector migration stale baseline')
   if image_assets.vector_state(d):raise ValueError('untracked vector schema')
   if d.execute('pragma journal_mode').fetchone()[0].lower()=='wal':raise ValueError('vector migration requires reviewed non-WAL checkpoint')
   before=d.execute('pragma data_version').fetchone()[0]
   out=backup_dir(kind)/('pre-vector-migration-'+uuid.uuid4().hex+'.bundle')
   proof=image_assets.bundle(p,image_assets.store_root(d),out)
   d.execute('BEGIN IMMEDIATE')
   if a.target_identity(d)!=current or dict(d.execute('select * from local_archive_control where id=1').fetchone())!=dict(control) or d.execute('pragma data_version').fetchone()[0]!=before or a.sha(p.read_bytes())!=expected_file_sha256:raise ValueError('vector migration baseline changed during backup')
   priorrow=d.execute('select revision,revision_sha256 from local_archive_schema_revision order by revision desc limit 1').fetchone()
   revision=priorrow[0]+1 if priorrow else 1;previous=priorrow[1] if priorrow else '0'*64
   image_assets.install_vectors(d)
   if failpoint=='after_ddl':raise RuntimeError('vector migration after DDL')
   result=dict(migration_id='vector-originals-derived-v2',previous_schema_sha256=current['schema_sha256'],schema_sha256=a.schema_hash(d),target_uuid=current['database_uuid'],target_scope=current['scope'],from_generation=generation,to_generation=generation+1,old_code=old_code,new_code=new_code,previous_file_sha256=expected_file_sha256,backup=str(out),backup_manifest_sha256=proof['manifest_sha256'],created_at=a.now())
   payload=a.canonical(result)
   d.execute('insert into local_archive_schema_revision values(?,?,?,?,?,?)',(revision,current['schema_sha256'],result['schema_sha256'],previous,payload,a.sha(payload)))
   d.execute("update local_archive_control set code_json=?,generation=? where id=1 and mode='paused'",(a.canonical(new_code),generation+1))
   d.execute('insert into local_archive_control_event values(?,?,?,?,?)',(str(uuid.uuid4()),generation+1,'schema_migration',payload,a.now()))
   image_assets.verify_all(d,image_assets.store_root(d))
   for row in d.execute('select receipt_json from local_archive_receipt'):a.verify_receipt(d,json.loads(row[0]))
   if a.target_identity(d)['schema_sha256']!=result['schema_sha256'] or d.execute('pragma foreign_key_check').fetchone():raise ValueError('vector migration postcondition')
   if failpoint=='before_commit':raise RuntimeError('vector migration before commit')
   d.commit()
   if failpoint=='after_commit':raise RuntimeError('vector migration committed response lost')
   return result
  finally:d.close()



def migrate_image_formats(kind,expected_identity,generation,expected_file_sha256,old_code,new_code,failpoint=None):
 """One explicitly reviewed paused migration: vector schema + original PNG CHECK."""
 if kind=='production' and failpoint is not None:raise ValueError('production failpoints forbidden')
 if set(expected_identity)!={'database_uuid','schema_sha256'} or new_code!=code_identity() or set(old_code)!=set(FILES):raise ValueError('combined migration code/identity contract')
 p=a.safe(target(kind))
 with writer_lock(p):
  d=a.connect(p)
  try:
   current=a.target_identity(d);control=d.execute('select * from local_archive_control where id=1').fetchone()
   if current['database_uuid']!=expected_identity['database_uuid'] or current['scope']!=scope(kind):raise ValueError('combined migration target mismatch')
   prior=[json.loads(r[0]) for r in d.execute('select payload_json from local_archive_schema_revision') if json.loads(r[0]).get('migration_id')=='original-image-formats-v3']
   if prior:
    if len(prior)!=1:raise ValueError('multiple combined migrations')
    result=prior[0]
    if any(result[k]!=v for k,v in dict(previous_schema_sha256=expected_identity['schema_sha256'],from_generation=generation,old_code=old_code,new_code=new_code,previous_file_sha256=expected_file_sha256).items()) or control['mode']!='paused' or control['generation']!=generation+1 or json.loads(control['code_json'])!=new_code or current['schema_sha256']!=result['schema_sha256']:raise ValueError('combined migration replay mismatch')
    image_assets.vector_state(d,True)
    if image_assets.original_png_schema_state(d)!='original-png-metadata-v2':raise ValueError('combined replay PNG schema')
    image_assets.verify_all(d,image_assets.store_root(d));return dict(result,replayed=True)
   if current['schema_sha256']!=expected_identity['schema_sha256'] or control['mode']!='paused' or control['generation']!=generation or json.loads(control['code_json'])!=old_code or a.sha(p.read_bytes())!=expected_file_sha256:raise ValueError('combined migration stale baseline')
   has_vectors=image_assets.vector_state(d)
   if image_assets.original_png_schema_state(d)!='legacy':raise ValueError('untracked PNG schema already upgraded')
   if d.execute('pragma journal_mode').fetchone()[0].lower()=='wal':raise ValueError('combined migration requires reviewed non-WAL checkpoint')
   before=d.execute('pragma data_version').fetchone()[0]
   out=backup_dir(kind)/('pre-image-formats-migration-'+uuid.uuid4().hex+'.bundle')
   proof=image_assets.bundle(p,image_assets.store_root(d),out)
   # SQLite generalized CHECK rebuild requires this on a private connection BEFORE BEGIN.
   d.execute('pragma foreign_keys=off')
   if d.execute('pragma foreign_keys').fetchone()[0]!=0:raise ValueError('combined migration FK-off not effective')
   d.execute('BEGIN IMMEDIATE')
   if a.target_identity(d)!=current or dict(d.execute('select * from local_archive_control where id=1').fetchone())!=dict(control) or d.execute('pragma data_version').fetchone()[0]!=before or a.sha(p.read_bytes())!=expected_file_sha256:raise ValueError('combined migration baseline changed during backup')
   priorrow=d.execute('select revision,revision_sha256 from local_archive_schema_revision order by revision desc limit 1').fetchone()
   revision=priorrow[0]+1 if priorrow else 1;previous=priorrow[1] if priorrow else '0'*64
   if not has_vectors:image_assets.install_vectors(d)
   if failpoint=='after_vectors':raise RuntimeError('combined migration after_vectors')
   image_assets.install_original_png_metadata(d,failpoint)
   result=dict(migration_id='original-image-formats-v3',previous_schema_sha256=current['schema_sha256'],schema_sha256=a.schema_hash(d),target_uuid=current['database_uuid'],target_scope=current['scope'],from_generation=generation,to_generation=generation+1,old_code=old_code,new_code=new_code,previous_file_sha256=expected_file_sha256,had_vectors=has_vectors,backup=str(out),backup_manifest_sha256=proof['manifest_sha256'],created_at=a.now())
   payload=a.canonical(result)
   d.execute('insert into local_archive_schema_revision values(?,?,?,?,?,?)',(revision,current['schema_sha256'],result['schema_sha256'],previous,payload,a.sha(payload)))
   d.execute("update local_archive_control set code_json=?,generation=? where id=1 and mode='paused'",(a.canonical(new_code),generation+1))
   d.execute('insert into local_archive_control_event values(?,?,?,?,?)',(str(uuid.uuid4()),generation+1,'schema_migration',payload,a.now()))
   image_assets.verify_all(d,image_assets.store_root(d))
   for row in d.execute('select receipt_json from local_archive_receipt'):a.verify_receipt(d,json.loads(row[0]))
   if a.target_identity(d)['schema_sha256']!=result['schema_sha256'] or d.execute('pragma foreign_key_check').fetchone():raise ValueError('combined migration postcondition')
   if failpoint=='before_commit':raise RuntimeError('combined migration before_commit')
   d.commit()
   if failpoint=='after_commit':raise RuntimeError('combined migration committed response lost')
   return result
  finally:
   # Rollback DDL and row changes on every precommit failure; FK mode cannot change inside a transaction.
   try:
    if d.in_transaction:d.rollback()
    d.execute('pragma foreign_keys=on')
    if d.execute('pragma foreign_keys').fetchone()[0]!=1:raise ValueError('combined migration FK restoration failed')
   finally:d.close()


def main():
 ap=argparse.ArgumentParser();ap.add_argument('command',choices=['baseline','clone-candidate','initialize','set-mode','apply-stdin','status','recover-initialization-receipt','migrate-primary-stdin','migrate-entities-stdin']);ap.add_argument('--kind',choices=['candidate','production'],required=True);ap.add_argument('--expected-file-sha256');ap.add_argument('--expected-schema-sha256');ap.add_argument('--expected-database-uuid');ap.add_argument('--generation',type=int);ap.add_argument('--mode',choices=['paused','enabled']);x=ap.parse_args()
 identity={'database_uuid':x.expected_database_uuid,'schema_sha256':x.expected_schema_sha256}
 if x.command=='status':r=status(x.kind)
 elif x.command=='recover-initialization-receipt':r=status(x.kind,True)
 elif x.command in ('migrate-primary-stdin','migrate-entities-stdin'):
  raw=sys.stdin.buffer.read(1024*1024+1)
  if len(raw)>1024*1024:raise ValueError('migration envelope too large')
  e=json.loads(raw.decode('ascii'))
  if set(e)!={'expected_target','expected_control_generation','old_code','new_code'}:raise ValueError('migration envelope fields')
  r=(migrate_primary if x.command=='migrate-primary-stdin' else migrate_entities)(x.kind,e['expected_target'],e['expected_control_generation'],e['old_code'],e['new_code'])
 elif x.command=='baseline':r=baseline(target(x.kind))
 elif x.command=='clone-candidate':
  if x.kind!='candidate':raise ValueError('clone is candidate-only')
  r=clone_candidate(x.expected_file_sha256)
 elif x.command=='initialize':r=initialize(x.kind,x.expected_file_sha256,x.expected_schema_sha256)
 elif x.command=='set-mode':r=set_mode(x.kind,identity,x.generation,x.mode)
 else:
  raw=sys.stdin.buffer.read(2*1024*1024+1)
  if len(raw)>2*1024*1024:raise ValueError('stdin too large')
  e=json.loads(raw.decode('ascii'))
  if set(e)!={'batch','expected_sha256','expected_target','expected_control_generation'}:raise ValueError('controlled envelope keys')
  if a.sha(a.canonical(e['batch']))!=e['expected_sha256']:raise ValueError('controlled payload hash mismatch')
  r=apply(x.kind,e['batch'],e['expected_target'],e['expected_control_generation'])
 print(json.dumps(r,ensure_ascii=True,sort_keys=True))
if __name__=='__main__':main()
