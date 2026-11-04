#!/usr/bin/env python3
"""Remove each corrected guard in isolated source; require an observed behavior failure."""
import hashlib,json,os,pathlib,shutil,subprocess,sys,tempfile
root=pathlib.Path(__file__).resolve().parents[2];out=root/'artifacts/wf-audit-gaps-ablation';out.mkdir(parents=True,exist_ok=True)
env=os.environ|{'GOROOT':'/tmp/zed-argo-go-mod/golang.org/toolchain@v0.0.1-go1.27.3.linux-amd64','GOMODCACHE':'/tmp/zed-argo-go-mod','GOCACHE':'/tmp/zed-argo-go-build','GOWORK':'off'}
variants=[
 ('scope-completeness','internal/analysis/edits.go','if !complete[document.Target.ID] {','if false && !complete[document.Target.ID] {','incomplete-scope-field-fix'),
 ('onexit','internal/analysis/symbols.go','add(member(task, "onExit"))','// omitted active onExit','rename-dag-onexit'),
 ('scope-simulation','internal/lsp/edits.go','if analysis.SafeScopePlan(ctx, w, d, fix.Plan) != nil {','if false {','fieldfix-external'),
 ('strict-index','internal/analysis/external.go','if _, err := project(ctx, d.Snapshot, doc, d.Target.Version); err != nil {','if err := preflight(ctx, d.Snapshot, doc); err != nil {','scope-same-duplicate'),
 ('target-gate','internal/analysis/projection.go','if _, err := compat.SchemaFile(version, api, kind); err != nil {','if _, err := compat.SchemaFile(version, api, kind); false && err != nil {','unsupported-gvk'),
 ('alias-inputs','internal/analysis/references.go','inputParameters, inputArtifacts := semanticNames(inputs, "parameters"), semanticNames(inputs, "artifacts")','_ = inputs\n inputParameters, inputArtifacts := namedInputs(member(template, "inputs"), "parameters"), namedInputs(member(template, "inputs"), "artifacts")','alias-input')]
if len(sys.argv)>1: variants=[v for v in variants if v[0] in sys.argv[1:]]
assert variants, "no selected ablation cases"
reports=[]
for name,file,before,after,case in variants:
 dest=out/name;dest.mkdir(parents=True,exist_ok=True)
 with tempfile.TemporaryDirectory(prefix='wf-audit-ablation-') as temp:
  copy=pathlib.Path(temp)
  for directory in ['cmd','compat','internal','vendor']:shutil.copytree(root/directory,copy/directory)
  for filename in ['go.mod','go.sum']:shutil.copy2(root/filename,copy/filename)
  p=copy/file;s=p.read_text();assert before in s;original=hashlib.sha256(p.read_bytes()).hexdigest();p.write_text(s.replace(before,after));shutil.copy2(p,dest/'mutated-source.go')
  command=[env['GOROOT']+'/bin/go','build','-mod=vendor','-buildvcs=false','-trimpath','-o',str(dest/'server'),'./cmd/argo-workflows-ls'];build=subprocess.run(command,cwd=copy,env=env,capture_output=True);(dest/'build.stdout').write_bytes(build.stdout);(dest/'build.stderr').write_bytes(build.stderr)
  report={'variant':name,'sourceFile':file,'originalSourceSha256':original,'mutation':{'before':before,'after':after},'buildCommand':command,'buildExitCode':build.returncode,'detected':False}
  if build.returncode==0:
   command=[sys.executable,str(root/'tests/e2e/wf-audit-gaps.py'),str(dest/'server'),str(dest/'run')];p=subprocess.run(command,capture_output=True,timeout=120);(dest/'runner.stdout').write_bytes(p.stdout);(dest/'runner.stderr').write_bytes(p.stderr);results=json.loads((dest/'run/results.json').read_text());report.update(command=command,exitCode=p.returncode,binarySha256=hashlib.sha256((dest/'server').read_bytes()).hexdigest(),detected=p.returncode==1 and any(x['case']==case and not x['pass'] for x in results),decision='retain behavior guard if independently detected')
  reports.append(report);(out/'report.json').write_text(json.dumps({'pythonVersion':sys.version,'driverSha256':hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest(),'variants':reports},indent=2));print(name,report['detected'],flush=True)
sys.exit(0 if len(reports)==len(variants) and all(r['detected'] for r in reports) else 1)
