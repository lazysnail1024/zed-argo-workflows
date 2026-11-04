#!/usr/bin/env python3
"""Shared anchor origins refuse edits while an unrelated exact sibling remains editable."""
from pathlib import Path
import importlib.util,json,sys,traceback
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('wire',HERE/'wf-audit-gaps.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
text='apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: shared}\nspec:\n  entrypoint: first\n  templates:\n    - name: first\n      container: &box {image: alpine, imagePullPolcy: Always}\n    - name: second\n      container: *box\n    - name: third\n      container: {image: alpine, imagePullPolcy: Always}\n'
def span(needle,occurrence=0):
 a=m.pos(text,needle,occurrence);z=dict(a);z['character']+=len(needle);return {'start':a,'end':z}
def norm(items):return sorted(json.dumps(x,sort_keys=True) for x in items)
name='shared-anchor-edits';dest,work=m.setup(name,{'case.yaml':text});path=work/'case.yaml';uri=path.as_uri();client=None;checks={};responses=[]
expected=[{'range':span('imagePullPolcy'),'code':'SCHEMA_UNKNOWN_FIELD','positionKind':'derived'},{'range':span('*box'),'code':'SCHEMA_UNKNOWN_FIELD','positionKind':'derived'},{'range':span('imagePullPolcy',1),'code':'SCHEMA_UNKNOWN_FIELD','positionKind':'exact'}]
m.save(dest/'expected.json',{'diagnostics':expected,'sharedCompletion':[],'sharedActions':[],'exactEdit':{'range':span('imagePullPolcy',1),'newText':'imagePullPolicy'},'afterBytes':text.replace('container: {image: alpine, imagePullPolcy:', 'container: {image: alpine, imagePullPolicy:')})
try:
 code,report=m.cli(dest,work);checks['fullCLI']=code==0 and report['filesAnalyzed']==1 and report['resourcesAnalyzed']==1 and report['analysisComplete'] is True and report['configurationErrors']==[] and norm([{k:d[k] for k in ['range','code','positionKind']} for d in report['diagnostics']])==norm(expected) and all(d['severity']=='Warning' and d['source']=='argo-workflows' and d['uri']==uri for d in report['diagnostics']) and report['notEvaluated']==[]
 client=m.LSP(dest,work);ds=client.open(path,text);checks['fullPublication']=norm([{k:d[k] for k in ['range','code']} for d in ds])==norm([{k:d[k] for k in ['range','code']} for d in expected]) and all(d['severity']==2 and d['source']=='argo-workflows' for d in ds)
 for index in [0,1]:
  p=span('imagePullPolcy',index)['start'];r=client.request('textDocument/completion',{'textDocument':{'uri':uri},'position':p});responses.append(r);checks['completion'+str(index)]=r.get('result')==[] if index==0 else any(x.get('label')=='imagePullPolicy' for x in r.get('result',[]))
  r=client.request('textDocument/codeAction',{'textDocument':{'uri':uri},'range':span('imagePullPolcy',index),'context':{'diagnostics':[]}});responses.append(r);actions=r.get('result',[])
  if index==0:checks['sharedActions']=actions==[]
  else:
   exact=[a for a in actions if a.get('title')=='Replace field with imagePullPolicy'];checks['exactAction']=len(exact)==1
   if exact:
    resolved=client.request('codeAction/resolve',exact[0]);responses.append(resolved);edited=m.apply(text,resolved['result']['edit'],uri,1);checks['exactUnrelatedBytes']=edited==text.replace('container: {image: alpine, imagePullPolcy:', 'container: {image: alpine, imagePullPolicy:')
    path.write_text(edited);code,after=m.cli(dest,work,'after');checks['afterCLI']=code==0 and after['filesAnalyzed']==1 and after['resourcesAnalyzed']==1 and norm([{k:d[k] for k in ['range','code','positionKind']} for d in after['diagnostics']])==norm(expected[:2]) and after['notEvaluated']==report['notEvaluated'] and after['configurationErrors']==[]
 m.result(name,checks,{'responses':responses,'cli':report,'publication':ds})
except Exception:
 (dest/'error.txt').write_text(traceback.format_exc());m.result(name,checks|{'completed':False},{'responses':responses})
finally:
 if client:client.close()
m.save(m.OUT/'manifest.json',{'command':sys.argv,'binarySha256':m.sha(m.BINARY),'testSha256':m.sha(Path(__file__))});sys.exit(0 if len(m.RESULTS)==1 and all(r['pass'] for r in m.RESULTS) else 1)
