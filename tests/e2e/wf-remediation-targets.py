#!/usr/bin/env python3
"""Accepted/experimental/unknown targets and visible effective source changes."""
import importlib.util,pathlib,sys,json,time
p=pathlib.Path(__file__).with_name('wf-audit-gaps.py');spec=importlib.util.spec_from_file_location('wire',p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
text=m.REN_DAG
for version in ['3.6.0','3.5.0','99.99.99']:
 dest,work=m.setup(version,{'a.yaml':text},m.CONFIG.replace('3.6.0',version));code,data=m.cli(dest,work);c=m.LSP(dest,work);path=work/'a.yaml'
 try:
  ds=c.open(path,text);r=c.request('textDocument/prepareRename',{'textDocument':{'uri':path.as_uri()},'position':m.pos(text,'name: cleanup',offset=7)});m.result(version,{'complete_only_accepted':data['analysisComplete']==(version=='3.6.0'),'edits_only_accepted':(r.get('result')=={'placeholder':'cleanup','range':{'start':m.pos(text,'name: cleanup',offset=6),'end':m.pos(text,'name: cleanup',offset=13)}} and 'error' not in r) if version=='3.6.0' else (r.get('error',{}).get('code')==(-32602 if version=='3.5.0' else -32803) and 'result' not in r),'status':[d['code'] for d in ds]==([] if version=='3.6.0' else ['WF_EXPERIMENTAL_TARGET' if version=='3.5.0' else 'WF_ANALYSIS_UNAVAILABLE']),'exit':code==(0 if version=='3.6.0' else 2)},{'cli':data,'response':r})
 finally:c.close()
dest,work=m.setup('source-switch',{'a.yaml':text});c=m.LSP(dest,work);path=work/'a.yaml'
try:
 c.open(path,text)
 for i,override in enumerate(['3.5.0','','99.99.99','3.6.0']):
  c.send('workspace/didChangeConfiguration',{'settings':{'targetVersion':override}});c.diagnostics(path.as_uri(),1);r=c.request('textDocument/hover',{'textDocument':{'uri':path.as_uri()},'position':m.pos(text,'entrypoint',offset=2)});content=json.dumps(r.get('result'));m.result('source-'+str(i),{'target_visible':(r.get('error',{}).get('code')==-32803 and '99.99.99' in r['error']['message'] and 'result' not in r) if override=='99.99.99' else (override or '3.6.0') in content,'source_visible':override=='99.99.99' or 'Target selection' in content,'pin_unchanged':(work/'.argo-workflows-ls.yaml').read_text()==m.CONFIG},{'response':r})
finally:c.close()
m.save(m.OUT/'manifest.json',{'command':sys.argv,'binarySha256':m.sha(m.BINARY),'testSha256':m.sha(pathlib.Path(__file__)),'inputHashes':{str(p.relative_to(m.OUT)):m.sha(p) for p in m.OUT.rglob('*.yaml')}})
sys.exit(0 if len(m.RESULTS)==7 and all(x['pass'] for x in m.RESULTS) else 1)
