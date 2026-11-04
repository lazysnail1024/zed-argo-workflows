#!/usr/bin/env python3
"""Static external producer outputs and argument completion, real consumers."""
import pathlib,sys,json,importlib.util
root=pathlib.Path(__file__).resolve().parents[2];spec=importlib.util.spec_from_file_location('wire',root/'tests/e2e/wf-audit-gaps.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
lib='apiVersion: argoproj.io/v1alpha1\nkind: WorkflowTemplate\nmetadata: {name: library, namespace: argo}\nspec:\n  templates:\n    - name: worker\n      inputs: {parameters: [{name: message, default: hello}]}\n      outputs: {parameters: [{name: result, value: hello}]}\n      container: {image: alpine}\n'
caller='''apiVersion: argoproj.io/v1alpha1
kind: Workflow
metadata: {name: caller, namespace: argo}
spec:
  templates:
    - name: main
      dag:
        tasks:
          - name: produce
            templateRef: {name: library, template: worker}
            arguments: {parameters: [{name: message, value: hello}]}
          - name: consume
            dependencies: [produce]
            template: local
            arguments: {parameters: [{name: message, value: "{{tasks.produce.outputs.parameters.result}}"}]}
    - name: local
      inputs: {parameters: [{name: message, default: hello}]}
      container: {image: alpine}
'''
for name,marked,label,prefix in [('output',caller.replace('parameters.result','parameters.res<CURSOR>'),'result','res'),('argument',caller.replace('name: message, value: hello','name: mes<CURSOR>, value: hello'),'message','mes')]:
 text=marked.replace('<CURSOR>','');at=marked.index('<CURSOR>');dest,work=m.setup(name,{'caller.yaml':text,'library.yaml':lib});path=work/'caller.yaml';client=m.LSP(dest,work)
 try:
  client.open(path,text);r=client.request('textDocument/completion',{'textDocument':{'uri':path.as_uri()},'position':m.pos(marked,'<CURSOR>')});items=r.get('result',[]);checks={'exactCandidate':[x['label'] for x in items]==[label]}
  if len(items)==1:
   actual=m.apply(text,{'documentChanges':[{'textDocument':{'uri':path.as_uri(),'version':1},'edits':[items[0]['textEdit']]}]},path.as_uri(),1);expected=text[:at-len(prefix)]+label+text[at:];checks['exactBytes']=actual==expected;(dest/'expected.yaml').write_bytes(expected.encode());path.write_bytes(actual.encode());run=m.command(dest,'check',[m.BINARY,'check','--root',work,'--format','json']);report=json.loads(run.stdout);checks['fullCLI']=run.returncode==0 and report['diagnostics']==[] and report['filesAnalyzed']==2 and report['resourcesAnalyzed']==2 and report['analysisComplete'] is True and not report['configurationErrors']
  m.result(name,checks,{'response':r})
 finally:client.close()
for name,text in [('static-output',caller),('bracket-output',caller.replace('tasks.produce.outputs.parameters.result',"= tasks['produce'].outputs.parameters['result']"))]:
 dest,work=m.setup(name,{'caller.yaml':text,'library.yaml':lib});path=work/'caller.yaml';client=m.LSP(dest,work)
 try:
  client.open(path,text);needle='parameters.result' if name=='static-output' else "parameters['result";r=client.request('textDocument/definition',{'textDocument':{'uri':path.as_uri()},'position':m.pos(text,needle,offset=len(needle)-3)});got=r.get('result',[]);checks={'exactDefinition':len(got)==1 and got[0]['uri']==(work/'library.yaml').as_uri() and got[0]['range']['start']==m.pos(lib,'name: result',offset=6)};m.result(name,checks,{'response':r})
 finally:client.close()
m.save(m.OUT/'manifest.json',{'command':sys.argv,'binarySha256':m.sha(m.BINARY),'testSha256':m.sha(pathlib.Path(__file__)),'inputHashes':{str(p.relative_to(m.OUT)):m.sha(p) for p in m.OUT.rglob('*.yaml')}});sys.exit(0 if len(m.RESULTS)==4 and all(x['pass'] for x in m.RESULTS) else 1)
