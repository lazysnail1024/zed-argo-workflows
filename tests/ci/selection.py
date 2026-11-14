#!/usr/bin/env python3
"""Conservative name-status classifier; activation follows hosted gate validation."""
import argparse,hashlib,json,pathlib
DOCS={'README.md','DEVELOPMENT.md','docs/distribution.md'}
def classify(changes,event='pull_request',complete=True):
 if event!='pull_request' or not complete or not changes:return 'full','non-PR or incomplete/empty diff'
 if all(row.get('status')=='M' and row.get('path') in DOCS for row in changes):return 'docs','only registered explanatory documents changed'
 return 'full','code, rename, delete, snippets, dependency or unknown path'
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--changes',type=pathlib.Path,required=True);p.add_argument('--event',default='pull_request');p.add_argument('--output',type=pathlib.Path,required=True);a=p.parse_args()
 try:
  value=json.loads(a.changes.read_text());mode,reason=classify(value['changes'],a.event,value.get('complete') is True)
 except (ValueError,KeyError,TypeError,OSError):mode,reason='full','diff unavailable or invalid'
 a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps({'schemaVersion':1,'classification':mode,'reason':reason,'activation':'observation-only until hosted gate and reuse acceptance','effectiveSelection':'full'},indent=2)+'\n')
