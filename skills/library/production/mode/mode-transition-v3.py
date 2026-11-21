"""Reviewed-mode candidate helper; no CLI, no automatic production action."""
import pathlib,os,json,contextlib,uuid
import archiver as a
MAIN=pathlib.Path(r"<workspace>\正式数据库\Company-Profile-local.sqlite")
TEST=pathlib.Path(r"<workspace>\临时隔离验证-历史memo-20261116\same-class-qualification-integration\mode-v2-tests")
def need(v,m):
 if not v:raise RuntimeError(m)
def allowed(p):
 p=pathlib.Path(p);need(p.is_absolute() and '..' not in p.parts,'absolute target')
 need(p==MAIN or (p.parent in [TEST/'normal',TEST/'optimized'] and p.name=='fixture.sqlite'),'fixed mode target allowlist')
 return a.safe(p)
def clean_sidecars(p):
 for ext in ['-wal','-shm','-journal']:
  side=pathlib.Path(str(p)+ext)
  if side.exists():
   a.safe(side);need(side.stat().st_size==0,'nonempty SQLite sidecar: reviewed recovery required')
def snapshot(p):
 p=allowed(p);clean_sidecars(p);d=a.connect(p,True)
 try:
  need(d.execute('pragma journal_mode').fetchone()[0].lower() =='delete','unsupported journal baseline')
  t=a.target_identity(d);c=d.execute('select * from local_archive_control where id=1').fetchone()
  return {'db_sha256':a.sha(p.read_bytes()),'identity':t,'generation':c['generation'],'mode':c['mode'],'pins':json.loads(c['code_json'])}
 finally:d.close()
@contextlib.contextmanager
def locked(p):
 lock=allowed(p).with_suffix('.writer.lock');a.safe(lock,False);fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
 try:yield
 finally:os.close(fd);lock.unlink()
def transition(p,expected,new_mode,event_id,manifest):
 p=allowed(p);need(new_mode in ['enabled','paused'],'mode invalid');need(expected['pins']==manifest(),'live code pins mismatch')
 with locked(p):
  clean_sidecars(p)
  d=a.connect(p)
  try:
   need(d.execute('pragma journal_mode').fetchone()[0].lower() =='delete','unsupported journal baseline')
   d.execute('BEGIN IMMEDIATE')
   identity=a.target_identity(d);row=d.execute('select * from local_archive_control where id=1').fetchone()
   actual={'db_sha256':a.sha(p.read_bytes()),'identity':identity,'generation':row['generation'],'mode':row['mode'],'pins':json.loads(row['code_json'])}
   need(actual==expected and actual['pins']==manifest(),'locked transaction baseline drift')
   need(row['mode']!=new_mode,'transition must change mode')
   target={k:identity[k] for k in ['database_uuid','schema_sha256']}
   d.execute('update local_archive_control set mode=?,generation=generation+1 where id=1',(new_mode,))
   # Same event fields/action/detail structure as original pinned set_mode.
   detail={'previous_mode':row['mode'],'new_mode':new_mode,'target':target}
   d.execute('insert into local_archive_control_event values(?,?,?,?,?)',(event_id,row['generation']+1,'set_mode',a.canonical(detail),a.now()))
   d.commit()
   return snapshot(p)
  finally:d.close()

def recover_committed_enable(p,baseline,event_id,manifest):
 # The enabled transition may commit then fail while taking its return snapshot.
 # Recover only with the pre-persisted unique event and exact owner identity/gen/pins.
 try:
  d=a.connect(allowed(p),True)
  try:row=d.execute('select * from local_archive_control_event where id=?',(event_id,)).fetchone()
  finally:d.close()
  if row is None:return None
  target={k:baseline['identity'][k] for k in ['database_uuid','schema_sha256']}
  need(row['generation']==baseline['generation']+1 and row['action']=='set_mode' and json.loads(row['detail_json'])=={'previous_mode':'paused','new_mode':'enabled','target':target},'enable event ownership mismatch')
  now=snapshot(p)
  need(now['generation']==baseline['generation']+1 and now['mode']=='enabled' and now['identity']==baseline['identity'] and now['pins']==baseline['pins']==manifest(),'committed enable no longer owned')
  return now
 except Exception as ex:raise RuntimeError('blocked: cannot prove post-commit enable ownership; no automatic pause') from ex

@contextlib.contextmanager
def enabled_owned(p,baseline,event_ids,manifest):
 context={'baseline':baseline,'enabled':None,'paused':None,'enable_event_id':event_ids[0],'pause_event_id':event_ids[1]}
 try:
  context['enabled']=transition(p,baseline,'enabled',event_ids[0],manifest)
  yield context
 finally:
  e=context['enabled']
  if e is None:
   e=recover_committed_enable(p,baseline,event_ids[0],manifest)
   context['enabled']=e
  if e is not None:
   observed=snapshot(p)
   owned=observed['generation']==e['generation'] and observed['identity']==e['identity'] and observed['pins']==e['pins']==manifest()
   need(owned and observed['mode']=='enabled','ownership lost: no automatic pause')
   context['paused']=transition(p,observed,'paused',event_ids[1],manifest)
def verify_transition_events(p,baseline,event_ids):
 d=a.connect(allowed(p),True)
 try:
  target={k:baseline['identity'][k] for k in ['database_uuid','schema_sha256']}
  for delta,evt,previous,new in [(1,event_ids[0],'paused','enabled'),(2,event_ids[1],'enabled','paused')]:
   row=d.execute('select * from local_archive_control_event where id=?',(evt,)).fetchone()
   need(row is not None and row['generation']==baseline['generation']+delta and row['action']=='set_mode','owned transition event missing')
   need(json.loads(row['detail_json'])=={'previous_mode':previous,'new_mode':new,'target':target},'owned event detail mismatch')
  observed=snapshot(p)
  need(observed['mode']=='paused' and observed['generation']==baseline['generation']+2 and observed['identity']==baseline['identity'] and observed['pins']==baseline['pins'],'owned paused state mismatch')
  return observed
 finally:d.close()
