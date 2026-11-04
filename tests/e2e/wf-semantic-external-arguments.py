#!/usr/bin/env python3
"""External required-input and supplied-value checks consume projected YAML values."""
from pathlib import Path
import importlib.util,json,sys,traceback
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('wire',HERE/'wf-audit-gaps.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
caller='apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: caller, namespace: argo, annotations: {value: &value hello}}\nspec:\n  workflowTemplateRef: {name: library}\n  entrypoint: worker\n'
library='apiVersion: argoproj.io/v1alpha1\nkind: WorkflowTemplate\nmetadata: {name: library, namespace: argo}\nspec:\n  templates:\n    - name: first\n      inputs: &inputs {parameters: [{name: message}]}\n      container: {image: alpine}\n    - name: worker\n      inputs: *inputs\n      container: {image: alpine}\n'
def norm(xs):return sorted(json.dumps(x,sort_keys=True) for x in xs)
for name,args,missing in [('external-aliased-required-input','',True),('external-alias-supplied-value','  arguments: {parameters: [{name: message, value: *value}]}\n',False),('external-alias-value-direct-input','  arguments: {parameters: [{name: message, value: *value}]}\n',False)]:
 callee=library.replace('inputs: *inputs','inputs: {parameters: [{name: message}]}') if name=='external-alias-value-direct-input' else library
 text=caller+args;dest,work=m.setup(name,{'caller.yaml':text,'library.yaml':callee});path=work/'caller.yaml';lp=work/'library.yaml';a=m.pos(text,'entrypoint: worker',offset=12);z=dict(a);z['character']+=6;expected=[{'uri':path.as_uri(),'range':{'start':a,'end':z},'code':'WF_ARGUMENT_MISSING','source':'argo-workflows','severity':'Error','positionKind':'exact'}] if missing else [];target=m.pos(callee,'name: worker',offset=6);end=dict(target);end['character']+=6;definition=[{'uri':lp.as_uri(),'range':{'start':target,'end':end}}];m.save(dest/'expected.json',{'diagnostics':expected,'definition':definition,'filesAnalyzed':2,'resourcesAnalyzed':2,'exitCode':2});client=None;checks={}
 try:
  code,report=m.cli(dest,work);checks['completeCLI']=norm([{k:d[k] for k in ['uri','range','code','source','severity','positionKind']} for d in report['diagnostics']])==norm(expected);checks['envelope']=code==int(any(d['severity']=='Error' for d in expected)) and report['filesAnalyzed']==2 and report['resourcesAnalyzed']==2 and report['analysisComplete'] is True and report['configurationErrors']==[] and report['notEvaluated']==[];client=m.LSP(dest,work);ds=client.open(path,text);checks['publication']=norm([{k:d[k] for k in ['range','code','source','severity']} for d in ds])==norm([{k:d[k] for k in ['range','code','source']}|{'severity':1} for d in expected]);r=client.request('textDocument/definition',{'textDocument':{'uri':path.as_uri()},'position':a});checks['exactTemplateOrigin']=r.get('result')==definition and 'error' not in r;checks['unchangedBytes']=path.read_bytes()==text.encode() and lp.read_bytes()==callee.encode();m.result(name,checks,{'cli':report,'publication':ds,'definition':r})
 except Exception:
  (dest/'error.txt').write_text(traceback.format_exc());m.result(name,checks|{'completed':False})
 finally:
  if client:client.close()
m.save(m.OUT/'manifest.json',{'command':sys.argv,'nativeSha256':m.sha(m.BINARY),'testSha256':m.sha(Path(__file__))});sys.exit(0 if len(m.RESULTS)==3 and all(r['pass'] for r in m.RESULTS) else 1)
