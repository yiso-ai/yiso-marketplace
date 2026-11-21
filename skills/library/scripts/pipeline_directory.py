"""Read only an explicitly supplied local text directory into capture-v1. No connectors."""
import argparse, os, stat, datetime
from pathlib import Path
import pipeline as p
from local_sources import safe_source

def inventory(root,max_files):
    result={}
    for base,dirs,files in os.walk(root,followlinks=False):
        for name in sorted(dirs+files):
            path=Path(base)/name;info=path.lstat();p.require(not stat.S_ISLNK(info.st_mode),'symlink rejected')
            resolved=safe_source(path) if os.name=='nt' else path.resolve()
            p.require(resolved.is_relative_to(root),'path escaped directory')
            if stat.S_ISDIR(info.st_mode):continue
            p.require(stat.S_ISREG(info.st_mode),'nonregular file rejected');p.require(path.suffix.lower() in ('.txt','.md'),'text-only directory required; unsupported file present')
            result[path.relative_to(root).as_posix()]=(info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns)
            p.require(len(result)<=max_files,'directory file limit exceeded')
    return dict(sorted(result.items()))

def secure_open(root,relative):
    if os.name=='nt':
        path=safe_source(root/relative)
        p.require(path.is_relative_to(root),'path escaped directory')
        fd=os.open(path,os.O_RDONLY|os.O_BINARY)
        try:
            current=safe_source(root/relative)
            p.require(current==path and os.path.samestat(os.fstat(fd),current.stat()),'file replaced before read')
        except BaseException:
            os.close(fd)
            raise
        return fd
    # Anchor traversal to directory descriptors, rejecting symlinks in every component.
    fd=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        parts=Path(relative).parts
        for part in parts[:-1]:
            next_fd=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd);os.close(fd);fd=next_fd
        return os.open(parts[-1],os.O_RDONLY|os.O_NOFOLLOW,dir_fd=fd)
    finally:os.close(fd)

def directory_namespace(directory):return 'local-directory:'+p.digest(str(Path(directory).resolve(strict=True)))

def scan(directory,config,capture_id,start,end,category,institution,display_author,page_size=50,max_files=10000,max_bytes=2000000):
    original=Path(directory);p.require(not original.is_symlink(),'symlink root rejected')
    root=safe_source(original) if os.name=='nt' else original.resolve(strict=True)
    p.require(root.is_dir(),'directory required');p.require(config['source_namespace']==directory_namespace(root),'directory root namespace mismatch');p.require(category in ('internal','sellside','news'));p.require(1<=page_size<=500)
    a,b=p.utc(start),p.utc(end);before=inventory(root,max_files);items=[]
    for relative,identity in before.items():
        timestamp=datetime.datetime.fromtimestamp(identity[3]/1e9,datetime.timezone.utc)
        if not a<=timestamp<b:continue
        p.require(identity[2]<=max_bytes,'text file exceeds byte limit');path=root/relative
        fd=secure_open(root,relative)
        try:
            info=os.fstat(fd);p.require((info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns)==identity,'file changed before read')
            with os.fdopen(fd,'rb',closefd=False) as f:raw=f.read(max_bytes+1)
            p.require(len(raw)<=max_bytes,'text file exceeds byte limit');content=raw.decode('utf-8');p.text(content)
            p.require(not any(ord(c)<32 and c not in '\n\r\t' for c in content),'binary/control bytes rejected')
            p.require('![' not in content and '<img' not in content.lower(),'image-bearing markdown outside text-only scope')
            after=os.fstat(fd);p.require((after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns)==identity,'file changed during read')
        finally:os.close(fd)
        items.append({'source_key':relative,'source_time_utc':timestamp.isoformat(timespec='microseconds').replace('+00:00','Z'),'status':'captured','reason':None,'metadata_origin':'file_label','title':Path(relative).stem,'category':category,'material_date':None,'institution':institution,'display_author':display_author,'content':content,'content_sha256':p.digest(content)})
    p.require(before==inventory(root,max_files),'directory changed during scan')
    snapshot='directory-'+p.digest(p.canonical([[x['source_key'],x['content_sha256'],x['source_time_utc']] for x in items]));pages=[]
    for index,offset in enumerate(range(0,max(1,len(items)),page_size)):
        pages.append({'index':index,'request_cursor':None if index==0 else 'page-'+str(index),'next_cursor':'page-'+str(index+1) if offset+page_size<len(items) else None,'snapshot_id':snapshot,'total_items':len(items),'items':items[offset:offset+page_size]})
    result={'format':'cp-source-capture-v1','capture_id':capture_id,'scope_id':config['scope_id'],'account_namespace':config['account_namespace'],'source_namespace':config['source_namespace'],'window_start_utc':start,'window_end_utc':end,'snapshot_id':snapshot,'pages':pages};p.validate_capture(result,config);return result

if __name__=='__main__':
    cli=argparse.ArgumentParser(description=__doc__);cli.add_argument('action',nargs='?',choices=['scan','namespace'],default='scan');cli.add_argument('--directory',required=True);cli.add_argument('--scope-config');cli.add_argument('--capture-id');cli.add_argument('--start');cli.add_argument('--end');cli.add_argument('--category',choices=['internal','sellside','news']);cli.add_argument('--institution');cli.add_argument('--display-author');cli.add_argument('--page-size',type=int,default=50);cli.add_argument('--output');a=cli.parse_args()
    if a.action=='namespace':print(p.canonical({'source_namespace':directory_namespace(a.directory)}));raise SystemExit(0)
    p.require(all([a.scope_config,a.capture_id,a.start,a.end,a.category,a.output]),'scan requires scope-config, capture-id, start, end, category, output')
    result=scan(a.directory,p.starter.load_json(Path(a.scope_config).read_text()),a.capture_id,a.start,a.end,a.category,a.institution,a.display_author,a.page_size);Path(a.output).write_text(p.canonical(result));print(p.canonical({'capture_written':str(Path(a.output)),'items':sum(len(x['items']) for x in result['pages']),'pages':len(result['pages'])}))
