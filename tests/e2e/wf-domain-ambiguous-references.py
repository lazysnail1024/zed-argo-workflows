#!/usr/bin/env python3
"""Ambiguous declarations remain candidates, never a resolved reference set."""
import pathlib,sys,json,importlib.util
root=pathlib.Path(__file__).resolve().parents[2];spec=importlib.util.spec_from_file_location('wire',root/'tests/e2e/wf-audit-gaps.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
text='apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: ambiguity}\nspec:\n  templates:\n    - name: main\n      inputs: {parameters: [{name: message, default: first}, {name: message, default: second}]}\n      container: {image: alpine, args: ["{{inputs.parameters.message}}"]}\n'
dest,work=m.setup('ambiguous-reference-set',{'case.yaml':text});path=work/'case.yaml';client=m.LSP(dest,work)
try:
 client.open(path,text)
 for site,position in [('use',m.pos(text,'parameters.message',offset=13)),('declaration',m.pos(text,'name: message',offset=6))]:
  for include in [False,True]:
   r=client.request('textDocument/references',{'textDocument':{'uri':path.as_uri()},'position':position,'context':{'includeDeclaration':include}});m.result(site+str(include),{'noProtocolError':'error' not in r,'noResolvedReferences':r.get('result')==[]},{'response':r})
finally:client.close()
m.save(m.OUT/'manifest.json',{'command':sys.argv,'binarySha256':m.sha(m.BINARY),'testSha256':m.sha(pathlib.Path(__file__)),'inputHashes':{str(p.relative_to(m.OUT)):m.sha(p) for p in m.OUT.rglob('*.yaml')}});sys.exit(0 if len(m.RESULTS)==4 and all(x['pass'] for x in m.RESULTS) else 1)
