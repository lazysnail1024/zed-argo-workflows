#!/usr/bin/env python3
"""YAML implicit scalars must not acquire string symbol identities."""
import pathlib,sys,importlib.util,json
root=pathlib.Path(__file__).resolve().parents[2];spec=importlib.util.spec_from_file_location('client',root/'tests/e2e/wf-audit-gaps.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
for scalar,string_name in [('main','main'),('"no"','no'),('no',None),('YES',None),('null',None),('123',None),('y',None),('Y',None),('n',None),('N',None),('on',None),('OFF',None),('Null',None),('NULL',None),('NaN','NaN'),('.NaN',None),('.Inf',None)]:
 name='scalar-'+str(len(m.RESULTS));text='apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: scalar}\nspec:\n  templates:\n    - name: '+scalar+'\n      container: {image: alpine}\n';dest,work=m.setup(name,{'case.yaml':text});client=m.LSP(dest,work)
 try:
  client.open(work/'case.yaml',text);r=client.request('textDocument/documentSymbol',{'textDocument':{'uri':(work/'case.yaml').as_uri()}});names=[x['name'] for x in r.get('result',[])];m.result(name,{'exactSymbols':names==([] if string_name is None else [string_name])},{'response':r})
 finally:client.close()
m.save(m.OUT/'manifest.json',{'command':sys.argv,'binarySha256':m.sha(m.BINARY),'testSha256':m.sha(pathlib.Path(__file__))});sys.exit(0 if len(m.RESULTS)==17 and all(r['pass'] for r in m.RESULTS) else 1)
