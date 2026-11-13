#!/usr/bin/env python3
import pathlib,sys,json,hashlib,subprocess
root=pathlib.Path(__file__).resolve().parents[2];binary=pathlib.Path(sys.argv[1]).resolve();out=pathlib.Path(sys.argv[2]) if len(sys.argv)>2 else root/'artifacts/wf3-schema';out.mkdir(parents=True,exist_ok=True)
e=json.loads((root/'tests/e2e/wf3-schema.expected.json').read_text());results=[]
for name in ['mixed','valid','duplicate']:
 command=[str(binary),'check','--root',str(root/'tests/fixtures/schema'),'--file',str(root/'tests/fixtures/schema'/(name+'.yaml')),'--target-version',e['targetVersion'],'--format','json'];p=subprocess.run(command,capture_output=True,timeout=20);(out/(name+'.stdout')).write_bytes(p.stdout);(out/(name+'.stderr')).write_bytes(p.stderr);checks={'exit':p.returncode==e['exitCode']}
 try:
  v=json.loads(p.stdout);checks['partial']=v['analysisComplete'] is False;diags=[d for d in v['diagnostics'] if d['code']=='SCHEMA_TYPE'];checks['type_error']=len(diags)==(1 if name=='mixed' else 0)
  if name=='mixed':checks['position']=len(diags)==1 and diags[0]['range']['start']=={'line':e[name]['typeErrorLine'],'character':e[name]['typeErrorColumn']};checks['ownership']=all('mixed.yaml' in d['uri'] for d in v['diagnostics'])
  if name=='duplicate':checks['strict']=any('duplicate' in x.lower() for x in v['notEvaluated'])
 except Exception as err:checks['parse']=str(err)
 results.append({'name':name,'command':command,'exitCode':p.returncode,'assertions':checks,'pass':all(x is True for x in checks.values())})
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
(out/'results.json').write_text(json.dumps(results,indent=2));(out/'manifest.json').write_text(json.dumps({'command':sys.argv,'binarySha256':sha(binary),'testSha256':sha(pathlib.Path(__file__)),'expectedSha256':sha(root/'tests/e2e/wf3-schema.expected.json'),'inputHashes':{p.name:sha(p) for p in (root/'tests/fixtures/schema').glob('*.yaml')}},indent=2));sys.exit(0 if results and all(r['pass'] for r in results) else 1)
