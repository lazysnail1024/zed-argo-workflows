#!/usr/bin/env python3
"""A workflow argument has declaration and entrypoint-argument roles."""
import pathlib,sys,json,importlib.util
root=pathlib.Path(__file__).resolve().parents[2];spec=importlib.util.spec_from_file_location('wire',root/'tests/e2e/wf-audit-gaps.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
text='apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: overlap}\nspec:\n  arguments: {parameters: [{name: message, value: hello}]}\n  entrypoint: main\n  templates:\n    - name: main\n      inputs: {parameters: [{name: message}]}\n      container: {image: alpine, args: ["{{workflow.parameters.message}}", "{{inputs.parameters.message}}"]}\n'
dest,work=m.setup('overlap',{'case.yaml':text});path=work/'case.yaml';client=m.LSP(dest,work)
def loc(needle,offset,occurrence=0):
 a=m.pos(text,needle,occurrence=occurrence,offset=offset);z=dict(a);z['character']+=len('message');return {'uri':path.as_uri(),'range':{'start':a,'end':z}}
def norm(rows):return sorted(json.dumps(x,sort_keys=True) for x in rows)
argument=loc('name: message',6);inputdecl=loc('name: message',6,1);workflowuse=loc('workflow.parameters.message',20);inputuse=loc('inputs.parameters.message',18)
try:
 client.open(path,text)
 for name,selected,defs,uses in [('overlap',argument,[argument,inputdecl],None),('workflow',workflowuse,[argument],[workflowuse]),('input',inputuse,[inputdecl],[argument,inputuse])]:
  params={'textDocument':{'uri':path.as_uri()},'position':selected['range']['start']};r=client.request('textDocument/definition',params);checks={'exactDefinitionCandidates':norm(r.get('result',[]))==norm(defs)}
  for include in [False,True]:
   response=client.request('textDocument/references',params|{'context':{'includeDeclaration':include}});expected=[] if uses is None else uses+(defs if include else []);checks['noProtocolError'+str(include)]='error' not in response;checks['exactReferenceSet'+str(include)]=norm(response.get('result',[]))==norm(expected)
  m.result(name,checks,{'definition':r})
 r=client.request('textDocument/prepareRename',{'textDocument':{'uri':path.as_uri()},'position':argument['range']['start']});m.result('rename-still-closed',{'refused':'error' in r or r.get('result') is None},{'response':r})
finally:client.close()
m.save(m.OUT/'manifest.json',{'command':sys.argv,'binarySha256':m.sha(m.BINARY),'testSha256':m.sha(pathlib.Path(__file__)),'inputHashes':{str(p.relative_to(m.OUT)):m.sha(p) for p in m.OUT.rglob('*.yaml')}});sys.exit(0 if len(m.RESULTS)==4 and all(x['pass'] for x in m.RESULTS) else 1)
