#!/usr/bin/env python3
"""Deadline completion counts include external-reference analysis, not just schema work."""
import pathlib,sys,subprocess,json,time,hashlib
binary=pathlib.Path(sys.argv[1]).resolve();out=pathlib.Path(sys.argv[2]).resolve();out.mkdir(parents=True,exist_ok=True);work=out/'input';work.mkdir(exist_ok=True);(work/'.argo-workflows-ls.yaml').write_text('schemaVersion: 1\ntargetVersion: "3.6.0"\nreferencePolicy: closed\n')
for i in range(4000):
 text='apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: dense-%d}\nspec:\n  entrypoint: main\n  templates:\n    - name: main\n      dag:\n        tasks:\n'%i
 text+=''.join('          - name: task-%d\n            templateRef: {name: absent-%d, template: main}\n'%(j,j) for j in range(15));(work/('%04d.yaml'%i)).write_text(text)
cmd=[str(binary),'check','--root',str(work),'--format','json'];start=time.monotonic()
try:p=subprocess.run(cmd,capture_output=True,timeout=9)
except subprocess.TimeoutExpired as e:p=subprocess.CompletedProcess(cmd,-9,e.stdout or b'',e.stderr or b'')
elapsed=time.monotonic()-start;(out/'stdout.raw').write_bytes(p.stdout);(out/'stderr.raw').write_bytes(p.stderr);
try:r=json.loads(p.stdout)
except ValueError:r={'notEvaluated':[],'filesAnalyzed':4000,'resourcesAnalyzed':4000}
checks={'deadlineExit':p.returncode==3,'deadlineReason':any('deadline' in x for x in r['notEvaluated']),'notAllCompleted':r['filesAnalyzed']<4000 and r['resourcesAnalyzed']<4000,'bounded':elapsed<5.5};sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest();(out/'results.json').write_text(json.dumps({'command':cmd,'exitCode':p.returncode,'seconds':elapsed,'assertions':checks,'pass':all(checks.values()),'binarySha256':sha(binary),'testSha256':sha(pathlib.Path(__file__))},indent=2)+'\n');sys.exit(0 if all(checks.values()) else 1)
