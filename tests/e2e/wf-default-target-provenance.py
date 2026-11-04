#!/usr/bin/env python3
"""Target provenance agrees between full CLI acquisition and local LSP queries."""
import importlib.util,pathlib,sys,tempfile,shutil,json
p=pathlib.Path(__file__).with_name('wf-audit-gaps.py');spec=importlib.util.spec_from_file_location('wire',p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
text=m.REN_DAG;dest=m.OUT/'fresh-root';dest.mkdir(exist_ok=True)
with tempfile.TemporaryDirectory(prefix='wf-target-provenance-') as tmp:
 work=pathlib.Path(tmp);path=work/'a.yaml';path.write_text(text);config=work/'.argo-workflows-ls.yaml'
 assert all(not (parent/'.argo-workflows-ls.yaml').exists() for parent in work.parents)
 c=m.LSP(dest,work)
 try:
  c.open(path,text)
  for name,content,want in [('absent',None,'default'),('empty','','default'),('no-pin','schemaVersion: 1\nreferencePolicy: closed\n','default'),('root-pin',m.CONFIG,'root'),('scope-pin',m.CONFIG+'scopes:\n  - name: selected\n    include: ["*.yaml"]\n    targetVersion: "3.6.0"\n','scope')]:
   if content is None:
    if config.exists():config.unlink()
   else:config.write_text(content)
   c.send('workspace/didChangeWatchedFiles',{'changes':[{'uri':config.as_uri(),'type':2}]});c.diagnostics(path.as_uri(),1)
   code,data=m.cli(dest,work,name);hover=c.request('textDocument/hover',{'textDocument':{'uri':path.as_uri()},'position':m.pos(text,'entrypoint',offset=2)})
   value=hover.get('result',{}).get('contents',{}).get('value','');m.result(name,{'cli_provenance':bool(data['targets']) and data['targets'][0]['source']==want,'lsp_provenance':f'Target selection: {want} ·' in value,'target_unchanged':data['targets'][0]['version']=='3.6.0' and '`3.6.0`' in value,'complete':code==0 and data['analysisComplete']},{'cli':data,'hover':hover,'config':content})
  config.unlink();c.send('workspace/didChangeWatchedFiles',{'changes':[{'uri':config.as_uri(),'type':3}]});c.diagnostics(path.as_uri(),1)
  for name,override,want in [('override','3.5.0','editor settings override'),('remove-override','','default')]:
   c.send('workspace/didChangeConfiguration',{'settings':{'targetVersion':override}});c.diagnostics(path.as_uri(),1);hover=c.request('textDocument/hover',{'textDocument':{'uri':path.as_uri()},'position':m.pos(text,'entrypoint',offset=2)});value=hover.get('result',{}).get('contents',{}).get('value','');m.result(name,{'provenance':f'Target selection: {want} ·' in value,'target':f'`{override or "3.6.0"}`' in value},{'hover':hover})
 finally:
  c.close();shutil.copytree(work,dest/'final-input');m.save(m.OUT/'manifest.json',{'command':sys.argv,'binarySha256':m.sha(m.BINARY),'testSha256':m.sha(pathlib.Path(__file__)),'originalTemporaryRoot':str(work),'ancestorConfigurations':[],'initialInputSha256':__import__('hashlib').sha256(text.encode()).hexdigest()})
sys.exit(0 if len(m.RESULTS)==7 and all(x['pass'] for x in m.RESULTS) else 1)
