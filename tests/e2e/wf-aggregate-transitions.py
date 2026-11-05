#!/usr/bin/env python3
"""Same-process target activation and buffer recovery for aggregate globals."""
from pathlib import Path
import importlib.util,json,sys,traceback
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('wire',HERE/'wf-audit-gaps.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
BASE='complete domain rules, version acceptance, and resource budgets are not yet implemented'
text='apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: local}\nspec:\n  entrypoint: main\n  templates:\n    - name: main\n      container: {image: alpine, args: ["{{workflow.parameters}}"]}\n---\napiVersion: argoproj.io/v1alpha1\nkind: WorkflowTemplate\nmetadata: {name: public}\nspec:\n  entrypoint: main\n  templates:\n    - name: main\n      container: {image: alpine, args: ["{{workflow.parameters.absent}}"]}\n'
sibling='---\napiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: stable-json}\nspec:\n  arguments: {parameters: [{name: json, value: literal}]}\n  entrypoint: main\n  templates:\n    - name: main\n      container: {image: alpine, args: ["{{workflow.parameters.json}}"]}\n'
text+=sibling
fixed=text.replace('  entrypoint: main\n','  entrypoint: main\n  arguments: {parameters: [{name: absent, value: literal}]}\n',2)
def expected(value,version):
 rows=[]
 for tag,message in [('workflow.parameters','workflow parameters aggregate is not available'),('workflow.parameters.absent','workflow parameter is not declared: absent')]:
  if value==fixed:continue
  if tag.endswith('.absent') and version!='3.4.1':continue
  token='"{{'+tag+'}}"';start=m.pos(value,token);rows.append({'range':{'start':start,'end':dict(start,character=start['character']+len(token))},'code':'WF_REFERENCE_SCOPE','message':message,'source':'argo-workflows','severity':1})
 return rows
norm=lambda xs:sorted(json.dumps(x,sort_keys=True) for x in xs)
dest,work=m.setup('activation-recovery',{'case.yaml':text},'schemaVersion: 1\ntargetVersion: "3.4.1"\nreferencePolicy: closed\n');path=work/'case.yaml';config=work/'.argo-workflows-ls.yaml';client=None;checks={};responses=[]
m.save(dest/'expected.json',{'targets':['3.4.1','3.4.2','3.6.0','3.4.1'],'original':{v:expected(text,v) for v in ['3.4.1','3.4.2','3.6.0']},'recovered':{v:expected(fixed,v) for v in ['3.4.1','3.4.2','3.6.0']},'bytesUnchanged':True})
try:
 client=m.LSP(dest,work);checks['initial']=norm(client.open(path,text))==norm(expected(text,'3.4.1'));docversion=1
 for revision,target in enumerate(['3.4.1','3.4.2','3.6.0','3.4.1'],1):
  cfg='schemaVersion: 1\ntargetVersion: "'+target+'"\nreferencePolicy: closed\n'
  if revision==1:client.send('textDocument/didOpen',{'textDocument':{'uri':config.as_uri(),'version':revision,'languageId':'yaml','text':cfg}})
  else:client.send('textDocument/didChange',{'textDocument':{'uri':config.as_uri(),'version':revision},'contentChanges':[{'text':cfg}]})
  ds=client.diagnostics(path.as_uri(),docversion);checks[str(revision)+'-target']=norm(ds)==norm(expected(text,target));docversion+=1;checks[str(revision)+'-recovery']=norm(client.change(path,fixed,docversion))==norm(expected(fixed,target));docversion+=1;checks[str(revision)+'-restore']=norm(client.change(path,text,docversion))==norm(expected(text,target));responses.append({'target':target,'diagnostics':ds})
  declared=m.pos(text,'name: json',offset=6);use=m.pos(text,'workflow.parameters.json',offset=len('workflow.parameters.'));location=lambda pos:{'uri':path.as_uri(),'range':{'start':pos,'end':dict(pos,character=pos['character']+4)}}
  for method,want in [('definition',[location(declared)]),('referencesFalse',[location(use)]),('referencesTrue',[location(use),location(declared)])]:
   params={'textDocument':{'uri':path.as_uri()},'position':use}
   if method!='definition':params['context']={'includeDeclaration':method=='referencesTrue'}
   response=client.request('textDocument/'+('definition' if method=='definition' else 'references'),params);checks[str(revision)+'-json-'+method]='error' not in response and norm(response.get('result',[]))==norm(want)

 checks['unchangedSibling']=fixed.endswith(sibling) and text.endswith(sibling)
 checks['bytes']=path.read_bytes()==text.encode() and config.read_text()=='schemaVersion: 1\ntargetVersion: "3.4.1"\nreferencePolicy: closed\n'
except Exception:checks['completed']=False;(dest/'error.txt').write_text(traceback.format_exc())
finally:
 if client:client.close();checks['exitZero']=client.p.returncode==0
m.result('activation-recovery',checks,{'responses':responses});m.save(m.OUT/'manifest.json',{'command':sys.argv,'testSha256':m.sha(Path(__file__)),'nativeSha256':m.sha(m.BINARY),'helperSha256':m.sha(HERE/'wf-audit-gaps.py')});raise SystemExit(0 if all(checks.values()) else 1)
