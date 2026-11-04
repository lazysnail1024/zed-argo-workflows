#!/usr/bin/env python3
"""Exact public parser error text across source coordinate forms."""
import pathlib,sys,json,importlib.util
root=pathlib.Path(__file__).resolve().parents[2];spec=importlib.util.spec_from_file_location('client',root/'tests/e2e/wf-audit-gaps.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
prefix='apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: expression}\nspec:\n  templates:\n    - name: main\n      inputs:\n        parameters: [{name: alpha, default: seed}]\n      container:\n        image: alpine\n'
cases=[('ascii',"        args: ['{{=inputs.parameters.alpha +}}']\n",'invalid expression: unexpected token EOF (1:25)\n | inputs.parameters.alpha +\n | ........................^'),('tab',"        args: ['{{=\tinputs.parameters.alpha +}}']\n",'invalid expression: unexpected token EOF (1:26)\n |  inputs.parameters.alpha +\n | .........................^'),('multibyte',"        args: ['{{=\"\U0001f600\" + inputs.parameters.alpha +}}']\n",'invalid expression: unexpected token EOF (1:31)\n | "\U0001f600" + inputs.parameters.alpha +'),('multiline','        args:\n          - |\n            {{=inputs.parameters.alpha +\n            +}}\n','invalid expression: unexpected token EOF (2:13)\n |             +\n | ............^')]
for name,tail,expected in cases:
 text=prefix+tail;dest,work=m.setup(name,{'case.yaml':text});client=m.LSP(dest,work)
 try:
  path=work/'case.yaml';client.open(path,text);r=client.request('textDocument/rename',{'textDocument':{'uri':path.as_uri()},'position':m.pos(text,'name: alpha',offset=6),'newName':'beta'});m.result(name,{'exactError':r.get('error')=={'code':-32602,'message':expected}},{'expected':expected,'response':r})
 finally:client.close()
m.save(m.OUT/'manifest.json',{'binarySha256':m.sha(m.BINARY),'testSha256':m.sha(pathlib.Path(__file__))});sys.exit(0 if len(m.RESULTS)==4 and all(r['pass'] for r in m.RESULTS) else 1)
