#!/usr/bin/env python3
"""Measure the real tiny-workspace failure without importing product code."""
import hashlib,json,pathlib,subprocess,sys,time
binary=pathlib.Path(sys.argv[1]).resolve();out=pathlib.Path(sys.argv[2]).resolve();work=out/'input';work.mkdir(parents=True,exist_ok=True)
(work/'.argo-workflows-ls.yaml').write_text('schemaVersion: 1\ntargetVersion: "3.6.0"\nreferencePolicy: closed\n')
for i in range(24):
 (work/('%02d.yaml'%i)).write_text('apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: bounded-%d}\nspec:\n  entrypoint: main\n  templates:\n    - name: main\n      container: {image: alpine}\n'%i)
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest();results=[]
for trial in range(5):
 cmd=[str(binary),'check','--root',str(work),'--format','json'];start=time.monotonic();r=subprocess.run(cmd,capture_output=True,timeout=12);elapsed=time.monotonic()-start
 (out/('%d.stdout'%trial)).write_bytes(r.stdout);(out/('%d.stderr'%trial)).write_bytes(r.stderr)
 report=json.loads(r.stdout);checks={'allFiles':report['filesAnalyzed']==24,'allResources':report['resourcesAnalyzed']==24,'noDeadline':not any('deadline' in str(x).lower() for x in report['notEvaluated']),'withinBudget':elapsed<=5,'acceptedComplete':r.returncode==0 and report['analysisComplete'] is True}
 results.append({'command':cmd,'exitCode':r.returncode,'elapsedSeconds':elapsed,'assertions':checks,'pass':all(checks.values())});(out/'results.json').write_text(json.dumps(results,indent=2)+'\n')
(out/'manifest.json').write_text(json.dumps({'binarySha256':sha(binary),'testSha256':sha(pathlib.Path(__file__)),'python':sys.version,'inputs':{p.name:sha(p) for p in work.iterdir()},'measurement':'Diagnostic baseline; not the constrained full-load acceptance gate'},indent=2)+'\n')
sys.exit(0 if all(r['pass'] for r in results) else 1)
