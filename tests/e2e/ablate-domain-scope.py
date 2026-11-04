#!/usr/bin/env python3
"""Compile a query acquisition or pattern-rejection reversal and run real LSP."""
from pathlib import Path
import sys,os,subprocess,shutil,json,hashlib
root=Path(__file__).resolve().parents[2];out=Path(sys.argv[1]).resolve();work=Path(sys.argv[2]).resolve();source=out/'source';source.mkdir(parents=True,exist_ok=False)
for name in ['cmd','internal','compat','schemas','vendor']:shutil.copytree(root/name,source/name)
for name in ['go.mod','go.sum']:shutil.copy2(root/name,source/name)
variant=sys.argv[3] if len(sys.argv)>3 else 'scope'
if variant=='prefix':
 p=source/'internal/workspace/config.go';before=p.read_text();after=before.replace('if !strings.HasPrefix(name, literal) {','if false && !strings.HasPrefix(name, literal) {',1)
else:
 p=source/'internal/lsp/stdio.go';before=p.read_text();old='s.workspace(s.root(string(d.Snapshot.URI())), d.Target.Scope)';after=before.replace(old,'s.workspace(s.root(string(d.Snapshot.URI())), "")',1)
assert after!=before;p.write_text(after);(out/'change.json').write_text(json.dumps({'file':str(p.relative_to(source)),'before':before,'after':after},indent=2)+'\n')
env=os.environ|{'GOWORK':'off','GOTOOLCHAIN':'local','CGO_ENABLED':'1','GOPROXY':'off','GOSUMDB':'off'};cmd=[str(Path(env['GOROOT'])/'bin/go'),'build','-mod=vendor','-trimpath','-buildvcs=false','-o',str(out/'server'),'./cmd/argo-workflows-ls']
with (out/'build.stdout').open('wb') as so,(out/'build.stderr').open('wb') as se:r=subprocess.run(cmd,cwd=source,env=env,stdout=so,stderr=se)
row={'variant':variant,'buildCommand':cmd,'buildExit':r.returncode,'detected':False}
if r.returncode==0:
 row['binarySha256']=hashlib.sha256((out/'server').read_bytes()).hexdigest();cmd=[sys.executable,str(root/'tests/e2e/wf-domain-external-load.py'),str(out/'server'),str(work),str(out/'consumer')];row['consumerCommand']=cmd
 with (out/'consumer.stdout').open('wb') as so,(out/'consumer.stderr').open('wb') as se:r=subprocess.run(cmd,env=env,stdout=so,stderr=se,timeout=180)
 results=json.loads((out/'consumer/results.json').read_text());row.update(consumerExit=r.returncode,results=results);row['detected']=r.returncode==1 and results[0]['assertions']['allExactCandidates'] and not results[0]['assertions']['p95Within100ms']
(out/'results.json').write_text(json.dumps(row,indent=2)+'\n');sys.exit(0 if row['detected'] else 1)
