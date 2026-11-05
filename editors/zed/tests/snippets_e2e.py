#!/usr/bin/env python3
import pathlib,json,re,tempfile,subprocess,sys,hashlib
root=pathlib.Path(__file__).resolve().parents[1];binary=pathlib.Path(sys.argv[1]).resolve();out=pathlib.Path(sys.argv[2]).resolve() if len(sys.argv)>2 else root/'artifacts/wf5-snippets';out.mkdir(parents=True,exist_ok=True);snippets=json.loads((root/'snippets/yaml.json').read_text());expected={'Workflow','WorkflowTemplate','ClusterWorkflowTemplate','CronWorkflow'};seen=set();results=[]
with tempfile.TemporaryDirectory(prefix='wf-snippets-') as d:
 work=pathlib.Path(d)
 for name,snippet in snippets.items():
  text='\n'.join(snippet['body'])+'\n';text=re.sub(r'\$\{\d+:([^}]+)\}',lambda m:m.group(1),text);text=re.sub(r'\$\d+','',text);kind=next((line.split(':',1)[1].strip() for line in text.splitlines() if line.startswith('kind:')),'');seen.add(kind);(work/'case.yaml').write_text(text);(out/(kind+'.input.yaml')).write_text(text);cmd=[str(binary),'check','--root',str(work),'--target-version','3.6.0','--format','json'];p=subprocess.run(cmd,capture_output=True,timeout=15);(out/(kind+'.stdout')).write_bytes(p.stdout);(out/(kind+'.stderr')).write_bytes(p.stderr)
  try:v=json.loads(p.stdout);passed=p.returncode==0 and v['analysisComplete'] is True and v['notEvaluated']==[] and v['configurationErrors']==[] and v['filesAnalyzed']==v['resourcesAnalyzed']==1 and v['diagnostics']==[] and snippet['prefix'].startswith('argo-wf-') and '3.6.0' in snippet['description']
  except Exception:passed=False
  results.append({'name':name,'kind':kind,'command':cmd,'exitCode':p.returncode,'pass':passed})
results.append({'name':'four-owned-resource-kinds','pass':seen==expected and len(snippets)==4});sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest();(out/'manifest.json').write_text(json.dumps({'command':sys.argv,'pythonVersion':sys.version,'binarySha256':sha(binary),'testSha256':sha(pathlib.Path(__file__)),'snippetSha256':sha(root/'snippets/yaml.json'),'inputHashes':{p.name:sha(p) for p in out.glob('*.input.yaml')},'expectedKinds':sorted(expected)},indent=2));(out/'results.json').write_text(json.dumps(results,indent=2));sys.exit(0 if results and all(x['pass'] for x in results) else 1)
