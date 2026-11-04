#!/usr/bin/env python3
import pathlib,json,subprocess,tempfile,sys,hashlib,os
root=pathlib.Path(__file__).resolve().parents[2];binary=pathlib.Path(sys.argv[1]).resolve();argo=pathlib.Path(sys.argv[2] if len(sys.argv)>2 else '/tmp/zed-argo-official-3.6.0/argo');out=pathlib.Path(sys.argv[3]) if len(sys.argv)>3 else root/'artifacts/wf5-official';out.mkdir(parents=True,exist_ok=True);results=[];sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
with tempfile.TemporaryDirectory(prefix='wf-official-') as d:
 work=pathlib.Path(d);(work/'.argo-workflows-ls.yaml').write_text('schemaVersion: 1\ntargetVersion: "3.6.0"\n')
 for name,expected,args in [('valid',0,[]),('missing',1,[]),('mismatch',2,['--target-version','3.5.0'])]:
  source=root/'tests/fixtures/domain'/('valid.yaml' if name=='mismatch' else name+'.yaml');(work/'case.yaml').write_bytes(source.read_bytes());cmd=[str(binary),'lint-official','--root',str(work),'--argo-binary',str(argo),*args];p=subprocess.run(cmd,capture_output=True,timeout=30,env=os.environ|{'ARGO_SERVER':'127.0.0.1:1','ARGO_TOKEN':'test-not-a-real-token','KUBECONFIG':'/missing-fixture-config'});(out/(name+'.stdout')).write_bytes(p.stdout);(out/(name+'.stderr')).write_bytes(p.stderr);checks={'exit':p.returncode==expected}
  try:
   if name!='mismatch':
    v=json.loads(p.stdout);checks['real_tool_status']=v['toolExitCode']==expected and bool(v['stdout']);checks['offline']='--offline' in v['command'];checks['version']=v['targetVersion']=='3.6.0' and v['toolVersion']=='argo: v3.6.0'
   else:checks['reason']='version' in json.loads(p.stdout).get('error','').lower()
  except Exception as e:checks['parse']=str(e)
  results.append({'name':name,'command':cmd,'exitCode':p.returncode,'assertions':checks,'pass':all(x is True for x in checks.values())})
(out/'manifest.json').write_text(json.dumps({'command':sys.argv,'pythonVersion':sys.version,'binarySha256':sha(binary),'argoBinarySha256':sha(argo),'testSha256':sha(pathlib.Path(__file__)),'inputHashes':{p.name:sha(p) for p in [root/'tests/fixtures/domain/valid.yaml',root/'tests/fixtures/domain/missing.yaml']},'expected':'official lint valid=0/missing-template=1/exact-version-mismatch=2; offline despite poisoned inherited connection settings'},indent=2));(out/'results.json').write_text(json.dumps(results,indent=2));sys.exit(0 if results and all(x['pass'] for x in results) else 1)
