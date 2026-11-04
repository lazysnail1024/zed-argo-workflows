#!/usr/bin/env python3
"""The external command deadline must still own post-render analysis."""
import pathlib,sys,subprocess,time,json,hashlib
cg=pathlib.Path('/sys/fs/cgroup')/pathlib.Path('/proc/self/cgroup').read_text().strip().split('::',1)[1].lstrip('/');assert int((cg/'memory.max').read_text())<=512*1024**2
binary=pathlib.Path(sys.argv[1]).resolve();out=pathlib.Path(sys.argv[2]).resolve();out.mkdir(parents=True,exist_ok=True);work=out/'input';work.mkdir(exist_ok=True);chart=out/'chart';chart.mkdir(exist_ok=True)
(work/'.argo-workflows-ls.yaml').write_text('schemaVersion: 1\ntargetVersion: "3.6.0"\n');(chart/'Chart.yaml').write_text('apiVersion: v2\nname: sample\nversion: 0.1.0\n')
helper=out/'helm';helper.write_text('#!/usr/bin/python3\nimport sys,time\nif sys.argv[1]=="version":print("v3.19.0");sys.exit(0)\ntime.sleep(29.5)\nprint(("apiVersion: argoproj.io/v1alpha1\\nkind: Workflow\\nmetadata: {name: sample}\\nspec: {templates: [{name: main, container: {image: alpine}}]}\\n---\\n")*1800)\n');helper.chmod(0o755)
cmd=[str(binary),'render-check','--root',str(work),'--chart',str(chart),'--helm-binary',str(helper),'--output',str(out/'generated')];start=time.monotonic()
try:r=subprocess.run(cmd,capture_output=True,timeout=38)
except subprocess.TimeoutExpired as e:r=subprocess.CompletedProcess(cmd,-9,e.stdout or b'',e.stderr or b'')
seconds=time.monotonic()-start;(out/'stdout.raw').write_bytes(r.stdout);(out/'stderr.raw').write_bytes(r.stderr)
try:report=json.loads(r.stdout)
except ValueError:report={}
checks={'cancelExit':r.returncode==3,'terminalDeadlineReason':'deadline' in json.dumps(report).lower(),'commandDeadlineOwnsCheck':seconds<31,'reachedRenderedAnalysis':(out/'generated/rendered.yaml').exists()};sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest();(out/'results.json').write_text(json.dumps({'command':cmd,'seconds':seconds,'exitCode':r.returncode,'assertions':checks,'pass':all(checks.values()),'binarySha256':sha(binary),'testSha256':sha(pathlib.Path(__file__))},indent=2)+'\n');sys.exit(0 if all(checks.values()) else 1)
