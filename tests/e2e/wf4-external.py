#!/usr/bin/env python3
import json,pathlib,tempfile,subprocess,sys,hashlib
root=pathlib.Path(__file__).resolve().parents[2];binary=pathlib.Path(sys.argv[1]).resolve();out=pathlib.Path(sys.argv[2]) if len(sys.argv)>2 else root/'artifacts/wf4-external';out.mkdir(parents=True,exist_ok=True);e=json.loads((root/'tests/e2e/wf4-external.expected.json').read_text());results=[]
consumer='apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: consumer, namespace: argo}\nspec:\n  entrypoint: main\n  templates:\n    - name: main\n      steps:\n        - - name: invoke\n            templateRef: {name: shared, template: run}\n'
external='apiVersion: argoproj.io/v1alpha1\nkind: WorkflowTemplate\nmetadata: {name: shared, namespace: argo}\nspec:\n  templates:\n    - name: run\n      container: {image: alpine}\n'
with tempfile.TemporaryDirectory(prefix='wf-external-') as d:
 work=pathlib.Path(d);(work/'consumer.yaml').write_text(consumer)
 for name in ['resolved','missing-open','missing-closed','wrong-namespace','missing-template','ambiguous','unknown-namespace']:
  (work/'consumer.yaml').write_text(consumer.replace(', namespace: argo','') if name=='unknown-namespace' else consumer)
  for p in work.glob('external*.yaml'):p.unlink()
  policy='closed' if name in ['missing-closed','unknown-namespace'] else 'open';(work/'.argo-workflows-ls.yaml').write_text('schemaVersion: 1\ntargetVersion: "3.6.0"\nreferencePolicy: '+policy+'\n')
  if not name.startswith('missing-') or name=='missing-template':
   text=external.replace('namespace: argo','namespace: elsewhere') if name=='wrong-namespace' else external.replace('name: run','name: other') if name=='missing-template' else external.replace(', namespace: argo','') if name=='unknown-namespace' else external;(work/'external.yaml').write_text(text)
  if name=='ambiguous':(work/'external2.yaml').write_text(external)
  cmd=[str(binary),'check','--root',str(work),'--file',str(work/'consumer.yaml'),'--format','json'];p=subprocess.run(cmd,capture_output=True,timeout=15);(out/(name+'.stdout')).write_bytes(p.stdout);(out/(name+'.stderr')).write_bytes(p.stderr);checks={'exit':p.returncode==e['exitCode']}
  try:v=json.loads(p.stdout);checks['externalResolution']=sorted([[d['code'],d['severity']] for d in v['diagnostics'] if d['code'].startswith('WF_TEMPLATE_REF')])==e[name]
  except Exception as err:checks['parse']=str(err)
  results.append({'name':name,'command':cmd,'exitCode':p.returncode,'assertions':checks,'pass':all(x is True for x in checks.values())})
 for p in work.glob('external*.yaml'):p.unlink()
 (work/'a').mkdir();(work/'b').mkdir();(work/'consumer.yaml').unlink();(work/'a/caller.yaml').write_text(consumer);(work/'b/broken.yaml').write_text('apiVersion: argoproj.io/v1alpha1\nkind: WorkflowTemplate\nmetadata: [broken\n');(work/'.argo-workflows-ls.yaml').write_text('schemaVersion: 1\ntargetVersion: "3.6.0"\nreferencePolicy: closed\nscopes:\n  - name: a\n    include: ["a/**/*.yaml"]\n  - name: b\n    include: ["b/**/*.yaml"]\n')
 cmd=[str(binary),'check','--root',str(work),'--file',str(work/'a/caller.yaml'),'--format','json'];p=subprocess.run(cmd,capture_output=True,timeout=15);(out/'other-scope-damaged.stdout').write_bytes(p.stdout);(out/'other-scope-damaged.stderr').write_bytes(p.stderr)
 try:v=json.loads(p.stdout);passed=p.returncode==2 and [[x['code'],x['severity']] for x in v['diagnostics'] if x['code']=='WF_TEMPLATE_REF_UNRESOLVED']==[['WF_TEMPLATE_REF_UNRESOLVED','Error']]
 except Exception:passed=False
 results.append({'name':'other-scope-damaged','command':cmd,'exitCode':p.returncode,'assertions':{'scopeCompletenessIsolation':passed},'pass':passed})
(out/'consumer.input.yaml').write_text(consumer);(out/'external.input.yaml').write_text(external)
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
(out/'manifest.json').write_text(json.dumps({'command':sys.argv,'pythonVersion':sys.version,'binarySha256':sha(binary),'testSha256':sha(pathlib.Path(__file__)),'expectedSha256':sha(root/'tests/e2e/wf4-external.expected.json'),'inputHashes':{p.name:sha(p) for p in out.glob('*.input.yaml')}},indent=2));(out/'results.json').write_text(json.dumps(results,indent=2));sys.exit(0 if results and all(r['pass'] for r in results) else 1)
