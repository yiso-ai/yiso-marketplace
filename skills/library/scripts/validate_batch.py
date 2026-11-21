#!/usr/bin/env python3
"""Offline structural preflight only; never reads sources or writes production."""
import json,sys,re
from datetime import date

def validate(b):
    errors=[]
    def need(ok,msg):
        if not ok: errors.append(msg)
    need(b.get('mode') in ('draft','publish'),'mode must be draft/publish')
    if b.get('mode')=='publish':
        for k in ('batch_id','state_file_id','expected_state_version','lock_owner'):
            need(k in b and b[k] is not None and b[k]!='',f'missing {k}')
    mats=b.get('materials',[]); ids=[x.get('id') for x in mats]
    need(all(ids) and len(ids)==len(set(ids)),'material IDs missing/duplicate')
    for m in mats:
        p=str(m.get('id')); kind=m.get('kind')
        need(kind in ('internal','sellside','news'),p+': invalid kind')
        need(bool(m.get('source_key')) and bool(m.get('company_id')),p+': source/company required')
        try: date.fromisoformat(m['date'])
        except (ValueError,KeyError,TypeError): errors.append(p+': valid material date required')
        need(bool(m.get('institution')),p+': institution required')
        need(not m.get('display_analysts'),p+': analyst display forbidden')
        need(bool(m.get('title')) and bool(m.get('body')),p+': title/body required')
        heading=f"{m.get('date')}　{m.get('title')} | {m.get('institution')}"
        need(m.get('heading')==heading,p+': heading format')
        if kind=='internal': need(m.get('body')==m.get('original_excerpt'),p+': internal original changed')
        need(not re.search(r'https?://|!\[|<img|来源链接[：:]|文件路径[：:]',m.get('body','')),p+': display clutter/image/source URL')
    companies=set(b.get('companies',[])); topics=set(b.get('topics',[])); forward=set(); reverse=set()
    for m in mats: need(m.get('company_id') in companies, str(m.get('id'))+': company endpoint missing')
    for e in b.get('company_topic_edges',[]):
        c,t=e.get('company'),e.get('topic'); key=(c,t)
        need(key not in forward,'duplicate company-topic edge'); forward.add(key)
        need(c in companies and t in topics,'edge endpoint missing')
        need(e.get('evidence_id') in ids and bool(e.get('reason')),'edge evidence/reason missing')
        need(e.get('basis') in ('fact','source_view'),'unverified edge cannot publish')
    for e in b.get('topic_company_edges',[]):
        key=(e.get('company'),e.get('topic'))
        need(key not in reverse,'duplicate reverse edge'); reverse.add(key)
    need(forward==reverse,'bidirectional edges inconsistent')
    return errors
if __name__=='__main__':
    try:
        errors=validate(json.load(open(sys.argv[1],encoding='utf-8')))
        print(json.dumps({'valid':not errors,'errors':errors,'scope':'structural checks only; semantic and remote verification required'},ensure_ascii=False))
        sys.exit(bool(errors))
    except (OSError,ValueError,IndexError,TypeError) as e:
        print(json.dumps({'valid':False,'error':str(e)}));sys.exit(2)
