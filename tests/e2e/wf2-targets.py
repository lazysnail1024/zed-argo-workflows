#!/usr/bin/env python3
import pathlib,json,hashlib,subprocess,sys
root=pathlib.Path(__file__).resolve().parents[2];binary=pathlib.Path(sys.argv[1]).resolve();out=root/'artifacts/wf2-targets';out.mkdir(parents=True,exist_ok=True)
e=json.loads((root/'tests/e2e/wf2.expected.json').read_text());cases=[('known','3.6.0',False),('unknown',e['unknownTarget'],True)];results=[]
for name,target,unknown in cases:
 command=[str(binary),'check','--root',str(root/'tests/fixtures/schema'),'--format','json','--target-version',target];p=subprocess.run(command,capture_output=True,timeout=10);(out/(name+'.stdout')).write_bytes(p.stdout);(out/(name+'.stderr')).write_bytes(p.stderr)
 checks={'exit':p.returncode==2}
 try:
  v=json.loads(p.stdout); checks['incomplete']=v['analysisComplete'] is False;checks['catalog']=isinstance(v['catalogHash'],str) and len(v['catalogHash'])==64;checks['target_selection']=any('unknown target' in x for x in v['configurationErrors'])==unknown;checks['no_unverified_target']=all(t.get('analysisComplete') is False for t in v['targets'])
 except Exception as err:checks['parse']=str(err)
 results.append({'name':name,'command':command,'exitCode':p.returncode,'assertions':checks,'pass':all(x is True for x in checks.values())})
(out/'results.json').write_text(json.dumps(results,indent=2));(out/'manifest.json').write_text(json.dumps({'command':sys.argv,'binarySha256':hashlib.sha256(binary.read_bytes()).hexdigest(),'testSha256':hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest(),'expectedSha256':hashlib.sha256((root/'tests/e2e/wf2.expected.json').read_bytes()).hexdigest()},indent=2));sys.exit(0 if all(r['pass'] for r in results) else 1)
