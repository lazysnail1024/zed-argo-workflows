#!/usr/bin/env python3
"""One external identity includes local and external references exactly once."""
import pathlib,sys,json,importlib.util
root=pathlib.Path(__file__).resolve().parents[2];spec=importlib.util.spec_from_file_location('wire',root/'tests/e2e/wf-audit-gaps.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
lib='apiVersion: argoproj.io/v1alpha1\nkind: WorkflowTemplate\nmetadata: {name: library, namespace: argo}\nspec:\n  entrypoint: worker\n  templates:\n    - name: worker\n      inputs: {parameters: [{name: message, default: hello}]}\n      container: {image: alpine, args: ["{{inputs.parameters.message}}"]}\n'
caller='apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: caller, namespace: argo}\nspec:\n  templates:\n    - name: main\n      dag:\n        tasks:\n          - name: call\n            templateRef: {name: library, template: worker}\n            arguments: {parameters: [{name: message, value: hello}]}\n'
def loc(path,text,needle,name):
 a=m.pos(text,needle,offset=len(needle)-len(name));z=dict(a);z['character']+=len(name);return {'uri':path.as_uri(),'range':{'start':a,'end':z}}
def norm(rows):return sorted(json.dumps(x,sort_keys=True) for x in rows)
dest,work=m.setup('external-reference-union',{'caller.yaml':caller,'library.yaml':lib});cp,lp=work/'caller.yaml',work/'library.yaml';client=m.LSP(dest,work)
try:
 client.open(cp,caller);client.open(lp,lib)
 for name,decl,local,external in [('template',loc(lp,lib,'name: worker','worker'),loc(lp,lib,'entrypoint: worker','worker'),loc(cp,caller,'template: worker','worker')),('parameter',loc(lp,lib,'name: message','message'),loc(lp,lib,'parameters.message','message'),loc(cp,caller,'name: message','message'))]:
  for selected in [decl,external]:
   for include in [False,True]:
    r=client.request('textDocument/references',{'textDocument':{'uri':selected['uri']},'position':selected['range']['start'],'context':{'includeDeclaration':include}});expected=[local,external]+([decl] if include else []);m.result(name+('-declaration' if selected==decl else '-external')+str(include),{'exactUnion':norm(r.get('result',[]))==norm(expected)},{'response':r})
finally:client.close()
m.save(m.OUT/'manifest.json',{'command':sys.argv,'binarySha256':m.sha(m.BINARY),'testSha256':m.sha(pathlib.Path(__file__)),'inputHashes':{str(p.relative_to(m.OUT)):m.sha(p) for p in m.OUT.rglob('*.yaml')}});sys.exit(0 if len(m.RESULTS)==8 and all(x['pass'] for x in m.RESULTS) else 1)
