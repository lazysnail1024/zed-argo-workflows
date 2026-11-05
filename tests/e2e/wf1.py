#!/usr/bin/env python3
"""Run actual executable, retaining raw output and independently defined expectations."""
import hashlib,json,pathlib,subprocess,sys,platform
root=pathlib.Path(__file__).resolve().parents[2]
binary=pathlib.Path(sys.argv[1]).resolve(); out=pathlib.Path(sys.argv[2]).resolve(); out.mkdir(parents=True,exist_ok=True)
expected=json.loads((root/'tests/e2e/wf1.expected.json').read_text())
work=out/'input';work.mkdir(exist_ok=True);(work/'case.yaml').write_bytes('apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: smoke}\nspec: {entrypoint: main, templates: [{name: main, container: {image: alpine}}]}\n'.encode('utf-8'))
cases=[('version',['version','--json'],0),('help',['help'],0),('check',['check','--root',str(work),'--format','json'],0),('unknown',['bogus'],2),('extra',['version','--json','bogus'],2)]
results=[]
for name,args,code in cases:
 p=subprocess.run([str(binary),*args],capture_output=True,timeout=10)
 (out/(name+'.stdout')).write_bytes(p.stdout); (out/(name+'.stderr')).write_bytes(p.stderr)
 assertions={'exit':p.returncode==code}
 try:
  if name=='version':
   v=json.loads(p.stdout); assertions['identity']=all(v.get(k)==expected[k] for k in ['product','adapterContractVersion','analysisReady']); assertions['fields']=all(k in v for k in ['serverVersion','configSchemaVersions','reportSchemaVersions','toolkitVersion','catalogHash','sourceCommit','dirty','buildMode'])
  if name=='check':
   v=json.loads(p.stdout); assertions['complete']=v.get('analysisComplete') is True and v.get('notEvaluated')==[] and v.get('diagnostics')==[]; assertions['report']=v.get('product')==expected['product'] and v.get('reportSchemaVersion')==1 and v.get('validationScope')==expected['validationScope']
  if name=='serve': assertions['no_fake_protocol']=p.stdout==b'' and bool(p.stderr)
 except Exception as e: assertions['parse']=str(e)
 results.append({'name':name,'command':[str(binary),*args],'exitCode':p.returncode,'assertions':assertions,'pass':all(x is True for x in assertions.values())})
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
manifest={'command':sys.argv,'python':platform.python_version(),'binary':str(binary),'binarySha256':sha(binary),'testSha256':sha(pathlib.Path(__file__)),'expectedSha256':sha(root/'tests/e2e/wf1.expected.json'),'inputs':{'argvCases':cases},'rerun':f'python3 tests/e2e/wf1.py {binary} {out}'}
(out/'manifest.json').write_text(json.dumps(manifest,indent=2)); (out/'results.json').write_text(json.dumps(results,indent=2)); sys.exit(0 if results and all(r['pass'] for r in results) else 1)
