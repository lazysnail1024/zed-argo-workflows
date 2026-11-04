#!/usr/bin/env python3
import pathlib,json,subprocess,tempfile,sys,hashlib
root=pathlib.Path(__file__).resolve().parents[2];binary=pathlib.Path(sys.argv[1]).resolve();out=pathlib.Path(sys.argv[2]) if len(sys.argv)>2 else root/'artifacts/wf4-local';out.mkdir(parents=True,exist_ok=True);e=json.loads((root/'tests/e2e/wf4-local.expected.json').read_text());results=[]
with tempfile.TemporaryDirectory(prefix='wf-local-') as d:
 work=pathlib.Path(d)
 for name in ['missing','valid','duplicate','dag','depends','arguments','defaults','stepduplicate','inputscope','outputmissing','dagscope','stepscope','dynamicindex','referencevalid','publicinputs']:
  fixture=root/'tests/fixtures/domain'/(name+'.yaml');(work/'case.yaml').write_bytes(fixture.read_bytes());cmd=[str(binary),'check','--root',str(work),'--target-version',e['targetVersion'],'--format','json'];p=subprocess.run(cmd,capture_output=True,timeout=15);(out/(name+'.stdout')).write_bytes(p.stdout);(out/(name+'.stderr')).write_bytes(p.stderr);checks={'exit':p.returncode==e['exitCode']}
  try:
   v=json.loads(p.stdout);ds=[d for d in v['diagnostics'] if d['code'].startswith('WF_')];checks['domainCodes']=sorted({d['code'] for d in ds})==e[name]
   if name=='missing':checks['position']=ds[0]['range']['start']=={'line':4,'character':14}
  except Exception as err:checks['parse']=str(err)
  results.append({'name':name,'command':cmd,'exitCode':p.returncode,'assertions':checks,'pass':all(x is True for x in checks.values())})
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
(out/'manifest.json').write_text(json.dumps({'command':sys.argv,'pythonVersion':sys.version,'binarySha256':sha(binary),'testSha256':sha(pathlib.Path(__file__)),'expectedSha256':sha(root/'tests/e2e/wf4-local.expected.json'),'inputHashes':{p.name:sha(p) for p in (root/'tests/fixtures/domain').glob('*.yaml')}},indent=2));(out/'results.json').write_text(json.dumps(results,indent=2));sys.exit(0 if results and all(r['pass'] for r in results) else 1)
