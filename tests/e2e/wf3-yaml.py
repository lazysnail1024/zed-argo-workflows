#!/usr/bin/env python3
import json,pathlib,tempfile,subprocess,sys,hashlib
root=pathlib.Path(__file__).resolve().parents[2];binary=pathlib.Path(sys.argv[1]).resolve();out=pathlib.Path(sys.argv[2]) if len(sys.argv)>2 else root/'artifacts/wf3-yaml';out.mkdir(parents=True,exist_ok=True);e=json.loads((root/'tests/e2e/wf3-yaml.expected.json').read_text());results=[]
cron='apiVersion: argoproj.io/v1alpha1\nkind: CronWorkflow\nmetadata: {name: demo}\nspec:\n  schedules: ["* * * * *"]\n  workflowSpec:\n    entrypoint: main\n    templates: [{name: main, container: {image: alpine}}]\n'
alias='apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata:\n  name: demo\n  annotations:\n    value: &limit wrong\nspec:\n  parallelism: *limit\n'
base='apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: demo}\n'
cases=[('old',cron,e['oldVersion']),('new',cron,e['newVersion']),('alias',alias,e['newVersion']),('tag',base+'spec: !custom {entrypoint: 123}\n',e['newVersion']),('merge',base+'spec: {<<: {entrypoint: 123}}\n',e['newVersion']),('cycle',base+'spec: &loop {arguments: *loop}\n',e['newVersion'])]
cases.append(('scalarBudget',base+'spec: {entrypoint: '+json.dumps('x'*1048577)+'}\n',e['newVersion']))
with tempfile.TemporaryDirectory(prefix='wf-yaml-') as d:
 work=pathlib.Path(d)
 for name,text,target in cases:
  (work/'case.yaml').write_text(text);(out/(name+'.input.yaml')).write_text(text);cmd=[str(binary),'check','--root',str(work),'--target-version',target,'--format','json'];p=subprocess.run(cmd,capture_output=True,timeout=15);(out/(name+'.stdout')).write_bytes(p.stdout);(out/(name+'.stderr')).write_bytes(p.stderr);checks={'exit':p.returncode==e['exitCode']}
  try:
   v=json.loads(p.stdout);ds=v['diagnostics'];unknown=[x for x in ds if x['code']==e['unknownCode']]
   if name=='old':checks['unknown']=len(unknown)==1 and unknown[0]['severity']=='Warning' and unknown[0]['range']['start']=={'line':4,'character':2}
   if name=='new':checks['version']=not unknown
   if name=='alias':checks['derived']=len(ds)==1 and ds[0]['code']=='SCHEMA_TYPE' and ds[0].get('positionKind')=='derived' and ds[0]['range']['start']=={'line':e['aliasLine'],'character':e['aliasColumn']};checks['evaluated']=not any('alias' in x.lower() for x in v['notEvaluated'])
   if name=='tag':checks['tagRejected']=not ds and any('tag' in x.lower() for x in v['notEvaluated'])
   if name=='merge':checks['mergeDerived']=len(ds)==1 and ds[0].get('positionKind')=='derived'
   if name=='scalarBudget':checks['budget']=not ds and any('scalar' in x.lower() for x in v['notEvaluated'])
   if name=='cycle':checks['cycleRejected']=not ds and any('alias' in x.lower() for x in v['notEvaluated'])
  except Exception as err:checks['parse']=str(err)
  results.append({'name':name,'command':cmd,'exitCode':p.returncode,'assertions':checks,'pass':all(x is True for x in checks.values())})
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
(out/'manifest.json').write_text(json.dumps({'command':sys.argv,'pythonVersion':sys.version,'binarySha256':sha(binary),'testSha256':sha(pathlib.Path(__file__)),'expectedSha256':sha(root/'tests/e2e/wf3-yaml.expected.json'),'inputHashes':{p.name:sha(p) for p in out.glob('*.input.yaml')}},indent=2));(out/'results.json').write_text(json.dumps(results,indent=2));sys.exit(0 if results and all(r['pass'] for r in results) else 1)
