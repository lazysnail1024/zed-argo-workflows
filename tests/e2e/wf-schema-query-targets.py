#!/usr/bin/env python3
"""Authoritative target changes must update query metadata in one real server."""
import pathlib,sys,json,importlib.util
root=pathlib.Path(__file__).resolve().parents[2];spec=importlib.util.spec_from_file_location('client',root/'tests/e2e/wf-audit-gaps.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
text='apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: target}\nspec:\n  parallelism: 2\n  entrypoint: main\n  templates:\n    - name: main\n      container: {image: alpine}\n';dest,work=m.setup('current-target',{'case.yaml':text});client=m.LSP(dest,work);path=work/'case.yaml';config=work/'.argo-workflows-ls.yaml';commits={'3.6.0':'b26ed4aa4dee395844531efa4a76a022183bec22','3.5.0':'bf735a2e861d6b1c686dd4a076afc3468aa89c4a'}
try:
 client.open(path,text)
 for revision,version in enumerate(['3.6.0','3.5.0','99.0.0','3.6.0'],1):
  value='schemaVersion: 1\ntargetVersion: "'+version+'"\n'
  if revision==1:client.send('textDocument/didOpen',{'textDocument':{'uri':config.as_uri(),'version':revision,'languageId':'yaml','text':value}})
  else:client.send('textDocument/didChange',{'textDocument':{'uri':config.as_uri(),'version':revision},'contentChanges':[{'text':value}]})
  client.diagnostics(path.as_uri(),1);params={'textDocument':{'uri':path.as_uri()},'position':m.pos(text,'parallelism',offset=3)};r=client.request('textDocument/hover',params)
  if version=='99.0.0':checks={'explicitUnknownTargetRefusal':bool(r.get('error'))}
  else:
   content=(r.get('result') or {}).get('contents',{}).get('value','');other=next(c for v,c in commits.items() if v!=version);checks={'currentTarget':version in content,'currentSource':commits[version] in content,'noStaleSource':other not in content,'integerType':'integer' in content}
  if version!='99.0.0':
   c=client.request('textDocument/completion',{'textDocument':{'uri':path.as_uri()},'position':m.pos(text,'parallelism')});items=c.get('result',[]);checks['currentCompletionTarget']=bool(items) and all(version in x.get('detail','') for x in items);checks['existingKeyExcluded']='parallelism' not in [x['label'] for x in items]
  m.result('target-'+str(revision)+'-'+version,checks,{'response':r})
finally:client.close()
m.save(m.OUT/'manifest.json',{'command':sys.argv,'binarySha256':m.sha(m.BINARY),'testSha256':m.sha(pathlib.Path(__file__)),'fixedUpstreamCommits':commits});sys.exit(0 if len(m.RESULTS)==4 and all(x['pass'] for x in m.RESULTS) else 1)
