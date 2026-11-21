"""ISOLATED COMPONENT PROTOTYPE. Not imported by production writer.
Raw PNG RGB/RGBA/grayscale 8-bit noninterlaced only. No conversion, OCR, network.
Caller must hold existing single-writer lock and SQL transaction when binding.
Filesystem crash model: durable bytes before DB reference; orphan bytes are allowed,
never deleted automatically. This is not an atomic filesystem+SQLite transaction.
"""
import base64,datetime,hashlib,io,json,os,pathlib,posixpath,re,sqlite3,stat,struct,subprocess,time,uuid,zipfile,zlib
from xml.etree import ElementTree as ET
MAX_BYTES=16*1024*1024
MAX_PIXELS=16_000_000
SCHEMA="CREATE TABLE cp_original_image_asset(\n image_sha256 TEXT PRIMARY KEY CHECK(length(image_sha256)=64),\n relative_path TEXT NOT NULL UNIQUE,\n mime_type TEXT NOT NULL CHECK(mime_type IN ('image/png','image/jpeg')),\n size_bytes INTEGER NOT NULL CHECK(size_bytes>0),\n width_pixels INTEGER NOT NULL CHECK(width_pixels>0),\n height_pixels INTEGER NOT NULL CHECK(height_pixels>0),\n validator TEXT NOT NULL CHECK(validator IN ('png-subset-v1','jpeg-baseline-gdi-v1')));\nCREATE TABLE cp_memo_original_image(\n id TEXT PRIMARY KEY,\n memo_id TEXT NOT NULL REFERENCES cp_memo(id),\n body_id TEXT NOT NULL REFERENCES cp_body(id),\n source_id TEXT NOT NULL REFERENCES cp_source_record(id),\n image_sha256 TEXT NOT NULL REFERENCES cp_original_image_asset(image_sha256),\n source_sha256 TEXT NOT NULL,\n private_locator TEXT NOT NULL,\n role TEXT NOT NULL CHECK(role IN ('standalone_chart','standalone_image_table')),\n classification_reason TEXT NOT NULL,\n reviewed_by TEXT NOT NULL,\n reviewed_at TEXT NOT NULL,\n plan_sha256 TEXT NOT NULL CHECK(length(plan_sha256)=64),\n UNIQUE(memo_id,source_id,private_locator));\nCREATE TRIGGER original_image_current_binding BEFORE INSERT ON cp_memo_original_image BEGIN\n SELECT RAISE(ABORT,'image current source/body mismatch') WHERE NOT EXISTS(\n SELECT 1 FROM cp_memo m JOIN cp_memo_source ms ON ms.memo_id=m.id\n JOIN cp_source_record s ON s.id=ms.source_id\n WHERE m.id=NEW.memo_id AND m.body_id=NEW.body_id AND s.id=NEW.source_id\n AND s.content_hash=NEW.source_sha256);\nEND;\n"

def canon(x):return json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def sha(x):return hashlib.sha256(x).hexdigest()
def safe(p):
 p=pathlib.Path(p).absolute()
 for x in (p,*p.parents):
  if not x.exists():raise ValueError('missing safe path')
  st=x.lstat()
  if stat.S_ISLNK(st.st_mode) or getattr(st,'st_file_attributes',0)&1024:raise ValueError('reparse path forbidden')
 return p.resolve()

def fs_path(p):
 # Extended-length filesystem path only; never pass this representation to SQLite URI.
 p=safe(p);prefix=chr(92)*2+'?'+chr(92)
 if os.name=='nt' and not str(p).startswith(prefix):
  if not re.match(r'^[A-Za-z]:',str(p)):raise ValueError('local drive required')
  return pathlib.Path(prefix+str(p))
 return p

def install(d):
 # Executes one complete SQL statement at a time without executescript implicit COMMIT.
 pending=''
 for line in SCHEMA.splitlines(True):
  pending+=line
  if sqlite3.complete_statement(pending):d.execute(pending);pending=''
 if pending.strip():raise ValueError('incomplete schema')
 for t in ('cp_original_image_asset','cp_memo_original_image','cp_original_image_search','cp_independent_image_record'):
  for action in ('update','delete'):
   d.execute('CREATE TRIGGER guard_'+t+'_'+action+' BEFORE '+action+' ON '+t+" BEGIN SELECT RAISE(ABORT,'images append-only'); END")
def png(raw):
 if not 0<len(raw)<=MAX_BYTES or raw[:8]!=b'\x89PNG\r\n\x1a\n':raise ValueError('PNG signature/size')
 pos=8;chunks=[];data=[];ended=False;seen_data=False;after_data=False
 while pos<len(raw):
  if pos+12>len(raw):raise ValueError('PNG truncated')
  size=struct.unpack('>I',raw[pos:pos+4])[0];typ=raw[pos+4:pos+8];end=pos+12+size
  if end>len(raw) or size>MAX_BYTES:raise ValueError('PNG chunk length')
  content=raw[pos+8:pos+8+size];crc=struct.unpack('>I',raw[pos+8+size:end])[0]
  if zlib.crc32(typ+content)&0xffffffff!=crc:raise ValueError('PNG CRC')
  if typ not in (b'IHDR',b'sRGB',b'IDAT',b'IEND'):raise ValueError('PNG unsupported chunk; preserve original pending validator expansion')
  if not chunks and typ!=b'IHDR':raise ValueError('PNG missing first header')
  if typ==b'IHDR':
   if chunks or size!=13:raise ValueError('PNG header')
   w,h,depth,color,comp,filt,inter=struct.unpack('>IIBBBBB',content)
   if not w or not h or w*h>MAX_PIXELS or depth!=8 or color not in (0,2,6) or (comp,filt,inter)!=(0,0,0):raise ValueError('PNG unsupported pixel layout')
  elif typ==b'sRGB':
   if b'sRGB' in chunks or seen_data or size!=1 or content[0]>3:raise ValueError('PNG sRGB intent/order/length')
  elif typ==b'IDAT':
   if after_data:raise ValueError('PNG IDAT order')
   data.append(content);seen_data=True
  else:
   if size or not seen_data or end!=len(raw):raise ValueError('PNG end/trailing bytes')
   ended=True;after_data=True
  chunks.append(typ);pos=end
 if not ended:raise ValueError('PNG incomplete')
 channels={0:1,2:3,6:4}[color];stride=1+w*channels;expected=stride*h
 decoder=zlib.decompressobj()
 try:pixels=decoder.decompress(b''.join(data),expected+1)
 except zlib.error as e:raise ValueError('PNG compressed stream invalid') from e
 if len(pixels)!=expected or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:raise ValueError('PNG compressed image size')
 if any(pixels[y*stride]>4 for y in range(h)):raise ValueError('PNG invalid filter')
 return dict(mime_type='image/png',size_bytes=len(raw),width_pixels=w,height_pixels=h,validator='png-subset-v1')
def relative(digest,mime="image/png"):
 if not re.fullmatch('[a-f0-9]{64}',digest):raise ValueError('invalid SHA')
 if mime not in ('image/png','image/jpeg'):raise ValueError('unsupported MIME')
 return 'objects/'+digest[:2]+'/'+digest+('.png' if mime=='image/png' else '.jpg')
def location(root,digest,mime="image/png"):
 root=fs_path(root);rel=relative(digest,mime);p=root.joinpath(*rel.split('/'))
 # No caller-supplied paths/extensions; reject reparse points in every parent.
 if p.exists():safe(p)
 for part in (root/'objects',root/'objects'/digest[:2]):
  if not part.exists():part.mkdir()
  safe(part)
 return p

