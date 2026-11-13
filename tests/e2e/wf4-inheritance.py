#!/usr/bin/env python3
import json,pathlib,tempfile,subprocess,sys,hashlib
root=pathlib.Path(__file__).resolve().parents[2];binary=pathlib.Path(sys.argv[1]).resolve();out=pathlib.Path(sys.argv[2]) if len(sys.argv)>2 else root/'artifacts/wf4-inheritance';out.mkdir(parents=True,exist_ok=True);results=[]
caller='apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: caller, namespace: argo}\nspec:\n  workflowTemplateRef: {name: shared}\n  arguments:\n    parameters: [{name: supplied, value: hello}]\n'
base='apiVersion: argoproj.io/v1alpha1\nkind: WorkflowTemplate\nmetadata: {name: shared, namespace: argo}\nspec:\n  entrypoint: run\n  arguments:\n    parameters: [{name: inherited, value: default-value}]\n  templates:\n    - name: run\n      inputs:\n        parameters: [{name: inherited}, {name: supplied}]\n      container: {image: alpine}\n'
with tempfile.TemporaryDirectory(prefix='wf-inherit-') as d:
 work=pathlib.Path(d);(work/'.argo-workflows-ls.yaml').write_text('schemaVersion: 1\ntargetVersion: "3.6.0"\n');(work/'base.yaml').write_text(base)
 for name,text,expected in [('inherited',caller,[]),('missing-argument',caller.replace('  arguments:\n    parameters: [{name: supplied, value: hello}]\n',''),['WF_ARGUMENT_MISSING']),('bad-entrypoint',caller.replace('spec:\n','spec:\n  entrypoint: absent\n'),['WF_TEMPLATE_REF_UNRESOLVED'])]:
  (work/'caller.yaml').write_text(text);(out/(name+'.input.yaml')).write_text(text);cmd=[str(binary),'check','--root',str(work),'--file',str(work/'caller.yaml'),'--format','json'];p=subprocess.run(cmd,capture_output=True,timeout=15);(out/(name+'.stdout')).write_bytes(p.stdout);(out/(name+'.stderr')).write_bytes(p.stderr)
  try:
   v=json.loads(p.stdout);checks={'exit':p.returncode==(1 if expected else 0),'diagnostics':sorted(d['code'] for d in v['diagnostics'])==expected,'completeEnvelope':v['analysisComplete'] is True and v['notEvaluated']==[] and v['configurationErrors']==[] and v['filesAnalyzed']==v['resourcesAnalyzed']==2};passed=all(checks.values())
  except Exception:checks={'completed':False};passed=False
  results.append({'name':name,'command':cmd,'exitCode':p.returncode,'expectedCodes':expected,'assertions':checks,'pass':passed})
(out/'base.input.yaml').write_text(base);sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest();(out/'manifest.json').write_text(json.dumps({'command':sys.argv,'pythonVersion':sys.version,'binarySha256':sha(binary),'testSha256':sha(pathlib.Path(__file__)),'inputHashes':{p.name:sha(p) for p in out.glob('*.input.yaml')}},indent=2));(out/'results.json').write_text(json.dumps(results,indent=2));sys.exit(0 if results and all(x['pass'] for x in results) else 1)
