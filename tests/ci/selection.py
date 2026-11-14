#!/usr/bin/env python3
"""Conservative classification with source-bound production plan selection."""
import argparse,hashlib,json,pathlib
DOCS={'README.md','DEVELOPMENT.md'}
def classify(changes,event='pull_request',complete=True):
 if event!='pull_request' or not complete or not changes:return 'full','non-PR or incomplete/empty diff'
 if all(row.get('status')=='M' and row.get('path') in DOCS for row in changes):return 'docs','only registered explanatory documents changed'
 return 'full','code, rename, delete, snippets, dependency or unknown path'
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--changes',type=pathlib.Path,required=True);p.add_argument('--event',default='pull_request');p.add_argument('--output',type=pathlib.Path,required=True);a=p.parse_args()
 try:
  value=json.loads(a.changes.read_text());mode,reason=classify(value['changes'],a.event,value.get('complete') is True)
 except (ValueError,KeyError,TypeError,OSError):mode,reason='full','diff unavailable or invalid'
 a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps({'schemaVersion':1,'classification':mode,'reason':reason,'activation':'classifier preview only; production plans verify exact Git revisions','effectiveSelection':'full'},indent=2)+'\n')

def source_selection(root,event_path,event_name):
 """Use only complete Git bytes for the event's exact base/head commits."""
 import subprocess,re
 result={'selection':'full','selectionReason':'non-PR or unavailable complete source diff','baseSHA':None,'headSHA':None,'changedPathsDigest':None}
 if event_name!='pull_request' or not event_path:return result
 try:
  pr=json.loads(pathlib.Path(event_path).read_text())['pull_request'];base=pr['base']['sha'];head=pr['head']['sha'];result.update(baseSHA=base,headSHA=head)
  if not all(isinstance(v,str) and re.fullmatch('[0-9a-f]{40}',v) for v in (base,head)):return result
  for commit in (base,head):
   subprocess.run(['git','cat-file','-e',commit+'^{commit}'],cwd=root,check=True,capture_output=True,timeout=20)
  tested=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True,timeout=20).strip()
  parents=subprocess.check_output(['git','show','-s','--format=%P','HEAD'],cwd=root,text=True,timeout=20).split()
  if tested!=head and parents!=[base,head]:raise RuntimeError('tested checkout differs from the event head or exact merge parents')
  for commit in (base,head):
   subprocess.run(['git','merge-base','--is-ancestor',commit,'HEAD'],cwd=root,check=True,capture_output=True,timeout=20)
  raw=subprocess.check_output(['git','diff','--no-renames','--name-status','-z',base,head,'--'],cwd=root,timeout=20)
  if len(raw)>1024*1024 or (raw and not raw.endswith(b'\0')):return result
  fields=raw.decode('utf-8').split('\0')[:-1]
  if len(fields)%2:return result
  changes=[{'status':fields[n],'path':fields[n+1]} for n in range(0,len(fields),2)]
  mode,reason=classify(changes,event_name,True);result.update(selection=mode,selectionReason=reason,changedPathsDigest=hashlib.sha256(raw).hexdigest())
 except (OSError,ValueError,KeyError,TypeError,UnicodeError,subprocess.SubprocessError):pass
 return result