def prepare(root,raw,expected_sha256):
 info=image_info(raw);digest=sha(raw)
 if digest!=expected_sha256:raise ValueError('image SHA mismatch')
 dest=location(root,digest,info["mime_type"])
 if dest.exists():
  if dest.read_bytes()!=raw:raise ValueError('existing asset corrupt')
 else:
  tmp=dest.with_name(dest.name+'.'+uuid.uuid4().hex+'.tmp')
  with tmp.open('xb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
  # On Windows rename refuses an existing destination. No replace/overwrite.
  tmp.rename(dest)
  if safe(dest).read_bytes()!=raw:raise ValueError('installed bytes differ')
 return dict(info,image_sha256=digest,relative_path=relative(digest,info["mime_type"]))
def verify_asset(root,row):
 if row['relative_path']!=relative(row['image_sha256'],row['mime_type']):raise ValueError('asset path differs')
 p=fs_path(root).joinpath(*row['relative_path'].split('/'));p=safe(p)
 if not 0<p.stat().st_size<=MAX_BYTES:raise ValueError('stored image byte limit')
 raw=p.read_bytes()
 if sha(raw)!=row['image_sha256']:raise ValueError('stored image SHA mismatch')
 info=image_info(raw)
 if any(row[k]!=v for k,v in info.items()):raise ValueError('stored image metadata mismatch')
 return raw


PLAN_KEYS={'reviewed_body_sha256','research_json','memo_id','body_id','source_id','source_sha256','private_locator','role','classification_reason','reviewed_by','reviewed_at'}

def validate_plan(plan):
 if not isinstance(plan.get('reviewed_body_sha256'),str) or not re.fullmatch('[a-f0-9]{64}',plan['reviewed_body_sha256']):raise ValueError('reviewed body SHA required')
 if set(plan)!=PLAN_KEYS or any(not isinstance(v,str) or not v.strip() or '\x00' in v for v in plan.values()):raise ValueError('image binding fields')
 if any(len(plan[k])>512 for k in ('memo_id','body_id','source_id','reviewed_by')) or len(plan['classification_reason'])>4096:raise ValueError('image review metadata limit')
 if plan['role'] not in ('standalone_chart','standalone_image_table'):raise ValueError('report images must not enter original-image store')
 if not re.fullmatch('[a-f0-9]{64}',plan['source_sha256']):raise ValueError('source SHA invalid')
 try:dt=datetime.datetime.fromisoformat(plan['reviewed_at'])
 except ValueError as e:raise ValueError('review time invalid') from e
 if dt.tzinfo is None:raise ValueError('review time timezone required')
 if len(plan['private_locator'])>8192:raise ValueError('locator length')
 try:locator=json.loads(plan['private_locator'])
 except ValueError as e:raise ValueError('locator JSON invalid') from e
 if not isinstance(locator,dict) or set(locator)!={'source_sha256','origin_kind','location'}:raise ValueError('locator fields')
 if locator['source_sha256']!=plan['source_sha256']:raise ValueError('locator source SHA differs')
 if locator['origin_kind'] not in ('standalone_file','docx_media','pdf_embedded_image'):raise ValueError('locator origin invalid')
 loc=locator['location']
 if not isinstance(loc,str) or not loc.strip() or '\x00' in loc or len(loc)>4096:raise ValueError('locator location invalid')
 if plan['private_locator']!=canon(locator):raise ValueError('locator must be canonical JSON')
 validate_research(json.loads(plan['research_json']))
 if plan['research_json']!=canon(json.loads(plan['research_json'])):raise ValueError('research JSON canonical required')
 # Locator is a review claim, not proof of extraction from an original container.
 return plan

def binding_id(plan):
 # Role deliberately excluded: one reviewed interpretation per memo/source/image location.
 return 'image-link-'+sha(canon({k:plan[k] for k in ('memo_id','source_id','private_locator')}).encode())
def plan_hash(plan,digest):return sha(canon(dict(plan,image_sha256=digest)).encode())
def current_binding(d,plan):
 row=d.execute('select m.body_id,s.content_hash from cp_memo m join cp_memo_source ms on ms.memo_id=m.id join cp_source_record s on s.id=ms.source_id where m.id=? and s.id=?',(plan['memo_id'],plan['source_id'])).fetchone()
 if row is None or tuple(row)!=(plan['body_id'],plan['source_sha256']):raise ValueError('current image provenance mismatch')

def bind(d,root,descriptor,plan):
 if not d.in_transaction:raise ValueError('caller transaction required')
 validate_plan(plan);current_binding(d,plan);validate_research_db(d,plan) # Also applies before any replay return.
 if set(descriptor)!={'image_sha256','relative_path','mime_type','size_bytes','width_pixels','height_pixels','validator'}:raise ValueError('asset descriptor fields')
 verify_asset(root,descriptor)
 columns=('image_sha256','relative_path','mime_type','size_bytes','width_pixels','height_pixels','validator')
 old=d.execute('select * from cp_original_image_asset where image_sha256=?',(descriptor['image_sha256'],)).fetchone()
 if old:
  if any(old[k]!=descriptor[k] for k in columns):raise ValueError('asset metadata conflict')
 else:d.execute('insert into cp_original_image_asset values(?,?,?,?,?,?,?)',tuple(descriptor[k] for k in columns))
 bid=binding_id(plan)
 values=dict(plan,id=bid,image_sha256=descriptor['image_sha256'],plan_sha256=plan_hash(plan,descriptor['image_sha256']))
 cols=('id','memo_id','body_id','source_id','image_sha256','source_sha256','private_locator','role','classification_reason','reviewed_by','reviewed_at','research_json','reviewed_body_sha256','plan_sha256')
 old=d.execute('select * from cp_memo_original_image where memo_id=? and source_id=? and private_locator=?',(plan['memo_id'],plan['source_id'],plan['private_locator'])).fetchone()
 if old:
  if any(old[k]!=values[k] for k in cols):raise ValueError('binding replay conflict')
  verify_search(d,bid,plan);return bid
 d.execute('insert into cp_memo_original_image values('+','.join('?' for _ in cols)+')',tuple(values[k] for k in cols));store_search(d,bid,plan);return bid

def verify_all(d,root):
 for row in d.execute('select * from cp_original_image_asset'):verify_asset(root,row)
 seen=set()
 for row in d.execute('select * from cp_memo_original_image'):
  plan={k:row[k] for k in PLAN_KEYS};validate_plan(plan);current_binding(d,plan);validate_research_db(d,plan);verify_search(d,row['id'],plan)
  if row['id']!=binding_id(plan):raise ValueError('image deterministic link id mismatch')
  if row['plan_sha256']!=plan_hash(plan,row['image_sha256']):raise ValueError('image classification/review checksum mismatch')
  identity=(plan['memo_id'],plan['source_id'],plan['private_locator'])
  if identity in seen:raise ValueError('multiple classifications at image location')
  seen.add(identity)
 verify_image_record_rows(d)
 if d.execute('pragma foreign_key_check').fetchone():raise ValueError('image foreign key mismatch')
 return {'assets':d.execute('select count(*) from cp_original_image_asset').fetchone()[0],'links':d.execute('select count(*) from cp_memo_original_image').fetchone()[0]}


def bundle(db_path,root,output):
 """Isolated proof: snapshot DB, then every referenced immutable asset. Original untouched."""
 output=pathlib.Path(output)
 if output.exists():raise ValueError('bundle destination exists')
 safe(output.parent);output.mkdir();snapshot=output/'database.sqlite'
 src=sqlite3.connect(safe(db_path).as_uri()+'?mode=ro',uri=True);dst=sqlite3.connect(snapshot)
 try:src.backup(dst)
 finally:src.close();dst.close()
 d=sqlite3.connect(snapshot);d.row_factory=sqlite3.Row;d.execute('pragma foreign_keys=on')
 try:
  verify_all(d,root);assets=[];image_out=output/'images';image_out.mkdir()
  for row in d.execute('select * from cp_original_image_asset order by image_sha256'):
   raw=verify_asset(root,row);desc=prepare(image_out,raw,row['image_sha256']);assets.append(desc)
  verify_all(d,image_out)
 finally:d.close()
 manifest={'protocol':'isolated-original-image-bundle-v1','database_sha256':sha(snapshot.read_bytes()),'assets':assets}
 raw=canon(manifest).encode();(output/'manifest.json').write_bytes(raw)
 return {'manifest_sha256':sha(raw),'assets':len(assets)}

def restore_bundle(bundle_root,expected_manifest_sha256,output):
 bundle_root=safe(bundle_root);manifest_raw=safe(bundle_root/'manifest.json').read_bytes()
 if sha(manifest_raw)!=expected_manifest_sha256:raise ValueError('bundle manifest SHA mismatch')
 m=json.loads(manifest_raw)
 if set(m)!={'protocol','database_sha256','assets'} or m['protocol']!='isolated-original-image-bundle-v1':raise ValueError('bundle manifest shape')
 db=safe(bundle_root/'database.sqlite')
 if sha(db.read_bytes())!=m['database_sha256']:raise ValueError('bundle database SHA mismatch')
 d=sqlite3.connect(db.as_uri()+'?mode=ro',uri=True);d.row_factory=sqlite3.Row;d.execute('pragma foreign_keys=on')
 try:
  verify_all(d,bundle_root/'images')
  actual=[dict(x) for x in d.execute('select * from cp_original_image_asset order by image_sha256')]
  if actual!=m['assets']:raise ValueError('bundle asset manifest differs')
  for row in actual:verify_asset(bundle_root/'images',row)
 finally:d.close()
 output=pathlib.Path(output)
 if output.exists():raise ValueError('restore destination exists')
 safe(output.parent);output.mkdir();(output/'images').mkdir()
 for row in actual:prepare(output/'images',verify_asset(bundle_root/'images',row),row['image_sha256'])
 # Copy bytes only after full bundle validation; never replace any existing database.
 with (output/'database.sqlite').open('xb') as f:f.write(db.read_bytes());f.flush();os.fsync(f.fileno())
 return {'database_sha256':sha((output/'database.sqlite').read_bytes()),'assets':len(actual)}

JPEG_DECODER_PS="$ErrorActionPreference='Stop';Add-Type -AssemblyName System.Drawing\n$raw=[Convert]::FromBase64String([Console]::In.ReadToEnd());$stream=New-Object IO.MemoryStream(,$raw);$im=$null;$bits=$null\ntry {$im=[Drawing.Image]::FromStream($stream,$false,$true);if($im.Width*$im.Height -gt 16000000){throw 'pixel limit'}\n$bmp=New-Object Drawing.Bitmap($im);try{$rect=New-Object Drawing.Rectangle(0,0,$bmp.Width,$bmp.Height);$bits=$bmp.LockBits($rect,[Drawing.Imaging.ImageLockMode]::ReadOnly,[Drawing.Imaging.PixelFormat]::Format24bppRgb)\n$sum=0;for($y=0;$y -lt $bmp.Height;$y++){$sum=$sum -bxor [Runtime.InteropServices.Marshal]::ReadByte([IntPtr]::Add($bits.Scan0,$y*$bits.Stride))}\n@{width=$bmp.Width;height=$bmp.Height;decoded=$true}|ConvertTo-Json -Compress\n} finally {if($bits){$bmp.UnlockBits($bits)};$bmp.Dispose()}\n} finally{if($im){$im.Dispose()};$stream.Dispose()}"

def jpeg(raw):
 if not 4<=len(raw)<=MAX_BYTES or raw[:2]!=b'\xff\xd8':raise ValueError('JPEG signature/size')
 pos=2;frame=None;sos=0;ended=False
 while pos<len(raw):
  if raw[pos]!=255:raise ValueError('JPEG marker boundary')
  while pos<len(raw) and raw[pos]==255:pos+=1
  if pos>=len(raw):raise ValueError('JPEG truncated marker')
  marker=raw[pos];pos+=1
  if marker==0xd9:
   if pos!=len(raw) or not sos or frame is None:raise ValueError('JPEG EOI/trailing')
   ended=True;break
  if marker in (0,0xd8,0x01) or 0xd0<=marker<=0xd7:raise ValueError('JPEG unexpected marker')
  if pos+2>len(raw):raise ValueError('JPEG truncated length')
  length=struct.unpack('>H',raw[pos:pos+2])[0]
  if length<2 or pos+length>len(raw):raise ValueError('JPEG truncated segment')
  segment=raw[pos+2:pos+length];pos+=length
  if marker in (0xc0,0xc1,0xc2):
   if frame or len(segment)<6:raise ValueError('JPEG frame')
   depth,h,w,n=struct.unpack('>BHHB',segment[:6])
   if depth!=8 or n not in (1,3,4) or len(segment)!=6+3*n or not w or not h or w*h>MAX_PIXELS:raise ValueError('JPEG pixel layout/limit')
   frame=(w,h)
  elif 0xc0<=marker<=0xcf and marker not in (0xc4,0xc8,0xcc):raise ValueError('JPEG unsupported frame mode')
  if marker==0xda:
   if frame is None:raise ValueError('JPEG scan before frame')
   sos+=1
   while pos<len(raw):
    if raw[pos]!=255:pos+=1;continue
    start=pos
    while pos<len(raw) and raw[pos]==255:pos+=1
    if pos>=len(raw):raise ValueError('JPEG truncated entropy')
    if raw[pos]==0 or 0xd0<=raw[pos]<=0xd7:pos+=1;continue
    pos=start;break
 if not ended:raise ValueError('JPEG missing EOI')
 strict_baseline_entropy(raw)
 exe=pathlib.Path(os.environ.get('SystemRoot','C:/Windows'))/'System32'/'WindowsPowerShell'/'v1.0'/'powershell.exe'
 if str(exe).lower().replace('\\','/')!='c:/windows/system32/windowspowershell/v1.0/powershell.exe':raise ValueError('unexpected system decoder path')
 safe(exe)
 encoded=base64.b64encode(JPEG_DECODER_PS.encode('utf-16-le')).decode('ascii')
 try:r=subprocess.run([str(exe),'-NoLogo','-NoProfile','-NonInteractive','-EncodedCommand',encoded],input=base64.b64encode(raw),stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=20,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
 except subprocess.TimeoutExpired as e:raise ValueError('JPEG decoder timeout') from e
 if r.returncode!=0:raise ValueError('JPEG decode validation failed')
 try:result=json.loads(r.stdout.decode('utf-8-sig').strip())
 except (ValueError,UnicodeError) as e:raise ValueError('JPEG decoder response invalid') from e
 if result!={'width':frame[0],'height':frame[1],'decoded':True}:raise ValueError('JPEG decoded dimensions differ')
 return dict(mime_type='image/jpeg',size_bytes=len(raw),width_pixels=frame[0],height_pixels=frame[1],validator='jpeg-baseline-gdi-v1')
def image_info(raw):
 if raw[:8]==b'\x89PNG\r\n\x1a\n':return png(raw)
 if raw[:2]==b'\xff\xd8':return jpeg(raw)
 raise ValueError('unsupported original image format')


MAX_SOURCE=32*1024*1024
IMAGE_KEYS={'reviewed_body_sha256','research','source_sha256','image_sha256','origin','role','classification_reason','reviewed_by','reviewed_at'}
def validate_image_claim(claim,source_hash):
 if set(claim)!=IMAGE_KEYS or claim['source_sha256']!=source_hash or not re.fullmatch('[a-f0-9]{64}',claim['image_sha256']):raise ValueError('original image claim fields/hash')
 origin=claim['origin']
 if not isinstance(origin,dict) or origin.get('kind') not in ('standalone_file','docx_media'):raise ValueError('source capture type unsupported')
 if origin['kind']=='standalone_file':
  if origin!={'kind':'standalone_file'}:raise ValueError('standalone origin must be canonical')
 else:
  if set(origin)!={'kind','part','relationship_id'} or origin['part']!='word/document.xml' or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.-]{0,100}',origin['relationship_id']):raise ValueError('DOCX origin must be canonical document relationship')
 loc=canon({'source_sha256':source_hash,'origin_kind':origin['kind'],'location':canon(origin)})
 validate_plan(dict(memo_id='pending',body_id='pending',source_id='pending',source_sha256=source_hash,private_locator=loc,role=claim['role'],classification_reason=claim['classification_reason'],reviewed_by=claim['reviewed_by'],reviewed_at=claim['reviewed_at'],research_json=canon(claim['research']),reviewed_body_sha256=claim['reviewed_body_sha256']))
def validate_item(i):
 validate_image_record_item(i)
 images=i.get('original_images',[])
 if not isinstance(images,list) or len(images)>20:raise ValueError('original image batch count')
 seen=set()
 for claim in images:
  if claim.get('reviewed_body_sha256')!=sha(i['body'].encode()):raise ValueError('image reviewed body differs from batch')
  validate_image_claim(claim,i['source']['content_hash']);validate_coverage(claim['research'],i['body']);key=canon(claim['origin'])
  if key in seen:raise ValueError('duplicate image source location/classification')
  seen.add(key)
def capture_source(inbox,raw,expected_sha256):
 inbox=safe(inbox)
 if not 0<len(raw)<=MAX_SOURCE or sha(raw)!=expected_sha256:raise ValueError('source capture size/hash')
 dest=inbox/(expected_sha256+'.bin')
 if dest.exists():
  if safe(dest).read_bytes()!=raw:raise ValueError('existing captured source differs')
 else:
  with dest.open('xb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
 return expected_sha256

# Bounded DOCX parsing. No DTDs, network, package extraction, or relaxed proof semantics.
DOC_XML_LIMIT=8*1024*1024
RELS_XML_LIMIT=4*1024*1024
ZIP_TOTAL_LIMIT=64*1024*1024
ZIP_MEMBER_LIMIT=32*1024*1024
ZIP_RATIO_LIMIT=200
XML_NODE_LIMIT=250000
XML_DEPTH_LIMIT=128
XML_ATTR_LIMIT=64
XML_ATTR_VALUE_LIMIT=8192
XML_NAME_LIMIT=2048
XML_NAME_TOTAL_LIMIT=32*1024*1024
XML_NAMESPACE_URI_LIMIT=1024
XML_NAMESPACE_PREFIX_LIMIT=128
XML_NAMESPACE_COUNT_LIMIT=4096
XML_NAMESPACE_TOTAL_LIMIT=1024*1024

class _BoundedXMLTree(ET.TreeBuilder):
 def __init__(self):
  super().__init__();self.nodes=0;self.depth=0;self.names=0;self.namespace_count=0;self.namespace_chars=0
 def doctype(self,name,pubid,system):
  raise ValueError('source XML DTD/entities forbidden')
 def start_ns(self,prefix,uri):
  self.namespace_count+=1;self.namespace_chars+=len(prefix)+len(uri)
  if len(prefix)>XML_NAMESPACE_PREFIX_LIMIT or len(uri)>XML_NAMESPACE_URI_LIMIT or self.namespace_count>XML_NAMESPACE_COUNT_LIMIT or self.namespace_chars>XML_NAMESPACE_TOTAL_LIMIT:raise ValueError('source XML namespace budget')
 def start(self,tag,attrs):
  self.nodes+=1;self.depth+=1
  if self.nodes>XML_NODE_LIMIT or self.depth>XML_DEPTH_LIMIT:raise ValueError('source XML structure budget')
  if len(attrs)>XML_ATTR_LIMIT or any(len(v)>XML_ATTR_VALUE_LIMIT for v in attrs.values()):raise ValueError('source XML attribute budget')
  names=(tag,*attrs)
  if any(len(n)>XML_NAME_LIMIT for n in names):raise ValueError('source XML expanded name budget')
  self.names+=sum(map(len,names))
  if self.names>XML_NAME_TOTAL_LIMIT:raise ValueError('source XML cumulative name budget')
  return super().start(tag,attrs)
 def end(self,tag):
  result=super().end(tag);self.depth-=1;return result

def _docx_inventory(z):
 entries=z.infolist();names=[x.filename for x in entries]
 if len(names)!=len(set(names)) or len(names)>10000:raise ValueError('ambiguous ZIP members')
 total=0
 for info in entries:
  name=info.orig_filename
  if name!=info.filename:raise ValueError('noncanonical ZIP member name')
  if chr(92) in name or name.startswith('/') or '..' in name.split('/') or ':' in name:raise ValueError('unsafe ZIP member name')
  if info.flag_bits&1 or info.compress_type not in (zipfile.ZIP_STORED,zipfile.ZIP_DEFLATED):raise ValueError('ZIP encryption/compression forbidden')
  if not 0<=info.file_size<=ZIP_MEMBER_LIMIT:raise ValueError('ZIP member budget')
  total+=info.file_size
  if total>ZIP_TOTAL_LIMIT:raise ValueError('ZIP expanded total budget')
  if info.file_size and (info.compress_size<=0 or info.file_size>ZIP_RATIO_LIMIT*info.compress_size):raise ValueError('ZIP compression ratio budget')

def _docx_member(z,name,limit):
 info=z.getinfo(name)
 if not 0<info.file_size<=limit or info.flag_bits&1:raise ValueError('ZIP member bounds/encryption')
 with z.open(info) as stream:data=stream.read(limit+1)
 if len(data)!=info.file_size or len(data)>limit:raise ValueError('ZIP actual member length mismatch')
 return data

def _docx_xml(z,name):
 limit=DOC_XML_LIMIT if name=='word/document.xml' else RELS_XML_LIMIT
 data=_docx_member(z,name,limit)
 # XMLParser calls doctype before processing internal declarations, independent of encoding.
 return ET.fromstring(data,parser=ET.XMLParser(target=_BoundedXMLTree()))

def extract_proven(inbox,claim):
 validate_image_claim(claim,claim['source_sha256']);path=safe(safe(inbox)/(claim['source_sha256']+'.bin'))
 if not 0<path.stat().st_size<=MAX_SOURCE:raise ValueError('captured source limit')
 raw=path.read_bytes()
 if sha(raw)!=claim['source_sha256']:raise ValueError('captured source SHA mismatch')
 origin=claim['origin'];proof=dict(source_sha256=sha(raw),origin_kind=origin['kind'])
 if origin['kind']=='standalone_file':image=raw;location={'kind':'standalone_file'}
 else:
  with zipfile.ZipFile(io.BytesIO(raw)) as z:
   _docx_inventory(z)
   def member(name,limit):return _docx_member(z,name,limit)
   def xml(name):return _docx_xml(z,name)
   part=origin['part'];rid=origin['relationship_id'];doc=xml(part);rels=xml('word/_rels/document.xml.rels')
   R='http://schemas.openxmlformats.org/officeDocument/2006/relationships'
   uses=[n for n in doc.iter() if (n.tag=='{http://schemas.openxmlformats.org/drawingml/2006/main}blip' and n.attrib.get('{'+R+'}embed')==rid) or (n.tag=='{urn:schemas-microsoft-com:vml}imagedata' and n.attrib.get('{'+R+'}id')==rid)]
   if not uses:raise ValueError('relationship not referenced in document')
   if len(uses)!=1:raise ValueError('ambiguous repeated image relationship occurrence')
   matches=[n for n in rels if n.attrib.get('Id')==rid]
   if len(matches)!=1:raise ValueError('ambiguous/missing image relationship')
   rel=matches[0]
   if rel.attrib.get('TargetMode','Internal')!='Internal' or not rel.attrib.get('Type','').endswith('/image'):raise ValueError('external/nonimage relationship forbidden')
   target=rel.attrib.get('Target','')
   if not target or '\\' in target or target.startswith('/') or ':' in target or '..' in target.split('/') or '%' in target:raise ValueError('unsafe relationship target')
   media=posixpath.normpath(posixpath.join('word',target))
   if not media.startswith('word/media/') or media!=posixpath.join('word',target):raise ValueError('noncanonical image member path')
   image=member(media,MAX_BYTES);location={'part':part,'relationship_id':rid,'media_part':media}
 if sha(image)!=claim['image_sha256']:raise ValueError('extracted original image SHA mismatch')
 proof['location']=canon(location)
 return image,canon(proof)

def store_root(d,create=False):
 paths=[r[2] for r in d.execute('pragma database_list') if r[1]=='main']
 if len(paths)!=1 or not paths[0]:raise ValueError('image database path required')
 p=pathlib.Path(paths[0]).parent/'images'
 if not p.exists() and create:p.mkdir()
 return safe(p)
def input_root(d):
 paths=[r[2] for r in d.execute('pragma database_list') if r[1]=='main']
 return safe(pathlib.Path(paths[0]).parent/'image-input')
def apply_images(d,i,mid,sid):
 root=store_root(d,True);records=[]
 prior=d.execute('select * from cp_memo_original_image where memo_id=? and source_id=?',(mid,sid)).fetchall()
 for claim in i.get('original_images',[]):
  if prior:
   matches=[]
   for row in prior:
    loc=json.loads(row['private_locator']);where=json.loads(loc['location']);origin=claim['origin']
    if row['image_sha256']==claim['image_sha256'] and loc['origin_kind']==origin['kind'] and all(where.get(k)==v for k,v in origin.items() if k!='kind'):matches.append(row)
   if len(matches)!=1:raise ValueError('existing image source location differs')
   locator=matches[0]['private_locator'];asset=d.execute('select * from cp_original_image_asset where image_sha256=?',(claim['image_sha256'],)).fetchone();desc=dict(asset)
  else:
   image,locator=extract_proven(input_root(d),claim);desc=prepare(root,image,claim['image_sha256'])
  plan={k:claim[k] for k in ('source_sha256','role','classification_reason','reviewed_by','reviewed_at')}
  plan['research_json']=canon(claim['research']);plan['reviewed_body_sha256']=claim['reviewed_body_sha256']
  plan.update(memo_id=mid,body_id=d.execute('select body_id from cp_memo where id=?',(mid,)).fetchone()[0],source_id=sid,private_locator=locator)
  bid=bind(d,root,desc,plan);records.append(dict(id=bid,descriptor=desc,plan=plan,plan_sha256=plan_hash(plan,desc['image_sha256'])))
 return records

def verify_records(d,records,mid,sid):
 if not records:return
 root=store_root(d)
 for record in records:
  if set(record)!={'id','descriptor','plan','plan_sha256'}:raise ValueError('image receipt record fields')
  plan=record['plan'];validate_plan(plan);current_binding(d,plan);validate_research_db(d,plan);verify_search(d,record['id'],plan)
  if plan['memo_id']!=mid or plan['source_id']!=sid or record['id']!=binding_id(plan) or record['plan_sha256']!=plan_hash(plan,record['descriptor']['image_sha256']):raise ValueError('image receipt identity mismatch')
  row=d.execute('select * from cp_memo_original_image where id=?',(record['id'],)).fetchone()
  if not row or any(row[k]!=v for k,v in plan.items()) or row['image_sha256']!=record['descriptor']['image_sha256'] or row['plan_sha256']!=record['plan_sha256']:raise ValueError('image receipt database differs')
  asset=d.execute('select * from cp_original_image_asset where image_sha256=?',(row['image_sha256'],)).fetchone()
  if not asset or dict(asset)!=record['descriptor']:raise ValueError('image receipt asset differs')
  verify_asset(root,asset)
 # Verification deliberately does not read image-input or original source paths.

def snapshot_bundle(snapshot,root,out):
 return bundle(snapshot,root,out)


class _Bits:
 def __init__(self,data):self.data=data;self.at=0
 def take(self,n):
  if self.at+n>len(self.data)*8:raise ValueError('JPEG entropy truncated')
  value=0
  for _ in range(n):value=(value<<1)|((self.data[self.at//8]>>(7-self.at%8))&1);self.at+=1
  return value
 def finish(self):
  remain=len(self.data)*8-self.at
  if remain>7 or (remain and self.take(remain)!=(1<<remain)-1):raise ValueError('JPEG entropy padding/extra data')

def _huffman(bits,table):
 code=0
 for size in range(1,17):
  code=(code<<1)|bits.take(1)
  if (size,code) in table:return table[size,code]
 raise ValueError('JPEG invalid Huffman code')
def _block(bits,dc,ac):
 n=_huffman(bits,dc)
 if n>11:raise ValueError('JPEG invalid DC size')
 bits.take(n);k=1
 while k<64:
  symbol=_huffman(bits,ac);run= symbol>>4;size=symbol&15
  if symbol==0:return
  if symbol==0xf0:k+=16
  else:
   if not 1<=size<=10:raise ValueError('JPEG invalid AC size')
   k+=run
   if k>=64:raise ValueError('JPEG AC run overflow')
   bits.take(size);k+=1
  if k>64:raise ValueError('JPEG AC run overflow')

def strict_baseline_entropy(raw):
 """Decode Huffman symbol lengths for every expected baseline DCT block.
 No inverse transform/re-encoding. Reject progressive/lossless until separately reviewed.
 """
 deadline=time.monotonic()+10
 pos=2;frame=None;components={};tables={};quant=set();dri=0;seen=set();scan_count=0
 while pos<len(raw):
  if raw[pos]!=255:raise ValueError('JPEG marker boundary')
  while pos<len(raw) and raw[pos]==255:pos+=1
  if pos>=len(raw):raise ValueError('JPEG marker truncated')
  marker=raw[pos];pos+=1
  if marker==0xd9:
   if seen!=set(components) or not scan_count:raise ValueError('JPEG incomplete component scans')
   return
  length=int.from_bytes(raw[pos:pos+2],'big');seg=raw[pos+2:pos+length];pos+=length
  if marker==0xc0:
   if frame:raise ValueError('JPEG duplicate frame')
   depth,h,w,n=struct.unpack('>BHHB',seg[:6]);frame=(w,h)
   for k in range(n):
    cid,hv,tq=seg[6+3*k:9+3*k];hs,vs=hv>>4,hv&15
    if cid in components or not 1<=hs<=4 or not 1<=vs<=4 or tq>3:raise ValueError('JPEG sampling factors')
    components[cid]=(hs,vs,tq)
   if sum(h*v for h,v,q in components.values())>10:raise ValueError('JPEG excessive blocks per MCU')
  elif marker in (0xc1,0xc2):raise ValueError('JPEG nonbaseline mode pending strict entropy validator')
  elif marker==0xc4:
   k=0
   while k<len(seg):
    spec=seg[k];k+=1;kind,tid=spec>>4,spec&15
    if kind not in (0,1) or tid>3 or k+16>len(seg):raise ValueError('JPEG DHT header')
    counts=list(seg[k:k+16]);k+=16;n=sum(counts)
    if not n or n>256 or k+n>len(seg):raise ValueError('JPEG DHT values')
    values=seg[k:k+n];k+=n;code=0;j=0;t={}
    for size,count in enumerate(counts,1):
     for _ in range(count):
      if code>=(1<<size)-1:raise ValueError('JPEG oversubscribed/all-ones Huffman')
      t[size,code]=values[j];code+=1;j+=1
     code<<=1
    tables[kind,tid]=t
  elif marker==0xdb:
   k=0
   while k<len(seg):
    spec=seg[k];k+=1
    if spec>>4!=0 or (spec&15)>3 or k+64>len(seg):raise ValueError('JPEG baseline quantization')
    if any(v==0 for v in seg[k:k+64]):raise ValueError('JPEG zero quantizer')
    quant.add(spec&15);k+=64
  elif marker==0xdd:
   if len(seg)!=2:raise ValueError('JPEG restart interval')
   dri=int.from_bytes(seg,'big')
  elif marker==0xda:
   if not frame or not seg:raise ValueError('JPEG scan header')
   ns=seg[0]
   if not 1<=ns<=len(components) or len(seg)!=1+2*ns+3 or seg[-3:]!=b'\x00\x3f\x00':raise ValueError('JPEG baseline scan parameters')
   sc=[]
   for k in range(ns):
    cid,ta=seg[1+2*k:3+2*k]
    if cid not in components or cid in seen or cid in [x[0] for x in sc]:raise ValueError('JPEG duplicate/unknown scanned component')
    dc,ac=ta>>4,ta&15
    if (0,dc) not in tables or (1,ac) not in tables or components[cid][2] not in quant:raise ValueError('JPEG undefined table')
    sc.append((cid,dc,ac))
   groups=[];buf=bytearray()
   while pos<len(raw):
    v=raw[pos];pos+=1
    if v!=255:buf.append(v);continue
    start=pos-1
    while pos<len(raw) and raw[pos]==255:pos+=1
    if pos>=len(raw):raise ValueError('JPEG entropy ending')
    v=raw[pos];pos+=1
    if v==0:buf.append(255)
    elif 0xd0<=v<=0xd7:groups.append((bytes(buf),v));buf.clear()
    else:groups.append((bytes(buf),None));pos=start;break
   w,h=frame;hm=max(v[0] for v in components.values());vm=max(v[1] for v in components.values())
   if ns==1:
    hs,vs,_=components[sc[0][0]];nx=(w*hs+8*hm-1)//(8*hm);ny=(h*vs+8*vm-1)//(8*vm);blocks=[sc[0]]
   else:
    nx=(w+8*hm-1)//(8*hm);ny=(h+8*vm-1)//(8*vm);blocks=[t for t in sc for _ in range(components[t[0]][0]*components[t[0]][1])]
   total=nx*ny;consumed=0
   for gi,(data,restart) in enumerate(groups):
    count=min(dri,total-consumed) if dri else total-consumed
    if count<=0 or (not dri and gi):raise ValueError('JPEG unexpected restart group')
    bits=_Bits(data)
    for mcu_index in range(count):
     if mcu_index%128==0 and time.monotonic()>deadline:raise ValueError('JPEG entropy validation timeout')
     for cid,dc,ac in blocks:_block(bits,tables[0,dc],tables[1,ac])
    bits.finish();consumed+=count
    if consumed<total:
     if restart!=0xd0+(gi%8):raise ValueError('JPEG missing/wrong restart marker')
    elif restart is not None:raise ValueError('JPEG excess restart marker')
   if consumed!=total:raise ValueError('JPEG incomplete entropy scan')
   seen.update(t[0] for t in sc);scan_count+=1
 raise ValueError('JPEG missing complete scan')

# Forward-only reviewed retention policy. No update/backfill of previously archived Memo.
SCHEMA=SCHEMA.replace(' plan_sha256 TEXT NOT NULL', ' research_json TEXT NOT NULL,\n reviewed_body_sha256 TEXT NOT NULL CHECK(length(reviewed_body_sha256)=64),\n plan_sha256 TEXT NOT NULL')
SCHEMA += """CREATE TABLE cp_original_image_search(
 image_link_id TEXT NOT NULL REFERENCES cp_memo_original_image(id),
 ordinal INTEGER NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('company','theme','metric')),
 company_id TEXT REFERENCES cp_company(id),
 theme_id TEXT REFERENCES cp_theme(id),
 name TEXT NOT NULL,
 unit TEXT NOT NULL,
 period TEXT NOT NULL,
 source_quote TEXT NOT NULL,
 region_json TEXT NOT NULL,
 reason TEXT NOT NULL,
 PRIMARY KEY(image_link_id,ordinal),
 CHECK((kind='company' AND company_id IS NOT NULL AND theme_id IS NULL) OR
       (kind='theme' AND company_id IS NULL AND theme_id IS NOT NULL) OR
       (kind='metric' AND company_id IS NULL AND theme_id IS NULL)));
CREATE INDEX original_image_search_name ON cp_original_image_search(kind,name);
CREATE INDEX original_image_search_company ON cp_original_image_search(company_id);
CREATE INDEX original_image_search_theme ON cp_original_image_search(theme_id);
"""
def nonempty(x,limit=4096):
 return isinstance(x,str) and 0<len(x.strip())<=limit and '\x00' not in x

def validate_research(r):
 required={'policy','source_context','decision','whole_body_reviewed','covered_text','text_coverage_assessment','material_increment','retention_reason','search'}
 if not isinstance(r,dict) or set(r)!=required:raise ValueError('image research decision fields')
 if r['policy']!='forward-only-material-increment-v1' or r['decision']!='retain_original' or r['whole_body_reviewed'] is not True:raise ValueError('image retention review required')
 if r['source_context'] not in ('standalone_chart','standalone_image_table'):raise ValueError('report images excluded by retention policy')
 for k in ('text_coverage_assessment','material_increment','retention_reason'):
  if not nonempty(r[k]):raise ValueError('material increment and coverage rationale required')
 ranges=r['covered_text']
 if not isinstance(ranges,list) or len(ranges)>100:raise ValueError('covered text list')
 for q in ranges:
  if not isinstance(q,dict) or set(q)!={'start','end','quote'} or type(q['start']) is not int or type(q['end']) is not int or not 0<=q['start']<q['end'] or not nonempty(q['quote'],20000):raise ValueError('covered text range')
 entries=r['search']
 if not isinstance(entries,list) or not 1<=len(entries)<=100:raise ValueError('image search metadata required')
 seen=set();has_theme=False;has_metric=False
 for e in entries:
  keys={'kind','id','name','unit','period','source_quote','region','reason','scope_sha256'}
  if not isinstance(e,dict) or set(e)!=keys or e['kind'] not in ('company','theme','metric'):raise ValueError('image search fields')
  if any(not nonempty(e[k]) for k in ('name','source_quote','reason')):raise ValueError('source-derived image search evidence required')
  if any(not isinstance(e[k],str) or len(e[k])>512 or '\x00' in e[k] for k in ('unit','period')):raise ValueError('image metric unit/period')
  if e['kind']=='metric':
   has_metric=True
   if e['id'] is not None or e['scope_sha256'] is not None:raise ValueError('metric must not infer identity')
  else:
   if not nonempty(e['id'],512) or e['unit'] or e['period']:raise ValueError('image entity search identity')
   if e['kind']=='theme':
    has_theme=True
    if not isinstance(e['scope_sha256'],str) or not re.fullmatch('[a-f0-9]{64}',e['scope_sha256']):raise ValueError('reviewed theme scope hash required')
   elif e['scope_sha256'] is not None:raise ValueError('company scope must be null')
  box=e['region']
  if not isinstance(box,list) or len(box)!=4 or any(type(v) not in (int,float) for v in box) or not (0<=box[0]<box[2]<=1 and 0<=box[1]<box[3]<=1):raise ValueError('image evidence normalized region')
  key=(e['kind'],e['id'],e['name'],e['unit'],e['period'])
  if key in seen:raise ValueError('duplicate image search entry')
  seen.add(key)
 if not has_theme or not has_metric:raise ValueError('reviewed theme and primary metric required')

def validate_coverage(r,body):
 validate_research(r)
 for q in r['covered_text']:
  if body[q['start']:q['end']]!=q['quote']:raise ValueError('body coverage quote differs')

def validate_research_db(d,plan):
 r=json.loads(plan['research_json']);body=d.execute('select body from cp_body where id=?',(plan['body_id'],)).fetchone()
 if not body:raise ValueError('image historical body missing')
 if sha(body[0].encode())!=plan['reviewed_body_sha256']:raise ValueError('image reviewed body full SHA mismatch')
 validate_coverage(r,body[0])
 if r['source_context']!=plan['role']:raise ValueError('image retention role mismatch')
 for e in r['search']:
  if e['kind']=='metric':continue
  table='cp_company' if e['kind']=='company' else 'cp_theme'
  row=d.execute('select * from '+table+' where id=?',(e['id'],)).fetchone()
  if not row or row['status']!='active' or row['name']!=e['name']:raise ValueError('image reviewed entity name/status mismatch')
  if e['kind']=='theme' and sha(row['scope'].encode())!=e['scope_sha256']:raise ValueError('image reviewed theme scope changed')
 # Evidence quotes/regions are reviewer observations, never OCR-verified by byte hash.

def search_rows(plan):
 r=json.loads(plan['research_json']);out=[]
 for ix,e in enumerate(r['search']):
  out.append((ix,e['kind'],e['id'] if e['kind']=='company' else None,e['id'] if e['kind']=='theme' else None,e['name'],e['unit'],e['period'],e['source_quote'],canon(e['region']),e['reason']))
 return out

def store_search(d,bid,plan):
 for row in search_rows(plan):d.execute('insert into cp_original_image_search values('+','.join('?' for _ in range(11))+')',(bid,*row))

def verify_search(d,bid,plan):
 rows=[tuple(x)[1:] for x in d.execute('select * from cp_original_image_search where image_link_id=? order by ordinal',(bid,))]
 if rows!=search_rows(plan):raise ValueError('image searchable metadata differs')

SCHEMA += """CREATE TABLE cp_independent_image_record(
 memo_id TEXT NOT NULL REFERENCES cp_memo(id),
 source_id TEXT NOT NULL UNIQUE REFERENCES cp_source_record(id),
 body_id TEXT NOT NULL REFERENCES cp_body(id),
 image_sha256 TEXT NOT NULL,
 original_text TEXT NOT NULL,
 record_json TEXT NOT NULL,
 record_sha256 TEXT NOT NULL,
 assignment_json TEXT NOT NULL,
 assignment_sha256 TEXT NOT NULL,
 PRIMARY KEY(memo_id,source_id));
CREATE INDEX independent_image_identity ON cp_independent_image_record(image_sha256);
"""
RECORD_KEYS={'protocol','extraction_method','original_paragraphs','original_text','original_text_sha256','quotes','body_origin'}
def record_body(record):
 return '[原始来源文字 / 原样保留，无文字则为空]\n'+record['original_text']+'\n\n[图内逐字引用 / 派生内容，非原文正文]\n'+'\n'.join(q['quote'] for q in record['quotes'])

def validate_record(record):
 if not isinstance(record,dict) or set(record)!=RECORD_KEYS or record['protocol']!='independent-image-record-v1' or record['extraction_method'] not in ('docx-simple-paragraphs-v1','standalone-image-v1') or record['body_origin']!='original_text_plus_explicit_image_quotes':raise ValueError('independent image record fields')
 ps=record['original_paragraphs']
 if not isinstance(ps,list) or not 0<=len(ps)<=100 or any(not isinstance(p,str) or '\x00' in p for p in ps) or sum(map(len,ps))>4096:raise ValueError('simple original paragraphs required')
 if (record['extraction_method']=='standalone-image-v1' and ps!=[]) or (record['extraction_method']=='docx-simple-paragraphs-v1' and not ps):raise ValueError('original paragraph/source type mismatch')
 if record['original_text']!='\n'.join(ps) or record['original_text_sha256']!=sha(record['original_text'].encode()):raise ValueError('original text reconstruction differs')
 qs=record['quotes']
 if not isinstance(qs,list) or not 1<=len(qs)<=6:raise ValueError('minimal reviewed image quotes required')
 seen=set()
 for q in qs:
  if not isinstance(q,dict) or set(q)!={'image_sha256','quote','region','reviewed_by','reviewed_at'} or not nonempty(q['quote'],512) or not nonempty(q['reviewed_by'],512) or not re.fullmatch('[a-f0-9]{64}',q['image_sha256']):raise ValueError('derived quote fields')
  try:stamp=datetime.datetime.fromisoformat(q['reviewed_at'])
  except (ValueError,TypeError) as ex:raise ValueError('derived quote review time') from ex
  if stamp.tzinfo is None:raise ValueError('derived quote review timezone')
  b=q['region']
  if not isinstance(b,list) or len(b)!=4 or any(type(v) not in (int,float) for v in b) or not 0<=b[0]<b[2]<=1 or not 0<=b[1]<b[3]<=1:raise ValueError('derived quote region')
  if q['quote'] in seen:raise ValueError('repeated derived quotation')
  seen.add(q['quote'])

def validate_image_record_item(i):
 record=i.get('image_record')
 if record is None:return
 validate_record(record)
 if i['body']!=record_body(record):raise ValueError('image record deterministic body differs')
 if i['material_date'] is not None:raise ValueError('image-record-v1 date is explicitly unknown; reviewed date protocol required')
 images=i.get('original_images',[])
 if len(images)!=1 or images[0]['origin'].get('kind') not in ('docx_media','standalone_file') or i.get('native_tables') or i.get('relations'):raise ValueError('image-record-v1 single independent image only')
 expected_method='docx-simple-paragraphs-v1' if images[0]['origin']['kind']=='docx_media' else 'standalone-image-v1'
 if record['extraction_method']!=expected_method:raise ValueError('image record extraction method/source mismatch')
 if expected_method=='standalone-image-v1' and images[0]['source_sha256']!=images[0]['image_sha256']:raise ValueError('direct original file hash mismatch')
 if any(q['image_sha256']!=images[0]['image_sha256'] for q in record['quotes']):raise ValueError('derived quote original image mismatch')

def extract_original_paragraphs(inbox,claim):
 # Re-use full hash/member/relationship proof, then narrowly decode text, never silently discard structure.
 original_image,_=extract_proven(inbox,claim)
 if claim['origin']['kind']=='standalone_file':
  image_info(original_image);return []
 raw=safe(safe(inbox)/(claim['source_sha256']+'.bin')).read_bytes()
 with zipfile.ZipFile(io.BytesIO(raw)) as z:
  _docx_inventory(z)
  doc=_docx_xml(z,'word/document.xml');w='{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
  forbidden={'tbl','fldChar','instrText','delText','ins','del','altChunk','object','txbxContent','headerReference','footerReference','footnoteReference','endnoteReference','sym'}
  if any(n.tag in {w+x for x in forbidden} for n in doc.iter()):raise ValueError('unsupported original DOCX structure')
  body=doc.find(w+'body')
  if body is None or any(n.tag not in (w+'p',w+'sectPr') for n in body):raise ValueError('unsupported original body structure')
  paragraphs=list(body.findall(w+'p'))
  image_nodes=[node for node in doc.iter() if node.tag in ('{http://schemas.openxmlformats.org/drawingml/2006/main}blip','{urn:schemas-microsoft-com:vml}imagedata')]
  if len(image_nodes)!=1:raise ValueError('simple image record requires exactly one source image')
  a_ns='{http://schemas.openxmlformats.org/drawingml/2006/main}';pic_ns='{http://schemas.openxmlformats.org/drawingml/2006/picture}'
  if any(node.tag==a_ns+'t' or node.tag==w+'pict' or node.tag.endswith('}OLEObject') or '/chart}' in node.tag or '/diagram}' in node.tag for node in doc.iter()):raise ValueError('unsupported drawing text/chart/diagram/OLE')
  for node in doc.iter():
   if node.tag in (a_ns+'srcRect',a_ns+'custGeom',a_ns+'effectDag'):raise ValueError('unsupported picture crop/geometry/effect')
   if node.tag==a_ns+'xfrm' and any(node.attrib.get(k,'0').lower() not in ('0','false') for k in ('rot','flipH','flipV')):raise ValueError('unsupported picture rotation/flip')
   if node.tag==a_ns+'prstGeom' and node.attrib.get('prst')!='rect':raise ValueError('unsupported nonrectangular picture geometry')
   if node.tag==a_ns+'effectLst' and len(node):raise ValueError('unsupported picture effects')
   if node.tag==a_ns+'blip' and len(node):raise ValueError('unsupported picture color/effect children')
   if node.tag==a_ns+'fillRect' and any(v not in ('0','') for v in node.attrib.values()):raise ValueError('unsupported picture fill crop')
  graphics=list(doc.iter(a_ns+'graphicData'))
  if len(graphics)!=1 or graphics[0].attrib.get('uri')!='http://schemas.openxmlformats.org/drawingml/2006/picture' or len(graphics[0])!=1 or graphics[0][0].tag!=pic_ns+'pic' or len(list(doc.iter(w+'drawing')))!=1:raise ValueError('pure single picture graphic required')
  direct_text=[node for para in paragraphs for run in para.findall(w+'r') for node in run if node.tag==w+'t']
  if len(direct_text)!=len(list(doc.iter(w+'t'))):raise ValueError('hidden text outside simple paragraph runs')

  for paragraph in paragraphs:
   if any(child.tag not in (w+'pPr',w+'r') for child in paragraph):raise ValueError('unsupported original paragraph node')
   for run in paragraph.findall(w+'r'):
    if any(child.tag not in (w+'rPr',w+'t',w+'tab',w+'br',w+'cr',w+'drawing',w+'pict') for child in run):raise ValueError('unsupported original run text node')
  if any(node.tag=='{http://schemas.openxmlformats.org/markup-compatibility/2006}AlternateContent' for node in doc.iter()):raise ValueError('alternate text/image content unsupported')
  if len(list(doc.iter(w+'p')))!=len(paragraphs):raise ValueError('nested paragraph unsupported')
  out=[]
  for p in paragraphs:
   parts=[]
   for node in p.iter():
    if node.tag==w+'t':parts.append(node.text or '')
    elif node.tag==w+'tab':parts.append('\t')
    elif node.tag in (w+'br',w+'cr'):parts.append('\n')
   out.append(''.join(parts))
  return out

def assignment_data(i):return {k:i.get(k) for k in ('title','category','material_date','institution','companies','themes','primary_subject','relevance')}
def assignment_hash(i):return sha(canon(assignment_data(i)).encode())

def check_image_record_duplicate(d,i,mid):
 if not i.get('image_record'):raise ValueError('existing body image attachment requires reviewed version plan')
 digest=i['original_images'][0]['image_sha256'];old=d.execute('select * from cp_independent_image_record where memo_id=?',(mid,)).fetchall()
 if not old:raise ValueError('old Memo image backfill forbidden')
 wanted=sha(canon(i['image_record']).encode());assign=assignment_hash(i)
 if any(r['image_sha256']!=digest or r['record_sha256']!=wanted or r['assignment_sha256']!=assign for r in old):raise ValueError('same body different image/derivation requires reconciliation')

def precheck_image_record(d,i):
 if not i.get('image_record'):return
 validate_image_record_item(i)
 hits=d.execute('select distinct memo_id from cp_independent_image_record where image_sha256=?',(i['original_images'][0]['image_sha256'],)).fetchall()
 if len(hits)>1:raise ValueError('image already ambiguously counted')
 if hits:
  mid=hits[0][0];check_image_record_duplicate(d,i,mid)
  actual=d.execute('select b.body from cp_memo m join cp_body b on b.id=m.body_id where m.id=?',(mid,)).fetchone()
  if not actual or actual[0]!=i['body']:raise ValueError('same image changed interpretation HOLD')
 # Source bytes needed only for a genuinely new source, not a committed replay.
 existing=d.execute('select 1 from cp_source_record where namespace=? and record_key=?',(i['source']['namespace'],i['source']['record_key'])).fetchone()
 if not existing and extract_original_paragraphs(input_root(d),i['original_images'][0])!=i['image_record']['original_paragraphs']:raise ValueError('original DOCX paragraph proof differs')

def apply_image_record(d,i,mid,sid):
 if not i.get('image_record'):return None
 record=i['image_record'];body_id=d.execute('select body_id from cp_memo where id=?',(mid,)).fetchone()[0]
 values=dict(memo_id=mid,source_id=sid,body_id=body_id,image_sha256=i['original_images'][0]['image_sha256'],original_text=record['original_text'],record_json=canon(record),record_sha256=sha(canon(record).encode()),assignment_json=canon(assignment_data(i)),assignment_sha256=assignment_hash(i))
 row=d.execute('select * from cp_independent_image_record where source_id=?',(sid,)).fetchone()
 if row:
  if dict(row)!=values:raise ValueError('image-record replay differs')
 else:d.execute('insert into cp_independent_image_record values('+','.join('?' for _ in values)+')',tuple(values.values()))
 return values

def verify_image_record_row(d,row):
 verify_record_assignment(d,row)
 record=json.loads(row['record_json']);validate_record(record)
 if row['record_json']!=canon(record) or row['record_sha256']!=sha(row['record_json'].encode()) or row['original_text']!=record['original_text']:raise ValueError('image-record origin checksum')
 body=d.execute('select b.body,m.body_id,m.material_date from cp_memo m join cp_body b on b.id=m.body_id where m.id=?',(row['memo_id'],)).fetchone()
 if not body or body[0]!=record_body(record) or body[1]!=row['body_id'] or body[2] is not None:raise ValueError('image-record original/derived reconstruction mismatch')
 images=d.execute('select image_sha256,reviewed_body_sha256 from cp_memo_original_image where memo_id=? and source_id=?',(row['memo_id'],row['source_id'])).fetchall()
 if len(images)!=1 or images[0][0]!=row['image_sha256'] or images[0][1]!=sha(body[0].encode()) or any(q['image_sha256']!=row['image_sha256'] for q in record['quotes']):raise ValueError('image-record original asset association differs')
 if not d.execute('select 1 from cp_memo_source where memo_id=? and source_id=?',(row['memo_id'],row['source_id'])).fetchone():raise ValueError('image-record source association absent')

def verify_image_record_rows(d):
 for row in d.execute('select * from cp_independent_image_record'):verify_image_record_row(d,row)
 # One retained independent image identity corresponds to one Memo, not repeated coverage.
 if d.execute('select image_sha256 from cp_independent_image_record group by image_sha256 having count(distinct memo_id)>1').fetchone():raise ValueError('independent image double counted')

def verify_image_record_receipt(d,record,mid,sid):
 actual=d.execute('select * from cp_independent_image_record where memo_id=? and source_id=?',(mid,sid)).fetchone()
 if record is None:
  if actual:raise ValueError('missing image-record receipt origin')
  return
 if not actual or dict(actual)!=record:raise ValueError('image-record receipt origin differs')
 verify_image_record_row(d,actual)

def verify_record_assignment(d,row):
 import extensions,primary_subject
 data=json.loads(row['assignment_json'])
 if row['assignment_json']!=canon(data) or row['assignment_sha256']!=sha(row['assignment_json'].encode()) or set(data)!={'title','category','material_date','institution','companies','themes','primary_subject','relevance'}:raise ValueError('image-record assignment checksum')
 mid=row['memo_id'];m=d.execute('select title,category,material_date,institution,body_id,lifecycle from cp_memo where id=?',(mid,)).fetchone()
 if not m or tuple(m[:4])!=tuple(data[k] for k in ('title','category','material_date','institution')) or m[4]!=row['body_id'] or m[5]!='active':raise ValueError('image-record semantic metadata differs')
 for kind,table,key in [('companies','cp_memo_company','company_id'),('themes','cp_memo_theme','theme_id')]:
  actual=d.execute('select * from '+table+' where memo_id=?',(mid,)).fetchall()
  if len(actual)!=len(data[kind]):raise ValueError('image-record assignment count differs')
  for n,wanted in enumerate(data[kind]):
   found=[x for x in actual if x[key]==wanted[key]]
   if len(found)!=1:raise ValueError('image-record assignment missing')
   x=found[0]
   if x['review_status']!='approved' or x['reason']!=wanted['reason'] or x['evidence_id']!=mid+'-'+kind+'-'+str(n) or (kind=='companies' and x['scope_kind']!=wanted['scope_kind']):raise ValueError('image-record assignment differs')
   extensions.check_evidence(d,x['evidence_id'],wanted['evidence'],mid,row['body_id'])
 rel=data.get('relevance') or [];actual={(x[0],x[1]) for x in d.execute('select company_id,theme_id from cp_relevance where memo_id=?',(mid,))}
 if actual!={(x['company_id'],x['theme_id']) for x in rel}:raise ValueError('image-record relevance set differs')
 extensions.duplicate_check(d,{'relevance':rel},mid);primary_subject.verify(d,data['primary_subject'],mid)

# BEGIN additive vector originals v2. Never render EMF here.
VECTOR_TABLES=('cp_vector_original_asset','cp_vector_preview','cp_memo_vector_image')
VECTOR_SCHEMA="""
CREATE TABLE cp_vector_original_asset(
 original_sha256 TEXT PRIMARY KEY CHECK(length(original_sha256)=64),
 relative_path TEXT NOT NULL UNIQUE,
 mime_type TEXT NOT NULL CHECK(mime_type='image/emf'),
 size_bytes INTEGER NOT NULL CHECK(size_bytes>0 AND size_bytes<=16777216),
 envelope_json TEXT NOT NULL CHECK(json_valid(envelope_json)),
 validator TEXT NOT NULL CHECK(validator='emf-envelope-opaque-v1'));
CREATE TABLE cp_vector_preview(
 original_sha256 TEXT NOT NULL REFERENCES cp_vector_original_asset(original_sha256),
 preview_sha256 TEXT NOT NULL CHECK(length(preview_sha256)=64),
 relative_path TEXT NOT NULL,
 mime_type TEXT NOT NULL CHECK(mime_type='image/png'),
 validator TEXT NOT NULL CHECK(validator='derived-png-metadata-v1'),
 size_bytes INTEGER NOT NULL CHECK(size_bytes>0 AND size_bytes<=16777216),
 width_pixels INTEGER NOT NULL CHECK(width_pixels>0),
 height_pixels INTEGER NOT NULL CHECK(height_pixels>0),
 provenance_json TEXT NOT NULL CHECK(json_valid(provenance_json)),
 provenance_sha256 TEXT NOT NULL CHECK(length(provenance_sha256)=64),
 PRIMARY KEY(original_sha256,preview_sha256));
CREATE TABLE cp_memo_vector_image(
 id TEXT PRIMARY KEY,
 memo_id TEXT NOT NULL REFERENCES cp_memo(id),
 body_id TEXT NOT NULL REFERENCES cp_body(id),
 source_id TEXT NOT NULL REFERENCES cp_source_record(id),
 source_sha256 TEXT NOT NULL,
 private_locator TEXT NOT NULL,
 original_sha256 TEXT NOT NULL,
 preview_sha256 TEXT NOT NULL,
 role TEXT NOT NULL CHECK(role IN ('standalone_chart','standalone_image_table')),
 classification_reason TEXT NOT NULL,
 reviewed_by TEXT NOT NULL,
 reviewed_at TEXT NOT NULL,
 reviewed_body_sha256 TEXT NOT NULL,
 research_json TEXT NOT NULL CHECK(json_valid(research_json)),
 plan_sha256 TEXT NOT NULL,
 UNIQUE(memo_id,source_id,private_locator),
 FOREIGN KEY(original_sha256,preview_sha256) REFERENCES cp_vector_preview(original_sha256,preview_sha256));
CREATE TRIGGER vector_current_binding BEFORE INSERT ON cp_memo_vector_image BEGIN
 SELECT RAISE(ABORT,'vector current source/body mismatch') WHERE NOT EXISTS(
 SELECT 1 FROM cp_memo m JOIN cp_memo_source ms ON ms.memo_id=m.id JOIN cp_source_record s ON s.id=ms.source_id
 WHERE m.id=NEW.memo_id AND m.body_id=NEW.body_id AND s.id=NEW.source_id AND s.content_hash=NEW.source_sha256);
 SELECT RAISE(ABORT,'vector/raster locator conflict') WHERE EXISTS(
 SELECT 1 FROM cp_memo_original_image WHERE memo_id=NEW.memo_id AND source_id=NEW.source_id AND private_locator=NEW.private_locator);
END;
CREATE TRIGGER raster_vector_exclusion BEFORE INSERT ON cp_memo_original_image BEGIN
 SELECT RAISE(ABORT,'raster/vector locator conflict') WHERE EXISTS(
 SELECT 1 FROM cp_memo_vector_image WHERE memo_id=NEW.memo_id AND source_id=NEW.source_id AND private_locator=NEW.private_locator);
END;
"""
for _vt in VECTOR_TABLES:
 for _va in ('update','delete'):
  VECTOR_SCHEMA+="CREATE TRIGGER guard_"+_vt+"_"+_va+" BEFORE "+_va+" ON "+_vt+" BEGIN SELECT RAISE(ABORT,'vectors append-only'); END;\n"
VECTOR_OBJECTS=VECTOR_TABLES+('vector_current_binding','raster_vector_exclusion')+tuple('guard_'+t+'_'+a for t in VECTOR_TABLES for a in ('update','delete'))

def _vector_sql(d):
 pending=''
 for line in VECTOR_SCHEMA.splitlines(True):
  pending+=line
  if sqlite3.complete_statement(pending):d.execute(pending);pending=''
 if pending.strip():raise ValueError('vector DDL incomplete')

def vector_state(d,required=False):
 marks=','.join('?' for _ in VECTOR_OBJECTS)
 rows=[tuple(x) for x in d.execute('select name,type,sql from sqlite_master where name in ('+marks+') order by name',VECTOR_OBJECTS)]
 if not rows:
  if required:raise ValueError('vector schema upgrade required')
  return False
 tmp=sqlite3.connect(':memory:')
 try:
  tmp.execute('create table cp_memo_original_image(id text)')
  _vector_sql(tmp)
  expected=[tuple(x) for x in tmp.execute('select name,type,sql from sqlite_master where name in ('+marks+') order by name',VECTOR_OBJECTS)]
 finally:tmp.close()
 if rows!=expected:raise ValueError('partial or changed vector schema')
 return True

def install_vectors(d):
 if not d.in_transaction:raise ValueError('explicit schema transaction required')
 if vector_state(d):raise ValueError('vector schema already installed')
 _vector_sql(d);vector_state(d,True)

def emf_envelope(raw):
 if not 108<=len(raw)<=MAX_BYTES:raise ValueError('EMF byte limit')
 kind,header=struct.unpack_from('<II',raw)
 if kind!=1 or header<88 or header%4 or header>len(raw):raise ValueError('EMF header')
 sig,version,total,count=struct.unpack_from('<IIII',raw,40)
 if sig!=0x464d4520 or version!=0x10000 or total!=len(raw) or not 3<=count<=100000:raise ValueError('EMF identity/size/count')
 bounds=struct.unpack_from('<iiii',raw,8);frame=struct.unpack_from('<iiii',raw,24)
 if bounds[2]<=bounds[0] or bounds[3]<=bounds[1] or frame[2]<=frame[0] or frame[3]<=frame[1]:raise ValueError('EMF geometry')
 chars,offset=struct.unpack_from('<II',raw,60)
 if chars and (offset<88 or offset%2 or offset+2*chars>header):raise ValueError('EMF description range')
 pos=0;observed=0
 while pos<len(raw):
  if len(raw)-pos<8:raise ValueError('truncated EMF record')
  typ,size=struct.unpack_from('<II',raw,pos)
  if size<8 or size%4 or pos+size>len(raw):raise ValueError('EMF record boundary')
  if pos and typ==1:raise ValueError('duplicate EMF header')
  observed+=1
  if observed>100000:raise ValueError('EMF record limit')
  if typ==14:
   if size<20 or pos+size!=len(raw) or struct.unpack_from('<I',raw,pos+size-4)[0]!=size:raise ValueError('EMF EOF envelope')
   entries,off=struct.unpack_from('<II',raw,pos+8)
   if entries and (off<16 or off+4*entries>size-4):raise ValueError('EMF palette bounds')
  elif pos+size==len(raw):raise ValueError('EMF missing EOF')
  pos+=size
 if observed!=count:raise ValueError('EMF record count')
 return dict(bounds=list(bounds),frame_01mm=list(frame),record_count=count)

def vector_rel(digest,preview=False):
 if not isinstance(digest,str) or not re.fullmatch('[a-f0-9]{64}',digest):raise ValueError('vector SHA format')
 return ('derived/' if preview else 'objects/')+digest[:2]+'/'+digest+('.png' if preview else '.emf')

def _vector_write(root,raw,relative_path):
 # Caller generated content path only, no user-supplied filename.
 base=fs_path(root);parts=relative_path.split('/');parent=base
 for part in parts[:-1]:
  child=parent/part
  if not child.exists():child.mkdir()
  parent=fs_path(child)
 dest=parent/parts[-1]
 if dest.exists():
  if safe(dest).read_bytes()!=raw:raise ValueError('immutable vector asset conflict')
 else:
  temp=dest.with_name(dest.name+'.'+uuid.uuid4().hex+'.tmp')
  with temp.open('xb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
  temp.rename(dest)
  if safe(dest).read_bytes()!=raw:raise ValueError('vector persisted bytes mismatch')

def _vector_read(root,rel,expected):
 p=safe(fs_path(root).joinpath(*rel.split('/')))
 if not 0<p.stat().st_size<=MAX_BYTES:raise ValueError('vector asset byte limit')
 raw=p.read_bytes()
 if sha(raw)!=expected:raise ValueError('vector asset digest')
 return raw

PREVIEW_KEYS={'original_sha256','preview_sha256','kind','renderer','renderer_version','renderer_binary_sha256','render_options','visual_review','reviewed_by','reviewed_at'}
def validate_preview(meta,original_sha):
 if not isinstance(meta,dict) or set(meta)!=PREVIEW_KEYS:raise ValueError('preview fields')
 for key in ('original_sha256','preview_sha256','renderer_binary_sha256'):
  if not isinstance(meta[key],str) or not re.fullmatch('[a-f0-9]{64}',meta[key]):raise ValueError('preview hash')
 if meta['original_sha256']!=original_sha or meta['kind']!='derived_render' or meta['visual_review']!='rendered_preview_reviewed_with_original_provenance':raise ValueError('preview provenance')
 for key in ('renderer','renderer_version','reviewed_by'):
  if not nonempty(meta[key],512):raise ValueError('preview renderer/reviewer')
 if not isinstance(meta['render_options'],dict) or len(canon(meta['render_options']).encode())>8192:raise ValueError('preview options')
 json.dumps(meta,allow_nan=False)
 if not isinstance(meta['reviewed_at'],str) or datetime.datetime.fromisoformat(meta['reviewed_at']).tzinfo is None:raise ValueError('preview review time')

def vector_prepare(root,original,png_bytes,meta):
 validate_preview(meta,sha(original));env=emf_envelope(original);pinfo=derived_png_info(png_bytes)
 if sha(png_bytes)!=meta['preview_sha256']:raise ValueError('preview bytes SHA')
 orig=dict(original_sha256=sha(original),relative_path=vector_rel(sha(original)),mime_type='image/emf',size_bytes=len(original),envelope_json=canon(env),validator='emf-envelope-opaque-v1')
 preview=dict(original_sha256=sha(original),preview_sha256=sha(png_bytes),relative_path=vector_rel(sha(png_bytes),True),mime_type='image/png',validator='derived-png-metadata-v1',size_bytes=len(png_bytes),width_pixels=pinfo['width_pixels'],height_pixels=pinfo['height_pixels'],provenance_json=canon(meta),provenance_sha256=sha(canon(meta).encode()))
 _vector_write(root,original,orig['relative_path']);_vector_write(root,png_bytes,preview['relative_path'])
 return orig,preview

def vector_verify_pair(root,orig,preview):
 original=_vector_read(root,vector_rel(orig['original_sha256']),orig['original_sha256'])
 if orig!=dict(original_sha256=sha(original),relative_path=vector_rel(sha(original)),mime_type='image/emf',size_bytes=len(original),envelope_json=canon(emf_envelope(original)),validator='emf-envelope-opaque-v1'):raise ValueError('vector original metadata')
 meta=json.loads(preview['provenance_json']);validate_preview(meta,orig['original_sha256'])
 raw=_vector_read(root,vector_rel(preview['preview_sha256'],True),preview['preview_sha256']);info=derived_png_info(raw)
 expected=dict(original_sha256=orig['original_sha256'],preview_sha256=sha(raw),relative_path=vector_rel(sha(raw),True),mime_type='image/png',validator='derived-png-metadata-v1',size_bytes=len(raw),width_pixels=info['width_pixels'],height_pixels=info['height_pixels'],provenance_json=canon(meta),provenance_sha256=sha(canon(meta).encode()))
 if preview!=expected or meta['preview_sha256']!=sha(raw):raise ValueError('vector preview metadata')
 return original,raw

def vector_plan_hash(plan,orig,preview):
 return sha(canon(dict(plan,original_sha256=orig['original_sha256'],preview_sha256=preview['preview_sha256'],provenance_sha256=preview['provenance_sha256'])).encode())

def _vector_insert(d,table,values,keys):
 row=d.execute('select * from '+table+' where '+' and '.join(k+'=?' for k in keys),tuple(values[k] for k in keys)).fetchone()
 if row:
  if dict(row)!=values:raise ValueError('immutable vector row conflict')
 else:
  cols=tuple(values);d.execute('insert into '+table+'('+','.join(cols)+') values('+','.join('?' for _ in cols)+')',tuple(values[k] for k in cols))

def vector_bind(d,root,orig,preview,plan):
 if not d.in_transaction:raise ValueError('vector caller transaction required')
 vector_state(d,True);validate_plan(plan);current_binding(d,plan);validate_research_db(d,plan)
 vector_verify_pair(root,orig,preview)
 if d.execute('select 1 from cp_memo_original_image where memo_id=? and source_id=? and private_locator=?',(plan['memo_id'],plan['source_id'],plan['private_locator'])).fetchone():raise ValueError('raster/vector locator conflict')
 _vector_insert(d,'cp_vector_original_asset',orig,('original_sha256',))
 _vector_insert(d,'cp_vector_preview',preview,('original_sha256','preview_sha256'))
 row=dict(plan,id='vector-'+binding_id(plan),original_sha256=orig['original_sha256'],preview_sha256=preview['preview_sha256'],plan_sha256=vector_plan_hash(plan,orig,preview))
 _vector_insert(d,'cp_memo_vector_image',row,('memo_id','source_id','private_locator'))
 return dict(id=row['id'],original=orig,preview=preview,plan=plan,plan_sha256=row['plan_sha256'])

def _vector_rows(d):
 return {name:[dict(x) for x in d.execute('select * from '+name+' order by '+('original_sha256,preview_sha256' if name=='cp_vector_preview' else 'id' if name=='cp_memo_vector_image' else 'original_sha256'))] for name in VECTOR_TABLES}

def verify_vectors(d,root):
 if not vector_state(d):return {'vector_originals':0,'vector_previews':0,'vector_links':0}
 inventory=_vector_rows(d);originals={x['original_sha256']:x for x in inventory[VECTOR_TABLES[0]]};pairs={}
 for preview in inventory[VECTOR_TABLES[1]]:
  original=originals.get(preview['original_sha256'])
  if original is None:raise ValueError('missing vector original row')
  vector_verify_pair(root,original,preview);pairs[(preview['original_sha256'],preview['preview_sha256'])]=preview
 # Orphan DB rows are corruption. Filesystem orphans from rolled-back transactions are permitted.
 used_pairs=set();used_orig=set()
 for row in inventory[VECTOR_TABLES[2]]:
  plan={k:row[k] for k in PLAN_KEYS};validate_plan(plan);current_binding(d,plan);validate_research_db(d,plan)
  key=(row['original_sha256'],row['preview_sha256']);preview=pairs.get(key)
  if preview is None:raise ValueError('missing vector preview row')
  original=originals[row['original_sha256']]
  if row['id']!='vector-'+binding_id(plan) or row['plan_sha256']!=vector_plan_hash(plan,original,preview):raise ValueError('vector binding checksum')
  if d.execute('select 1 from cp_memo_original_image where memo_id=? and source_id=? and private_locator=?',(plan['memo_id'],plan['source_id'],plan['private_locator'])).fetchone():raise ValueError('cross-table vector locator')
  used_pairs.add(key);used_orig.add(row['original_sha256'])
 if used_pairs!=set(pairs) or used_orig!=set(originals):raise ValueError('unbound vector DB asset')
 return dict(vector_originals=len(originals),vector_previews=len(pairs),vector_links=len(inventory[VECTOR_TABLES[2]]))

def verify_vector_records(d,records,mid,sid):
 if not isinstance(records,list):raise ValueError('vector receipt list')
 if not records:return # Old receipts remain unchanged.
 vector_state(d,True);root=store_root(d);seen=set()
 for r in records:
  if not isinstance(r,dict) or set(r)!={'id','original','preview','plan','plan_sha256'}:raise ValueError('vector receipt fields')
  plan=r['plan'];validate_plan(plan);current_binding(d,plan);validate_research_db(d,plan)
  if plan['memo_id']!=mid or plan['source_id']!=sid or r['id'] in seen:raise ValueError('vector receipt scope/duplicate')
  seen.add(r['id']);o=r['original'];p=r['preview']
  if r['id']!='vector-'+binding_id(plan) or r['plan_sha256']!=vector_plan_hash(plan,o,p):raise ValueError('vector receipt identity')
  row=d.execute('select * from cp_memo_vector_image where id=?',(r['id'],)).fetchone()
  expected=dict(plan,id=r['id'],original_sha256=o['original_sha256'],preview_sha256=p['preview_sha256'],plan_sha256=r['plan_sha256'])
  if row is None or dict(row)!=expected:raise ValueError('vector receipt DB binding')
  for table,descriptor,keys in (('cp_vector_original_asset',o,('original_sha256',)),('cp_vector_preview',p,('original_sha256','preview_sha256'))):
   row=d.execute('select * from '+table+' where '+' and '.join(k+'=?' for k in keys),tuple(descriptor[k] for k in keys)).fetchone()
   if row is None or dict(row)!=descriptor:raise ValueError('vector receipt DB asset')
  vector_verify_pair(root,o,p)
 # No original DOCX or staging dependency.

_legacy_validate_item=validate_item
def validate_item(i):
 _legacy_validate_item(i)
 claims=i.get('original_vector_images',[])
 if not isinstance(claims,list) or len(claims)+len(i.get('original_images',[]))>20:raise ValueError('vector image batch count')
 if claims and i.get('image_record'):raise ValueError('independent single raster record incompatible with vector set')
 seen={canon(c['origin']) for c in i.get('original_images',[])}
 for entry in claims:
  if not isinstance(entry,dict) or set(entry)!={'original','preview'}:raise ValueError('vector claim fields')
  claim=entry['original'];validate_image_claim(claim,i['source']['content_hash']);validate_preview(entry['preview'],claim['image_sha256'])
  if claim['reviewed_body_sha256']!=sha(i['body'].encode()):raise ValueError('vector reviewed body mismatch')
  validate_coverage(claim['research'],i['body']);key=canon(claim['origin'])
  if key in seen:raise ValueError('duplicate vector/raster source locator')
  seen.add(key)

def apply_vectors(d,i,mid,sid):
 claims=i.get('original_vector_images',[])
 if not claims:return []
 vector_state(d,True);root=store_root(d,True);records=[]
 prior=d.execute('select * from cp_memo_vector_image where memo_id=? and source_id=?',(mid,sid)).fetchall()
 if prior and len(prior)!=len(claims):raise ValueError('vector replay set changed')
 for entry in claims:
  claim=entry['original'];meta=entry['preview'];matches=[]
  for row in prior:
   loc=json.loads(row['private_locator']);where=json.loads(loc['location']);origin=claim['origin']
   if row['original_sha256']==claim['image_sha256'] and loc['origin_kind']==origin['kind'] and all(where.get(k)==v for k,v in origin.items() if k!='kind'):matches.append(row)
  if prior:
   if len(matches)!=1:raise ValueError('vector replay original location changed')
   row=matches[0];locator=row['private_locator']
   orig=dict(d.execute('select * from cp_vector_original_asset where original_sha256=?',(row['original_sha256'],)).fetchone())
   preview=dict(d.execute('select * from cp_vector_preview where original_sha256=? and preview_sha256=?',(row['original_sha256'],row['preview_sha256'])).fetchone())
   if preview['provenance_json']!=canon(meta):raise ValueError('vector replay preview changed')
  else:
   original,locator=extract_proven(input_root(d),claim)
   preview_bytes=_vector_read(input_root(d),meta['preview_sha256']+'.png',meta['preview_sha256'])
   orig,preview=vector_prepare(root,original,preview_bytes,meta)
  plan={k:claim[k] for k in ('source_sha256','role','classification_reason','reviewed_by','reviewed_at','reviewed_body_sha256')}
  plan.update(research_json=canon(claim['research']),memo_id=mid,body_id=d.execute('select body_id from cp_memo where id=?',(mid,)).fetchone()[0],source_id=sid,private_locator=locator)
  records.append(vector_bind(d,root,orig,preview,plan))
 return records

def vector_search(d,kind=None,entity_id=None,text=None):
 # Read-only searchable metadata view; no extra mutable search table.
 vector_state(d,True);out=[]
 for row in d.execute('select * from cp_memo_vector_image order by id'):
  for index,e in enumerate(json.loads(row['research_json'])['search']):
   if kind is not None and e['kind']!=kind:continue
   if entity_id is not None and e['id']!=entity_id:continue
   if text is not None and text.casefold() not in canon(e).casefold():continue
   out.append(dict(image_link_id=row['id'],memo_id=row['memo_id'],source_id=row['source_id'],original_sha256=row['original_sha256'],preview_sha256=row['preview_sha256'],ordinal=index,entry=e))
 return out

_legacy_verify_all=verify_all
def verify_all(d,root):
 old=_legacy_verify_all(d,root)
 if vector_state(d):old.update(verify_vectors(d,root))
 return old

_legacy_bundle=bundle
_legacy_restore_bundle=restore_bundle

def _bundle_db(path):
 d=sqlite3.connect(safe(path).as_uri()+'?mode=ro',uri=True);d.row_factory=sqlite3.Row;d.execute('pragma foreign_keys=on')
 if d.execute('pragma integrity_check').fetchone()[0]!='ok':d.close();raise ValueError('bundle SQLite integrity')
 try:
  if d.execute("select 1 from sqlite_master where name='local_archive_identity'").fetchone():
   import archiver
   archiver.target_identity(d)
 except Exception:
  d.close();raise
 return d

def bundle(db_path,root,output):
 src=_bundle_db(db_path)
 try:enabled=vector_state(src)
 finally:src.close()
 if not enabled:return _legacy_bundle(db_path,root,output)
 output=pathlib.Path(output)
 if output.exists():raise ValueError('bundle destination exists')
 safe(output.parent);output.mkdir();snapshot=output/'database.sqlite'
 src=_bundle_db(db_path);dst=sqlite3.connect(snapshot)
 try:src.backup(dst)
 finally:src.close();dst.close()
 d=_bundle_db(snapshot)
 try:
  vector_state(d,True);verify_all(d,root);assets=[dict(x) for x in d.execute('select * from cp_original_image_asset order by image_sha256')]
  vectors=_vector_rows(d);out=output/'images';out.mkdir()
  for row in assets:prepare(out,verify_asset(root,row),row['image_sha256'])
  originals={x['original_sha256']:x for x in vectors[VECTOR_TABLES[0]]}
  for preview in vectors[VECTOR_TABLES[1]]:
   original=originals[preview['original_sha256']];raw,png_raw=vector_verify_pair(root,original,preview)
   actual=vector_prepare(out,raw,png_raw,json.loads(preview['provenance_json']))
   if actual!=(original,preview):raise ValueError('bundle vector changed')
  verify_all(d,out)
 finally:d.close()
 m=dict(protocol='original-and-derived-image-bundle-v2',database_sha256=sha(snapshot.read_bytes()),assets=assets,vectors=vectors)
 raw=canon(m).encode()
 with (output/'manifest.json').open('xb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
 return dict(manifest_sha256=sha(raw),assets=len(assets),vector_originals=len(vectors[VECTOR_TABLES[0]]),vector_previews=len(vectors[VECTOR_TABLES[1]]))

def restore_bundle(bundle_root,expected_manifest_sha256,output):
 bundle_root=safe(bundle_root);mp=safe(bundle_root/'manifest.json')
 if not 0<mp.stat().st_size<=64*1024*1024:raise ValueError('bundle manifest bound')
 raw=mp.read_bytes()
 if sha(raw)!=expected_manifest_sha256:raise ValueError('bundle manifest SHA')
 m=json.loads(raw);db=safe(bundle_root/'database.sqlite')
 if m.get('protocol')=='isolated-original-image-bundle-v1':
  d=_bundle_db(db)
  try:
   if vector_state(d):raise ValueError('v1 bundle cannot represent vector schema')
  finally:d.close()
  return _legacy_restore_bundle(bundle_root,expected_manifest_sha256,output)
 if set(m)!={'protocol','database_sha256','assets','vectors'} or m['protocol']!='original-and-derived-image-bundle-v2' or raw!=canon(m).encode():raise ValueError('v2 bundle shape/protocol')
 db_raw=db.read_bytes()
 if sha(db_raw)!=m['database_sha256']:raise ValueError('v2 bundle DB digest')
 d=_bundle_db(db)
 try:
  vector_state(d,True);verify_all(d,bundle_root/'images')
  actual=[dict(x) for x in d.execute('select * from cp_original_image_asset order by image_sha256')]
  vectors=_vector_rows(d)
  if actual!=m['assets'] or vectors!=m['vectors']:raise ValueError('v2 bundle inventory mismatch')
 finally:d.close()
 output=pathlib.Path(output)
 if output.exists():raise ValueError('restore destination exists')
 safe(output.parent);output.mkdir();out=output/'images';out.mkdir()
 for row in actual:prepare(out,verify_asset(bundle_root/'images',row),row['image_sha256'])
 originals={x['original_sha256']:x for x in vectors[VECTOR_TABLES[0]]}
 for preview in vectors[VECTOR_TABLES[1]]:
  original=originals[preview['original_sha256']];r,p=vector_verify_pair(bundle_root/'images',original,preview)
  vector_prepare(out,r,p,json.loads(preview['provenance_json']))
 with (output/'database.sqlite').open('xb') as f:f.write(db_raw);f.flush();os.fsync(f.fileno())
 d=_bundle_db(output/'database.sqlite')
 try:verified=verify_all(d,out)
 finally:d.close()
 return dict(database_sha256=sha(db_raw),**verified)
# END additive vector originals v2.


def derived_png_info(raw):
 # Preserve renderer PNG bytes exactly. Validate benign bounded metadata without interpreting it.
 if not 0<len(raw)<=MAX_BYTES or raw[:8]!=b'\x89PNG\r\n\x1a\n':raise ValueError('derived PNG signature/size')
 pos=8;kept=[raw[:8]];physical=False;idat=False;idat_ended=False;meta_bytes=0;chunks=0
 while pos<len(raw):
  if pos+12>len(raw):raise ValueError('derived PNG truncated')
  size=struct.unpack_from('>I',raw,pos)[0];typ=raw[pos+4:pos+8];end=pos+12+size;chunks+=1
  if end>len(raw) or chunks>10000:raise ValueError('derived PNG chunk bound')
  data=raw[pos+8:pos+8+size]
  if typ==b'IDAT' and idat_ended:raise ValueError('derived PNG nonconsecutive IDAT')
  if idat and typ!=b'IDAT':idat_ended=True
  if zlib.crc32(typ+data)&0xffffffff!=struct.unpack_from('>I',raw,end-4)[0]:raise ValueError('derived PNG CRC')
  if typ in (b'pHYs',b'tEXt'):
   if pos==8:raise ValueError('derived PNG missing first IHDR')
   meta_bytes+=size
   if meta_bytes>1024*1024:raise ValueError('derived PNG metadata limit')
   if typ==b'pHYs':
    if physical or idat or size!=9 or data[-1] not in (0,1):raise ValueError('derived PNG physical metadata')
    physical=True
   else:
    parts=data.split(b'\x00')
    if len(parts)!=2 or not 1<=len(parts[0])<=79 or any(c<32 or 126<c<161 for c in parts[0]) or parts[0].startswith(b' ') or parts[0].endswith(b' ') or b'  ' in parts[0]:raise ValueError('derived PNG text metadata')
  else:kept.append(raw[pos:end])
  if typ==b'IDAT':idat=True
  if typ==b'IEND' and end!=len(raw):raise ValueError('derived PNG trailing bytes')
  pos=end
 info=png(b''.join(kept));info['size_bytes']=len(raw);info['validator']='derived-png-metadata-v1'
 return info

# Original PNG metadata v2. Pure validation; original bytes and legacy labels remain unchanged.
import collections
ORIGINAL_PNG_METADATA_VALIDATOR='original-png-metadata-v2'
def original_png_metadata(raw):
 info=derived_png_info(raw)
 return dict(info,validator=ORIGINAL_PNG_METADATA_VALIDATOR)

_previous_image_info=image_info
def image_info(raw):
 if raw.startswith(b'\x89PNG\r\n\x1a\n'):
  try:return png(raw)
  except ValueError:return original_png_metadata(raw)
 return _previous_image_info(raw)

def verify_asset(root,row):
 if row['relative_path']!=relative(row['image_sha256'],row['mime_type']):raise ValueError('asset path differs')
 p=safe(fs_path(root).joinpath(*row['relative_path'].split('/')))
 if not 0<p.stat().st_size<=MAX_BYTES:raise ValueError('stored original byte limit')
 raw=p.read_bytes()
 if sha(raw)!=row['image_sha256']:raise ValueError('stored original SHA')
 validators={'png-subset-v1':png,'jpeg-baseline-gdi-v1':jpeg,ORIGINAL_PNG_METADATA_VALIDATOR:original_png_metadata}
 if row['validator'] not in validators:raise ValueError('unknown original validator')
 info=validators[row['validator']](raw)
 if any(row[k]!=v for k,v in info.items()):raise ValueError('stored original descriptor mismatch')
 return raw

def original_png_schema_state(d):
 old=SCHEMA.split(';',1)[0].strip()
 new=old.replace('CREATE TABLE cp_original_image_asset(','CREATE TABLE "cp_original_image_asset"(',1).replace("'png-subset-v1','jpeg-baseline-gdi-v1'","'png-subset-v1','jpeg-baseline-gdi-v1','original-png-metadata-v2'")
 row=d.execute("select sql from sqlite_master where type='table' and name='cp_original_image_asset'").fetchone()
 if row is None:raise ValueError('original asset schema missing')
 if row[0]==old:return 'legacy'
 if row[0]==new:return 'original-png-metadata-v2'
 raise ValueError('unreviewed original asset schema')

def install_original_png_metadata(d,failpoint=None):
 # Dedicated reviewed migration only. No commit, FK mode changes, or implicit upgrade here.
 if not d.in_transaction or d.execute('pragma foreign_keys').fetchone()[0]!=0:raise ValueError('explicit FK-off migration transaction required')
 if original_png_schema_state(d)!='legacy':raise ValueError('PNG metadata schema already upgraded')
 table='cp_original_image_asset';temp='cp_original_image_asset_metadata_upgrade'
 if d.execute('select 1 from sqlite_master where name=?',(temp,)).fetchone():raise ValueError('stale PNG upgrade table')
 old=d.execute('select sql from sqlite_master where type="table" and name=?',(table,)).fetchone()[0]
 related=[tuple(x) for x in d.execute('select type,name,sql from sqlite_master where tbl_name=? and sql is not null and type<>"table" order by type,name',(table,))]
 if {x[1] for x in related}!={'guard_cp_original_image_asset_update','guard_cp_original_image_asset_delete'}:raise ValueError('unexpected original asset dependencies')
 rows=collections.Counter(map(tuple,d.execute('select * from '+table)))
 fks={r[0]:list(map(tuple,d.execute('pragma foreign_key_list("'+r[0].replace('"','""')+'")'))) for r in d.execute("select name from sqlite_master where type='table'")}
 ddl=old.replace('CREATE TABLE '+table+'(','CREATE TABLE '+temp+'(',1).replace("'png-subset-v1','jpeg-baseline-gdi-v1'","'png-subset-v1','jpeg-baseline-gdi-v1','original-png-metadata-v2'")
 d.execute(ddl);d.execute('insert into '+temp+' select * from '+table)
 if collections.Counter(map(tuple,d.execute('select * from '+temp)))!=rows:raise ValueError('PNG upgrade copied rows differ')
 if failpoint=='after_copy':raise RuntimeError('injected PNG after_copy')
 d.execute('drop table '+table)
 if failpoint=='after_drop':raise RuntimeError('injected PNG after_drop')
 d.execute('alter table '+temp+' rename to '+table)
 for _,_,sql in related:d.execute(sql)
 if failpoint=='after_rename':raise RuntimeError('injected PNG after_rename')
 if collections.Counter(map(tuple,d.execute('select * from '+table)))!=rows:raise ValueError('PNG upgrade old rows differ')
 after={r[0]:list(map(tuple,d.execute('pragma foreign_key_list("'+r[0].replace('"','""')+'")'))) for r in d.execute("select name from sqlite_master where type='table'")}
 if after!=fks or d.execute('pragma foreign_key_check').fetchone():raise ValueError('PNG upgrade FK definitions/integrity differ')
 if original_png_schema_state(d)!='original-png-metadata-v2':raise ValueError('PNG upgrade postcondition')

# original-gAMA-bounded-v1: original metadata category, exact code-pin versioned.
# Never changes original bytes, legacy labels/schema, derived PNG, or vector policy.
def original_png_gamma_info(raw):
 if not 0<len(raw)<=MAX_BYTES or raw[:8]!=b'\x89PNG\r\n\x1a\n':raise ValueError('original gAMA PNG signature/size')
 pos=8;count=0;seen=set();compressed=[];idat=False;idat_ended=False;ended=False;gamma=None;srgb=None;meta_bytes=0
 while pos<len(raw):
  if pos+12>len(raw):raise ValueError('original gAMA PNG truncated')
  size=struct.unpack_from('>I',raw,pos)[0];typ=raw[pos+4:pos+8];end=pos+size+12;count+=1
  if size>MAX_BYTES or end>len(raw) or count>10000:raise ValueError('original gAMA PNG chunk budget')
  content=raw[pos+8:pos+8+size]
  if zlib.crc32(typ+content)&0xffffffff!=struct.unpack_from('>I',raw,end-4)[0]:raise ValueError('original gAMA PNG CRC')
  if typ not in (b'IHDR',b'sRGB',b'gAMA',b'pHYs',b'IDAT',b'IEND'):raise ValueError('original gAMA PNG unsupported chunk')
  if count==1 and typ!=b'IHDR':raise ValueError('original gAMA PNG first header')
  if typ!=b'IDAT' and typ in seen:raise ValueError('original gAMA PNG duplicate singleton')
  if idat and typ!=b'IDAT':idat_ended=True
  if typ==b'IHDR':
   if count!=1 or size!=13:raise ValueError('original gAMA PNG header')
   w,h,depth,color,comp,filt,inter=struct.unpack('>IIBBBBB',content)
   if not w or not h or w*h>MAX_PIXELS or depth!=8 or color not in (0,2,6) or (comp,filt,inter)!=(0,0,0):raise ValueError('original gAMA PNG pixel layout/budget')
  elif typ in (b'sRGB',b'gAMA',b'pHYs'):
   if idat:raise ValueError('original gAMA PNG metadata after IDAT')
   meta_bytes+=size
   if meta_bytes>1024*1024:raise ValueError('original gAMA PNG metadata budget')
   if typ==b'sRGB':
    if size!=1 or content[0]>3:raise ValueError('original gAMA PNG sRGB intent/length')
    srgb=content[0]
   elif typ==b'gAMA':
    if size!=4:raise ValueError('original gAMA PNG gamma length')
    gamma=struct.unpack('>I',content)[0]
    if not 1<=gamma<=0x7fffffff:raise ValueError('original gAMA PNG gamma range')
   else:
    if size!=9:raise ValueError('original gAMA PNG physical length')
    x,y,unit=struct.unpack('>IIB',content)
    if x>0x7fffffff or y>0x7fffffff or unit not in (0,1):raise ValueError('original gAMA PNG physical range/unit')
  elif typ==b'IDAT':
   if idat_ended:raise ValueError('original gAMA PNG nonconsecutive IDAT')
   compressed.append(content);idat=True
  else:
   if size or not idat or end!=len(raw):raise ValueError('original gAMA PNG end/trailing')
   ended=True
  seen.add(typ);pos=end
 if not ended or gamma is None:raise ValueError('original gAMA PNG incomplete/missing gamma')
 if srgb is not None and gamma!=45455:raise ValueError('original gAMA PNG sRGB gamma mismatch')
 channels={0:1,2:3,6:4}[color];stride=1+w*channels;expected=stride*h;decoder=zlib.decompressobj()
 try:pixels=decoder.decompress(b''.join(compressed),expected+1)
 except zlib.error as e:raise ValueError('original gAMA PNG compressed stream invalid') from e
 if len(pixels)!=expected or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:raise ValueError('original gAMA PNG compressed image size')
 if any(pixels[y*stride]>4 for y in range(h)):raise ValueError('original gAMA PNG filter')
 return dict(mime_type='image/png',size_bytes=len(raw),width_pixels=w,height_pixels=h,validator=ORIGINAL_PNG_METADATA_VALIDATOR)

_previous_original_png_metadata=original_png_metadata

def original_png_metadata(raw):
 try:return _previous_original_png_metadata(raw)
 except ValueError:return original_png_gamma_info(raw)
