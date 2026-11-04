#!/usr/bin/env python3
"""Required external definition lookup on the canonical full workspace."""
from pathlib import Path
import sys,json,time,importlib.util
root=Path(__file__).resolve().parents[2];binary=Path(sys.argv[1]).resolve();work=Path(sys.argv[2]).resolve();out=Path(sys.argv[3]).resolve();out.mkdir(parents=True,exist_ok=True)
sys.argv=[sys.argv[0],str(binary),str(out)];spec=importlib.util.spec_from_file_location('wire',root/'tests/e2e/wf-audit-gaps.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
caller='apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: caller, namespace: argo}\nspec:\n  workflowTemplateRef: {name: library}\n  entrypoint: main\n  arguments: {parameters: [{name: message, value: hello}]}\n'
caller+='#'+'p'*(192*1024-len(caller.encode())-2)+'\n'
library='apiVersion: argoproj.io/v1alpha1\nkind: WorkflowTemplate\nmetadata: {name: library, namespace: argo}\nspec:\n  templates:\n    - name: main\n      inputs: {parameters: [{name: message, default: hello}]}\n      container: {image: alpine, args: ["{{inputs.parameters.message}}"]}\n'
paths=[work/'group-000/00000.yaml',work/'group-000/00001.yaml'];texts=[caller,library]
def loc(index,needle,offset,name):
 start=m.pos(texts[index],needle,offset=offset);return {'uri':paths[index].as_uri(),'range':{'start':start,'end':dict(start,character=start['character']+len(name))}}
expected=[[loc(1,'name: main',6,'main')],[loc(0,'name: message',6,'message'),loc(1,'name: message',6,'message')]]
m.save(out/'expected.json',{'definitions':expected,'samples':30,'p95MaximumSeconds':.1})
for name,text in [('caller.yaml',caller),('library.yaml',library)]: (out/name).write_bytes(text.encode())
client=None;rows=[];results=[]
try:
 client=m.LSP(out,work)
 for path,text in zip(paths,texts):client.open(path,text)
 for i in range(30):
  j=i%2;params={'textDocument':{'uri':paths[0].as_uri()},'position':m.pos(caller,'entrypoint: main' if j==0 else 'name: message',offset=12 if j==0 else 6)};start=time.monotonic();response=client.request('textDocument/definition',params);rows.append({'request':params,'response':response,'seconds':time.monotonic()-start,'pass':sorted(json.dumps(x,sort_keys=True) for x in response.get('result',[]))==sorted(json.dumps(x,sort_keys=True) for x in expected[j]) and 'error' not in response})
 samples=[x['seconds'] for x in rows];p95=sorted(samples)[int((len(samples)-1)*.95)];checks={'allExactCandidates':all(x['pass'] for x in rows),'allSamples':len(rows)==30,'p95Within100ms':p95<=.1};results.append({'case':'required-external-definitions','assertions':checks,'pass':all(checks.values()),'p95Seconds':p95,'samples':rows})
finally:
 if client:client.close()
 m.save(out/'results.json',results);m.save(out/'manifest.json',{'command':sys.argv,'binarySha256':m.sha(binary),'testSha256':m.sha(Path(__file__)),'workspaceFiles':10000,'filesPerScope':100,'bufferHashes':{p.name:m.sha(p) for p in out.glob('*.yaml')},'samples':len(rows)})
sys.exit(0 if results and all(x['pass'] for x in results) else 1)
