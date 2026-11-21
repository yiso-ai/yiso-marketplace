"""Narrow read-only navigation-bookmark validator. Does not edit/repack source."""
import re
import image_assets as im
W='{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
MARKS={W+'bookmarkStart',W+'bookmarkEnd'}
def need(x,msg):
 if not x:raise ValueError('bookmark qualification: '+msg)
def inspect(z,doc,lo,hi):
 im._docx_inventory(z)
 body=doc.find(W+'body');need(body is not None,'body absent');ps=body.findall(W+'p');need(type(lo) is int and type(hi) is int and 0<=lo<=hi<len(ps),'range')
 roots={'word/document.xml':doc}
 for part in z.namelist():
  if (part.endswith('.xml') or part.endswith('.rels')) and part!='word/document.xml':roots[part]=im._docx_xml(z,part)
 nodes=list(doc.iter());order={n:i for i,n in enumerate(nodes)};parents={n:p for p in doc.iter() for n in p};starts={};ends={};names=set();stack=[];pairs=[];markers=[]
 for part,root in roots.items():
  for n in root.iter():
   local=n.tag.split('}')[-1]
   need(local not in {'hyperlink','fldSimple','instrText','fldChar'},'field/hyperlink requires separate semantic qualification')
   if local in ['bookmarkStart','bookmarkEnd']:
    need(part=='word/document.xml' and n.tag in MARKS,'non-main/nonstandard bookmark')
 for n in nodes:
  if n.tag not in MARKS:continue
  parent=parents.get(n);need(parent is not None and parent.tag in {W+'p',W+'body'},'unsupported marker parent')
  need(len(n)==0 and not(n.text or '').strip() and not(n.tail or '').strip(),'marker contains text/children')
  attrs={W+'id',W+'name'} if n.tag==W+'bookmarkStart' else {W+'id'};need(set(n.attrib)==attrs,'marker attributes outside strict subset')
  ident=n.get(W+'id');need(re.fullmatch('[0-9]+',ident or '') is not None,'invalid ID')
  if n.tag==W+'bookmarkStart':
   name=n.get(W+'name');need(re.fullmatch(r'_Hlk[0-9]+|_GoBack',name or '') is not None,'unqualified bookmark name class')
   need(ident not in starts and name not in names,'duplicate bookmark ID/name');starts[ident]=n;names.add(name);stack.append(ident)
  else:
   need(ident in starts and ident not in ends,'missing/duplicate start/end');need(stack and stack[-1]==ident,'crossed or reversed bookmark range');stack.pop();ends[ident]=n;pairs.append((ident,starts[ident],n))
  markers.append(n)
 need(not stack and set(starts)==set(ends),'unmatched bookmark')
 for part,root in roots.items():
  for n in root.iter():
   if n.tag in MARKS:continue
   text=' '.join(list(n.attrib.values())+[n.text or '',n.tail or ''])
   need(not any(name in text for name in names),'bookmark name referenced outside marker')
 first=order[ps[lo]];last=max(order[n] for n in ps[hi].iter());review=[]
 for ident,start,end in pairs:
  a,b=order[start],order[end];overlap=a<=last and b>=first
  need(not overlap or (first<=a<b<=last),'bookmark crosses selected event boundary')
  def position(n):
   par=parents[n]
   if par.tag==W+'p':return {'paragraph':ps.index(par),'parent':'p'}
   before=[p for p in ps if order[p]<order[n]];return {'after_paragraph':ps.index(before[-1]) if before else -1,'parent':'body'}
  review.append({'id':ident,'name':start.get(W+'name'),'start':position(start),'end':position(end),'intersects_selected_event':overlap,'zero_visible_content':True})
 return {'policy':'nonvisual-navigation-bookmarks-v1','pairs':review,'selected_event':[lo,hi],'xml_parts_scanned':sorted(roots),'fields_hyperlinks_or_references':0,'source_modified':False},set(markers)
