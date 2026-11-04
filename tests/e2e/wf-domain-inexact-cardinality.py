#!/usr/bin/env python3
"""Count semantic declarations before excluding inexact source spellings."""
import pathlib,sys,json,importlib.util
root=pathlib.Path(__file__).resolve().parents[2];spec=importlib.util.spec_from_file_location('wire',root/'tests/e2e/wf-audit-gaps.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
local='apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: cardinality}\nspec:\n  templates:\n    - name: main\n      inputs: {parameters: [{name: message, default: first}, {name: "me\\u0073sage", default: second}, {name: other, default: okay}]}\n      container: {image: alpine, args: ["{{inputs.parameters.message}}", "{{inputs.parameters.other}}"]}\n'
lib='apiVersion: argoproj.io/v1alpha1\nkind: WorkflowTemplate\nmetadata: {name: library, namespace: argo}\nspec:\n  templates:\n    - name: worker\n      inputs: {parameters: [{name: message, default: first}, {name: "me\\u0073sage", default: second}]}\n      outputs: {parameters: [{name: result, value: first}, {name: "res\\u0075lt", value: second}]}\n      container: {image: alpine}\n'
caller='apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: caller, namespace: argo}\nspec:\n  templates:\n    - name: main\n      dag:\n        tasks:\n          - name: produce\n            templateRef: {name: library, template: worker}\n            arguments: {parameters: [{name: message, value: hi}]}\n          - name: consume\n            dependencies: [produce]\n            template: local\n            arguments: {parameters: [{name: value, value: "{{tasks.produce.outputs.parameters.result}}"}]}\n    - name: local\n      inputs: {parameters: [{name: value}]}\n      container: {image: alpine}\n'
rows=[('local-definition',local,'definition','parameters.message',13,{},False),('local-completion',local.replace('parameters.message','parameters.mes'),'completion','parameters.mes',14,{},False),('local-unique',local,'definition','parameters.other',12,{},True),('external-output-definition',caller,'definition','parameters.result',13,{'library.yaml':lib},False),('external-output-completion',caller.replace('parameters.result','parameters.res'),'completion','parameters.res',14,{'library.yaml':lib},False),('external-input-completion',caller.replace('name: message','name: mes'),'completion','name: mes',9,{'library.yaml':lib},False)]
duplicates=lib+'    - name: "wo\\u0072ker"\n      container: {image: alpine}\n'
rows.append(('unsafe-name-completion',local.replace('message','bad name').replace('parameters.bad name','parameters.ba'),'completion','parameters.ba',13,{},False))
rows.append(('external-template-completion',caller.replace('template: worker','template: wo'),'completion','template: wo',12,{'library.yaml':duplicates},False))
aliasLocal=local.replace('metadata: {name: cardinality}','metadata: {name: cardinality, annotations: {alias: &message message}}').replace('"me\\u0073sage"','*message')
rows += [('local-alias-definition',aliasLocal,'definition','parameters.message',13,{},False),('local-alias-completion',aliasLocal.replace('parameters.message','parameters.mes'),'completion','parameters.mes',14,{},False)]
aliasLib=lib.replace('namespace: argo}', 'namespace: argo, annotations: {alias: &result result}}').replace('"res\\u0075lt"','*result')
rows.append(('external-alias-output-definition',caller,'definition','parameters.result',13,{'library.yaml':aliasLib},False))
aliasTemplate=lib.replace('- name: worker','- &worker\n      name: worker')+'    - *worker\n'
rows.append(('external-alias-template-completion',caller.replace('template: worker','template: wo'),'completion','template: wo',12,{'library.yaml':aliasTemplate},False))
for name,text,method,needle,offset,extra,positive in rows:
 dest,work=m.setup(name,{'case.yaml':text,**extra});path=work/'case.yaml';client=m.LSP(dest,work)
 try:
  client.open(path,text);r=client.request('textDocument/'+method,{'textDocument':{'uri':path.as_uri()},'position':m.pos(text,needle,offset=offset)});got=r.get('result',[]);checks={'uniqueBeforeOriginFilter':len(got)==1 and got[0]['range']['start']==m.pos(text,'name: other',offset=6) if positive else got==[]};
  if name in ['local-definition','local-alias-definition']:
   start=m.pos(text,'name: message',offset=6);end=dict(start);end['character']+=7
   checks={'exactCandidateDespiteAmbiguity':got==[{'uri':path.as_uri(),'range':{'start':start,'end':end}}]}
  checks['noProtocolError']='error' not in r;m.result(name,checks,{'response':r})
 finally:client.close()
dest,work=m.setup('local-references',{'case.yaml':local});path=work/'case.yaml';client=m.LSP(dest,work)
try:
 client.open(path,local);checks={}
 for include in [False,True]:
  r=client.request('textDocument/references',{'textDocument':{'uri':path.as_uri()},'position':m.pos(local,'parameters.message',offset=13),'context':{'includeDeclaration':include}});checks['empty'+str(include)]=r.get('result')==[] and 'error' not in r
 m.result('local-references',checks)
finally:client.close()
m.save(m.OUT/'manifest.json',{'command':sys.argv,'binarySha256':m.sha(m.BINARY),'testSha256':m.sha(pathlib.Path(__file__)),'inputHashes':{str(p.relative_to(m.OUT)):m.sha(p) for p in m.OUT.rglob('*.yaml')}});sys.exit(0 if len(m.RESULTS)==13 and all(x['pass'] for x in m.RESULTS) else 1)
